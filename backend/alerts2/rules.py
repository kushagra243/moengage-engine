"""
Market Alerts 2.0 — the BRD encoded as data.

Every number, order and cap from the BRD lives in DEFAULTS so the team can see it and change it (setting
ma2_config_json, merged over the defaults). Where the BRD is silent or ambiguous the choice is written down in
ASSUMPTIONS and shown in the terminal, so nobody mistakes an engine decision for a BRD requirement.
"""
from __future__ import annotations
import copy
import json
import re
from typing import Any, Dict, List, Optional

PRODUCTS = ("futures", "us_futures", "options", "spot")

DEFAULTS: Dict[str, Any] = {
    # ── Relevance (P1) ────────────────────────────────────────────────────────
    "pnl_baseline_pct": 5.0,            # BRD 3.1: ±5% token-level position PnL
    "pnl_breach_pct": 10.0,             # BRD 3.1: a further 10% move from the last alert sent
    "z_threshold": 2.0,                 # BRD 3.2: "standard threshold" (value not given in the BRD)
    "z_breach_multiple": 2.0,           # BRD 3.2: breach when Z exceeds 2x the standard threshold
    "candle": {"futures": "1h", "us_futures": "1h", "options": "1h", "spot": "1d"},   # BRD 3.2: F&O hourly, spot daily
    "futures_fast_variant": False,      # BRD 3.2: a 5-minute variant is defined for futures/options
    "z_window": {"1h": 168, "5m": 288, "1d": 90},      # returns in the rolling baseline
    # ── Discovery (P2) ────────────────────────────────────────────────────────
    "ath_window_days": 365,             # BRD 4.1: 1-year ATH/ATL
    "ath_cap_per_token_per_week": 2,    # BRD 4.1: ATH/ATL capped at 2 per token per week
    "milestone_bands": {"BTC": 1000, "ETH": 200},      # BRD 4.1
    "milestone_breach": {"BTC": 2000, "ETH": 400},     # BRD 4.1 gives BTC $2,000; ETH $400 is an assumption (2x its band)
    "milestone_cap_per_day": 2,         # BRD 4.1
    "volume_exclude": {"futures": ["BTC", "ETH", "SOL"], "spot": ["BTC", "ETH", "SOL"], "options": [], "us_futures": []},  # BRD 4.2
    # ── Moments of Truth (outside the MA cap) ─────────────────────────────────
    "cross_sell_move_pct": 5.0,         # BRD 5.1 says ±x%; 5 matches the BRD's own example copy
    "cross_sell_window_days": 30,       # BRD 5.1: 1/user/month
    "cross_sell_min_futures_views_7d": 1,
    "referral_min_profit_pct": 10.0,    # BRD 5.2
    "referral_min_volume_inr": 1000.0,  # BRD 5.2
    # ── Governance ────────────────────────────────────────────────────────────
    "category_baseline_cap": 1,         # BRD 6: one baseline per category per day
    "category_extra_cap": 1,            # BRD 6: one breach-triggered extra per category per day
    "product_order": ["futures", "us_futures", "options", "spot"],    # BRD: Futures > Options > Spot
    "relation_order": ["active", "traded", "watchlist", "none"],      # BRD: Active token/position first, then traded/watchlisted
    "theme_order": {"relevance": ["pnl", "price_movement"], "discovery": ["price_trending", "volume_trending"]},
    # ── Engine guardrails (ours, not the BRD's) ───────────────────────────────
    "holdout_pct": 20,
    "quiet_start": "22:00", "quiet_end": "08:00",   # IST; push outside 08:00–22:00 is DND for Indian users
    "stress_suppress_discovery": True,             # doctrine: stress regime → service messages only
    "exposure_max_age_min": {"pnl": 60, "other": 1440},
    "max_sends_per_run": 500,
    "max_tokens_per_run": 80,
    "run_every_min": 15,
    "event_platform": "web",
}

