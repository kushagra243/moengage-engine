import json
from backend import approvals
from backend.database import set_setting
from backend.moengage.executors import register_all
from backend.moengage import capture, registry
from backend.moengage.session import parse_cookies, cookie_summary, DashboardSession, WriteBlocked
import pytest

register_all()


def test_write_requires_approval_and_executes_in_mock():
    set_setting("mock_mode", "true")
    p = approvals.propose("create_segment", "Seg", {"name": "At-risk VIPs", "criteria": {"lifetime_value": {"gt": 300}}}, "test", created_by="agent")
    assert p["status"] == "pending" and p["preview"]["mode"] == "mock"
    with pytest.raises(approvals.ApprovalError):
        approvals.approve_and_execute(999)
    done = approvals.approve_and_execute(p["id"], decided_by="user")
    assert done["status"] == "executed" and done["result"]["success"] and done["result"]["_source"] == "mock"
    with pytest.raises(approvals.ApprovalError):
        approvals.approve_and_execute(p["id"])          # cannot execute twice
    goal = {"transition": "funded_activated", "hypothesis": "h", "primary_kpi": "first_trade_rate_7d", "target": "18→21%", "guardrail_metric": "uninstall_rate_7d",
            "control_group_pct": 10, "measurement_window_days": 7, "kill_criteria": "guardrail breach", "suppressions": ["loss-dormant"]}
    ok = approvals.propose("create_campaign", "c", {"name": "x", "channel": "push", "target_segment": "s", "variants": [{"title": "t", "body": "b", "cta": "Go"}], "goal": goal}, "r")
    r = approvals.reject(ok["id"], "no")
    assert r["status"] == "rejected"
    # no goal → refused at the gate; bad channel → refused; tiny holdout → refused
    with pytest.raises(ValueError):
        approvals.propose("create_campaign", "c", {"name": "x", "channel": "push", "target_segment": "s", "variants": [{"title": "t", "body": "b"}]}, "r")
    with pytest.raises(ValueError):
        approvals.propose("create_campaign", "c", {"name": "x", "channel": "fax", "target_segment": "s", "variants": [], "goal": goal}, "r")
    with pytest.raises(ValueError):
        approvals.propose("create_campaign", "c", {"name": "x", "channel": "push", "target_segment": "s", "variants": [{"title": "t", "body": "b"}], "goal": {**goal, "control_group_pct": 2}}, "r")


def test_clm_doctrine_tools():
    from backend.llm.tools import TOOLS, experiment_plan, campaign_brief_check
    set_setting("mock_mode", "true")
    audit = TOOLS["clm_program_audit"]()
    assert audit["campaigns_total"] == 6 and "coverage" in audit and isinstance(audit["uncovered_transitions"], list)
    plan = experiment_plan(18.0, 3.0, 2000, control_group_pct=10)
    assert plan["required_per_arm"] > 1000 and plan["days_to_reach_n"] >= 1 and "advice" in plan
    good = campaign_brief_check({"transition": "funded_activated", "hypothesis": "h", "primary_kpi": "first_trade_rate_7d", "target": "x", "guardrail_metric": "g",
                                 "control_group_pct": 10, "measurement_window_days": 7, "kill_criteria": "k", "suppressions": ["a"]},
                                [{"title": "Your first trade in 2 minutes", "body": "Pick one asset from your watchlist.", "cta": "Open"}], "push")
    assert good["ok"] and not good["problems"]
    bad = campaign_brief_check({"transition": "funded_activated"}, [{"title": "BTC will moon, buy now!", "body": "guaranteed 100%"}], "push", market_linked=True)
    assert not bad["ok"] and any("missing" in p for p in bad["problems"]) and any("compliant" in p for p in bad["problems"]) and any("ttl" in p for p in bad["problems"])
    # tool-level propose_campaign refuses an incomplete brief without creating a proposal
    before = len(approvals.list_proposals())
    res = TOOLS["propose_campaign"](name="n", channel="push", target_segment="s", variants=[{"title": "t", "body": "b", "cta": "c"}], rationale="r", goal={"transition": "x"})
    assert res.get("error") == "brief rejected" and len(approvals.list_proposals()) == before


def test_session_write_gate_and_cookie_formats():
    for raw in ('sessionid=abc123; csrftoken=tok456', '{"sessionid":"abc123","csrftoken":"tok456"}',
                '[{"name":"sessionid","value":"abc123"},{"name":"csrftoken","value":"tok456"}]',
                '{"cookies":[{"name":"sessionid","value":"abc123"},{"name":"csrftoken","value":"tok456"}]}',
                "# Netscape HTTP Cookie File\n.moengage.com\tTRUE\t/\tTRUE\t0\tsessionid\tabc123\n.moengage.com\tTRUE\t/\tTRUE\t0\tcsrftoken\ttok456\n"):
        c = parse_cookies(raw)
        assert c == {"sessionid": "abc123", "csrftoken": "tok456"}, raw
    summ = cookie_summary(c)
    assert summ["has_csrf"] and "abc123" not in json.dumps(summ)
    s = DashboardSession(cookies=c, region="dashboard-01.moengage.com", app_id="app1", db_name="db1")
    assert s.http.headers.get("X-CSRFToken") == "tok456"
    with pytest.raises(WriteBlocked):
        s.request_raw("POST", "https://dashboard-01.moengage.com/v4/segments", json_body={})


