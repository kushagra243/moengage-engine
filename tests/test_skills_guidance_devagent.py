"""
API catalog lookups, shared skills, operator guidance injection, engine knobs
allowlist, and the code-change proposal flow (fake engine on a temp git repo).
"""
import os, subprocess, json
import pytest

from backend.database import set_setting, get_setting


def test_api_catalog_search_detail_and_read_safety():
    from backend import api_catalog as c
    ov = c.overview()
    assert ov["operations"] >= 120 and ov["read_safe_operations"] >= 40
    hits = c.search("campaign stats")
    assert hits and any("/campaign-stats" in h["full_path"] for h in hits)
    d = c.operation_detail("POST", "/core-services/v1/campaign-stats")
    assert d["title"] and d["read_safe"] and "campaigns API key" in d["auth"]
    assert "start_date*" in (d["request_body"] or {}).get("schema", {})
    assert c.read_safe("GET", "/v5/flows/abc123") and c.read_safe("POST", "/v5/campaigns/search")
    assert not c.read_safe("POST", "/v5/campaigns") and not c.read_safe("PATCH", "/v5/flows/x/status")
    assert c.operation_detail("DELETE", "/nope").get("error")


def test_documented_caller_refuses_writes_and_unknown(monkeypatch):
    from backend.moengage.public_api import PublicAPI, PublicAPIError
    set_setting("moengage_app_id", "WS123"); set_setting("moengage_campaign_key", "k-campaigns-secret-1234567890")
    api = PublicAPI()
    with pytest.raises(PublicAPIError):
        api.call_documented("POST", "/v5/campaigns", body={})
    with pytest.raises(PublicAPIError):
        api.call_documented("GET", "/made/up")
    with pytest.raises(PublicAPIError):
        api.call_documented("GET", "/v3/custom-segments/{id}")          # unfilled variable
    set_setting("moengage_campaign_key", "")


def test_skills_present_and_readable():
    from backend.skills import list_skills, read_skill, prompt_lines
    names = {s["name"] for s in list_skills()}
    assert {"moengage", "moengage-api", "moengage-engine", "clm-operator"} <= names
    sk = read_skill("moengage-api")
    assert "read-safe" in sk["content"].lower() and not sk.get("error")
    assert read_skill("../etc/passwd").get("error")
    assert "moengage-engine" in prompt_lines()


def test_guidance_injected_into_prompt_and_toggle():
    from backend import guidance
    from backend.llm.agent import MarketerAgent
    g = guidance.add("Always exclude KYC-pending users from promotional pushes.", author="user", scope="audience")
    sysp = MarketerAgent()._system()
    assert "OPERATOR GUIDANCE" in sysp and "KYC-pending" in sysp and "SKILLS AVAILABLE" in sysp and "moengage-api" in sysp
    guidance.set_active(g["id"], False)
    assert "KYC-pending" not in MarketerAgent()._system()
    # duplicate text re-activates instead of duplicating
    again = guidance.add("always exclude kyc-pending users from promotional pushes.", author="agent")
    assert again["id"] == g["id"] and again["active"] == 1
    assert guidance.delete(g["id"])


def test_engine_setting_allowlist():
    from backend.guidance import set_engine_setting
    assert set_engine_setting("llm_api_key", "sk-x").get("error")
    assert set_engine_setting("moengage_region", "evil.example.com").get("error")
    r = set_engine_setting("autopilot_max_actions", "4"); assert r["ok"] and get_setting("autopilot_max_actions") == "4"
    assert set_engine_setting("autopilot_max_actions", 99).get("error")
    assert set_engine_setting("schedule_time", "25:00").get("error") and set_engine_setting("schedule_time", "07:30")["ok"]
    set_engine_setting("taxonomy_codes", {"VIP": "trader:VIP"}); assert json.loads(get_setting("taxonomy_codes"))["VIP"] == "trader:VIP"
    assert set_engine_setting("market_universe_mode", "nasdaq").get("error")


def _init_repo(path):
    subprocess.run(["git", "init", "-q", "-b", "main", path], check=True)
    open(os.path.join(path, "hello.py"), "w").write("def greet():\n    return 'hi'\n")
    open(os.path.join(path, "start.py"), "w").write("print('fake start')\n")
    os.makedirs(os.path.join(path, "tests"))
    subprocess.run(["git", "-C", path, "add", "-A"], check=True)
    subprocess.run(["git", "-C", path, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init"], check=True)


def test_code_change_proposal_draft_merge_and_reject(tmp_path, monkeypatch):
    from backend import approvals, devagent
    repo = str(tmp_path / "repo"); _init_repo(repo)
    monkeypatch.setattr(devagent, "ROOT", repo)
    monkeypatch.setattr(devagent, "WORKTREES", os.path.join(repo, "data", "worktrees"))
    monkeypatch.setattr(devagent, "RESTART", False)
    set_setting("devagent_enabled", "true")

    def fake_engine(prompt, ctx):
        assert "Implement this change request" in prompt
        with open(os.path.join(ctx["worktree"], "hello.py"), "a") as f:
            f.write("\ndef bye():\n    return 'bye'\n")
        return {"ok": True, "summary": "added bye()"}
    monkeypatch.setattr(devagent, "ENGINE_OVERRIDE", fake_engine)
    devagent.register()

    p = approvals.propose("code_change", "Add bye()", {"request": "Add a bye() function next to greet() in hello.py", "scope": "backend"}, created_by="test")
    assert p["kind"] == "code_change" and p["preview"]["status"] in ("draft_pending", "no_engine")
    # approving before drafting is refused
    r = approvals.approve_and_execute(p["id"], decided_by="test")
    assert r["status"] == "failed" and "Draft" in (r["error"] or "")
    p2 = approvals.propose("code_change", "Add bye() again", {"request": "Add a bye() function next to greet() in hello.py (second)", "scope": "backend"}, created_by="test")
    pv = devagent.draft(p2["id"], actor="test")
    assert pv["status"] == "drafted", pv
    assert "hello.py" in pv["files"] and "+def bye" in pv["diff"] and pv["branch"] == f"agent/change-{p2['id']}"
    done = approvals.approve_and_execute(p2["id"], decided_by="test")
    assert done["status"] == "executed", done
    assert "def bye" in open(os.path.join(repo, "hello.py")).read()
    log = subprocess.run(["git", "-C", repo, "log", "--oneline", "-3"], capture_output=True, text=True).stdout
    assert "Merge agent change" in log
    assert not os.path.exists(devagent.worktree_path(p2["id"]))
    # reject path cleans the branch
    p3 = approvals.propose("code_change", "Third", {"request": "Add a third function to hello.py please", "scope": "backend"}, created_by="test")
    devagent.draft(p3["id"], actor="test")
    approvals.reject(p3["id"], note="no", decided_by="test")
    branches = subprocess.run(["git", "-C", repo, "branch", "--list", f"agent/change-{p3['id']}"], capture_output=True, text=True).stdout
    assert branches.strip() == ""


def test_agent_tools_registered():
    from backend.llm.tools import TOOLS, TOOL_SCHEMAS
    names = {t["function"]["name"] for t in TOOL_SCHEMAS}
    for n in ("moengage_api_reference", "moengage_api_read", "skill", "remember_guidance", "set_engine_setting", "propose_code_change"):
        assert n in names and n in TOOLS
    out = TOOLS["moengage_api_reference"](query="flow status")
    assert out["matches"] and any("/v5/flows" in m["full_path"] for m in out["matches"])
    assert TOOLS["skill"](name="clm-operator")["content"]
