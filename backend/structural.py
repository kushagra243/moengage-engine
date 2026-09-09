"""
Structural audit of the CRM / funnel journey — the campaigns a crypto
exchange's lifecycle programme must have, drawn from the CLM playbook, the
product-cohort playbook, derivatives-marketing practice and years of CLM case
material. Each play is detected against what MoEngage actually runs (campaign
inventory, channels, flows, SOP runs, cohort families) and, when missing,
becomes a P0 recommendation with an ICE score, a tagline, the segment, the SOP,
the KPI and a benchmark. Nothing here sends anything: it feeds Brain Lab,
Campaign Ideas and the agent, which still propose → approve.
"""
from __future__ import annotations
import re
from typing import Any, Dict, List, Optional

from . import ice as ice_mod

# detect: ("transition", id) → uncovered transition in clm_program_audit; ("words", regex) → no campaign name/segment matches;
#         ("channel", name) → channel absent from inventory; ("sop_run", sop_id) → never run via SOP; ("always", None) → always checked by fn
PLAYS: List[Dict[str, Any]] = [
    dict(id="kyc_progress_rescue", transition="acquired_verified", title="KYC rescue at the exact failure step", tagline="For sign-ups stuck at KYC · step-specific help within 2h of the failure, not a generic reminder · measured by kyc completion 72h",
         why="KYC drop-off is the largest leak in every Indian exchange funnel; document-specific help at the failure point (blurry PAN → retry tips, selfie → lighting) beats reminders 2–3× and never needs an incentive.",
         what="Event-triggered flow: failure reason → matching help (push + WhatsApp utility), support hand-off after the 2nd failure, silence after day 5.", who="KYC_PENDING / KYC_FAILED (by reason)", channel="push + whatsapp", sop="sop_kyc_completion", kpi="kyc_completion_rate_72h", benchmark="+8–15 pp completion vs reminder-only", impact=9, confidence=8, ease=6,
         detect=[("transition", "acquired_verified"), ("words", r"kyc|verif")]),
    dict(id="deposit_failure_recovery", transition="verified_funded", title="UPI / deposit failure recovery within minutes", tagline="For users whose deposit failed · fix-it message inside 10 minutes with the retry path · measured by first deposit 7d",
         why="The highest-ROI message in Indian fintech: a failed UPI intent is a user with money in hand. Recovery within minutes converts 25–40% of failures; the next day it is under 10%.",
         what="Smart Trigger on deposit_failed → in-app + push with the exact reason and retry path; bank-specific tips; escalate to WhatsApp utility if no retry in 2h.", who="DEPOSIT_FAILED_24H", channel="push + in-app + whatsapp", sop="sop_deposit_failure_recovery", kpi="first_deposit_rate_7d", benchmark="25–40% of failures recovered", impact=9, confidence=9, ease=7,
         detect=[("words", r"deposit fail|upi fail|payment fail|failure recovery")]),
    dict(id="verified_to_funded_objections", transition="verified_funded", title="Verified → funded objection ladder", tagline="For verified-but-empty accounts · money stuck? safety? how much? answered in order over 7 days · measured by first deposit 7d",
         why="Verified users who never fund cite three objections in a fixed order; answering them in sequence (instant withdrawals, custody facts, ₹100 is enough) lifts funding without a bonus. Salary-week timing (2–6 of month) adds 20–30%.",
         what="Flow: D0 in-app objection card, D2 email custody facts, D4 push '₹100 is enough' with one path, D6 WhatsApp utility; ≤4 touches; 10% holdout.", who="KYC_APPROVED_NODEP", channel="in-app + email + push", sop="sop_verified_to_funded", kpi="first_deposit_rate_7d", benchmark="+3–6 pp funding rate", impact=8, confidence=8, ease=7,
         detect=[("transition", "verified_funded")]),
    dict(id="funded_first_trade", transition="funded_activated", title="Funded → first trade in 7 days (own watchlist, SIP as low-anxiety first action)", tagline="For funded users with zero trades · guided first action from their own watchlist or a recurring buy · measured by first trade 7d",
         why="Funded-but-idle balances churn quietly; the best first action is one the user already signalled (watchlist) or the lowest-anxiety one (recurring buy). Naming an asset to buy is the trap.",
         what="Flow: D0 bottom-sheet tour, D1 watchlist-based nudge, D3 SIP setup, D5 alerts ('follow before you trade'); no perps, no urgency.", who="FTD_NOTRADE", channel="in-app + push + email", sop="sop_funded_to_first_trade", kpi="first_trade_rate_7d", benchmark="+5–10 pp first-trade rate", impact=9, confidence=8, ease=7,
         detect=[("transition", "funded_activated")]),
    dict(id="second_trade_72h", transition="activated_habitual", title="Second trade within 72h", tagline="For first-time traders · the one-more-action loop (alert → review → second order) · measured by second trade 7d",
         why="Retention curves are decided by the second trade, not the first; users who trade twice in 72h retain 2–3× at day 30.", what="Triggered on first_trade: T+2h receipt + 'set an alert', T+24h recap of the asset, T+48h watchlist review; stop at second trade.", who="FIRST_TRADE_72H", channel="push + in-app", sop="sop_second_trade_72h", kpi="second_trade_within_7d", benchmark="+6–12 pp second-trade rate", impact=8, confidence=8, ease=8,
         detect=[("words", r"second trade|2nd trade|72h|first week")]),
    dict(id="price_alert_adoption", transition="activated_habitual", title="Own-asset price-move alerts (the highest-CTR message class)", tagline="For holders and watchers · alerts on their own assets, one per day max · measured by alert adoption",
         why="Per-user price-move events on held or watched assets pull 10–20% CTR versus 2–8% for broadcast; alert users retain best of any habit cohort.", what="Smart Trigger on user-level price move (engine emits it) → push with the fact and the alert tool; cap 1/day; Cards for the rest.", who="HOLDERS / WATCHLIST per asset", channel="push + cards", sop="sop_market_move_alert", kpi="alert_adoption_rate", benchmark="CTR 10–20%; +20–30% alert adoption", impact=8, confidence=9, ease=6,
         detect=[("words", r"\balert|price move|watchlist")]),
    dict(id="weekly_recap_own_numbers", transition="activated_habitual", title="Weekly recap from the user's own numbers", tagline="For habitual traders · portfolio, fees saved, what moved in their holdings · measured by open rate and weekly active weeks",
         why="The recap is the retention backbone of every good trading app: it is service, not marketing, so it survives every regime and earns the right to send anything else.", what="Weekly email + Cards at best-time-to-send with own P&L, fees, alerts hit, one education line; 10% holdout at fixed time.", who="Habitual + Core", channel="email + cards", sop="sop_weekly_digest", kpi="weekly_active_weeks_4w", benchmark="open 30–45% (own data beats content)", impact=7, confidence=8, ease=8,
         detect=[("words", r"weekly|recap|digest|statement")]),
    dict(id="fee_tier_distance", transition="habitual_core", title="Fee-tier distance nudge (within 15% of the next tier)", tagline="For traders within reach of a lower fee · their exact distance to the next tier · measured by fee-tier upgrade rate",
         why="Depth comes from tiers, not promos; a personal distance-to-tier message converts 10–20% of near-threshold users and is compliant everywhere.", what="Monthly attribute fee_tier_distance_pct; trigger at ≤15%; email + in-app with the number; never 'trade more'.", who="NEAR_TIER (HVT/MVT)", channel="email + in-app", sop="sop_fee_tier_nudge", kpi="fee_tier_upgrade_rate", benchmark="10–20% of near-threshold users upgrade", impact=7, confidence=7, ease=7,
         detect=[("words", r"\bfee|tier")]),
    dict(id="product_graduation_by_intent", transition="habitual_core", title="Product graduation by intent (viewed perps/options twice → education first)", tagline="For spot traders who browsed derivatives · education-first path, never a leverage lure · measured by products per user",
         why="Cross-sell to derivatives works when it follows intent signals and starts with risk education; blanket perps promos raise liquidation-driven churn.", what="Trigger on 2 derivatives page views in 7d → 3-step education (margin, funding, liquidation distance) → risk-tools CTA; suppress liquidated/loss-dormant.", who="SPOT_HABITUAL with derivatives intent", channel="in-app + email", sop="sop_perp_intent_education", kpi="products_per_user", benchmark="+10–15% multi-product share; liquidation rate flat", impact=8, confidence=7, ease=6,
         detect=[("words", r"perp|futures|leverage|options|graduat|cross-?sell")]),
    dict(id="tokenised_cross_sell_us_hours", transition="habitual_core", title="Tokenised markets to US-hours and weekend traders", tagline="For crypto perp traders active 19:00–02:00 IST · 24/7 US stocks, indices, commodities as education · measured by tokenised first fill 14d",
         why="Our differentiator is 24/7 TradFi exposure; the natural cohort already trades in US hours. After-hours moves are the hook, never earnings forecasts.", what="Weekly after-hours movers card + one education email; weekend variant; venue never named.", who="PERP_HABITUAL_US_HOURS / WEEKEND_ACTIVE", channel="cards + email + push", sop="sop_cross_sell_crypto_to_tokenised", kpi="tokenised_first_fill_rate_14d", benchmark="5–8% of cohort makes a first fill", impact=8, confidence=6, ease=7,
         detect=[("words", r"tokeni|us stock|stocks|commodit|indices|index")]),
    dict(id="slipping_own_baseline", transition="slipping", title="Slipping detection vs the user's own baseline", tagline="For traders 40%+ below their own 8-week rhythm · cause-based check-in, lighter cadence · measured by trade frequency recovery 14d",
         why="'We miss you at day 7' is noise; decline versus the user's own baseline caught early and answered by cause (market, friction, loss) recovers 15–25% of slippers.", what="Weekly job computes trade-frequency delta → segments by cause → portfolio-review email, friction fix message, or silence for loss cases.", who="SLIPPING_BY_CAUSE", channel="email + in-app", sop="sop_slipping_checkin", kpi="trade_frequency_recovery_14d", benchmark="15–25% recovery vs holdout", impact=8, confidence=7, ease=5,
         detect=[("transition", "slipping"), ("words", r"slipping|declin|check-?in")]),
    dict(id="dormant_by_cause", transition="dormant_activated", title="Dormant reactivation segmented by cause with a 20% holdout", tagline="For 30–90d dormant users · market-dormant get 'what changed', friction-dormant get 'fixed', loss-dormant get service · measured by reactivation 14d vs holdout",
         why="Reactivation is where the market steals credit; only a holdout separates the campaign from the rally, and only cause segmentation avoids offers to lossy users.", what="Monthly wave, one attempt per user per month, email + push, regime-gated (never in stress).", who="DORMANT_30_90 by cause", channel="email + push", sop="sop_dormant_by_cause", kpi="reactivation_rate_14d", benchmark="1–4% incremental over holdout", impact=7, confidence=8, ease=7,
         detect=[("transition", "dormant_activated")]),
    dict(id="liquidation_recovery", transition="slipping", title="Liquidation-recovery flow (silence → explainer → spot-first)", tagline="For users liquidated in the last 14 days · T+0 silence, T+24h explainer, T+14 spot-first path · measured by return to trade 30d",
         why="A liquidated user who gets a market push the same day is gone; a silence-then-education sequence brings 20–30% back within 30 days, mostly via spot.", what="Trigger on liquidation event; suppress all promos 14d; explainer email; spot-first path; risk tools.", who="LIQUIDATED_14D", channel="email + in-app", sop="sop_liquidation_recovery", kpi="return_to_trade_rate_30d", benchmark="20–30% return within 30d", impact=9, confidence=8, ease=6,
         detect=[("words", r"liquidat")]),
    dict(id="funding_crowding_nudge", transition="habitual_core", title="Funding-cost and OI-crowding nudges to positioned users", tagline="For open perp positions in crowded trades · the funding cost in their own numbers, no direction · measured by risk-tool opens and liquidation rate",
         why="Derivatives retention is risk management; users who see funding cost and margin buffer before the move survive it. This is the only derivatives message that is compliant and welcome.", what="Daily job flags crowded longs/shorts → in-app + push to holders with funding APR and buffer; never 'close' or 'open'.", who="OPEN_POSITION in crowded perps", channel="in-app + push", sop="sop_funding_crowding_nudge", kpi="risk_tool_open_rate", benchmark="liquidation rate of nudged −10–20% vs holdout", impact=8, confidence=7, ease=6,
         detect=[("words", r"funding|open interest|\boi\b|crowd|margin")]),
    dict(id="regime_stress_mode", transition="slipping", title="Market-stress mode (Business Event that freezes promos and switches tone)", tagline="For everyone on a capitulation day · promos paused, service tone, product lenses · measured by uninstall and unsubscribe flat through the event",
         why="One tone-deaf send on a crash day costs years of trust; a regime flag that suppresses promotions and switches to service copy is the cheapest insurance a CRM can buy.", what="Regime detector (built in) → MoEngage Business Event → pause promo campaigns, send Tier-0 announcement with product lenses.", who="All actives (holders vs cash split)", channel="in-app + email", sop="sop_regime_stress_mode", kpi="uninstall_rate", benchmark="zero regret sends; disable rate ≤ baseline", impact=9, confidence=8, ease=6,
         detect=[("words", r"stress|market mode|regime|crash|volatil")]),
    dict(id="global_control_group", transition="programme", title="Global control group + per-campaign holdouts", tagline="For the whole programme · 5% global holdout and ≥10% per campaign · measured by incremental lift, not CTR",
         why="Without holdouts a third of 'winning' campaigns are noise or market; the experiment ledger only means something when every send has a control.", what="MoEngage global control 5%; every campaign brief carries control_group_pct ≥ 10; readouts in Experiments.", who="All", channel="—", sop=None, kpi="incremental_lift", benchmark="kills ~30% of false winners", impact=8, confidence=9, ease=8,
         detect=[("fn", "no_holdouts")]),
    dict(id="broadcast_share_cap", transition="programme", title="Cut broadcast share below 30% of sends", tagline="For the send mix · triggered and lifecycle sends replace generic blasts · measured by broadcast share and notification-disable rate",
         why="Broadcast-heavy programmes train users to disable notifications; every point of broadcast share moved to triggered sends raises CTR and lowers uninstalls.", what="Retire or re-target promotional blasts; move recaps to Cards; enforce frequency caps by stage.", who="All", channel="push", sop="sop_push_alert_digest", kpi="broadcast_share_pct", benchmark="broadcast ≤30%; disable rate < 0.1%/push", impact=7, confidence=8, ease=6,
         detect=[("fn", "broadcast_heavy")]),
    dict(id="stage_frequency_caps", transition="programme", title="Frequency caps and quiet hours by lifecycle stage", tagline="For every cohort · best users hear least; caps per channel per week with quiet hours · measured by peace index in band",
         why="Unbounded frequency across overlapping segments (the same HVT in five campaigns) is the silent killer of retention programmes.", what="Comms limits (built in) enforced at proposal time; MoEngage frequency capping + minimum delay per stage; peace index monitored weekly.", who="All", channel="push + email", sop=None, kpi="peace_index_in_band_pct", benchmark="−20–40% uninstalls on high-volume days", impact=7, confidence=8, ease=8,
         detect=[("fn", "peace_breaches")]),
    dict(id="web3_safety_onboarding", transition="funded_activated", title="Web3 wallet onboarding with safety first", tagline="For new Web3 wallet users · scams, slippage, gas and one watchlist action before any trend · measured by 7d retention and complaint rate",
         why="Web3 users churn on the first bad experience (scam token, failed swap); a safety-first onboarding lifts 7-day retention and cuts complaints.", what="D0 safety card, D1 first swap guide, D3 trending-as-data with 'unverified' wording; no token picks.", who="WEB3_NEW", channel="in-app + push", sop="sop_web3_onboarding_safety", kpi="web3_retention_7d", benchmark="+10–15 pp 7d retention", impact=6, confidence=6, ease=7,
         detect=[("words", r"web3|wallet|dex|onchain|on-chain")]),
    dict(id="sip_nurture", transition="activated_habitual", title="SIP / recurring-buy nurture with continuity through volatility", tagline="For SIP holders · monthly statement and DCA education, never a pause prompt · measured by SIP continuation 90d",
         why="Recurring buyers are the most valuable retention cohort; volatility weeks are when they cancel unless the education arrives first.", what="Monthly statement email; volatility-week education (no timing); anniversary card; suppress promos.", who="SIP_ACTIVE", channel="email + cards", sop="sop_sip_nurture", kpi="sip_continuation_rate_90d", benchmark="continuation 80%+ at 90d", impact=7, confidence=7, ease=8,
         detect=[("words", r"\bsip\b|recurring|dca|auto-?invest")]),
    dict(id="whatsapp_utility_journey", transition="verified_funded", title="WhatsApp utility journey for the onboarding funnel", tagline="For KYC and deposit stages · utility-only WhatsApp with 70%+ read rates · measured by step completion",
         why="WhatsApp utility messages are read by 70%+ in India versus 5–8% push CTR; the onboarding funnel is where that reach pays most.", what="Utility templates for KYC status, deposit confirmation/failure, first-trade receipt; strictly transactional tone.", who="ONBOARDING (KYC → first trade)", channel="whatsapp", sop="sop_whatsapp_utility_journey", kpi="first_deposit_rate_7d", benchmark="read 70%+; +2–4 pp funnel completion", impact=7, confidence=7, ease=5,
         detect=[("channel", "whatsapp")]),
    dict(id="cards_inbox_recaps", transition="activated_habitual", title="Cards (app inbox) for non-urgent recaps instead of push", tagline="For habitual users · market recaps and education move to the inbox · measured by card click rate at −30% push volume",
         why="Moving non-urgent content to Cards cuts push volume by a third at equal engagement, which is the budget that lets alerts stay loud.", what="Cards campaign with Content API prices; push only on threshold events.", who="Habitual + Core", channel="cards", sop="sop_push_alert_digest", kpi="card_click_rate", benchmark="push volume −30% at equal engagement", impact=6, confidence=7, ease=6,
         detect=[("channel", "cards")]),
    dict(id="channel_recovery_push_off", transition="activated_habitual", title="Channel recovery for users with push disabled", tagline="For high-value users who turned push off · email/WhatsApp reach and one reason to re-enable · measured by delivery reach on the segment",
         why="15–25% of the base has push off; for high-value users that is silent churn unless another channel carries alerts.", what="Segment push_disabled ∧ value tier → email digest + WhatsApp utility for critical alerts; one in-app re-enable prompt with a benefit.", who="PUSH_OFF_HVT", channel="email + whatsapp + in-app", sop="sop_channel_recovery_push_off", kpi="delivery_rate", benchmark="+15–25% reach on the segment", impact=6, confidence=7, ease=7,
         detect=[("words", r"push (off|disabled)|re-?enable|opt-?in")]),
    dict(id="rekyc_escalation", transition="acquired_verified", title="Re-KYC with escalating channel and constant tone", tagline="For users due for re-KYC · email → push → WhatsApp → in-app block, same calm tone · measured by completion before deadline",
         why="Compliance campaigns are judged on completion and support contacts avoided; escalating channels with a constant tone completes 80%+ before deadline.", what="T−30 email, T−14 push, T−7 WhatsApp, T−2 in-app; no fear framing.", who="REKYC_DUE", channel="email + push + whatsapp + in-app", sop="sop_rekyc", kpi="rekyc_completion_rate", benchmark="80%+ before deadline", impact=6, confidence=8, ease=7,
         detect=[("words", r"re-?kyc|periodic|update your")]),
    dict(id="monthly_cohort_refresh", transition="programme", title="Monthly cohort upload refresh with version deltas", tagline="For every uploaded family · new version re-pointed, old retired, deltas read · measured by coverage of families with a campaign",
         why="Segments uploaded monthly decay silently; campaigns keep firing at last month's HVT list unless the refresh is procedural.", what="SOP on upload: decode names, compare reach and response to prior version, re-point standing campaigns, archive old versions.", who="All uploaded families", channel="—", sop="sop_monthly_cohort_upload", kpi="cohort_coverage_pct", benchmark="100% families mapped within 3 days of upload", impact=6, confidence=8, ease=7,
         detect=[("fn", "orphan_families")]),
]


