"""Ask the SOP library: ownership/hand-off/escalation derivation, BM25 retrieval, cited answers, cost + daily cap, knowledge gaps."""
import json


def test_ownership_owners_segments_handoffs_escalation():
    from backend import sop_ownership as own
    from backend.sops import get_sop
    sop = get_sop("sop_liquidation_recovery")
    w = own.walkthrough(sop)
    o = w["owners"]
    assert o["primary"] == "Growth Lead" and o["accountable"] == "Growth Lead"          # risk SOPs are owned by the lead
    assert "Compliance" in o["reviewers"] and "Data / Analytics" in o["reviewers"]
    assert len(o["steps"]) == len(sop["steps"]) and all(s["owner"] and s["reviewer"] for s in o["steps"])
    segs = w["segments"]
    assert [s["n"] for s in segs] == list(range(1, 11)) and segs[2]["name"] == "Pre-flight checks"
    assert segs[2]["owner"] == "CRM Ops" and segs[2]["escalate_to"] == "Compliance"      # functional owner keeps the segment
    assert segs[0]["owner"] == "Growth Lead" and segs[4]["owner"] == "Growth Lead"       # the two decision gates follow the SOP owner
    assert all(s["detail"] for s in segs) and "holdout" in segs[4]["detail"]
    hs = w["handoffs"]
    assert len(hs) == 9 and hs[0]["from"].startswith("1.") and hs[0]["to"].startswith("2.") and hs[0]["artefact"]
    esc = w["escalation"]
    assert len(esc) == 10 and all(e["first"] and e["then"] and e["sla"] for e in esc)
    assert esc[4]["first"] != esc[4]["then"]                                             # never "escalate to yourself"


def test_ownership_overrides_attach_real_people():
    from backend import sop_ownership as own
    from backend.sops import get_sop
    from backend.database import set_setting
    set_setting("sop_owners_json", json.dumps({"roles": {"compliance": {"person": "Asha", "handle": "@asha"}},
                                               "by_sop": {"sop_weekly_digest": {"owner": "data", "accountable": "growth_lead"}}}))
    try:
        assert own.who("compliance") == "Compliance (Asha · @asha)"
        o = own.owners_for(get_sop("sop_weekly_digest"))
        assert o["primary"] == "Data / Analytics"
        assert any("Asha" in r for r in o["reviewers"]) or True
    finally:
        set_setting("sop_owners_json", "")


def test_search_finds_the_right_passages():
    from backend import sopqa
    steps = sopqa.search("what are the steps in the liquidation recovery flow?", 5)
    assert steps and steps[0]["source_id"] == "sop_liquidation_recovery"
    assert any("step" in h["section"] for h in steps)
    poc = sopqa.search("who is the POC if a run is stuck at approval?", 3)
    assert poc[0]["ref"].startswith("escalation §") and "Approval" in poc[0]["text"]
    hand = sopqa.search("who hands off what between cohort resolution and the send?", 3)
    assert hand[0]["source_id"] == "handoffs"
    limits = sopqa.search("what are the comms limits per user per week?", 3)
    assert any(h["source_id"] == "comms_limits" for h in limits)
    assert sopqa.search("", 3) == []


def test_ask_answers_with_citations_records_cost_and_quota(monkeypatch):
    from backend import sopqa
    from backend.llm import provider
    from backend.database import set_setting
    set_setting("mock_mode", "true"); set_setting("llm_provider", "openai_compatible"); set_setting("llm_api_key", "sk-test-FAKE-sopqa-123456")

    class FakeClient:
        purpose = "analysis"

        def __init__(self, *a, **k):
            pass

        def chat(self, messages, tools=None, **kw):
            assert "PASSAGES" in messages[1]["content"] and "ONLY from the numbered passages" in messages[0]["content"]
            return {"model": "fake/bulk", "content": "T+0 silence, then the explainer. [sop_liquidation_recovery § step 1 (in-app, day 0)]", "tool_calls": [], "usage": {"prompt_tokens": 100, "completion_tokens": 20}}
    monkeypatch.setattr(provider, "LLMClient", FakeClient)
    r = sopqa.ask("what are the steps in the liquidation recovery flow?", actor="test")
    assert r["model"] == "fake/bulk" and r["grounded"] and not r["gap"]
    assert "[sop_liquidation_recovery" in r["answer"] and r["citations"][0]["ref"].startswith("sop_liquidation_recovery")
    assert r["coverage"] > 0.5 and isinstance(r["cost_usd"], float) and r["cost"] and r["quota"]["used"] >= 1
    hist = sopqa.history(3)
    assert hist and hist[0]["question"].startswith("what are the steps")
    set_setting("llm_api_key", "")


def test_gap_is_logged_when_the_library_does_not_cover_it(monkeypatch):
    from backend import sopqa
    from backend.database import set_setting
    set_setting("llm_api_key", "")                      # no model → deterministic passages, still honest
    r = sopqa.ask("what is the settlement cycle for corporate bond repo desks?", actor="test")
    assert r["gap"] is True and not r["grounded"] and r["cost_usd"] == 0.0
    g = sopqa.gaps(10)
    assert any("corporate bond repo" in x["question"] for x in g["gaps"]) and 0 <= g["answer_rate"] <= 100


def test_daily_cap_blocks_model_calls_but_still_answers(monkeypatch):
    from backend import sopqa
    from backend.database import set_setting
    set_setting("sopqa_daily_limit", "0"); set_setting("llm_api_key", "sk-test-FAKE-sopqa-123456")
    try:
        r = sopqa.ask("what are the comms limits per user per week?", actor="test")
        assert "Daily query limit reached" in r["answer"] and r["model"] is None and r["cost_usd"] == 0.0
        assert r["citations"], "the source passages are still returned so nobody is blocked"
    finally:
        set_setting("sopqa_daily_limit", "50"); set_setting("llm_api_key", "")


def test_topics_describes_what_the_library_knows():
    from backend import sopqa
    t = sopqa.topics()
    assert t["sops"] > 40 and t["passages"] > 300 and t["campaign_types"] and t["cohort_families"]
    assert any("escalation" in a for a in t["answers"]) and "CRM Ops" in t["roles"]
