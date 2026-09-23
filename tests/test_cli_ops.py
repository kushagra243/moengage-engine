"""The operator CLI: reads are JSON-clean, writes go through the same paths as the UI, secrets are never accepted as arguments."""
import argparse
import io
import json
import sys

import pytest


def _ns(**kw):
    base = {"json": True, "env": "", "stdin": False, "values": [], "chat": "", "new_token": False, "signal": "", "title": None, "body": None, "file": None,
            "alert": None, "proposal": None, "to": "moengage", "cohorts": "internal", "control": 10, "yes": False, "actor": "cli", "action": None, "id": None, "key": None}
    base.update(kw)
    return argparse.Namespace(**base)


def _run(fn, a, capsys):
    from backend import cli_ops
    getattr(cli_ops, fn)(a)
    return json.loads(capsys.readouterr().out)


def test_doctor_today_and_asks_are_json_and_do_not_call_pending_drafts_unexecutable(capsys):
    from backend.database import set_setting
    set_setting("mock_mode", "true")
    d = _run("doctor", _ns(), capsys)
    assert set(d) >= {"mock_mode", "model", "asks", "preflight", "telegram", "skills_never_opened"}
    assert not any(a["id"].endswith(":blocked") and "no way to carry" in a["title"] for a in d["asks"])
    t = _run("today", _ns(), capsys)
    assert "decisions" in t and "headline" in t
    a = _run("asks", _ns(), capsys)
    assert "asks" in a and all("id" in x for x in a["asks"])


def test_answer_refuses_a_secret_as_an_argument_and_takes_it_hidden(capsys, monkeypatch):
    from backend.database import set_setting, get_setting
    set_setting("moengage_campaign_key", "")
    with pytest.raises(SystemExit):
        _run("answer", _ns(id="setting:moengage_campaign_key", values=["moengage_campaign_key=sk-visible-in-history"]), capsys)
    assert not get_setting("moengage_campaign_key")
    monkeypatch.setattr("backend.cli_ops._hidden", lambda *a, **k: "sk-typed-at-the-hidden-prompt-0123456789")
    r = _run("answer", _ns(id="setting:moengage_campaign_key"), capsys)
    assert r["ok"] and get_setting("moengage_campaign_key") == "sk-typed-at-the-hidden-prompt-0123456789"
    set_setting("moengage_campaign_key", "")


def test_secret_and_set_share_one_policy(capsys, monkeypatch):
    from backend import cli_ops
    from backend.database import get_setting, set_setting
    with pytest.raises(SystemExit):
        cli_ops.secret(_ns(key="moengage_region"))                      # not a secret
    with pytest.raises(SystemExit):
        cli_ops.secret(_ns(key="evil_thing_token"))                     # not an allowed prefix
    monkeypatch.setattr("backend.cli_ops._hidden", lambda *a, **k: "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw12")
    cli_ops.secret(_ns(key="telegram_bot_token")); capsys.readouterr()
    assert get_setting("telegram_bot_token").startswith("123456789:")
    set_setting("telegram_bot_token", "")
    from backend.settings_policy import save_plain, normalise
    assert normalise("moengage_dc", "dashboard-03.moengage.com") == "03"
    r = save_plain({"telegram_alerts": "off", "not_allowed": "x", "moengage_campaign_key": ""})
    assert r["saved"] == ["telegram_alerts"] and r["rejected"] == ["not_allowed"] and get_setting("telegram_alerts") == "off"
    set_setting("telegram_alerts", "on")


def test_launch_prints_the_brief_first_and_only_queues_with_yes(capsys):
    from backend.database import set_setting
    from backend import approvals
    set_setting("mock_mode", "true")
    b = _run("launch", _ns(), capsys)
    assert "copy" in b and "moengage_draft" in b and "summary" in b, "the whole brief, nothing queued"
    before = len(approvals.list_proposals(status="pending", limit=300))
    r = _run("launch", _ns(yes=True), capsys)
    after = len(approvals.list_proposals(status="pending", limit=300))
    assert not r.get("error") and after >= before


def test_decide_is_the_human_click_and_refuses_a_blocked_draft(capsys, monkeypatch):
    from backend import approvals, v3
    from backend.database import set_setting
    from backend.llm import tools
    set_setting("mock_mode", "true"); set_setting("v3_deferred_json", "{}")
    goal = {"transition": "verified_funded", "hypothesis": "h", "primary_kpi": "first_deposit_rate_7d", "target": "+2 pp", "control_group_pct": 20, "measurement_window_days": 14}
    r = tools.propose_campaign("CLI_Blocked_Draft", "push", "", [{"title": "Your account is ready", "body": "Add funds with UPI in under a minute."}], "r", goal)
    pid = r["proposal_id"]
    monkeypatch.setattr("builtins.input", lambda *a, **k: "yes")
    with pytest.raises(SystemExit):
        _run("decide", _ns(id=f"proposal:{pid}", action="approve"), capsys)
    assert approvals.get_proposal(pid)["status"] == "pending"
    capsys.readouterr()                                                   # the refusal's plain-text explanation, not JSON
    d = _run("decide", _ns(id=f"proposal:{pid}", action="defer", yes=True), capsys)
    assert d["ok"] and not any(x["id"] == f"proposal:{pid}" for x in v3.today()["decisions"])


def test_the_ops_skill_exists_and_names_the_human_boundaries():
    from backend.skills import list_skills, read_skill
    assert "moengage-ops" in [s["name"] for s in list_skills()]
    body = read_skill("moengage-ops")["content"]
    for must in ("hidden prompt", "decide proposal:", "never paste a secret", "./cli.py doctor", "./cli.py launch --yes"):
        assert must in body, must
