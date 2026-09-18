"""
Test sends to a saved list of test users, the cohort choice at launch, standing drafts that never expire, and a 401 on the
business-events list that says which of the three credentials is wrong.
"""
import json

import pytest

GOAL = {"transition": "verified_funded", "hypothesis": "h", "primary_kpi": "first_deposit_rate_7d", "target": "+2 pp vs holdout", "guardrail_metric": "unsubscribe_rate",
        "control_group_pct": 20, "measurement_window_days": 14, "kill_criteria": ["unsubscribe_rate > 0.4%"], "suppressions": ["DND"]}
A, B = "asha.rao@example.com", "cdx_100234"


def _boot():
    from backend.database import set_setting
    from backend.moengage.executors import register_all
    from backend import test_sends
    register_all(); test_sends.register()
    set_setting("mock_mode", "true"); set_setting(test_sends.SETTING, "")


def test_test_users_are_stored_encrypted_and_only_ever_shown_masked():
    from backend import test_sends, v3_ops
    import backend.main as m
    import sqlite3
    from backend import database as db
    _boot()
    with pytest.raises(test_sends.TestSendError):
        test_sends.save_users("+91 98765 43210")
    with pytest.raises(test_sends.TestSendError):
        test_sends.save_users("\n".join(f"u{i}@example.com" for i in range(11)))
    with pytest.raises(test_sends.TestSendError):
        test_sends.save_users("not@an-email")
    meta = test_sends.save_users(f"{A}\n{B}\n{A}")
    assert meta["count"] == 2 and meta["by_email"] == 1 and meta["by_id"] == 1 and test_sends.users() == [A, B]
    raw = sqlite3.connect(db.DB_PATH).execute("select value from settings where key=?", (test_sends.SETTING,)).fetchone()[0]
    assert A not in raw, "encrypted at rest"
    blob = json.dumps([meta, v3_ops.asks(), v3_ops.alerts()["test_users"], v3_ops.engine(m.status())["test_users"]], default=str, ensure_ascii=False)
    assert A not in blob and B not in blob and "a•••" in blob


def test_a_test_send_is_recorded_like_any_write_and_carries_no_addresses():
    from backend import test_sends, approvals
    _boot()
    no = test_sends.send("Large buying in ETH", "A large buy of about $3.8M was recorded in ETH. See the market.", name="t")
    assert not no["ok"] and "no test users" in no["error"]
    test_sends.save_users(f"{A}\n{B}")
    bad = test_sends.send("Beat Binance fees", "Trade with 50x leverage now", name="t")
    assert not bad["ok"] and "breaks a rule" in bad["error"], "a test is still copy that reaches a phone"
    ok = test_sends.send("Large buying in ETH", "A large buy of about $3.8M was recorded in ETH. See the market.", name="MA2 test")
    assert ok["ok"] and ok["sent_to"] == 2 and "practice mode" in ok["status"]
    p = approvals.get_proposal(ok["proposal_id"])
    assert p["kind"] == "test_send" and p["status"] == "executed" and A not in json.dumps(p, default=str) and B not in json.dumps(p, default=str)


def test_the_moengage_request_is_the_documented_one():
    from backend import test_sends
    _boot(); test_sends.save_users(f"{A}\n{B}")
    bodies = test_sends._bodies("Title", "Body", "MA2 test")
    assert [b["test_campaign_meta"]["identifier"] for b in bodies] == ["USER_ATTRIBUTE_USER_EMAIL", "USER_ATTRIBUTE_UNIQUE_ID"]
    assert bodies[0]["test_campaign_meta"]["identifier_values"] == [A] and bodies[0]["channel"] == "PUSH" and bodies[0]["request_id"]
    and_basic = bodies[0]["campaign_content"]["content"]["push"]["android"]["basic_details"]
    assert and_basic == {"title": "Title", "message": "Body"}
    draft = test_sends._bodies("", "", "x", draft_id="0123456789abcdef01234567")[0]
    assert draft["draft_id"] and "campaign_content" not in draft and "channel" not in draft
    reg = json.load(open("backend/moengage/endpoints.default.json"))
    ep = next(v for k, v in (reg.get("public") or reg).items() if k == "campaign_test_v5")
    assert ep["path"] == "/v5/campaigns/test" and ep["key"] == "campaigns"


def test_every_new_draft_is_test_sent_by_default_and_today_offers_a_test_first():
    from backend import test_sends, approvals, v3
    from backend.database import set_setting
    _boot(); test_sends.save_users(A); set_setting("v3_deferred_json", "{}")
    p = approvals.propose("create_campaign", "Campaign draft: TS_Auto_Test", {"name": "TS_Auto_Test", "channel": "push", "target_segment": "KYC_APPROVED_NODEP",
                          "variants": [{"title": "Your account is ready", "body": "Add funds with UPI in under a minute."}], "goal": dict(GOAL)}, rationale="r", created_by="test")
    d = next(x for x in v3.today()["decisions"] if x["id"] == f"proposal:{p['id']}")
    assert d["test"] == {"label": "SEND A TEST TO THE TEST USERS", "proposal_id": p["id"]}
    done = approvals.approve_and_execute(p["id"], decided_by="test")
    assert done["status"] == "executed" and done["result"]["test_send"]["ok"] and done["result"]["test_send"]["sent_to"] == 1
    set_setting("moengage_test_on_create", "false")
    p2 = approvals.propose("create_campaign", "Campaign draft: TS_Auto_Test_Off", {"name": "TS_Auto_Test_Off", "channel": "push", "target_segment": "KYC_APPROVED_NODEP",
                           "variants": [{"title": "Your account is ready", "body": "Add funds with UPI in under a minute."}], "goal": dict(GOAL)}, rationale="r", created_by="test")
    assert "test_send" not in approvals.approve_and_execute(p2["id"], decided_by="test")["result"]
    set_setting("moengage_test_on_create", "true")


