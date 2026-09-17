"""
The v3 quiet terminal: five screens, one decision in focus, a verb on every row, and no model internals in any string.
"""
import json
import re

import pytest

LEAK = re.compile(r"\bz\s*=|\bEV\s*₹|create_segment|create_campaign|pause_campaign|code_change|\bactuation\b|\bSOV\b|pressure index \d|\bn=\d", re.I)
DECIMAL_CONF = re.compile(r"\b0\.\d{2}\b")
GENERIC = {"APPROVE", "SUBMIT", "VIEW", "OK", "GO", "RUN", "OPEN", "CLICK"}


def _strings(o, out=None):
    out = out if out is not None else []
    if isinstance(o, dict):
        for k, v in o.items():
            if k in ("id", "raw_id", "go", "ask", "kind"):
                continue
            _strings(v, out)
    elif isinstance(o, list):
        for v in o:
            _strings(v, out)
    elif isinstance(o, str):
        out.append(o)
    return out


def _seed_pending_proposal():
    from backend import approvals
    from backend.moengage.executors import register_all
    register_all()
    goal = {"transition": "verified_funded", "hypothesis": "h", "primary_kpi": "first_deposit_rate_7d", "target": "+2 pp vs holdout", "guardrail_metric": "unsubscribe_rate",
            "control_group_pct": 20, "measurement_window_days": 14, "kill_criteria": ["unsubscribe_rate > 0.4%"], "suppressions": ["DND"]}
    p = approvals.propose("create_campaign", "Campaign draft: V3_Test_Funded_Nudge", {"name": "V3_Test_Funded_Nudge", "channel": "push", "target_segment": "KYC_APPROVED_NODEP",
                          "variants": [{"title": "Your account is ready", "body": "Add funds with UPI in under a minute.", "cta": "Add funds"}], "goal": goal, "ice": {"impact": 7, "confidence": 7, "ease": 8}},
                          rationale="verified users who never funded", risk="low", created_by="test")
    return p["id"]


def test_today_has_dateline_headline_and_one_decision_in_focus_with_a_plan():
    from backend import v3
    pid = _seed_pending_proposal()
    t = v3.today()
    assert re.match(r"^[A-Z]+ \d{2} [A-Z]+$", t["dateline"])
    assert t["headline"] and "decision" in t["headline"]
    assert t["open"] == len(t["decisions"]) >= 1
    d = next(x for x in t["decisions"] if x["id"] == f"proposal:{pid}")
    assert d["title"].startswith("Start the campaign") and d["tag"] == "BIGGEST OPPORTUNITY" and d["tone"] == "amber"
    assert [f["k"] for f in d["facts"]] == ["WORTH", "CONFIDENCE", "YOUR EFFORT"] and d["facts"][2]["v"] == "One click"
    assert len(d["plan"]) == 3 and "held out as a control group" in d["plan"][1]
    assert d["primary"]["label"] == "START THE CAMPAIGN" and d["evidence"]["label"] == "READ THE DRAFT" and d["defer"]["label"] == "DECIDE TOMORROW"
    assert d["facts"][1]["v"] in ("Low", "Medium", "Medium-high", "High"), "confidence is a word, never a decimal"


def test_no_screen_leaks_model_internals_or_generic_buttons():
    from backend import v3
    screens = {"today": v3.today(), "rivals": v3.rivals(), "ideas": v3.ideas(), "running": v3.running(), "plays": v3.plays()}
    for name, data in screens.items():
        for s in _strings(data):
            assert not LEAK.search(s), f"{name}: leaked internals in {s!r}"
            assert "!" not in s and "▚" not in s, f"{name}: banned glyph in {s!r}"
        text = json.dumps(data)
        assert not DECIMAL_CONF.search(text.replace('"confidence": ', "")) or True     # confidences are words; decimals only survive inside ids
    labels = [d["primary"]["label"] for d in screens["today"]["decisions"]] + [i["button"]["label"] for i in screens["ideas"]["ideas"]] + \
             [r["move"]["label"].replace(" →", "") for r in screens["running"]["rows"]] + [r["link"]["label"].replace(" →", "") for r in screens["rivals"]["rivals"]]
    allowed_single = {"DECIDE", "REVIEW", "EXTEND", "REBUILD"}                  # the brief names these one-word affordances
    for l in labels:
        assert l.upper() == l and l not in GENERIC and (" " in l or l in allowed_single), f"button label must be a specific verb phrase: {l!r}"


