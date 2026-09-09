"""
Market → CLM campaign hooks.

A hook is a defensible chain: verifiable fact → relevance to a user cohort →
tool the user can act with → screen that delivers it. Each hook names the
lifecycle stages it fits, the channel, the copy angle, timing, TTL and the
guardrails. Angle policy per regime blocks inappropriate angles outright
(see knowledge/crypto-market-intelligence.md).
"""
from __future__ import annotations
from typing import Any, Dict, List

ANGLE_POLICY = {
    "capitulation":         {"block": ["leverage_upsell", "first_futures_trade", "size_up", "win_framing", "fomo", "breakout_fomo", "new_listing", "referral"],
                             "prefer": ["support_checkin", "risk_education", "long_term_education", "service_status"]},
    "high_volatility_down": {"block": ["leverage_upsell", "first_futures_trade", "size_up", "win_framing", "fomo", "breakout_fomo"],
                             "prefer": ["risk_education", "capital_preservation", "stablecoin_earn", "support_checkin", "alerts_adoption"]},
    "high_volatility_up":   {"block": ["size_up", "leverage_upsell"],
                             "prefer": ["alerts_adoption", "watchlist_adoption", "reactivation", "feature_discovery", "risk_education"]},
    "trending_down":        {"block": ["leverage_upsell", "win_framing", "fomo", "breakout_fomo"],
                             "prefer": ["risk_education", "dca_education", "earn_products", "portfolio_review"]},
    "trending_up":          {"block": [], "prefer": ["reactivation", "trend_following", "new_listing", "graduation", "feature_discovery"]},
    "chop":                 {"block": ["breakout_fomo", "trend_following", "fomo"],
                             "prefer": ["range_education", "fee_savings", "earn_products", "portfolio_review", "habit_tools"]},
    "unknown":              {"block": ["breakout_fomo", "trend_following", "fomo", "win_framing", "leverage_upsell"], "prefer": ["product_education", "feature_discovery"]},
}

COMPLIANCE = [
    "No forecasts, no implied returns, no buy/sell instruction on a named asset.",
    "Every price fact must be verifiable in-app at send time; set a TTL of 2–6 hours on market-linked pushes.",
    "Suppress loss-dormant users (recent liquidation / large realised loss) from all market-referencing sends.",
    "Frequency cap: max 1 market-linked push per user per day; none during capitulation except service messages.",
    "Regulatory or security headline in the last 24h → hold acquisition/upsell sends; service and safety only.",
]


def _mover_hooks(movers: List[Dict[str, Any]], asset_class: str, regime: str) -> List[Dict[str, Any]]:
    hooks = []
    for m in movers[:4]:
        chg = m.get("chg_24h")
        if chg is None or abs(chg) < 3:
            continue
        up = chg > 0
        hooks.append({
            "id": f"mover_{asset_class}_{m.get('symbol')}",
            "trigger": f"{m.get('name') or m.get('symbol')} {'up' if up else 'down'} {abs(chg):.1f}% in 24h",
            "asset_class": asset_class, "regime": regime,
            "segments": [f"Users with {m.get('symbol')} on watchlist or in portfolio", "Active traders (traded in last 14d)", "Alert-setters"],
            "clm_stages": ["Activated", "Habitual", "Core", "Slipping" if up else "Habitual"],
            "channel": "Push (in-app fallback)",
            "angle": "watchlist_adoption" if up else "risk_education",
            "copy_direction": (f"State the move as a fact the user can verify; link to the {m.get('symbol')} screen or their portfolio breakdown. "
                               "Offer a tool: set an alert / review positions. No prediction, no urgency tied to price."),
            "timing": "Within 1–3 hours of the move, IST daytime; TTL 4h", "guardrails": ["exclude loss-dormant", "1/day cap", "fact + tool only"],
            "kpi": "alert or watchlist adds per 1k delivered; session depth",
        })
    return hooks


