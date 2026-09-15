"""
Per-user decisions for Market Alerts 2.0. Pure functions: no database, no network, so every BRD rule is testable.

build_candidates(user, signals, ledger, cfg)  → every alert this user qualifies for right now, with baseline/breach flags
decide(user, candidates, ledger, gates, cfg)  → which ones send, which are held back and exactly why

Ledger (what this user already received, from hashed-id rows):
  today:        {"relevance": {"baseline": n, "extra": n}, "discovery": {...}}
  milestone_today: n          pnl_last: {position_key: pnl_at_last_alert}
  cross_sell_recent: bool     referral_ever: bool
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional, Tuple

SEND, SUPPRESS, HOLDOUT = "send", "suppress", "holdout"


def _rank(order: List[str], value: str) -> int:
    try:
        return order.index(value)
    except ValueError:
        return len(order)


def _relation_rank(rel: str) -> int:
    return {"active": 0, "traded": 1, "watchlist": 1}.get(rel, 2)       # BRD: traded and watchlisted share P1


def relation(user: Dict[str, Any], token: str) -> str:
    if token in {p["token"] for p in user.get("positions") or []} or token in {h["token"] for h in user.get("spot_holdings") or []}:
        return "active"
    if token in set(user.get("traded") or []):
        return "traded"
    if token in set(user.get("watchlist") or []):
        return "watchlist"
    return "none"


def position_pnl(pos: Dict[str, Any], price: Optional[float]) -> Optional[float]:
    """The app's own PnL when the cohort file carries it; otherwise from entry, mark price, side and leverage."""
    if pos.get("pnl_pct") is not None:
        try:
            return float(pos["pnl_pct"])
        except (TypeError, ValueError):
            return None
    try:
        entry = float(pos.get("entry_price") or 0); px = float(price or 0)
    except (TypeError, ValueError):
        return None
    if entry <= 0 or px <= 0:
        return None
    side = -1.0 if str(pos.get("side") or "long").lower() in ("short", "sell") else 1.0
    lev = float(pos.get("leverage") or 1) or 1.0
    return round(side * (px / entry - 1.0) * 100.0 * lev, 2)


def _cand(category, theme, alert_type, token, product, direction, rel, strength, baseline_ok, breach_ok, fields, **extra) -> Dict[str, Any]:
    return {"category": category, "theme": theme, "alert_type": alert_type, "token": token, "product": product, "direction": direction,
            "relation": rel, "strength": float(strength or 0), "baseline_ok": bool(baseline_ok), "breach_ok": bool(breach_ok), "fields": fields, **extra}


