"""
Telegram: a second place every market alert lands, so the alerts can be seen working today.

MoEngage is the delivery channel for users. Telegram is for the team: while MoEngage is still being wired up (keys,
business event, campaign draft), and afterwards as a live mirror, every alert the engine fires is also posted to one
Telegram chat — a group or a person's chat with the bot. Nothing else is ever posted there: alerts, test sends the
operator asks for, and the one hello message that proves the bot works.

Setup is two values in Settings or Asks, never in chat: the bot token from @BotFather (a secret, encrypted like every
key) and the chat id. If the chat id is unknown the engine finds it: message the bot (or add it to the group and say
anything), and `discover_chats()` lists the chats the bot has seen so the operator picks one.

Boundaries: outbound only to api.telegram.org through the `telegram` network scope; the token never appears in logs
(`redact` masks the bot-token shape); no agent tool can post to Telegram, only the engine's own fire path and the
operator's click; a post that fails never fails the alert.
"""
from __future__ import annotations
import html
import json
import re
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from .database import get_db, get_setting, set_setting
from .security import audit, guarded_session, redact

IST = timezone(timedelta(hours=5, minutes=30))
TOKEN_KEY = "telegram_bot_token"           # ends in _token → encrypted secret
CHAT_KEY = "telegram_chat_id"
MODE_KEY = "telegram_alerts"               # on | off (default on once configured)
TOKEN_SHAPE = re.compile(r"^\d{6,12}:[A-Za-z0-9_-]{30,}$")
_chat_cache: Dict[str, Any] = {"at": 0.0, "chats": []}


def init_tables() -> None:
    conn = get_db()
    conn.execute("""CREATE TABLE IF NOT EXISTS telegram_log (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT DEFAULT CURRENT_TIMESTAMP, day_ist TEXT, kind TEXT, ok INTEGER, title TEXT, error TEXT)""")
    conn.commit(); conn.close()


def token() -> str:
    return (get_setting(TOKEN_KEY, "") or "").strip()


def chat_id() -> str:
    return (get_setting(CHAT_KEY, "") or "").strip()


def enabled() -> bool:
    return str(get_setting(MODE_KEY, "on")).lower() != "off"


def configured() -> bool:
    return bool(token() and chat_id())


