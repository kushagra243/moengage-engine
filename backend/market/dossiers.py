"""
Competitor dossiers — one marketing-grade profile per rival, assembled from
what the engine already tracks: market position (volumes by category, share,
24h/7d change, fees, traffic), app presence (rank, rating, version, release
notes), the campaigns detected in the last 7 days (types, cadence, channels,
audience focus), inferred playbook, latest pair moves, and the counters we
would run. Free public sources only; internal only.
"""
from __future__ import annotations
import re
from collections import Counter
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..database import get_db
from .campaign_intel import COUNTER, APPS
from .benchmarks import VENUES

ALIASES = {"delta-exchange": "delta", "coinbase-exchange": "coinbase", "koinbazar": "koinbx", "gate-io": "gate"}
AUDIENCE_WORDS = {"acquisition": r"welcome|new user|sign ?up|first (trade|deposit)|deposit (bonus|reward)|refer", "activation": r"trade to earn|competition|tournament|leaderboard|volume|challenge", "retention": r"\bvip\b|loyalty|tier|holder|staking|earn", "product": r"launch|introduc|now live|list|new feature|perpetual|options|stock"}
PLAYBOOK = {"trading_competition": "competition-led activation", "fee_promo": "price-led (fees)", "cashback_bonus": "bonus-led acquisition", "listing": "listing-velocity", "stock_perps": "TradFi expansion", "options": "options push", "product_launch": "product-velocity",
            "earn_apy": "yield-led retention", "airdrop": "airdrop/points hype", "referral": "referral loops", "festival_offer": "festival promotions", "learn_earn": "education-led", "vip_program": "VIP retention", "other": "mixed"}


def _campaigns_7d(venue: str) -> List[Dict[str, Any]]:
    conn = get_db()
    try:
        rows = [dict(r) for r in conn.execute("SELECT * FROM competitor_campaigns WHERE venue=? AND (published_at >= datetime('now','-7 days') OR first_seen >= datetime('now','-7 days')) ORDER BY COALESCE(published_at, first_seen) DESC LIMIT 60", (venue,)).fetchall()]
    except Exception:
        rows = []
    conn.close()
    return rows


def _app(venue: str) -> Dict[str, Any]:
    conn = get_db()
    try:
        cur = conn.execute("SELECT * FROM app_rank_snapshots WHERE app=? AND chart='top_free' ORDER BY day DESC LIMIT 1", (venue,)).fetchone()
        prev = conn.execute("SELECT * FROM app_rank_snapshots WHERE app=? AND chart='top_free' AND day <= date('now','-7 days') ORDER BY day DESC LIMIT 1", (venue,)).fetchone()
    except Exception:
        cur = prev = None
    conn.close()
    if not cur:
        return {}
    return {"rank_top_free": cur["rank"], "rating": cur["rating"], "ratings_count": cur["ratings_count"], "version": cur["version"], "rank_7d_ago": prev["rank"] if prev else None, "ratings_added_7d": (cur["ratings_count"] or 0) - (prev["ratings_count"] or 0) if prev else None}


