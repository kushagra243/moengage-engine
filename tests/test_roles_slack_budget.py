"""Builder role refuses workspace credentials; the credit ration holds; Slack approval executes a proposal; each alert can wait for a human."""
import json

import pytest


def test_a_builder_never_stores_credentials_or_customer_ids():
    from backend import roles, test_sends
    from backend.settings_policy import save_plain
    from backend.database import set_setting, get_setting
    from backend.alerts2 import cohort
    set_setting("moengage_campaign_key", "sk-campaign-000000000000000000"); set_setting("mock_mode", "false")
    r = roles.set_role("builder", actor="test")
    try:
        assert r["role"] == "builder" and "moengage_campaign_key" in r["cleared"] and get_setting("moengage_campaign_key") == "" and get_setting("mock_mode") == "true"
        assert save_plain({"moengage_data_api_key": "sk-x-00000000000000000", "telegram_alerts": "on"})["rejected"] == ["moengage_data_api_key"]
        with pytest.raises(test_sends.TestSendError, match="builder"):
            test_sends.save_users("a@example.com")
        with pytest.raises(cohort.CohortError, match="builder"):
            cohort.save_internal_users(["cdx_1"])
        import backend.main as m
        out = m.update_settings(m.SettingsPayload(values={"moengage_cookies": "a=b", "llm_daily_budget_usd": "3"}))
        assert "moengage_cookies" in out["rejected"] and "llm_daily_budget_usd" in out["saved"]
    finally:
        roles.set_role("operator", actor="test"); set_setting("llm_daily_budget_usd", "")


def test_the_credit_ration_pauses_background_work_first_and_everything_at_150_percent(monkeypatch):
    from backend.llm import budget, provider as p
    from backend.database import set_setting
    set_setting("llm_daily_budget_usd", "1.0"); set_setting("llm_background_share", "0.3")
    monkeypatch.setattr(budget, "spend_today", lambda: {"total": 0.5, "background": 0.31, "calls": 9})
    with pytest.raises(budget.BudgetExceeded, match="background"):
        budget.check("analysis")
    budget.check("chat")
    monkeypatch.setattr(budget, "spend_today", lambda: {"total": 1.2, "background": 0.1, "calls": 9})
    budget.check("chat")
    with pytest.raises(budget.BudgetExceeded):
        budget.check("copy") if False else budget.check("classification")
    monkeypatch.setattr(budget, "spend_today", lambda: {"total": 1.6, "background": 0.1, "calls": 9})
    with pytest.raises(budget.BudgetExceeded, match="paused"):
        budget.check("chat")
    c = p.LLMClient({"provider": "anthropic", "base_url": p.ANTHROPIC_BASE, "model": "claude-haiku-4-5-20251001", "api_key": "k", "temperature": 0.2, "max_tokens": 5, "model_bulk": "auto-free"})
    c.purpose = "chat"
    with pytest.raises(p.LLMError, match="credit ration"):
        c.chat([{"role": "user", "content": "hi"}])
    set_setting("llm_daily_budget_usd", ""); set_setting("llm_background_share", "")
    assert budget.status()["state"] in ("ok", "essential only", "paused")


def test_basic_model_everywhere_unless_a_purpose_is_named_premium():
    from backend.llm import provider as p
    from backend.database import set_setting
    cfg = {"provider": "anthropic", "base_url": p.ANTHROPIC_BASE, "model": "claude-haiku-4-5-20251001", "api_key": "k", "temperature": 0.2, "max_tokens": 5, "model_bulk": "auto-free"}
    set_setting("llm_premium_purposes", "")
    assert all(v[0] == "claude-haiku-4-5-20251001" for v in p.routes(cfg).values())
    set_setting("llm_premium_purposes", "copy,review")
    r = p.routes(cfg)
    assert r["copy"] == ["claude-sonnet-5", "claude-haiku-4-5-20251001"] and r["chat"] == ["claude-haiku-4-5-20251001"]
    set_setting("llm_premium_purposes", "")


FAKE_SLACK = "xox" + "b-" + "0" * 12 + "-" + "0" * 12 + "-" + "abcdefghijklmnopqrstuvwx"     # assembled so no token shape ever sits in the repository


def _slack_boot(monkeypatch, calls, replies):
    from backend import slack_out as so
    from backend.database import set_setting
    set_setting("slack_bot_token", FAKE_SLACK); set_setting("slack_channel_id", "C0TEST"); set_setting("slack_approvers", "U0APPROVER"); set_setting("slack_ask_approval", "on")

    class R:
        def __init__(self, p): self._p = p; self.status_code = 200
        def json(self): return self._p

    class S:
        @staticmethod
        def post(url, headers=None, json=None, timeout=None):
            calls.append((url.rsplit("/", 1)[-1], json)); return R({"ok": True, "channel": "C0TEST", "ts": f"171.{len(calls)}", "user": "UBOT", "team": "T1"})

        @staticmethod
        def get(url, headers=None, params=None, timeout=None):
            calls.append((url.rsplit("/", 1)[-1], params)); return R({"ok": True, "messages": replies})
    monkeypatch.setattr(so, "guarded_session", lambda scope: S)
    so.init_tables()
    return so


