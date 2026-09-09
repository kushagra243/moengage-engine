import json
from backend.database import set_setting


def test_usage_ledger_records_and_summarises():
    from backend.llm import usage
    usage.init_usage_tables()
    usage.record("chat", "main", "anthropic/claude-sonnet-4.5", {"prompt_tokens": 1200, "completion_tokens": 300, "prompt_tokens_details": {"cached_tokens": 900}}, None)
    usage.record("autopilot", "bulk", "meta-llama/llama-3.3-70b-instruct:free", {"prompt_tokens": 5000, "completion_tokens": 800}, None)
    s = usage.summary(1)
    assert s["today"]["calls"] >= 2 and s["today"]["cached_tokens"] >= 900
    assert usage.estimate_cost("meta-llama/llama-3.3-70b-instruct:free", 5000, 800) == 0.0   # free tier costs nothing
    assert s["by_purpose"]["chat"]["cost_usd"] > 0
    assert s["budgets"]["tool_output_chars"] >= 1500 and s["budgets"]["brief_tier"] in ("bulk", "main")
    assert usage.estimate_cost("openai/gpt-4o-mini", 1_000_000, 0) == 0.15


def test_tool_output_budget_compaction_and_dedupe(monkeypatch):
    from backend.llm import agent as ag
    a = ag.MarketerAgent.__new__(ag.MarketerAgent); a._seen_calls = {}; a.purpose = "chat"
    big = {"rows": [{"id": i, "ctr": 1.23456789, "_stats_raw": {"x": "y" * 50}, "empty": None, "note": ""} for i in range(2000)]}
    monkeypatch.setitem(ag.TOOLS, "fake_big", lambda **kw: big)
    monkeypatch.setitem(ag.TOOL_BUDGETS, "fake_big", 3000)
    out = a._run_tool("fake_big", {"q": 1})
    assert len(out) < 3400 and "truncated" in out and "_stats_raw" not in out and "1.2346" in out
    again = a._run_tool("fake_big", {"q": 1})
    assert "identical call already answered" in again and len(again) < 1200
    other = a._run_tool("fake_big", {"q": 2})
    assert "identical call" not in other
    # write-type tools are never de-duplicated
    monkeypatch.setitem(ag.TOOLS, "record_ideas", lambda **kw: {"ok": True})
    assert "identical call" not in a._run_tool("record_ideas", {"ideas": []}) and "identical call" not in a._run_tool("record_ideas", {"ideas": []})


def test_history_and_round_budgets_are_settings():
    from backend.guidance import set_engine_setting, ENGINE_SETTINGS
    for k in ("llm_tool_output_chars", "llm_history_messages", "llm_max_rounds", "llm_max_rounds_autopilot", "llm_brief_tier"):
        assert k in ENGINE_SETTINGS
    assert set_engine_setting("llm_history_messages", 4)["ok"] and set_engine_setting("llm_tool_output_chars", 100).get("error")
    set_setting("llm_history_messages", "")
    from backend.llm.tools import TOOLS
    assert "token_usage" in TOOLS and "budgets" in TOOLS["token_usage"]()


def test_prompt_caching_marker_for_claude_models(monkeypatch):
    """The system message is sent as a cache_control text block for Claude models and left as plain text otherwise."""
    import backend.llm.provider as pv
    captured = {}
    class FakeResp:
        status_code = 200
        text = ""
        def json(self):
            return {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 10, "completion_tokens": 1, "cost": 0.0001}, "model": "anthropic/claude-sonnet-4.5"}
    class FakeSession:
        def post(self, url, headers=None, json=None, timeout=None):
            captured["payload"] = json; return FakeResp()
    set_setting("llm_provider", "openrouter"); set_setting("llm_base_url", "https://openrouter.ai/api/v1"); set_setting("llm_model", "anthropic/claude-sonnet-4.5"); set_setting("llm_api_key", "sk-or-v1-testkey-000000000000000000000000")
    c = pv.LLMClient(); c.session = FakeSession(); c.purpose = "test"
    r = c.chat([{"role": "system", "content": "SYSTEM DOCTRINE"}, {"role": "user", "content": "hi"}], tools=None)
    sysmsg = captured["payload"]["messages"][0]
    assert isinstance(sysmsg["content"], list) and sysmsg["content"][0]["cache_control"] == {"type": "ephemeral"} and captured["payload"]["usage"] == {"include": True}
    assert r["content"] == "ok"
    set_setting("llm_model", "openai/gpt-4o-mini")
    c2 = pv.LLMClient(); c2.session = FakeSession()
    c2.chat([{"role": "system", "content": "SYSTEM"}, {"role": "user", "content": "hi"}], tools=None)
    assert isinstance(captured["payload"]["messages"][0]["content"], str)
    set_setting("llm_api_key", ""); set_setting("llm_provider", "openai_compatible")
