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
# Free-tier OpenRouter models tried in order for bulk analysis (per-campaign deep dives, idea generation).
FREE_BULK_MODELS = ["meta-llama/llama-3.3-70b-instruct:free", "deepseek/deepseek-chat-v3-0324:free", "qwen/qwen3-235b-a22b:free", "google/gemma-3-27b-it:free", "mistralai/mistral-small-3.2-24b-instruct:free"]


class LLMError(RuntimeError):
    pass


CLI_ALIASES = ("sonnet", "opus", "haiku")


def cli_model(model: str) -> str:
    """Claude Code CLI accepts aliases (sonnet/opus/haiku) or full claude-* ids, never provider-prefixed ids."""
    m = (model or "").strip()
    if not m:
        return "sonnet"
    if "/" in m:                      # e.g. anthropic/claude-3.7-sonnet (OpenRouter style)
        m = m.split("/", 1)[1]
    low = m.lower()
    if low in CLI_ALIASES or low.startswith("claude-"):
        return m
    return "opus" if "opus" in low else "haiku" if "haiku" in low else "sonnet"


def normalise_model_for_provider(provider: str, model: str) -> str:
    if provider == "claude_cli":
        return cli_model(model)
    if provider == "openrouter" and model and "/" not in model:
        return DEFAULT_MODEL
    return model or DEFAULT_MODEL


def reconcile_llm_settings(saved_keys) -> Dict[str, str]:
    """
    Infer intent after a settings save so provider/model/key never contradict:
      * an API key was saved while provider is claude_cli → the user wants a hosted API → openrouter
      * a provider-prefixed model id (vendor/model) while provider is claude_cli → openrouter
      * provider claude_cli with an alias-less model → normalise to a CLI model
      * provider openrouter with a bare model → default OpenRouter model
    Returns the changes applied.
    """
    from ..database import get_setting, set_setting
    prov = get_setting("llm_provider", "openrouter"); model = get_setting("llm_model", ""); key = get_setting("llm_api_key", "")
    base = get_setting("llm_base_url", DEFAULT_BASE_URL)
    changes: Dict[str, str] = {}
    if prov == "claude_cli" and (("llm_api_key" in saved_keys and key) or ("/" in (model or "") and "llm_model" in saved_keys)):
        prov = "openrouter" if ("openrouter" in base or not base) else "openai_compatible"
        set_setting("llm_provider", prov); changes["llm_provider"] = prov
        if prov == "openrouter" and "openrouter" not in base:
            set_setting("llm_base_url", DEFAULT_BASE_URL); changes["llm_base_url"] = DEFAULT_BASE_URL
    fixed = normalise_model_for_provider(prov, model)
    if fixed != model:
        set_setting("llm_model", fixed); changes["llm_model"] = fixed
    return changes


def llm_settings() -> Dict[str, Any]:
    return {
        "provider": get_setting("llm_provider", "openrouter"),        # openrouter | openai_compatible | claude_cli
        "base_url": get_setting("llm_base_url", DEFAULT_BASE_URL).rstrip("/"),
        "model": get_setting("llm_model", DEFAULT_MODEL),
        "api_key": get_setting("llm_api_key", ""),
        "temperature": float(get_setting("llm_temperature", "0.3") or 0.3),
        "max_tokens": int(get_setting("llm_max_tokens", "2000") or 2000),
        "model_bulk": get_setting("llm_model_bulk", "auto-free"),   # auto-free = first working FREE_BULK_MODELS entry (OpenRouter), else the main model
    }


def bulk_models(cfg: Optional[Dict[str, Any]] = None) -> List[str]:
    """Ordered candidates for bulk work. Only OpenRouter has a free tier; elsewhere fall back to the main model."""
    cfg = cfg or llm_settings()
    mb = (cfg.get("model_bulk") or "auto-free").strip()
    if cfg["provider"] == "openrouter":
        if mb and mb != "auto-free":
            return [mb, cfg["model"]]
        return FREE_BULK_MODELS + [cfg["model"]]
    if cfg["provider"] == "claude_cli":
        return ["haiku" if mb in ("", "auto-free") else mb]
    return [mb] if mb and mb != "auto-free" else [cfg["model"]]


# Purpose → ordered model candidates. Defaults lean free for volume work and keep the main model for chat/code/copy.
# Override any purpose with the llm_routes setting (JSON object), e.g. {"copy": ["anthropic/claude-sonnet-4.5"], "analysis": ["deepseek/deepseek-chat-v3-0324:free", "openai/gpt-4o-mini"]}
ROUTE_PURPOSES = ("chat", "autopilot", "analysis", "brief", "copy", "classification", "code", "review", "test")