def test_a_slack_approve_in_the_thread_executes_the_draft_and_a_stranger_does_not(monkeypatch):
    from backend import approvals, slack_out as so
    from backend.database import set_setting
    from backend.moengage.executors import register_all
    from backend.security import redact
    register_all(); set_setting("mock_mode", "true")
    calls, replies = [], [{"text": "root", "reactions": []}, {"user": "USTRANGER", "text": "approve"}]
    so = _slack_boot(monkeypatch, calls, replies)
    try:
        assert FAKE_SLACK[:16] not in redact(f"token {FAKE_SLACK} here")
        goal = {"transition": "verified_funded", "hypothesis": "h", "primary_kpi": "first_deposit_rate_7d", "target": "+2 pp", "guardrail_metric": "unsubscribe_rate", "control_group_pct": 20, "measurement_window_days": 14, "kill_criteria": ["x > 1%"]}
        p = approvals.propose("create_campaign", "Campaign draft: Slack_Approved_One", {"name": "Slack_Approved_One", "channel": "push", "target_segment": "KYC_APPROVED_NODEP", "variants": [{"title": "Your account is ready", "body": "Add funds with UPI."}], "goal": goal}, created_by="test")
        assert p.get("slack", {}).get("ok") and calls[0][0] == "chat.postMessage" and "Slack_Approved_One" in json.dumps(calls[0][1])
        assert so.status()["awaiting"] >= 1
        r = so.poll()
        assert not r["decided"] and approvals.get_proposal(p["id"])["status"] == "pending", "a stranger's approve is ignored"
        replies.append({"user": "U0APPROVER", "text": "approve, ship it"})
        r = so.poll()
        assert r["decided"] and r["decided"][0]["status"] == "executed" and approvals.get_proposal(p["id"])["decided_by"] == "slack:U0APPROVER"
        assert any(c[0] == "chat.postMessage" and c[1].get("thread_ts") and "Approved by <@U0APPROVER>" in c[1]["text"] for c in calls)
        assert so.status()["awaiting"] == 0
    finally:
        set_setting("slack_bot_token", ""); set_setting("slack_channel_id", ""); set_setting("slack_approvers", "")


def test_a_reaction_rejects_and_each_alert_can_wait_for_a_human(monkeypatch):
    from backend import approvals, slack_out as so
    from backend.database import set_setting
    from backend.alerts2 import discovery, rules
    from datetime import datetime, timedelta
    calls, replies = [], [{"text": "root", "reactions": [{"name": "x", "users": ["U0APPROVER"]}]}]
    so = _slack_boot(monkeypatch, calls, replies)
    discovery.register(); set_setting("mock_mode", "true"); set_setting("ma2_alert_approval", "ask")
    try:
        assert discovery.approval_mode() == "ask"
        cfg = rules.config(stage="internal"); now = datetime.now(discovery.IST)
        c = {"signal": "large_trades", "token": "ETH", "product": "futures", "direction": "up", "value": 3.8e6, "det_key": "large_trades|ETH|t1", "source": "rules"}
        pr = discovery.propose_alert(c, {"title": "Large buying in ETH", "body": "A large buy of about $3.8M was recorded in ETH. See the market."}, {"signal": "large_trades", "token": "ETH", "title": "t", "body": "b"}, ["internal"], now, cfg, "test")
        assert pr["kind"] == "alert_send" and pr.get("slack", {}).get("ok")
        r = so.poll()
        assert r["decided"][0]["decision"] == "reject" and approvals.get_proposal(pr["id"])["status"] == "rejected"
        pr2 = discovery.propose_alert(dict(c, det_key="large_trades|ETH|t2"), {"title": "Large buying in ETH", "body": "A large buy of about $3.8M was recorded in ETH. See the market."}, {"signal": "large_trades", "token": "ETH"}, ["internal"], now - timedelta(hours=2), cfg, "test")
        with pytest.raises(approvals.ApprovalError, match="stale"):
            approvals.approve_and_execute(pr2["id"], decided_by="test")
        pr3 = discovery.propose_alert(dict(c, det_key="large_trades|ETH|t3"), {"title": "Large buying in ETH", "body": "A large buy of about $3.8M was recorded in ETH. See the market."}, {"signal": "large_trades", "token": "ETH"}, ["internal"], now, cfg, "test")
        done = approvals.approve_and_execute(pr3["id"], decided_by="test")
        assert done["status"] == "executed" and done["result"]["status"] == "recorded_mock", done.get("error")
    finally:
        set_setting("ma2_alert_approval", "auto"); set_setting("slack_bot_token", ""); set_setting("slack_channel_id", ""); set_setting("slack_approvers", "")


def test_telemetry_carries_counts_only():
    from backend import telemetry
    snap = telemetry.snapshot(7)
    blob = json.dumps(snap)
    assert set(snap) >= {"model", "spend", "tool_errors", "jobs", "alerts", "approvals", "skills", "challenges"}
    assert "@" not in blob and "title" not in json.dumps(snap["alerts"]) and "variants" not in blob
