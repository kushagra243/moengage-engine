"""
Signal Bridge — the engine's market and behaviour signals become MoEngage
Business Events, so campaigns inside MoEngage fire per user in real time
without this engine ever touching per-user data.

How it works
  catalog   SIGNALS: each has a detector over the cached market context that
            returns zero or more event payloads (attributes MoEngage campaigns
            can filter on: symbol, change, regime, event name …).
  rules     A standing rule is approved once through the normal queue
            (proposal kind `signal_rule`). It carries the daily cap, quiet
            hours (IST) and the regimes in which it may fire. Service signals
            (regime flip, tier-0, OI flush) are allowed in stress regimes;
            everything else is suppressed there.
  evaluate  Runs every 15 minutes from the scheduler: detect → policy → cap →
            de-duplicate (one fire per key per day) → fire through the Business
            Event Trigger API (mock mode records the fire only) → ledger.
The MoEngage side: create the business event with the listed attributes once,
build a Business-Event-Triggered campaign on it (segment filter = the cohort,
e.g. watchers of {{symbol}}), and the engine keeps it fed.
"""
from __future__ import annotations
import json
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from .database import get_db, get_setting
from .security import audit, redact

IST = timezone(timedelta(hours=5, minutes=30))
STRESS = ("capitulation", "high_volatility_down")


def _ctx() -> Dict[str, Any]:
    try:
        from .market.context import _latest
        return _latest(6 * 3600) or {}
    except Exception:
        return {}


def _regime(ctx: Dict[str, Any]) -> str:
    return str(((ctx.get("crypto") or {}).get("regime") or {}).get("label") or (ctx.get("hooks") or {}).get("regime") or "unknown")


# ── detectors: ctx → list of {key, attributes} ────────────────────────────────
def _d_regime_flip(ctx):
    cur = _regime(ctx); prev = _state_get("last_regime")
    _state_set("last_regime", cur)
    if prev and prev != cur and cur != "unknown":
        return [{"key": f"{prev}->{cur}", "attributes": {"regime": cur, "previous": prev, "stress": cur in STRESS, "reasons": "; ".join(((ctx.get("crypto") or {}).get("regime") or {}).get("reasons") or [])[:200]}}]
    return []


def _d_tier0(ctx):
    t0 = ctx.get("tier0")
    if not t0:
        return []
    return [{"key": str(t0.get("detail") or t0.get("kind"))[:80], "attributes": {"kind": t0.get("kind"), "detail": str(t0.get("detail") or "")[:300]}}]


def _d_asset_move(ctx):
    thr = float(get_setting("signal_move_pct", "8") or 8)
    out = []
    for m in (ctx.get("crypto_movers") or []) + (ctx.get("equity_movers") or []) + (ctx.get("commodity_movers") or []):
        ch = m.get("chg_24h")
        if ch is None or abs(float(ch)) < thr or (m.get("vol_24h_usd") or 0) < 5e6:
            continue
        sym = m.get("symbol") or m.get("name")
        out.append({"key": f"{sym}:{'up' if ch > 0 else 'down'}", "attributes": {"symbol": sym, "chg_24h": round(float(ch), 2), "price": m.get("price"), "asset_class": m.get("asset_class", "crypto"), "direction_word": "moved"}})
    return out[:12]


def _d_new_listing(ctx):
    out = []
    for l in ((ctx.get("listings") or {}).get("new") or [])[:10]:
        out.append({"key": f"{l.get('symbol')}:{l.get('product')}", "attributes": {"symbol": l.get("symbol"), "product": l.get("product"), "asset_class": l.get("asset_class", "crypto")}})
    return out


def _d_funding_crowding(ctx):
    md = ctx.get("crypto_movers_detail") or {}
    out = []
    for side in ("crowded_long", "crowded_short"):
        for m in (md.get(side) or [])[:6]:
            out.append({"key": f"{m.get('symbol')}:{side}", "attributes": {"symbol": m.get("symbol"), "side": side.replace("crowded_", ""), "funding_apr_pct": m.get("funding_apr_pct"), "oi_usd": m.get("oi_usd")}})
    return out


def _d_oi_flush(ctx):
    try:
        from .market import moneyflow
        lev = (moneyflow.flow(ctx) or {}).get("leverage") or {}
    except Exception:
        return []
    ch = lev.get("oi_chg_1d_pct")
    if ch is not None and ch <= -5:
        return [{"key": date.today().isoformat(), "attributes": {"oi_chg_1d_pct": ch, "hl_oi_usd": lev.get("hl_oi_usd")}}]
    return []


