"""
Growth Hacks: a curated, sourced library of tactics that are working in
crypto / fintech lifecycle marketing right now, ranked for THIS workspace by
what the data says (regime, coverage gaps, channel mix, anomalies), plus
model-suggested additions on the bulk tier that are clearly labelled as
unverified until a person marks them.

Each hack: what it is, why it works, who does it (source), how to run it in
MoEngage, prerequisites, KPI, effort, regime fit, and a relevance score for
today. Statuses mirror the growth feed: new / saved / dismissed / proposed.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .database import get_db, get_setting
from .security import redact

LIBRARY: List[Dict[str, Any]] = [
    dict(id="price_move_alerts", title="Per-user price-move alerts (5% / 10% tiers)", category="engagement",
         what="Push when an asset the user holds or watches moves ≥5% (second tier 10%), capped per asset per day.",
         why="Relevance is the whole game: Robinhood auto-enrols holders, caps 1/hour and 3/day per asset, and lets users mute for a week. It is the highest-CTR message class in trading apps.",
         source="Robinhood support: crypto price alerts; CleverTap fintech push benchmarks", source_url="https://robinhood.com/us/en/support/articles/crypto-price-alerts/",
         how="Emit asset_moved_5pct {symbol,pct,direction} per holder/watcher via Data API → Event-triggered campaign → push immediately; Ignore FC on, Count for FC on; TTL 4h; DND respected; fact + tool copy.",
         prereqs=["watchlist/holdings events in MoEngage", "price-watch job (this engine can emit it)"], kpi="alert CTR; watchlist adds; 7d retention of alert users", effort="medium",
         regimes=["trending_up", "high_volatility_up", "high_volatility_down", "chop"], channels=["push"]),
    dict(id="funding_rate_education", title="Funding-rate 'you are paying to stay long' nudges", category="risk",
         what="When funding on a user's open perp is extreme, tell them what it costs per day and link to the position screen.",
         why="Crowded positioning precedes violent reversals; users who understand funding size positions better and churn less after squeezes.",
         source="Exchange-native data (Hyperliquid funding/OI); risk-nudge pattern from Zerodha Kite", source_url="https://zerodha.com/z-connect/updates/kite-nudge-to-warn-you-about-portfolio-concentration",
         how="Daily job: users with open positions where |funding APR| > 20% → in-app nudge + optional push; no directional language; 1/day cap.",
         prereqs=["position events or attributes"], kpi="risk-tool opens; liquidation rate of nudged vs holdout", effort="medium", regimes=["all"], channels=["in-app", "push"]),
    dict(id="watchlist_recap_cards", title="Cards inbox 'Your watchlist today' instead of push", category="engagement",
         what="A daily persistent card with the user's watchlist moves; push only when their own asset moved > 5%.",
         why="Recaps do not need interruption. Cards persist, are measurable, and cut push fatigue.",
         source="MoEngage Cards + Content APIs docs", source_url="https://www.moengage.com/docs/user-guide/campaigns-and-channels/getting-started/campaign-content/content-apis",
         how="Content API serves /content/quote?symbols=… (cached); Cards campaign renders it at open; push branch only on threshold.", prereqs=["Cards enabled", "Content API endpoint"], kpi="card click rate; push volume −", effort="medium", regimes=["all"], channels=["cards"]),
    dict(id="market_stress_mode", title="Market-stress mode via Business Event", category="risk",
         what="On regime flip to high-vol-down/capitulation, fire one Business Event that pauses acquisition/upsell, tightens FC to 1/day, and sends a calm explainer.",
         why="The most expensive send in crypto is the tone-deaf one during a crash. Business Events fire attached campaigns/flows in one call (≤200/day).",
         source="MoEngage Business Events; crisis-comms practice", source_url="https://www.moengage.com/docs/api/business-events/business-events-overview",
         how="Register business event market_stress; attach: pause promos (status API), in-app explainer, FC tightening; trigger from this engine's regime detector (debounce 30 min).",
         prereqs=["Campaigns key", "regime detector (built in)"], kpi="uninstall & unsubscribe flat through the event", effort="medium", regimes=["high_volatility_down", "capitulation"], channels=["in-app", "email"]),
    dict(id="first_trade_flow", title="Funded → first trade Flow with one concrete action", category="activation",
         what="Entry on first deposit; 24h wait; if no trade, in-app 'one first action'; 48h; education email; exit on trade; 20% holdout.",
         why="Funded-no-trade is a knowledge gap, not an intent gap; the second trade inside 7 days is the retention cliff.",
         source="CoinDCX × MoEngage case study (KYC +10%, bank verification +95%)", source_url="https://www.moengage.com/casestudy/coindcx-user-path-analysis-case-study/",
         how="Flows → trigger first_deposit → wait → condition split on trade_executed → in-app → wait → email → exit; Message Queuing on; control group 20%.",
         prereqs=["deposit & trade events"], kpi="first_trade_rate_7d", effort="medium", regimes=["all"], channels=["in-app", "email"]),
    dict(id="dormancy_by_cause", title="Dormancy split by cause (market / loss / friction / competitor / life)", category="retention",
         what="Five winback segments, each with its own message or silence; loss- and friction-dormant get no market or upsell content.",
         why="'Dormant 30 days' is a bucket, not a segment. Reason-specific winback beats generic by multiples and cuts unsubscribes.",
         source="CLM playbook (knowledge/clm-lifecycle.md)", source_url="", how="Segments on last_liquidation_at, realised_loss_90d, failed_deposit/withdrawal, support_ticket_open, balance withdrawn; separate campaigns; suppression lists.",
         prereqs=["those events/attributes"], kpi="reactivation_rate_14d by cause; unsubscribe rate", effort="medium", regimes=["all"], channels=["email", "push"]),
    dict(id="bts_weekly_recap", title="Best Time to Send on the weekly recap", category="engagement",
         what="Let MoEngage pick each user's hour for the Sunday/Monday recap; measure against a fixed-slot holdout.",
         why="BTS learns from 60 days of engagement per user; it is the cheapest lift available for non-triggered sends.",
         source="MoEngage Best Time to Send docs", source_url="https://www.moengage.com/docs/user-guide/ai-and-intelligence/timing/best-time-to-send",
         how="Campaign → Delivery → Best time to send (fallback 19:00 IST); 10% control at fixed time.", prereqs=[], kpi="open rate", effort="low", regimes=["all"], channels=["email", "push"]),
    dict(id="coin_of_the_day", title="'Trending today' educational card on exchange-listed movers", category="acquisition",
         what="Daily card for alt traders excluding current holders: 3 verifiable facts about a top mover, link to asset page, risk disclaimer.",
         why="Listed movers are the moment users are curious; educational framing keeps it compliant; rotation prevents fatigue.",
         source="Exchange listings (Binance/Hyperliquid) + ASCI/FCA compliance rules", source_url="",
         how="This engine's mover list → Content API → Cards/in-app; 1/day; no repeat asset in 7d; suppress on risk headlines and in capitulation.",
         prereqs=["Content API"], kpi="asset page views per 1k; watchlist adds", effort="low", regimes=["trending_up", "chop", "high_volatility_up"], channels=["cards", "in-app"]),
    dict(id="macro_event_briefs", title="Pre/post briefs around CPI, FOMC, RBI", category="engagement",
         what="Email T−1 explaining the event and how alerts/limit orders work; quiet window ±60 min; factual push within 2h after.",
         why="Volatility windows drive sessions and support load; prepared users trade calmer and set alerts.",
         source="ForexFactory calendar (built in); MoEngage Content API", source_url="", how="Calendar → periodic campaign T−1 with BTS; event-time quiet rule via FC; post-event triggered push with TTL 3h.",
         prereqs=[], kpi="alert adoption; support tickets −", effort="low", regimes=["all"], channels=["email", "push"]),
    dict(id="global_control_group", title="Global control group + per-campaign holdouts", category="measurement",
         what="5% global holdout plus ≥5% per campaign; read every result against it with Wilson intervals.",
         why="Without a holdout every uplift is the market's uplift; roughly a third of 'winning' campaigns are noise.",
         source="MoEngage global control group; Brown, Cai & DasGupta 2001", source_url="", how="Settings → Global control group 5%; brief checker enforces per-campaign holdout.",
         prereqs=[], kpi="incremental lift", effort="low", regimes=["all"], channels=["all"]),
    dict(id="whatsapp_fallback", title="WhatsApp utility fallback for undelivered high-value push", category="deliverability",
         what="Flow: push → wait 3h → if not delivered and value tier high → WhatsApp utility template.",
         why="A third of users have push disabled; the high-value slice is worth a second channel.",
         source="MoEngage Flows branching on delivery", source_url="", how="Flow with 'not delivered' condition; utility template pre-approved; cap 1/week.",
         prereqs=["WhatsApp BSP connected"], kpi="effective reach on high-value segment", effort="medium", regimes=["all"], channels=["whatsapp"]),
    dict(id="referral_off_in_stress", title="Referral and joining-bonus copy switched off automatically in stress regimes", category="compliance",
         what="Incentive angles are blocked by the angle policy in high-vol-down/capitulation, and never used in UK-targeted sends (FCA PS23/6).",
         why="FCA bans incentives in crypto promotions; India ASCI requires the risk disclaimer on every ad.",
         source="FCA PS23/6; ASCI VDA guidelines", source_url="https://www.fca.org.uk/publications/policy-statements/ps23-6-financial-promotion-rules-cryptoassets",
         how="Angle policy (built in) + brief checker; add disclaimer snippets as content blocks.", prereqs=[], kpi="zero regret sends", effort="low", regimes=["all"], channels=["all"]),
]


def init_hacks_tables() -> None:
    conn = get_db()
    conn.execute("""CREATE TABLE IF NOT EXISTS growth_hacks (
        id TEXT PRIMARY KEY, source TEXT NOT NULL, title TEXT, category TEXT, payload_json TEXT,
        status TEXT DEFAULT 'new', verified INTEGER DEFAULT 0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
    conn.commit(); conn.close()


def _relevance(h: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    score = 50; reasons = []
    regime = ctx.get("regime", "unknown")
    if "all" in h.get("regimes", []) or regime in h.get("regimes", []):
        score += 15; reasons.append(f"fits regime {regime}")
    else:
        score -= 20; reasons.append(f"not for regime {regime}")
    chans = {c.lower() for c in ctx.get("channels_in_use", [])}
    for ch in h.get("channels", []):
        if ch not in ("all",) and ch not in chans:
            score += 8; reasons.append(f"{ch} channel unused today"); break
    gaps = ctx.get("uncovered_transitions", [])
    if h["category"] == "activation" and any("Funded" in g for g in gaps):
        score += 15; reasons.append("Funded → Activated has no standing campaign")
    if h["category"] == "retention" and any("Dormant" in g or "Slipping" in g for g in gaps):
        score += 15; reasons.append("dormant/slipping transition uncovered")
    if h["category"] == "risk" and ctx.get("crowded_positioning"):
        score += 10; reasons.append("crowded positioning detected today")
    if h["category"] == "deliverability" and ctx.get("delivery_problems"):
        score += 15; reasons.append("deliverability anomalies present")
    if h["category"] == "measurement" and not ctx.get("has_holdouts", False):
        score += 10; reasons.append("no holdouts configured yet")
    if h["category"] == "compliance" and ctx.get("risk_headlines", 0):
        score += 10; reasons.append(f"{ctx['risk_headlines']} risk headlines live")
    return {"score": max(0, min(100, score)), "reasons": reasons}


def workspace_context() -> Dict[str, Any]:
    ctx: Dict[str, Any] = {"regime": "unknown", "channels_in_use": [], "uncovered_transitions": [], "crowded_positioning": False, "delivery_problems": False, "has_holdouts": False, "risk_headlines": 0}
    try:
        from .market import market_context
        mk = market_context(force=False)
        ctx["regime"] = ((mk.get("crypto") or {}).get("regime") or {}).get("label", "unknown")
        md = mk.get("crypto_movers_detail") or {}
        ctx["crowded_positioning"] = bool(md.get("crowded_long") or md.get("crowded_short"))
        ctx["risk_headlines"] = len(((mk.get("news") or {}).get("risk_flags")) or [])
    except Exception:
        pass
    try:
        from .llm.tools import clm_program_audit, anomaly_report
        from .moengage import MoEngageClient
        camps = MoEngageClient().get_campaigns()
        ctx["channels_in_use"] = sorted({str(c.get("channel", "")).lower() for c in camps if c.get("channel")})
        ctx["uncovered_transitions"] = clm_program_audit().get("uncovered_transitions", [])
        an = anomaly_report()
        ctx["delivery_problems"] = any(a.get("metric") == "delivery_rate" and a.get("impact") == "bad" for a in an.get("anomalies", []))
    except Exception:
        pass
    return ctx


def ranked(status: Optional[str] = None) -> Dict[str, Any]:
    init_hacks_tables()
    ctx = workspace_context()
    conn = get_db()
    rows = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM growth_hacks").fetchall()}
    conn.close()
    out = []
    for h in LIBRARY:
        st = rows.get(h["id"], {}).get("status", "new")
        rel = _relevance(h, ctx)
        out.append({**h, "source_kind": "curated", "verified": True, "status": st, "relevance": rel["score"], "relevance_reasons": rel["reasons"]})
    for hid, r in rows.items():
        if r["source"] == "model":
            try:
                h = json.loads(r["payload_json"] or "{}")
            except Exception:
                continue
            rel = _relevance(h, ctx)
            out.append({**h, "id": hid, "source_kind": "model-suggested", "verified": bool(r["verified"]), "status": r["status"], "relevance": rel["score"] - 10, "relevance_reasons": rel["reasons"] + ["model-suggested; unverified" if not r["verified"] else "verified by a person"]})
    if status:
        out = [h for h in out if h["status"] == status]
    out.sort(key=lambda h: -h["relevance"])
    return {"context": ctx, "hacks": out, "generated_at": datetime.now(timezone.utc).isoformat()}


def set_status(hack_id: str, status: str, verified: Optional[bool] = None) -> None:
    init_hacks_tables()
    conn = get_db()
    row = conn.execute("SELECT id FROM growth_hacks WHERE id=?", (hack_id,)).fetchone()
    if row:
        if verified is None:
            conn.execute("UPDATE growth_hacks SET status=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (status, hack_id))
        else:
            conn.execute("UPDATE growth_hacks SET status=?, verified=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (status, int(verified), hack_id))
    else:
        lib = next((h for h in LIBRARY if h["id"] == hack_id), None)
        conn.execute("INSERT INTO growth_hacks (id, source, title, category, payload_json, status, verified) VALUES (?,?,?,?,?,?,?)",
                     (hack_id, "curated" if lib else "model", (lib or {}).get("title", hack_id), (lib or {}).get("category", ""), json.dumps(lib or {}), status, 1 if lib else int(bool(verified))))
    conn.commit(); conn.close()


def suggest_with_model(n: int = 5) -> Dict[str, Any]:
    """Ask the bulk-tier model for trending tactics not already in the library; stored as unverified."""
    from .llm.provider import LLMClient, llm_settings, LLMError
    cfg = llm_settings()
    if not (cfg["api_key"] or cfg["provider"] == "claude_cli"):
        return {"added": 0, "note": "no model configured"}
    ctx = workspace_context()
    known = [h["title"] for h in LIBRARY]
    prompt = (f"List {n} growth tactics that crypto/stock trading apps are using effectively in lifecycle/CRM marketing right now that are NOT in this list: {known}. "
              f"Workspace context: {json.dumps(ctx)}. For each return STRICT JSON items with keys: id (slug), title, category (acquisition|activation|engagement|retention|risk|deliverability|measurement|compliance), "
              "what, why, source (who is known to do it; say 'unverified' if unsure), how (MoEngage steps), prereqs (list), kpi, effort (low|medium|high), regimes (list, or ['all']), channels (list). "
              "No forecasts, no buy/sell instructions, keep compliance in mind. Output {\"hacks\":[...]} only.")
    try:
        out = LLMClient().chat([{"role": "user", "content": prompt}], tools=None, max_tokens=1600, temperature=0.4, tier="bulk")
        text = out.get("content") or ""
        s, e = text.find("{"), text.rfind("}")
        items = json.loads(text[s:e + 1]).get("hacks", []) if s >= 0 else []
    except (LLMError, ValueError, json.JSONDecodeError) as ex:
        return {"added": 0, "error": redact(str(ex))[:160]}
    init_hacks_tables()
    conn = get_db(); added = 0
    for h in items:
        if not isinstance(h, dict) or not h.get("title"):
            continue
        hid = "m_" + "".join(ch for ch in str(h.get("id") or h["title"]).lower().replace(" ", "_") if ch.isalnum() or ch == "_")[:60]
        if conn.execute("SELECT 1 FROM growth_hacks WHERE id=?", (hid,)).fetchone():
            continue
        conn.execute("INSERT INTO growth_hacks (id, source, title, category, payload_json, status, verified) VALUES (?,?,?,?,?,?,?)",
                     (hid, "model", h["title"][:160], h.get("category", ""), json.dumps(h, default=str), "new", 0))
        added += 1
    conn.commit(); conn.close()
    return {"added": added, "model": out.get("model")}
