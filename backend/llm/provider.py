"""
Live LLM provider client.

OpenAI-compatible chat-completions with tool calling. Default is OpenRouter
(https://openrouter.ai/api/v1) so any frontier model can be selected by id;
the same client works for OpenAI, Groq, Together, Ollama (/v1) and LM Studio.
An optional "claude-cli" mode drives the locally installed Claude Code CLI
headlessly (no API key; uses the machine's own login).

Security: the request goes through the 'llm' GuardedSession whose allowlist is
exactly the configured base_url host. Prompts and tool outputs are passed
through redact() before leaving the process.
"""
from __future__ import annotations
import json
import logging
import shutil
import subprocess
import time
from typing import Any, Dict, List, Optional

from ..database import get_setting
from ..security import guarded_session, redact, NetworkPolicyError

log = logging.getLogger("moengage.llm")

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "anthropic/claude-sonnet-4.5"
FALLBACK_MODELS = ["openai/gpt-4o-mini", "google/gemini-2.5-flash", "meta-llama/llama-3.3-70b-instruct"]


class LLMError(RuntimeError):
    pass


def llm_settings() -> Dict[str, Any]:
    return {
        "provider": get_setting("llm_provider", "openrouter"),        # openrouter | openai_compatible | claude_cli
        "base_url": get_setting("llm_base_url", DEFAULT_BASE_URL).rstrip("/"),
        "model": get_setting("llm_model", DEFAULT_MODEL),
        "api_key": get_setting("llm_api_key", ""),
        "temperature": float(get_setting("llm_temperature", "0.3") or 0.3),
        "max_tokens": int(get_setting("llm_max_tokens", "2000") or 2000),
    }


def _headers(cfg: Dict[str, Any]) -> Dict[str, str]:
    h = {"Content-Type": "application/json", "Accept": "application/json"}
    if cfg["api_key"]:
        h["Authorization"] = f"Bearer {cfg['api_key']}"
    if "openrouter.ai" in cfg["base_url"]:
        h["X-Title"] = "moengage-engine (local)"
    return h


def list_models(limit: int = 400) -> Dict[str, Any]:
    cfg = llm_settings()
    if cfg["provider"] == "claude_cli":
        return {"ok": True, "models": ["claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5-20251001"], "source": "claude_cli"}
    s = guarded_session("llm")
    try:
        r = s.get(cfg["base_url"] + "/models", headers=_headers(cfg), timeout=20)
    except NetworkPolicyError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:
        return {"ok": False, "error": redact(str(e))}
    if r.status_code != 200:
        return {"ok": False, "error": f"HTTP {r.status_code}: {redact(r.text[:200])}"}
    data = r.json()
    items = data.get("data", data if isinstance(data, list) else [])
    out = []
    for m in items:
        if not isinstance(m, dict):
            continue
        out.append({
            "id": m.get("id"),
            "name": m.get("name") or m.get("id"),
            "context": m.get("context_length") or m.get("top_provider", {}).get("context_length"),
            "tools": (m.get("supported_parameters") is None) or ("tools" in (m.get("supported_parameters") or [])),
            "prompt_price": (m.get("pricing") or {}).get("prompt"),
        })
    out = [m for m in out if m["id"]]
    return {"ok": True, "models": out[:limit], "count": len(out)}


def probe() -> Dict[str, Any]:
    """Cheap connectivity + auth check that never includes MoEngage data."""
    cfg = llm_settings()
    started = time.time()
    try:
        client = LLMClient()
        resp = client.chat([{"role": "user", "content": "Reply with the single word: ready"}], tools=None, max_tokens=10)
        text = (resp.get("content") or "").strip()
        return {"ok": True, "provider": cfg["provider"], "model": cfg["model"], "base_url": cfg["base_url"],
                "reply": text[:40], "latency_ms": int((time.time() - started) * 1000)}
    except Exception as e:
        return {"ok": False, "provider": cfg["provider"], "model": cfg["model"], "base_url": cfg["base_url"], "error": redact(str(e))}


