"""
Discovery experiment — the half of Market Alerts 2.0 that needs no per-user data, so it can go live now.

Position PnL and "relevant token" alerts need each user's positions and watchlist, which we do not have yet. Discovery
does not: a large-trade burst, the day's most traded token, a BTC/ETH round-number milestone or an unusual BTC move are
facts about the market. The engine detects them, writes the copy, and fires ONE MoEngage **business event** per signal;
MoEngage picks the audience (a segment) and enforces the per-user frequency cap, because without user ids the engine
cannot. That trade-off is the only difference from the BRD's Discovery section, and it is stated in the tab.

Whale movement: CoinDCX's own whale feed is authoritative when uploaded (data/ma2/whales.json). Without it the engine
derives a **large-trade burst** from public 5-minute candles — notional volume and average trade size well above their own
baseline — and labels it a proxy everywhere it appears. The public trades endpoint returns only ~10 seconds of history,
so real per-trade whale detection would need a streaming connection we do not run.
"""
from __future__ import annotations
import json
import statistics
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from ..database import get_db, get_setting
from ..security import audit, redact
from . import rules, signals as sig_mod, timing, detect

IST = timezone(timedelta(hours=5, minutes=30))
STRESS = ("capitulation", "high_volatility_down")
EVENT = "MA2_Discovery"
DEFAULT_COHORTS: List[Dict[str, Any]] = [
    {"id": "internal", "label": "Internal employees (test cohort)", "segment": None, "event": "MA2_Discovery_INTERNAL", "control_pct": 5, "stage": "internal",
     "signals": ["large_trades", "most_traded", "milestone", "btc_move", "ath_atl", "agent_pick"], "why": "the first and only cohort until the internal stage has run"},
    {"id": "futures_active", "label": "Futures traders, active 30d", "segment": "FUTURES_ACTIVE_30D", "event": "MA2_Discovery_FUTURES", "control_pct": 10, "stage": "all",
     "signals": ["large_trades", "most_traded", "milestone", "btc_move", "ath_atl"], "why": "large trades and open-interest stories are futures stories"},
    {"id": "spot_active", "label": "Spot traders, active 30d", "segment": "SPOT_ACTIVE_30D", "event": "MA2_Discovery_SPOT", "control_pct": 10, "stage": "all",
     "signals": ["most_traded", "milestone", "btc_move", "ath_atl"], "why": "price facts and milestones; no whale or leverage stories for spot-only users"},
    {"id": "all", "label": "Whole push-enabled base", "segment": "ALL_PUSH_ENABLED", "event": EVENT, "control_pct": 10, "stage": "all",
     "signals": ["most_traded", "milestone", "btc_move", "ath_atl"], "why": "the broad base gets the calm facts only"},
]
_BAD_TOKENS: Dict[str, float] = {}          # token → retry-after timestamp, for symbols the venue answers 500 to (delisted, renamed)

SIGNALS = {
    "large_trades": {"label": "Large trades (whale)", "why": "CoinDCX's whale module when it posts trades in, otherwise a burst of unusual notional volume and average trade size in one 5-minute candle", "product": "futures"},
    "most_traded": {"label": "Most traded today", "why": "the day's highest 24h volume, majors excluded per the BRD", "product": "futures"},
    "milestone": {"label": "Round-number milestone", "why": "BTC crossing a $1,000 band or ETH a $200 band", "product": "futures"},
    "btc_move": {"label": "Unusual BTC/ETH move", "why": "an hourly move far outside its own recent range", "product": "futures"},
    "ath_atl": {"label": "1-year high or low", "why": "the price reached its highest or lowest level in a year", "product": "futures"},
    "agent_pick": {"label": "Agent pick (internal stage)", "why": "the agent's own read of the market picture: funding crowds, OI flushes, listings, rotations — internal cohort only, every line linted", "product": "futures"},
}