def _inventory_text(campaigns: List[Dict[str, Any]]) -> str:
    return " ".join(" ".join(str(c.get(k, "")) for k in ("name", "target_segment", "description", "campaign_name")) for c in campaigns).lower()


def audit(campaigns: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Which structural plays are missing from the live programme, ranked by ICE; plus what is covered."""
    from .llm.tools import clm_program_audit, _client
    from . import experiments, guardrails
    camps = campaigns if campaigns is not None else _client().get_campaigns()
    pa = clm_program_audit()
    cov = pa.get("coverage") or {}
    text = _inventory_text(camps)
    channels = {str(c.get("channel") or "").lower() for c in camps}
    try:
        from .sops import list_runs
        runs = {r.get("sop_id") for r in list_runs(200)}
    except Exception:
        runs = set()
    exps = experiments.list_experiments(200)
    holdouts = any((e.get("control_group_pct") or 0) >= 5 for e in exps)
    try:
        pi = guardrails.peace_index(camps)
        breaches = (pi.get("counts") or {}).get("too_much", 0); orphan = (pi.get("counts") or {}).get("too_little", 0)
    except Exception:
        breaches = orphan = 0
    fns = {"no_holdouts": not holdouts and len(camps) > 0, "broadcast_heavy": (pa.get("broadcast_share_pct") or 0) > 30, "peace_breaches": breaches > 0, "orphan_families": orphan > 0}
    gaps, covered = [], []
    for p in PLAYS:
        missing = True
        for kind, arg in p["detect"]:
            if kind == "transition" and (cov.get(arg) or {}).get("campaigns"):
                missing = False
            elif kind == "words" and re.search(arg, text):
                missing = False
            elif kind == "channel" and any(arg in ch for ch in channels):
                missing = False
            elif kind == "sop_run" and arg in runs:
                missing = False
            elif kind == "fn":
                missing = bool(fns.get(arg))
        if p.get("sop") and p["sop"] in runs:
            missing = False
        row = {k: v for k, v in p.items() if k != "detect"}
        row["ice"] = ice_mod.score(p["impact"], p["confidence"], p["ease"])
        (gaps if missing else covered).append(row)
    gaps.sort(key=lambda r: -r["ice"]["score"])
    return {"gaps": gaps, "covered": [c["id"] for c in covered], "programme": {"campaigns": len(camps), "broadcast_share_pct": pa.get("broadcast_share_pct"), "uncovered_transitions": pa.get("uncovered_transitions"), "holdouts": holdouts, "channels": sorted(channels)},
            "verdict": f"{len(gaps)} structural plays missing of {len(PLAYS)}; top gap: {gaps[0]['title']}" if gaps else "every structural play is present", "source": "clm-campaign-playbook · product-cohort-playbook · crypto-derivatives-marketing · CLM case material"}


def as_recommendations(limit: int = 6, campaigns: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """Top structural gaps in the Brain Lab recommendation shape (priority 95+, flagged structural)."""
    out = []
    for g in audit(campaigns)["gaps"][:limit]:
        out.append({"priority": 95 + min(4, g["ice"]["score"] // 200), "structural": True, "title": g["title"], "tagline": g["tagline"], "why": g["why"], "who": g["who"], "what": g["what"], "sop": g.get("sop"), "kpi": g["kpi"], "benchmark": g.get("benchmark"),
                    "urgency": "this week", "avoid": "shipping without a holdout and a kill rule", "product": None, "ice": g["ice"], "transition": g["transition"], "channel": g["channel"], "id": g["id"]})
    return out


def sync_to_feed(campaigns: Optional[List[Dict[str, Any]]] = None) -> Dict[str, int]:
    """Push missing plays into the growth feed as structural_gap ideas (de-duplicated by title; covered plays expire out)."""
    from . import growth
    a = audit(campaigns)
    ideas = []
    for g in a["gaps"]:
        ideas.append({"title": g["title"], "kind": "structural_gap", "why": g["why"], "how": g["what"], "segment": g["who"], "channel": g["channel"], "angle": g["transition"], "kpi": g["kpi"], "transition": g["transition"],
                      "effort": "low" if g["ease"] >= 8 else "high" if g["ease"] <= 4 else "medium", "expected_impact": g.get("benchmark") or "", "priority": min(99, 80 + g["ice"]["score"] // 40),
                      "data": {"structural": True, "play_id": g["id"], "sop": g.get("sop"), "tagline": g["tagline"], "ice": g["ice"], "ttl_hours": 24 * 45}})
    res = growth.upsert_ideas(ideas, "skills")
    return {**res, "gaps": len(a["gaps"]), "covered": len(a["covered"])}
