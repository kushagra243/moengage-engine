"""
Sentiment pushes for ATPU: read the market's mood, turn it into compliant nudges per product, spread them over every
channel, and measure one number — average trades per active user — with portfolio breadth as the second.

  read()     the mood from data the engine already has: Fear & Greed, breadth, funding bias, regime, market-cap change,
             dominance shift. Five labels: fearful, cautious, neutral, constructive, euphoric. Evidence with every read.
  nudges()   per product (spot, SIP, crypto perps, tokenised stocks/indices/commodities, options, earn, web3) the one
             angle that fits the mood — education and tools in fear, breadth and discipline in euphoria, never a
             direction, never a price target, never a venue or competitor name, never a leverage lure. Every line is
             linted with the alert linter before it is shown.
  plan()     the channel plan for a nudge: push (the fact, ≤ 60/140), in-app (the tool), email (the education),
             WhatsApp (utility templates only, 10:00–21:00 IST), Telegram broadcast (the community post), with the
             comms limits that bind. Nothing here sends: proposals do, through approvals.
  breadth()  portfolio breadth — how many products a cohort touches — as the diversification lens, from the cohort
             families the engine already decodes, without any per-user data.
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional

from .database import get_setting

PRODUCTS = ["spot", "sip", "crypto_perps", "tokenised_stocks", "indices_commodities", "options", "earn", "web3"]
LABELS = ["fearful", "cautious", "neutral", "constructive", "euphoric"]
NORTH_STAR_ATPU = ("Average trades per active user (ATPU) per week, lifted versus holdout across every product, with portfolio breadth "
                   "(products traded per user, 30d) as the second number and unsubscribe/opt-out as the guardrail — every channel, sentiment-timed, never a price call.")

# (label) → per product: angle, push title, push body, in-app tool, email theme, whatsapp utility, telegram post
NUDGES: Dict[str, Dict[str, Dict[str, str]]] = {
    "fearful": {
        "spot": {"angle": "calm and tools", "title": "Markets are nervous today", "body": "Fear is high. Set a price alert on what you hold and let the market come to you.", "tool": "price alert", "email": "How fear readings have behaved historically, and what a plan looks like", "whatsapp": "Your price alerts and portfolio summary", "telegram": "Fear reading is elevated. Alerts, not impulses: set levels on what you hold."},
        "sip": {"angle": "habit over timing", "title": "A nervous market, a steady plan", "body": "Recurring buys keep working when timing feels impossible. Review your SIP schedule.", "tool": "SIP schedule", "email": "Why rupee-cost averaging is built for weeks like this", "whatsapp": "Your next SIP date and amount", "telegram": "Nervous week. Recurring buyers do not need to guess the bottom; the schedule does the work."},
        "crypto_perps": {"angle": "risk first", "title": "Volatility is up", "body": "Check margin, liquidation levels and open positions before the next move. Education, not entries.", "tool": "liquidation calculator", "email": "Managing risk in high-volatility weeks: margin, stops, position size", "whatsapp": "Margin and liquidation summary for your open positions", "telegram": "Volatility regime: know your liquidation levels before the market tests them."},
        "tokenised_stocks": {"angle": "diversification", "title": "US stocks trade 24/7 here", "body": "When crypto is fearful, tokenised US stocks and indices offer a different rhythm. Learn how they work.", "tool": "tokenised markets explainer", "email": "Tokenised US stocks: hours, settlement, funding, what moves them", "whatsapp": "Tokenised markets guide", "telegram": "Crypto fearful? Tokenised US stocks and indices trade around the clock. Here is how they work."},
        "indices_commodities": {"angle": "macro hedge education", "title": "Gold and indices, tokenised", "body": "Learn how index and commodity perps behave when crypto sells off. Education only.", "tool": "macro calendar", "email": "How indices and commodities have moved in crypto drawdowns", "whatsapp": "Macro calendar for the week", "telegram": "Risk-off weeks: a look at how gold and index perps have behaved. Not a call."},
        "options": {"angle": "defined risk", "title": "Options: risk you can define", "body": "In volatile weeks, learn how buying options caps your downside. Education only.", "tool": "options explainer", "email": "Defined-risk basics: premium, expiry, what you can lose", "whatsapp": "Options basics guide", "telegram": "Volatile week: options let you define the maximum you can lose. Learn the basics."},
        "earn": {"angle": "park and wait", "title": "Waiting on the sidelines?", "body": "Idle balances can earn while you wait. See the current terms.", "tool": "earn terms", "email": "Earn products: terms, lock-ups, what to check", "whatsapp": "Your earn positions and terms", "telegram": "Sitting out? Read the earn terms before parking balances; know the lock-ups."},
        "web3": {"angle": "safety", "title": "Stay safe in the wallet", "body": "Fear brings scams. Verify contracts and never sign what you do not understand.", "tool": "wallet safety checklist", "email": "Wallet safety in volatile weeks", "whatsapp": "Wallet safety checklist", "telegram": "Fear brings phishing. Verify every contract; unverified tokens are unverified."},
    },
    "euphoric": {
        "spot": {"angle": "discipline", "title": "Greed is high", "body": "Strong weeks reward a plan. Set take-profit alerts and review position sizes.", "tool": "price alert", "email": "What greed readings have preceded, and how a plan protects gains", "whatsapp": "Your alerts and portfolio summary", "telegram": "Greed reading is high. Plans beat impulses: set levels and sizes now."},
        "sip": {"angle": "keep the habit", "title": "Do not skip the plan", "body": "Euphoria tempts lump sums. Your recurring plan already has a rhythm; keep it.", "tool": "SIP schedule", "email": "Why SIPs should not be paused in strong weeks", "whatsapp": "Your next SIP date", "telegram": "Strong week. Recurring plans are built to keep buying through both moods."},
        "crypto_perps": {"angle": "crowded positioning", "title": "Funding is crowded", "body": "When everyone leans one way, funding costs rise. Check your funding and leverage. Education only.", "tool": "funding tracker", "email": "Crowded funding: what it costs and how it unwinds", "whatsapp": "Funding summary for your open positions", "telegram": "Crowded funding means the trade is expensive to hold. Check what you pay every 8 hours."},
        "tokenised_stocks": {"angle": "breadth", "title": "Widen the view", "body": "Crypto is not the only market moving. See tokenised US stocks and indices, 24/7.", "tool": "tokenised markets", "email": "Diversifying beyond crypto with tokenised stocks and indices", "whatsapp": "Tokenised markets guide", "telegram": "One market running hot is a reason to look at the others. Tokenised stocks and indices, 24/7."},
        "indices_commodities": {"angle": "breadth", "title": "Indices and gold, tokenised", "body": "Learn how index and commodity perps diversify a crypto-heavy book. Education only.", "tool": "macro calendar", "email": "Index and commodity perps: how they differ from crypto", "whatsapp": "Macro calendar for the week", "telegram": "Diversification note: index and commodity perps move to a different calendar."},
        "options": {"angle": "protection", "title": "Protecting gains with options", "body": "Learn how protective puts work when a run has been good. Education only.", "tool": "options explainer", "email": "Protective strategies explained: what they cost, what they cover", "whatsapp": "Options basics guide", "telegram": "After a strong run, learn how protection with options works. Education, not advice."},
        "earn": {"angle": "take some off", "title": "Gains sitting idle?", "body": "Balances you do not plan to trade can earn. See the current terms.", "tool": "earn terms", "email": "Earn products for balances you are not trading", "whatsapp": "Your earn positions", "telegram": "Idle gains can earn; read the terms and lock-ups first."},
        "web3": {"angle": "verify", "title": "Trending is not verified", "body": "Trending tokens attract copies and scams. Check the contract before you act.", "tool": "token checker", "email": "Trending tokens: how to verify before you touch them", "whatsapp": "Wallet safety checklist", "telegram": "Everything trending is unverified until you check the contract."},
    },
}
# cautious/neutral/constructive reuse the two poles with softer framing
NUDGES["cautious"] = {k: dict(v, title=v["title"].replace("Markets are nervous today", "A cautious week").replace("Volatility is up", "Volatility is picking up")) for k, v in NUDGES["fearful"].items()}
NUDGES["constructive"] = {k: dict(v, title=v["title"].replace("Greed is high", "Momentum is building")) for k, v in NUDGES["euphoric"].items()}
NUDGES["neutral"] = {
    "spot": {"angle": "tools", "title": "Quiet market, good time to set alerts", "body": "Set price alerts on your watchlist so you hear about the next move first.", "tool": "price alert", "email": "Using alerts and watchlists well", "whatsapp": "Your watchlist summary", "telegram": "Quiet tape. Set your alerts now; the next move rarely announces itself."},
    "sip": NUDGES["fearful"]["sip"], "crypto_perps": {"angle": "learn", "title": "Learn perps in a calm week", "body": "Calm weeks are the time to learn funding, margin and liquidation. Education only.", "tool": "perps explainer", "email": "Perps basics: funding, margin, liquidation", "whatsapp": "Perps basics guide", "telegram": "Calm week: the best time to learn how funding and liquidation actually work."},
    "tokenised_stocks": NUDGES["euphoric"]["tokenised_stocks"], "indices_commodities": NUDGES["euphoric"]["indices_commodities"], "options": NUDGES["fearful"]["options"], "earn": NUDGES["euphoric"]["earn"], "web3": NUDGES["fearful"]["web3"],
}
CHANNEL_ROLES = {"push": "the fact, ≤ 60 / 140 characters, one tool CTA", "in_app": "the tool itself, on the next open", "email": "the education, with the ASCI disclaimer in the footer",
                 "whatsapp": "utility template only (alerts, summaries), 10:00–21:00 IST, DLT-registered", "telegram_broadcast": "the community post: fact + why + tool, no CTA to buy, no direction"}
GEO = {"crypto_perps": "not to UK residents; US persons geo-fenced; India education-only", "options": "not to UK or US; India education-only", "tokenised_stocks": "US persons geo-fenced", "indices_commodities": "US persons geo-fenced"}


def read(ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The mood, from what the engine already knows. Score −2…+2, label, evidence lines. Never raises."""
    if ctx is None:
        try:
            from .market.context import market_context
            ctx = market_context()
        except Exception:
            ctx = {}
    fg = (ctx.get("fear_greed") or {}); reg = ((ctx.get("crypto") or {}).get("regime") or {}); cg = ctx.get("crypto_global") or {}
    score = 0.0; ev: List[str] = []
    try:
        v = float(fg.get("value"))
        score += (v - 50) / 25.0; ev.append(f"Fear & Greed {int(v)} ({fg.get('classification') or fg.get('label') or ''})".strip())
    except (TypeError, ValueError):
        pass
    try:
        b = float(reg.get("breadth_up_pct"))
        score += (b - 50) / 50.0; ev.append(f"{int(b)}% of tracked markets up on the day")
    except (TypeError, ValueError):
        pass
    fb = str(reg.get("funding_bias") or "").lower()
    if "long" in fb or "positive" in fb:
        score += 0.4; ev.append("funding leans long (crowded longs)")
    elif "short" in fb or "negative" in fb:
        score -= 0.4; ev.append("funding leans short")
    lab = str(reg.get("label") or "").lower()
    if lab in ("stress", "risk_off", "capitulation", "selloff"):
        score -= 0.8; ev.append(f"regime {lab}")
    elif lab in ("risk_on", "trend_up", "breakout", "expansion"):
        score += 0.6; ev.append(f"regime {lab}")
    try:
        m = float(cg.get("mcap_chg_24h"))
        score += max(-1.0, min(1.0, m / 5.0)); ev.append(f"market cap {m:+.1f}% in 24h")
    except (TypeError, ValueError):
        pass
    score = max(-2.0, min(2.0, score))
    label = "fearful" if score <= -1.2 else "cautious" if score <= -0.4 else "neutral" if score < 0.4 else "constructive" if score < 1.2 else "euphoric"
    return {"label": label, "score": round(score, 2), "evidence": ev or ["no market data yet; treated as neutral"], "stress": lab in ("stress", "risk_off", "capitulation", "selloff"),
            "generated_at": ctx.get("generated_at"), "note": "a read of the market's mood from public data; it times the tone of a nudge and never becomes a price call"}