def _d_salary_week(ctx):
    d = datetime.now(IST).day
    return [{"key": f"day{d}", "attributes": {"day_of_month": d}}] if 2 <= d <= 6 else []


def _d_macro_print(ctx):
    now = datetime.now(timezone.utc); out = []
    for e in ctx.get("calendar") or []:
        if str(e.get("impact")).lower() != "high":
            continue
        try:
            when = datetime.fromisoformat(str(e.get("date")).replace("Z", "+00:00"))
        except Exception:
            continue
        h = (when - now).total_seconds() / 3600
        if 0 < h <= 24:
            out.append({"key": f"{e.get('title')}:{when.date()}", "attributes": {"event": e.get("title"), "country": e.get("country"), "when_utc": when.isoformat(), "hours_away": round(h, 1)}})
    return out[:4]


def _d_competitor_surge(ctx):
    out = []
    for s in ((ctx.get("competitors") or {}).get("surges") or []):
        if s.get("we_list_it"):
            out.append({"key": f"{s.get('symbol')}:{s.get('product')}", "attributes": {"symbol": s.get("symbol"), "product": s.get("product"), "surge_pct": s.get("surge_pct")}})   # venue never included
    return out[:6]


SIGNALS: Dict[str, Dict[str, Any]] = {
    "regime_flip":       dict(name="Market regime flipped", event="moe_regime_flip", service=True, max_per_day=2, detector=_d_regime_flip, sop="sop_regime_stress_mode", who="all actives (holders vs cash)", copy="switch tone; in stress: promos freeze, service card with product lenses", attrs=["regime", "previous", "stress", "reasons"]),
    "tier0_event":       dict(name="Tier-0 event (geopolitical / regulatory / exchange)", event="moe_tier0_event", service=True, max_per_day=1, detector=_d_tier0, sop="sop_global_announcement_lenses", who="everyone, by product lens", copy="core fact + product lens; no promos", attrs=["kind", "detail"]),
    "asset_move":        dict(name="Asset moved ≥ threshold (24h)", event="moe_asset_move", service=False, max_per_day=8, detector=_d_asset_move, sop="sop_market_move_alert", who="watchers / holders of {{symbol}} (segment filter on the event attribute)", copy="fact + alert tool; no direction; TTL 4h", attrs=["symbol", "chg_24h", "price", "asset_class"]),
    "new_listing":       dict(name="New listing on our liquidity venue", event="moe_new_listing", service=False, max_per_day=4, detector=_d_new_listing, sop="sop_new_listing_watchers", who="watchers of related assets; NOT loss-dormant", copy="listing fact + watchlist CTA; never 'buy'", attrs=["symbol", "product", "asset_class"]),
    "funding_crowding":  dict(name="Funding / OI crowding on a perp", event="moe_funding_crowding", service=False, max_per_day=4, detector=_d_funding_crowding, sop="sop_funding_crowding_nudge", who="open positions in {{symbol}}", copy="funding cost in own numbers + margin buffer; no direction", attrs=["symbol", "side", "funding_apr_pct", "oi_usd"]),
    "oi_flush":          dict(name="Leverage flushed (OI −5% in a day)", event="moe_oi_flush", service=True, max_per_day=1, detector=_d_oi_flush, sop="sop_liquidation_recovery", who="LIQUIDATED_24H → T+0 silence, T+24 explainer", copy="service only", attrs=["oi_chg_1d_pct", "hl_oi_usd"]),
    "salary_week":       dict(name="Salary week (2–6 of the month)", event="moe_salary_week", service=False, max_per_day=1, detector=_d_salary_week, sop="sop_verified_to_funded", who="KYC_APPROVED_NODEP, SIP prospects", copy="deposit path / recurring buy; no bonus", attrs=["day_of_month"]),
    "macro_print_t24":   dict(name="High-impact macro print in the next 24h", event="moe_macro_print", service=False, max_per_day=3, detector=_d_macro_print, sop="sop_macro_print_brief", who="perp traders (crypto, index, commodity)", copy="T−24h risk brief; margin buffer; no forecast", attrs=["event", "country", "when_utc", "hours_away"]),
    "competitor_surge":  dict(name="Volume surge elsewhere on a pair we list", event="moe_pair_attention", service=False, max_per_day=4, detector=_d_competitor_surge, sop="sop_asset_spotlight", who="watchers / holders of {{symbol}}", copy="asset spotlight (fact + tool); the venue is never named", attrs=["symbol", "product", "surge_pct"]),
}


