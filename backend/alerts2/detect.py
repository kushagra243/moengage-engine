"""
Deterministic detection for Market Alerts 2.0: every closed candle is judged exactly once.

The old detector looked at whichever candle happened to be newest when the job ran. With a 15-minute job over 5-minute
candles that saw one candle in three, judged the hourly candle before it had closed, and forgot it once it had. Here:

  * only **closed** candles are evaluated (a candle closes at t + interval; the forming one waits);
  * a **watermark** per (signal, token, interval) records the last candle judged, so a late or restarted job catches up
    on everything that closed in between instead of skipping it — lateness delays, it never drops;
  * milestones are read off the **price path** (each candle's high and low against the previous close), so a level
    touched and abandoned between two runs is still a crossing;
  * every detection has a **deterministic key** (signal|token|candle time or band) stored in `ma2_detections`, so running
    the same minute twice, or replaying a day, produces the same set and nothing is fired twice;
  * `coverage()` replays a day from the candle history and lists any detection the live path never recorded — the
    number that must stay at zero.
"""
from __future__ import annotations
import json
import math
import statistics
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from ..database import get_db

IST = timezone(timedelta(hours=5, minutes=30))
INTERVAL_S = {"5m": 300, "15m": 900, "1h": 3600, "1d": 86400}


def init_tables() -> None:
    conn = get_db()
    conn.execute("CREATE TABLE IF NOT EXISTS ma2_watermarks (key TEXT PRIMARY KEY, candle_t REAL, updated_at TEXT)")
    conn.execute("""CREATE TABLE IF NOT EXISTS ma2_detections (det_key TEXT PRIMARY KEY, signal TEXT, token TEXT, product TEXT, direction TEXT, value REAL,
                    candle_t REAL, detected_at TEXT, fields_json TEXT, outcome TEXT, outcome_at TEXT)""")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_ma2_det_day ON ma2_detections (detected_at)")
    conn.commit(); conn.close()


# ── pure helpers ──────────────────────────────────────────────────────────────
def closed(candles: List[Dict[str, float]], interval: str, now_ts: float) -> List[Dict[str, float]]:
    """Candles whose period has ended. The forming candle is never judged: its numbers are not final."""
    secs = INTERVAL_S.get(interval, 3600)
    return [c for c in candles if float(c.get("t", 0)) + secs <= now_ts]


def burst_at(rows: List[Dict[str, float]], i: int, min_usd: float, vol_mult: float, size_mult: float, lookback: int = 48) -> Optional[Dict[str, Any]]:
    """Is candle i a large-trade burst against the `lookback` candles before it? Uses only what was known at candle i."""
    if i < 30:
        return None
    base = rows[max(0, i - lookback):i]
    c = rows[i]
    if not c.get("v") or not c.get("c"):
        return None
    notional = float(c["v"]) * float(c["c"])
    size = notional / max(1.0, float(c.get("n") or 1))
    base_n = statistics.median([float(b["v"]) * float(b["c"]) for b in base if b.get("v") and b.get("c")] or [0])
    base_s = statistics.median([float(b["v"]) * float(b["c"]) / max(1.0, float(b.get("n") or 1)) for b in base if b.get("v") and b.get("c")] or [0])
    if notional < min_usd or base_n <= 0 or base_s <= 0 or notional < base_n * vol_mult or size < base_s * size_mult:
        return None
    move = (float(c["c"]) / float(c["o"]) - 1.0) * 100.0 if c.get("o") else 0.0
    return {"notional_usd": round(notional), "vol_x": round(notional / base_n, 1), "size_x": round(size / base_s, 1), "avg_trade_usd": round(size),
            "move_pct": round(move, 2), "direction": "up" if move >= 0 else "down", "price": float(c["c"]), "candle_t": float(c["t"])}