ASSUMPTIONS: List[Dict[str, str]] = [
    {"topic": "Z-score standard threshold", "choice": "2.0 standard deviations; breach at 4.0", "why": "the BRD defines the breach as 2x the standard threshold but does not give the standard value"},
    {"topic": "Z-score definition", "choice": "latest candle return against the mean and deviation of the previous window (168 hourly, 288 five-minute or 90 daily returns)", "why": "the BRD names Z-score deviation on price without a window"},
    {"topic": "PnL breach", "choice": "the position's PnL has moved 10 percentage points or more since the last alert on that same position, on any day", "why": "BRD 3.1 says 'an additional 10% move from the last alert sent'"},
    {"topic": "Order inside a category", "choice": "Relevance: PnL before Price Movement. Discovery: Price Trending before Volume Trending", "why": "the BRD caps the category at one baseline but does not rank its themes; table order is used"},
    {"topic": "ETH milestone breach", "choice": "a further $400 beyond the first ETH milestone of the day", "why": "the BRD gives the $2,000 breach band for BTC only"},
    {"topic": "ATH/ATL weekly cap", "choice": "a token's ATH/ATL alert can go out on at most 2 days in an ISO week across the cohort", "why": "BRD 4.1 caps 'per token per week' without saying per user"},
    {"topic": "Volume Trending audience", "choice": "Futures and Options users; token exclusions still follow the per-product rule", "why": "BRD 4.2 lists the audience as Futures | Options while its token rule also mentions Spot"},
    {"topic": "US Futures", "choice": "treated as a futures product, ranked right after crypto futures", "why": "the BRD scopes US Futures in the objective and volume rule but not in the product order"},
    {"topic": "Cross-sell move", "choice": "±5% 24-hour move on the held token; the highest-AUC token wins", "why": "BRD 5.1 leaves x unset; its example copy uses 5%"},
    {"topic": "One alert per category per run", "choice": "a run never sends a baseline and an extra in the same category at once", "why": "the extra is defined as a further move after an alert; two pushes seconds apart would read as spam"},
    {"topic": "Quiet hours and stress", "choice": "no alerts 22:00–08:00 IST; in capitulation or high-volatility-down regimes Discovery and Cross-sell pause, Relevance continues", "why": "engine doctrine (DND window, service-only in stress); the BRD is silent"},
    {"topic": "Holdout", "choice": "20% of the pilot cohort, chosen by a stable hash, decided and capped exactly like treatment but never sent", "why": "without a holdout the pilot cannot show whether alerts change trading"},
]

CATEGORIES = {
    "relevance": {"label": "Relevance", "priority": "P1", "anchor": True},
    "discovery": {"label": "Discovery", "priority": "P2", "anchor": False},
    "mot": {"label": "Moments of Truth", "priority": "exempt", "anchor": False},
}

THEMES: Dict[str, Dict[str, Any]] = {
    "pnl": {"category": "relevance", "label": "PnL (position-level gain/loss)", "brd": "3.1", "audience": ["futures", "us_futures"],
            "landing": "position_page", "event": "MA2_Alert", "extra": "further 10% PnL move from the last alert"},
    "price_movement": {"category": "relevance", "label": "Price movement (relevant tokens)", "brd": "3.2", "audience": ["futures", "us_futures", "options", "spot"],
                       "landing": "token_page", "event": "MA2_Alert", "extra": "Z-score beyond 2x the threshold"},
    "price_trending": {"category": "discovery", "label": "Price trending (ATH/ATL & milestones)", "brd": "4.1", "audience": ["futures", "us_futures", "options", "spot"],
                       "landing": "token_page", "event": "MA2_Alert", "extra": "each further $2,000 BTC band, max 2 milestone alerts/day"},
    "volume_trending": {"category": "discovery", "label": "Volume trending (most traded + whale)", "brd": "4.2", "audience": ["futures", "us_futures", "options"],
                        "landing": "token_page", "event": "MA2_Alert", "extra": None},
    "cross_sell": {"category": "mot", "label": "Futures cross-sell", "brd": "5.1", "audience": ["spot"], "landing": "token_page", "event": "MOT_FuturesCrossSell", "extra": None},
    "referral": {"category": "mot", "label": "Referral moment of truth", "brd": "5.2", "audience": ["futures", "us_futures", "options", "spot"], "landing": "referral_page", "event": "MOT_Referral", "extra": None},
}

THEME_TEMPLATES = {"pnl": ("pnl:",), "price_movement": ("price_movement:",), "price_trending": ("ath:", "atl:", "milestone:"),
                   "volume_trending": ("most_traded:", "whale:"), "cross_sell": ("cross_sell:",), "referral": ("referral:",)}