def init_tables() -> None:
    conn = get_db()
    conn.execute("""CREATE TABLE IF NOT EXISTS ma2_discovery_fires (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER, day_ist TEXT, week_ist TEXT, signal TEXT,
                    token TEXT, product TEXT, direction TEXT, value REAL, title TEXT, body TEXT, status TEXT, error TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_ma2_disc_day ON ma2_discovery_fires (day_ist, signal)")
    conn.commit(); conn.close()


# ── detection ─────────────────────────────────────────────────────────────────
def large_trade_burst(candles: List[Dict[str, float]], min_usd: float, vol_mult: float, size_mult: float) -> Optional[Dict[str, Any]]:
    """A 5-minute candle whose notional volume and average trade size are both far above the baseline of the ones before it."""
    rows = [c for c in candles if c.get("v") and c.get("c")]
    if len(rows) < 30:
        return None
    notional = [float(c["v"]) * float(c["c"]) for c in rows]
    sizes = [n / max(1.0, float(c.get("n") or 1)) for n, c in zip(notional, rows)]
    last_n, last_s, c = notional[-1], sizes[-1], rows[-1]
    base_n = statistics.median(notional[-49:-1]); base_s = statistics.median(sizes[-49:-1])
    if last_n < min_usd or base_n <= 0 or base_s <= 0:
        return None
    if last_n < base_n * vol_mult or last_s < base_s * size_mult:
        return None
    move = (float(c["c"]) / float(c["o"]) - 1.0) * 100.0 if c.get("o") else 0.0
    return {"notional_usd": round(last_n), "vol_x": round(last_n / base_n, 1), "size_x": round(last_s / base_s, 1),
            "avg_trade_usd": round(last_s), "move_pct": round(move, 2), "direction": "up" if move >= 0 else "down", "price": float(c["c"])}


def _candidates(cfg: Dict[str, Any], state: Dict[str, Any], ctx: Dict[str, Any], now: datetime, whales: List[Dict[str, Any]],
                advance: bool = False) -> Tuple[List[Dict[str, Any]], Dict[str, Any], List[str]]:
    """Every alert the market produced since the last run. `advance` moves the watermarks (live runs only), so a dry run
    previews the same set the next live run will judge, and a late live run catches up on everything that closed meanwhile."""
    errors: List[str] = []
    out: List[Dict[str, Any]] = []
    updates: Dict[str, Any] = {}
    now_ts = now.timestamp()
    crypto = [r for r in ctx.get("crypto_markets") or [] if isinstance(r, dict) and r.get("symbol")]
    prices = {str(r["symbol"]).upper(): r.get("price") for r in crypto if r.get("price")}
    try:
        prices.update({k.split(":", 1)[-1].upper(): v for k, v in sig_mod.mids().items()})      # mark prices beat the cached snapshot
    except Exception as e:
        errors.append(f"mark prices: {str(e)[:60]}")
    top = [str(r["symbol"]).upper() for r in sorted(crypto, key=lambda r: -(r.get("vol_24h_usd") or 0))[:int(cfg.get("discovery_scan_tokens") or 25)]]
    day = now.strftime("%Y-%m-%d")

    # 1. whale trades from the module, keyed by the trade itself; otherwise every closed 5-minute candle since the watermark
    if whales:
        for w in whales:
            tok = str(w.get("token", "")).upper()
            out.append({"det_key": f"large_trades|{tok}|feed:{w.get('side')}:{int(float(w.get('size_usd') or 0))}:{int(float(w.get('ts') or 0))}", "signal": "large_trades",
                        "token": tok, "product": str(w.get("product") or "futures"), "direction": "up" if str(w.get("side", "buy")).lower() == "buy" else "down",
                        "value": float(w.get("size_usd") or 0), "candle_t": float(w.get("ts") or now_ts), "from_feed": True,
                        "source": "CoinDCX whale module (liquidity-venue trades)", "fields": {"token": tok, "product": str(w.get("product") or "futures"), "size_usd": w.get("size_usd")}})
    else:
        for token in top:
            if _BAD_TOKENS.get(token, 0) > now_ts:
                continue
            try:
                dets, _ = detect.scan_bursts(token, sig_mod.hl_candles(token, "5m", sig_mod.CANDLES_5M), cfg, now_ts, advance, now)
                out.extend(dets)
            except Exception as e:
                if "500" in str(e):
                    _BAD_TOKENS[token] = now_ts + 3600          # the venue has no candles for this symbol; ask again in an hour, not every 5 minutes
                errors.append(f"burst {token}: {str(e)[:60]}")

    # 2. the day's most traded, majors excluded (BRD 4.2) — a daily fact, keyed by the day
    mt = sig_mod.most_traded(crypto, (cfg.get("volume_exclude") or {}).get("futures", []))
    if mt:
        out.append({"det_key": f"most_traded|{mt}|{day}", "signal": "most_traded", "token": mt, "product": "futures", "direction": "any",
                    "value": next((r.get("vol_24h_usd") for r in crypto if str(r["symbol"]).upper() == mt), 0), "candle_t": now_ts,
                    "source": "24h volume on the liquidity venue", "fields": {"token": mt, "product": "futures"}})

    # 3. round-number milestones along the closed 5-minute price path since the watermark
    for token, band in (cfg.get("milestone_bands") or {}).items():
        try:
            dets, last_close = detect.scan_milestones(token, sig_mod.hl_candles(token, "5m", sig_mod.CANDLES_5M), float(band), now_ts, advance, now, state.get(f"ms_close:{token}"))
            out.extend(dets)
            if last_close is not None:
                updates[f"ms_close:{token}"] = last_close
            for d in dets:
                if state.get(f"ms_first:{token}:{day}") is None and f"ms_first:{token}:{day}" not in updates:
                    updates[f"ms_first:{token}:{day}"] = d["value"]
        except Exception as e:
            errors.append(f"milestone {token}: {str(e)[:60]}")

    # 4. an unusual move in the majors, judged on each closed candle since the watermark
    interval = (cfg.get("candle") or {}).get("futures", "1h"); window = int((cfg.get("z_window") or {}).get(interval, 168))
    secs = detect.INTERVAL_S.get(interval, 3600)
    for token in (cfg.get("discovery_move_tokens") or ["BTC", "ETH"]):
        try:
            out.extend(detect.scan_moves(token, sig_mod.hl_candles(token, interval, window + sig_mod.CANDLES_1H_EXTRA), interval, window, float(cfg["z_threshold"]), now_ts, advance, now))
        except Exception as e:
            errors.append(f"move {token}: {str(e)[:60]}")

    # 5. 1-year high or low — a daily fact, keyed by the day
    week = sig_mod.iso_week(now)
    for token in top[:int(cfg.get("discovery_ath_tokens") or 12)]:
        try:
            from ..market import sources
            kind = sig_mod.ath_atl(sources.klines(token, "1d", int(cfg["ath_window_days"]) + 1), prices.get(token), int(cfg["ath_window_days"]))
            if kind:
                days = state.get(f"ath_days:{token}:{week}") or []
                blocked = day not in days and len(days) >= int(cfg["ath_cap_per_token_per_week"])
                out.append({"det_key": f"ath_atl|{token}|{kind}:{day}", "signal": "ath_atl", "token": token, "product": "futures", "direction": "up" if kind == "ath" else "down",
                            "value": prices.get(token) or 0, "candle_t": now_ts, "source": "daily candles, 1-year window", "alert_type": kind,
                            "blocked": "ath_weekly_token_cap" if blocked else None, "fields": {"token": token, "product": "futures", "price": prices.get(token)}})
        except Exception as e:
            errors.append(f"ath {token}: {str(e)[:60]}")
    return out, updates, errors


def _template_key(c: Dict[str, Any]) -> str:
    """Real whale trades say buy or sell; the proxy only knows a burst of size, so it says exactly that."""
    if c["signal"] == "agent_pick":
        return "agent"
    if c["signal"] == "large_trades":
        return f"whale:{'buy' if c['direction'] == 'up' else 'sell'}" if c.get("from_feed") else f"burst:{c['direction']}"
    if c["signal"] == "milestone":
        return f"milestone:{c['direction']}"
    if c["signal"] == "ath_atl":
        return "ath:up" if c.get("alert_type") == "ath" else "atl:down"
    if c["signal"] == "btc_move":
        return f"market_move:{c['direction']}"
    return "most_traded:any"


# ── caps ──────────────────────────────────────────────────────────────────────
def fired_today(now: datetime) -> Dict[str, int]:
    init_tables(); conn = get_db()
    rows = conn.execute("SELECT signal, token, direction, COUNT(*) n FROM ma2_discovery_fires WHERE day_ist=? AND status IN ('sent','recorded_mock') GROUP BY signal, token, direction",
                        (now.strftime("%Y-%m-%d"),)).fetchall()
    conn.close()
    out: Dict[str, int] = {"_total": 0}
    for r in rows:
        out[r["signal"]] = out.get(r["signal"], 0) + r["n"]
        out[f"{r['signal']}|{r['token']}|{r['direction']}"] = r["n"]
        out["_total"] += r["n"]
    return out


def _count(counts: Dict[str, int], c: Dict[str, Any], key: str) -> None:
    counts[key] = counts.get(key, 0) + 1
    counts[c["signal"]] = counts.get(c["signal"], 0) + 1
    counts["_total"] = counts.get("_total", 0) + 1


def _quiet(now: datetime, cfg: Dict[str, Any]) -> bool:
    s, e, t = cfg["quiet_start"], cfg["quiet_end"], now.strftime("%H:%M")
    return (t >= s or t < e) if s > e else (s <= t < e)


def experiment() -> Optional[Dict[str, Any]]:
    from .service import state_all
    x = state_all().get("discovery")
    return x if isinstance(x, dict) else None


def is_live(now: Optional[datetime] = None) -> Dict[str, Any]:
    from .service import kill_switch
    now = now or datetime.now(IST)
    x = experiment()
    if kill_switch():
        return {"live": False, "why": "kill switch is on", "experiment": x}
    if not x:
        return {"live": False, "why": "no approved discovery experiment — propose one and approve it on the Ideas board", "experiment": None}
    if x.get("ends_at") and now.isoformat() > x["ends_at"]:
        return {"live": False, "why": f"experiment ended {x['ends_at'][:16]}", "experiment": x}
    return {"live": True, "why": None, "experiment": x, "standing": not x.get("ends_at")}


def _whale_path() -> str:
    from . import cohort
    import os
    return os.path.join(cohort.data_dir(), "whales.json")


def ingest_whales(rows: List[Dict[str, Any]], actor: str = "system") -> Dict[str, Any]:
    """Whale trades from CoinDCX's own liquidity-venue module, posted in by that system.

    Only these fields are kept: token, product, side, size_usd, price, ts. Wallet addresses and anything else are
    dropped at the door — the alert says what traded, never who. Anything older than the TTL is aged out."""
    import os
    cfg = rules.config()
    ttl = float(cfg.get("whale_feed_ttl_min") or 60) * 60
    now = time.time()
    clean: List[Dict[str, Any]] = []
    for r in rows or []:
        if not isinstance(r, dict) or not str(r.get("token") or "").strip():
            continue
        try:
            size = float(r.get("size_usd") or r.get("notional_usd") or 0)
        except (TypeError, ValueError):
            continue
        if size < float(cfg.get("whale_feed_min_usd") or 250000):
            continue
        ts = r.get("ts") or r.get("time") or now
        try:
            ts = float(ts) / 1000.0 if float(ts) > 1e11 else float(ts)        # accept milliseconds or seconds
        except (TypeError, ValueError):
            ts = now
        side = str(r.get("side") or "buy").lower()
        side = "buy" if side in ("buy", "b", "bid", "long") else "sell"
        clean.append({"token": str(r["token"]).upper().split(":")[-1], "product": str(r.get("product") or "futures").lower(), "side": side,
                      "size_usd": round(size), "price": r.get("price"), "ts": ts})
    existing = _whale_feed(all_rows=True)
    seen = {(w["token"], w["side"], w["size_usd"], round(float(w.get("ts") or 0))) for w in existing}
    fresh = [w for w in existing if now - float(w.get("ts") or 0) <= ttl]
    for w in clean:
        if (w["token"], w["side"], w["size_usd"], round(w["ts"])) not in seen:
            fresh.append(w)
    fresh = sorted(fresh, key=lambda w: -float(w.get("ts") or 0))[:500]
    with open(_whale_path(), "w") as f:
        json.dump({"whales": fresh, "updated_at": now}, f)
    audit("ma2.whales_ingested", {"accepted": len(clean), "kept": len(fresh), "rejected": len(rows or []) - len(clean)}, actor=actor)
    return {"accepted": len(clean), "rejected": len(rows or []) - len(clean), "in_window": len(fresh), "ttl_min": ttl / 60,
            "note": "only token, product, side, size and time are kept; wallet addresses and any other field are dropped"}


def _whale_feed(all_rows: bool = False) -> List[Dict[str, Any]]:
    import os
    p = _whale_path()
    if not os.path.exists(p):
        return []
    try:
        with open(p) as f:
            raw = json.load(f)
        rows = raw if isinstance(raw, list) else raw.get("whales") or []
        if all_rows:
            return [w for w in rows if isinstance(w, dict) and w.get("token")]
        ttl = float(rules.config().get("whale_feed_ttl_min") or 60) * 60
        fresh = time.time() - ttl
        return [w for w in rows if isinstance(w, dict) and w.get("token") and (not w.get("ts") or float(w["ts"]) >= fresh)]
    except Exception:
        return []


# ── the run ───────────────────────────────────────────────────────────────────
def _agent_due(now: datetime, cfg: Dict[str, Any]) -> bool:
    from .service import state_all
    last = (state_all() or {}).get("agent_last_run")
    if not last:
        return True
    try:
        return (now - datetime.fromisoformat(last)).total_seconds() >= float(cfg.get("agent_every_min") or 15) * 60
    except Exception:
        return True


def _mark_agent_ran(now: datetime) -> None:
    from .service import state_set_many
    state_set_many({"agent_last_run": now.isoformat()})


def _interval_of(c: Dict[str, Any], cfg: Dict[str, Any]) -> float:
    if c.get("from_feed") or c.get("from_agent"):
        return 0.0
    if c["signal"] == "btc_move":
        return float(detect.INTERVAL_S.get((cfg.get("candle") or {}).get("futures", "1h"), 3600))
    return 300.0


def _level_recent(token: str, level: Any, now: datetime, cfg: Dict[str, Any]) -> bool:
    """Has this round level already been announced (either direction) inside the cooldown?"""
    if level is None:
        return False
    since = (now - timedelta(minutes=float(cfg.get("milestone_level_cooldown_min") or 360))).isoformat()
    conn = get_db()
    r = conn.execute("SELECT 1 FROM ma2_discovery_fires WHERE signal='milestone' AND token=? AND value=? AND status IN ('sent','recorded_mock') AND created_at >= ? LIMIT 1",
                     (token, float(level), since)).fetchone()
    q = conn.execute("SELECT 1 FROM ma2_queue WHERE signal='milestone' AND token=? AND value=? AND status IN ('queued','sent') AND created_at >= ? LIMIT 1",
                     (token, float(level), since)).fetchone()
    conn.close()
    return bool(r or q)


def _mirror(copy: Dict[str, Any], c: Dict[str, Any], cohort_ids: List[str], moengage_status: str) -> Dict[str, Any]:
    """The team's Telegram copy of every alert that went out. Never raises and never changes the alert's own outcome."""
    try:
        from .. import telegram_out
        return telegram_out.mirror_alert(copy["title"], copy["body"], {"signal": c.get("signal"), "token": c.get("token"), "product": c.get("product"), "cohorts": cohort_ids,
                                                                        "source": c.get("source"), "moengage": moengage_status})
    except Exception as e:
        return {"ok": False, "error": redact(str(e))[:120]}


def _write_fire(run_id, now, c, copy, status, error=None) -> None:
    """One row per delivery, written the moment it happens — a crash after this line can never double-send."""
    conn = get_db()
    conn.execute("""INSERT INTO ma2_discovery_fires (run_id, day_ist, week_ist, signal, token, product, direction, value, title, body, status, error, created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", (run_id, now.strftime("%Y-%m-%d"), sig_mod.iso_week(now), c["signal"], c["token"], c.get("product", "futures"),
                                                          c.get("direction", "any"), c.get("value"), copy["title"], copy["body"], status, error, now.isoformat()))
    conn.commit(); conn.close()