def z_at(rows: List[Dict[str, float]], i: int, window: int) -> Optional[Dict[str, Any]]:
    """Z-score of candle i's return against the `window` returns before it. Nothing after i is used."""
    if i < 21:
        return None
    cl = [float(r["c"]) for r in rows[: i + 1] if r.get("c")]
    if len(cl) < 22:
        return None
    rets = [cl[k] / cl[k - 1] - 1.0 for k in range(1, len(cl))]
    last, base = rets[-1], rets[-(window + 1):-1]
    if len(base) < 20:
        return None
    mu = sum(base) / len(base)
    sd = math.sqrt(sum((r - mu) ** 2 for r in base) / (len(base) - 1))
    if sd <= 0:
        return None
    return {"z": round((last - mu) / sd, 2), "ret_pct": round(last * 100.0, 2), "price": cl[-1], "candle_t": float(rows[i]["t"])}


def path_crossings(rows: List[Dict[str, float]], prev_close: Optional[float], band: float) -> List[Dict[str, Any]]:
    """Every round level the price path crossed, candle by candle, using highs and lows — not just where it closed.
    A spike through 78,000 that closes back at 77,950 is still a crossing (up), then a crossing back (down)."""
    out: List[Dict[str, Any]] = []
    if band <= 0:
        return out
    ref = prev_close
    for c in rows:
        try:
            o, h, l, cl = float(c["o"]), float(c["h"]), float(c["l"]), float(c["c"])
        except (KeyError, TypeError, ValueError):
            continue
        start = ref if ref is not None else o
        # up leg: from start to the high; down leg: from the high to the low; then to the close — the path a candle can take
        for a, b in ((start, h), (h, l), (l, cl)):
            if a == b:
                continue
            lo, hi = (a, b) if a < b else (b, a)
            first = math.floor(lo / band) + 1
            last = math.floor(hi / band)
            ks = range(first, last + 1) if b > a else range(last, first - 1, -1)     # levels in the order the price met them
            for k in ks:
                level = k * band
                if b > a and level > a and level <= b:
                    out.append({"direction": "up", "level": int(level), "price": cl, "candle_t": float(c["t"])})
                elif b < a and level < a and level >= b:
                    out.append({"direction": "down", "level": int(level), "price": cl, "candle_t": float(c["t"])})
        ref = cl
    # collapse immediate up/down chatter on the same level inside one candle to the net move the candle made
    dedup: List[Dict[str, Any]] = []
    for x in out:
        if dedup and dedup[-1]["candle_t"] == x["candle_t"] and dedup[-1]["level"] == x["level"] and dedup[-1]["direction"] != x["direction"]:
            dedup.pop()
            continue
        dedup.append(x)
    return dedup


# ── watermarks and the detection ledger ───────────────────────────────────────
def watermark(key: str) -> Optional[float]:
    init_tables(); conn = get_db()
    r = conn.execute("SELECT candle_t FROM ma2_watermarks WHERE key=?", (key,)).fetchone(); conn.close()
    return float(r["candle_t"]) if r else None


def set_watermark(key: str, candle_t: float, now: datetime) -> None:
    conn = get_db()
    conn.execute("INSERT INTO ma2_watermarks (key, candle_t, updated_at) VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET candle_t=MAX(candle_t, excluded.candle_t), updated_at=excluded.updated_at",
                 (key, candle_t, now.isoformat()))
    conn.commit(); conn.close()


def record(dets: List[Dict[str, Any]], now: datetime) -> List[Dict[str, Any]]:
    """Insert detections by key; return only the ones not seen before. Same input twice → the second call returns nothing."""
    if not dets:
        return []
    init_tables(); conn = get_db()
    fresh = []
    for d in dets:
        cur = conn.execute("INSERT OR IGNORE INTO ma2_detections (det_key, signal, token, product, direction, value, candle_t, detected_at, fields_json) VALUES (?,?,?,?,?,?,?,?,?)",
                           (d["det_key"], d["signal"], d["token"], d.get("product", "futures"), d.get("direction", "any"), d.get("value"), d.get("candle_t"), now.isoformat(), json.dumps(d.get("fields") or {})))
        if cur.rowcount:
            fresh.append(d)
    conn.commit(); conn.close()
    return fresh