def dossier(venue: str, intel: Optional[Dict[str, Any]] = None, bench: Optional[Dict[str, Any]] = None, campaigns_now: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    vid = ALIASES.get(venue, venue)
    meta = VENUES.get(vid) or {"name": vid.title(), "scope": "intl", "cats": []}
    intel = intel or {}; bench = bench or {}
    ex = next((t for t in (intel.get("exchanges") or []) if t["exchange"] == vid), {})
    rival = next((r for r in (intel.get("rivals") or []) if r.get("id") == vid), {})
    by_cat = {}
    for cat, c in (bench.get("categories") or {}).items():
        v = next((x for x in (c.get("venues") or []) if x["venue"] == vid), None)
        if v:
            by_cat[cat] = {"vol_24h_usd": v.get("vol_24h_usd"), "oi_usd": v.get("oi_usd"), "markets": v.get("markets"), "rank": 1 + [x["venue"] for x in c["venues"]].index(vid), "leader": (c.get("leader") or {}).get("name"), "top_pairs": v.get("top_pairs", [])[:5], "vol_7d_ago_usd": v.get("vol_7d_ago_usd")}
    camps = _campaigns_7d(vid)
    types = Counter(c["type"] for c in camps if c["type"] not in ("maintenance", "delisting"))
    channels = Counter((c.get("source") or "").split(" ·")[0] for c in camps)
    aud = Counter()
    for c in camps:
        t = f"{c.get('title', '')} {c.get('summary', '')}".lower()
        for k, rx in AUDIENCE_WORDS.items():
            if re.search(rx, t):
                aud[k] += 1
    top_types = [t for t, _ in types.most_common(2)]
    playbook = " + ".join(PLAYBOOK.get(t, t) for t in top_types) or "quiet week"
    counters = []
    for t in top_types:
        sop, how = COUNTER.get(t, COUNTER["other"])
        counters.append({"against": t, "sop": sop, "how": how})
    surges = [s for s in (intel.get("surges") or []) if s["competitor"] == vid][:5]
    gaps = [g for g in (intel.get("listing_gaps") or []) if g["competitor"] == vid][:5]
    cmc = {k: ex.get(k) for k in ("cmc_total_vol_24h_usd", "cmc_vol_chg_24h_pct", "cmc_vol_chg_7d_pct", "cmc_market_share_pct", "maker_fee_pct", "taker_fee_pct", "weekly_visits", "cmc_score") if ex.get(k) is not None}
    changes = []
    if cmc.get("cmc_vol_chg_7d_pct") is not None:
        changes.append(f"volume {float(cmc['cmc_vol_chg_7d_pct']):+.0f}% w/w (CMC)")
    app = _app(vid)
    if app.get("rank_7d_ago") and app.get("rank_top_free"):
        changes.append(f"App Store finance rank #{app['rank_7d_ago']} → #{app['rank_top_free']}")
    if app.get("ratings_added_7d"):
        changes.append(f"{app['ratings_added_7d']:+,} ratings in 7d")
    for cat, c in by_cat.items():
        if c.get("vol_7d_ago_usd") and c.get("vol_24h_usd"):
            changes.append(f"{cat} volume {((c['vol_24h_usd'] / c['vol_7d_ago_usd']) - 1) * 100:+.0f}% vs 7d ago")
    return {"id": vid, "name": meta.get("name") or ex.get("name") or vid, "scope": meta.get("scope"), "categories": meta.get("cats"), "reference": bool(meta.get("reference")), "us": bool(meta.get("us")),
            "pressure": {"index": rival.get("pressureIndex"), "threat": rival.get("threat"), "color": rival.get("color")}, "market": {"share_of_tracked_inr_spot_pct": ex.get("share_of_tracked_inr_spot_pct"), **cmc, "by_category": by_cat},
            "app": {**app, "name": APPS.get(vid, (None, None))[0]} if app or vid in APPS else {}, "campaigns_7d": {"count": len(camps), "by_type": dict(types.most_common()), "channels": dict(channels.most_common()), "audience_focus": dict(aud.most_common(3)), "cadence_per_day": round(len(camps) / 7, 1),
            "latest": [{"title": c["title"], "type": c["type"], "published_at": c.get("published_at"), "url": c.get("url"), "weight": c.get("weight")} for c in camps[:8]]},
            "playbook": playbook, "moves": {"surges": surges, "listing_gaps": gaps}, "what_changed_7d": changes, "counters": counters,
            "how_to_beat": _how_to_beat(vid, top_types, by_cat, ex, app)}


def _how_to_beat(vid: str, top_types: List[str], by_cat: Dict[str, Any], ex: Dict[str, Any], app: Dict[str, Any]) -> List[str]:
    tips = []
    if "cashback_bonus" in top_types or "fee_promo" in top_types:
        tips.append("They buy volume with price; we win the next 90 days with total-cost transparency, SIP habit loops and service — track deposit rate for 14 days, do not match bonuses.")
    if "trading_competition" in top_types:
        tips.append("Answer competitions with a volume-ranked (never P&L) challenge only when regime allows; otherwise asset spotlights on the same pairs to our watchers.")
    if "listing" in top_types or "stock_perps" in top_types:
        tips.append("Listing velocity is their story; ours is depth and 24/7 tokenised access — spotlight what we already list, file listing asks with evidence for the rest.")
    for cat, c in by_cat.items():
        if c.get("rank") and c["rank"] <= 3:
            tips.append(f"They rank #{c['rank']} in {cat}: pick their top pairs we list and run spotlights; compare our share pair by pair in the benchmarks.")
            break
    if app.get("rank_top_free") and app["rank_top_free"] < 100:
        tips.append(f"Strong app presence (finance #{app['rank_top_free']}): our release notes and ratings cadence must match — ask product for a monthly 'what's new' that marketing can amplify.")
    return tips[:4] or ["Quiet week for them: press our advantage with a product-cohort programme and keep the peace index in band."]


def all_dossiers() -> Dict[str, Any]:
    from .competitors import intel as _intel
    from .benchmarks import benchmarks as _bench
    from .. import brain
    try:
        it = brain.intel()
    except Exception:
        it = {}
    try:
        ci = _intel({"crypto_markets": []})
    except Exception:
        ci = {}
    try:
        bench = _bench()
    except Exception:
        bench = {}
    merged = {**ci, "rivals": it.get("rivals") or []}
    ids = [v for v in VENUES if not VENUES[v].get("us")]
    rows = [dossier(v, merged, bench) for v in ids]
    rows.sort(key=lambda d: -((d["pressure"] or {}).get("index") or 0))
    return {"dossiers": rows, "generated_at": datetime.utcnow().isoformat(), "note": "marketing view of each rival from free public sources; internal only — never named in copy"}