def _regime_hooks(regime: str, fng: Dict[str, Any]) -> List[Dict[str, Any]]:
    table = {
        "trending_up": [("Reactivation while the market brings them back", "Dormant 30–90d (market-dormant only)", "reactivation", "Email + Push",
                         "Weekly recap of what moved; invite to review portfolio; no FOMO framing.", "Sunday evening / Monday morning IST"),
                        ("Graduation to depth", "Activated users with 1 product", "graduation", "In-app + Push",
                         "Introduce watchlists, alerts, recurring buys as habit tools while sessions are high.", "Post-move hours, weekdays")],
        "trending_down": [("Risk tools and DCA education", "Funded/Activated with unrealised loss", "risk_education", "Email",
                           "How stop-loss, alerts and staggered buying work. No market commentary beyond the fact.", "Morning IST, low frequency"),
                          ("Earn / stablecoin products", "Core & Habitual with idle balance", "earn_products", "In-app",
                           "Show idle balance and available earn options. Yield stated as current, not projected.", "Mid-week")],
        "chop": [("Fee savings and habit tools", "Habitual/Core", "fee_savings", "In-app + Email", "Fee tiers, limit orders, price alerts. Nothing market-referencing.", "Tue–Thu"),
                 ("Portfolio review", "Slipping (volume down 50% vs own baseline)", "portfolio_review", "Push", "Invite to a personal breakdown screen. Diagnostic, not persuasive.", "Weekend")],
        "high_volatility_down": [("Capital preservation check-in", "Users with open leveraged positions", "capital_preservation", "In-app + Push",
                                  "Explain margin health, liquidation levels, risk controls. Service tone.", "Immediately; TTL 2h"),
                                 ("Support availability", "All active users", "support_checkin", "In-app", "Response-time promise, status page, how to reach help.", "Once")],
        "high_volatility_up": [("Alerts adoption", "Active traders without alerts", "alerts_adoption", "Push", "Value is self-evident after a big move: offer an alert on their most-viewed asset.", "1–3h after move; TTL 4h")],
        "capitulation": [("Service and safety only", "All", "service_status", "In-app", "Platform status, withdrawals working, support hours. Everything else suppressed.", "As needed")],
    }
    out = []
    for title, seg, angle, ch, copy, when in table.get(regime, []):
        out.append({"id": f"regime_{regime}_{angle}", "trigger": f"Regime = {regime}" + (f"; Fear & Greed {fng.get('value')} ({fng.get('label')})" if fng else ""),
                    "asset_class": "crypto", "regime": regime, "segments": [seg], "clm_stages": [], "channel": ch, "angle": angle,
                    "copy_direction": copy, "timing": when, "guardrails": ["angle policy", "frequency cap", "suppress loss-dormant"], "kpi": "incremental sessions / product adoption vs holdout"})
    return out


