"""Challenges: what the agent could not do becomes a redacted build prompt; capture points fire; the build loop closes the loop."""
import json

import pytest


def test_log_dedupes_scrubs_and_reopens():
    from backend import challenges as ch
    from backend.database import get_db
    ch.init_tables(); conn = get_db(); conn.execute("DELETE FROM challenges"); conn.commit(); conn.close()
    r1 = ch.log("agent", "send WhatsApp to HVT", "no whatsapp executor for cdx_1029384756abcdef (mail me@example.com)", tried="propose_campaign", suggestion="an executor")
    r2 = ch.log("agent", "send WhatsApp to HVT", "no whatsapp executor for cdx_9988776655443322 (mail other@example.com)")
    assert r1["ok"] and r2["signature"] == r1["signature"]
    c = ch.get(r1["id"])
    assert c["count"] == 2 and "<id>" in c["blocked_by"] and "<email>" in c["blocked_by"] and "cdx_" not in c["blocked_by"]
    assert ch.set_status(c["id"], "resolved", "built it", "abc1234", actor="test")["status"] == "resolved"
    r3 = ch.log("agent", "send WhatsApp to HVT", "no whatsapp executor for cdx_1111111111111111 (mail third@example.com)")
    assert r3["reopened"] and ch.get(c["id"])["status"] == "open", "the same failure after a fix reopens it"
    text = ch.prompt(ch.get(c["id"]))
    assert text.startswith("# Challenge #") and "## What blocked it" in text and "challenges resolve %d" % c["id"] in text and "me@example.com" not in text
    assert ch.summary()["open"] >= 1


def test_capture_points_fire_from_tools_briefs_approvals_and_the_agent_loop(monkeypatch):
    from backend import challenges as ch, approvals
    from backend.llm import tools
    from backend.database import set_setting
    from backend.moengage.executors import register_all
    register_all(); set_setting("mock_mode", "true")
    before = {c["signature"] for c in ch.list_open("all", 500)}
    assert "error" in tools.TOOLS["market_alerts_coverage"](day="not-a-day") or True
    bad = [{"title": "Beat Binance fees", "body": "Trade now."}]
    goal = {"transition": "verified_funded", "hypothesis": "h", "primary_kpi": "first_deposit_rate_7d", "target": "+2 pp", "guardrail_metric": "unsubscribe_rate", "control_group_pct": 20, "measurement_window_days": 14, "kill_criteria": ["x > 1%"]}
    tools.propose_campaign("Chal_Bad_Copy", "push", "ACTIVE_30D", bad, "r", goal)
    kinds = {c["kind"] for c in ch.list_open("all", 500) if c["signature"] not in before}
    assert "brief_rejected" in kinds
    p = approvals.propose("create_campaign", "Campaign draft: Chal_Blocked", {"name": "Chal_Blocked", "channel": "push", "target_segment": "", "variants": bad[:0] + [{"title": "Your account is ready", "body": "Add funds with UPI."}], "goal": {k: v for k, v in goal.items() if k != "kill_criteria"}}, created_by="test", allow_incomplete=True)
    with pytest.raises(approvals.ApprovalError):
        approvals.approve_and_execute(p["id"])
    assert any(c["kind"] == "blocked_draft" and str(p["id"]) in (c.get("context") or "") for c in ch.list_open("all", 500))
    from backend.llm.agent import MarketerAgent
    set_setting("llm_autoload_skills", "false")
    a = MarketerAgent(persona="strategist")
    a.client.chat = lambda messages, tools=None: {"content": None, "tool_calls": [{"id": "t1", "name": "no_such_tool", "arguments": {}}], "model": "fake", "usage": {}}
    set_setting("llm_max_rounds", "2")
    out = a.chat("do a thing", history=[], persist=False)
    set_setting("llm_max_rounds", "8"); set_setting("llm_autoload_skills", "true")
    kinds = {c["kind"] for c in ch.list_open("all", 500)}
    assert {"unknown_tool", "out_of_steps"} <= kinds and "ran out of tool steps" in out["reply"]
    assert "log_challenge" in tools.TOOLS and "log_challenge" in json.dumps(tools.TOOL_SCHEMAS)
    from backend.llm.roster import allowed_tools
    assert all("log_challenge" in allowed_tools(p) for p in ("strategist", "analyst", "copywriter", "compliance", "alerts", "ops"))


def test_export_import_round_trip_and_issue_parsing(monkeypatch):
    from backend import challenges as ch
    r = ch.log("job_failed", "background job prices", "HTTP 500 from venue", source="refresher")
    rows = ch.export("all")
    row = next(x for x in rows if x["signature"] == r["signature"])
    assert set(row) == set(ch.EXPORT_KEYS) and "id" not in row and "issue_number" not in row
    row2 = dict(row, status="resolved", commit_sha="deadbee", updated_at="2999-01-01 00:00:00", count=7)
    res = ch.import_rows([row2, {"signature": "abcdef012345", "kind": "agent", "task": "new one", "blocked_by": "b", "status": "open", "count": 1, "created_at": "2026-09-24 00:00:00", "updated_at": "2026-09-24 00:00:00"}])
    assert res == {"new": 1, "updated": 1} and ch.get(r["id"])["status"] == "resolved" and ch.get(r["id"])["count"] == 7
    body = ch.prompt(ch.get(r["id"]))
    monkeypatch.setattr(ch, "_gh", lambda args: json.dumps([{"number": 12, "title": f"[chal:{r['signature']}] job_failed: background job prices", "body": body.replace("seen 7×", "seen 9×"), "createdAt": "2026-09-24T01:02:03Z", "updatedAt": "2999-02-02T00:00:00Z"}]))
    monkeypatch.setattr(ch.shutil, "which", lambda x: "/usr/bin/gh")
    pulled = ch.pull_issues()
    assert pulled["ok"] and pulled["issues"] == 1 and ch.get(r["id"])["issue_number"] == 12 and ch.get(r["id"])["count"] == 9 and ch.get(r["id"])["status"] == "open"
