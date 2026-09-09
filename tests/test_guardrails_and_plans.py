import json
from datetime import date
from backend.database import set_setting, get_setting


def test_limits_defaults_overrides_and_validation():
    from backend import guardrails as g
    set_setting("comms_limits", "")
    eff = g.effective_limits("high_volatility_down", "Core")
    assert eff["multiplier"] == 0.5 and eff["total_per_week"] == 2 and eff["per_user"]["push"]["per_week"] == 1
    assert g.effective_limits("capitulation")["total_per_week"] == 0
    r = g.set_limits({"per_user": {"push": {"per_week": 3}}, "total_per_week": 6}, "test")
    assert r["ok"] and g.limits()["per_user"]["push"]["per_week"] == 3 and g.limits()["total_per_week"] == 6 and g.limits()["per_user"]["email"]["per_week"] == 3
    assert g.set_limits({"per_user": {"push": {"per_week": 99}}}, "test").get("error")
    assert g.set_limits({"regime_multiplier": {"chop": 2}}, "test").get("error")
    assert g.set_north_star("short").get("error") and g.set_north_star("Maximise incremental active trading weeks at the lowest message load.")["ok"]
    set_setting("comms_limits", ""); set_setting("north_star", "")


def test_sop_framework_respects_global_limits():
    from backend import sops, guardrails
    set_setting("comms_limits", json.dumps({"per_user": {"email": {"per_week": 1}}}))
    spec = json.loads(json.dumps(next(s for s in sops.LIBRARY if s["id"] == "sop_verified_to_funded")))
    spec["steps"] = [dict(st, channel="email") for st in spec["steps"]]     # 4 emails in 1 week
    chk = sops.sop_check(spec)
    assert any("email limit of 1/week" in p for p in chk["problems"]), chk
    set_setting("comms_limits", "")
    assert any("email limit of 3/week" in p for p in sops.sop_check(spec)["problems"])      # default cap still applies
    spec["steps"] = spec["steps"][:3]
    assert not any("email limit" in p for p in sops.sop_check(spec)["problems"])


def test_peace_index_and_monitor(monkeypatch):
    from backend import guardrails as g, segments, approvals
    from backend.moengage import mock
    from backend.moengage.executors import register_all
    register_all(); set_setting("mock_mode", "true"); set_setting("comms_limits", "")
    segments.sync(mock.segments())
    for p in approvals.list_proposals(status="pending", limit=300):
        approvals.reject(p["id"], note="reset", decided_by="test")
    # 5 pending pushes to LVT this week → over the 4/week push cap
    for i in range(5):
        approvals.propose("create_campaign", f"LVT push {i}", {"name": f"LVT_test_{i}", "channel": "push", "target_segment": "LVT_Sep26", "variants": [{"title": "t", "body": "b", "cta": "c"}], "schedule": {"date": date.today().isoformat()},
                                                              "goal": {"transition": "activated_habitual", "hypothesis": "h", "primary_kpi": "k", "target": "t", "guardrail_metric": "g", "control_group_pct": 20, "measurement_window_days": 7, "kill_criteria": "k"}}, created_by="test")
    pi = g.peace_index(mock.campaigns(), "trending_up")
    lvt = next(f for f in pi["families"] if f["family"] == "LVT")
    assert lvt["status"] == "too_much" and any("push" in b for b in lvt["breaches"]), lvt
    assert lvt["planned_7d"].get("push") == 5
    assert pi["counts"]["too_much"] >= 1
    # capitulation zeroes the caps → everything with any touch is too_much
    pi2 = g.peace_index(mock.campaigns(), "capitulation")
    assert pi2["multiplier"] == 0.0
    mon = g.sop_monitor("trending_up")
    assert "findings" in mon and "checked_runs" in mon
    for p in approvals.list_proposals(status="pending", limit=300):
        approvals.reject(p["id"], note="cleanup", decided_by="test")


def test_flight_plan_document_and_month_summary():
    from backend import plans
    bad = plans.create_plan({"title": "x"}, "test"); assert not bad["ok"]
    r = plans.create_plan({"title": "FTD to first trade — October", "month": "2026-10", "objective": "First spot trade within 72h of first deposit", "north_star_link": "funded_activated → weekly actives",
                           "transition": "funded_activated", "cohort_family": "FTD_NOTRADE", "sop_id": "sop_funded_to_first_trade",
                           "audience": {"definition": "first deposit, no fill", "reach": 7300, "exclusions": ["KYC pending", "DND"]},
                           "sequence": [{"day": 0, "channel": "in-app", "purpose": "guided first trade", "variants": [{"label": "A", "title": "Your ₹ is in", "body": "Three ways to start.", "cta": "Start"}]}, {"day": 1, "channel": "push", "purpose": "watchlist first"}, {"day": 2, "channel": "email", "purpose": "fees and TDS"}],
                           "kpi": {"primary": "first_trade_rate_7d", "target": "+4 pp vs holdout", "guardrail": "notification_disable_rate", "holdout_pct": 20, "window_days": 10},
                           "experiment": {"kill_criteria": ["delivery_rate < 85%"]}, "month_on_month": {"last_month_result": "+2.1 pp (CI 0.4–3.8)", "change": "move email to day 2", "expected_gain": "+1 pp"},
                           "whats_possible": {"now": ["segment exists"], "needs_data": ["order_filled.is_first_ever attribute"], "needs_api": []}}, "test")
    assert r["ok"] and r["limits_check"]["status"] in ("in_band", "over_cap", "unknown")
    doc = r["doc_md"]
    for h in ("# Flight Plan", "## 1. Objective", "## 3. Journey", "| D0 | in-app |", "## 5. KPI", "first_trade_rate_7d", "## 6. Communication limits", "## 8. Month-on-month", "+2.1 pp", "## 11. What's possible", "is_first_ever"):
        assert h in doc, h
    got = plans.get_plan(r["id"]); assert got and got["spec"]["cohort_family"] == "FTD_NOTRADE"
    ms = plans.month_summary("2026-10")
    assert ms["plans"] >= 1 and "funded_activated" in ms["by_transition"] and "verified_funded" in ms["transitions_without_plan"]
    from backend.llm.tools import TOOLS
    for t in ("north_star", "set_comms_limits", "peace_index", "guardrail_monitor", "write_flight_plan", "flight_plans"):
        assert t in TOOLS
    from backend.skills import list_skills
    assert "flight-plans-and-guardrails" in {s["name"] for s in list_skills()}