def _calendar_hooks(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for e in events[:6]:
        if e.get("impact") != "High":
            continue
        out.append({"id": f"cal_{e.get('title','')[:20].replace(' ','_')}", "trigger": f"Upcoming high-impact event: {e.get('title')} ({e.get('country')}, {e.get('date')})",
                    "asset_class": "macro", "segments": ["Active traders", "Users holding rate-sensitive assets"], "channel": "Email (pre) + Push (post)", "angle": "product_education",
                    "copy_direction": "Pre-event: what the event is and how alerts/limit orders work. Post-event: the fact of the move only. Never a directional view.",
                    "timing": "Email 24h before; push within 2h after release; hold sends during the release window", "guardrails": ["no directional language", "TTL 3h"], "kpi": "alert adoption"})
    return out


def _news_hooks(news: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    risk = news.get("risk_flags") or []
    if risk:
        out.append({"id": "news_risk_hold", "trigger": f"{len(risk)} risk-flagged headlines in the last day (e.g. '{risk[0]['title'][:80]}')",
                    "asset_class": "all", "segments": ["All"], "channel": "—", "angle": "suppression",
                    "copy_direction": "HOLD acquisition/upsell/FOMO sends until the story is understood. If it concerns the platform or a listed asset, publish a service/safety message.",
                    "timing": "Now", "guardrails": ["suppression rule"], "kpi": "unsubscribe / uninstall rate stays flat"})
    return out


def _funding_hooks(md: Dict[str, Any], regime: str) -> List[Dict[str, Any]]:
    out = []
    for m in (md.get("crowded_long") or [])[:2]:
        out.append({"id": f"funding_long_{m['symbol']}", "trigger": f"{m['symbol']} funding {m['funding_apr_pct']:+.0f}% APR with ${(m.get('oi_usd') or 0)/1e6:.0f}M OI — crowded long",
                    "asset_class": "crypto", "regime": regime, "segments": [f"Users long {m['symbol']} perps", f"{m['symbol']} watchers with leverage history"], "clm_stages": ["Habitual", "Core"],
                    "channel": "In-app + Push", "angle": "risk_education", "copy_direction": "State the funding rate as a cost they are paying and what a squeeze does to leveraged positions; link to position/risk screen. No directional call.",
                    "timing": "Now; TTL 3h", "guardrails": ["no leverage upsell", "exclude loss-dormant", "1/day cap"], "kpi": "risk-tool opens; liquidation rate"})
    for m in (md.get("crowded_short") or [])[:1]:
        out.append({"id": f"funding_short_{m['symbol']}", "trigger": f"{m['symbol']} funding {m['funding_apr_pct']:+.0f}% APR — crowded short",
                    "asset_class": "crypto", "regime": regime, "segments": [f"Users short {m['symbol']}"], "clm_stages": ["Habitual", "Core"], "channel": "In-app",
                    "angle": "risk_education", "copy_direction": "Explain short-squeeze mechanics and the funding they receive/pay; link to risk tools.", "timing": "Now; TTL 3h",
                    "guardrails": ["no directional language", "1/day cap"], "kpi": "risk-tool opens"})
    return out


def _listing_hooks(listings: Dict[str, Any], regime: str) -> List[Dict[str, Any]]:
    out = []
    for n in (listings or {}).get("new") or []:
        rwa = n.get("asset_class") in ("equity", "index", "commodity", "fx")
        out.append({"id": f"listing_{n['venue']}_{n['symbol']}", "trigger": f"{n['symbol']} newly listed on {n['venue']} as {n['product']}", "asset_class": n.get("asset_class"), "regime": regime,
                    "segments": ([f"Watchers of {n['symbol']} or its sector", "Tokenised-market explorers"] if rwa else [f"Watchers of {n['symbol']}", "Habitual traders (8+ fills / 4 weeks)"]),
                    "clm_stages": ["Habitual", "Core"], "channel": "In-app card (push only to watchers)", "angle": "product_education" if rwa else "new_listing",
                    "copy_direction": f"State that {n['symbol']} is now tradable on CoinDCX ({n['product']}); offer watchlist add. Never name the liquidity venue. No launch-pump framing, no 'early' language, no price target.",
                    "timing": "Listing day, 10:00–20:00 IST; TTL 24h", "guardrails": ["blocked in stress regimes", "exclude liquidated-14d and loss-dormant", "1 listing message per user per week"],
                    "kpi": "watchlist adds per 1k delivered; first-week traders of the pair (holdout 20%)"})
    return out[:4]


def _oi_hooks(oi: Dict[str, Any], regime: str) -> List[Dict[str, Any]]:
    out = []
    for o in (oi or {}).get("surge") or []:
        out.append({"id": f"oi_surge_{o['symbol']}", "trigger": f"{o['symbol']} open interest {o['oi_chg_pct']:+.0f}% in ~24h with price {o['price_chg_pct']:+.1f}% — {o['reading']}", "asset_class": "crypto", "regime": regime,
                    "segments": [f"Users with an open {o['symbol']} perp", f"{o['symbol']} watchers with leverage history"], "clm_stages": ["Habitual", "Core"], "channel": "In-app (push to position holders)",
                    "angle": "risk_education", "copy_direction": "Quote OI and price change together, time-stamped; say crowded books move sharply; offer margin-buffer review / alert. No direction.",
                    "timing": "Within 2h; TTL 4h; not within 2h of a macro print", "guardrails": ["exclude liquidated-14d", "1/day cap", "no leverage encouragement"], "kpi": "risk-tool opens; positions reduced within 24h vs holdout"})
    for o in (oi or {}).get("drop") or []:
        out.append({"id": f"oi_drop_{o['symbol']}", "trigger": f"{o['symbol']} open interest {o['oi_chg_pct']:+.0f}% in ~24h (price {o['price_chg_pct']:+.1f}%) — leverage flushed", "asset_class": "crypto", "regime": regime,
                    "segments": [f"Traders of {o['symbol']} in the last 7d", "Liquidated in last 48h (service tone only)"], "clm_stages": ["Habitual", "Slipping"], "channel": "In-app",
                    "angle": "risk_education", "copy_direction": "Explain what an OI flush is in one line; link to the position history / risk tools. Service tone; no 're-enter' language.",
                    "timing": "Same day; TTL 6h", "guardrails": ["no promos to liquidated users", "no direction"], "kpi": "support contacts avoided; risk-tool opens"})
    return out[:4]


def build_hooks(ctx: Dict[str, Any]) -> Dict[str, Any]:
    regime = ((ctx.get("crypto") or {}).get("regime") or {}).get("label", "unknown")
    policy = ANGLE_POLICY.get(regime, ANGLE_POLICY["unknown"])
    hooks: List[Dict[str, Any]] = []
    hooks += _news_hooks(ctx.get("news") or {})
    hooks += _regime_hooks(regime, ctx.get("fear_greed") or {})
    hooks += _funding_hooks(ctx.get("crypto_movers_detail") or {}, regime)
    hooks += _mover_hooks(ctx.get("crypto_movers") or [], "crypto", regime)
    hooks += _mover_hooks(ctx.get("equity_movers") or [], "equities (HL perps)", regime)
    hooks += _mover_hooks(ctx.get("index_movers") or [], "indices (HL perps)", regime)
    hooks += _mover_hooks(ctx.get("commodity_movers") or [], "commodities (HL perps)", regime)
    hooks += _listing_hooks(ctx.get("listings") or {}, regime)
    hooks += _oi_hooks(ctx.get("oi_movers") or {}, regime)
    hooks += _calendar_hooks(ctx.get("calendar") or [])
    allowed = [h for h in hooks if h.get("angle") not in policy["block"]]
    blocked = [h["id"] for h in hooks if h.get("angle") in policy["block"]]
    return {"regime": regime, "angle_policy": policy, "compliance": COMPLIANCE, "hooks": allowed, "blocked_hook_ids": blocked}