def _quiet_end(now: datetime, cfg: Dict[str, Any]) -> datetime:
    h, m = [int(x) for x in str(cfg["quiet_end"]).split(":")[:2]]
    end = now.replace(hour=h, minute=m, second=0, microsecond=0)
    return end if end > now else end + timedelta(days=1)


def run(mode: str = "dry_run", actor: str = "user", now: Optional[datetime] = None, ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Judge everything the market produced since the last run. Live runs advance the watermarks and record each
    detection once; a dry run previews the same set without moving anything."""
    from ..market import context as mctx
    from .service import state_all, state_set_many, _deliver_business_event
    t0 = time.time(); init_tables(); detect.init_tables()
    now = now or datetime.now(IST)
    live = is_live(now)
    if mode == "live" and not live["live"]:
        return {"ok": False, "error": f"not live: {live['why']}"}
    x = live["experiment"] or {}
    stage = x.get("stage") or "internal"                    # no experiment yet → preview the internal stage, which is what launches first
    cfg = rules.config(stage=stage)
    enabled = set(x.get("signals") or SIGNALS) if mode == "live" else set(SIGNALS)
    if cfg.get("agent_autonomy"):
        enabled.add("agent_pick")
    if ctx is None:
        try:
            ctx = mctx.market_context()
        except Exception as e:
            return {"ok": False, "error": f"no market context: {redact(str(e))[:150]}"}
    state = state_all()
    cands, updates, errors = _candidates(cfg, state, ctx, now, _whale_feed(), advance=(mode == "live"))
    already = 0
    if mode == "live":
        fresh = detect.record(cands, now)
        already = len(cands) - len(fresh)
        cands = fresh
    regime = str(((ctx.get("crypto") or {}).get("regime") or {}).get("label") or (ctx.get("hooks") or {}).get("regime") or "unknown")
    agent_note = None
    if cfg.get("agent_autonomy") and stage == "internal" and mode == "live" and _agent_due(now, cfg):
        from . import agent as agent_mod
        tpls0 = rules.templates()
        for c in cands:                                                       # give the agent the default copy it may sharpen
            k0 = _template_key(c); cp0 = rules.render(k0, c.get("fields") or {}, tpls0.get(k0)); c["title"], c["body"] = cp0["title"], cp0["body"]
        ap = agent_mod.pass_once(cands, ctx, cfg, now)
        agent_note = ap
        for c in cands:
            rw = ap["rewrites"].get(c.get("det_key"))
            if rw:
                c["agent_title"], c["agent_body"], c["agent_why"] = rw["title"], rw["body"], rw["why"]
        picks = detect.record(ap["picks"], now)
        cands.extend(picks)
        _mark_agent_ran(now)
    counts = fired_today(now)
    caps = cfg.get("discovery_signal_caps") or {}
    total_cap = int(cfg.get("discovery_daily_cap") or 3)
    quiet = _quiet(now, cfg)
    stress = regime in STRESS and cfg.get("stress_suppress_discovery", True)
    tpls = rules.templates()
    perishable = set(cfg.get("perishable_signals") or [])
    evergreen_set = set(cfg.get("evergreen_signals") or [])
    targets_all = active_cohorts() if mode == "live" else [c for c in cohorts(cfg) if c["stage"] == stage or (stage == "all")]

    order = list(cfg.get("discovery_order") or ["large_trades", "milestone", "btc_move", "ath_atl", "most_traded"])
    cands.sort(key=lambda c: (order.index(c["signal"]) if c["signal"] in order else 99, float(c.get("candle_t") or 0), -abs(float(c.get("value") or 0))))
    decided: List[Dict[str, Any]] = []
    sent = supp = failed = queued_n = held = 0
    queued = timing.queued_keys(now)
    run_id = None
    if mode == "live":
        conn = get_db(); cur = conn.execute("INSERT INTO ma2_runs (started_at, mode, actor) VALUES (?,?,?)", (now.isoformat(), "discovery", actor)); run_id = cur.lastrowid; conn.commit(); conn.close()

    def outcome(c, o):
        if mode == "live" and c.get("det_key"):
            detect.set_outcome(c["det_key"], o, now)

    for c in cands:
        key = f"{c['signal']}|{c['token']}|{c['direction']}"
        reason = None
        if c["signal"] not in enabled:
            reason = "signal_not_enabled"
        elif stress:
            reason = "stress_regime"
        elif c.get("blocked"):
            reason = c["blocked"]
        elif c["signal"] in perishable and not c.get("from_feed") and c.get("candle_t") and (now.timestamp() - (float(c["candle_t"]) + _interval_of(c, cfg))) / 60.0 > float((cfg.get("perishable_max_age_min") or {}).get(c["signal"], 60)):
            reason = "stale_at_detection"            # caught up after an outage: recorded, but a fact this old is not news
        elif c["signal"] == "milestone" and _level_recent(c["token"], c.get("value"), now, cfg):
            reason = "same_level_recently"
        elif counts.get(key, 0) >= 1:
            reason = "already_sent_today"
        elif counts.get(c["signal"], 0) >= int(caps.get(c["signal"], 1)):
            reason = "signal_daily_cap"
        elif counts.get("_total", 0) >= total_cap:
            reason = "platform_daily_cap"
        tkey = _template_key(c) if c["signal"] != "agent_pick" else "agent"
        copy = {"title": c["title"], "body": c["body"]} if c["signal"] == "agent_pick" else rules.render(tkey, c.get("fields") or {}, tpls.get(tkey))
        if c.get("agent_title"):
            copy = {"title": c["agent_title"], "body": c["agent_body"]}
        row = {**c, "template": tkey, "title": copy["title"], "body": copy["body"], "reason": reason, "decision": "suppressed" if reason else ("would_send" if mode != "live" else "sent"),
               "by_agent": bool(c.get("agent_title") or c.get("from_agent")), "why": c.get("agent_why") or c.get("why")}
        evergreen = c["signal"] in evergreen_set
        if not reason and (evergreen or quiet) and (c["signal"], c["token"], c["direction"]) in queued:
            reason = "already_queued_today"
        if reason:
            supp += 1; outcome(c, "suppressed:" + reason)
        elif evergreen or (quiet and c["signal"] in perishable):
            # evergreen facts wait for the day's window; perishable facts caught in quiet hours are HELD until it ends, not dropped
            due = None if evergreen else _quiet_end(now, cfg)
            if mode == "live":
                q = timing.enqueue({**c, "title": copy["title"], "body": copy["body"]}, now, cfg, actor=actor, due_at=due)
                row["decision"] = "queued" if evergreen else "held_quiet_hours"; row["window"] = q["window"]; row["due_at"] = q["due_at"]; row["held_for_min"] = q["held_for_min"]
                queued.add((c["signal"], c["token"], c["direction"]))
                outcome(c, "queued" if evergreen else "held_quiet_hours")
            else:
                w = timing.window_for_day(now.strftime("%Y-%m-%d"), cfg)["window"]
                row["decision"] = "would_queue" if evergreen else "would_hold_quiet_hours"; row["window"] = w["id"] if evergreen else "quiet_end"
                row["due_at"] = (due or timing.next_due(now, w)).isoformat()
            if evergreen:
                queued_n += 1
            else:
                held += 1
        elif mode == "live":
            attrs = {"signal": c["signal"], "token": c["token"], "product": c["product"], "direction": c["direction"], "value": c.get("value"),
                     "title": copy["title"], "body": copy["body"], "landing": "token_page", "source": c.get("source"), "event_key": c.get("det_key"), "run_id": run_id,
                     **{k: v for k, v in (c.get("fields") or {}).items() if k not in ("token", "product")}}
            targets = route(c, targets_all)
            if not targets:
                row["decision"] = "suppressed"; row["reason"] = "no_cohort_takes_this_signal"; supp += 1; outcome(c, "suppressed:no_cohort_takes_this_signal")
                decided.append(row); continue
            if approval_mode(cfg) != "auto":                # a human (in Slack or on Today) says go before each alert leaves; the alert expires with the fact
                pr = propose_alert(c, copy, attrs, [t["id"] for t in targets], now, cfg, actor)
                row["decision"] = "awaiting_approval"; row["proposal_id"] = pr.get("id"); row["cohorts"] = [t["id"] for t in targets]
                _count(counts, c, key); outcome(c, "awaiting_approval")
                decided.append(row); continue
            results = {t["id"]: _send_to_cohort(t, attrs, c.get("det_key") or key) for t in targets}
            ok = [k for k, r in results.items() if r["status"] != "failed"]
            r = {"status": (results[ok[0]]["status"] if ok else "failed"), "error": "; ".join(f"{k}: {v.get('error')}" for k, v in results.items() if v["status"] == "failed") or None}
            row["decision"] = r["status"]; row["cohorts"] = ok
            _write_fire(run_id, now, c, copy, r["status"], r.get("error"))
            if r["status"] == "failed":
                failed += 1; row["error"] = r.get("error"); outcome(c, "failed")
            else:
                sent += 1; _count(counts, c, key); outcome(c, "sent")
                row["telegram"] = _mirror(copy, c, ok, r["status"])
        else:
            sent += 1
            _count(counts, c, key)          # a dry run counts against the caps too, so it shows what live would really send
            row["cohorts"] = [t["id"] for t in route(c, targets_all)]
        decided.append(row)

    released = release(now, actor, regime) if mode == "live" else {"fired": 0, "expired": 0, "due": len(timing.due_now(now))}
    sent += released.get("fired", 0)
    summary = {"regime": regime, "stage": stage, "profile": cfg.get("stage_profile"), "agent": ({k: agent_note[k] for k in ("note", "model", "rejected")} if agent_note else None),
               "quiet_hours": quiet, "stress": stress, "queued": queued_n, "held_quiet_hours": held, "already_recorded": already, "released": released,
               "timing": timing.view(cfg, now), "by_signal": {k: sum(1 for d in decided if d["signal"] == k and d["decision"] in ("sent", "recorded_mock", "would_send")) for k in SIGNALS},
               "suppression_reasons": {r: sum(1 for d in decided if d["reason"] == r) for r in {d["reason"] for d in decided if d["reason"]}},
               "detected": len(cands), "decisions": decided[:40], "whale_source": "CoinDCX whale feed" if _whale_feed() else "large-trade burst proxy (public candles)",
               "experiment": x.get("name"), "audience_note": x.get("audience") or "set in MoEngage on the business-event campaign"}
    ms = int((time.time() - t0) * 1000)
    conn = get_db()
    if mode == "live":
        conn.execute("UPDATE ma2_runs SET delivery=?, users=0, sent=?, holdout=0, suppressed=?, failed=?, summary_json=?, errors_json=?, ms=? WHERE id=?",
                     ("mock" if get_setting("mock_mode", "true").lower() == "true" else "moengage", sent, supp, failed, json.dumps(summary, default=str), json.dumps(errors[:20]), ms, run_id))
    else:
        cur = conn.execute("""INSERT INTO ma2_runs (started_at, mode, delivery, users, sent, holdout, suppressed, failed, summary_json, errors_json, ms, actor)
                              VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", (now.isoformat(), "discovery_dry", "none", 0, sent, 0, supp, failed, json.dumps(summary, default=str), json.dumps(errors[:20]), ms, actor))
        run_id = cur.lastrowid
    conn.commit(); conn.close()
    if mode == "live":
        ath_tokens = [d["token"] for d in decided if d["signal"] == "ath_atl" and d["decision"] in ("sent", "recorded_mock")]
        state_set_many({**updates, **sig_mod.ath_state_updates(ath_tokens, state, now)})
        audit("ma2.discovery_run", {"run_id": run_id, "sent": sent, "failed": failed, "held": held}, actor=actor)
    return {"ok": True, "run_id": run_id, "mode": mode, "detected": len(cands), ("sent" if mode == "live" else "would_send"): sent,
            "queued": queued_n, "held_quiet_hours": held, "released": released.get("fired", 0), "suppressed": supp, "failed": failed, "ms": ms, "summary": summary, "errors": errors[:8]}


def release(now: datetime, actor: str = "engine", regime: Optional[str] = None) -> Dict[str, Any]:
    """Send what the window lane has been holding, re-checking the gates at the moment of sending, not when it was queued."""
    from .service import _deliver_business_event
    cfg = rules.config(stage=(experiment() or {}).get("stage") or "internal")
    expired = timing.expire(now)
    items = timing.due_now(now)
    if not items:
        return {"fired": 0, "expired": expired, "due": 0}
    if _quiet(now, cfg):
        return {"fired": 0, "expired": expired, "due": len(items), "held": "quiet_hours"}
    if regime is None:
        try:
            from ..market import context as mctx
            ctx = mctx._latest(3600) or {}
            regime = str(((ctx.get("crypto") or {}).get("regime") or {}).get("label") or "unknown")
        except Exception:
            regime = "unknown"
    if regime in STRESS and cfg.get("stress_suppress_discovery", True):
        for it in items:
            timing.mark(it["id"], "expired", now)
        return {"fired": 0, "expired": expired + len(items), "due": len(items), "held": "stress_regime"}
    counts = fired_today(now)
    fired = 0
    mids: Dict[str, float] = {}
    if any(it["signal"] == "milestone" for it in items):
        try:
            mids = {k.split(":", 1)[-1].upper(): v for k, v in sig_mod.mids().items()}
        except Exception:
            mids = {}
    for it in items:
        key = f"{it['signal']}|{it['token']}|{it['direction']}"
        if counts.get(key, 0) >= 1 or counts.get(it["signal"], 0) >= int((cfg.get("discovery_signal_caps") or {}).get(it["signal"], 1)) or counts.get("_total", 0) >= int(cfg.get("discovery_daily_cap") or 3):
            timing.mark(it["id"], "expired", now)
            continue
        if it["signal"] == "milestone" and mids.get(it["token"]) is not None:
            px, level = float(mids[it["token"]]), float(it["value"] or 0)
            if (it["direction"] == "up" and px < level) or (it["direction"] == "down" and px > level):
                timing.mark(it["id"], "stale", now)              # the fact stopped being true while it waited
                continue
        targets = route(it, active_cohorts())
        if not targets:
            timing.mark(it["id"], "expired", now); continue
        results = [_send_to_cohort(t, {"signal": it["signal"], "token": it["token"], "product": it["product"], "direction": it["direction"],
                                       "value": it["value"], "title": it["title"], "body": it["body"], "landing": "token_page",
                                       "source": it.get("source"), "window": it["window_id"], "queued_at": it["created_at"]}, f"q{it['id']}|{it['signal']}|{it['token']}") for t in targets]
        r = next((x for x in results if x["status"] != "failed"), results[0])
        timing.mark(it["id"], "sent" if r["status"] != "failed" else "failed", now)
        if r["status"] != "failed":
            _mirror({"title": it["title"], "body": it["body"]}, it, [t["id"] for t in targets], r["status"])
        conn = get_db()
        conn.execute("""INSERT INTO ma2_discovery_fires (run_id, day_ist, week_ist, signal, token, product, direction, value, title, body, status, error, created_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                     (None, now.strftime("%Y-%m-%d"), sig_mod.iso_week(now), it["signal"], it["token"], it["product"], it["direction"], it["value"],
                      it["title"], it["body"], r["status"], r.get("error"), now.isoformat()))
        conn.commit(); conn.close()
        if r["status"] != "failed":
            fired += 1
            _count(counts, {"signal": it["signal"]}, key)
    if fired:
        timing.count_fire(now.strftime("%Y-%m-%d"), fired)
        audit("ma2.discovery_released", {"fired": fired, "window": items[0]["window_id"]}, actor=actor)
    return {"fired": fired, "expired": expired, "due": len(items)}


HEARTBEAT_EVENT = "MA2_Engine_Heartbeat"


def heartbeat(now: datetime) -> Dict[str, Any]:
    """One business event per tick while live. A MoEngage flow that waits for the next heartbeat and pages the ops cohort
    when none arrives in 30 minutes is the dead-man's switch: MoEngage watches the engine, not the other way round."""
    from .service import _deliver_business_event, state_set_many
    r = _deliver_business_event(HEARTBEAT_EVENT, {"at": now.isoformat(), "engine": "moengage-engine", "cadence_min": 5})
    state_set_many({"heartbeat_last": now.isoformat(), "heartbeat_status": r["status"]})
    return r


def scheduled_tick() -> Dict[str, Any]:
    st = is_live()
    now = datetime.now(IST)
    if st["live"]:
        try:
            heartbeat(now)
        except Exception:
            pass
    if not st["live"]:
        released = release(now, "scheduler") if timing.due_now(now) else {"fired": 0}
        return {"ok": True, "skipped": st["why"], "released": released.get("fired", 0)}
    out = run("live", actor="scheduler")
    if now.hour == 6 and now.minute < 30:                       # once a day, learn which window earned its clicks
        try:
            out["feedback"] = timing.feedback(actor="scheduler")
        except Exception as e:
            out["feedback"] = {"ok": False, "why": str(e)[:120]}
    return out


# ── the MoEngage side: a real campaign draft and a real experiment ────────────
def campaign_brief(audience: str = "", control_pct: int = 10, window_days: int = 14, kpi: str = "sessions_per_week", ttl_hours: int = 4, continuous: bool = True,
                   cohort_id: Optional[str] = None) -> Dict[str, Any]:
    """The business-event-triggered push campaign this experiment runs on, as a goal brief the engine can propose.

    Continuous by default: the whole push-enabled base minus the standing exclusions, with a permanent control group so
    lift can be read in any week without ever stopping the programme."""
    cfg = rules.config()
    co = cohort(cohort_id) if cohort_id else None
    if co:
        audience = co["segment"]; control_pct = int(co.get("control_pct") or control_pct)
    stage = "all" if str(audience).upper().startswith("ALL") else ("internal" if (not audience or audience == cfg.get("internal_segment")) else "segment")
    if stage == "internal":
        control_pct = 5                                          # employees all see it; 5% is the smallest control the framework allows
    seg = audience or str(cfg.get("internal_segment") or "INTERNAL_EMPLOYEES")
    event = (co or {}).get("event") or (EVENT if stage == "all" else "MA2_Discovery_INTERNAL" if stage == "internal" else EVENT)
    suffix = ("_" + (co["id"] if co else stage).upper()) if (co or stage != "all") else ""
    return {
        "name": f"MA2_Discovery_Push{suffix}_{datetime.now(IST).strftime('%b%y')}",
        "stage": stage, "cohort": (co or {}).get("id"), "event": event,
        "channel": "push",
        "target_segment": seg,
        "variants": [{"title": "{{BusinessEvent.title}}", "body": "{{BusinessEvent.body}}", "cta": "See the market",
                      "note": "copy is written by the engine per signal and passed on the event; wording variants are tested in the engine's templates, not here"}],
        "schedule": {"type": "business_event_triggered", "business_event": event, "send": "immediately on trigger",
                     "frequency_capping": "leave MoEngage capping ON: the engine caps per signal and per day, not per user"},
        "ttl_hours": ttl_hours,
        "market_hook_id": f"ma2_discovery:{event}",
        "exclusions": ["liquidated in last 14d", "loss-dormant (realised loss >20% of deposits, no trade 21d)", "unsubscribed / DND", "push disabled"],
        "frequency_cap": f"MoEngage per-user cap; engine cap {cfg.get('discovery_daily_cap')}/day across the platform",
        "goal": {
            "transition": "activated_habitual",
            "continuous": continuous,
            "stage": stage,
            "hypothesis": "Market facts that need no personal data (large trades, the day's most traded token, round-number milestones, unusual major moves, 1-year highs) bring active traders back into the app within the day.",
            "primary_kpi": kpi,
            "target": "+0.3 sessions per active user per week vs the permanent control group" if continuous else "+0.3 sessions per active user per week vs the control group",
            "guardrail_metric": "notification_disable_rate",
            "control_group_pct": int(control_pct),
            "measurement_window_days": int(window_days),
            "kill_criteria": ["notification_disable_rate > 0.3% in any week", "uninstall rate above its 28-day baseline", "regime turns to capitulation (the engine pauses discovery on its own)"],
            "suppressions": ["liquidated in last 14 days", "loss-dormant", "over frequency cap", "DND / push disabled"],
        },
        "ice": {"impact": 6, "confidence": 6, "ease": 9, "tagline": "For active traders · market facts they can act on · measured by sessions per week vs control"},
    }


def propose_campaign(audience: str = "", control_pct: int = 10, window_days: int = 14, kpi: str = "sessions_per_week", created_by: str = "user", continuous: bool = True,
                     cohort_id: Optional[str] = None) -> Dict[str, Any]:
    """Queue the MoEngage campaign itself: approving it creates the draft in MoEngage and registers a live experiment with a readout."""
    from ..llm import tools as t
    b = campaign_brief(audience, control_pct, window_days, kpi, continuous=continuous, cohort_id=cohort_id)
    check = t.campaign_brief_check(b["goal"], b["variants"], b["channel"], market_linked=True, ttl_hours=b["ttl_hours"])
    if not check["ok"]:
        return {"error": "brief rejected", "problems": check["problems"]}
    r = t.propose_campaign(b["name"], b["channel"], b["target_segment"], b["variants"],
                           rationale=(f"Discovery alerts experiment, cohort {b.get('cohort') or b['stage']}: the engine fires the {b['event']} business event when a market signal qualifies for this cohort, and this campaign turns each fire into a push. "
                                      "Copy comes from the event attributes, so every alert is linted before it leaves the engine. Control group " + str(control_pct) + "% so the result reads as lift."),
                           goal=b["goal"], schedule=b["schedule"], ttl_hours=b["ttl_hours"], market_hook_id=b["market_hook_id"],
                           exclusions=b["exclusions"], frequency_cap=b["frequency_cap"], ice=b["ice"])
    if r.get("error"):
        return r
    return {"proposal_id": r["proposal_id"], "status": r["status"], "brief_warnings": r.get("brief_warnings"), "campaign": b["name"], "event": b["event"], "cohort": b.get("cohort"),
            "note": "approve on the Ideas board: MoEngage gets the draft and the engine registers it as a live experiment with a readout"}


def launch(audience: str = "", days: int = 0, signals: Optional[List[str]] = None, control_pct: int = 10, kpi: str = "sessions_per_week", created_by: str = "user",
           reviewed: bool = False, cohort_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    """Two steps, on purpose. The first call returns the full launch brief — every push word for word, the audience, the
    timing, the caps, the exact MoEngage draft, the measurement plan, what would fire this minute — and creates nothing.
    Only a call with reviewed=True queues the two approvals, and the brief travels with both so the approver reads the
    same document. days=0 (the default) means continuous: no end date, a permanent control group, weekly readouts."""
    from . import brief as brief_mod
    cfg = rules.config()
    plan = cohorts(cfg)
    ids = [c for c in (cohort_ids or []) if any(x["id"] == c for x in plan)]
    if not ids:                                                                            # legacy single-audience call → the matching cohort
        audience = audience or str(cfg.get("internal_segment") or "INTERNAL_EMPLOYEES")
        ids = ["all" if str(audience).upper().startswith("ALL") else "internal"]
    chosen = [c for c in plan if c["id"] in ids]
    stage = "internal" if all(c["stage"] == "internal" for c in chosen) else "all"
    if any(c["stage"] != "internal" for c in chosen):
        pr = promotion()
        if not pr["ready"]:
            return {"error": "cohorts beyond the internal employees are gated behind the internal stage: " + pr["why"], "promotion": pr, "locked": [c["id"] for c in chosen if c["stage"] != "internal"]}
    audience = chosen[0]["segment"]
    control_pct = int(chosen[0].get("control_pct") or control_pct)
    b = brief_mod.build(days, signals, audience, control_pct, kpi, with_dry_run=True, cohort_ids=ids)
    if b["copy_blocking"]:
        return {"error": "copy fails compliance for " + ", ".join(b["copy_blocking"]) + " — fix the templates first", "brief": b}
    if not reviewed:
        return {"needs_review": True, "brief": b, "brief_markdown": brief_mod.markdown(b),
                "next": "read the brief, then call again with reviewed=true (the tab's Confirm launch button does this)"}
    camps = []
    for c in chosen:
        camp = propose_campaign(c["segment"], int(c.get("control_pct") or control_pct), days or 28, kpi, created_by=created_by, continuous=days <= 0, cohort_id=c["id"])
        if camp.get("error"):
            return {**camp, "cohort": c["id"]}
        camps.append({**camp, "cohort": c["id"], "segment": c["segment"]})
    camp = camps[0]
    exp = propose(name="", days=days, signals=signals, audience=audience, kpi=kpi,
                  note="MoEngage campaign drafts queued as " + ", ".join(f"#{x['proposal_id']} ({x['cohort']})" for x in camps), created_by=created_by, cohort_ids=ids)
    try:                                                   # the approver on the Ideas board sees what the launcher saw
        from .. import approvals
        md = brief_mod.markdown({**b, "would_fire_now": b.get("would_fire_now")})
        for pid in [x["proposal_id"] for x in camps] + [exp.get("proposal_id")]:
            if pid:
                approvals.update_payload(pid, {"launch_brief_md": md, "launch_brief": {k: b[k] for k in ("summary", "signals", "copy", "timing", "governance", "audience", "experiment")}}, actor=created_by, note="launch brief attached")
    except Exception as e:
        exp["brief_attach_error"] = str(e)[:120]
    return {"campaign_proposal_id": camp["proposal_id"], "campaign_proposals": [{k: x[k] for k in ("proposal_id", "cohort", "segment", "campaign", "event")} for x in camps],
            "experiment_proposal_id": exp.get("proposal_id"), "campaign": camp.get("campaign"), "cohorts": ids,
            "brief_warnings": camp.get("brief_warnings"), "brief": b, "stage": stage, "audience": audience,
            "next": "approve both on the Ideas board: the campaign creates the MoEngage draft and the live experiment, the experiment lets the engine fire the event"}


# ── experiment approval ───────────────────────────────────────────────────────
def cohorts(cfg: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """The cohort plan: which segment hears which signals on which business event. Overridable with ma2_cohorts_json."""
    from ..database import get_setting
    cfg = cfg or rules.config()
    rows = [dict(c) for c in DEFAULT_COHORTS]
    try:
        over = json.loads(get_setting("ma2_cohorts_json", "") or "[]") or []
        if over:
            rows = [dict(c) for c in over if isinstance(c, dict) and c.get("id") and c.get("event")]
    except Exception:
        pass
    for c in rows:
        if c["id"] == "internal" or not c.get("segment"):
            c["segment"] = c.get("segment") or str(cfg.get("internal_segment") or "INTERNAL_EMPLOYEES")
        c["stage"] = c.get("stage") or ("internal" if c["id"] == "internal" else "all")
        c["signals"] = [x for x in (c.get("signals") or []) if x in SIGNALS]
    return rows


def cohort(cid: str) -> Optional[Dict[str, Any]]:
    return next((c for c in cohorts() if c["id"] == cid), None)


def active_cohorts() -> List[Dict[str, Any]]:
    """Cohorts whose MoEngage campaign draft has been created (proposal executed) and that the approved experiment lists."""
    from .. import approvals
    x = experiment() or {}
    wanted = set(x.get("cohorts") or ([("internal" if (x.get("stage") or "internal") == "internal" else "all")] if x else []))
    try:
        props = [p for p in approvals.list_proposals(limit=300) if p.get("kind") == "create_campaign" and p.get("status") == "executed"
                 and str((p.get("payload") or {}).get("name") or "").startswith("MA2_Discovery")]
    except Exception:
        props = []
    executed_events = {str(((p.get("payload") or {}).get("schedule") or {}).get("business_event") or "") for p in props}
    mock = get_setting("mock_mode", "true").lower() == "true"
    inform = internal_delivery()["mode"] == "inform"
    out = []
    for c in cohorts():
        if c["id"] not in wanted:
            continue
        has_channel = c["event"] in executed_events or mock or (c["id"] == "internal" and inform)   # a real campaign, a mock workspace, or Inform for employees
        if has_channel:
            out.append(c)
    return out


def internal_delivery() -> Dict[str, Any]:
    """How the employee cohort is reached: the business event → campaign path, or MoEngage Inform straight to the employee ids."""
    from ..database import get_setting
    from . import cohort as cohort_mod
    mode = (get_setting("ma2_internal_delivery", "event") or "event").strip().lower()
    users = cohort_mod.internal_users() if mode == "inform" else []
    return {"mode": mode if (mode == "inform" and users) else "event", "requested": mode, "users": len(users),
            "alert_id_set": bool(get_setting("ma2_inform_alert_id", "").strip()), "user_ids": users}


def _send_to_cohort(t: Dict[str, Any], attrs: Dict[str, Any], det_key: str) -> Dict[str, Any]:
    from .service import _deliver_business_event, _deliver_inform
    if t["id"] == "internal":
        d = internal_delivery()
        if d["mode"] == "inform":
            return _deliver_inform(det_key, {**attrs, "cohort": t["id"]}, d["user_ids"])
    return _deliver_business_event(t["event"], {**attrs, "cohort": t["id"]})


def route(alert: Dict[str, Any], targets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Which cohort events this alert goes to: every active cohort whose signal list includes it."""
    return [c for c in targets if alert["signal"] in set(c.get("signals") or [])]


def stage_of(audience: str, cfg: Optional[Dict[str, Any]] = None) -> str:
    cfg = cfg or rules.config()
    if not audience or audience == cfg.get("internal_segment"):
        return "internal"
    return "all" if str(audience).upper().startswith("ALL") else "segment"


def promotion(now: Optional[datetime] = None) -> Dict[str, Any]:
    """May the whole base be launched yet? Only after the internal-employee stage has run long enough and actually delivered."""
    cfg = rules.config(); now = now or datetime.now(IST)
    init_tables(); conn = get_db()
    row = conn.execute("SELECT MIN(created_at) first_at, COUNT(*) n FROM ma2_discovery_fires WHERE status IN ('sent','recorded_mock')").fetchone()
    conn.close()
    x = experiment() or {}
    internal_seen = bool(x) and x.get("stage", "internal") == "internal"
    first_at = row["first_at"] if row and row["first_at"] else None
    days = round((now - datetime.fromisoformat(first_at)).total_seconds() / 86400, 1) if first_at else 0.0
    fires = int(row["n"] or 0) if row else 0
    need_days, need_fires = int(cfg.get("internal_min_days") or 3), int(cfg.get("internal_min_fires") or 5)
    if not internal_seen and not first_at:
        return {"ready": False, "why": f"no internal stage has run yet — launch to {cfg.get('internal_segment')} first", "days": days, "fires": fires, "need_days": need_days, "need_fires": need_fires}
    if days < need_days or fires < need_fires:
        return {"ready": False, "why": f"internal stage has {fires}/{need_fires} alerts over {days}/{need_days} days", "days": days, "fires": fires, "need_days": need_days, "need_fires": need_fires}
    return {"ready": True, "why": f"internal stage delivered {fires} alerts over {days} days", "days": days, "fires": fires, "need_days": need_days, "need_fires": need_fires}


def propose(name: str = "", days: int = 14, signals: Optional[List[str]] = None, audience: str = "", kpi: str = "", note: str = "", created_by: str = "user",
            override_reason: str = "", cohort_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    """override_reason lets a lead skip the internal-stage gate for the whole base; it is written into the proposal and the audit log."""
    from .. import approvals
    cfg = rules.config()
    sigs = [s for s in (signals or [k for k in SIGNALS if k != "agent_pick"]) if s in SIGNALS and s != "agent_pick"]
    stage = stage_of(audience, cfg)
    payload = {"name": name or f"Discovery alerts {'internal test ' if stage == 'internal' else ''}{datetime.now(IST).strftime('%b %Y')}", "days": int(days), "signals": sigs,
               "stage": stage, "audience": audience or str(cfg.get("internal_segment") or "INTERNAL_EMPLOYEES"),
               "cohorts": [c for c in (cohort_ids or []) if cohort(c)] or (["internal"] if stage == "internal" else ["all"]),
               "kpi": kpi or "sessions and trades within 24h of a fire, treated vs the campaign's control group",
               "caps": {"platform_per_day": cfg.get("discovery_daily_cap"), "per_signal": cfg.get("discovery_signal_caps")}, "note": note[:500]}
    if override_reason and stage == "all":
        payload["promotion_override"] = True; payload["override_reason"] = override_reason[:300]
        audit("ma2.promotion_override", {"reason": override_reason[:300]}, actor=created_by)
    title = f"Discovery alerts experiment ({'internal employees' if stage == 'internal' else payload['audience']}): {payload['name']} · {len(sigs)} signals · " + ("continuous, permanent holdout" if int(days) <= 0 else f"{days}d")
    why = ("Market-level alerts that need no per-user data: large-trade bursts, the day's most traded token, BTC/ETH milestones, unusual major moves and 1-year highs. "
           f"The engine fires the {EVENT} business event; MoEngage chooses the audience and enforces the per-user frequency cap. "
           f"Engine caps: {payload['caps']['platform_per_day']}/day across the platform, per-signal caps, quiet hours, paused in stress regimes. Kill switch stops it at once.")
    r = approvals.propose("ma2_discovery", title, payload, rationale=why, risk="medium", created_by=created_by)
    return {"proposal_id": r["id"], "status": r["status"], "preview": r.get("preview"), "note": "approve on the Ideas board to go live"}


def _validate(payload: Dict[str, Any]) -> None:
    if not [s for s in payload.get("signals") or [] if s in SIGNALS]:
        raise ValueError("pick at least one signal")
    if payload.get("stage") == "all" and not payload.get("promotion_override"):
        pr = promotion()
        if not pr["ready"]:
            raise ValueError("the whole base is gated behind the internal-employee stage: " + pr["why"])
    if not 0 <= int(payload.get("days") or 0) <= 90:
        raise ValueError("days must be 0 (continuous) or 1-90")
    blocking = [k for k in rules.lint_all()["blocking"] if k.startswith(("whale:", "most_traded:", "milestone:", "ath:", "atl:", "price_movement:"))]
    if blocking:
        raise ValueError("copy fails compliance for " + ", ".join(blocking) + " — fix the templates first")


def _preview(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {"status": "ready", "signals": payload.get("signals"), "days": payload.get("days"), "caps": payload.get("caps"), "event": EVENT,
            "kpi": payload.get("kpi"), "audience": payload.get("audience"), "mock_mode": get_setting("mock_mode", "true"),
            "note": "MoEngage frequency capping must stay ON for this campaign: without user ids the engine cannot cap per user."}


def _execute(payload: Dict[str, Any]) -> Dict[str, Any]:
    from .service import state_set_many, set_kill
    now = datetime.now(IST)
    x = {"name": payload["name"], "signals": payload["signals"], "audience": payload.get("audience"), "kpi": payload.get("kpi"), "stage": payload.get("stage", "internal"),
         "cohorts": payload.get("cohorts") or (["internal"] if payload.get("stage", "internal") == "internal" else ["all"]),
         "started_at": now.isoformat(), "continuous": int(payload.get("days") or 0) <= 0,
         "ends_at": None if int(payload.get("days") or 0) <= 0 else (now + timedelta(days=int(payload["days"]))).isoformat(),
         "proposal_id": payload.get("_proposal_id"), "approved_by": payload.get("_approved_by", "user")}
    state_set_many({"discovery": x})
    set_kill(False, actor=x["approved_by"])
    audit("ma2.discovery_live", {k: x[k] for k in ("name", "signals", "ends_at")}, actor=x["approved_by"])
    return {"ok": True, "experiment": x}


# ── one alert, one approval ───────────────────────────────────────────────────
def approval_mode(cfg: Optional[Dict[str, Any]] = None) -> str:
    """auto (the engine fires on detection) | ask (each alert is a proposal: approve in Slack or on Today, then it fires)."""
    return "ask" if str(get_setting("ma2_alert_approval", "auto")).lower() in ("ask", "slack", "queue", "on") else "auto"


def propose_alert(c: Dict[str, Any], copy: Dict[str, str], attrs: Dict[str, Any], cohort_ids: List[str], now: datetime, cfg: Dict[str, Any], actor: str) -> Dict[str, Any]:
    from .. import approvals
    perishable = c["signal"] in set(cfg.get("perishable_signals") or [])
    ages = cfg.get("perishable_max_age_min") or {}
    age_min = float(ages.get(c["signal"], 30) if isinstance(ages, dict) else ages or 30)
    stale_at = (now + timedelta(minutes=age_min)) if perishable else (now + timedelta(hours=6))
    payload = {"signal": c["signal"], "token": c["token"], "product": c.get("product"), "direction": c.get("direction"), "value": c.get("value"), "title": copy["title"], "body": copy["body"],
               "attrs": {k: v for k, v in attrs.items() if k != "run_id"}, "cohorts": cohort_ids, "det_key": c.get("det_key"), "detected_at": now.isoformat(), "stale_at": stale_at.isoformat(), "source": c.get("source")}
    return approvals.propose("alert_send", f"Market alert: {copy['title'][:70]} · {c.get('det_key') or c['token']}", payload,
                             rationale=f"{c['signal'].replace('_', ' ')} on {c['token']}; goes to {', '.join(cohort_ids)}; stale after {stale_at.strftime('%H:%M')} IST", risk="low", created_by=actor)


def _alert_validate(p: Dict[str, Any]) -> None:
    if not p.get("title") or not p.get("body") or not p.get("cohorts"):
        raise ValueError("an alert needs a title, a body and at least one cohort")
    if p.get("_approving") and p.get("stale_at") and datetime.now(IST).isoformat() > str(p["stale_at"]):
        raise ValueError(f"this market fact went stale at {str(p['stale_at'])[11:16]} IST; it is not sent late")
    bad = [x for x in rules.lint(str(p["title"]), str(p["body"])) if x["severity"] in ("high", "medium")]
    if bad:
        raise ValueError("copy fails the alert rules: " + "; ".join(x["rule"] for x in bad))


def _alert_preview(p: Dict[str, Any]) -> Dict[str, Any]:
    return {"title": p.get("title"), "body": p.get("body"), "cohorts": p.get("cohorts"), "events": [c["event"] for c in cohorts() if c["id"] in set(p.get("cohorts") or [])], "stale_at": p.get("stale_at")}


def _alert_execute(p: Dict[str, Any]) -> Dict[str, Any]:
    now = datetime.now(IST)
    targets = [c for c in cohorts() if c["id"] in set(p.get("cohorts") or [])]
    attrs = {**(p.get("attrs") or {}), "title": p["title"], "body": p["body"]}
    results = {t["id"]: _send_to_cohort(t, attrs, str(p.get("det_key") or p.get("token"))) for t in targets}
    ok = [k for k, r in results.items() if r["status"] != "failed"]
    status = results[ok[0]]["status"] if ok else "failed"
    err = "; ".join(f"{k}: {v.get('error')}" for k, v in results.items() if v["status"] == "failed") or None
    c = {"signal": p.get("signal"), "token": p.get("token"), "product": p.get("product") or "futures", "direction": p.get("direction") or "any", "value": p.get("value"), "source": p.get("source")}
    _write_fire(None, now, c, {"title": p["title"], "body": p["body"]}, status, err)
    if status != "failed":
        _mirror({"title": p["title"], "body": p["body"]}, c, ok, status)
    if status == "failed":
        raise RuntimeError(err or "delivery failed")
    return {"success": True, "status": status, "cohorts": ok}


def register() -> None:
    from ..approvals import register_executor
    register_executor("ma2_discovery", _execute, _preview, _validate)
    register_executor("alert_send", _alert_execute, _alert_preview, _alert_validate)


def fires(limit: int = 30) -> List[Dict[str, Any]]:
    init_tables(); conn = get_db()
    rows = [dict(r) for r in conn.execute("SELECT id, created_at, signal, token, direction, value, title, body, status FROM ma2_discovery_fires ORDER BY id DESC LIMIT ?", (limit,))]
    conn.close()
    return rows


def _campaign_state() -> Dict[str, Any]:
    """Is the MoEngage campaign for this experiment proposed, approved or live, and is there an experiment row reading it out?"""
    from .. import approvals
    try:
        props = [p for p in approvals.list_proposals(limit=200) if p.get("kind") == "create_campaign" and "MA2_Discovery" in str((p.get("payload") or {}).get("name") or "")]
    except Exception:
        props = []
    if not props:
        return {"state": "not proposed", "detail": "propose it below: the draft is created in MoEngage on approval", "proposal_id": None, "experiment_id": None}
    p = next((x for x in props if x.get("status") in ("pending", "approved", "executed")), props[0])      # an old expired or rejected draft never hides a newer live one
    exp_id = None
    try:
        conn = get_db(); row = conn.execute("SELECT id FROM experiments WHERE proposal_id=?", (p["id"],)).fetchone(); conn.close()
        exp_id = row["id"] if row else None
    except Exception:
        pass
    detail = {"pending": "waiting for approval on the Ideas board", "executed": "draft created in MoEngage — publish it there to start receiving fires",
              "failed": "creation failed, see the proposal", "approved": "approved", "rejected": "the draft was rejected; launch again when ready",
              "expired": "the draft waited too long for approval and was expired by housekeeping; launch again (standing campaigns no longer expire)"}.get(p.get("status"), p.get("status") or "")
    return {"state": p.get("status"), "detail": detail, "proposal_id": p["id"], "experiment_id": exp_id, "name": (p.get("payload") or {}).get("name")}


def preflight() -> List[Dict[str, Any]]:
    """Why nothing appeared in MoEngage. Every reason a draft or a fire can silently go nowhere, checked one by one."""
    from ..database import get_setting
    from ..moengage import MoEngageClient
    out: List[Dict[str, Any]] = []
    mock = get_setting("mock_mode", "true").lower() == "true"
    out.append({"check": "Mode", "ok": not mock, "detail": "mock mode: drafts and fires are simulated and nothing reaches MoEngage" if mock else "live: writes go to MoEngage",
                "fix": "Engine → Settings → mock_mode = false (and add the API keys) to work against the real workspace" if mock else ""})
    try:
        from ..moengage.public_api import PublicAPI
        api = PublicAPI()
        has_c = api.has("campaigns")
    except Exception as e:
        api, has_c = None, False
        out.append({"check": "MoEngage API", "ok": False, "detail": f"client error: {redact(str(e))[:120]}", "fix": "check Workspace ID, region and keys in Engine → Settings"})
    out.append({"check": "Campaigns API key", "ok": bool(has_c), "detail": "present" if has_c else "missing: the draft cannot be created over the API",
                "fix": "" if has_c else "Engine → Settings → moengage_campaign_key (Campaigns API key from MoEngage → Settings → APIs)"})
    from ..moengage.executors import _created_by
    cb = _created_by()
    out.append({"check": "Creator email", "ok": bool(cb) or mock, "detail": cb or ("not set (only needed once mock mode is off)" if mock else "not set — MoEngage rejects a draft without created_by"),
                "fix": "" if cb or mock else "Engine → Settings → moengage_created_by = your MoEngage dashboard login email"})
    ev_ok, ev_detail = None, "cannot be checked in mock mode"
    ev_fix = f"create {EVENT} in MoEngage → Business Events with the attributes below before going live"
    wanted = sorted({c["event"] for c in cohorts() if c.get("stage") == "internal"} or {EVENT})
    confirmed = [x for x in (get_setting("ma2_business_events_confirmed", "") or "").split(",") if x]
    camp_ok = None; ev_cause = ""
    if not mock and api:
        try:
            r = api.business_events_list()
            names = json.dumps(r.get("data") or r)
            missing = [w for w in wanted if w not in names]
            ev_ok = not missing
            ev_detail = f"{', '.join(wanted)} exist{'s' if len(wanted) == 1 else ''} in MoEngage" if ev_ok else f"{', '.join(missing)} {'is' if len(missing) == 1 else 'are'} not defined in this workspace yet"
            ev_cause = "" if ev_ok else "missing"
            ev_fix = "" if ev_ok else f"MoEngage → Settings → Business Events → create {', '.join(missing)} with the attributes title, body, token, product, signal, deep_link"
        except Exception as e:
            msg = redact(str(e))
            if "401" in msg or "403" in msg:                      # which of workspace id, data centre and key is wrong? two cheap calls tell them apart
                data_ok = None
                try:
                    api.campaigns_search(page_size=1); camp_ok = True
                except Exception:
                    camp_ok = False
                try:
                    api.test_connection(); data_ok = True
                except Exception:
                    data_ok = False if api.has("data") else None
                ev_cause = "key_permission" if camp_ok else "campaigns_key" if data_ok else "workspace_or_dc"
                if camp_ok:
                    ev_detail = "the Campaigns key works for campaigns, but MoEngage will not let it read Business Events: the key lacks that permission"
                    ev_fix = "MoEngage → Settings → Account → API keys → open the Campaigns key → enable Business Events (read and trigger); or confirm here that the event exists"
                elif data_ok:
                    ev_detail = f"the Workspace ID and data centre api-{api.dc} are right (the Data key is accepted), but MoEngage rejects the Campaigns key"
                    ev_fix = "paste the Campaigns API key again: MoEngage → Settings → Account → API keys → Campaigns. A Data or Segmentation key in that field is rejected exactly like this"
                else:
                    ev_detail = f"MoEngage rejects every key on api-{api.dc}: the Workspace ID or the data centre is wrong"
                    ev_fix = "check the Workspace ID (the LIVE one, not the TEST one ending _DEBUG) and the data centre number in the dashboard address (dashboard-0X)"
                ev_ok = False
            else:
                ev_cause = "unknown"
                ev_ok, ev_detail = False, f"could not list business events: {msg[:300]}"
                ev_fix = "the business-events list could not be read; create the event in the dashboard and confirm it here"
            if camp_ok and all(w in confirmed for w in wanted):   # the key can create the draft; only the list is closed to it, and a human has said the event is there
                ev_ok, ev_detail, ev_fix = True, f"{', '.join(wanted)} confirmed by you; MoEngage's list could not be read with this key", ""
    out.append({"check": "Business event", "ok": ev_ok, "detail": ev_detail, "fix": ev_fix, "cause": "" if ev_ok else ev_cause, "events": wanted})
    camp = _campaign_state()
    ok = camp["state"] == "executed" and not mock
    detail = {"not proposed": "no campaign proposal exists yet — press Launch", "pending": f"proposal #{camp['proposal_id']} is waiting for approval on the Ideas board",
              "executed": (f"draft simulated in mock mode (proposal #{camp['proposal_id']}): nothing was created in MoEngage" if mock
                           else f"draft created in MoEngage (proposal #{camp['proposal_id']})"), "failed": f"proposal #{camp['proposal_id']} failed"}.get(camp["state"], camp["state"])
    err = ""
    if camp.get("proposal_id"):
        try:
            from .. import approvals
            pr = approvals.get_proposal(camp["proposal_id"])
            err = str(pr.get("error") or "")[:300]
        except Exception:
            pass
    out.append({"check": "Campaign draft", "ok": ok, "state": camp["state"], "proposal_id": camp.get("proposal_id"), "detail": (camp.get("detail") if camp["state"] in ("expired", "rejected") else detail) + (f" — {err}" if err else ""),
                "fix": "" if ok else {"pending": "approve it on the Ideas board", "not proposed": "press Launch below",
                                      "executed": "switch mock_mode off, then launch again to create the real draft", "expired": "read the launch brief and queue the launch again",
                                      "rejected": "read the launch brief and queue the launch again"}.get(camp["state"], "fix the cause above and launch again")})
    st = is_live()
    out.append({"check": "Engine firing", "ok": st["live"], "detail": st["why"] or "approved and firing", "fix": "" if st["live"] else "approve the discovery experiment on the Ideas board"})
    try:
        import subprocess
        svc = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=5).stdout
        installed = "moengage" in svc.lower()
    except Exception:
        installed = None
    out.append({"check": "Engine as a service", "ok": installed, "detail": "installed under launchd: restarts on crash and at login" if installed else ("not installed: the emitter dies with the terminal" if installed is False else "could not check"),
                "fix": "" if installed else "./cli.py service install"})
    d = internal_delivery()
    if d["requested"] == "inform":
        has_i = False
        try:
            has_i = bool(api and api.has("inform"))
        except Exception:
            pass
        out.append({"check": "Inform (internal cohort)", "ok": bool(has_i and d["alert_id_set"] and d["users"]) or (mock and d["users"] > 0),
                    "detail": f"direct sends to {d['users']} employee id(s)" + ("" if d["alert_id_set"] else " · alert id missing") + ("" if has_i or mock else " · Inform API key missing"),
                    "fix": "" if (has_i or mock) and d["alert_id_set"] and d["users"] else "Engine → Settings: moengage_inform_key and ma2_inform_alert_id; POST /api/alerts2/internal-users with the employee customer ids"})
    try:
        from .. import telegram_out
        tg = telegram_out.status()
        out.append({"check": "Telegram mirror", "ok": True if tg["on"] else None,
                    "detail": (f"on · chat {tg['chat_masked']} · {tg['sent_today']} posted today" + (f" · last error: {tg['last_error']}" if tg["last_error"] and not tg["sent_today"] else "")) if tg["on"]
                    else "set up but switched off" if tg["configured"] else "bot token saved, chat not chosen yet" if tg["token_set"] else "not set up: no bot token saved on this machine",
                    "fix": "" if tg["on"] else "Asks → Get every alert in Telegram: paste the bot token, message the bot once, save again and pick the chat (this machine only)"})
    except Exception:
        pass
    hb = state_all_safe().get("heartbeat_last")
    out.append({"check": "Heartbeat", "ok": bool(hb) if st["live"] else None, "detail": f"last {HEARTBEAT_EVENT} at {str(hb)[11:16]}" if hb else ("no heartbeat yet" if st["live"] else "starts when the experiment is live"),
                "fix": f"create a MoEngage flow: entry on {HEARTBEAT_EVENT}, wait 30 min for the next one, else push the ops cohort — MoEngage then pages you if the engine goes silent"})
    return out


def keepalive() -> Dict[str, Any]:
    """What MoEngage keeps alive on its own, what still needs the engine, and how the engine is kept up."""
    x = experiment() or {}
    return {
        "moengage_owns": ["the campaigns per cohort (business-event triggered, expiry one year, renew before it)", "segments, per-user frequency capping, DND, quiet hours on the campaign",
                          "the control group and the campaign analytics", "delivery, retries, device tokens, opt-outs"],
        "engine_still_needed_for": ["watching the market and firing the business event: MoEngage has no market-data ingestion", "the agent's picks on the internal cohort"],
        "engine_kept_up_by": ["launchd service (./cli.py service install): starts at login, restarts on crash", "in-place reload (./cli.py update) so upgrades never stop it",
                              "watermarks: a run that was late catches up instead of skipping", f"{HEARTBEAT_EVENT} every 5 minutes while live, so a MoEngage flow can page ops when it stops"],
        "internal_delivery": ("MoEngage Inform: direct transactional sends to the employee ids, delivery status per message, no campaign to keep alive"
                              if internal_delivery()["mode"] == "inform" else "business event → the internal campaign (switch to Inform with ma2_internal_delivery=inform and the employee ids)"),
        "heartbeat_event": HEARTBEAT_EVENT, "heartbeat_last": state_all_safe().get("heartbeat_last"),
        "campaign_expiry": "each cohort campaign is created with a one-year expiry; the preflight will warn 14 days before and a PATCH proposal extends it",
        "if_the_mac_is_off": "nothing fires until it is back; MoEngage keeps the campaigns armed, so the first run after restart resumes from the watermark and announces only what is still fresh",
        "cohorts_live": [c["id"] for c in active_cohorts()], "experiment": x.get("name"),
    }


def coverage(day: Optional[str] = None) -> Dict[str, Any]:
    """Replay a day from candle history and prove nothing slipped past the live path. missed_count must be zero."""
    from ..market import context as mctx
    cfg = rules.config()
    day = day or datetime.now(IST).strftime("%Y-%m-%d")
    ctx = mctx._latest(24 * 3600) or {}
    crypto = [r for r in ctx.get("crypto_markets") or [] if isinstance(r, dict) and r.get("symbol")]
    top = [str(r["symbol"]).upper() for r in sorted(crypto, key=lambda r: -(r.get("vol_24h_usd") or 0))[:int(cfg.get("discovery_scan_tokens") or 25)]]
    tokens = list(dict.fromkeys(top + list((cfg.get("milestone_bands") or {}).keys()) + list(cfg.get("discovery_move_tokens") or [])))
    interval = (cfg.get("candle") or {}).get("futures", "1h"); window = int((cfg.get("z_window") or {}).get(interval, 168))
    out = detect.coverage(day, cfg, tokens, lambda t: sig_mod.hl_candles(t, "5m", sig_mod.CANDLES_5M), lambda t: sig_mod.hl_candles(t, interval, window + sig_mod.CANDLES_1H_EXTRA))
    out["outcomes"] = detect.outcomes_since(datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=IST))
    out["whale_feed"] = bool(_whale_feed())
    out["live_since"] = ((experiment() or {}).get("started_at") or "")[:16]
    out["note"] = ("expected = every detection a full replay of the day's closed candles produces; recorded = those the LIVE path judged (dry runs record nothing). "
                   "missed must be zero from the moment the experiment went live; a stale outcome means it was seen but too old to announce")
    return out


def state_all_safe() -> Dict[str, Any]:
    try:
        from .service import state_all
        return state_all() or {}
    except Exception:
        return {}


def status(now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or datetime.now(IST)
    st = is_live(now)
    cfg = rules.config()
    counts = fired_today(now)
    camp = _campaign_state()
    return {"live": st["live"], "why_not_live": st["why"], "experiment": st["experiment"], "signals": SIGNALS, "campaign": camp,
            "launch": [{"step": "MoEngage campaign draft", "state": camp["state"], "detail": camp["detail"]},
                       {"step": "Engine permission to fire", "state": "live" if st["live"] else ("approved" if st["experiment"] else "not proposed"), "detail": st["why"] or f"firing {EVENT}"},
                       {"step": "Readout", "state": "waiting for data" if camp["experiment_id"] else "starts with the campaign", "detail": "lift vs the campaign control group over the measurement window"}],
            "today": {"total": counts.get("_total", 0), "by_signal": {k: counts.get(k, 0) for k in SIGNALS}},
            "caps": {"platform_per_day": cfg.get("discovery_daily_cap"), "per_signal": cfg.get("discovery_signal_caps"), "quiet": f"{cfg['quiet_start']}–{cfg['quiet_end']} IST"},
            "whale_source": "CoinDCX whale feed (uploaded)" if _whale_feed() else "large-trade burst proxy from public 5-minute candles",
            "timing": timing.view(cfg, now), "continuous": bool((st["experiment"] or {}).get("continuous")), "preflight": preflight(),
            "stage": (st["experiment"] or {}).get("stage") or "internal", "internal_segment": cfg.get("internal_segment"), "promotion": promotion(now),
            "cohorts": [{**c, "active": c["id"] in {a["id"] for a in active_cohorts()}, "locked": c["stage"] != "internal" and not promotion(now)["ready"]} for c in cohorts(cfg)],
            "keepalive": keepalive(),
            "autonomy": {"on": bool(rules.config(stage="internal").get("agent_autonomy")), "profile": {k: rules.INTERNAL_PROFILE[k] for k in ("discovery_daily_cap", "quiet_start", "quiet_end", "agent_every_min", "agent_max_picks")},
                         "agent_last_run": (state_all_safe().get("agent_last_run")), "picks_today": counts.get("agent_pick", 0),
                         "note": "internal stage only: the agent rewrites copy and adds its own picks with no human click per alert; the linter, freshness, kill switch and the employee segment still bind"},
            "determinism": {"cadence_min": 5, "closed_candles_only": True, "watermarks": True,
                            "outcomes_today": detect.outcomes_since(now.replace(hour=0, minute=0, second=0, microsecond=0)),
                            "note": "every closed candle is judged once; a late run catches up; quiet hours hold perishable facts instead of dropping them; use /api/alerts2/discovery/coverage to replay a day"},
            "event": EVENT, "fires": fires(20),
            "setup": [
                {"step": f"Create the business event {EVENT}", "detail": "Attributes: signal, token, product, direction, value, title, body, landing, source, plus move, price, level, size_usd where they apply."},
                {"step": "One Business-Event-Triggered push campaign", "detail": f"Trigger on {EVENT}. Title {{{{BusinessEvent.title}}}}, message {{{{BusinessEvent.body}}}}; deep link by token and product."},
                {"step": "Choose the audience on that campaign", "detail": "The engine sends no user ids here: MoEngage decides who hears it. Start with users active in the last 30 days who have push enabled, and exclude anyone liquidated in the last 14 days."},
                {"step": "Leave MoEngage frequency capping ON", "detail": "The opposite of the per-user pilot: without user ids the engine caps per signal and per day, not per user. MoEngage must hold the per-user limit."},
                {"step": "Set a control group on the campaign", "detail": "10–20% control so the experiment can be read as lift rather than as a before-and-after."},
            ]}