def status() -> Dict[str, Any]:
    init_tables()
    day = datetime.now(IST).strftime("%Y-%m-%d")
    conn = get_db()
    row = conn.execute("SELECT SUM(ok) sent, COUNT(*) tried, MAX(at) last FROM telegram_log WHERE day_ist=?", (day,)).fetchone()
    last_err = conn.execute("SELECT error FROM telegram_log WHERE ok=0 ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    cid = chat_id()
    return {"token_set": bool(token()), "chat_set": bool(cid), "chat_masked": (cid[:4] + "…" + cid[-2:]) if len(cid) > 6 else ("set" if cid else ""), "configured": configured(),
            "enabled": enabled(), "on": configured() and enabled(), "sent_today": int(row["sent"] or 0) if row else 0, "tried_today": int(row["tried"] or 0) if row else 0,
            "last_at": str(row["last"] or "")[11:16] if row and row["last"] else "", "last_error": (last_err["error"] if last_err else "") or ""}


# ── the wire ──────────────────────────────────────────────────────────────────
def _api(method: str, body: Optional[Dict[str, Any]] = None, tok: str = "") -> Dict[str, Any]:
    tok = tok or token()
    if not tok:
        raise ValueError("no Telegram bot token is saved")
    r = guarded_session("telegram").post(f"https://api.telegram.org/bot{tok}/{method}", json=body or {}, timeout=15)
    try:
        d = r.json()
    except ValueError:
        d = {}
    if r.status_code >= 400 or not d.get("ok", False):
        desc = str(d.get("description") or f"HTTP {r.status_code}")
        raise ValueError(redact(desc)[:200])
    return d.get("result") if isinstance(d.get("result"), (dict, list)) else d


def _log(kind: str, ok: bool, title: str, error: str = "") -> None:
    try:
        init_tables()
        conn = get_db()
        conn.execute("INSERT INTO telegram_log (day_ist, kind, ok, title, error) VALUES (?,?,?,?,?)", (datetime.now(IST).strftime("%Y-%m-%d"), kind, 1 if ok else 0, title[:120], redact(error)[:200]))
        conn.commit(); conn.close()
    except Exception:
        pass


def save(tok: str = "", cid: str = "", actor: str = "user") -> Dict[str, Any]:
    tok = (tok or "").strip(); cid = (cid or "").strip()
    if tok:
        if not TOKEN_SHAPE.match(tok):
            raise ValueError("that does not look like a bot token (digits, a colon, then the secret part, as @BotFather gives it)")
        set_setting(TOKEN_KEY, tok)
        _chat_cache["at"] = 0.0
    if cid:
        m = re.match(r"^\s*(-?\d{1,20})", cid)               # a picked option reads "-100123… · Group name"; keep the id only
        if not m:
            raise ValueError("a chat id is a number (negative for groups); pick one of the chats the bot has seen")
        set_setting(CHAT_KEY, m.group(1))
    audit("telegram.saved", {"token": bool(tok), "chat": bool(cid)}, actor=actor)
    return status()


def discover_chats(tok: str = "") -> List[Dict[str, Any]]:
    """Chats that have messaged the bot (or groups it was added to). Cached a minute; empty when the bot has heard nothing yet."""
    tok = tok or token()
    if not tok:
        return []
    if time.time() - _chat_cache["at"] < 60 and _chat_cache["chats"]:
        return _chat_cache["chats"]
    try:
        upd = _api("getUpdates", {"limit": 100, "allowed_updates": ["message", "my_chat_member", "channel_post"]}, tok=tok)
    except Exception:
        return _chat_cache["chats"] or []
    seen: Dict[str, Dict[str, Any]] = {}
    for u in upd if isinstance(upd, list) else []:
        for k in ("message", "channel_post", "my_chat_member"):
            m = u.get(k) if isinstance(u, dict) else None
            ch = (m or {}).get("chat") or {}
            if ch.get("id") is None:
                continue
            name = ch.get("title") or " ".join(x for x in (ch.get("first_name"), ch.get("last_name")) if x) or ch.get("username") or "chat"
            seen[str(ch["id"])] = {"id": str(ch["id"]), "name": str(name)[:60], "type": ch.get("type", "")}
    _chat_cache.update(at=time.time(), chats=list(seen.values()))
    return _chat_cache["chats"]


def me(tok: str = "") -> Dict[str, Any]:
    r = _api("getMe", tok=tok)
    return {"username": r.get("username"), "name": r.get("first_name")} if isinstance(r, dict) else {}


def _esc(s: Any) -> str:
    return html.escape(str(s or ""), quote=False)


def format_alert(title: str, body: str, meta: Optional[Dict[str, Any]] = None, kind: str = "alert") -> str:
    meta = meta or {}
    head = {"alert": "🔔", "test": "🧪", "hello": "👋"}.get(kind, "🔔")
    lines = [f"{head} <b>{_esc(title)}</b>", _esc(body)]
    facts = [meta.get("signal", "").replace("_", " "), meta.get("token"), meta.get("product"), (f"to {', '.join(meta['cohorts'])}" if meta.get("cohorts") else ""),
             ("written by the agent" if meta.get("source") == "agent" else "")]
    facts = [str(x) for x in facts if x]
    if facts:
        lines.append(f"<i>{_esc(' · '.join(facts))}</i>")
    tail = [datetime.now(IST).strftime("%H:%M IST")]
    if kind == "test":
        tail.append("test send from the engine, not a market event")
    if meta.get("moengage") == "recorded_mock":
        tail.append("MoEngage in practice mode: recorded, not delivered to users")
    elif meta.get("moengage") == "sent":
        tail.append("also fired to MoEngage")
    lines.append(f"<code>{_esc(' · '.join(tail))}</code>")
    return "\n".join(lines)


def post(text: str, kind: str = "alert", title: str = "") -> Dict[str, Any]:
    """Send one message. Never raises; the result says what happened and it is logged either way."""
    if not configured():
        return {"ok": False, "error": "Telegram is not set up: bot token and chat id are needed"}
    try:
        _api("sendMessage", {"chat_id": chat_id(), "text": text[:4000], "parse_mode": "HTML", "disable_web_page_preview": True})
        _log(kind, True, title)
        return {"ok": True}
    except Exception as e:
        err = redact(str(e))[:200]
        _log(kind, False, title, err)
        return {"ok": False, "error": err}


def mirror_alert(title: str, body: str, meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Called from the engine's fire path. Off, unconfigured or failing, it changes nothing about the alert."""
    if not (configured() and enabled()):
        return {"ok": False, "skipped": True}
    return post(format_alert(title, body, meta, "alert"), "alert", title)


def send_test(title: str, body: str, meta: Optional[Dict[str, Any]] = None, actor: str = "user") -> Dict[str, Any]:
    from .alerts2 import rules
    bad = [x for x in rules.lint(title, body) if x["severity"] in ("high", "medium")]
    if bad:
        return {"ok": False, "error": "the copy breaks a rule and is not sent even as a test: " + "; ".join(f"{x['rule']} ({x['snippet']})" for x in bad[:3])}
    r = post(format_alert(title, body, meta, "test"), "test", title)
    audit("telegram.test_send", {"ok": r.get("ok"), "title": title[:80]}, actor=actor)
    return r


def hello(actor: str = "user") -> Dict[str, Any]:
    """Prove the bot and the chat work: who the bot is, then one message in the chat."""
    try:
        who = me()
    except Exception as e:
        return {"ok": False, "step": "bot", "error": redact(str(e))[:200]}
    if not chat_id():
        return {"ok": False, "step": "chat", "bot": who, "error": "the bot works; now pick the chat", "chats": discover_chats()}
    r = post(format_alert("The alerts engine is connected", "Every market alert the engine fires will also appear here. This message is the only one that is not an alert.", {}, "hello"), "hello", "hello")
    audit("telegram.hello", {"ok": r.get("ok")}, actor=actor)
    return {**r, "step": "sent" if r.get("ok") else "chat", "bot": who}
