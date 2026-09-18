"""
The working half of the quiet terminal: every missing input becomes a question, answering it puts the value in the right
place, and the blocked thing then runs. Plus the parity guarantee: every operator module opens inside the new shell.
"""
import json

import pytest

GOAL_FULL = {"transition": "verified_funded", "hypothesis": "h", "primary_kpi": "first_deposit_rate_7d", "target": "+2 pp vs holdout", "guardrail_metric": "unsubscribe_rate",
             "control_group_pct": 20, "measurement_window_days": 14, "kill_criteria": ["unsubscribe_rate > 0.4%"], "suppressions": ["DND"]}
VARIANT = [{"title": "Your account is ready", "body": "Add funds with UPI in under a minute.", "cta": "Add funds"}]


def _save(values):
    import backend.main as m
    return m.update_settings(m.SettingsPayload(values=values))


def _boot():
    from backend.database import set_setting
    from backend.moengage.executors import register_all
    register_all()
    set_setting("mock_mode", "true"); set_setting("v3_deferred_json", "{}")


def test_a_draft_short_of_inputs_is_kept_as_a_question_not_thrown_away():
    from backend import approvals, v3, v3_ops
    from backend.llm import tools
    _boot()
    goal = {k: v for k, v in GOAL_FULL.items() if k not in ("kill_criteria", "guardrail_metric")}
    r = tools.propose_campaign("V3Ops_Incomplete_Nudge", "push", "", VARIANT, "verified users who never funded", goal)
    assert r["status"] == "pending_incomplete" and "needs_from_operator" in r, r
    pid = r["proposal_id"]
    with pytest.raises(approvals.ApprovalError):
        approvals.approve_and_execute(pid)                                         # it can never run while incomplete…
    assert approvals.get_proposal(pid)["status"] == "pending"                        # …and the attempt does not burn it
    ask = next(a for a in v3_ops.asks()["asks"] if a["id"] == f"proposal:{pid}")
    keys = {f["key"] for f in ask["fields"]}
    assert keys == {"target_segment", "goal.guardrail_metric", "goal.kill_criteria"}, keys
    assert ask["button"] == "SAVE AND RE-CHECK THE DRAFT" and "cannot start until 3 things are filled in" in ask["title"]
    d = next(x for x in v3.today()["decisions"] if x["id"] == f"proposal:{pid}")
    assert d["primary"] == {"label": "FILL IN WHAT IS MISSING", "go": f"#asks?focus=proposal:{pid}"} and d["blocked"]

    part = v3_ops.answer(f"proposal:{pid}", {"target_segment": "KYC_APPROVED_NODEP"}, _save, actor="test")
    assert part["ok"] and "2 more" in part["toast"] and approvals.get_proposal(pid)["payload"]["target_segment"] == "KYC_APPROVED_NODEP"
    full = v3_ops.answer(f"proposal:{pid}", {"goal.guardrail_metric": "unsubscribe_rate", "goal.kill_criteria": "unsubscribe_rate > 0.4%\ncomplaint_rate > 0.1%"}, _save, actor="test")
    assert full["ok"] and full["next"]["go"] == f"#today?decision=proposal:{pid}", full
    pl = approvals.get_proposal(pid)["payload"]
    assert pl["goal"]["kill_criteria"] == ["unsubscribe_rate > 0.4%", "complaint_rate > 0.1%"] and pl["goal"]["control_group_pct"] == 20, "answers merge, nothing else is lost"
    assert not any(a["id"].startswith(f"proposal:{pid}") for a in full["asks"]["asks"])
    done = v3.resolve(f"proposal:{pid}", "approve", actor="test")
    assert done["ok"] and approvals.get_proposal(pid)["status"] == "executed", "after putting in the required thing it works"


def test_a_broken_rule_is_still_refused_outright():
    from backend.llm import tools
    _boot()
    bad = [{"title": "Beat Binance fees today", "body": "Trade now.", "cta": "Trade"}]
    r = tools.propose_campaign("V3Ops_Bad_Copy", "push", "ACTIVE_30D", bad, "r", dict(GOAL_FULL))
    assert r.get("error") == "brief rejected" and any("venue" in p or "competitor" in p for p in r["problems"])


