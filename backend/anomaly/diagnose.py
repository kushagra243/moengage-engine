"""
Deep campaign / anomaly diagnosis on top of the daily snapshots.

For a campaign it computes trend statistics (7d vs 28d, weekday-normalised,
streaks, volatility), decomposes the funnel (sent → delivered → opened →
converted → revenue) to find WHICH stage moved, ranks likely causes with the
evidence available, and returns practical options: what to do, how to do it
in MoEngage, effort, expected effect, risk, and what to check first. Pure
functions over stored data; no model involved, so it runs on every refresh.
"""
from __future__ import annotations
import statistics
from datetime import datetime
from typing import Any, Dict, List, Optional

from .store import get_history, list_tracked_campaigns

STAGES = [("sent_count", "sent"), ("delivery_rate", "delivered %"), ("ctr", "opened/clicked %"), ("conversion_rate", "converted %"), ("revenue_generated", "revenue")]


def _mean(xs: List[float]) -> Optional[float]:
    xs = [x for x in xs if x is not None]
    return statistics.fmean(xs) if xs else None


def _pct(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or b in (None, 0):
        return None
    return round((a - b) / abs(b) * 100, 1)


def _streak(vals: List[Optional[float]]) -> int:
    """Consecutive most-recent days moving in the same direction (negative = falling)."""
    v = [x for x in vals if x is not None]
    if len(v) < 3:
        return 0
    direction = 0; count = 0
    for i in range(len(v) - 1, 0, -1):
        step = v[i] - v[i - 1]
        if step == 0:
            break
        s = 1 if step > 0 else -1
        if direction == 0:
            direction = s
        elif s != direction:
            break
        count += 1
    return direction * count


def trend_stats(hist: List[Dict[str, Any]], metric: str) -> Dict[str, Any]:
    vals = [r.get(metric) for r in hist]
    today = next((v for v in reversed(vals) if v is not None), None)
    prior = [r for r in hist[:-1]]
    last7 = [r.get(metric) for r in prior[-7:]]
    last28 = [r.get(metric) for r in prior[-28:]]
    dow = datetime.fromisoformat(hist[-1]["snapshot_date"]).weekday() if hist else None
    same_dow = [r.get(metric) for r in prior if datetime.fromisoformat(r["snapshot_date"]).weekday() == dow] if dow is not None else []
    clean7 = [x for x in last7 if x is not None]
    return {
        "today": today, "mean_7d": _mean(last7), "mean_28d": _mean(last28), "mean_same_weekday": _mean(same_dow),
        "vs_7d_pct": _pct(today, _mean(last7)), "vs_28d_pct": _pct(today, _mean(last28)), "vs_same_weekday_pct": _pct(today, _mean(same_dow)),
        "volatility_7d_pct": round(statistics.pstdev(clean7) / statistics.fmean(clean7) * 100, 1) if len(clean7) >= 3 and statistics.fmean(clean7) else None,
        "streak_days": _streak(vals[-10:]), "min_28d": min([x for x in last28 if x is not None], default=None), "max_28d": max([x for x in last28 if x is not None], default=None),
    }


def _funnel_decomposition(stats: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Which stage moved most vs its 28d mean (sign-aware, in %)."""
    moves = {}
    for m, label in STAGES:
        s = stats.get(m) or {}
        if s.get("vs_28d_pct") is not None:
            moves[m] = {"label": label, "change_pct": s["vs_28d_pct"]}
    if not moves:
        return {"moved_most": None, "moves": {}}
    worst = min(moves.items(), key=lambda kv: kv[1]["change_pct"])
    best = max(moves.items(), key=lambda kv: kv[1]["change_pct"])
    return {"moved_most": worst[0] if abs(worst[1]["change_pct"]) >= abs(best[1]["change_pct"]) else best[0], "moves": moves}


def _causes_and_options(camp: Dict[str, Any], stats: Dict[str, Dict[str, Any]], decomp: Dict[str, Any]) -> Dict[str, Any]:
    ch = str(camp.get("channel", "")).lower()
    d = lambda m: (stats.get(m) or {}).get("vs_28d_pct")
    deliv, ctr, conv, rev, sent = d("delivery_rate"), d("ctr"), d("conversion_rate"), d("revenue_generated"), d("sent_count")
    causes: List[Dict[str, Any]] = []
    options: List[Dict[str, Any]] = []

    def cause(title, evidence, confidence, check):
        causes.append({"cause": title, "evidence": evidence, "confidence": confidence, "check_first": check})

    def opt(action, how, effort, effect, risk, when="now"):
        options.append({"action": action, "how_in_moengage": how, "effort": effort, "expected_effect": effect, "risk": risk, "when": when})

    # 1) deliverability
    if deliv is not None and deliv <= -3:
        cause("Deliverability degradation", f"delivery rate {deliv:+.1f}% vs 28d mean while sends {sent if sent is not None else 'n/a'}%",
              "high" if deliv <= -8 else "medium",
              "Push: invalid/expired token share, iOS opt-out changes, recent app release. Email: bounce/complaint rate, domain reputation, list source changes.")
        opt("Pause and clean the audience before the next send", "Campaign → Pause. Segment: exclude users with push_token_invalid or last_seen > 60d; email: exclude hard bounces. Re-enable with a 10% canary.",
            "low", "Delivery back to baseline within one send; protects sender reputation", "Lose one cycle of reach", "today")
        opt("Add a delivery fallback branch", "Flow: send push → wait 3h → if not delivered → in-app card (or email) → exit on any engagement.",
            "medium", "+10–20% effective reach on the segment", "Message volume up for undelivered users", "this week")
    # 2) engagement (CTR) with delivery stable
    if ctr is not None and ctr <= -10 and (deliv is None or deliv > -3):
        cause("Creative fatigue or relevance loss", f"CTR {ctr:+.1f}% vs 28d with delivery stable; streak {stats['ctr'].get('streak_days')} days",
              "high" if (stats["ctr"].get("streak_days") or 0) <= -3 else "medium",
              "Was the same creative/offer used > 3 sends? Did audience broaden (lower intent)? Did send time collide with market hours or another campaign?")
        opt("Rotate creative with an A/B on the hook", "Duplicate campaign → 2 variants (fact-led vs benefit-led title), 50/50, 10% control; keep body ≤ 140 chars, one CTA.",
            "low", "+15–30% CTR recovery if fatigue; null result if relevance", "None beyond test cost", "next send")
        opt("Tighten the audience to intent signals", "Segment: add 'viewed asset/product in last 7d' or 'has watchlist'; exclude users with 0 opens in last 5 sends.",
            "low", "CTR up, reach down; net conversions usually up", "Smaller reach", "next send")
        opt("Switch to Best Time to Send (non-triggered only)", "Campaign → Delivery → Best time to send with fallback hour; compare against fixed-slot holdout.",
            "low", "+5–12% opens", "Not for triggered sends", "this week")
    if ctr is not None and ctr >= 15 and (conv is None or conv < 5):
        cause("Curiosity click without conversion (or click-bot/tracking anomaly)", f"CTR {ctr:+.1f}% while conversions {conv if conv is not None else 'n/a'}%",
              "medium", "Check click source distribution and landing screen; verify no tracking change or bot traffic.")
        opt("Align landing with the promise", "Deep-link to the exact asset/screen referenced; add the number from the push on the landing.", "low", "Conversion catches up with CTR", "None", "next send")
    # 3) conversion with engagement stable
    if conv is not None and conv <= -15 and (ctr is None or ctr > -5):
        cause("Post-click friction or offer/product change", f"conversion {conv:+.1f}% with CTR stable", "medium",
              "Landing screen errors, KYC/payment friction spikes, price/spread change, market regime shift (users hesitate in drawdowns).")
        opt("Instrument the landing funnel and fix the drop step", "Compare screen-view → action rates for clickers this week vs 4 weeks ago; if a deposit/trade step regressed, route to product/support.",
            "medium", "Restores conversion; usually the largest single lever", "Needs product help", "this week")
        opt("Regime-aware copy", "If regime is trending_down/high-vol: switch the CTA from 'trade' to 'review portfolio / set an alert'.", "low", "Higher completion, no pressure sells", "None", "next send")
    # 4) revenue vs conversions
    if rev is not None and conv is not None and (rev - conv) >= 25:
        cause("Order value / whale effect", f"revenue {rev:+.1f}% but conversions {conv:+.1f}%", "medium", "Check top-10 orders share; if one user drives it, treat as noise.")
        opt("Do not scale on this signal", "Keep audience; add revenue guardrail metric = median order value, not total.", "low", "Avoids over-investing in noise", "None", "now")
    if rev is not None and conv is not None and (conv - rev) >= 25:
        cause("Smaller baskets / lower notional per convert", f"conversions {conv:+.1f}% but revenue {rev:+.1f}%", "medium", "Fee tier change, smaller first trades, promo-driven users.")
        opt("Segment by value tier and message depth, not volume", "Split converts by notional; move low-value converts into the education/graduation flow.", "medium", "AOV recovery over 2–4 weeks", "None", "this week")
    # 5) volume shift
    if sent is not None and abs(sent) >= 25:
        cause("Audience size shift", f"sends {sent:+.1f}% vs 28d", "high", "Segment definition edited? Upstream event volume change? Frequency cap or DND eating sends?")
        opt("Freeze the audience definition and version it", "Duplicate the segment with a date suffix; compare reach estimate to last week; check FC/DND exclusions in campaign analytics.", "low", "Restores comparability", "None", "today")
    # 6) positive outlier
    if (ctr or 0) >= 20 and (conv or 0) >= 10:
        cause("Genuine lift (creative or context)", f"CTR {ctr:+.1f}% and conversion {conv:+.1f}%", "medium", "Was there a market move or event? Reproduce the conditions, then scale.")
        opt("Scale with a lookalike and a holdout", "Widen one criterion; 20% holdout; keep creative; measure over 7 days.", "low", "+20–40% of the lift, sustained", "Dilution if widened too fast", "this week")
    if not causes:
        cause("No dominant driver", "Metrics within normal variation relative to their 28-day means", "low", "Watch for a 3-day streak; nothing to change yet.")
        opt("Hold", "No change; keep the daily snapshot running.", "low", "—", "None", "—")
    # generic guardrails
    options.append({"action": "Read against the holdout, not last week", "how_in_moengage": "Campaign analytics → control group comparison; Wilson interval before declaring a change.", "effort": "low", "expected_effect": "Avoids acting on noise", "risk": "None", "when": "always"})
    prio = {"high": 0, "medium": 1, "low": 2}
    causes.sort(key=lambda c: prio.get(c["confidence"], 3))
    return {"likely_causes": causes[:4], "options": options[:6]}


def diagnose_campaign(campaign_id: str, source: str, days: int = 35) -> Dict[str, Any]:
    hist = get_history(campaign_id, source, days)
    if not hist:
        return {"campaign_id": campaign_id, "error": "no history"}
    camp = hist[-1]
    stats = {m: trend_stats(hist, m) for m, _ in STAGES}
    stats["opened_count"] = trend_stats(hist, "opened_count"); stats["conversions"] = trend_stats(hist, "conversions")
    decomp = _funnel_decomposition(stats)
    co = _causes_and_options(camp, stats, decomp)
    sev = "critical" if any(c["confidence"] == "high" for c in co["likely_causes"]) else ("watch" if co["likely_causes"] and co["likely_causes"][0]["cause"] != "No dominant driver" else "normal")
    moved = decomp.get("moved_most")
    headline = (f"{camp.get('campaign_name')}: {STAGES_LABEL.get(moved, moved)} moved {decomp['moves'][moved]['change_pct']:+.1f}% vs 28d — {co['likely_causes'][0]['cause']}" if moved and decomp["moves"] else f"{camp.get('campaign_name')}: within normal variation")
    return {"campaign_id": campaign_id, "campaign_name": camp.get("campaign_name"), "channel": camp.get("channel"), "days": len(hist), "severity": sev,
            "headline": headline, "funnel": decomp, "stats": stats, **co}


STAGES_LABEL = {m: l for m, l in STAGES}


def diagnose_all(source: str, limit: int = 20) -> List[Dict[str, Any]]:
    out = []
    for t in list_tracked_campaigns(source)[:limit]:
        try:
            out.append(diagnose_campaign(t["campaign_id"], source))
        except Exception as e:
            out.append({"campaign_id": t["campaign_id"], "error": str(e)})
    rank = {"critical": 0, "watch": 1, "normal": 2}
    out.sort(key=lambda d: rank.get(d.get("severity", "normal"), 3))
    return out
