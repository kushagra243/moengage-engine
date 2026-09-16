"""
When a discovery alert goes out — the "best time of day", learned rather than assumed.

Two lanes, because market facts age at different speeds:
  now lane        large trades, milestones, an unusual major move. A price fact is worth sending while it is true, so
                  these fire on detection inside the allowed hours and expire quickly.
  window lane     the day's most traded token, a 1-year high. These keep, so they are queued and released in the send
                  window chosen for that day.

The window is not guessed. Each day the engine picks one of the candidate windows — mostly the best one so far, and a
fifth of the time another one, so it keeps learning — and records which day used which. `feedback()` reads the campaign's
own daily stats and attributes that day's click rate to the window it used. Stats come a day at a time, so the learning
is day-level: honest, slow, and it improves as days accumulate. Until a window has `min_days` behind it the engine says
so instead of claiming a best time it has not earned.
"""
from __future__ import annotations
import json
import random
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from ..database import get_db
from ..security import audit

IST = timezone(timedelta(hours=5, minutes=30))

DEFAULT_WINDOWS = [
    {"id": "morning", "start": "09:15", "end": "10:30", "why": "first look of the day, before work"},
    {"id": "midday", "start": "13:15", "end": "14:30", "why": "lunch break; inside the 10:00–21:00 India window"},
    {"id": "evening", "start": "19:30", "end": "21:00", "why": "highest app time in India; the weekly recap hour"},
]


def init_tables() -> None:
    conn = get_db()
    conn.execute("""CREATE TABLE IF NOT EXISTS ma2_queue (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, signal TEXT, token TEXT, product TEXT, direction TEXT,
                    value REAL, title TEXT, body TEXT, source TEXT, window_id TEXT, due_at TEXT, expires_at TEXT, status TEXT DEFAULT 'queued', fired_at TEXT)""")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_ma2_queue_due ON ma2_queue (status, due_at)")
    conn.execute("""CREATE TABLE IF NOT EXISTS ma2_window_days (day_ist TEXT PRIMARY KEY, window_id TEXT, chosen_by TEXT, fires INTEGER DEFAULT 0,
                    delivered INTEGER, clicks INTEGER, ctr REAL, stats_at TEXT)""")
    conn.commit(); conn.close()


def windows(cfg: Dict[str, Any]) -> List[Dict[str, str]]:
    w = cfg.get("send_windows") or DEFAULT_WINDOWS
    return [x for x in w if x.get("id") and x.get("start")]


