import json
from backend.database import set_setting


def test_route_defaults_and_override():
    from backend.llm import provider as pv
    set_setting("llm_provider", "openrouter"); set_setting("llm_base_url", "https://openrouter.ai/api/v1"); set_setting("llm_model", "anthropic/claude-sonnet-4.5"); set_setting("llm_model_bulk", "auto-free"); set_setting("llm_routes", "")
    r = pv.routes()
    assert r["chat"] == ["anthropic/claude-sonnet-4.5"] and r["autopilot"][0].endswith(":free") and r["autopilot"][-1] == "anthropic/claude-sonnet-4.5"
    from backend.guidance import set_engine_setting
    assert set_engine_setting("llm_routes", {"copy": ["openai/gpt-4o-mini"], "analysis": "deepseek/deepseek-chat-v3-0324:free, openai/gpt-4o-mini"})["ok"]
    r2 = pv.routes()
    assert r2["copy"] == ["openai/gpt-4o-mini", "anthropic/claude-sonnet-4.5"] and r2["analysis"][:2] == ["deepseek/deepseek-chat-v3-0324:free", "openai/gpt-4o-mini"]
    assert pv.route_models("nonexistent") == r2["chat"]
    set_setting("llm_routes", "")


def test_chat_follows_purpose_route_with_fallback():
    from backend.llm import provider as pv
    set_setting("llm_provider", "openrouter"); set_setting("llm_base_url", "https://openrouter.ai/api/v1"); set_setting("llm_model", "anthropic/claude-sonnet-4.5"); set_setting("llm_api_key", "sk-or-v1-testkey-000000000000000000000000")
    set_setting("llm_routes", json.dumps({"copy": ["broken/model", "openai/gpt-4o-mini"]}))
    seen = []
    class Resp:
        def __init__(self, code, model): self.status_code = code; self.model = model; self.text = "boom"
        def json(self): return {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 5, "completion_tokens": 1}, "model": self.model}
    class Sess:
        def post(self, url, headers=None, json=None, timeout=None):
            seen.append(json["model"]); return Resp(400, json["model"]) if json["model"] == "broken/model" else Resp(200, json["model"])
    c = pv.LLMClient(); c.session = Sess(); c.purpose = "copy"
    out = c.chat([{"role": "user", "content": "hi"}], tools=None)
    assert out["content"] == "ok" and seen == ["broken/model", "openai/gpt-4o-mini"]
    from backend.llm.tools import TOOLS
    assert "model_routes" in TOOLS and TOOLS["model_routes"]()["routes"]["copy"][0] == "broken/model"
    set_setting("llm_routes", ""); set_setting("llm_api_key", ""); set_setting("llm_provider", "openai_compatible")