# Copy: fact + relevance + a tool. The content team finalises wording (BRD: "TBD"); every template is linted before a pilot can go live.
DEFAULT_TEMPLATES: Dict[str, Dict[str, str]] = {
    "pnl:up": {"title": "{token} position update", "body": "Your {token} {product_label} position is at {pnl_signed}% unrealised PnL. Review it in Positions."},
    "pnl:down": {"title": "{token} position update", "body": "Your {token} {product_label} position is at {pnl_signed}% unrealised PnL. Check margin and stop-loss in Positions."},
    "price_movement:up": {"title": "{token} is up {move_abs}%", "body": "An unusual move for {token}, which you {relation_phrase}. See the chart or set a price alert."},
    "price_movement:down": {"title": "{token} is down {move_abs}%", "body": "An unusual move for {token}, which you {relation_phrase}. See the chart or set a price alert."},
    "ath:up": {"title": "{token} at a 1-year high", "body": "{token} is at {price}, its highest level in a year. See the chart."},
    "atl:down": {"title": "{token} at a 1-year low", "body": "{token} is at {price}, its lowest level in a year. See the chart."},
    "milestone:up": {"title": "{token} crossed {level}", "body": "{token} is trading at {price} after crossing {level}. See the chart or set an alert."},
    "milestone:down": {"title": "{token} fell below {level}", "body": "{token} is trading at {price} after dropping below {level}. See the chart or set an alert."},
    "most_traded:any": {"title": "Most traded today: {token}", "body": "{token} leads global {product_label} volume today. See the market."},
    "whale:buy": {"title": "Large buying in {token}", "body": "A large buy of about {size} was recorded in {token}. See the market."},
    "whale:sell": {"title": "Large selling in {token}", "body": "A large sell of about {size} was recorded in {token}. See the market."},
    "cross_sell:up": {"title": "Your {token} holding is up {move_abs}%", "body": "Futures can be used to trade or hedge {token} price moves. Learn how it works and the risks first."},
    "cross_sell:down": {"title": "Your {token} holding is down {move_abs}%", "body": "Learn how futures can be used to hedge a spot holding, and the risks involved."},
    "referral:any": {"title": "Know someone who trades?", "body": "Invite them to CoinDCX from your referral page."},
}

BRD_REFERENCE_COPY = {
    "cross_sell:up": "Your BTC position is up by 5%. You could have made 25% Profit at 5x leverage on Futures on this movement. Start trading in BTC and maximize your upside!",
    "cross_sell:down": "Your BTC position is down by 5%. Hedge your position with futures and cap your downside.",
}

# Stricter than the live-campaign sweep: alerts fire automatically, so nothing reads them before they send.
_EXTRA_RULES = [
    ("hypothetical_returns", "high", re.compile(r"\b(could have made|would have made|you could have earned|missed (out|opportunity)|\d+(\.\d+)?%\s*profit)\b", re.I),
     "never show returns a user did not get; hypothetical profit is a performance claim"),
    ("leverage_multiple", "high", re.compile(r"\b\d{1,3}(\.\d+)?x\b(\s*leverage)?|\bleverage of \d", re.I), "a leverage multiple in a push is a lure, even a single digit"),
    ("implied_safety", "high", re.compile(r"\b(cap your downside|protect your (portfolio|position|money)|limit your losses|no risk|risk[- ]free|safe(ly)? (hedge|bet))\b", re.I),
     "futures do not cap downside; hedging carries its own risk"),
    ("upside_hype", "medium", re.compile(r"\b(maximi[sz]e your (upside|profits?|gains?)|big gains|huge profits?)\b", re.I), "no hype about gains"),
    ("trade_urgency", "medium", re.compile(r"\b(start trading now|trade now|act fast|right now)\b", re.I), "alerts inform; they do not push a trade"),
]


def config() -> Dict[str, Any]:
    from ..database import get_setting
    cfg = copy.deepcopy(DEFAULTS)
    try:
        over = json.loads(get_setting("ma2_config_json", "") or "{}")
        for k, v in (over or {}).items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k].update(v)
            elif k in cfg:
                cfg[k] = v
    except Exception:
        pass
    if cfg.get("futures_fast_variant"):
        cfg["candle"].update({"futures": "5m", "us_futures": "5m", "options": "5m"})
    return cfg


def templates() -> Dict[str, Dict[str, str]]:
    from ..database import get_setting
    out = copy.deepcopy(DEFAULT_TEMPLATES)
    try:
        for k, v in (json.loads(get_setting("ma2_templates_json", "") or "{}") or {}).items():
            if k in out and isinstance(v, dict):
                out[k].update({kk: str(vv) for kk, vv in v.items() if kk in ("title", "body")})
    except Exception:
        pass
    return out