def test_har_learner_keeps_structure_drops_secrets():
    har = {"log": {"entries": [
        {"request": {"method": "GET", "url": "https://dashboard-03.moengage.com/v4/campaigns/all?app_id=APP123&page=1",
                     "headers": [{"name": "Cookie", "value": "sessionid=SECRETCOOKIE"}, {"name": "X-Moe-Token", "value": "OPAQUE_TOKEN_VALUE_1234567890"}, {"name": "X-Region", "value": "dc3"}]},
         "response": {"status": 200, "content": {"text": json.dumps({"campaigns": [{"id": "1", "name": "x", "ctr": 1.2}]})}}},
        {"request": {"method": "POST", "url": "https://dashboard-03.moengage.com/v4/segments/create",
                     "headers": [{"name": "Authorization", "value": "Bearer SECRETBEARER"}],
                     "postData": {"text": json.dumps({"name": "seg", "included_filters": {"a": 1}, "db_name": "DB999", "token": "LONGOPAQUEVALUE_ABCDEFGHIJKLMNOPQRSTUVWXYZ"})}},
         "response": {"status": 201, "content": {"text": json.dumps({"segment_id": "abc"})}}},
        {"request": {"method": "GET", "url": "https://app-cdn.moengage.com/prod/app.js", "headers": []}, "response": {"status": 200}},
    ]}}
    out = capture.learn(json.dumps(har), app_id="APP123", db_name="DB999")
    eps = out["endpoints"]
    assert eps["campaign_list"]["path"] == "/v4/campaigns/all" and eps["campaign_list"]["params"]["app_id"] == "{app_id}"
    assert eps["segment_create"]["query_keys"] == ["included_filters"] and eps["segment_create"]["body_template"]["db_name"] == "DB999"
    assert eps["segment_create"]["body_template"]["token"] == ""
    assert out["extra_headers"] == {"X-Moe-Token": "", "X-Region": "dc3"}
    dumped = json.dumps(out)
    for secret in ("SECRETCOOKIE", "SECRETBEARER", "OPAQUE_TOKEN_VALUE", "LONGOPAQUEVALUE"):
        assert secret not in dumped
    assert registry.is_usable("campaign_list") and registry.resolve("segment_create")["method"] == "POST"
    st = registry.registry_status()
    assert st["usable_reads"] >= 1 and st["usable_writes"] >= 1


def test_parse_credentials_from_devtools_header_block():
    from backend.moengage.session import parse_credentials
    block = """:authority: dashboard-03.moengage.com
:method: GET
accept: application/json
authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyIn0.abcdefghijklmnopqrstuvwxyz0123456789
cookie: sessionid=abc12345; csrftoken=tok98765
moe-appkey: APPKEY123
moetraceid: 1234
refreshtoken: rt-ABCDEFGHIJKLMNOP
"""
    c = parse_credentials(block)
    assert c["cookies"] == {"sessionid": "abc12345", "csrftoken": "tok98765"}
    assert c["access_token"].startswith("eyJ") and c["refresh_token"] == "rt-ABCDEFGHIJKLMNOP" and c["app_key"] == "APPKEY123"
    j = parse_credentials('{"bearer":"eyJx.yyy.zzz","refresh_token":"r1","app_key":"A1"}')
    assert j["access_token"] == "eyJx.yyy.zzz" and j["refresh_token"] == "r1" and j["app_key"] == "A1"
    assert parse_credentials("a=b; c=d")["cookies"] == {"a": "b", "c": "d"}


def test_discovery_classification_and_candidates(isolated_db):
    import importlib, json as _json, os
    disc = importlib.import_module("backend.moengage.discover")
    assert disc._classify_path("/getLoggedInUserData") == "whoami"
    assert disc._classify_path("/segmentation/all-segments/custom-segments") == "segment_list"
    assert disc._classify_path("/segmentation/create") == "segment_create"
    assert disc._classify_path("/campaigns/all") == "campaign_list"
    assert disc._classify_path("/flows/all") == "flow_list"
    assert disc._classify_path("/session/2fa/update") is None
    disc.DISCOVERED_PATH = os.path.join(isolated_db, "endpoints.discovered.json")
    disc._save({"region": "dashboard-01.moengage.com", "prefixes": ["/v4", "/v3"], "roles": {"campaign_list": [{"path": "/campaigns/all", "seen": 14}]}})
    cands = disc.candidates_for("campaign_list", "dashboard-01.moengage.com")
    assert [c["path"] for c in cands][:3] == ["/campaigns/all", "/v4/campaigns/all", "/v3/campaigns/all"]
    reg = registry.get_registry()["dashboard"]["campaign_list"]
    assert any((c if isinstance(c, str) else c["path"]) == "/campaigns/all" for c in reg["candidates"])