class LLMClient:
    def __init__(self, cfg: Optional[Dict[str, Any]] = None):
        self.cfg = cfg or llm_settings()
        self.session = guarded_session("llm") if self.cfg["provider"] != "claude_cli" else None

    # ── OpenAI-compatible ────────────────────────────────────────────────
    def chat(self, messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]] = None,
             tool_choice: Optional[str] = None, max_tokens: Optional[int] = None, temperature: Optional[float] = None,
             response_format: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Returns {"content": str|None, "tool_calls": [{"id","name","arguments"(dict)}], "finish_reason", "usage", "model"}
        """
        if self.cfg["provider"] == "claude_cli":
            return self._chat_claude_cli(messages, tools)
        if not self.cfg["api_key"] and "openrouter.ai" in self.cfg["base_url"]:
            raise LLMError("No LLM API key configured. Add your OpenRouter key in Settings → LLM.")
        safe_messages = [_redact_message(m) for m in messages]
        payload: Dict[str, Any] = {
            "model": self.cfg["model"],
            "messages": safe_messages,
            "temperature": self.cfg["temperature"] if temperature is None else temperature,
            "max_tokens": max_tokens or self.cfg["max_tokens"],
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice or "auto"
        if response_format:
            payload["response_format"] = response_format
        url = self.cfg["base_url"] + "/chat/completions"
        last_err = None
        for attempt in range(3):
            try:
                r = self.session.post(url, headers=_headers(self.cfg), json=payload, timeout=120)
            except NetworkPolicyError:
                raise
            except Exception as e:
                last_err = LLMError(f"LLM request failed: {redact(str(e))}")
                time.sleep(1.5 * (attempt + 1)); continue
            if r.status_code == 429 or r.status_code >= 500:
                last_err = LLMError(f"LLM HTTP {r.status_code}: {redact(r.text[:300])}")
                time.sleep(2.0 * (attempt + 1)); continue
            if r.status_code in (401, 403):
                raise LLMError(f"LLM auth failed (HTTP {r.status_code}). Check the API key in Settings → LLM.")
            if r.status_code != 200:
                raise LLMError(f"LLM HTTP {r.status_code}: {redact(r.text[:300])}")
            data = r.json()
            if "error" in data and not data.get("choices"):
                raise LLMError(f"LLM error: {redact(json.dumps(data['error'])[:300])}")
            choice = (data.get("choices") or [{}])[0]
            msg = choice.get("message") or {}
            tool_calls = []
            for tc in msg.get("tool_calls") or []:
                fn = tc.get("function") or {}
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except Exception:
                    args = {"_raw": fn.get("arguments")}
                tool_calls.append({"id": tc.get("id") or f"call_{len(tool_calls)}", "name": fn.get("name"), "arguments": args})
            return {
                "content": msg.get("content"),
                "tool_calls": tool_calls,
                "finish_reason": choice.get("finish_reason"),
                "usage": data.get("usage") or {},
                "model": data.get("model") or self.cfg["model"],
                "raw_message": msg,
            }
        raise last_err or LLMError("LLM request failed")

    # ── Claude Code CLI (headless) ───────────────────────────────────────
    def _chat_claude_cli(self, messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]]) -> Dict[str, Any]:
        binary = shutil.which("claude")
        if not binary:
            raise LLMError("Claude Code CLI not found on PATH; switch provider to openrouter.")
        # Emulate tool calling through a strict JSON protocol.
        sys_parts = [m["content"] for m in messages if m["role"] == "system"]
        convo = []
        for m in messages:
            if m["role"] == "system":
                continue
            if m["role"] == "tool":
                convo.append(f"[TOOL RESULT for {m.get('name','tool')}]\n{m.get('content','')}")
            elif m["role"] == "assistant":
                if m.get("tool_calls"):
                    convo.append("[ASSISTANT TOOL CALLS]\n" + json.dumps(m["tool_calls"]))
                elif m.get("content"):
                    convo.append(f"[ASSISTANT]\n{m['content']}")
            else:
                convo.append(f"[USER]\n{m.get('content','')}")
        protocol = ""
        if tools:
            protocol = ("\n\nTOOLS: You may call tools. To call tools reply with ONLY a JSON object: "
                        '{"tool_calls":[{"name":"<tool>","arguments":{...}}]} . Otherwise reply with plain text.\n'
                        "Available tools:\n" + json.dumps([t["function"] for t in tools])[:12000])
        prompt = redact("\n\n".join(sys_parts) + protocol + "\n\n" + "\n\n".join(convo))
        try:
            out = subprocess.run([binary, "-p", "--output-format", "json", "--model", self.cfg["model"]],
                                 input=prompt, capture_output=True, text=True, timeout=180)
            data = json.loads(out.stdout or "{}")
        except Exception as e:
            raise LLMError(f"claude cli failed: {redact(str(e))}")
        if data.get("is_error"):
            raise LLMError(f"claude cli error: {redact(str(data.get('result')))[:200]}")
        text = str(data.get("result") or "")
        tool_calls = []
        stripped = text.strip()
        if stripped.startswith("{") and '"tool_calls"' in stripped:
            try:
                obj = json.loads(stripped)
                for i, tc in enumerate(obj.get("tool_calls") or []):
                    tool_calls.append({"id": f"cli_{i}", "name": tc.get("name"), "arguments": tc.get("arguments") or {}})
                text = ""
            except Exception:
                pass
        return {"content": text or None, "tool_calls": tool_calls, "finish_reason": "tool_calls" if tool_calls else "stop",
                "usage": data.get("usage") or {}, "model": self.cfg["model"], "raw_message": {"role": "assistant", "content": text, "tool_calls": tool_calls}}


def _redact_message(m: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(m)
    if isinstance(out.get("content"), str):
        out["content"] = redact(out["content"])
    return out