# ── storage ───────────────────────────────────────────────────────────────────
def init_tables() -> None:
    conn = get_db()
    conn.execute("""CREATE TABLE IF NOT EXISTS signal_rules (id INTEGER PRIMARY KEY AUTOINCREMENT, signal_id TEXT UNIQUE, event_name TEXT, enabled INTEGER DEFAULT 1, max_per_day INTEGER, quiet_start TEXT, quiet_end TEXT, regimes_json TEXT, approved_by TEXT, proposal_id INTEGER, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS signal_fires (id INTEGER PRIMARY KEY AUTOINCREMENT, rule_id INTEGER, signal_id TEXT, event_name TEXT, fire_key TEXT, attributes_json TEXT, mode TEXT, result TEXT, fired_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS signal_state (key TEXT PRIMARY KEY, value TEXT, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
    conn.commit(); conn.close()


def _state_get(key: str) -> Optional[str]:
    try:
        init_tables(); conn = get_db(); r = conn.execute("SELECT value FROM signal_state WHERE key=?", (key,)).fetchone(); conn.close()
        return r["value"] if r else None
    except Exception:
        return None


def _state_set(key: str, value: str) -> None:
    try:
        init_tables(); conn = get_db(); conn.execute("INSERT INTO signal_state (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP", (key, value)); conn.commit(); conn.close()
    except Exception:
        pass


def rules() -> List[Dict[str, Any]]:
    init_tables(); conn = get_db(); rows = [dict(r) for r in conn.execute("SELECT * FROM signal_rules ORDER BY id").fetchall()]; conn.close()
    for r in rows:
        r["regimes"] = json.loads(r.pop("regimes_json") or "[]")
    return rows


def fires(limit: int = 50, signal_id: Optional[str] = None) -> List[Dict[str, Any]]:
    init_tables(); conn = get_db()
    q = "SELECT * FROM signal_fires" + (" WHERE signal_id=?" if signal_id else "") + " ORDER BY id DESC LIMIT ?"
    rows = [dict(r) for r in conn.execute(q, ((signal_id, limit) if signal_id else (limit,))).fetchall()]; conn.close()
    for r in rows:
        r["attributes"] = json.loads(r.pop("attributes_json") or "{}")
    return rows


def _fired_today(signal_id: str, key: Optional[str] = None) -> int:
    conn = get_db()
    if key is None:
        n = conn.execute("SELECT COUNT(*) FROM signal_fires WHERE signal_id=? AND date(fired_at)=date('now')", (signal_id,)).fetchone()[0]
    else:
        n = conn.execute("SELECT COUNT(*) FROM signal_fires WHERE signal_id=? AND fire_key=? AND date(fired_at)=date('now')", (signal_id, key)).fetchone()[0]
    conn.close(); return int(n)


# ── firing ────────────────────────────────────────────────────────────────────
def _fire(event_name: str, attributes: Dict[str, Any]) -> Dict[str, Any]:
    from .moengage import MoEngageClient
    c = MoEngageClient()
    if getattr(c, "mock_mode", True) or c.mode == "mock":
        return {"mode": "mock", "ok": True, "note": "mock mode: recorded, not sent"}
    try:
        from .moengage.public_api import PublicAPI
        api = PublicAPI()
        r = api.business_event_trigger(event_name, attributes)
        return {"mode": "live", "ok": not (isinstance(r, dict) and r.get("error")), "response": redact(json.dumps(r, default=str))[:300]}
    except Exception as e:
        return {"mode": "live", "ok": False, "error": redact(str(e))[:200]}


def _in_quiet(rule: Dict[str, Any]) -> bool:
    qs, qe = rule.get("quiet_start") or "22:00", rule.get("quiet_end") or "08:00"
    now = datetime.now(IST).strftime("%H:%M")
    return (now >= qs or now < qe) if qs > qe else (qs <= now < qe)


def evaluate(ctx: Optional[Dict[str, Any]] = None, dry_run: bool = False) -> Dict[str, Any]:
    """Detect every signal that has an approved rule, apply policy, caps and de-duplication, fire, ledger."""
    init_tables()
    ctx = ctx if ctx is not None else _ctx()
    regime = _regime(ctx); stress = regime in STRESS
    out: List[Dict[str, Any]] = []
    active = {r["signal_id"]: r for r in rules() if r.get("enabled")}
    for sid, spec in SIGNALS.items():
        rule = active.get(sid)
        try:
            payloads = spec["detector"](ctx) or []
        except Exception as e:
            out.append({"signal": sid, "detected": 0, "status": "detector_error", "error": redact(str(e))[:120]}); continue
        row = {"signal": sid, "event": spec["event"], "detected": len(payloads), "rule": bool(rule), "fired": 0, "skipped": []}
        if not rule:
            row["status"] = "no_rule" if not payloads else "detected_no_rule"; out.append(row); continue
        if stress and not spec["service"]:
            row["status"] = "suppressed_stress"; out.append(row); continue
        if rule.get("regimes") and regime not in rule["regimes"]:
            row["status"] = "regime_not_allowed"; out.append(row); continue
        if not spec["service"] and _in_quiet(rule):
            row["status"] = "quiet_hours"; out.append(row); continue
        cap = int(rule.get("max_per_day") or spec["max_per_day"])
        for p in payloads:
            if _fired_today(sid) >= cap:
                row["skipped"].append(f"cap {cap}/day reached"); break
            if _fired_today(sid, p["key"]):
                row["skipped"].append(f"{p['key']} already fired today"); continue
            attrs = {**p["attributes"], "signal": sid, "regime": regime, "fired_at": datetime.now(timezone.utc).isoformat()}
            res = {"mode": "dry_run", "ok": True} if dry_run else _fire(rule["event_name"] or spec["event"], attrs)
            if not dry_run:
                conn = get_db()
                conn.execute("INSERT INTO signal_fires (rule_id, signal_id, event_name, fire_key, attributes_json, mode, result) VALUES (?,?,?,?,?,?,?)", (rule["id"], sid, rule["event_name"] or spec["event"], p["key"], json.dumps(attrs, default=str), res.get("mode"), json.dumps(res, default=str)[:400]))
                conn.commit(); conn.close()
            row["fired"] += 1
        row["status"] = "fired" if row["fired"] else ("nothing_new" if not payloads else "deduped")
        out.append(row)
    summary = {"evaluated_at": datetime.now(timezone.utc).isoformat(), "regime": regime, "stress": stress, "dry_run": dry_run, "fired": sum(r.get("fired", 0) for r in out), "rows": out}
    _state_set("last_evaluation", json.dumps(summary, default=str)[:20000])
    if summary["fired"] and not dry_run:
        audit("signals.fired", {"count": summary["fired"], "signals": [r["signal"] for r in out if r.get("fired")]}, actor="signal_bridge")
    return summary


def catalog() -> Dict[str, Any]:
    ctx = _ctx(); active = {r["signal_id"]: r for r in rules()}
    rows = []
    for sid, spec in SIGNALS.items():
        try:
            det = spec["detector"](ctx) if sid != "regime_flip" else []      # regime_flip mutates state; shown from the ledger instead
        except Exception:
            det = []
        rows.append({"id": sid, "name": spec["name"], "event": spec["event"], "service": spec["service"], "default_max_per_day": spec["max_per_day"], "attributes": spec["attrs"], "sop": spec["sop"], "who": spec["who"], "copy": spec["copy"],
                     "live_now": len(det), "examples": [d["attributes"] for d in det[:3]], "rule": active.get(sid), "fired_today": _fired_today(sid) if active.get(sid) else 0})
    last = _state_get("last_evaluation")
    return {"signals": rows, "rules": list(active.values()), "fires": fires(30), "last_evaluation": json.loads(last) if last else None, "regime": _regime(ctx),
            "setup": "In MoEngage: Data → Business Events → create each event with the listed attributes; then Campaigns → Business-Event-Triggered → segment filter on the attribute (e.g. watchlist contains {{symbol}}). Approve the rule here; the engine fires the event."}


# ── proposals (approval-gated standing rules) ────────────────────────────────
def propose_rule(signal_id: str, max_per_day: Optional[int] = None, quiet_start: str = "22:00", quiet_end: str = "08:00", regimes: Optional[List[str]] = None, rationale: str = "", created_by: str = "agent") -> Dict[str, Any]:
    from . import approvals
    spec = SIGNALS.get(signal_id)
    if not spec:
        return {"error": f"unknown signal {signal_id}", "available": list(SIGNALS)}
    payload = {"signal_id": signal_id, "event_name": spec["event"], "max_per_day": int(max_per_day or spec["max_per_day"]), "quiet_start": quiet_start, "quiet_end": quiet_end, "regimes": regimes or [], "attributes": spec["attrs"], "sop": spec["sop"], "who": spec["who"]}
    p = approvals.propose("signal_rule", f"Signal rule: {spec['name']} → {spec['event']}", payload, rationale or f"Standing rule: fire MoEngage business event {spec['event']} whenever '{spec['name']}' is detected (≤{payload['max_per_day']}/day, quiet {quiet_start}–{quiet_end} IST, {'service signal: allowed in stress' if spec['service'] else 'suppressed in stress regimes'}). {spec['copy']}.", risk="medium", created_by=created_by)
    return {"proposal_id": p["id"], "status": p["status"], "preview": p.get("preview"), "note": "approve once; the engine then fires the event automatically within the caps"}


def _rule_validate(p: Dict[str, Any]) -> None:
    if p.get("signal_id") not in SIGNALS:
        raise ValueError("unknown signal")
    if not (1 <= int(p.get("max_per_day") or 1) <= 20):
        raise ValueError("max_per_day must be 1–20")


def _rule_preview(p: Dict[str, Any]) -> Dict[str, Any]:
    spec = SIGNALS[p["signal_id"]]
    return {"status": "ready", "event_name": p.get("event_name") or spec["event"], "attributes": spec["attrs"], "cap_per_day": p.get("max_per_day"), "quiet_ist": f"{p.get('quiet_start')}–{p.get('quiet_end')}", "stress_regimes": "allowed (service signal)" if spec["service"] else "suppressed",
            "moengage_setup": ["Data → Business Events → create '%s' with attributes %s" % (spec["event"], ", ".join(spec["attrs"])), "Campaigns → Business-Event-Triggered → trigger on that event; audience filter = %s" % spec["who"], "Copy per SOP %s: %s" % (spec["sop"], spec["copy"])], "example_now": (spec["detector"](_ctx()) or [{}])[:1] if p["signal_id"] != "regime_flip" else []}


def _rule_execute(p: Dict[str, Any]) -> Dict[str, Any]:
    init_tables(); conn = get_db()
    conn.execute("""INSERT INTO signal_rules (signal_id, event_name, enabled, max_per_day, quiet_start, quiet_end, regimes_json, approved_by, proposal_id) VALUES (?,?,1,?,?,?,?,?,?)
                    ON CONFLICT(signal_id) DO UPDATE SET event_name=excluded.event_name, enabled=1, max_per_day=excluded.max_per_day, quiet_start=excluded.quiet_start, quiet_end=excluded.quiet_end, regimes_json=excluded.regimes_json, approved_by=excluded.approved_by, proposal_id=excluded.proposal_id, updated_at=CURRENT_TIMESTAMP""",
                 (p["signal_id"], p.get("event_name") or SIGNALS[p["signal_id"]]["event"], int(p.get("max_per_day") or 1), p.get("quiet_start") or "22:00", p.get("quiet_end") or "08:00", json.dumps(p.get("regimes") or []), p.get("_approved_by", "user"), p.get("_proposal_id")))
    conn.commit(); conn.close()
    return {"ok": True, "rule": p["signal_id"], "note": "rule active; the engine evaluates every 15 minutes"}


def set_enabled(signal_id: str, enabled: bool, actor: str = "user") -> Dict[str, Any]:
    init_tables(); conn = get_db(); conn.execute("UPDATE signal_rules SET enabled=?, updated_at=CURRENT_TIMESTAMP WHERE signal_id=?", (1 if enabled else 0, signal_id)); conn.commit(); conn.close()
    audit("signals.rule_toggle", {"signal": signal_id, "enabled": enabled}, actor=actor)
    return {"ok": True, "signal_id": signal_id, "enabled": enabled}


def register() -> None:
    from .approvals import register_executor
    register_executor("signal_rule", _rule_execute, _rule_preview, _rule_validate)
