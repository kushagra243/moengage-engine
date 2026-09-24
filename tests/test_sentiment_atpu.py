"""ATPU across the portfolio: the mood read, compliant nudges per product on every channel, the community channel behind an approval and a cap."""
import json

import pytest


def test_mood_labels_follow_the_evidence_and_never_raise():
    from backend import sentiment as s
    fear = s.read({"fear_greed": {"value": 12, "classification": "Extreme Fear"}, "crypto": {"regime": {"breadth_up_pct": 10, "funding_bias": "short", "label": "stress"}}, "crypto_global": {"mcap_chg_24h": -8}})
    greed = s.read({"fear_greed": {"value": 88}, "crypto": {"regime": {"breadth_up_pct": 92, "funding_bias": "long", "label": "risk_on"}}, "crypto_global": {"mcap_chg_24h": 6}})
    assert fear["label"] == "fearful" and fear["stress"] and greed["label"] == "euphoric" and not greed["stress"]
    assert s.read({})["label"] == "neutral" and s.read({})["evidence"]


def test_every_nudge_on_every_mood_passes_the_copy_rules_and_names_no_venue():
    from backend import sentiment as s
    from backend.llm.tools import VENUE_WORDS
    for label in s.LABELS:
        n = s.nudges({"label": label, "score": 0, "evidence": [], "stress": label == "fearful"})
        assert len(n["nudges"]) == len(s.PRODUCTS)
        for x in n["nudges"]:
            assert x["push"]["ok"], (label, x["product"], x["push"]["lint"])
            text = " ".join([x["push"]["title"], x["push"]["body"], x["email"], x["telegram"], x["whatsapp"]])
            assert not VENUE_WORDS.search(text), (label, x["product"], text)
            assert len(x["push"]["title"]) <= 60 and len(x["push"]["body"]) <= 140
        paused = {x["product"] for x in n["nudges"] if x["paused"]}
        assert paused == ({"crypto_perps", "options", "web3"} if label == "fearful" else set())
    p = s.plan("crypto_perps", {"label": "euphoric", "score": 1.5, "evidence": [], "stress": False})
    assert [st["channel"] for st in p["steps"]] == ["push", "in_app", "telegram_broadcast", "email", "whatsapp"]
    assert p["measure"]["primary_kpi"] == "trades_per_active_user" and p["measure"]["control_group_pct"] == 20 and "UK" in p["geo"]
    assert "error" in s.plan("bonds")


def test_the_north_star_is_atpu_and_the_agent_has_the_tools():
    from backend.guardrails import north_star
    from backend.llm.tools import TOOLS
    from backend.llm.roster import allowed_tools
    assert "trades per active user" in north_star().lower()
    assert {"sentiment_pushes", "propose_telegram_post"} <= set(TOOLS)
    assert "sentiment_pushes" in allowed_tools("analyst") and "propose_telegram_post" in allowed_tools("strategist") and "propose_telegram_post" not in allowed_tools("analyst")
    from backend import skill_router
    picks = skill_router.pick("what should we push this week to lift ATPU across the portfolio", "strategist", "chat")
    assert ("atpu-portfolio-playbook", "SKILL", "") in picks


def test_a_community_post_is_approved_capped_linted_and_sent_to_the_users_chat_only(monkeypatch):
    from backend import telegram_out as t, approvals
    from backend.database import set_setting
    t.register(); t.init_tables()
    calls = []

    class R:
        status_code = 200
        def json(self): return {"ok": True, "result": {}}

    class S:
        @staticmethod
        def post(url, json=None, timeout=None): calls.append(json); return R()
    monkeypatch.setattr(t, "guarded_session", lambda scope: S)
    set_setting(t.TOKEN_KEY, "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw12"); set_setting(t.CHAT_KEY, "-100TEAM"); set_setting(t.BROADCAST_KEY, ""); set_setting("telegram_broadcast_daily_cap", "1")
    from backend.database import get_db
    conn = get_db(); conn.execute("DELETE FROM telegram_log WHERE kind='broadcast'"); conn.commit(); conn.close()
    try:
        with pytest.raises(ValueError, match="venue_named"):
            approvals.propose("telegram_post", "Community post: bad", {"text": "Trade with 50x leverage on Binance today"}, created_by="test")
        p = t.propose_broadcast("Greed reading is high. Plans beat impulses: set levels and sizes now.", "euphoric mood, spot discipline", "spot", actor="test")
        with pytest.raises(approvals.ApprovalError, match="community channel"):
            approvals.approve_and_execute(p["id"], decided_by="test")
        set_setting(t.BROADCAST_KEY, "-100USERS")
        done = approvals.approve_and_execute(p["id"], decided_by="test")
        assert done["status"] == "executed" and calls[-1]["chat_id"] == "-100USERS" and "Not investment advice" in calls[-1]["text"]
        assert t.broadcast_sent_today() == 1
        p2 = t.propose_broadcast("Quiet tape. Set your alerts now; the next move rarely announces itself.", "neutral", "spot", actor="test")
        with pytest.raises(approvals.ApprovalError, match="cap"):
            approvals.approve_and_execute(p2["id"], decided_by="test")
        assert all(c["chat_id"] != "-100TEAM" for c in calls), "the team mirror is never a distribution channel"
    finally:
        set_setting(t.TOKEN_KEY, ""); set_setting(t.CHAT_KEY, ""); set_setting(t.BROADCAST_KEY, ""); set_setting("telegram_broadcast_daily_cap", "")
