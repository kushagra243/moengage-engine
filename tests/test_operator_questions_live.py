"""The brain asks the team what only they know and remembers the answer; the Live page shows every number against a baseline; Approve carries the copy and takes an edit."""
import json


def test_a_question_from_the_brain_lands_on_setup_and_the_answer_becomes_guidance():
    from backend import operator_questions as oq, v3_ops, guidance
    from backend.llm.tools import TOOLS
    r = TOOLS["ask_operator"](question="Which segment is the internal employee cohort called in this workspace?", why="the alerts launch needs its name", options=["INTERNAL_EMPLOYEES", "EMPLOYEES_TEST"])
    assert r["ok"] and not r.get("duplicate")
    again = TOOLS["ask_operator"](question="which segment is the internal employee cohort called in this workspace?")
    assert again.get("duplicate")
    a = next(x for x in v3_ops.asks()["asks"] if x["id"] == f"question:{r['id']}")
    assert a["group"] == "FROM THE BRAIN" and a["fields"][0]["type"] == "choice" and "other" in a["fields"][0]["options"] and a["n"] == 1, "questions come first"
    out = v3_ops.answer(f"question:{r['id']}", {"answer": "INTERNAL_EMPLOYEES"}, lambda v: {}, actor="ops")
    assert out["ok"] and "remembers" in out["toast"] and not any(x["id"] == f"question:{r['id']}" for x in out["asks"]["asks"])
    texts = " ".join(g.get("text", "") for g in (guidance.list_guidance() if hasattr(guidance, "list_guidance") else guidance.all()))
    assert "INTERNAL_EMPLOYEES" in texts and "internal employee cohort" in texts.lower()
    r2 = oq.ask("Is options trading live for users in the UK?", asked_by="agent")
    assert v3_ops.answer(f"question:{r2['id']}", {"_dismiss": "1"}, lambda v: {})["ok"] and not any(q["id"] == r2["id"] for q in oq.open_questions())


def test_live_page_has_every_section_with_baseline_words_and_set_baseline_changes_the_deltas():
    from backend import v3_live
    from backend.database import set_setting
    from tests.test_v3 import LEAK, _strings
    set_setting(v3_live.BASELINE_KEY, "")
    l = v3_live.live()
    assert set(l) >= {"lead", "markets", "moengage", "alerts", "engine", "baseline"} and "press SET BASELINE" in l["lead"]
    assert [f["k"] for f in l["markets"]["facts"]] == ["MOOD", "REGIME", "FEAR & GREED", "MARKET CAP 24H", "BTC DOMINANCE", "BREADTH"]
    assert {"totals", "channels", "league", "anomalies", "experiments"} <= set(l["moengage"]) and all("vs" in c and set(c["vs"]) == {"delivery_rate", "ctr", "conversion_rate"} for c in l["moengage"]["league"])
    for s in _strings(l):
        assert not LEAK.search(s), s
    r = v3_live.set_baseline("test", "day one")
    assert r["ok"] and r["baseline"]["campaigns"] >= 1
    l2 = v3_live.live()
    assert l2["baseline"]["by"] == "test" and "Baseline set" in l2["lead"]
    ctr = next(t for t in l2["moengage"]["totals"] if t["k"] == "CLICK RATE")
    assert ctr["word"] == "at baseline" and all(c["baseline_source"] == "set" for c in l2["moengage"]["league"])
    assert v3_live._delta(5.0, 4.0, "pp") == {"delta": 1.0, "word": "above baseline", "tone": "green"} and v3_live._delta(3.0, 4.0, "pp")["tone"] == "magenta" and v3_live._delta(None, 4.0)["word"] == "no baseline"


def test_approve_carries_the_copy_and_reject_and_edit_work(monkeypatch):
    from backend import v3, approvals
    from backend.database import set_setting
    from backend.moengage.executors import register_all
    register_all(); set_setting("mock_mode", "true"); set_setting("v3_deferred_json", "{}")
    goal = {"transition": "verified_funded", "hypothesis": "h", "primary_kpi": "first_deposit_rate_7d", "target": "+2 pp", "guardrail_metric": "unsubscribe_rate", "control_group_pct": 20, "measurement_window_days": 14, "kill_criteria": ["unsubscribe_rate > 0.4%"]}
    p = approvals.propose("create_campaign", "Campaign draft: Approve_View_One", {"name": "Approve_View_One", "channel": "push", "target_segment": "KYC_APPROVED_NODEP", "variants": [{"title": "Your account is ready", "body": "Add funds with UPI in under a minute.", "cta": "Add funds"}], "goal": goal}, created_by="test")
    a = v3.approve_view()
    d = next(x for x in a["decisions"] if x["id"] == f"proposal:{p['id']}")
    assert d["draft"]["copy"][0]["title"] == "Your account is ready" and d["draft"]["holdout"] == 20 and d["draft"]["segment"] == "KYC_APPROVED_NODEP" and "ideas" in a
    from backend.llm import agent as agent_mod

    class FakeAgent:
        def __init__(self, persona=None): self.purpose = "copy"
        def chat(self, msg, history=None, persist=False):
            approvals.update_payload(p["id"], {"variants": [{"title": "Your account is ready", "body": "Add funds with UPI in under a minute. Start with ₹100.", "cta": "Add funds"}]}, actor="agent", note="test")
            return {"reply": "Added the ₹100 starting amount to the body."}
    monkeypatch.setattr(agent_mod, "MarketerAgent", FakeAgent)
    r = v3.suggest_edit(f"proposal:{p['id']}", "mention that they can start with 100 rupees", actor="ops")
    assert r["ok"] and r["changed"] and "₹100" in approvals.get_proposal(p["id"])["payload"]["variants"][0]["body"]
    pr = approvals.get_proposal(p["id"])
    assert "operator asks" in json.dumps(pr.get("revisions") or pr.get("comments") or pr, default=str)
    assert not v3.suggest_edit(f"proposal:{p['id']}", "", actor="ops")["ok"]
    rj = v3.reject(f"proposal:{p['id']}", "not now", actor="ops")
    assert rj["ok"] and approvals.get_proposal(p["id"])["status"] == "rejected"