def set_outcome(det_key: str, outcome: str, now: datetime) -> None:
    conn = get_db()
    conn.execute("UPDATE ma2_detections SET outcome=?, outcome_at=? WHERE det_key=?", (outcome, now.isoformat(), det_key))
    conn.commit(); conn.close()


def outcomes_since(since: datetime) -> Dict[str, int]:
    init_tables(); conn = get_db()
    rows = conn.execute("SELECT COALESCE(outcome,'undecided') o, COUNT(*) n FROM ma2_detections WHERE detected_at >= ? GROUP BY o", (since.isoformat(),)).fetchall(); conn.close()
    return {r["o"]: r["n"] for r in rows}


# ── scanning since the watermark ──────────────────────────────────────────────
def _key(signal: str, token: str, extra: Any) -> str:
    return f"{signal}|{token}|{extra}"


def scan_bursts(token: str, candles: List[Dict[str, float]], cfg: Dict[str, Any], now_ts: float, advance: bool, now: datetime) -> Tuple[List[Dict[str, Any]], Optional[float]]:
    rows = closed(candles, "5m", now_ts)
    wm = watermark(_key("large_trades", token, "5m"))
    out = []
    for i, c in enumerate(rows):
        if wm is not None and float(c["t"]) <= wm:
            continue
        b = burst_at(rows, i, float(cfg["large_trade_min_usd"]), float(cfg["large_trade_vol_multiple"]), float(cfg["large_trade_size_multiple"]))
        if b:
            out.append({"det_key": _key("large_trades", token, int(b["candle_t"])), "signal": "large_trades", "token": token, "product": "futures", "direction": b["direction"],
                        "value": b["notional_usd"], "candle_t": b["candle_t"], "source": "large-trade burst proxy from closed 5-minute candles",
                        "fields": {"token": token, "product": "futures", "size_usd": b["notional_usd"], "move": b["move_pct"], "price": b["price"]}, "detail": b})
    new_wm = float(rows[-1]["t"]) if rows else None
    if advance and new_wm is not None:
        set_watermark(_key("large_trades", token, "5m"), new_wm, now)
    return out, new_wm


def scan_moves(token: str, candles: List[Dict[str, float]], interval: str, window: int, threshold: float, now_ts: float, advance: bool, now: datetime) -> List[Dict[str, Any]]:
    rows = closed(candles, interval, now_ts)
    key = _key("btc_move", token, interval)
    wm = watermark(key)
    out = []
    for i, c in enumerate(rows):
        if wm is not None and float(c["t"]) <= wm:
            continue
        z = z_at(rows, i, window)
        if z and abs(z["z"]) >= threshold:
            out.append({"det_key": _key("btc_move", token, int(z["candle_t"])), "signal": "btc_move", "token": token, "product": "futures",
                        "direction": "up" if z["ret_pct"] >= 0 else "down", "value": z["z"], "candle_t": z["candle_t"],
                        "source": f"{interval} close against its own {window}-candle range", "fields": {"token": token, "product": "futures", "move": z["ret_pct"], "price": z["price"]}})
    if advance and rows:
        set_watermark(key, float(rows[-1]["t"]), now)
    return out