def _lint(title: str, body: str) -> List[str]:
    try:
        from .alerts2 import rules
        return [x["rule"] for x in rules.lint(title, body) if x["severity"] in ("high", "medium")]
    except Exception:
        return []


def nudges(mood: Optional[Dict[str, Any]] = None, products: Optional[List[str]] = None) -> Dict[str, Any]:
    mood = mood or read()
    table = NUDGES.get(mood["label"]) or NUDGES["neutral"]
    out = []
    for prod in products or PRODUCTS:
        n = table.get(prod)
        if not n:
            continue
        bad = _lint(n["title"], n["body"])
        out.append({"product": prod, "angle": n["angle"], "push": {"title": n["title"], "body": n["body"], "ok": not bad, "lint": bad}, "in_app": n["tool"], "email": n["email"], "whatsapp": n["whatsapp"], "telegram": n["telegram"],
                    "geo": GEO.get(prod, "India first; ASCI VDA disclaimer where the channel can carry it"),
                    "paused": mood["stress"] and prod in ("crypto_perps", "options", "web3"), "why": f"{mood['label']} mood → {n['angle']}"})
    return {"mood": mood, "nudges": out, "channels": CHANNEL_ROLES, "north_star": get_setting("north_star", "") or NORTH_STAR_ATPU,
            "rules": ["one nudge per product per week across all channels combined", "cross-sell only on shown intent (viewed, watchlisted, asked)", "stress regime: service tone only for perps, options and web3",
                      "never a direction, a price target, a venue or competitor name, a leverage multiple, or a hypothetical return", "quiet hours 22:00–08:00 IST; WhatsApp 10:00–21:00 only"]}