def scores(cfg: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Click rate per window from the days that used it."""
    from . import rules
    cfg = cfg or rules.config()
    init_tables(); conn = get_db()
    rows = conn.execute("""SELECT window_id, COUNT(*) days, SUM(COALESCE(fires,0)) fires, SUM(COALESCE(delivered,0)) delivered,
                           SUM(COALESCE(clicks,0)) clicks, AVG(ctr) avg_ctr, SUM(CASE WHEN ctr IS NOT NULL THEN 1 ELSE 0 END) days_with_stats
                           FROM ma2_window_days GROUP BY window_id""").fetchall()
    conn.close()
    by = {r["window_id"]: dict(r) for r in rows}
    out = []
    for w in windows(cfg):
        r = by.get(w["id"], {})
        out.append({**w, "days": r.get("days", 0), "days_with_stats": r.get("days_with_stats", 0), "fires": r.get("fires", 0),
                    "delivered": r.get("delivered", 0), "clicks": r.get("clicks", 0),
                    "ctr": round(r["avg_ctr"], 3) if r.get("avg_ctr") is not None else None})
    return out


def best(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    from . import rules
    cfg = cfg or rules.config()
    min_days = int(cfg.get("window_min_days") or 3)
    ranked = [s for s in scores(cfg) if s["ctr"] is not None and s["days_with_stats"] >= min_days]
    ranked.sort(key=lambda s: -s["ctr"])
    if not ranked:
        w = windows(cfg)
        pref = str(cfg.get("window_default") or "evening")
        pick = next((x for x in w if x["id"] == pref), w[0])
        return {"window": pick, "learned": False, "why": f"no window has {min_days} days of stats yet; using {pick['id']} from the India send calendar"}
    return {"window": {k: ranked[0][k] for k in ("id", "start", "end", "why")}, "learned": True,
            "why": f"best click rate so far: {ranked[0]['ctr']}% over {ranked[0]['days_with_stats']} day(s)"}


def window_for_day(day: str, cfg: Optional[Dict[str, Any]] = None, actor: str = "engine") -> Dict[str, Any]:
    """One window per day, recorded so the result can be attributed to it. Mostly the best; sometimes another, to keep learning."""
    from . import rules
    cfg = cfg or rules.config()
    init_tables(); conn = get_db()
    row = conn.execute("SELECT window_id, chosen_by FROM ma2_window_days WHERE day_ist=?", (day,)).fetchone()
    if row:
        conn.close()
        w = next((x for x in windows(cfg) if x["id"] == row["window_id"]), windows(cfg)[0])
        return {"window": w, "chosen_by": row["chosen_by"]}
    b = best(cfg)
    explore = random.Random(day).random() * 100 < float(cfg.get("window_explore_pct") or 20)
    if explore:
        opts = [w for w in windows(cfg) if w["id"] != b["window"]["id"]] or windows(cfg)
        w = random.Random(day + "x").choice(opts); chosen_by = "explore"
    else:
        w = b["window"]; chosen_by = "best" if b["learned"] else "default"
    conn.execute("INSERT OR REPLACE INTO ma2_window_days (day_ist, window_id, chosen_by) VALUES (?,?,?)", (day, w["id"], chosen_by))
    conn.commit(); conn.close()
    audit("ma2.window_chosen", {"day": day, "window": w["id"], "chosen_by": chosen_by}, actor=actor)
    return {"window": w, "chosen_by": chosen_by}


def _at(day: datetime, hhmm: str) -> datetime:
    h, m = [int(x) for x in str(hhmm).split(":")[:2]]
    return day.replace(hour=h, minute=m, second=0, microsecond=0)


def next_due(now: datetime, w: Dict[str, str]) -> datetime:
    """The next time this window opens: today if it has not passed, otherwise tomorrow."""
    start = _at(now, w["start"]); end = _at(now, w["end"])
    if now < start:
        return start
    if start <= now <= end:
        return now                                    # the window is open: release on this tick
    return _at(now + timedelta(days=1), w["start"])


# ── queue ─────────────────────────────────────────────────────────────────────
def queued_keys(now: datetime) -> set:
    init_tables(); conn = get_db()
    rows = conn.execute("SELECT signal, token, direction FROM ma2_queue WHERE status IN ('queued','sent') AND created_at >= ?",
                        ((now - timedelta(hours=24)).isoformat(),)).fetchall()
    conn.close()
    return {(r["signal"], r["token"], r["direction"]) for r in rows}


def enqueue(item: Dict[str, Any], now: datetime, cfg: Dict[str, Any], actor: str = "engine") -> Dict[str, Any]:
    day = now.strftime("%Y-%m-%d")
    w = window_for_day(day, cfg, actor=actor)["window"]
    due = next_due(now, w)
    ttl = float((cfg.get("queue_ttl_min") or {}).get(item["signal"], 720))
    conn = get_db()
    conn.execute("""INSERT INTO ma2_queue (created_at, signal, token, product, direction, value, title, body, source, window_id, due_at, expires_at, status)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'queued')""",
                 (now.isoformat(), item["signal"], item["token"], item.get("product", "futures"), item.get("direction", "any"), item.get("value"),
                  item["title"], item["body"], item.get("source"), w["id"], due.isoformat(), (due + timedelta(minutes=ttl)).isoformat()))
    conn.commit(); conn.close()
    return {"window": w["id"], "due_at": due.isoformat(), "held_for_min": round((due - now).total_seconds() / 60)}


def due_now(now: datetime) -> List[Dict[str, Any]]:
    init_tables(); conn = get_db()
    rows = [dict(r) for r in conn.execute("SELECT * FROM ma2_queue WHERE status='queued' AND due_at <= ? ORDER BY id", (now.isoformat(),))]
    conn.close()
    return rows


def expire(now: datetime) -> int:
    init_tables(); conn = get_db()
    cur = conn.execute("UPDATE ma2_queue SET status='expired' WHERE status='queued' AND expires_at <= ?", (now.isoformat(),))
    conn.commit(); n = cur.rowcount; conn.close()
    return n or 0


def mark(qid: int, status: str, now: datetime) -> None:
    conn = get_db()
    conn.execute("UPDATE ma2_queue SET status=?, fired_at=? WHERE id=?", (status, now.isoformat(), qid))
    conn.commit(); conn.close()


def count_fire(day: str, n: int = 1) -> None:
    init_tables(); conn = get_db()
    conn.execute("INSERT INTO ma2_window_days (day_ist, window_id, chosen_by, fires) VALUES (?,?,?,?) ON CONFLICT(day_ist) DO UPDATE SET fires = COALESCE(fires,0) + ?",
                 (day, "", "", n, n))
    conn.commit(); conn.close()


def queue_view(limit: int = 12) -> List[Dict[str, Any]]:
    init_tables(); conn = get_db()
    rows = [dict(r) for r in conn.execute("SELECT id, signal, token, direction, title, window_id, due_at, expires_at, status FROM ma2_queue ORDER BY id DESC LIMIT ?", (limit,))]
    conn.close()
    return rows


# ── learning ──────────────────────────────────────────────────────────────────
def feedback(days: int = 21, actor: str = "engine") -> Dict[str, Any]:
    """Attribute each past day's click rate on the discovery campaign to the window that day used."""
    from .discovery import _campaign_state
    init_tables()
    camp = _campaign_state()
    if not camp.get("name"):
        return {"ok": False, "why": "the discovery campaign does not exist yet, so there is nothing to learn from"}
    conn = get_db()
    open_days = [r["day_ist"] for r in conn.execute("SELECT day_ist FROM ma2_window_days WHERE ctr IS NULL AND day_ist >= ? ORDER BY day_ist",
                                                    ((datetime.now(IST) - timedelta(days=days)).strftime("%Y-%m-%d"),)).fetchall()]
    conn.close()
    if not open_days:
        return {"ok": True, "updated": 0, "why": "every day already has its stats"}
    try:
        from ..anomaly.store import list_tracked_campaigns, get_history
        from ..database import get_setting
        source = "mock" if get_setting("mock_mode", "true").lower() == "true" else "live"
        cid = next((t["campaign_id"] for t in list_tracked_campaigns(source) if t["campaign_name"] == camp["name"]), None)
        hist = {h["snapshot_date"]: h for h in get_history(cid, source, 60)} if cid else {}
    except Exception as e:
        return {"ok": False, "why": f"campaign stats unavailable: {str(e)[:120]}"}
    updated = 0
    conn = get_db()
    for day in open_days:
        h = hist.get(day)
        if not h:
            continue
        conn.execute("UPDATE ma2_window_days SET delivered=?, clicks=?, ctr=?, stats_at=? WHERE day_ist=?",
                     (h.get("delivered_count"), h.get("click_count"), h.get("ctr"), datetime.now(IST).isoformat(), day))
        updated += 1
    conn.commit(); conn.close()
    if updated:
        audit("ma2.window_feedback", {"days": updated, "campaign": camp["name"]}, actor=actor)
    return {"ok": True, "updated": updated, "campaign": camp["name"], "scores": scores()}


def view(cfg: Optional[Dict[str, Any]] = None, now: Optional[datetime] = None) -> Dict[str, Any]:
    from . import rules
    cfg = cfg or rules.config(); now = now or datetime.now(IST)
    day = now.strftime("%Y-%m-%d")
    today = window_for_day(day, cfg)
    b = best(cfg)
    return {"windows": scores(cfg), "today": {**today["window"], "chosen_by": today["chosen_by"]}, "best": b,
            "lanes": {"now": list(cfg.get("perishable_signals") or []), "window": list(cfg.get("evergreen_signals") or [])},
            "queue": queue_view(), "explore_pct": cfg.get("window_explore_pct"), "min_days": cfg.get("window_min_days"),
            "note": "perishable market facts go out on detection inside the allowed hours; the rest wait for the day's window, which is chosen mostly by measured click rate and sometimes at random so the engine keeps learning"}