def test_setting_questions_save_to_settings_and_secrets_never_come_back():
    from backend import v3_ops
    from backend.database import set_setting, get_setting
    import backend.main as m
    _boot()
    for k in ("moengage_created_by", "moengage_user_email", "moengage_campaign_key"):
        set_setting(k, "")
    ids = {a["id"] for a in v3_ops.asks()["asks"]}
    assert {"setting:moengage_created_by", "setting:moengage_campaign_key", "setting:mock_mode"} <= ids
    assert not v3_ops.answer("setting:moengage_created_by", {"moengage_created_by": "not-an-email"}, _save)["ok"]
    ok = v3_ops.answer("setting:moengage_created_by", {"moengage_created_by": "crm@example.com"}, _save)
    assert ok["ok"] and get_setting("moengage_created_by") == "crm@example.com" and "setting:moengage_created_by" not in {a["id"] for a in ok["asks"]["asks"]}
    secret = "sk-test-campaign-key-0123456789"
    ok = v3_ops.answer("setting:moengage_campaign_key", {"moengage_campaign_key": secret}, _save)
    assert ok["ok"] and "encrypted" in ok["toast"]
    blob = json.dumps([ok, v3_ops.asks(), v3_ops.engine(m.status())], default=str)
    assert secret not in blob, "a saved key is never sent back to the page"
    eng = v3_ops.engine(m.status())
    fld = next(f for g in eng["groups"] for f in g["fields"] if f["key"] == "moengage_campaign_key")
    assert fld["set"] is True and fld["value"] == "" and fld["type"] == "secret"
    set_setting("moengage_campaign_key", ""); set_setting("moengage_created_by", "")


def test_going_live_is_refused_until_its_own_prerequisites_are_answered():
    from backend import v3_ops
    from backend.database import set_setting, get_setting
    _boot(); set_setting("moengage_app_id", "")
    r = v3_ops.answer("setting:mock_mode", {}, _save)
    assert not r["ok"] and "Workspace Id" in r["toast"] and get_setting("mock_mode") == "true"


def test_employee_ids_question_refuses_personal_details():
    from backend import v3_ops
    from backend.database import set_setting
    import os
    from backend.alerts2 import cohort
    _boot(); set_setting("ma2_internal_delivery", "inform")
    f = os.path.join(cohort.data_dir(), "internal_users.json")        # another test may have saved a list already; this one starts from none
    if os.path.exists(f):
        os.remove(f)
    try:
        assert "alerts:employee_ids" in {a["id"] for a in v3_ops.asks()["asks"]}
        bad = v3_ops.answer("alerts:employee_ids", {"user_ids": "someone@example.com\ncdx_1"}, _save)
        assert not bad["ok"] and bad["toast"].startswith("Not saved")
        good = v3_ops.answer("alerts:employee_ids", {"user_ids": "cdx_100234\ncdx_100871"}, _save)
        assert good["ok"] and "alerts:employee_ids" not in {a["id"] for a in good["asks"]["asks"]}
    finally:
        set_setting("ma2_internal_delivery", "event")


def test_alerts_and_engine_screens_speak_in_rows_and_verbs():
    from backend import v3_ops
    import backend.main as m
    from tests.test_v3 import LEAK, _strings
    _boot()
    al = v3_ops.alerts()
    assert al["lead"] and al["copy"] and all({"title", "body", "ok"} <= set(c) for c in al["copy"]) and al["cohorts"][0]["state"] in ("READY", "RUNNING", "LOCKED")
    assert all(b["verb"].endswith("→") and b["go"] == "#asks" for b in al["blockers"]) and al["ops"]["go"] == "#tool?m=alerts"
    eng = v3_ops.engine(m.status())
    assert [s["k"] for s in eng["state"]] == ["MOENGAGE", "THE BRAIN", "MORNING RUN", "SECRETS"] and all(j["verb"] == "RUN IT NOW →" for j in eng["jobs"])
    for name, data in {"asks": v3_ops.asks(), "alerts": al, "workbench": v3_ops.workbench()}.items():
        for s in _strings(data):
            assert not LEAK.search(s), f"{name}: leaked internals in {s!r}"


def test_every_operator_module_opens_inside_the_new_shell():
    """Parity: nothing the operator console did was removed. Each module is one row on Workbench and is framed by this origin only."""
    import re
    from fastapi.testclient import TestClient
    from backend import v3_ops
    import backend.main as m
    mods = [x["m"] for x in v3_ops.workbench()["modules"]]
    js = open("frontend/terminal/terminal.js").read()
    assert set(mods) == set(re.findall(r"^views\.([a-z]+) = ", js, re.M)), "every view of the operator console is on the Workbench"
    c = TestClient(m.app, base_url="http://127.0.0.1:8080"); h = {"Host": "127.0.0.1:8080"}
    framed = c.get("/ops?embed=1", headers=h)
    assert framed.status_code == 200 and framed.headers["x-frame-options"] == "SAMEORIGIN" and "frame-ancestors 'self'" in framed.headers["content-security-policy"]
    for path in ("/", "/ops"):
        r = c.get(path, headers=h)
        assert r.headers["x-frame-options"] == "DENY" and "frame-ancestors 'none'" in r.headers["content-security-policy"], f"{path} must never be framed"
    assert "embed" in open("frontend/terminal/boot.js").read() and ".embed .rail" in open("frontend/terminal/terminal.css").read()
    page = open("frontend/v3/v3.js").read()
    for scr in ("asks", "alerts", "engine", "bench", "tool"):
        assert f"{scr}: render" in page