def lint(title: str, body: str) -> List[Dict[str, str]]:
    """Every rule the live-campaign sweep uses plus the stricter alert rules. High severity blocks a pilot."""
    from ..compliance_sweep import _rules
    text = f"{title} {body}"
    out: List[Dict[str, str]] = []
    for rule, sev, rx, fix in list(_rules()) + _EXTRA_RULES:
        m = rx.search(text)
        if m:
            out.append({"rule": rule, "severity": sev, "snippet": m.group(0), "fix": fix})
    if len(title) > 60 or len(body) > 140:
        out.append({"rule": "push_length", "severity": "low", "snippet": f"title {len(title)} / body {len(body)} chars", "fix": "push title ≤ 60, body ≤ 140"})
    return out


def lint_all(tpls: Optional[Dict[str, Dict[str, str]]] = None) -> Dict[str, Any]:
    tpls = tpls or templates()
    rows = []
    for key, t in tpls.items():
        sample = render(key, _SAMPLE, t)
        findings = lint(sample["title"], sample["body"])
        rows.append({"key": key, "title": t["title"], "body": t["body"], "sample": sample, "findings": findings,
                     "blocks": any(f["severity"] == "high" for f in findings)})
    brd = [{"key": k, "text": v, "findings": lint("", v)} for k, v in BRD_REFERENCE_COPY.items()]
    return {"templates": rows, "blocking": [r["key"] for r in rows if r["blocks"]], "brd_reference": brd}


_SAMPLE = {"token": "BTC", "product": "futures", "pnl": 6.2, "move": 4.1, "price": 77650.0, "level": 78000, "size_usd": 2_400_000, "relation": "traded", "side": "buy"}
PRODUCT_LABEL = {"futures": "Futures", "us_futures": "US Futures", "options": "Options", "spot": "Spot"}
RELATION_PHRASE = {"active": "hold", "traded": "have traded", "watchlist": "watch", "none": "follow"}


def _money(v: Any) -> str:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return str(v)
    if x >= 1_000_000:
        return f"${x / 1_000_000:.1f}M"
    if x >= 1_000:
        return f"${x:,.0f}"
    return f"${x:,.4g}"


def render(key: str, fields: Dict[str, Any], tpl: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    tpl = tpl or templates().get(key) or {"title": "{token}", "body": "{token}"}
    pnl = fields.get("pnl"); move = fields.get("move")
    vals = {
        "token": fields.get("token", ""), "product_label": PRODUCT_LABEL.get(str(fields.get("product")), str(fields.get("product") or "")),
        "pnl_signed": (f"{pnl:+.1f}" if isinstance(pnl, (int, float)) else ""), "move_abs": (f"{abs(move):.1f}" if isinstance(move, (int, float)) else ""),
        "price": _money(fields.get("price")) if fields.get("price") is not None else "", "level": _money(fields.get("level")) if fields.get("level") is not None else "",
        "size": _money(fields.get("size_usd")) if fields.get("size_usd") is not None else "", "relation_phrase": RELATION_PHRASE.get(str(fields.get("relation")), "follow"),
    }

    class _D(dict):
        def __missing__(self, k):
            return ""
    return {"title": tpl["title"].format_map(_D(vals)).strip(), "body": tpl["body"].format_map(_D(vals)).strip()}


def template_key(theme: str, alert_type: str, direction: str) -> str:
    if theme == "pnl":
        return f"pnl:{'up' if direction == 'up' else 'down'}"
    if theme == "price_movement":
        return f"price_movement:{'up' if direction == 'up' else 'down'}"
    if alert_type in ("ath", "atl"):
        return f"{alert_type}:{'up' if alert_type == 'ath' else 'down'}"
    if alert_type == "milestone":
        return f"milestone:{'up' if direction == 'up' else 'down'}"
    if alert_type == "most_traded":
        return "most_traded:any"
    if alert_type == "whale":
        return f"whale:{'buy' if direction in ('up', 'buy') else 'sell'}"
    if theme == "cross_sell":
        return f"cross_sell:{'up' if direction == 'up' else 'down'}"
    return "referral:any"


def rules_view() -> Dict[str, Any]:
    cfg = config()
    return {"config": cfg, "assumptions": ASSUMPTIONS, "categories": CATEGORIES, "themes": THEMES,
            "caps": [
                "Up to 2 alerts per user per day: one Relevance (P1), one Discovery (P2).",
                "Each category can add one breach-triggered extra, so 4 per day only when both breach on the same day.",
                "A second alert in a category fires only on its breach criteria; baselines never stack.",
                "Relevance is the anchor: with no breach and no Discovery trigger, one Relevance alert is the day.",
                "Futures cross-sell (1/user/month) and Referral (1/user/lifetime) sit outside the cap.",
                "Capping is enforced here, not in MoEngage: switch MoEngage frequency capping off for these campaigns.",
            ]}
