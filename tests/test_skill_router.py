"""
Skills that are used, not merely installed: MoEngage's own vendored skill is reachable section by section, the right
sections are placed in context by the task itself, and every load is recorded.
"""
import json


def test_the_official_moengage_skill_is_reachable_by_part_and_section():
    from backend.skills import read_skill
    top = read_skill("moengage")
    assert "official-skill" in top["more_parts"] and "Common Gotchas" in top["more_parts"]["official-skill"], "the agent is told which headings exist"
    g = read_skill("moengage", part="official-skill", section="common gotchas")
    assert g["content"].startswith("## Common Gotchas") and "Verification Checklist" not in g["content"], "one heading, nothing after it"
    assert "parts" in read_skill("moengage", part="nope") and "sections" in read_skill("moengage", part="official-skill", section="no such heading")
    assert "error" in read_skill("../../etc/passwd")


def test_a_build_task_gets_moengage_rules_and_a_reading_task_gets_none():
    from backend import skill_router as r
    from backend.database import set_setting
    set_setting("llm_autoload_skills", "true")
    picks = r.pick("Draft a push campaign for verified users who never funded", "strategist", "chat")
    assert picks[:3] == r.ALWAYS_WHEN_BUILDING and ("moengage", "official-skill", "Typical Campaign Creation Workflow") in picks
    assert r.pick("why did winback fall this week", None, "chat") == []
    text, names = r.block("Create a business event triggered push for the employee cohort", "alerts", "chat")
    assert "Common Gotchas" in text and any(n.startswith("moengage/official-skill") for n in names) and len(text) < 7000, "compact: the token budget holds"
    u = r.usage(7)
    assert any(x["skill"] == "moengage" and x["auto"] >= 1 for x in u["used"]) and "moengage" not in u["never_opened"]
    set_setting("llm_autoload_skills", "false")
    assert r.block("Draft a push campaign", None, "chat") == ("", [])
    set_setting("llm_autoload_skills", "true")


def test_the_agent_loop_places_the_skills_before_the_task_and_records_them():
    from backend.llm.agent import MarketerAgent
    from backend.database import set_setting
    set_setting("llm_autoload_skills", "true")
    seen = {}

    def fake_chat(messages, tools=None):
        seen["messages"] = messages
        return {"content": "done", "tool_calls": [], "model": "fake", "usage": {}}
    a = MarketerAgent(persona="strategist")
    a.client.chat = fake_chat
    out = a.chat("Propose a push campaign for KYC approved users with no deposit", history=[], persist=False)
    roles = [m["role"] for m in seen["messages"]]
    assert roles[0] == "system" and roles[-1] == "user" and roles[-3:] == ["user", "assistant", "user"], roles
    assert "<skill name=\"moengage/official-skill § Common Gotchas\">" in seen["messages"][-3]["content"]
    assert seen["messages"][-1]["content"].startswith("Propose a push campaign"), "the operator's words stay the last thing the model reads"
    assert any(n.startswith("moengage") for n in a._skills_loaded) and out.get("reply") == "done"


def test_a_build_tool_without_the_skill_gets_the_checklist_attached():
    from backend import skill_router as r
    assert "Verification Checklist" in r.checklist_for_tool("propose_campaign", [])
    assert r.checklist_for_tool("propose_campaign", ["moengage § Quick facts"]) == "", "not twice"
    assert r.checklist_for_tool("campaign_stats", []) == "", "reads do not need it"


def test_every_specialist_that_builds_in_moengage_has_it_on_the_reading_list():
    from backend.llm.roster import PERSONAS
    for who in ("strategist", "copywriter", "experimenter", "cohorts", "alerts"):
        assert "moengage" in PERSONAS[who]["skills"], who
    assert "moengage-api" in PERSONAS["ops"]["skills"]