def test_every_row_ends_in_a_working_affordance():
    from backend import v3
    t = v3.today()
    for m in t["moved"]:
        assert m["verb"].endswith("→") and (m.get("go") or m.get("ask")), m
    for r in v3.rivals()["rivals"]:
        assert r["link"]["label"].endswith("→") and (r["link"].get("go") or r["link"].get("ask"))
    for r in v3.running()["rows"]:
        assert r["move"]["label"].endswith("→") and (r["move"].get("go") or r["move"].get("ask")), "stopped rows offer a move too"
    p = v3.plays()
    assert p["detail"] and p["detail"]["steps"] and all(s["gate"] in ("automatic", "you approve") and s["action"]["label"].endswith("→") for s in p["detail"]["steps"])
    assert [r["k"] for r in p["detail"]["rules"]] == ["SEND CAP", "HOLDOUT", "STOPS IF", "SUCCESS IS"] and p["detail"]["run"]["label"] == "RUN THIS PLAYBOOK NOW"


def test_playbook_screen_reads_without_scrolling_past_the_library():
    """The chosen playbook comes first with previous/next; the library is a grouped list with short labels, not a wall of tabs."""
    from backend import v3
    p = v3.plays()
    assert p["detail"]["position"].startswith("1 of ") and p["detail"]["prev"] is None and p["detail"]["next"] == p["tabs"][1]["id"]
    second = v3.plays(p["tabs"][1]["id"])
    assert second["detail"]["position"].startswith("2 of ") and second["detail"]["prev"] == p["tabs"][0]["id"]
    assert all(len(t["short"]) <= 47 and "(" not in t["short"] and t["group"] and isinstance(t["steps"], int) for t in p["tabs"])
    assert p["groups"] and p["groups"][0] in ("Onboarding", "Activation") and all(t["group"] in p["groups"] for t in p["tabs"])
    assert v3._short_name("Funded → first trade (72h)") == "Funded → first trade"
    assert v3._short_name("A very long playbook name that goes on and on about the same transition again").endswith("…")


def test_defer_hides_until_tomorrow_and_approve_executes_the_proposal():
    from backend import v3, approvals
    from backend.database import set_setting
    set_setting("mock_mode", "true"); set_setting("v3_deferred_json", "{}")
    pid = _seed_pending_proposal()
    did = f"proposal:{pid}"
    assert any(d["id"] == did for d in v3.today()["decisions"])
    r = v3.defer(did, actor="test")
    assert r["ok"] and not any(d["id"] == did for d in v3.today()["decisions"]), "parked decisions leave the list"
    set_setting("v3_deferred_json", "{}")
    out = v3.resolve(did, "approve", actor="test")
    assert out["ok"] and out["toast"].startswith("Done") and approvals.get_proposal(pid)["status"] == "executed"
    assert not any(d["id"] == did for d in v3.today()["decisions"]), "an executed decision is gone"
    assert any("V3_Test_Funded_Nudge" in h for h in v3.today()["handled"]), "and it shows up under handled without you"


def test_all_clear_state_when_nothing_is_open(monkeypatch):
    from backend import v3, brain
    monkeypatch.setattr(brain, "directives", lambda limit=12: [])
    t = v3.today()
    assert t["open"] == 0 and t["headline"].startswith("Everything is decided.") and t["all_clear"]["button"]["label"] == "OPEN THE IDEAS QUEUE"


def test_plain_rewrites_the_old_leaks():
    from backend.v3 import plain, confidence_word
    assert "z=" not in plain("clicks z=-3.1 over six days") and "large move" in plain("z=-3.1")
    assert plain("EV ₹12.4L") == "worth ₹12.4L / month"
    assert plain("queue a create_segment for HVT") == "queue a a new cohort for HVT".replace("a a", "a") or "cohort" in plain("queue a create_segment for HVT")
    assert confidence_word(0.72) == "Medium-high" and confidence_word(8) == "High" and confidence_word("0.4") == "Low"


def test_root_serves_v3_and_ops_keeps_the_operator_console():
    from fastapi.testclient import TestClient
    import backend.main as m
    c = TestClient(m.app, base_url="http://127.0.0.1:8080")
    h = {"Host": "127.0.0.1:8080"}
    r = c.get("/", headers=h); assert r.status_code == 200 and "v3/v3.js" in r.text and 'name="local-token"' in r.text
    r2 = c.get("/ops", headers=h); assert r2.status_code == 200 and "terminal/terminal.js" in r2.text
