"""
Market Alerts 2.0 — runs, ledger, pilot and delivery.

run("dry_run")  decides for every cohort user against live market signals; writes a run record only. Always allowed.
run("live")     needs an approved, unexpired pilot (proposal kind ma2_pilot) and the kill switch off. Sends one MoEngage
                user event per decided alert (mock mode records instead), writes the hashed cap ledger, and moves the
                milestone / ATH state forward. Holdout users are decided and capped exactly like treatment, never sent.
"""
from __future__ import annotations
import json
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from ..database import get_db, get_setting, set_setting
from ..security import audit, redact
from . import cohort as cohort_mod, governance as gov, rules, signals as sig_mod

IST = timezone(timedelta(hours=5, minutes=30))
STRESS = ("capitulation", "high_volatility_down")
SAMPLE_DECISIONS = 60


def init_tables() -> None:
    conn = get_db()
    conn.execute("""CREATE TABLE IF NOT EXISTS ma2_ledger (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER, uid_hash TEXT, day_ist TEXT, month_ist TEXT,
                    category TEXT, theme TEXT, alert_type TEXT, slot TEXT, token TEXT, product TEXT, direction TEXT, value REAL, pos_key TEXT,
                    status TEXT, error TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_ma2_ledger_uid_day ON ma2_ledger (uid_hash, day_ist)")
    conn.execute("""CREATE TABLE IF NOT EXISTS ma2_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT, mode TEXT, delivery TEXT, users INTEGER, holdout_users INTEGER,
                    sent INTEGER, holdout INTEGER, suppressed INTEGER, failed INTEGER, summary_json TEXT, errors_json TEXT, ms INTEGER, actor TEXT)""")
    conn.execute("CREATE TABLE IF NOT EXISTS ma2_state (key TEXT PRIMARY KEY, value TEXT, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    conn.commit(); conn.close()


# ── state ─────────────────────────────────────────────────────────────────────
def state_all() -> Dict[str, Any]:
    init_tables(); conn = get_db()
    rows = conn.execute("SELECT key, value FROM ma2_state").fetchall(); conn.close()
    out = {}
    for r in rows:
        try:
            out[r["key"]] = json.loads(r["value"])
        except Exception:
            out[r["key"]] = r["value"]
    return out


def state_set_many(updates: Dict[str, Any]) -> None:
    if not updates:
        return
    init_tables(); conn = get_db()
    for k, v in updates.items():
        conn.execute("INSERT INTO ma2_state (key, value, updated_at) VALUES (?,?,CURRENT_TIMESTAMP) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP",
                     (k, json.dumps(v)))
    conn.commit(); conn.close()


# ── ledger ────────────────────────────────────────────────────────────────────
CONSUMING = ("sent", "recorded_mock", "holdout")


def ledgers_for(hashes: List[str], now: datetime, cfg: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    init_tables()
    day = now.strftime("%Y-%m-%d"); since_xs = (now - timedelta(days=int(cfg["cross_sell_window_days"]))).strftime("%Y-%m-%d")
    out = {h: gov.empty_ledger() for h in hashes}
    if not hashes:
        return out
    conn = get_db()
    for i in range(0, len(hashes), 900):
        chunk = hashes[i:i + 900]; q = ",".join("?" * len(chunk)); cons = ",".join("?" * len(CONSUMING))
        for r in conn.execute(f"SELECT uid_hash, category, slot, alert_type FROM ma2_ledger WHERE uid_hash IN ({q}) AND day_ist=? AND status IN ({cons})", chunk + [day] + list(CONSUMING)):
            lg = out[r["uid_hash"]]
            if r["category"] in ("relevance", "discovery") and r["slot"] in ("baseline", "extra"):
                lg["today"][r["category"]][r["slot"]] += 1
            if r["alert_type"] == "milestone":
                lg["milestone_today"] += 1
        for r in conn.execute(f"SELECT uid_hash, pos_key, value FROM ma2_ledger WHERE uid_hash IN ({q}) AND theme='pnl' AND status IN ({cons}) ORDER BY id", chunk + list(CONSUMING)):
            out[r["uid_hash"]]["pnl_last"][r["pos_key"]] = r["value"]
        for r in conn.execute(f"SELECT DISTINCT uid_hash FROM ma2_ledger WHERE uid_hash IN ({q}) AND theme='cross_sell' AND day_ist>? AND status IN ({cons})", chunk + [since_xs] + list(CONSUMING)):
            out[r["uid_hash"]]["cross_sell_recent"] = True
        for r in conn.execute(f"SELECT DISTINCT uid_hash FROM ma2_ledger WHERE uid_hash IN ({q}) AND theme='referral' AND status IN ({cons})", chunk + list(CONSUMING)):
            out[r["uid_hash"]]["referral_ever"] = True
    conn.close()
    return out


# ── pilot ─────────────────────────────────────────────────────────────────────
def pilot() -> Optional[Dict[str, Any]]:
    p = state_all().get("pilot")
    return p if isinstance(p, dict) else None


def kill_switch() -> bool:
    return str(get_setting("ma2_kill_switch", "false")).lower() == "true"


def set_kill(on: bool, actor: str = "user") -> Dict[str, Any]:
    set_setting("ma2_kill_switch", "true" if on else "false")
    audit("ma2.kill_switch", {"on": on}, actor=actor)
    return {"kill_switch": on}


def pilot_live(now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or datetime.now(IST)
    p = pilot()
    if kill_switch():
        return {"live": False, "why": "kill switch is on", "pilot": p}
    if not p:
        return {"live": False, "why": "no approved pilot — propose one and approve it on the Ideas board", "pilot": None}
    if p.get("ends_at") and now.isoformat() > p["ends_at"]:
        return {"live": False, "why": f"pilot ended {p['ends_at'][:16]}", "pilot": p}
    return {"live": True, "why": None, "pilot": p}


def propose_pilot(name: str = "", holdout_pct: Optional[int] = None, days: int = 14, themes: Optional[List[str]] = None, note: str = "", created_by: str = "user") -> Dict[str, Any]:
    from .. import approvals
    c = cohort_mod.load()
    if not c:
        return {"error": "upload the pilot cohort first"}
    cfg = rules.config()
    themes = [t for t in (themes or list(rules.THEMES)) if t in rules.THEMES]
    payload = {"name": name or c["meta"]["name"], "members_digest": c["meta"]["members_digest"], "users": c["meta"]["summary"]["users"],
               "holdout_pct": int(holdout_pct if holdout_pct is not None else cfg["holdout_pct"]), "days": int(days), "themes": themes, "note": note[:500],
               "by_product": c["meta"]["summary"]["by_product"]}
    title = f"Market Alerts 2.0 pilot: {payload['name']} · {payload['users']} users · {payload['holdout_pct']}% holdout · {days}d"
    why = (f"Go live for this cohort only. Alerts send as MoEngage user events ({', '.join(sorted({rules.THEMES[t]['event'] for t in themes}))}); "
           f"caps and priorities are enforced by the engine per the BRD; {payload['holdout_pct']}% of the cohort is held out to measure lift; kill switch stops it at once.")
    r = approvals.propose("ma2_pilot", title, payload, rationale=why, risk="medium", created_by=created_by)
    return {"proposal_id": r["id"], "status": r["status"], "preview": r.get("preview"), "note": "approve on the Ideas board to go live"}


def _validate(payload: Dict[str, Any]) -> None:
    c = cohort_mod.load()
    if not c:
        raise ValueError("no pilot cohort uploaded")
    if payload.get("_approving") and c["meta"]["members_digest"] != payload.get("members_digest"):
        raise ValueError("the cohort changed since this pilot was proposed — propose again for the new membership")
    if not 0 <= int(payload.get("holdout_pct", 20)) <= 50:
        raise ValueError("holdout must be between 0 and 50%")
    prefixes = tuple(pre for t in payload.get("themes") or [] for pre in rules.THEME_TEMPLATES.get(t, ()))
    blocked = [k for k in rules.lint_all()["blocking"] if prefixes and k.startswith(prefixes)]
    if blocked:
        raise ValueError("copy fails compliance for " + ", ".join(blocked) + " — fix the templates first")


def _preview(payload: Dict[str, Any]) -> Dict[str, Any]:
    c = cohort_mod.load() or {"meta": {}}
    return {"status": "ready" if c.get("meta", {}).get("members_digest") == payload.get("members_digest") else "cohort changed",
            "users": payload.get("users"), "holdout_pct": payload.get("holdout_pct"), "days": payload.get("days"), "themes": payload.get("themes"),
            "copy_blocking": rules.lint_all()["blocking"], "mock_mode": get_setting("mock_mode", "true")}


def _execute(payload: Dict[str, Any]) -> Dict[str, Any]:
    now = datetime.now(IST)
    p = {"name": payload["name"], "members_digest": payload["members_digest"], "holdout_pct": int(payload["holdout_pct"]), "themes": payload["themes"],
         "users": payload.get("users"), "started_at": now.isoformat(), "ends_at": (now + timedelta(days=int(payload.get("days") or 14))).isoformat(),
         "proposal_id": payload.get("_proposal_id"), "approved_by": payload.get("_approved_by", "user")}
    state_set_many({"pilot": p})
    set_kill(False, actor=p["approved_by"])
    audit("ma2.pilot_live", {k: p[k] for k in ("name", "holdout_pct", "users", "ends_at")}, actor=p["approved_by"])
    return {"ok": True, "pilot": p}


def register() -> None:
    from ..approvals import register_executor
    register_executor("ma2_pilot", _execute, _preview, _validate)


# ── delivery ──────────────────────────────────────────────────────────────────
def _deliver(user_id: str, event: str, attrs: Dict[str, Any], cfg: Dict[str, Any]) -> Dict[str, Any]:
    from ..moengage import MoEngageClient
    c = MoEngageClient()
    if getattr(c, "mock_mode", True):
        return {"status": "recorded_mock"}
    try:
        from ..moengage.public_api import PublicAPI
        PublicAPI().track_event(user_id, event, attrs, platform=cfg.get("event_platform") or "web")
        return {"status": "sent"}
    except Exception as e:
        return {"status": "failed", "error": redact(str(e))[:200]}


def _deliver_business_event(event: str, attrs: Dict[str, Any]) -> Dict[str, Any]:
    """Discovery alerts have no user id: MoEngage picks the audience from a segment on the business-event campaign."""
    from ..moengage import MoEngageClient
    if getattr(MoEngageClient(), "mock_mode", True):
        return {"status": "recorded_mock"}
    try:
        from ..moengage.public_api import PublicAPI
        PublicAPI().business_event_trigger(event, {k: v for k, v in attrs.items() if v is not None})
        return {"status": "sent"}
    except Exception as e:
        return {"status": "failed", "error": redact(str(e))[:200]}


def _attrs(d: Dict[str, Any], key: str, copy: Dict[str, str], run_id: Optional[int]) -> Dict[str, Any]:
    f = d.get("fields") or {}
    return {"ma_category": d["category"], "ma_theme": d["theme"], "ma_alert_type": d["alert_type"], "ma_slot": d.get("slot") or "",
            "token": d["token"], "product": d["product"], "direction": d["direction"], "landing": rules.THEMES[d["theme"]]["landing"],
            "move_pct": f.get("move"), "pnl_pct": f.get("pnl"), "price": f.get("price"), "level": f.get("level"), "z_score": d.get("value") if d["theme"] == "price_movement" else None,
            "title": copy["title"], "body": copy["body"], "template": key, "run_id": run_id}


# ── the run ───────────────────────────────────────────────────────────────────
def _quiet(now: datetime, cfg: Dict[str, Any]) -> bool:
    s, e, t = cfg["quiet_start"], cfg["quiet_end"], now.strftime("%H:%M")
    return (t >= s or t < e) if s > e else (s <= t < e)


def run(mode: str = "dry_run", actor: str = "user", now: Optional[datetime] = None, ctx: Optional[Dict[str, Any]] = None,
        signals_override: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    t0 = time.time()
    init_tables()
    now = now or datetime.now(IST)
    cfg = rules.config()
    c = cohort_mod.load()
    if not c:
        return {"ok": False, "error": "no pilot cohort uploaded"}
    live_state = pilot_live(now)
    if mode == "live" and not live_state["live"]:
        return {"ok": False, "error": f"not live: {live_state['why']}"}
    p = live_state["pilot"] or {}
    holdout_pct = int(p.get("holdout_pct", cfg["holdout_pct"]))
    themes = set(p.get("themes") or rules.THEMES) if mode == "live" else set(rules.THEMES)
    approved = None
    if mode == "live":
        try:
            with open(f"{cohort_mod.data_dir()}/members.json") as f:
                approved = set(json.load(f)) if c["meta"]["members_digest"] == p.get("members_digest") else None
        except Exception:
            approved = None
        if approved is None:
            return {"ok": False, "error": "cohort membership changed since the pilot was approved — propose the pilot again"}

    users = c["users"]
    state = state_all()
    errors: List[str] = []
    if signals_override is not None:
        sig, updates = signals_override, {}
    else:
        sig, updates, errors = sig_mod.compute(cohort_mod.tokens_by_product(users), cfg, state, c.get("whales") or [], now, ctx)
    gates_base = {"quiet": _quiet(now, cfg), "stress": sig.get("regime") in STRESS, "exposure_age_min": c["meta"].get("age_min")}

    hashes = {u["user_id"]: cohort_mod.hash_id(u["user_id"]) for u in users}
    ledgers = ledgers_for(list(hashes.values()), now, cfg)
    tpls = rules.templates()
    reasons: Counter = Counter(); by_theme: Counter = Counter(); per_user: Counter = Counter(); samples: List[Dict[str, Any]] = []
    sent = hold = supp = failed = 0; holdout_users = 0; sends_left = int(cfg["max_sends_per_run"]); ath_sent: List[str] = []
    rows: List[tuple] = []
    run_id = None
    if mode == "live":
        conn = get_db(); cur = conn.execute("INSERT INTO ma2_runs (started_at, mode, actor) VALUES (?,?,?)", (now.isoformat(), mode, actor)); run_id = cur.lastrowid; conn.commit(); conn.close()

    for u in users:
        h = hashes[u["user_id"]]
        u = {**u, "uid_hash": h, "holdout": cohort_mod.holdout_bucket(h) < holdout_pct}
        holdout_users += 1 if u["holdout"] else 0
        if approved is not None and h not in approved:
            reasons["not_in_approved_cohort"] += 1
            continue
        lg = ledgers.get(h) or gov.empty_ledger()
        cands = [x for x in gov.build_candidates(u, sig, lg, cfg) if x["theme"] in themes]
        decisions = gov.decide(u, cands, lg, gates_base, cfg)
        n_user = 0
        for d in decisions:
            key = rules.template_key(d["theme"], d["alert_type"], d["direction"])
            copy = rules.render(key, {**(d.get("fields") or {}), "relation": d.get("relation")}, tpls.get(key))
            status = d["decision"]
            if status == gov.SEND and sends_left <= 0:
                status, d = gov.SUPPRESS, {**d, "reason": "run_send_limit"}
            if status == gov.SUPPRESS:
                supp += 1; reasons[d["reason"]] += 1
            elif status == gov.HOLDOUT:
                hold += 1; by_theme[f"{d['theme']}:holdout"] += 1
            else:
                by_theme[d["theme"]] += 1
                if d["category"] != "mot":
                    n_user += 1
            outcome = {"status": {"send": "would_send", "holdout": "holdout", "suppress": "suppressed"}[status]}
            if mode == "live" and status == gov.SEND:
                sends_left -= 1
                outcome = _deliver(u["user_id"], rules.THEMES[d["theme"]]["event"], _attrs(d, key, copy, run_id), cfg)
                if outcome["status"] == "failed":
                    failed += 1
                else:
                    sent += 1
                if d["alert_type"] in ("ath", "atl"):
                    ath_sent.append(d["token"])
            elif mode != "live" and status == gov.SEND:
                sent += 1
            if mode == "live" and status in (gov.SEND, gov.HOLDOUT):
                rows.append((run_id, h, now.strftime("%Y-%m-%d"), now.strftime("%Y-%m"), d["category"], d["theme"], d["alert_type"], d.get("slot"), d["token"],
                             d["product"], d["direction"], d.get("value"), d.get("pos_key"), outcome["status"], outcome.get("error")))
            if len(samples) < SAMPLE_DECISIONS and (status != gov.SUPPRESS or len(samples) < SAMPLE_DECISIONS // 3):
                samples.append({"user": h[:10], "holdout": u["holdout"], "category": d["category"], "theme": d["theme"], "alert_type": d["alert_type"], "slot": d.get("slot"),
                                "token": d["token"], "product": d["product"], "direction": d["direction"], "relation": d.get("relation"), "decision": outcome["status"],
                                "reason": d.get("reason"), "title": copy["title"], "body": copy["body"]})
        per_user[min(n_user, 4)] += 1

    ms = int((time.time() - t0) * 1000)
    summary = {"by_theme": dict(by_theme), "suppression_reasons": dict(reasons.most_common()), "alerts_per_user_this_run": {str(k): v for k, v in sorted(per_user.items())},
               "regime": sig.get("regime"), "quiet_hours": gates_base["quiet"], "exposure_age_min": gates_base["exposure_age_min"],
               "signals": {"moves": len(sig.get("moves") or {}), "moves_over_threshold": sum(1 for m in (sig.get("moves") or {}).values() if abs(m.get("z") or 0) >= cfg["z_threshold"]),
                           "ath_atl": sig.get("ath_atl"), "milestones": sig.get("milestones"), "most_traded": sig.get("most_traded"), "whales": len(sig.get("whales") or [])},
               "samples": samples, "pilot": p.get("name"), "holdout_pct": holdout_pct}
    delivery = "none" if mode != "live" else ("mock" if get_setting("mock_mode", "true").lower() == "true" else "moengage")
    conn = get_db()
    if mode == "live":
        conn.executemany("""INSERT INTO ma2_ledger (run_id, uid_hash, day_ist, month_ist, category, theme, alert_type, slot, token, product, direction, value, pos_key, status, error)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
        conn.execute("UPDATE ma2_runs SET delivery=?, users=?, holdout_users=?, sent=?, holdout=?, suppressed=?, failed=?, summary_json=?, errors_json=?, ms=? WHERE id=?",
                     (delivery, len(users), holdout_users, sent, hold, supp, failed, json.dumps(summary, default=str), json.dumps(errors[:30]), ms, run_id))
    else:
        cur = conn.execute("""INSERT INTO ma2_runs (started_at, mode, delivery, users, holdout_users, sent, holdout, suppressed, failed, summary_json, errors_json, ms, actor)
                              VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", (now.isoformat(), mode, delivery, len(users), holdout_users, sent, hold, supp, failed,
                                                                     json.dumps(summary, default=str), json.dumps(errors[:30]), ms, actor))
        run_id = cur.lastrowid
    conn.commit(); conn.close()
    if mode == "live":
        state_set_many({**updates, **sig_mod.ath_state_updates(ath_sent, state, now)})
        audit("ma2.run", {"run_id": run_id, "sent": sent, "holdout": hold, "failed": failed, "delivery": delivery}, actor=actor)
    return {"ok": True, "run_id": run_id, "mode": mode, "delivery": delivery, "users": len(users), "holdout_users": holdout_users,
            "sent" if mode == "live" else "would_send": sent, "holdout": hold, "suppressed": supp, "failed": failed, "ms": ms,
            "summary": summary, "errors": errors[:10]}


def scheduled_tick() -> Dict[str, Any]:
    """Refresher job: the per-user pilot and the discovery experiment run independently, each only while approved."""
    from . import discovery
    out: Dict[str, Any] = {"ok": True}
    st = pilot_live()
    out["pilot"] = {"skipped": st["why"]} if not st["live"] else run("live", actor="scheduler")
    out["discovery"] = discovery.scheduled_tick()
    return out


# ── views ─────────────────────────────────────────────────────────────────────
def runs(limit: int = 20) -> List[Dict[str, Any]]:
    init_tables(); conn = get_db()
    rows = [dict(r) for r in conn.execute("SELECT id, started_at, mode, delivery, users, holdout_users, sent, holdout, suppressed, failed, ms, actor FROM ma2_runs ORDER BY id DESC LIMIT ?", (limit,))]
    conn.close()
    return rows


def run_detail(run_id: int) -> Optional[Dict[str, Any]]:
    init_tables(); conn = get_db()
    r = conn.execute("SELECT * FROM ma2_runs WHERE id=?", (run_id,)).fetchone(); conn.close()
    if not r:
        return None
    d = dict(r)
    d["summary"] = json.loads(d.pop("summary_json") or "{}"); d["errors"] = json.loads(d.pop("errors_json") or "[]")
    return d


def today_distribution(now: Optional[datetime] = None) -> Dict[str, Any]:
    """Alerts per user today from the ledger — the proof that the 2 (+2 on breach) cap holds."""
    init_tables(); now = now or datetime.now(IST); day = now.strftime("%Y-%m-%d")
    conn = get_db()
    per = conn.execute(f"""SELECT uid_hash, SUM(CASE WHEN category IN ('relevance','discovery') THEN 1 ELSE 0 END) ma,
                           SUM(CASE WHEN status='holdout' THEN 1 ELSE 0 END) h FROM ma2_ledger WHERE day_ist=? AND status IN ('sent','recorded_mock','holdout') GROUP BY uid_hash""", (day,)).fetchall()
    themes = conn.execute("SELECT theme, slot, status, COUNT(*) n FROM ma2_ledger WHERE day_ist=? GROUP BY theme, slot, status", (day,)).fetchall()
    conn.close()
    dist = Counter(min(int(r["ma"] or 0), 5) for r in per)
    return {"day": day, "users_alerted": len(per), "per_user": {str(k): v for k, v in sorted(dist.items())}, "max_per_user": max((int(r["ma"] or 0) for r in per), default=0),
            "by_theme": [dict(r) for r in themes]}


def moengage_setup() -> List[Dict[str, Any]]:
    return [
        {"step": "Create the user events", "detail": "MA2_Alert, MOT_FuturesCrossSell and MOT_Referral arrive through the Data API with attributes: ma_category, ma_theme, ma_alert_type, ma_slot, token, product, direction, landing, move_pct, pnl_pct, price, level, z_score, title, body, template, run_id."},
        {"step": "One event-triggered push campaign per event", "detail": "Trigger on the event, send immediately. Title {{EventAttribute.title}}, message {{EventAttribute.body}}; route the deep link by landing (position_page, token_page, referral_page) with token and product."},
        {"step": "Switch MoEngage frequency capping off for these three campaigns", "detail": "BRD section 6: capping is enforced by the Market Alerts engine. Leaving MoEngage capping on double-caps and silently drops alerts."},
        {"step": "Keep DND and quiet hours consistent", "detail": "The engine already holds alerts 22:00–08:00 IST. Do not add a MoEngage send window that disagrees with it."},
        {"step": "Landing screens carry the disclaimer", "detail": "Push cannot hold the ASCI VDA disclaimer; the token, positions and futures education screens must."},
        {"step": "Tag the campaigns", "detail": "Name them MA2_Alert_Push, MOT_FuturesCrossSell_Push, MOT_Referral_Push so the engine's analysis links stats to this programme."},
    ]


def header_stats() -> List[Dict[str, Any]]:
    st = pilot_live(); m = cohort_mod.meta_only() or {}; t = today_distribution(); p = st["pilot"] or {}
    mock = get_setting("mock_mode", "true").lower() == "true"
    label, col = ("KILLED", "#ff5c9e") if kill_switch() else ("LIVE", "#4dffa8") if st["live"] else ("DRY RUN ONLY", "#ffb84d")
    return [{"k": "STATUS", "v": label, "sub": st["why"] or f"pilot {p.get('name', '')}", "c": col},
            {"k": "PILOT", "v": p.get("name") or "—", "sub": f"{p.get('holdout_pct')}% holdout · ends {str(p.get('ends_at', ''))[:10]}" if p else "propose one below", "c": "#eaf7fc"},
            {"k": "COHORT", "v": f"{(m.get('summary') or {}).get('users', 0):,}" if m else "—", "sub": f"{m.get('name')} · {int(m.get('age_min') or 0)} min old" if m else "no cohort uploaded", "c": "#6fe3ff"},
            {"k": "TODAY", "v": f"{t['users_alerted']:,}", "sub": f"users alerted · max {t['max_per_user']} per user", "c": "#ff5c9e" if t["max_per_user"] > 4 else "#eaf7fc"},
            {"k": "DELIVERY", "v": "MOCK" if mock else "MOENGAGE", "sub": "recorded, not sent" if mock else "Data API user events", "c": "#ffb84d" if mock else "#4dffa8"}]


def status() -> Dict[str, Any]:
    c = cohort_mod.load()
    st = pilot_live()
    last = runs(1)
    return {"live": st["live"], "why_not_live": st["why"], "pilot": st["pilot"], "kill_switch": kill_switch(), "mock_mode": get_setting("mock_mode", "true").lower() == "true",
            "cohort": (c or {}).get("meta"), "last_run": (run_detail(last[0]["id"]) if last else None), "today": today_distribution(),
            "copy": rules.lint_all(), "setup": moengage_setup(), "rules": rules.rules_view(), "runs": runs(12)}