def test_a_standing_campaign_draft_never_expires_while_a_one_off_market_draft_still_does():
    from backend import approvals
    from backend.database import get_db
    _boot()
    base = {"channel": "push", "target_segment": "INTERNAL_EMPLOYEES", "variants": [{"title": "Market note", "body": "See the market."}], "goal": dict(GOAL), "ttl_hours": 4}
    standing = approvals.propose("create_campaign", "Campaign draft: TS_Standing", {**base, "name": "TS_Standing", "schedule": {"type": "business_event_triggered", "business_event": "MA2_Discovery_INTERNAL"}}, created_by="test")
    oneoff = approvals.propose("create_campaign", "Campaign draft: TS_OneOff", {**base, "name": "TS_OneOff", "schedule": {"type": "one_time"}}, created_by="test")
    conn = get_db(); conn.execute("UPDATE proposals SET created_at=datetime('now','-3 days') WHERE id IN (?,?)", (standing["id"], oneoff["id"])); conn.commit(); conn.close()
    approvals.expire_stale()
    assert approvals.get_proposal(standing["id"])["status"] == "pending", "ttl_hours is how long each message lives, not how long the draft may wait"
    assert approvals.get_proposal(oneoff["id"])["status"] == "expired"


def test_a_401_on_business_events_names_the_wrong_credential_and_each_cause_has_its_own_question(monkeypatch):
    from backend.alerts2 import discovery
    from backend.moengage.public_api import PublicAPI, PublicAPIError
    from backend.database import set_setting
    from backend import v3_ops
    _boot()
    set_setting("mock_mode", "false"); set_setting("moengage_app_id", "APPID"); set_setting("moengage_campaign_key", "k"); set_setting("moengage_data_api_key", "k"); set_setting("ma2_business_events_confirmed", "")
    try:
        def boom(*a, **k):
            raise PublicAPIError("business_events_list: auth rejected (HTTP 401). Check Workspace ID, the campaigns key and the data centre (api-03).")
        ok = lambda *a, **k: {"status": 200, "data": {}}
        monkeypatch.setattr(PublicAPI, "business_events_list", boom)
        row = lambda: next(r for r in discovery.preflight() if r["check"] == "Business event")
        ask_ids = lambda: {a["id"] for a in v3_ops.asks()["asks"]}

        monkeypatch.setattr(PublicAPI, "campaigns_search", boom); monkeypatch.setattr(PublicAPI, "test_connection", boom)
        r = row(); assert r["cause"] == "workspace_or_dc" and "_DEBUG" in r["fix"] and "alerts:workspace" in ask_ids()
        monkeypatch.setattr(PublicAPI, "test_connection", ok)
        r = row(); assert r["cause"] == "campaigns_key" and "paste the Campaigns API key again" in r["fix"] and "alerts:campaigns_key" in ask_ids()
        monkeypatch.setattr(PublicAPI, "campaigns_search", ok)
        r = row(); assert r["cause"] == "key_permission" and r["detail"].endswith("lacks that permission"), "the whole sentence, never cut mid-word"
        assert "alerts:business_event_confirm" in ask_ids()
        blockers = {b["check"]: b for b in v3_ops.alerts()["blockers"]}
        assert blockers["Business event"]["go"] == "#asks?focus=alerts:business_event_confirm" and blockers["Business event"]["fix"]
        done = v3_ops.answer("alerts:business_event_confirm", {}, lambda v: {})
        assert done["ok"] and row()["ok"] is True and "confirmed by you" in row()["detail"]
    finally:
        set_setting("mock_mode", "true"); set_setting("moengage_app_id", ""); set_setting("moengage_campaign_key", ""); set_setting("moengage_data_api_key", ""); set_setting("ma2_business_events_confirmed", "")


def test_an_expired_launch_has_its_own_question_pointing_back_to_the_brief():
    from backend import approvals, v3_ops
    from backend.database import get_db
    _boot()
    p = approvals.propose("create_campaign", "Campaign draft: MA2_Discovery_Push_INTERNAL_T", {"name": "MA2_Discovery_Push_INTERNAL_T", "channel": "push", "target_segment": "INTERNAL_EMPLOYEES",
                          "variants": [{"title": "Market note", "body": "See the market."}], "goal": dict(GOAL), "schedule": {"type": "business_event_triggered", "business_event": "MA2_Discovery_INTERNAL"}}, created_by="test")
    conn = get_db(); conn.execute("UPDATE proposals SET status='expired' WHERE kind='create_campaign' AND title LIKE '%MA2_Discovery%'"); conn.commit(); conn.close()   # earlier tests may have left a live one
    a = next(x for x in v3_ops.asks()["asks"] if x["id"] == "alerts:launch")
    assert a["link"]["go"] == "#alerts" and "waited too long" in a["why"]
    b = next(x for x in v3_ops.alerts()["blockers"] if x["check"] == "Campaign draft")
    assert b["go"] == "#asks?focus=alerts:launch" and "expired" in b["detail"]