def build_candidates(user: Dict[str, Any], sig: Dict[str, Any], ledger: Dict[str, Any], cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    products = set(user.get("products") or [])
    prices = sig.get("prices") or {}
    thr = float(cfg["z_threshold"]); breach_z = thr * float(cfg["z_breach_multiple"])

    # ── Relevance · PnL (futures positions) ──────────────────────────────────
    for pos in user.get("positions") or []:
        if pos.get("product") not in ("futures", "us_futures"):
            continue
        pnl = position_pnl(pos, prices.get(pos["token"]))
        if pnl is None:
            continue
        key = pos.get("key") or f"{pos['token']}|{pos['product']}|{pos.get('side', 'long')}"
        last = (ledger.get("pnl_last") or {}).get(key)
        moved = abs(pnl - last) if last is not None else None
        baseline_ok = abs(pnl) >= cfg["pnl_baseline_pct"] and (last is None or moved >= cfg["pnl_breach_pct"])
        breach_ok = last is not None and moved >= cfg["pnl_breach_pct"]
        if not (baseline_ok or breach_ok):
            continue                                            # under ±5%, or not 10 points past the last alert: not an alert at all
        out.append(_cand("relevance", "pnl", "position_pnl", pos["token"], pos["product"], "up" if pnl >= 0 else "down", "active", abs(pnl),
                         baseline_ok, breach_ok, {"token": pos["token"], "product": pos["product"], "pnl": pnl}, pos_key=key, value=pnl))

    # ── Relevance · price movement on relevant tokens ────────────────────────
    relevant = {p["token"] for p in user.get("positions") or []} | {h["token"] for h in user.get("spot_holdings") or []} | set(user.get("traded") or []) | set(user.get("watchlist") or [])
    moves = sig.get("moves") or {}
    for token in sorted(relevant):
        rel = relation(user, token)
        for product in products:
            m = moves.get(f"{token}|{product}")
            if not m or m.get("z") is None:
                continue
            z = float(m["z"])
            if abs(z) < thr:
                continue
            out.append(_cand("relevance", "price_movement", "z_move", token, product, "up" if (m.get("ret_pct") or 0) >= 0 else "down", rel, abs(z),
                             True, abs(z) >= breach_z, {"token": token, "product": product, "move": m.get("ret_pct"), "price": m.get("price"), "relation": rel}, value=z))

    listed = sig.get("listed") or {}

    def best_product(token: str, allowed: Tuple[str, ...]) -> Optional[str]:
        avail = [p for p in cfg["product_order"] if p in products and p in allowed and token in set(listed.get(p) or [])]
        return avail[0] if avail else None

    # ── Discovery · price trending: 1-year ATH/ATL ───────────────────────────
    discovery_products = ("futures", "us_futures", "options", "spot")
    if products & set(discovery_products):
        blocked_ath = set(sig.get("ath_week_blocked") or [])
        for token, kind in (sig.get("ath_atl") or {}).items():
            product = best_product(token, discovery_products)
            if not product:
                continue
            rel = relation(user, token)
            out.append(_cand("discovery", "price_trending", kind, token, product, "up" if kind == "ath" else "down", rel, 1.0, True, False,
                             {"token": token, "product": product, "price": prices.get(token), "relation": rel},
                             blocked=("ath_weekly_token_cap" if token in blocked_ath else None)))

        # ── Discovery · price trending: round-number milestones ──────────────
        first = sig.get("milestone_day_first") or {}
        for token, ms in (sig.get("milestones") or {}).items():
            product = best_product(token, discovery_products)
            if not product:
                continue
            rel = relation(user, token)
            band = float((cfg.get("milestone_breach") or {}).get(token) or 0)
            f = first.get(token)
            breach_ok = bool(f is not None and band and abs(float(ms["level"]) - float(f)) >= band and ledger.get("milestone_today", 0) < cfg["milestone_cap_per_day"])
            out.append(_cand("discovery", "price_trending", "milestone", token, product, ms.get("direction", "up"), rel, abs(float(ms["level"]) - float(f or ms["level"])),
                             ledger.get("milestone_today", 0) < cfg["milestone_cap_per_day"], breach_ok,
                             {"token": token, "product": product, "price": ms.get("price"), "level": ms["level"], "relation": rel}, value=ms["level"]))

    # ── Discovery · volume trending (Futures | Options audience) ─────────────
    vol_products = [p for p in ("futures", "us_futures", "options") if p in products]
    for w in sig.get("whales") or []:
        if w.get("product") in vol_products:
            out.append(_cand("discovery", "volume_trending", "whale", w["token"], w["product"], "buy" if str(w.get("side")).lower() == "buy" else "sell",
                             relation(user, w["token"]), float(w.get("size_usd") or 0), True, False,
                             {"token": w["token"], "product": w["product"], "size_usd": w.get("size_usd"), "side": w.get("side")}))
    for product in vol_products:
        token = (sig.get("most_traded") or {}).get(product)
        if token and token not in set((cfg.get("volume_exclude") or {}).get(product) or []):
            out.append(_cand("discovery", "volume_trending", "most_traded", token, product, "any", relation(user, token), 0.0, True, False,
                             {"token": token, "product": product}))

    # ── Moments of Truth · futures cross-sell (pure spot users) ──────────────
    pure_spot = products == {"spot"} and not user.get("futures_ever")
    if pure_spot and int(user.get("futures_screen_views_7d") or 0) >= cfg["cross_sell_min_futures_views_7d"]:
        chg = sig.get("chg_24h") or {}
        fl = set(sig.get("futures_listed") or [])
        held = [h for h in user.get("spot_holdings") or [] if h["token"] in fl and chg.get(h["token"]) is not None and abs(float(chg[h["token"]])) >= cfg["cross_sell_move_pct"]]
        if held:
            h = max(held, key=lambda x: float(x.get("auc_inr") or 0))                     # BRD: highest AUC wins
            mv = float(chg[h["token"]])
            out.append(_cand("mot", "cross_sell", "cross_sell", h["token"], "spot", "up" if mv >= 0 else "down", "active", float(h.get("auc_inr") or 0), True, False,
                             {"token": h["token"], "product": "spot", "move": mv}, blocked=("cross_sell_monthly_cap" if ledger.get("cross_sell_recent") else None)))

    # ── Moments of Truth · referral (realised futures first, then unrealised spot) ─
    trades = user.get("profitable_trades") or []
    ok = lambda t: float(t.get("profit_pct") or 0) >= cfg["referral_min_profit_pct"] and float(t.get("volume_inr") or 0) >= cfg["referral_min_volume_inr"]
    pick = next((t for t in trades if t.get("kind") == "realized_futures" and ok(t)), None) or next((t for t in trades if t.get("kind") == "unrealized_spot" and ok(t)), None)
    if pick:
        out.append(_cand("mot", "referral", "referral", pick.get("token", ""), "futures" if pick["kind"] == "realized_futures" else "spot", "any", "active", float(pick.get("profit_pct") or 0),
                         True, False, {"token": pick.get("token", ""), "product": "futures" if pick["kind"] == "realized_futures" else "spot"},
                         trigger=pick["kind"], blocked=("referral_lifetime_cap" if ledger.get("referral_ever") else None)))
    return out


def _sort_key(c: Dict[str, Any], cfg: Dict[str, Any]) -> Tuple:
    theme_idx = _rank((cfg["theme_order"] or {}).get(c["category"], []), c["theme"])
    prod = _rank(cfg["product_order"], c["product"])
    if c["theme"] == "volume_trending":
        return (theme_idx, 0 if c["alert_type"] == "whale" else 1, prod, -c["strength"])          # BRD 4.2: whale over most traded
    type_idx = {"ath": 0, "atl": 0, "milestone": 1}.get(c["alert_type"], 0)
    return (theme_idx, _relation_rank(c["relation"]), prod, type_idx, -c["strength"])


def gate_reason(c: Dict[str, Any], user: Dict[str, Any], gates: Dict[str, Any], cfg: Dict[str, Any]) -> Optional[str]:
    flags = user.get("flags") or {}
    if flags.get("push_disabled") or flags.get("dnd"):
        return "push_disabled"
    if gates.get("quiet"):
        return "quiet_hours"
    if c.get("blocked"):
        return c["blocked"]
    promo = c["category"] == "discovery" or c["theme"] == "cross_sell"
    if promo and gates.get("stress") and cfg.get("stress_suppress_discovery", True):
        return "stress_regime"
    if promo and flags.get("liquidated_14d"):
        return "liquidated_14d"
    if c["theme"] == "cross_sell" and str(user.get("country") or "").upper() in ("UK", "GB", "US"):
        return "jurisdiction"
    age = gates.get("exposure_age_min")
    limits = cfg.get("exposure_max_age_min") or {}
    if age is not None and age > float(limits.get("pnl" if c["theme"] == "pnl" else "other", 1440)):
        return "stale_exposure"
    return None


def decide(user: Dict[str, Any], candidates: List[Dict[str, Any]], ledger: Dict[str, Any], gates: Dict[str, Any], cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    decisions: List[Dict[str, Any]] = []
    eligible: List[Dict[str, Any]] = []
    for c in candidates:
        why = gate_reason(c, user, gates, cfg)
        if why:
            decisions.append({**c, "decision": SUPPRESS, "slot": None, "reason": why})
        else:
            eligible.append(c)

    for cat in ("relevance", "discovery"):
        pool = sorted([c for c in eligible if c["category"] == cat], key=lambda c: _sort_key(c, cfg))
        if not pool:
            continue
        st = (ledger.get("today") or {}).get(cat) or {}
        if int(st.get("baseline", 0)) < cfg["category_baseline_cap"]:
            slot, qualifying = "baseline", [c for c in pool if c["baseline_ok"]]
            miss = "below_threshold"
        elif int(st.get("extra", 0)) < cfg["category_extra_cap"]:
            slot, qualifying = "extra", [c for c in pool if c["breach_ok"]]
            miss = "no_breach"                                                          # BRD 6: a second alert needs a breach
        else:
            slot, qualifying, miss = None, [], "category_cap"
        chosen = qualifying[0] if qualifying else None
        for c in pool:
            if c is chosen:
                decisions.append({**c, "decision": SEND, "slot": slot, "reason": None})
            else:
                decisions.append({**c, "decision": SUPPRESS, "slot": None,
                                  "reason": "lower_priority" if (chosen and (c["baseline_ok"] if slot == "baseline" else c["breach_ok"])) else miss})

    for theme in ("cross_sell", "referral"):                                           # outside the MA cap, one of each at most
        pool = sorted([c for c in eligible if c["theme"] == theme], key=lambda c: -c["strength"])
        for i, c in enumerate(pool):
            decisions.append({**c, "decision": SEND if i == 0 else SUPPRESS, "slot": "exempt" if i == 0 else None, "reason": None if i == 0 else "lower_priority"})

    if user.get("holdout"):
        decisions = [{**d, "decision": HOLDOUT, "reason": "holdout"} if d["decision"] == SEND else d for d in decisions]
    return decisions


def apply_to_ledger(ledger: Dict[str, Any], decisions: List[Dict[str, Any]]) -> Dict[str, Any]:
    """What the ledger looks like after these decisions — holdout consumes caps exactly like treatment."""
    lg = {"today": {k: dict(v) for k, v in (ledger.get("today") or {}).items()}, "milestone_today": ledger.get("milestone_today", 0),
          "pnl_last": dict(ledger.get("pnl_last") or {}), "cross_sell_recent": ledger.get("cross_sell_recent", False), "referral_ever": ledger.get("referral_ever", False)}
    for d in decisions:
        if d["decision"] not in (SEND, HOLDOUT):
            continue
        if d["category"] in ("relevance", "discovery"):
            lg["today"].setdefault(d["category"], {"baseline": 0, "extra": 0})
            lg["today"][d["category"]][d["slot"]] = lg["today"][d["category"]].get(d["slot"], 0) + 1
        if d["alert_type"] == "milestone":
            lg["milestone_today"] += 1
        if d["theme"] == "pnl":
            lg["pnl_last"][d["pos_key"]] = d["value"]
        if d["theme"] == "cross_sell":
            lg["cross_sell_recent"] = True
        if d["theme"] == "referral":
            lg["referral_ever"] = True
    return lg


def empty_ledger() -> Dict[str, Any]:
    return {"today": {"relevance": {"baseline": 0, "extra": 0}, "discovery": {"baseline": 0, "extra": 0}}, "milestone_today": 0, "pnl_last": {},
            "cross_sell_recent": False, "referral_ever": False}