def scan_milestones(token: str, candles: List[Dict[str, float]], band: float, now_ts: float, advance: bool, now: datetime, state_last_close: Optional[float]) -> Tuple[List[Dict[str, Any]], Optional[float]]:
    """Crossings along the closed 5-minute path since the watermark. The reference is the last judged close, so nothing between runs is skipped."""
    rows = closed(candles, "5m", now_ts)
    key = _key("milestone", token, "5m")
    wm = watermark(key)
    fresh = [c for c in rows if wm is None or float(c["t"]) > wm]
    if wm is None and len(fresh) > 3:
        fresh = fresh[-3:]                       # first ever run: judge the last quarter hour, not the whole history
    out = []
    for x in path_crossings(fresh, state_last_close, band):
        out.append({"det_key": _key("milestone", token, f"{x['direction']}{x['level']}@{int(x['candle_t'])}"), "signal": "milestone", "token": token, "product": "futures",
                    "direction": x["direction"], "value": x["level"], "candle_t": x["candle_t"], "source": "round-number band crossed on the closed 5-minute price path",
                    "fields": {"token": token, "product": "futures", "price": x["price"], "level": x["level"]}})
    last_close = float(fresh[-1]["c"]) if fresh else state_last_close
    if advance and fresh:
        set_watermark(key, float(fresh[-1]["t"]), now)
    return out, last_close


# ── coverage: prove nothing slipped ──────────────────────────────────────────
def coverage(day: str, cfg: Dict[str, Any], tokens: List[str], fetch_5m, fetch_1h) -> Dict[str, Any]:
    """Replay the day from candle history and compare with what the live path recorded.
    `missed` is the set of detections the replay finds that never made it into ma2_detections — the number to keep at zero."""
    init_tables()
    d0 = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=IST)
    start, end = d0.timestamp(), (d0 + timedelta(days=1)).timestamp()
    expected: Dict[str, Dict[str, Any]] = {}
    for token in tokens:
        try:
            rows5 = [c for c in fetch_5m(token) if start - 48 * 300 <= float(c["t"]) < end]
            rows5 = closed(rows5, "5m", end)
            for i, c in enumerate(rows5):
                if float(c["t"]) < start:
                    continue
                b = burst_at(rows5, i, float(cfg["large_trade_min_usd"]), float(cfg["large_trade_vol_multiple"]), float(cfg["large_trade_size_multiple"]))
                if b:
                    expected[_key("large_trades", token, int(b["candle_t"]))] = {"signal": "large_trades", "token": token, "candle_t": b["candle_t"]}
            band = float((cfg.get("milestone_bands") or {}).get(token) or 0)
            if band:
                day_rows = [c for c in rows5 if float(c["t"]) >= start]
                prev = next((float(c["c"]) for c in reversed(rows5) if float(c["t"]) < start), None)
                for x in path_crossings(day_rows, prev, band):
                    expected[_key("milestone", token, f"{x['direction']}{x['level']}@{int(x['candle_t'])}")] = {"signal": "milestone", "token": token, "candle_t": x["candle_t"]}
            if token in (cfg.get("discovery_move_tokens") or []):
                interval = (cfg.get("candle") or {}).get("futures", "1h"); window = int((cfg.get("z_window") or {}).get(interval, 168))
                rows1 = closed([c for c in fetch_1h(token) if float(c["t"]) < end], interval, end)
                for i, c in enumerate(rows1):
                    if float(c["t"]) < start:
                        continue
                    z = z_at(rows1, i, window)
                    if z and abs(z["z"]) >= float(cfg["z_threshold"]):
                        expected[_key("btc_move", token, int(z["candle_t"]))] = {"signal": "btc_move", "token": token, "candle_t": z["candle_t"]}
        except Exception as e:
            expected[f"error|{token}"] = {"error": str(e)[:100]}
    conn = get_db()
    seen = {r["det_key"]: dict(r) for r in conn.execute("SELECT det_key, outcome FROM ma2_detections WHERE candle_t >= ? AND candle_t < ?", (start - 1, end)).fetchall()}
    conn.close()
    missed = [{"det_key": k, **v} for k, v in expected.items() if k not in seen and not k.startswith("error|")]
    return {"day": day, "expected": len([k for k in expected if not k.startswith("error|")]), "recorded": len([k for k in expected if k in seen]),
            "missed": missed, "missed_count": len(missed), "errors": [v["error"] for k, v in expected.items() if k.startswith("error|")],
            "outcomes": {}, "note": "expected = what a full replay of the day's closed candles detects; missed must be zero"}