def plan(product: str, mood: Optional[Dict[str, Any]] = None, cohort: str = "") -> Dict[str, Any]:
    """The multi-channel plan for one product's nudge, ready to become proposals (push/email drafts, Telegram post, WhatsApp utility)."""
    n = next((x for x in nudges(mood, [product])["nudges"]), None)
    if not n:
        return {"error": f"unknown product {product}; one of {PRODUCTS}"}
    steps = [{"day": 0, "channel": "push", "what": n["push"], "gate": "you approve"},
             {"day": 0, "channel": "in_app", "what": {"tool": n["in_app"]}, "gate": "dashboard (no API for in-app drafts)"},
             {"day": 0, "channel": "telegram_broadcast", "what": {"text": n["telegram"]}, "gate": "you approve"},
             {"day": 2, "channel": "email", "what": {"theme": n["email"]}, "gate": "you approve"},
             {"day": 3, "channel": "whatsapp", "what": {"utility": n["whatsapp"]}, "gate": "utility template registered; Inform"}]
    return {"product": product, "cohort": cohort or f"{product}_intent_or_active_30d", "mood": (mood or n and read())["label"] if mood else read()["label"], "angle": n["angle"], "steps": steps, "paused": n["paused"], "geo": n["geo"],
            "measure": {"primary_kpi": "trades_per_active_user", "secondary": "products_traded_30d", "guardrail_metric": "push_opt_out_rate", "control_group_pct": 20, "measurement_window_days": 14,
                        "kill_criteria": ["push_opt_out_rate > 0.5%", "complaint_rate > 0.1%"]}}


def breadth() -> Dict[str, Any]:
    """Portfolio breadth from cohort families: which product each family touches, so the diversification lens needs no per-user data."""
    try:
        from . import segments
        fams = segments.families() if hasattr(segments, "families") else []
    except Exception:
        fams = []
    touch: Dict[str, int] = {p: 0 for p in PRODUCTS}
    for f in fams or []:
        name = str(f.get("family") or f.get("name") or "").lower()
        for p, keys in {"spot": ("spot", "hvt", "ltv"), "sip": ("sip", "recurring"), "crypto_perps": ("perp", "fut", "lev"), "tokenised_stocks": ("stock", "equit", "us_"), "indices_commodities": ("index", "gold", "commod"), "options": ("opt",), "earn": ("earn", "stake"), "web3": ("web3", "wallet", "dex")}.items():
            if any(k in name for k in keys):
                touch[p] += 1
    single = [p for p, n in touch.items() if n == 0]
    return {"families_by_product": touch, "products_without_a_cohort": single, "lens": "a user who trades one product is the diversification opportunity; the next product is the one they have shown intent for, never a random cross-sell",
            "next_step": "ask for product-view and watchlist events per product (data request) so breadth can be measured per user inside MoEngage"}
