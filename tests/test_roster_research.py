"""Agent roster with tool guardrails, review council, methodology radar and approval-gated skill updates."""
import json


def test_personas_restrict_tools_and_shape_system_prompt():
    from backend.llm.roster import roster, allowed_tools, system_block, PERSONAS
    from backend.llm.agent import MarketerAgent
    ids = {a["id"] for a in roster()}
    assert {"strategist", "analyst", "copywriter", "compliance", "experimenter", "intel", "cohorts", "ops", "researcher"} <= ids
    comp = MarketerAgent(persona="compliance"); names = {(t.get("function") or t)["name"] for t in comp._tools()}
    assert "comment_proposal" in names and "propose_campaign" not in names and "run_sop" not in names
    strat = MarketerAgent(persona="strategist"); s_names = {(t.get("function") or t)["name"] for t in strat._tools()}
    assert "propose_campaign" in s_names and "run_sop" in s_names
    assert len(MarketerAgent()._tools()) >= len(s_names)
    blk = system_block("copywriter")
    assert "Copy agent" in blk and "≤ 60" in blk and "crypto-copywriting" in blk
    assert all(set(allowed_tools(k)) for k in PERSONAS)


def test_council_review_records_verdicts(monkeypatch):
    from backend.llm import roster, provider
    from backend import approvals
    from backend.database import set_setting
    set_setting("mock_mode", "true"); set_setting("llm_provider", "openai_compatible"); set_setting("llm_base_url", "http://127.0.0.1:9"); set_setting("llm_api_key", "sk-test-FAKE-council-1234567890")
    p = approvals.propose("create_segment", "Segment: council test", {"name": "Council_Test", "criteria": {"x": 1}}, "test", risk="low", created_by="test")

    class FakeClient:
        purpose = "review"

        def __init__(self, *a, **k):
            pass

        def chat(self, messages, tools=None, **kw):
            role = "compliance" if "Compliance officer" in messages[0]["content"] else "experimenter" if "Experimentation" in messages[0]["content"] else "analyst"
            return {"model": "fake", "content": json.dumps({"verdict": "approve" if role != "experimenter" else "revise", "score": 7, "findings": ["f"], "fixes": ["add holdout 20%"], "one_line": f"{role} ok"}), "tool_calls": []}
    monkeypatch.setattr(provider, "LLMClient", FakeClient)
    r = roster.council_review(p["id"])
    assert r["overall"] == "revise" and {x["reviewer"] for x in r["reviews"]} == {"compliance", "experimenter", "analyst"}
    p2 = approvals.get_proposal(p["id"])
    summ = roster.council_summary(p2)
    assert summ and summ["overall"] == "revise" and summ["reviews"]["experimenter"]["verdict"] == "revise"
    set_setting("llm_api_key", "")


def test_research_classify_radar_and_skill_update(monkeypatch, tmp_path):
    from backend import research, approvals
    from backend.database import set_setting
    set_setting("mock_mode", "true")
    c = research.classify("Braze adds holdout-based incrementality reporting to lifecycle journeys", "control group lift test")
    assert "incrementality" in c["tags"] and c["score"] > 0
    assert research.classify("Local weather today", "")["tags"] == []
    monkeypatch.setattr(research, "_fetch_feed", lambda f, limit=20: [{"title": "ASCI tightens crypto advertising rules for VDA in India", "url": "https://example.test/asci-" + f["name"][:5], "published": None, "summary": "new disclaimer rules", "source": f["name"], "kind": f["kind"], "weight": float(f["weight"])},
                                                                        {"title": "Weather in Pune", "url": "https://example.test/w-" + f["name"][:5], "published": None, "summary": "", "source": f["name"], "kind": f["kind"], "weight": 1.0}])
    r = research.refresh(max_feeds=2)
    assert r["added"] >= 1 and not r["errors"]
    rd = research.radar(10)
    top = rd["items"][0]
    assert "compliance" in top["tags"] and top["why_it_matters"] and top["experiment"] and top["skill"] == "crypto-compliance-copy"
    assert research.set_status(top["id"], "reviewed", actor="test")["ok"]
    # skill update: proposal → approve → appended (redirect the write to a temp copy)
    import os, shutil
    src = os.path.join(research.ROOT, ".claude", "skills", "clm-operator", "SKILL.md")
    tmp_root = tmp_path; os.makedirs(tmp_root / ".claude" / "skills" / "clm-operator"); shutil.copy(src, tmp_root / ".claude" / "skills" / "clm-operator" / "SKILL.md")
    monkeypatch.setattr(research, "ROOT", str(tmp_root))
    research.register()
    bad = research.propose_skill_update("nope", "x", "y" * 50)
    assert bad.get("error")
    ok = research.propose_skill_update("clm-operator", "Sequential testing for short windows", "Use always-valid sequential tests when the window is under 14 days; applies to our 7-day SOP readouts; prove it by re-reading two closed experiments both ways.", "https://example.test/paper", created_by="test")
    assert ok["proposal_id"] and (ok["preview"] or {}).get("status") == "ready"
    done = approvals.approve_and_execute(ok["proposal_id"], decided_by="test")
    assert done["status"] == "executed"
    text = (tmp_root / ".claude" / "skills" / "clm-operator" / "SKILL.md").read_text(encoding="utf-8")
    assert "Sequential testing for short windows" in text and "via methodology radar" in text
