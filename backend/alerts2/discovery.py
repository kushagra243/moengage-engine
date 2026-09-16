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
from . import rules, signals as sig_mod

IST = timezone(timedelta(hours=5, minutes=30))
STRESS = ("capitulation", "high_volatility_down")
EVENT = "MA2_Discovery"

SIGNALS = {
    "large_trades": {"label": "Large trades (whale)", "why": "CoinDCX's whale module when it posts trades in, otherwise a burst of unusual notional volume and average trade size in one 5-minute candle", "product": "futures"},
    "most_traded": {"label": "Most traded today", "why": "the day's highest 24h volume, majors excluded per the BRD", "product": "futures"},
    "milestone": {"label": "Round-number milestone", "why": "BTC crossing a $1,000 band or ETH a $200 band", "product": "futures"},
    "btc_move": {"label": "Unusual BTC/ETH move", "why": "an hourly move far outside its own recent range", "product": "futures"},
    "ath_atl": {"label": "1-year high or low", "why": "the price reached its highest or lowest level in a year", "product": "futures"},
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


def _candidates(cfg: Dict[str, Any], state: Dict[str, Any], ctx: Dict[str, Any], now: datetime, whales: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Dict[str, Any], List[str]]:
    errors: List[str] = []
    out: List[Dict[str, Any]] = []
    updates: Dict[str, Any] = {}
    crypto = [r for r in ctx.get("crypto_markets") or [] if isinstance(r, dict) and r.get("symbol")]
    prices = {str(r["symbol"]).upper(): r.get("price") for r in crypto if r.get("price")}
    top = [str(r["symbol"]).upper() for r in sorted(crypto, key=lambda r: -(r.get("vol_24h_usd") or 0))[:int(cfg.get("discovery_scan_tokens") or 25)]]

    # 1. whale feed if the team uploaded one, else the large-trade burst proxy
    if whales:
        for w in whales[:3]:
            out.append({"signal": "large_trades", "token": str(w.get("token", "")).upper(), "product": str(w.get("product") or "futures"),
                        "direction": "up" if str(w.get("side", "buy")).lower() == "buy" else "down", "value": float(w.get("size_usd") or 0),
                        "from_feed": True, "source": "CoinDCX whale module (liquidity-venue trades)", "fields": {"token": str(w.get("token", "")).upper(), "product": str(w.get("product") or "futures"), "size_usd": w.get("size_usd")}})
    else:
        for token in top:
            try:
                c = sig_mod.hl_candles(token, "5m", 60)
                b = large_trade_burst(c, float(cfg["large_trade_min_usd"]), float(cfg["large_trade_vol_multiple"]), float(cfg["large_trade_size_multiple"]))
                if b:
                    out.append({"signal": "large_trades", "token": token, "product": "futures", "direction": b["direction"], "value": b["notional_usd"],
                                "source": "large-trade burst proxy from public 5-minute candles",
                                "fields": {"token": token, "product": "futures", "size_usd": b["notional_usd"], "move": b["move_pct"], "price": b["price"]}, "detail": b})
            except Exception as e:
                errors.append(f"burst {token}: {str(e)[:60]}")

    # 2. the day's most traded, majors excluded (BRD 4.2)
    mt = sig_mod.most_traded(crypto, (cfg.get("volume_exclude") or {}).get("futures", []))
    if mt:
        out.append({"signal": "most_traded", "token": mt, "product": "futures", "direction": "any", "value": next((r.get("vol_24h_usd") for r in crypto if str(r["symbol"]).upper() == mt), 0),
                    "source": "24h volume on the liquidity venue", "fields": {"token": mt, "product": "futures"}})

    # 3. round-number milestones against the last price the engine saw
    day = now.strftime("%Y-%m-%d")
    for token, band in (cfg.get("milestone_bands") or {}).items():
        px = prices.get(token)
        ms = sig_mod.milestone_cross(state.get(f"ms_last:{token}"), px, float(band))
        if px:
            updates[f"ms_last:{token}"] = px
        if ms:
            out.append({"signal": "milestone", "token": token, "product": "futures", "direction": ms["direction"], "value": ms["level"],
                        "source": "round-number band crossed since the last run", "fields": {"token": token, "product": "futures", "price": px, "level": ms["level"]}})
            if state.get(f"ms_first:{token}:{day}") is None:
                updates[f"ms_first:{token}:{day}"] = ms["level"]

    # 4. an unusual move in the majors ("BTC price hike")
    interval = (cfg.get("candle") or {}).get("futures", "1h"); window = int((cfg.get("z_window") or {}).get(interval, 168))
    for token in (cfg.get("discovery_move_tokens") or ["BTC", "ETH"]):
        try:
            z = sig_mod.zscore([c["c"] for c in sig_mod.hl_candles(token, interval, window + 2)], window)
            if z and abs(z["z"]) >= float(cfg["z_threshold"]):
                out.append({"signal": "btc_move", "token": token, "product": "futures", "direction": "up" if z["ret_pct"] >= 0 else "down", "value": z["z"],
                            "source": f"{interval} move against its own {window}-candle range",
                            "fields": {"token": token, "product": "futures", "move": z["ret_pct"], "price": prices.get(token) or z.get("price")}})
        except Exception as e:
            errors.append(f"move {token}: {str(e)[:60]}")

    # 5. 1-year high or low
    week = sig_mod.iso_week(now)
    for token in top[:int(cfg.get("discovery_ath_tokens") or 12)]:
        try:
            from ..market import sources
            kind = sig_mod.ath_atl(sources.klines(token, "1d", int(cfg["ath_window_days"]) + 1), prices.get(token), int(cfg["ath_window_days"]))
            if kind:
                days = state.get(f"ath_days:{token}:{week}") or []
                blocked = day not in days and len(days) >= int(cfg["ath_cap_per_token_per_week"])
                out.append({"signal": "ath_atl", "token": token, "product": "futures", "direction": "up" if kind == "ath" else "down", "value": prices.get(token) or 0,
                            "source": "daily candles, 1-year window", "alert_type": kind, "blocked": "ath_weekly_token_cap" if blocked else None,
                            "fields": {"token": token, "product": "futures", "price": prices.get(token)}})
        except Exception as e:
            errors.append(f"ath {token}: {str(e)[:60]}")
    return out, updates, errors


def _template_key(c: Dict[str, Any]) -> str:
    """Real whale trades say buy or sell; the proxy only knows a burst of size, so it says exactly that."""
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
    return {"live": True, "why": None, "experiment": x}


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
def run(mode: str = "dry_run", actor: str = "user", now: Optional[datetime] = None, ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    from ..market import context as mctx
    from .service import state_all, state_set_many, _deliver_business_event
    t0 = time.time(); init_tables()
    now = now or datetime.now(IST)
    cfg = rules.config()
    live = is_live(now)
    if mode == "live" and not live["live"]:
        return {"ok": False, "error": f"not live: {live['why']}"}
    x = live["experiment"] or {}
    enabled = set(x.get("signals") or SIGNALS) if mode == "live" else set(SIGNALS)
    if ctx is None:
        try:
            ctx = mctx.market_context()
        except Exception as e:
            return {"ok": False, "error": f"no market context: {redact(str(e))[:150]}"}
    state = state_all()
    cands, updates, errors = _candidates(cfg, state, ctx, now, _whale_feed())
    regime = str(((ctx.get("crypto") or {}).get("regime") or {}).get("label") or (ctx.get("hooks") or {}).get("regime") or "unknown")
    counts = fired_today(now)
    caps = cfg.get("discovery_signal_caps") or {}
    total_cap = int(cfg.get("discovery_daily_cap") or 3)
    quiet = _quiet(now, cfg)
    stress = regime in STRESS and cfg.get("stress_suppress_discovery", True)
    tpls = rules.templates()

    order = list(cfg.get("discovery_order") or ["large_trades", "milestone", "btc_move", "ath_atl", "most_traded"])
    cands.sort(key=lambda c: (order.index(c["signal"]) if c["signal"] in order else 99, -abs(float(c.get("value") or 0))))
    decided: List[Dict[str, Any]] = []
    fires: List[tuple] = []
    sent = supp = failed = 0
    run_id = None
    if mode == "live":
        conn = get_db(); cur = conn.execute("INSERT INTO ma2_runs (started_at, mode, actor) VALUES (?,?,?)", (now.isoformat(), "discovery", actor)); run_id = cur.lastrowid; conn.commit(); conn.close()

    for c in cands:
        key = f"{c['signal']}|{c['token']}|{c['direction']}"
        reason = None
        if c["signal"] not in enabled:
            reason = "signal_not_enabled"
        elif quiet:
            reason = "quiet_hours"
        elif stress:
            reason = "stress_regime"
        elif c.get("blocked"):
            reason = c["blocked"]
        elif counts.get(key, 0) >= 1:
            reason = "already_sent_today"
        elif counts.get(c["signal"], 0) >= int(caps.get(c["signal"], 1)):
            reason = "signal_daily_cap"
        elif counts.get("_total", 0) >= total_cap:
            reason = "platform_daily_cap"
        tkey = _template_key(c)
        copy = rules.render(tkey, c.get("fields") or {}, tpls.get(tkey))
        row = {**c, "template": tkey, "title": copy["title"], "body": copy["body"], "reason": reason, "decision": "suppressed" if reason else ("would_send" if mode != "live" else "sent")}
        if reason:
            supp += 1
        elif mode == "live":
            r = _deliver_business_event(EVENT, {"signal": c["signal"], "token": c["token"], "product": c["product"], "direction": c["direction"],
                                                "value": c.get("value"), "title": copy["title"], "body": copy["body"], "landing": "token_page",
                                                "source": c.get("source"), "run_id": run_id, **{k: v for k, v in (c.get("fields") or {}).items() if k not in ("token", "product")}})
            row["decision"] = r["status"]
            if r["status"] == "failed":
                failed += 1; row["error"] = r.get("error")
            else:
                sent += 1
                _count(counts, c, key)
            fires.append((run_id, now.strftime("%Y-%m-%d"), sig_mod.iso_week(now), c["signal"], c["token"], c["product"], c["direction"], c.get("value"), copy["title"], copy["body"], r["status"], r.get("error")))
        else:
            sent += 1
            _count(counts, c, key)          # a dry run counts against the caps too, so it shows what live would really send
        decided.append(row)

    summary = {"regime": regime, "quiet_hours": quiet, "stress": stress, "by_signal": {k: sum(1 for d in decided if d["signal"] == k and d["decision"] in ("sent", "recorded_mock", "would_send")) for k in SIGNALS},
               "suppression_reasons": {r: sum(1 for d in decided if d["reason"] == r) for r in {d["reason"] for d in decided if d["reason"]}},
               "detected": len(cands), "decisions": decided[:40], "whale_source": "CoinDCX whale feed" if _whale_feed() else "large-trade burst proxy (public candles)",
               "experiment": x.get("name"), "audience_note": x.get("audience") or "set in MoEngage on the business-event campaign"}
    ms = int((time.time() - t0) * 1000)
    conn = get_db()
    if mode == "live":
        if fires:
            conn.executemany("""INSERT INTO ma2_discovery_fires (run_id, day_ist, week_ist, signal, token, product, direction, value, title, body, status, error)
                                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", fires)
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
        audit("ma2.discovery_run", {"run_id": run_id, "sent": sent, "failed": failed}, actor=actor)
    return {"ok": True, "run_id": run_id, "mode": mode, "detected": len(cands), ("sent" if mode == "live" else "would_send"): sent,
            "suppressed": supp, "failed": failed, "ms": ms, "summary": summary, "errors": errors[:8]}


def scheduled_tick() -> Dict[str, Any]:
    st = is_live()
    if not st["live"]:
        return {"ok": True, "skipped": st["why"]}
    return run("live", actor="scheduler")


# ── the MoEngage side: a real campaign draft and a real experiment ────────────
def campaign_brief(audience: str = "", control_pct: int = 20, window_days: int = 14, kpi: str = "sessions_per_week", ttl_hours: int = 4) -> Dict[str, Any]:
    """The business-event-triggered push campaign this experiment runs on, as a goal brief the engine can propose."""
    cfg = rules.config()
    seg = audience or "ACTIVE_30D"
    return {
        "name": f"MA2_Discovery_Push_{datetime.now(IST).strftime('%b%y')}",
        "channel": "push",
        "target_segment": seg,
        "variants": [{"title": "{{BusinessEvent.title}}", "body": "{{BusinessEvent.body}}", "cta": "See the market",
                      "note": "copy is written by the engine per signal and passed on the event; wording variants are tested in the engine's templates, not here"}],
        "schedule": {"type": "business_event_triggered", "business_event": EVENT, "send": "immediately on trigger",
                     "frequency_capping": "leave MoEngage capping ON: the engine caps per signal and per day, not per user"},
        "ttl_hours": ttl_hours,
        "market_hook_id": f"ma2_discovery:{EVENT}",
        "exclusions": ["liquidated in last 14d", "loss-dormant (realised loss >20% of deposits, no trade 21d)", "unsubscribed / DND", "push disabled"],
        "frequency_cap": f"MoEngage per-user cap; engine cap {cfg.get('discovery_daily_cap')}/day across the platform",
        "goal": {
            "transition": "activated_habitual",
            "hypothesis": "Market facts that need no personal data (large trades, the day's most traded token, round-number milestones, unusual major moves, 1-year highs) bring active traders back into the app within the day.",
            "primary_kpi": kpi,
            "target": "+0.3 sessions per active user per week vs the control group",
            "guardrail_metric": "notification_disable_rate",
            "control_group_pct": int(control_pct),
            "measurement_window_days": int(window_days),
            "kill_criteria": ["notification_disable_rate > 0.3% in any week", "uninstall rate above its 28-day baseline", "regime turns to capitulation (the engine pauses discovery on its own)"],
            "suppressions": ["liquidated in last 14 days", "loss-dormant", "over frequency cap", "DND / push disabled"],
        },
        "ice": {"impact": 6, "confidence": 6, "ease": 9, "tagline": "For active traders · market facts they can act on · measured by sessions per week vs control"},
    }


def propose_campaign(audience: str = "", control_pct: int = 20, window_days: int = 14, kpi: str = "sessions_per_week", created_by: str = "user") -> Dict[str, Any]:
    """Queue the MoEngage campaign itself: approving it creates the draft in MoEngage and registers a live experiment with a readout."""
    from ..llm import tools as t
    b = campaign_brief(audience, control_pct, window_days, kpi)
    check = t.campaign_brief_check(b["goal"], b["variants"], b["channel"], market_linked=True, ttl_hours=b["ttl_hours"])
    if not check["ok"]:
        return {"error": "brief rejected", "problems": check["problems"]}
    r = t.propose_campaign(b["name"], b["channel"], b["target_segment"], b["variants"],
                           rationale=("Discovery alerts experiment: the engine fires the " + EVENT + " business event when a market signal qualifies, and this campaign turns each fire into a push. "
                                      "Copy comes from the event attributes, so every alert is linted before it leaves the engine. Control group " + str(control_pct) + "% so the result reads as lift."),
                           goal=b["goal"], schedule=b["schedule"], ttl_hours=b["ttl_hours"], market_hook_id=b["market_hook_id"],
                           exclusions=b["exclusions"], frequency_cap=b["frequency_cap"], ice=b["ice"])
    if r.get("error"):
        return r
    return {"proposal_id": r["proposal_id"], "status": r["status"], "brief_warnings": r.get("brief_warnings"), "campaign": b["name"],
            "note": "approve on the Ideas board: MoEngage gets the draft and the engine registers it as a live experiment with a readout"}


def launch(audience: str = "", days: int = 14, signals: Optional[List[str]] = None, control_pct: int = 20, kpi: str = "sessions_per_week", created_by: str = "user") -> Dict[str, Any]:
    """One action, two approvals: the MoEngage campaign draft, and the engine's permission to fire the signals."""
    camp = propose_campaign(audience, control_pct, days, kpi, created_by=created_by)
    if camp.get("error"):
        return camp
    exp = propose(name="", days=days, signals=signals, audience=audience, kpi=kpi,
                  note=f"MoEngage campaign draft queued as proposal #{camp['proposal_id']}", created_by=created_by)
    return {"campaign_proposal_id": camp["proposal_id"], "experiment_proposal_id": exp.get("proposal_id"), "campaign": camp.get("campaign"),
            "brief_warnings": camp.get("brief_warnings"),
            "next": "approve both on the Ideas board: the campaign creates the MoEngage draft and the live experiment, the experiment lets the engine fire the event"}


# ── experiment approval ───────────────────────────────────────────────────────
def propose(name: str = "", days: int = 14, signals: Optional[List[str]] = None, audience: str = "", kpi: str = "", note: str = "", created_by: str = "user") -> Dict[str, Any]:
    from .. import approvals
    cfg = rules.config()
    sigs = [s for s in (signals or list(SIGNALS)) if s in SIGNALS]
    payload = {"name": name or f"Discovery alerts {datetime.now(IST).strftime('%b %Y')}", "days": int(days), "signals": sigs,
               "audience": audience or "MoEngage segment on the MA2_Discovery campaign (engine sends no user ids)",
               "kpi": kpi or "sessions and trades within 24h of a fire, treated vs the campaign's control group",
               "caps": {"platform_per_day": cfg.get("discovery_daily_cap"), "per_signal": cfg.get("discovery_signal_caps")}, "note": note[:500]}
    title = f"Discovery alerts experiment: {payload['name']} · {len(sigs)} signals · {days}d"
    why = ("Market-level alerts that need no per-user data: large-trade bursts, the day's most traded token, BTC/ETH milestones, unusual major moves and 1-year highs. "
           f"The engine fires the {EVENT} business event; MoEngage chooses the audience and enforces the per-user frequency cap. "
           f"Engine caps: {payload['caps']['platform_per_day']}/day across the platform, per-signal caps, quiet hours, paused in stress regimes. Kill switch stops it at once.")
    r = approvals.propose("ma2_discovery", title, payload, rationale=why, risk="medium", created_by=created_by)
    return {"proposal_id": r["id"], "status": r["status"], "preview": r.get("preview"), "note": "approve on the Ideas board to go live"}


def _validate(payload: Dict[str, Any]) -> None:
    if not [s for s in payload.get("signals") or [] if s in SIGNALS]:
        raise ValueError("pick at least one signal")
    if not 1 <= int(payload.get("days") or 0) <= 90:
        raise ValueError("days must be between 1 and 90")
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
    x = {"name": payload["name"], "signals": payload["signals"], "audience": payload.get("audience"), "kpi": payload.get("kpi"),
         "started_at": now.isoformat(), "ends_at": (now + timedelta(days=int(payload.get("days") or 14))).isoformat(),
         "proposal_id": payload.get("_proposal_id"), "approved_by": payload.get("_approved_by", "user")}
    state_set_many({"discovery": x})
    set_kill(False, actor=x["approved_by"])
    audit("ma2.discovery_live", {k: x[k] for k in ("name", "signals", "ends_at")}, actor=x["approved_by"])
    return {"ok": True, "experiment": x}


def register() -> None:
    from ..approvals import register_executor
    register_executor("ma2_discovery", _execute, _preview, _validate)


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
    p = props[0]
    exp_id = None
    try:
        conn = get_db(); row = conn.execute("SELECT id FROM experiments WHERE proposal_id=?", (p["id"],)).fetchone(); conn.close()
        exp_id = row["id"] if row else None
    except Exception:
        pass
    detail = {"pending": "waiting for approval on the Ideas board", "executed": "draft created in MoEngage — publish it there to start receiving fires",
              "failed": "creation failed, see the proposal", "approved": "approved"}.get(p.get("status"), p.get("status") or "")
    return {"state": p.get("status"), "detail": detail, "proposal_id": p["id"], "experiment_id": exp_id, "name": (p.get("payload") or {}).get("name")}


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
            "event": EVENT, "fires": fires(20),
            "setup": [
                {"step": f"Create the business event {EVENT}", "detail": "Attributes: signal, token, product, direction, value, title, body, landing, source, plus move, price, level, size_usd where they apply."},
                {"step": "One Business-Event-Triggered push campaign", "detail": f"Trigger on {EVENT}. Title {{{{BusinessEvent.title}}}}, message {{{{BusinessEvent.body}}}}; deep link by token and product."},
                {"step": "Choose the audience on that campaign", "detail": "The engine sends no user ids here: MoEngage decides who hears it. Start with users active in the last 30 days who have push enabled, and exclude anyone liquidated in the last 14 days."},
                {"step": "Leave MoEngage frequency capping ON", "detail": "The opposite of the per-user pilot: without user ids the engine caps per signal and per day, not per user. MoEngage must hold the per-user limit."},
                {"step": "Set a control group on the campaign", "detail": "10–20% control so the experiment can be read as lift rather than as a before-and-after."},
            ]}
