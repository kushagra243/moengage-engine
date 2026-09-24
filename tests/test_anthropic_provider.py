"""The enterprise path: the Anthropic Messages API as a provider, the cheap model carrying the volume, tools and results translated both ways."""
import json


class _R:
    def __init__(self, p, code=200):
        self._p, self.status_code, self.text = p, code, json.dumps(p)

    def json(self):
        return self._p


def _cfg(model="claude-sonnet-5", bulk="auto-free"):
    return {"provider": "anthropic", "base_url": "https://api.anthropic.com", "model": model, "api_key": "sk-ant-test-key-000000000000000000000000", "temperature": 0.2, "max_tokens": 500, "model_bulk": bulk}


def test_settings_route_the_heavy_lifting_to_the_cheap_model():
    from backend.llm import provider as p
    cfg = _cfg()
    assert p.bulk_models(cfg) == ["claude-haiku-4-5-20251001", "claude-sonnet-5"]
    r = p.routes(cfg)
    assert r["chat"] == ["claude-sonnet-5"] and r["autopilot"][0] == "claude-haiku-4-5-20251001" and r["analysis"][0] == "claude-haiku-4-5-20251001" and r["classification"][0] == "claude-haiku-4-5-20251001"
    assert p.bulk_models(_cfg(model="claude-haiku-4-5-20251001")) == ["claude-haiku-4-5-20251001"], "everything on Haiku when the main model is Haiku"
    assert p.normalise_model_for_provider("anthropic", "anthropic/claude-sonnet-5") == "claude-sonnet-5" and p.normalise_model_for_provider("anthropic", "haiku") == "claude-haiku-4-5-20251001"
    h = p._headers(cfg)
    assert h["x-api-key"] == cfg["api_key"] and h["anthropic-version"] and "Authorization" not in h


def test_an_anthropic_key_switches_the_provider_and_base_url():
    from backend.llm import provider as p
    from backend.database import set_setting, get_setting
    set_setting("llm_provider", "openrouter"); set_setting("llm_base_url", p.DEFAULT_BASE_URL); set_setting("llm_model", "anthropic/claude-sonnet-4.5"); set_setting("llm_api_key", "sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789")
    ch = p.reconcile_llm_settings({"llm_api_key"})
    assert ch["llm_provider"] == "anthropic" and ch["llm_base_url"] == p.ANTHROPIC_BASE and get_setting("llm_model") == "claude-sonnet-4.5"
    set_setting("llm_provider", "openrouter"); set_setting("llm_base_url", p.DEFAULT_BASE_URL); set_setting("llm_model", p.DEFAULT_MODEL); set_setting("llm_api_key", "")


def test_the_conversation_and_tools_are_translated_and_the_reply_comes_back_in_the_engine_shape(monkeypatch):
    from backend.llm import provider as p
    sent = {}

    class S:
        def post(self, url, headers=None, json=None, timeout=None):
            sent["url"], sent["headers"], sent["body"] = url, headers, json
            return _R({"model": "claude-haiku-4-5-20251001", "stop_reason": "tool_use", "content": [{"type": "text", "text": "Checking."}, {"type": "tool_use", "id": "toolu_01", "name": "market_snapshot", "input": {"symbols": ["BTC"]}}],
                       "usage": {"input_tokens": 1200, "output_tokens": 40, "cache_read_input_tokens": 800}})
    c = p.LLMClient(_cfg()); c.session = S(); c.purpose = "analysis"
    tools = [{"type": "function", "function": {"name": "market_snapshot", "description": "d", "parameters": {"type": "object", "properties": {"symbols": {"type": "array"}}}}}]
    msgs = [{"role": "system", "content": "You are the CLM head."}, {"role": "user", "content": "what moved"},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "toolu_00", "type": "function", "function": {"name": "skill", "arguments": "{\"name\": \"moengage\"}"}}]},
            {"role": "tool", "tool_call_id": "toolu_00", "name": "skill", "content": "<skill…>"}, {"role": "user", "content": "go on, contact me at someone@example.com"}]
    out = c.chat(msgs, tools=tools, tier="bulk")
    assert sent["url"].endswith("/v1/messages") and sent["body"]["model"] == "claude-haiku-4-5-20251001", "bulk tier → the cheap model"
    assert sent["body"]["system"][0]["cache_control"] == {"type": "ephemeral"} and sent["body"]["tools"][0]["input_schema"]["type"] == "object"
    roles = [m["role"] for m in sent["body"]["messages"]]
    assert roles == ["user", "assistant", "user"], roles
    assert sent["body"]["messages"][1]["content"][0]["type"] == "tool_use" and sent["body"]["messages"][2]["content"][0]["type"] == "tool_result"
    assert "someone@example.com" not in json.dumps(sent["body"]), "redacted before it leaves"
    assert out["tool_calls"] == [{"id": "toolu_01", "name": "market_snapshot", "arguments": {"symbols": ["BTC"]}}] and out["finish_reason"] == "tool_calls" and out["content"] == "Checking."
    assert out["usage"]["prompt_tokens"] == 2000 and out["usage"]["cached_tokens"] == 800 and out["usage"]["cost"] == round((1200 * 1.0 + 40 * 5.0 + 800 * 0.1) / 1e6, 6)


def test_auth_and_overload_are_reported_as_llm_errors(monkeypatch):
    import pytest
    from backend.llm import provider as p

    class S:
        def __init__(self, code): self.code = code
        def post(self, *a, **k): return _R({"error": {"message": "x"}}, self.code)
    c = p.LLMClient(_cfg()); c.session = S(401)
    with pytest.raises(p.LLMError, match="auth"):
        c.chat([{"role": "user", "content": "hi"}], model="claude-haiku-4-5-20251001")
    monkeypatch.setattr(p.time, "sleep", lambda *a: None)
    c.session = S(529)
    with pytest.raises(p.LLMError, match="529"):
        c.chat([{"role": "user", "content": "hi"}], model="claude-haiku-4-5-20251001")