def route_defaults(cfg: Dict[str, Any]) -> Dict[str, List[str]]:
    main = cfg.get("model") or ""
    free = FREE_BULK_MODELS if cfg.get("provider") == "openrouter" else []
    bulk = bulk_models(cfg)
    return {"chat": [main], "code": [main], "copy": [main], "review": [main],
            "autopilot": bulk, "analysis": bulk, "brief": bulk, "classification": (free[:2] + [main]) if free else [main], "test": bulk}


def routes(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, List[str]]:
    cfg = cfg or llm_settings()
    out = route_defaults(cfg)
    try:
        override = json.loads(get_setting("llm_routes", "") or "{}")
        for k, v in (override or {}).items():
            if isinstance(v, str):
                v = [x.strip() for x in v.split(",") if x.strip()]
            if isinstance(v, list) and v:
                out[str(k)] = [str(m) for m in v] + ([cfg.get("model")] if cfg.get("model") not in v else [])
    except Exception:
        pass
    return out


def route_models(purpose: str, cfg: Optional[Dict[str, Any]] = None) -> List[str]:
    cfg = cfg or llm_settings()
    r = routes(cfg)
    return r.get(purpose) or r.get("chat") or [cfg.get("model")]


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
        return {"ok": True, "source": "claude_cli", "count": 6, "models": [
            {"id": "sonnet", "name": "Sonnet (alias, recommended)", "context": None, "tools": True},
            {"id": "opus", "name": "Opus (alias)", "context": None, "tools": True},
            {"id": "haiku", "name": "Haiku (alias)", "context": None, "tools": True},
            {"id": "claude-sonnet-5", "name": "claude-sonnet-5", "context": None, "tools": True},
            {"id": "claude-opus-5", "name": "claude-opus-5", "context": None, "tools": True},
            {"id": "claude-haiku-4-5-20251001", "name": "claude-haiku-4-5-20251001", "context": None, "tools": True}]}
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


