"""A paused brain spends nowhere; the diagnose bundle carries errors and counts but never a secret or an address."""
import json

import pytest


def test_pause_stops_every_model_call_including_the_fallback(monkeypatch):
    from backend import diagnose as dg
    from backend.llm import provider as p, budget
    from backend.database import set_setting
    set_setting("llm_fallback_api_key", "sk-or-v1-fallbackkey000000000000000000"); set_setting("llm_fallback_on", "errors,budget")
    dg.pause(actor="test", why="debugging")
    try:
        assert dg.paused() and budget.status()["state"] == "paused"
        with pytest.raises(budget.BudgetExceeded, match="paused"):
            budget.check("chat")
        called = []

        class S:
            def post(self, url, **k): called.append(url); raise AssertionError("no request may leave while paused")
        monkeypatch.setattr(p, "guarded_session", lambda scope: S())
        c = p.LLMClient({"provider": "anthropic", "base_url": p.ANTHROPIC_BASE, "model": "claude-haiku-4-5-20251001", "api_key": "k", "temperature": 0.2, "max_tokens": 5, "model_bulk": "auto-free"}); c.session = S(); c.purpose = "analysis"
        with pytest.raises(p.LLMError, match="paused"):
            c.chat([{"role": "user", "content": "hi"}])
        assert not called, "not even the OpenRouter fallback"
    finally:
        dg.resume(actor="test"); set_setting("llm_fallback_api_key", ""); set_setting("llm_fallback_on", "")
    assert not dg.paused() and budget.status()["state"] != "paused"


def test_diagnose_bundle_is_complete_and_scrubbed():
    from backend import diagnose as dg
    from backend.database import set_setting
    from backend.security import audit
    set_setting("moengage_campaign_key", "sk-campaign-000000000000000000000")
    audit("tool.failed", {"contact": "someone@example.com", "user": "cdx_1029384756abcdef", "why": "test row"}, actor="test")
    try:
        b = dg.bundle(2)
        assert set(b) >= {"machine", "build", "role", "paused", "model", "budget", "credits", "settings", "jobs", "preflight", "tool_errors", "failed_proposals", "recent_audit_failures", "tracebacks", "challenges", "telemetry"}
        assert b["settings"].get("moengage_campaign_key_set") == "true" and "moengage_campaign_key" not in {k for k in b["settings"] if not k.endswith("_set")}
        text = json.dumps(b, default=str) + dg.markdown(b)
        assert "sk-campaign-000000000000000000000" not in text and "someone@example.com" not in text and "cdx_1029384756abcdef" not in text
        assert "<email>" in text and "<id>" in text, "the audit row is present but scrubbed"
        assert {"by_purpose", "by_model", "by_hour", "biggest_calls"} <= set(b["credits"])
        md = dg.markdown(b)
        assert md.startswith("# Diagnose ·") and "## Model and credits" in md and "## Tracebacks" in md
    finally:
        set_setting("moengage_campaign_key", "")
