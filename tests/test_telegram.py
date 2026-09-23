"""
Telegram mirror: every fired alert also lands in one team chat, the token never leaks, the chat can be discovered, and no
agent tool can post there.
"""
import json

import pytest

TOK = "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw12"


class _Resp:
    def __init__(self, payload, code=200):
        self._p, self.status_code = payload, code

    def json(self):
        return self._p


def _fake(calls, updates=None, fail=None):
    def post(url, json=None, timeout=None, **kw):
        method = url.rsplit("/", 1)[-1]
        calls.append((url, method, json))
        if fail and method in fail:
            return _Resp({"ok": False, "description": fail[method]}, 401)
        if method == "getMe":
            return _Resp({"ok": True, "result": {"username": "coindcx_alerts_bot", "first_name": "Alerts"}})
        if method == "getUpdates":
            return _Resp({"ok": True, "result": updates or []})
        return _Resp({"ok": True, "result": {"message_id": 1}})
    return post


def _boot(monkeypatch, calls, **kw):
    from backend import telegram_out as t
    from backend.database import set_setting
    set_setting(t.TOKEN_KEY, ""); set_setting(t.CHAT_KEY, ""); set_setting(t.MODE_KEY, "on")
    t._chat_cache.update(at=0.0, chats=[])

    class S:
        post = staticmethod(_fake(calls, **kw))
    monkeypatch.setattr(t, "guarded_session", lambda scope: S if scope == "telegram" else (_ for _ in ()).throw(AssertionError(scope)))
    return t


def test_token_is_validated_encrypted_and_never_shown_and_urls_are_redacted(monkeypatch):
    calls = []
    t = _boot(monkeypatch, calls)
    from backend.security import redact
    from backend import database as db
    import sqlite3
    with pytest.raises(ValueError):
        t.save("not-a-token")
    t.save(TOK, "-1001234567890 · CoinDCX CRM (supergroup)")
    assert t.chat_id() == "-1001234567890", "a picked option keeps only the id"
    raw = sqlite3.connect(db.DB_PATH).execute("select value from settings where key=?", (t.TOKEN_KEY,)).fetchone()[0]
    assert TOK not in raw
    st = t.status()
    assert st["configured"] and TOK not in json.dumps(st) and st["chat_masked"] == "-100…90"
    assert TOK not in redact(f"https://api.telegram.org/bot{TOK}/sendMessage") and TOK not in redact(f"telegram says bot{TOK} failed")


def test_hello_walks_bot_then_chat_and_discovers_the_chats_the_bot_has_seen(monkeypatch):
    calls = []
    upd = [{"message": {"chat": {"id": 42, "type": "private", "first_name": "Kushagra"}}}, {"my_chat_member": {"chat": {"id": -100555, "type": "supergroup", "title": "CRM alerts"}}}]
    t = _boot(monkeypatch, calls, updates=upd)
    t.save(TOK)
    h = t.hello()
    assert not h["ok"] and h["step"] == "chat" and h["bot"]["username"] == "coindcx_alerts_bot"
    assert [c["id"] for c in h["chats"]] == ["42", "-100555"] and h["chats"][1]["name"] == "CRM alerts"
    t.save(cid="-100555")
    h = t.hello()
    assert h["ok"] and h["step"] == "sent"
    sent = [c for c in calls if c[1] == "sendMessage"][-1][2]
    assert sent["chat_id"] == "-100555" and sent["parse_mode"] == "HTML" and "connected" in sent["text"]
    bad = _boot(monkeypatch, [], fail={"getMe": "Unauthorized"})
    bad.save(TOK, "1")
    assert bad.hello()["step"] == "bot"


def test_a_live_fire_is_mirrored_with_its_facts_and_a_failure_never_touches_the_alert(monkeypatch):
    calls = []
    t = _boot(monkeypatch, calls)
    from backend.alerts2 import discovery
    r = discovery._mirror({"title": "Large buying in ETH", "body": "A large buy of about $3.8M was recorded in ETH. See the market."}, {"signal": "large_trades", "token": "ETH", "product": "futures", "source": "agent"}, ["internal"], "recorded_mock")
    assert r.get("skipped"), "not configured: silently skipped"
    t.save(TOK, "-100555"); before = t.status()["sent_today"]
    r = discovery._mirror({"title": "Large buying in ETH", "body": "A large buy of about $3.8M <b>x</b>"}, {"signal": "large_trades", "token": "ETH", "product": "futures", "source": "agent"}, ["internal"], "recorded_mock")
    assert r["ok"]
    text = calls[-1][2]["text"]
    assert "<b>Large buying in ETH</b>" in text and "&lt;b&gt;x&lt;/b&gt;" in text and "large trades · ETH · futures · to internal · written by the agent" in text and "practice mode" in text
    assert t.status()["sent_today"] == before + 1
    broken = _boot(monkeypatch, [], fail={"sendMessage": "Bad Request: chat not found"})
    broken.save(TOK, "-1")
    r = discovery._mirror({"title": "t", "body": "b"}, {"signal": "milestone", "token": "BTC"}, ["internal"], "sent")
    assert r["ok"] is False and "chat not found" in r["error"] and broken.status()["last_error"]
    from backend.database import set_setting
    set_setting(t.MODE_KEY, "off")
    assert discovery._mirror({"title": "t", "body": "b"}, {}, [], "sent").get("skipped")


def test_a_test_send_to_telegram_still_obeys_the_copy_rules_and_says_it_is_a_test(monkeypatch):
    calls = []
    t = _boot(monkeypatch, calls)
    t.save(TOK, "-100555")
    bad = t.send_test("Beat Binance fees", "Trade with 50x leverage now")
    assert not bad["ok"] and "breaks a rule" in bad["error"] and not any(c[1] == "sendMessage" for c in calls)
    ok = t.send_test("Large buying in ETH", "A large buy of about $3.8M was recorded in ETH. See the market.", {"signal": "large_trades", "token": "ETH"})
    assert ok["ok"] and "test send from the engine" in calls[-1][2]["text"] and calls[-1][2]["text"].startswith("🧪")


def test_telegram_is_operator_only_and_the_screens_know_about_it():
    from backend.llm.tools import TOOLS, TOOL_SCHEMAS
    import inspect
    from backend.llm import tools as tools_mod
    assert not any("telegram" in n for n in TOOLS) and "telegram" not in json.dumps(TOOL_SCHEMAS).lower()
    assert "telegram" not in inspect.getsource(tools_mod).lower(), "no agent tool imports or reaches the Telegram module"
    from backend import v3_ops
    import backend.main as m
    from backend.database import set_setting
    from backend import telegram_out as t
    set_setting(t.TOKEN_KEY, ""); set_setting(t.CHAT_KEY, "")
    a = next(x for x in v3_ops.asks()["asks"] if x["id"] == "setting:telegram")
    assert [f["key"] for f in a["fields"]] == ["bot_token", "chat_id"] and a["fields"][0]["type"] == "secret" and a["button"] == "SAVE AND SEND A HELLO"
    al = v3_ops.alerts()
    assert al["telegram"]["configured"] is False and "Asks" in al["telegram"]["line"]
    eng = v3_ops.engine(m.status())
    g = next(g for g in eng["groups"] if g["title"] == "Telegram mirror")
    assert [f["key"] for f in g["fields"]] == ["telegram_bot_token", "telegram_chat_id", "telegram_alerts"] and g["fields"][0]["type"] == "secret"
    assert "telegram_" in m.ALLOWED_SETTING_PREFIXES