def fallback_cfg(cfg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """When the primary is the Claude Code CLI (team login on this host) and an OpenRouter key exists, the key is the fallback:
    llm_fallback_provider (default openrouter when a key is saved; 'none' disables), llm_fallback_model (default anthropic/claude-sonnet-4.5)."""
    if cfg.get("provider") != "claude_cli":
        return None
    prov = get_setting("llm_fallback_provider", "openrouter" if cfg.get("api_key") else "none")
    if prov in ("", "none") or not cfg.get("api_key"):
        return None
    base = get_setting("llm_base_url", "https://openrouter.ai/api/v1") if prov == "openai_compatible" else "https://openrouter.ai/api/v1"
    return {**cfg, "provider": prov, "base_url": base, "model": get_setting("llm_fallback_model", "anthropic/claude-sonnet-4.5"), "model_bulk": get_setting("llm_model_bulk", "auto-free")}


class LLMClient:
    def __init__(self, cfg: Optional[Dict[str, Any]] = None):
        self.cfg = cfg or llm_settings()
        self.session = guarded_session("llm") if self.cfg["provider"] != "claude_cli" else None

    # ── OpenAI-compatible ────────────────────────────────────────────────
    def chat(self, messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]] = None,
             tool_choice: Optional[str] = None, max_tokens: Optional[int] = None, temperature: Optional[float] = None,
             response_format: Optional[Dict[str, Any]] = None, model: Optional[str] = None, tier: str = "main") -> Dict[str, Any]:
        """
        Returns {"content": str|None, "tool_calls": [...], "finish_reason", "usage", "model"}.
        tier="bulk" tries the free/cheap candidates in order and falls back to the main model.
        """
        purpose = getattr(self, "purpose", "chat")
        if tier == "main" and model is None and self.cfg["provider"] != "claude_cli":
            cands = route_models(purpose, self.cfg)
            if cands and (len(cands) > 1 or cands[0] != self.cfg["model"]):
                last = None
                for cand in cands:
                    try:
                        return self.chat(messages, tools, tool_choice, max_tokens, temperature, response_format, model=cand, tier="main")
                    except LLMError as e:
                        last = e
                        log.info("route %s model %s failed: %s", purpose, cand, redact(str(e))[:120])
                        continue
                raise last or LLMError(f"no model available for purpose {purpose}")
        if tier == "bulk" and model is None:
            last = None
            for cand in bulk_models(self.cfg):
                try:
                    return self.chat(messages, tools, tool_choice, max_tokens, temperature, response_format, model=cand, tier="main")
                except LLMError as e:
                    last = e
                    log.info("bulk model %s failed: %s", cand, redact(str(e))[:120])
                    continue
            raise last or LLMError("no bulk model available")
        if self.cfg["provider"] == "claude_cli":
            try:
                return self._chat_claude_cli(messages, tools, model=model)
            except LLMError as e:
                fb = fallback_cfg(self.cfg)
                if not fb:
                    raise
                log.warning("claude cli failed (%s); falling back to %s/%s", redact(str(e))[:100], fb["provider"], fb["model"])
                client = LLMClient(fb); client.purpose = purpose
                out = client.chat(messages, tools, tool_choice, max_tokens, temperature, response_format, model=None, tier="main")
                out["fallback_from"] = "claude_cli"
                return out
        if not self.cfg["api_key"] and "openrouter.ai" in self.cfg["base_url"]:
            raise LLMError("No LLM API key configured. Add your OpenRouter key in Settings → LLM.")
        safe_messages = [_redact_message(m) for m in messages]
        use_model = model or self.cfg["model"]
        if "claude" in use_model.lower() and safe_messages and safe_messages[0].get("role") == "system" and isinstance(safe_messages[0].get("content"), str):
            # Anthropic prompt caching (OpenRouter passes cache_control through): the cached prefix covers tools + system prompt
            safe_messages = [{"role": "system", "content": [{"type": "text", "text": safe_messages[0]["content"], "cache_control": {"type": "ephemeral"}}]}] + safe_messages[1:]
        payload: Dict[str, Any] = {
            "model": use_model,
            "messages": safe_messages,
            "temperature": self.cfg["temperature"] if temperature is None else temperature,
            "max_tokens": max_tokens or self.cfg["max_tokens"],
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice or "auto"
        if response_format:
            payload["response_format"] = response_format
        if "openrouter.ai" in self.cfg["base_url"]:
            payload["usage"] = {"include": True}          # provider-reported cost in the usage block
            if get_setting("llm_data_collection", "deny") == "deny":
                # privacy: only route to providers that do not store or train on prompts; free models that require data collection are skipped
                payload["provider"] = {"data_collection": "deny"}
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
            usage = data.get("usage") or {}
            try:
                from .usage import record as _record
                _record(getattr(self, "purpose", "chat"), ("bulk" if ":free" in str(model) else "route") if (model and model != self.cfg["model"]) else "main", data.get("model") or use_model, usage, usage.get("cost"))
            except Exception:
                pass
            return {
                "content": msg.get("content"),
                "tool_calls": tool_calls,
                "finish_reason": choice.get("finish_reason"),
                "usage": usage,
                "model": data.get("model") or self.cfg["model"],
                "raw_message": msg,
            }
        raise last_err or LLMError("LLM request failed")

    # ── Claude Code CLI (headless) ───────────────────────────────────────
    def _chat_claude_cli(self, messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]], model: Optional[str] = None) -> Dict[str, Any]:
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
        model = cli_model(model or self.cfg["model"])
        try:
            out = subprocess.run([binary, "-p", "--output-format", "json", "--model", model],
                                 input=prompt, capture_output=True, text=True, timeout=180)
            data = json.loads(out.stdout or "{}")
        except Exception as e:
            raise LLMError(f"claude cli failed: {redact(str(e))}")
        if data.get("is_error"):
            msg = redact(str(data.get("result")))[:200]
            hint = " Fix: Settings → LLM → model 'sonnet' (or ./cli.py set llm_model sonnet)." if "model" in msg.lower() else \
                   " Fix: run `claude login` in a terminal." if "auth" in msg.lower() or "oauth" in msg.lower() else ""
            raise LLMError(f"claude cli error: {msg}.{hint}")
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
        try:
            from .usage import record as _record
            u = data.get("usage") or {}
            _record(getattr(self, "purpose", "chat"), "main", "claude_cli:" + str(model or self.cfg["model"]),
                    {"prompt_tokens": u.get("input_tokens", 0), "completion_tokens": u.get("output_tokens", 0), "cache_read_input_tokens": u.get("cache_read_input_tokens", 0)}, data.get("total_cost_usd"))
        except Exception:
            pass
        return {"content": text or None, "tool_calls": tool_calls, "finish_reason": "tool_calls" if tool_calls else "stop",
                "usage": data.get("usage") or {}, "model": self.cfg["model"], "raw_message": {"role": "assistant", "content": text, "tool_calls": tool_calls}}


def _redact_message(m: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(m)
    if isinstance(out.get("content"), str):
        out["content"] = redact(out["content"])
    return out
