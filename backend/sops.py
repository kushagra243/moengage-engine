"""
Campaign SOPs — standard operating procedures the team runs the lifecycle
programme with. An SOP fixes, for one campaign type: objective/transition,
audience (a cohort family from the segment registry, exclusions,
jurisdictions), the message sequence (day offsets, channel, purpose, copy
brief), duration and frequency, holdout, KPI/target/guardrail/kill criteria,
and the checks that run before, during and after.

Framework checks are code (sop_check); the SOP library is seeded from the
best-performing patterns and is editable by the team or the agent
(define_sop). Running an SOP (run_sop) resolves the cohort, runs pre-flight,
and queues one approval-gated proposal per step with a full goal brief; a
sop_runs row tracks the run and its mid-flight checks.
"""
from __future__ import annotations
import json
import re
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from .database import get_db
from .security import audit, redact

CAMPAIGN_TYPES = ("onboarding", "activation", "retention", "winback", "risk", "compliance", "market", "newsletter", "competition", "cohort_upload", "education")
CHANNELS = ("push", "email", "whatsapp", "sms", "in-app", "cards")
STANDARD_EXCLUSIONS = ["unsubscribed / DND", "KYC pending", "open support ticket", "liquidated in last 14d", "loss-dormant (realised loss >20% of deposits, no trade 21d)"]
DERIVATIVES_TYPES = ("market", "risk", "education")

LIBRARY: List[Dict[str, Any]] = [
    dict(id="sop_verified_to_funded", name="Verified → first deposit (7-day sequence)", campaign_type="onboarding", transition="verified_funded",
         objective="Turn KYC-approved users into first-time depositors within 7 days without discount addiction.",
         audience=dict(segment_family="KYC_APPROVED_NODEP", description="KYC approved, no deposit, 0–7 days since approval", exclusions=STANDARD_EXCLUSIONS, min_reach=500, jurisdictions_excluded=[]),
         steps=[dict(day=0, channel="in-app", purpose="UPI deposit in 20 seconds; show the exact steps", copy_brief="Fact: account is ready. Relevance: first deposit unlocks trading. Tool: UPI deposit screen. No bonus.", send_time_ist="11:00"),
                dict(day=1, channel="push", purpose="Remove the fear: withdrawals are instant, no lock-in", copy_brief="Address the objection (money stuck). Tool: how withdrawals work screen.", send_time_ist="19:00"),
                dict(day=3, channel="whatsapp", purpose="Deposit failed / abandoned? help path", copy_brief="Utility template: if a deposit failed, retry or IMPS; support link.", condition="deposit_initiated without deposit_completed", send_time_ist="12:00"),
                dict(day=6, channel="email", purpose="What ₹500 lets you do: spot, alerts, SIP / recurring buy", copy_brief="Education, fee transparency, TDS one-liner; ASCI disclaimer in footer.", send_time_ist="09:00")],
         duration_days=7, frequency=dict(cadence="event_triggered", max_messages_per_user_per_week=4), holdout_pct=10,
         primary_kpi="first_deposit_rate_7d", target="+3 pp vs holdout", guardrail_metric="unsubscribe_rate", measurement_window_days=14,
         kill_criteria=["delivery_rate < 85% on any push step", "unsubscribe_rate > 0.5% on email step", "support_ticket_rate of cohort > 2× baseline"],
         checks=dict(preflight=["framework", "segment_exists", "exclusions_present", "brief_per_step", "compliance"], midflight=["kill_criteria_daily", "holdout_intact"], postflight=["readout_with_ci", "lesson_to_feed"]),
         compliance=dict(disclaimer_channels=["email", "whatsapp", "in-app"], banned_angles=["bonus", "fomo"]), source="library", version=1),
    dict(id="sop_funded_to_first_trade", name="Funded → first trade (72h)", campaign_type="activation", transition="funded_activated",
         objective="First spot trade within 72h of first deposit; small size, no leverage.",
         audience=dict(segment_family="FTD_NOTRADE", description="first deposit completed, no order_filled", exclusions=STANDARD_EXCLUSIONS, min_reach=300, jurisdictions_excluded=[]),
         steps=[dict(day=0, channel="in-app", purpose="Your ₹ is in: three ways to start (SIP / recurring buy, watchlist, ₹100 trade)", copy_brief="Fact: deposit landed. Tool: guided first trade. No asset recommendation — user picks from watchlist/top by volume.", send_time_ist="+1h"),
                dict(day=1, channel="push", purpose="Watchlist first: follow before you trade", copy_brief="Habit tool; alerts adoption.", send_time_ist="18:30"),
                dict(day=2, channel="email", purpose="How fees and TDS work on your first trade", copy_brief="Transparency; disclaimer footer; link to fee page.", send_time_ist="09:00")],
         duration_days=3, frequency=dict(cadence="event_triggered", max_messages_per_user_per_week=3), holdout_pct=10, primary_kpi="first_trade_rate_7d", target="+4 pp vs holdout",
         guardrail_metric="notification_disable_rate", measurement_window_days=10, kill_criteria=["delivery_rate < 85%", "notification_disable_rate > 0.3%"],
         checks=dict(preflight=["framework", "segment_exists", "exclusions_present", "brief_per_step", "compliance"], midflight=["kill_criteria_daily"], postflight=["readout_with_ci", "lesson_to_feed"]),
         compliance=dict(disclaimer_channels=["email", "in-app"], banned_angles=["asset_recommendation", "leverage"]), source="library", version=1),
    dict(id="sop_hvt_retention", name="HVT monthly retention (few, rich messages)", campaign_type="retention", transition="habitual_core",
         objective="Keep high-value traders active with service-grade content; fewer messages than any other cohort.",
         audience=dict(segment_family="HVT", description="monthly HVT upload (high-value traders)", exclusions=STANDARD_EXCLUSIONS, min_reach=200, jurisdictions_excluded=[]),
         steps=[dict(day=0, channel="email", purpose="Monthly statement: fees paid, tier progress, funding paid, what changed", copy_brief="Own numbers only; no market outlook; fee-tier threshold if within 15%.", send_time_ist="09:00"),
                dict(day=7, channel="in-app", purpose="Risk tool of the month (hedge calculator / isolated margin)", copy_brief="Education; derivatives disclaimer block on the screen.", send_time_ist="13:00"),
                dict(day=14, channel="push", purpose="Personal: alert on their most-viewed asset if not set", copy_brief="Fact + tool; only if no alert exists.", condition="price_alert_set = 0 in 30d", send_time_ist="19:00")],
         duration_days=30, frequency=dict(cadence="once_per_cohort_version", max_messages_per_user_per_week=1), holdout_pct=20, primary_kpi="weekly_active_weeks_4w", target="≥ +0.3 weeks vs holdout",
         guardrail_metric="unsubscribe_rate", measurement_window_days=30, kill_criteria=["unsubscribe_rate > 0.3%", "support_complaints about frequency > 0"],
         checks=dict(preflight=["framework", "segment_exists", "exclusions_present", "brief_per_step", "compliance", "previous_version_readout"], midflight=["kill_criteria_daily", "holdout_intact"], postflight=["readout_with_ci", "version_delta", "lesson_to_feed"]),
         compliance=dict(disclaimer_channels=["email", "in-app"], banned_angles=["leverage_upsell", "size_up"]), source="library", version=1),
    dict(id="sop_dormant_by_cause", name="Dormant win-back by cause (market / friction / loss)", campaign_type="winback", transition="dormant_activated",
         objective="Reactivate 30–90d dormant users with the message that matches why they left; loss-dormant get service, not promos.",
         audience=dict(segment_family="DORMANT_D30_D90", description="no trade 30–90d, split by cause attribute", exclusions=STANDARD_EXCLUSIONS + ["loss-dormant → separate service track"], min_reach=1000, jurisdictions_excluded=[]),
         steps=[dict(day=0, channel="email", purpose="Market-dormant: what changed since you left (facts, no outlook)", copy_brief="Regime-aware; only in trending_up or chop; features shipped since last session.", condition="cause = market", send_time_ist="09:00"),
                dict(day=0, channel="whatsapp", purpose="Friction-dormant: the thing that broke is fixed (deposit/KYC)", copy_brief="Utility tone; direct link to the fixed flow.", condition="cause = friction", send_time_ist="12:00"),
                dict(day=3, channel="push", purpose="Habit tool re-entry: one alert, no trading needed", copy_brief="Fact + tool.", send_time_ist="19:00"),
                dict(day=10, channel="in-app", purpose="If back: what to try next (one product)", copy_brief="Only for users who opened the app after step 1.", condition="app_opened since day 0", send_time_ist="on_open")],
         duration_days=14, frequency=dict(cadence="monthly", max_messages_per_user_per_week=2), holdout_pct=20, primary_kpi="reactivation_rate_14d", target="+2 pp vs holdout",
         guardrail_metric="unsubscribe_rate", measurement_window_days=21, kill_criteria=["unsubscribe_rate > 0.6%", "regime flips to high_volatility_down or capitulation → pause market-dormant track"],
         checks=dict(preflight=["framework", "segment_exists", "exclusions_present", "brief_per_step", "compliance", "regime_allows"], midflight=["kill_criteria_daily", "regime_watch"], postflight=["readout_with_ci", "lesson_to_feed"]),
         compliance=dict(disclaimer_channels=["email", "whatsapp", "in-app"], banned_angles=["fomo", "win_framing"]), source="library", version=1),
    dict(id="sop_liquidation_recovery", name="Liquidation recovery (T+0 silence → T+14 spot path)", campaign_type="risk", transition="slipping",
         objective="Keep liquidated users as customers without ever encouraging re-leveraging.",
         audience=dict(segment_family="LIQUIDATED_14D", description="position_liquidated in last 14 days", exclusions=["unsubscribed / DND", "open support ticket (route to support)"], min_reach=50, jurisdictions_excluded=["UK", "US"]),
         steps=[dict(day=0, channel="in-app", purpose="Service card: what happened, where to see it, support link", copy_brief="No promo. Their numbers. Derivatives disclaimer block.", send_time_ist="+1h"),
                dict(day=1, channel="email", purpose="Plain-language explainer: margin, funding, ADL; position-size calculator", copy_brief="No CTA to trade; disclaimer.", send_time_ist="10:00"),
                dict(day=4, channel="email", purpose="Isolated vs cross, stop-loss habits (opt-in series)", copy_brief="Education only.", condition="opened day-1 email", send_time_ist="10:00"),
                dict(day=14, channel="push", purpose="Spot-first path: trade without leverage", copy_brief="Only if no trade since liquidation; measured vs holdout.", condition="order_filled = 0 since day 0", send_time_ist="18:30")],
         duration_days=14, frequency=dict(cadence="event_triggered", max_messages_per_user_per_week=2), holdout_pct=20, primary_kpi="return_to_trade_rate_30d", target="+5 pp vs holdout",
         guardrail_metric="repeat_liquidation_30d", measurement_window_days=30, kill_criteria=["repeat_liquidation_30d of treated > holdout", "support_contact_rate > 2× baseline"],
         checks=dict(preflight=["framework", "segment_exists", "suppression_everywhere", "brief_per_step", "compliance", "jurisdiction"], midflight=["kill_criteria_daily", "suppression_intact"], postflight=["readout_with_ci", "lesson_to_feed"]),
         compliance=dict(disclaimer_channels=["email", "in-app", "push_landing"], banned_angles=["leverage_upsell", "re_enter", "win_framing", "fomo"]), source="library", version=1),
    dict(id="sop_market_move_alert", name="Market-move alert to watchers (fact + tool, TTL 4h)", campaign_type="market", transition="activated_habitual",
         objective="Convert market moves into alert/watchlist habits without predictions.",
         audience=dict(segment_family="WATCHERS_SYMBOL", description="watchlist or holding contains the moved symbol; active 30d", exclusions=STANDARD_EXCLUSIONS + ["received a market push today"], min_reach=200, jurisdictions_excluded=[]),
         steps=[dict(day=0, channel="push", purpose="The move as a fact + set an alert", copy_brief="Own relevance (their watchlist); time-stamped number; tool CTA; TTL 4h.", send_time_ist="within 2h of move, 08:00–21:00", ttl_hours=4)],
         duration_days=1, frequency=dict(cadence="event_triggered", max_messages_per_user_per_week=3), holdout_pct=10, primary_kpi="alert_adoption_rate", target="≥ 2% of delivered set an alert",
         guardrail_metric="notification_disable_rate", measurement_window_days=3, kill_criteria=["regime = capitulation or high_volatility_down → suppressed by policy", "notification_disable_rate > 0.3%"],
         checks=dict(preflight=["framework", "angle_policy", "ttl", "brief_per_step", "compliance"], midflight=["ttl_respected", "kill_criteria_daily"], postflight=["ctr_by_regime", "lesson_to_feed"]),
         compliance=dict(disclaimer_channels=["push_landing"], banned_angles=["forecast", "direction", "urgency_on_price"]), source="library", version=1),
    dict(id="sop_rekyc", name="Re-KYC compliance reminders (deadline-driven)", campaign_type="compliance", transition="acquired_verified",
         objective="Complete re-KYC before the deadline with the minimum messages; escalate channel, never tone.",
         audience=dict(segment_family="REKYC_PENDING", description="re-KYC due within 30 days, not completed", exclusions=["completed re-KYC", "unsubscribed transactional (not possible: transactional)"], min_reach=100, jurisdictions_excluded=[]),
         steps=[dict(day=0, channel="email", purpose="What is due, by when, 2-minute in-app flow", copy_brief="Transactional tone; exact date; documents accepted.", send_time_ist="09:00"),
                dict(day=7, channel="push", purpose="Reminder with days left", copy_brief="Days-left number; deep link.", send_time_ist="11:00"),
                dict(day=14, channel="whatsapp", purpose="Utility reminder + help", copy_brief="Approved utility template; support option.", send_time_ist="12:00"),
                dict(day=25, channel="sms", purpose="Final notice: withdrawals paused after deadline", copy_brief="DLT template; factual consequence; link.", send_time_ist="10:30")],
         duration_days=30, frequency=dict(cadence="monthly", max_messages_per_user_per_week=1), holdout_pct=5, primary_kpi="rekyc_completion_rate_30d", target="≥ 85% before deadline",
         guardrail_metric="support_ticket_rate", measurement_window_days=35, kill_criteria=["completion ≥ 95% → stop remaining steps", "support complaints about tone > 0"],
         checks=dict(preflight=["framework", "segment_exists", "brief_per_step", "compliance", "channel_templates_approved"], midflight=["kill_criteria_daily", "stop_on_completion"], postflight=["completion_curve", "lesson_to_feed"]),
         compliance=dict(disclaimer_channels=[], banned_angles=["promo"]), source="library", version=1),
    dict(id="sop_monthly_cohort_upload", name="Monthly cohort upload → re-point programmes", campaign_type="cohort_upload", transition="habitual_core",
         objective="When the new month's cohorts land (HVT_Oct26 …), baseline them, compare with last month, re-point standing campaigns, retire old versions.",
         audience=dict(segment_family="*", description="every family with a new version this month", exclusions=[], min_reach=0, jurisdictions_excluded=[]),
         steps=[dict(day=0, channel="in-app", purpose="(internal) run segment_study; produce the baseline note", copy_brief="No user message; analysis step.", send_time_ist="09:00"),
                dict(day=0, channel="email", purpose="(internal) re-point each standing campaign to the new version; archive old", copy_brief="No user message; proposals per campaign.", send_time_ist="10:00")],
         duration_days=2, frequency=dict(cadence="monthly", max_messages_per_user_per_week=0), holdout_pct=20, primary_kpi="cohort_coverage_pct", target="100% of new versions attached within 48h",
         guardrail_metric="orphan_cohorts", measurement_window_days=7, kill_criteria=["a new version differs > 40% in reach from last month → stop and ask the team"],
         checks=dict(preflight=["framework", "new_versions_detected"], midflight=["coverage_daily"], postflight=["version_delta", "lesson_to_feed"]),
         compliance=dict(disclaimer_channels=[], banned_angles=[]), source="library", version=1),
    dict(id="sop_weekly_digest", name="Weekly recap (Sunday 19:00 IST, own numbers)", campaign_type="newsletter", transition="activated_habitual",
         objective="A weekly habit anchor built from the user's own data; the market section is one factual paragraph.",
         audience=dict(segment_family="ACTIVE_30D", description="traded or opened app in 30d, email opt-in", exclusions=STANDARD_EXCLUSIONS, min_reach=2000, jurisdictions_excluded=[]),
         steps=[dict(day=0, channel="email", purpose="Your week: P&L, trades, fees, alerts hit; one market paragraph", copy_brief="Content API for numbers; disclaimer footer; no outlook.", send_time_ist="19:00")],
         duration_days=1, frequency=dict(cadence="weekly", max_messages_per_user_per_week=1), holdout_pct=10, primary_kpi="sessions_per_week", target="+0.2 vs holdout",
         guardrail_metric="unsubscribe_rate", measurement_window_days=28, kill_criteria=["unsubscribe_rate > 0.4% two weeks running", "open rate < 15% four weeks running → redesign"],
         checks=dict(preflight=["framework", "segment_exists", "content_api_healthy", "compliance"], midflight=["kill_criteria_weekly"], postflight=["readout_with_ci", "lesson_to_feed"]),
         compliance=dict(disclaimer_channels=["email"], banned_angles=["forecast"]), source="library", version=1),
    dict(id="sop_trading_competition", name="Spot trading competition (volume-ranked, 7 days)", campaign_type="competition", transition="habitual_core",
         objective="Lift weekly actives among Habitual spot traders with a fee-rebate competition; never P&L leaderboards; never to liquidated or UK users.",
         audience=dict(segment_family="HABITUAL_SPOT", description="≥8 spot fills / 4 weeks, no perp in 30d", exclusions=STANDARD_EXCLUSIONS + ["UK/US residents"], min_reach=1000, jurisdictions_excluded=["UK", "US"]),
         steps=[dict(day=-2, channel="email", purpose="Announce: rules, ranking by volume, rebates for top 500, terms", copy_brief="Complete terms; disclaimer; no 'win big'.", send_time_ist="10:00"),
                dict(day=0, channel="push", purpose="Starts now", copy_brief="Fact; link to leaderboard (volume only).", send_time_ist="10:00"),
                dict(day=4, channel="in-app", purpose="Your rank and distance to the next tier", copy_brief="Own numbers only.", send_time_ist="on_open"),
                dict(day=7, channel="email", purpose="Results and rebates credited", copy_brief="Transparency; next steps: alerts/watchlist.", send_time_ist="12:00")],
         duration_days=9, frequency=dict(cadence="quarterly", max_messages_per_user_per_week=3), holdout_pct=20, primary_kpi="weekly_active_weeks_4w", target="+0.4 vs holdout without volume collapse after",
         guardrail_metric="post_competition_volume_drop_pct", measurement_window_days=28, kill_criteria=["regime flips to stress → pause promotion, keep terms", "wash-trading flags > 1% of participants"],
         checks=dict(preflight=["framework", "segment_exists", "jurisdiction", "terms_approved", "compliance", "regime_allows"], midflight=["kill_criteria_daily", "fraud_watch"], postflight=["readout_with_ci", "post_period_volume", "lesson_to_feed"]),
         compliance=dict(disclaimer_channels=["email", "in-app"], banned_angles=["pnl_leaderboard", "win_framing", "incentive_to_uk"]), source="library", version=1),
]


def _sop(id, name, campaign_type, transition, objective, user, family, steps, duration_days, cadence, per_week, holdout, kpi, target, guardrail, window, kill, ideas,
         exclusions=None, jurisdictions=None, disclaimer=("email", "whatsapp", "in-app"), banned=(), min_reach=200, extra_checks=()):
    return dict(id=id, name=name, campaign_type=campaign_type, transition=transition, objective=objective, user=user,
                audience=dict(segment_family=family, description=user, exclusions=list(exclusions if exclusions is not None else STANDARD_EXCLUSIONS), min_reach=min_reach, jurisdictions_excluded=list(jurisdictions or [])),
                steps=steps, duration_days=duration_days, frequency=dict(cadence=cadence, max_messages_per_user_per_week=per_week), holdout_pct=holdout,
                primary_kpi=kpi, target=target, guardrail_metric=guardrail, measurement_window_days=window, kill_criteria=list(kill), ideas=list(ideas),
                checks=dict(preflight=["framework", "segment_exists", "exclusions_present", "brief_per_step", "compliance", "limits"] + list(extra_checks), midflight=["kill_criteria_daily", "peace_index"], postflight=["readout_with_ci", "lesson_to_feed"]),
                compliance=dict(disclaimer_channels=list(disclaimer), banned_angles=list(banned)), source="library", version=1)


def _st(day, channel, purpose, brief, t="11:00", condition=None, ttl=None):
    d = dict(day=day, channel=channel, purpose=purpose, copy_brief=brief, send_time_ist=t)
    if condition: d["condition"] = condition
    if ttl: d["ttl_hours"] = ttl
    return d


DERIV_EXCL = STANDARD_EXCLUSIONS
LIBRARY += [
    _sop("sop_kyc_completion", "KYC completion (72h, help at the failure point)", "onboarding", "acquired_verified", "Get signed-up users through KYC within 72h by helping at the exact step they failed.",
         "Signed up, KYC not approved, 0–3 days since signup (families: KYC_PENDING / SIGNUP_NOKYC)", "KYC_PENDING",
         [_st(0, "in-app", "Progress framing: what is done, what is left", "2 of 3 done; 2 minutes; documents accepted.", "+1h"), _st(1, "push", "Step-specific help", "Blurry PAN? retry tips; deep link to the failed step.", "12:00", "kyc_rejected or kyc_started without submit"),
          _st(2, "whatsapp", "Human hand-off after second failure", "Utility template; support chat link; no promo.", "12:30", "kyc_rejected >= 2")],
         3, "event_triggered", 3, 10, "kyc_completion_rate_72h", "+5 pp vs holdout", "support_ticket_rate", 7, ["support_ticket_rate > 2× baseline", "notification_disable_rate > 0.3%"],
         ["A/B: progress bar vs plain text", "Regional language for step help (Hindi/Telugu)", "Video 20s explainer on in-app card"], exclusions=["unsubscribed / DND", "KYC approved"], disclaimer=("in-app",), min_reach=100),
    _sop("sop_deposit_failure_recovery", "Deposit failure recovery (minutes, not days)", "activation", "verified_funded", "Recover failed/abandoned UPI deposits while intent is hot; the highest-ROI message in Indian fintech.",
         "deposit_initiated without deposit_completed in the last 60 minutes (family: DEP_FAILED)", "DEP_FAILED",
         [_st(0, "push", "Nothing was debited — retry in 20 seconds", "Bank-side timeout explanation; retry deep link; IMPS alternative.", "+15m"), _st(0, "whatsapp", "Still stuck? here is how", "Utility template with the two alternatives and support.", "+3h", "still no deposit_completed")],
         1, "event_triggered", 2, 10, "deposit_recovery_rate_24h", "≥ 25% of failures recovered", "support_ticket_rate", 3, ["delivery_rate < 85%", "complaints about duplicate debits > 0"],
         ["Show the exact bank error class in copy", "Pre-fill the retry amount", "Route NEFT failures to a different explainer"], exclusions=["unsubscribed / DND", "deposit completed"], disclaimer=("whatsapp",), min_reach=50),
    _sop("sop_first_week_habit", "First-week habit loop (watchlist → alert → recap)", "activation", "activated_habitual", "Turn a first trade into a weekly habit with tools, not promos.",
         "First trade in the last 7 days, < 2 trades total (family: FTT_WEEK1)", "FTT_WEEK1",
         [_st(0, "in-app", "Add your first watchlist item", "Habit tool; one tap from the asset just traded.", "+2h"), _st(2, "push", "Set one 5% alert", "Fact + tool; no market talk.", "19:00", "price_alert_set = 0"),
          _st(6, "email", "Your first week: trades, fees, what to try next", "Own numbers; one feature; disclaimer footer.", "09:00")],
         7, "event_triggered", 3, 20, "second_trade_within_7d", "+4 pp vs holdout", "notification_disable_rate", 14, ["notification_disable_rate > 0.3%", "unsubscribe_rate > 0.5%"],
         ["Cards recap instead of email for users who ignore email", "Alert threshold personalised to asset volatility", "Hinglish variant for app_language=hi"]),
    _sop("sop_second_trade_72h", "Second trade within 72h (guided, no asset recommendation)", "activation", "activated_habitual", "Move first-time traders to a second trade quickly without naming an asset.",
         "Exactly one fill, 1–3 days ago, still has balance (family: FTT_NOSECOND)", "FTT_NOSECOND",
         [_st(1, "push", "SIP (SIP / recurring buy) or a second asset from your watchlist", "Two tool paths; no asset named by us.", "18:30"), _st(3, "in-app", "Fees and TDS on small trades", "Transparency card; disclaimer.", "on_open")],
         3, "event_triggered", 2, 20, "second_trade_within_7d", "+3 pp vs holdout", "unsubscribe_rate", 10, ["delivery_rate < 85%"], ["Test 'SIP / recurring buy' vs 'watchlist' as the lead", "Send at the hour of the first trade"]),
    _sop("sop_perp_intent_education", "Perp intent without a trade → education path (opt-in, exit offered)", "education", "habitual_core", "Intent-triggered derivatives education with an explicit 'spot is fine' exit; never a promo.",
         "Viewed a perp market ≥2× in 14 days, 0 perp fills ever, country not UK/US (family: PERP_INTENT_NOTRADE)", "PERP_INTENT_NOTRADE",
         [_st(0, "in-app", "What a perp is, in one screen", "Funding in one line; isolated margin default; derivatives disclaimer block; exit: keep to spot.", "13:00"), _st(2, "email", "Funding, margin, liquidation — plainly", "Education; calculator link; disclaimer.", "10:00", "opened step 0"),
          _st(4, "in-app", "If you ever start: 1–2x, isolated, stop-loss", "Hygiene checklist; no CTA to trade.", "13:00", "opened step 1")],
         5, "event_triggered", 3, 20, "first_perp_trade_rate_30d", "opt-in cohort only; liquidation_rate_30d ≤ baseline", "liquidation_rate_30d", 30, ["liquidation_rate_30d of treated > holdout", "any UK/US recipient → stop"],
         ["Add a 'paper first' demo path", "Quiz gate before the third message", "Measure hedgers separately"], exclusions=DERIV_EXCL, jurisdictions=["UK", "US"], banned=("leverage_upsell", "first_futures_trade_promo", "size_up"), min_reach=100, extra_checks=["jurisdiction"]),
    _sop("sop_first_perp_hygiene", "First perp → position hygiene (72h)", "risk", "habitual_core", "After the first perp fill, teach SL/TP and isolated margin before the second.",
         "Exactly one perp fill in the last 3 days (family: FIRST_PERP)", "FIRST_PERP",
         [_st(0, "in-app", "How funding was charged on your position", "Their numbers; funding per day; disclaimer block.", "+2h"), _st(1, "push", "Set a stop-loss on your open position", "Tool only; no direction.", "12:00", "stop_loss_set = 0 and position open")],
         3, "event_triggered", 2, 20, "second_perp_within_14d", "liquidation_rate_30d −30% vs holdout", "liquidation_rate_30d", 30, ["liquidation_rate_30d treated > holdout"],
         ["Isolated-margin default prompt", "Distance-to-liquidation widget card"], exclusions=["unsubscribed / DND", "open support ticket"], jurisdictions=["UK", "US"], disclaimer=("in-app", "push_landing"), banned=("size_up", "leverage_upsell"), min_reach=50),
    _sop("sop_leverage_climber_risk", "Leverage climber risk brief (weekly)", "risk", "slipping", "Users whose leverage keeps rising get risk education, never encouragement.",
         "Median leverage up ≥2 steps in 30 days, no liquidation yet (family: LEV_CLIMBER)", "LEV_CLIMBER",
         [_st(0, "email", "What your current leverage means at a 5% move", "Personal scenario table; disclaimer.", "09:00"), _st(3, "in-app", "Isolated vs cross, in 30 seconds", "Education; no CTA to trade.", "13:00")],
         7, "weekly", 2, 20, "liquidation_rate_30d", "−25% vs holdout", "unsubscribe_rate", 30, ["unsubscribe_rate > 0.4%", "complaints about tone > 0"],
         ["Personal 'max loss at your size' calculator", "Peer-free framing (no leaderboards)"], exclusions=DERIV_EXCL, jurisdictions=["UK", "US"], banned=("size_up", "leverage_upsell", "win_framing"), min_reach=100),
    _sop("sop_funding_crowding_nudge", "Funding crowding nudge (event-triggered, TTL 3h)", "market", "habitual_core", "Tell holders of crowded positions what funding costs per day; offer the position screen.",
         "Open position on a perp whose funding APR is beyond ±20% (family: CROWDED_POSITION)", "CROWDED_POSITION",
         [_st(0, "in-app", "Funding on your position: X%/h ≈ Y%/day", "Their side pays; time-stamped; link to position; no direction.", "12:45", ttl=3), _st(0, "push", "Same, push only for leverage ≥5x", "Fact + tool.", "12:45", "leverage >= 5", ttl=3)],
         1, "event_triggered", 3, 10, "risk_tool_open_rate", "≥ 8% of delivered", "notification_disable_rate", 3, ["notification_disable_rate > 0.3%", "regime capitulation → suppressed by policy"],
         ["Per-day cost in INR", "Send 45 min before the 13:30 funding window", "Cards digest instead of push for < 5x"], exclusions=DERIV_EXCL + ["received a market push today"], jurisdictions=["UK", "US"], disclaimer=("in-app", "push_landing"), banned=("forecast", "direction"), min_reach=50, extra_checks=["angle_policy", "ttl"]),
    _sop("sop_oi_crowding_note", "Open-interest crowding note", "market", "habitual_core", "When OI builds ≥30% with flat price, tell holders/watchers positioning is crowded; offer margin review.",
         "Open position or watchlist on a symbol in oi_movers.surge (family: OI_SURGE_WATCHERS)", "OI_SURGE_WATCHERS",
         [_st(0, "in-app", "OI +X% in 24h, price flat — crowded book", "Quote OI and price together; margin buffer tool.", "within 2h", ttl=4)],
         1, "event_triggered", 2, 10, "risk_tool_open_rate", "≥ 6% of delivered", "notification_disable_rate", 3, ["notification_disable_rate > 0.3%", "within 2h of a macro print → hold"],
         ["Pair with funding note when both fire", "Watchers get alert CTA, holders get margin CTA"], exclusions=DERIV_EXCL, jurisdictions=["UK", "US"], disclaimer=("in-app",), banned=("forecast", "direction"), min_reach=50, extra_checks=["angle_policy", "ttl"]),
    _sop("sop_macro_print_brief", "Macro print T−24h risk brief + send freeze", "risk", "habitual_core", "Before CPI/NFP/FOMC: brief leverage users, freeze promotional market sends T−2h→T+2h.",
         "Open leveraged position or leverage ≥3x in 30 days (family: LEV_USERS)", "LEV_USERS",
         [_st(0, "in-app", "CPI at 18:00 IST tomorrow: what usually happens to volatility", "Fact from calendar; review margin; no direction.", "18:00"), _st(1, "push", "Print in 2 hours — check your margin buffer", "Tool only; last message before the freeze.", "16:00")],
         2, "event_triggered", 2, 10, "sl_tp_set_rate_24h", "+5 pp vs holdout", "liquidation_rate_print_night", 3, ["regime capitulation → service only"],
         ["Post-print factual recap only if the move is > 2σ", "Calendar-driven business event"], exclusions=DERIV_EXCL, jurisdictions=["UK", "US"], disclaimer=("in-app", "push_landing"), banned=("forecast", "direction"), min_reach=100, extra_checks=["calendar"]),
    _sop("sop_tokenised_after_hours", "Markets that never close (tokenised perps after US hours)", "education", "habitual_core", "Educate watchers that NVDA/SPX/gold trade 24/7 here when an after-hours move makes it self-evident.",
         "Watchlist contains a tokenised name or traded a builder dex; equity/index/commodity mover ≥3% while US closed (family: TOKENISED_WATCHERS)", "TOKENISED_WATCHERS",
         [_st(0, "push", "NVDA moved 4.1% after US close — it trades 24/7 here", "Fact; 'on CoinDCX' wording (never name the liquidity venue); product education CTA.", "08:00", ttl=4), _st(0, "in-app", "How 24/7 pricing works before Wall Street opens", "Education; disclaimer block.", "on_open")],
         1, "event_triggered", 3, 20, "tokenised_first_fill_rate_14d", "+2 pp vs holdout", "notification_disable_rate", 14, ["regime capitulation/high_volatility_down → hold"],
         ["Weekend edition Saturday 11:00", "Earnings-week variant once a calendar source exists"], exclusions=DERIV_EXCL, jurisdictions=["UK", "US"], banned=("trade_earnings", "forecast"), extra_checks=["angle_policy"]),
    _sop("sop_new_listing_watchers", "New listing → watchers (factual, TTL 24h)", "market", "activated_habitual", "Announce a new spot/perp listing to the people who follow the sector; watchlist add is the CTA.",
         "Watchlist or holdings in the listed asset's sector; habitual traders (family: LISTING_WATCHERS)", "LISTING_WATCHERS",
         [_st(0, "in-app", "X is now tradable as spot / as a perp", "Fact; watchlist CTA; no 'early' language.", "11:00", ttl=24), _st(0, "push", "Push only to explicit watchers of X", "Same fact.", "11:00", "watchlist contains X", ttl=24)],
         1, "event_triggered", 3, 20, "watchlist_add_rate", "≥ 3% of delivered", "notification_disable_rate", 7, ["regime stress → blocked by policy", "1 listing message per user per week exceeded → stop"],
         ["Sector-based audience from held symbols", "Delisting notice variant (compliance tone)"], exclusions=DERIV_EXCL, banned=("listing_pump", "early", "fomo"), extra_checks=["angle_policy"]),
    _sop("sop_fee_tier_nudge", "Fee-tier distance nudge", "retention", "habitual_core", "Users within 15% of the next tier learn the exact distance and the saving; nothing else.",
         "30-day volume within 15% of the next fee tier (family: FEE_TIER_NEAR)", "FEE_TIER_NEAR",
         [_st(0, "email", "You are ₹X of volume from tier Y — here is the saving", "Own numbers; fee table; disclaimer footer.", "09:00"), _st(5, "in-app", "Tier progress card", "Progress only; no urgency.", "on_open", "still below tier")],
         10, "monthly", 1, 20, "fee_tier_upgrade_rate", "+3 pp vs holdout", "post_month_volume_drop_pct", 30, ["complaints about pressure > 0", "regime stress → hold"],
         ["Tier progress as a persistent card", "Combine with monthly statement"], banned=("win_framing", "size_up")),
    _sop("sop_hedger_education", "Hedging education for concentrated spot holders", "education", "habitual_core", "Spot holders with >40% in one asset during volatile regimes learn how a small hedge works; education only.",
         "Spot concentration >40% in one asset; regime high_volatility_* (family: CONCENTRATED_SPOT)", "CONCENTRATED_SPOT",
         [_st(0, "email", "Your portfolio is 62% BTC — what a small hedge does", "Personal numbers; hedge calculator; disclaimer.", "09:00"), _st(3, "in-app", "Hedge calculator walkthrough", "Tool; no direction.", "13:00", "opened step 0")],
         7, "monthly", 2, 20, "hedge_calculator_use_rate", "≥ 5% of delivered", "unsubscribe_rate", 14, ["unsubscribe_rate > 0.4%"], ["Only in high-volatility regimes", "Show basis/funding view"],
         exclusions=DERIV_EXCL, jurisdictions=["UK", "US"], banned=("direction", "forecast", "size_up")),
    _sop("sop_slipping_checkin", "Slipping check-in (decline vs own baseline)", "retention", "slipping", "Users whose trade frequency fell vs their own baseline get a light, cause-aware check-in; cadence goes down, not up.",
         "Trade frequency −50% vs own 8-week baseline, no liquidation, still active in app (family: SLIPPING)", "SLIPPING",
         [_st(0, "email", "Portfolio review: your numbers this month", "Own numbers; one insight; no promo.", "09:00"), _st(7, "in-app", "One tool you have not used (alerts/SIP / recurring buy)", "Feature discovery; dismissible.", "on_open")],
         14, "monthly", 1, 20, "trade_frequency_recovery_14d", "+3 pp vs holdout", "unsubscribe_rate", 21, ["unsubscribe_rate > 0.4%"], ["Split by cause: market vs friction vs loss", "Cards instead of email for email-ignorers"]),
    _sop("sop_loss_dormant_service", "Loss-dormant service track (no promos, ever)", "retention", "dormant_activated", "Users dormant after a large realised loss get service and education only; the KPI is measured, not pushed.",
         "Realised loss >20% of deposits and no trade in 21+ days (family: LOSS_DORMANT)", "LOSS_DORMANT",
         [_st(0, "email", "Your account, your statement, our support", "Service tone; statement; support link; education library; disclaimer.", "10:00"), _st(30, "email", "Risk tools we added since", "Education; no CTA to trade.", "10:00")],
         30, "monthly", 1, 20, "reactivation_rate_60d", "measure only; support_contact_rate as guardrail", "support_contact_rate", 60, ["any promotional content detected → stop", "complaints > 0"],
         ["Human outreach for very high prior value", "Tax-loss explainer in March"], exclusions=["unsubscribed / DND"], banned=("promo", "market", "fomo", "win_framing"), min_reach=50),
    _sop("sop_friction_dormant_fixed", "Friction-dormant: the thing that broke is fixed", "winback", "dormant_activated", "Users who left after a deposit/KYC/withdrawal failure hear that it is fixed, with a direct link.",
         "Dormant 14–60 days whose last session ended in a failure event (family: FRICTION_DORMANT)", "FRICTION_DORMANT",
         [_st(0, "whatsapp", "The deposit issue you hit is fixed — 20-second retry", "Utility template; direct link; support.", "12:00"), _st(4, "push", "Habit tool re-entry: one alert", "Fact + tool.", "19:00", "app_opened since step 0")],
         7, "monthly", 2, 20, "reactivation_rate_14d", "+4 pp vs holdout", "unsubscribe_rate", 21, ["unsubscribe_rate > 0.6%"], ["Per-failure-class copy", "Test push vs WhatsApp first touch"], disclaimer=("whatsapp",)),
    _sop("sop_market_dormant_return", "Market-dormant return (regime-gated facts)", "winback", "dormant_activated", "Users who left in a drawdown get facts about what changed, only when the regime is trending_up or chop.",
         "Dormant 30–90 days, no loss event, left during trending_down/capitulation (family: MARKET_DORMANT)", "MARKET_DORMANT",
         [_st(0, "email", "What changed since you last logged in (facts, no outlook)", "Regime facts; features shipped; disclaimer.", "09:00"), _st(3, "push", "Re-enter with a watchlist, not a trade", "Habit tool.", "19:00")],
         7, "monthly", 2, 20, "reactivation_rate_14d", "+2 pp vs holdout (market steals credit — holdout mandatory)", "unsubscribe_rate", 21, ["regime flips to stress → pause", "unsubscribe_rate > 0.6%"],
         ["Sunday evening send", "Own-portfolio change as the lead number"], banned=("fomo", "win_framing", "forecast"), extra_checks=["regime_allows"]),
    _sop("sop_full_withdrawal_service", "Full-balance withdrawal → service check-in", "retention", "slipping", "A full withdrawal is a churn signal; respond with service, a feedback ask and nothing promotional.",
         "withdrawal_completed with is_full_balance in the last 3 days (family: FULL_WITHDRAWAL)", "FULL_WITHDRAWAL",
         [_st(1, "email", "Your withdrawal is complete — anything we should fix?", "Confirmation; 1-question feedback; support; disclaimer.", "10:00")],
         1, "event_triggered", 1, 20, "feedback_response_rate", "≥ 8%", "complaint_rate", 14, ["complaints > 0"], ["Route 'fees' answers to the fee-tier SOP next month", "Route 'bug' answers to support with the ticket pre-filled"],
         exclusions=["unsubscribed / DND"], disclaimer=("email",), banned=("promo", "retention_offer"), min_reach=20),
    _sop("sop_reactivation_90d_plus", "90-day+ reactivation (quarterly, email only)", "winback", "dormant_activated", "Long-dormant users get one quarterly email with own numbers and what changed; nothing else.",
         "No session in 90+ days, email opt-in, not loss-dormant (family: DORMANT_D90)", "DORMANT_D90",
         [_st(0, "email", "It has been a while: your account, what changed, how to come back", "Own numbers; features; one CTA; disclaimer.", "10:00")],
         1, "quarterly", 1, 20, "reactivation_rate_30d", "+1 pp vs holdout", "unsubscribe_rate", 30, ["unsubscribe_rate > 0.8%"], ["Sunset after two ignored quarters", "Regional language variant"], disclaimer=("email",), min_reach=1000),
    _sop("sop_referral_program", "Referral (two-sided, terms first, not UK)", "competition", "habitual_core", "Invite habitual users to refer; rewards are in-product and compliant; never to UK users.",
         "Habitual traders with NPS-positive signal or 3+ months tenure (family: HABITUAL_REFERRERS)", "HABITUAL_REFERRERS",
         [_st(0, "in-app", "Refer a friend: how it works and the terms", "Complete terms; no 'earn money' framing; disclaimer.", "13:00"), _st(7, "email", "Your referrals so far", "Own numbers; terms link.", "09:00", "referral_sent >= 1")],
         14, "quarterly", 1, 20, "referral_conversion_rate", "≥ 2% of invited refer a converted user", "fraud_flag_rate", 30, ["fraud_flag_rate > 1%", "UK/US recipient → stop"],
         ["Reward = fee credits, not cash", "Cap rewards per referrer per month"], jurisdictions=["UK", "US"], banned=("incentive_to_uk", "earn_money", "guaranteed"), extra_checks=["jurisdiction", "terms_approved"]),
    _sop("sop_feature_launch_adoption", "Feature launch → adoption (beta, announce, adopt, habit)", "education", "activated_habitual", "Launch a feature through lifecycle channels with an adoption KPI and an intent-only nudge.",
         "Target trader states for the feature; beta cohort 1–5% opt-in first (family: FEATURE_TARGET)", "FEATURE_TARGET",
         [_st(-7, "in-app", "Beta invite (opt-in)", "Invite; instrument feature_used / feature_abandoned.", "13:00"), _st(0, "in-app", "Announce: one job, one screen", "Card to target states; disclaimer if relevant.", "11:00"),
          _st(0, "email", "How-to for the feature", "Screens + one CTA.", "09:00"), _st(3, "push", "Intent nudge (viewed, not used)", "One most valuable use.", "19:00", "feature screen viewed and feature_used = 0")],
         14, "once_per_cohort_version", 3, 20, "feature_used_7d_rate", "≥ 15% of exposed", "support_ticket_rate", 30, ["feature_abandoned at one step > 40% → pause and fix"],
         ["Adopters vs matched non-adopters retention (say 'associated')", "Recap includes the feature's own number at day 14"], min_reach=500),
    _sop("sop_incident_service_comms", "Incident / status communications", "compliance", "activated_habitual", "Tell affected users before they ask; factual status, ETA, what is unaffected; no marketing for 24h after.",
         "Users active in the last 24h or with open orders during an incident (family: ACTIVE_24H)", "ACTIVE_24H",
         [_st(0, "in-app", "Status: what is affected, what is not", "Factual; ETA; support.", "+10m"), _st(0, "push", "Push only if funds/orders affected", "Same facts.", "+15m", "orders or withdrawals affected"), _st(1, "email", "Post-incident summary", "What happened, what changed.", "10:00")],
         2, "event_triggered", 3, 5, "support_contacts_avoided", "support_ticket_rate ≤ 1.5× baseline", "complaint_rate", 3, ["misleading status → correct within 30 min"],
         ["Status page link everywhere", "Freeze all promos 24h (autopilot protect)"], exclusions=[], disclaimer=(), banned=("promo",), min_reach=1),
    _sop("sop_tax_season_explainer", "Tax season explainer (India, 1–15 July)", "education", "activated_habitual", "Explain 30% tax, TDS statements and where to download reports; never tax advice.",
         "All KYC-approved users with any 2025–26 activity (family: ACTIVE_FY)", "ACTIVE_FY",
         [_st(0, "email", "Your FY statement and TDS certificate are ready", "Facts; download link; 'consult a tax professional'; disclaimer.", "09:00"), _st(10, "in-app", "Reminder: ITR deadline 31 July", "Factual reminder; link to statement.", "on_open")],
         15, "monthly", 1, 5, "statement_download_rate", "≥ 30%", "support_ticket_rate", 30, ["misleading tax wording → correct immediately"], ["Budget-day variant (1 Feb) prepared in advance", "March tax-loss education (no advice)"], min_reach=1000),
    _sop("sop_regime_stress_mode", "Regime stress mode (protect)", "compliance", "slipping", "On capitulation / high-volatility-down: pause acquisition and upsell, tighten caps, one calm service message.",
         "Everyone; promotional suppression is the action (family: *)", "*",
         [_st(0, "in-app", "Markets are volatile; trading and withdrawals are normal; risk tools here", "Service tone; no market commentary.", "+30m")],
         1, "event_triggered", 1, 5, "notification_disable_rate", "≤ baseline", "support_ticket_rate", 3, ["regime back to normal → lift suppression"], ["Business event market_regime_changed", "Support macro in-app"],
         exclusions=[], disclaimer=("in-app",), banned=("promo", "market", "fomo"), min_reach=0),
    _sop("sop_vip_concierge_quarterly", "VIP concierge quarterly (human + one email)", "retention", "habitual_core", "Top-value users get a quarterly human check-in and one statement; no campaigns otherwise.",
         "VHVT / top 100 by 90-day volume (family: VHVT)", "VHVT",
         [_st(0, "email", "Quarterly statement and a named contact", "Own numbers; direct line; disclaimer.", "09:00")],
         1, "quarterly", 1, 20, "weekly_active_weeks_12w", "≥ +0.5 vs holdout", "unsubscribe_rate", 90, ["unsubscribe > 0", "complaints > 0"], ["Fee review conversation", "Early access to features (beta SOP)"], disclaimer=("email",), min_reach=20),
    _sop("sop_weekend_tokenised", "Weekend: markets that stay open (Saturday education)", "education", "habitual_core", "Weekend traders learn that equities/indices/commodities perps trade through the weekend here.",
         "≥2 weekend fills in 4 weeks, no builder-dex fill yet (family: WEEKEND_TRADERS)", "WEEKEND_TRADERS",
         [_st(0, "push", "Saturday: gold and SPX are trading here right now", "Fact; 'on CoinDCX'; education CTA.", "11:00", ttl=6)],
         1, "weekly", 1, 20, "tokenised_first_fill_rate_14d", "+2 pp vs holdout", "notification_disable_rate", 14, ["regime stress → hold"], ["Rotate the featured market by the user's watchlist"],
         exclusions=DERIV_EXCL, jurisdictions=["UK", "US"], disclaimer=("push_landing",), banned=("forecast", "trade_earnings")),
    _sop("sop_channel_recovery_push_off", "Channel recovery: push disabled → email/cards", "retention", "activated_habitual", "Users who disabled push move to Cards and a lighter email cadence; win back the channel only with value.",
         "notification_disabled{push} in the last 30 days, still active (family: PUSH_OFF_ACTIVE)", "PUSH_OFF_ACTIVE",
         [_st(0, "cards", "Your watchlist today (persistent card)", "Own numbers; no interruption.", "08:00"), _st(7, "email", "Alerts without noise: how to turn on only what matters", "Granular settings explainer; disclaimer.", "09:00")],
         14, "monthly", 2, 20, "push_reenable_rate_30d", "+3 pp vs holdout", "unsubscribe_rate", 30, ["unsubscribe_rate > 0.4%"], ["Granular notification categories", "Cards-only recap as the default for this cohort"]),
    _sop("sop_pm_pillar_message_test", "Product marketing: pillar message-market-fit test", "education", "activated_habitual", "Find the lead message per pillar (transparency / control / access / service) per trader state; the winner becomes canonical.",
         "One trader state at a time (spot-only, first-perp, habitual, tokenised explorer); 20% holdout (family: PM_TEST_STATE)", "PM_TEST_STATE",
         [_st(0, "in-app", "Pillar lead message (bottom sheet, one job, one CTA)", "Variant per pillar; measure click → feature_used 7d; disclaimer if derivatives.", "13:00"), _st(7, "email", "Winner recap: the feature, the proof point", "Own numbers where possible.", "09:00", "clicked step 0")],
         14, "monthly", 2, 20, "feature_used_7d_rate", "winner ≥ +3 pp vs next variant", "notification_disable_rate", 21, ["any variant makes a return/leverage claim → stop"],
         ["Rotate pillar per month", "Record losers in the growth feed so nobody re-tests them", "Regional language variants for hi/te/ta"], min_reach=1000),
    _sop("sop_asset_spotlight", "Asset spotlight (factual asset push to watchers and sector)", "market", "activated_habitual", "Put one asset in front of the people who follow it or its sector with verifiable facts and a tool — never a recommendation.",
         "Watchlist/holders of the asset or its sector; active 30d; regime trending_up or chop (family: ASSET_WATCHERS)", "ASSET_WATCHERS",
         [_st(0, "push", "The fact: listing / volume record / 24h move / product availability", "Time-stamped number; watchlist or alert CTA; TTL 4h.", "11:00", ttl=4), _st(0, "in-app", "Asset page card: what it is, fees, risk", "Education; disclaimer block.", "on_open"),
          _st(2, "email", "Deep-dive explainer (what it is, how it trades here)", "Education, no outlook; disclaimer footer.", "09:00", "clicked step 0 or 1")],
         3, "event_triggered", 3, 20, "watchlist_add_rate", "≥ 3% of delivered; first-trade rate read vs holdout", "notification_disable_rate", 14, ["regime stress → blocked", "any 'buy' or forecast wording → stop", "1 spotlight per user per week exceeded → stop"],
         ["Sector rotation by held symbols", "Tokenised equity spotlight around US after-hours moves", "Commodity spotlight (gold/silver) in high-volatility regimes as education"],
         exclusions=DERIV_EXCL + ["received a market push today"], banned=("buy", "forecast", "fomo", "listing_pump"), extra_checks=["angle_policy", "ttl"]),
    _sop("sop_cross_sell_spot_to_perps", "Cross-sell spot → perps (intent-gated, education-first)", "education", "habitual_core", "Habitual spot traders who show perp intent get the education path; no perps promotion to anyone else.",
         "≥8 spot fills / 4 weeks AND perp screen viewed ≥2× in 14d, 0 perp fills, not UK/US (family: SPOT_HABITUAL_PERP_INTENT)", "SPOT_HABITUAL_PERP_INTENT",
         [_st(0, "in-app", "Bottom sheet: what a perp is, isolated margin default, 'keep to spot' exit", "Education; disclaimer block.", "13:00"), _st(3, "email", "Funding and liquidation, plainly, with the calculator", "Education; disclaimer.", "10:00", "opened step 0"),
          _st(7, "in-app", "Hygiene checklist if you start: 1–2x, SL, isolated", "No CTA to trade.", "13:00", "opened step 1")],
         7, "monthly", 3, 20, "first_perp_trade_rate_30d", "opt-in cohort; liquidation_rate_30d ≤ baseline", "liquidation_rate_30d", 30, ["liquidation_rate_30d treated > holdout", "UK/US recipient → stop"],
         ["Quiz gate", "Paper-trade demo first"], exclusions=DERIV_EXCL, jurisdictions=["UK", "US"], banned=("leverage_upsell", "size_up", "first_futures_trade_promo"), min_reach=100, extra_checks=["jurisdiction"]),
    _sop("sop_cross_sell_crypto_to_tokenised", "Cross-sell crypto perps → tokenised equities/indices", "education", "habitual_core", "Habitual crypto-perp traders who trade in US hours or hold equity watchlists learn about 24/7 tokenised markets.",
         "≥8 perp fills / 4 weeks, ≥30% of fills 19:00–01:30 IST or equity watchlist, 0 builder-dex fills (family: PERP_HABITUAL_US_HOURS)", "PERP_HABITUAL_US_HOURS",
         [_st(0, "push", "NVDA / SPX / gold trade 24/7 here — same account", "Fact; 'on CoinDCX'; product education CTA.", "18:00", ttl=6), _st(2, "in-app", "How tokenised perps price after hours", "Education; disclaimer block.", "on_open"), _st(5, "email", "Markets list, hours, fees, risks", "Education; disclaimer footer.", "09:00")],
         7, "monthly", 3, 20, "tokenised_first_fill_rate_14d", "+3 pp vs holdout", "notification_disable_rate", 21, ["regime stress → hold"], ["Earnings-week variant once a calendar source exists", "Commodities variant for high-vol regimes"],
         exclusions=DERIV_EXCL, jurisdictions=["UK", "US"], banned=("trade_earnings", "forecast", "direction")),
    _sop("sop_cross_sell_to_commodities", "Cross-sell → commodities perps (gold/silver as education in volatile regimes)", "education", "habitual_core", "During high-volatility regimes, explain how gold/silver/oil perps work to habitual traders; education only.",
         "Habitual traders (any product) with no commodity fill; regime high_volatility_* (family: HABITUAL_NO_COMMODITY)", "HABITUAL_NO_COMMODITY",
         [_st(0, "in-app", "Gold and silver perps: how they trade here", "Education; 'on CoinDCX'; disclaimer block.", "13:00"), _st(3, "email", "Commodities primer: hours, funding, risks", "Education; disclaimer footer.", "09:00", "opened step 0")],
         7, "monthly", 2, 20, "commodity_first_fill_rate_14d", "+2 pp vs holdout", "unsubscribe_rate", 21, ["unsubscribe_rate > 0.4%"], ["Only when commodity movers ≥3%", "Silver/platinum variant"],
         exclusions=DERIV_EXCL, jurisdictions=["UK", "US"], banned=("safe_haven_claim", "forecast", "direction")),
    _sop("sop_cross_sell_earn", "Cross-sell → earn / stablecoin products (idle balance, variable APR)", "retention", "habitual_core", "Users with idle balances learn about earn products; APR is variable and never framed as income.",
         "Idle balance > ₹10k for 14+ days, no earn subscription (family: IDLE_BALANCE)", "IDLE_BALANCE",
         [_st(0, "email", "Your idle balance and how earn works (variable APR, risks)", "Own numbers; 'variable, not guaranteed'; disclaimer footer.", "09:00"), _st(4, "in-app", "Earn explainer bottom sheet", "One product; risks; disclaimer.", "on_open", "opened step 0")],
         7, "monthly", 2, 20, "earn_subscription_rate_14d", "+2 pp vs holdout", "unsubscribe_rate", 21, ["any 'guaranteed'/'passive income' wording → stop"], ["Trending_down regime timing", "Stablecoin vs token earn split"],
         banned=("guaranteed", "passive_income", "yield_as_income")),
    _sop("sop_cross_sell_recurring_buy", "Cross-sell spot → SIP / recurring buy (habit product)", "activation", "activated_habitual", "Spot traders with irregular buys learn SIP / recurring buy as a habit tool; no asset named by us.",
         "2–6 spot buys in 60 days, no recurring plan (family: SPOT_IRREGULAR)", "SPOT_IRREGULAR",
         [_st(0, "in-app", "SIP: set once, from ₹100, pause anytime", "Tool; user picks the asset; disclaimer block.", "13:00"), _st(3, "push", "Salary week: set it up in 20 seconds", "Only 1–7 of the month.", "10:30", "no recurring plan")],
         7, "monthly", 2, 20, "recurring_plan_rate_14d", "+3 pp vs holdout", "notification_disable_rate", 21, ["notification_disable_rate > 0.3%"], ["DCA education (no advice)", "Amount presets by deposit history"], banned=("asset_recommendation", "forecast")),
    _sop("sop_bottom_sheet_onboarding_tour", "Bottom-sheet onboarding tour (in-app only, first 3 sessions)", "onboarding", "funded_activated", "New users get one bottom sheet per session for three sessions: deposit, watchlist, first trade — dismissible, never blocking.",
         "Signed up < 7 days, ≤ 3 sessions (family: NEW_SESSIONS)", "NEW_SESSIONS",
         [_st(0, "in-app", "Session 1: deposit in 20 seconds (bottom sheet)", "One job; dismiss; disclaimer.", "on_open"), _st(1, "in-app", "Session 2: build a watchlist", "One job.", "on_open", "session_count = 2"), _st(2, "in-app", "Session 3: your first ₹100 trade", "One job; no asset named.", "on_open", "session_count = 3")],
         7, "event_triggered", 3, 20, "first_trade_rate_7d", "+3 pp vs holdout", "session_abandon_rate", 14, ["session_abandon_rate up > 20% → stop"], ["Skip a step already completed", "Regional language sheets"],
         exclusions=["KYC pending"], disclaimer=("in-app",), min_reach=300),
    _sop("sop_whatsapp_utility_journey", "WhatsApp utility journey (opted-in users, templates only)", "onboarding", "verified_funded", "For WhatsApp-opted-in users, run the deposit and KYC help journey on approved utility templates; no marketing templates.",
         "WhatsApp opt-in, KYC approved, no deposit (family: WA_OPTIN_NODEP)", "WA_OPTIN_NODEP",
         [_st(0, "whatsapp", "Account ready: deposit steps (utility template)", "Approved template; steps; support.", "12:00"), _st(3, "whatsapp", "Deposit help / failure recovery", "Utility; alternatives.", "12:30", "deposit_initiated without completed"), _st(6, "whatsapp", "Withdrawals are instant — the common worry", "Utility tone; facts.", "12:00", "still no deposit")],
         8, "event_triggered", 2, 10, "first_deposit_rate_7d", "+2 pp vs email-only holdout", "opt_out_rate", 14, ["opt_out_rate > 1%", "template rejected → stop"], ["Hindi templates", "Voice-note style short copy"],
         disclaimer=("whatsapp",), banned=("promo_template",), extra_checks=["channel_templates_approved"]),
    _sop("sop_email_education_series", "Email education series (5 parts, weekly)", "education", "activated_habitual", "A five-part weekly series: fees & TDS, alerts, watchlists, risk basics, reading a statement. Opt-in, unsubscribe any time.",
         "Email opt-in, active 30d, not in another series (family: EMAIL_LEARNERS)", "EMAIL_LEARNERS",
         [_st(0, "email", "Part 1: fees and TDS", "Transparency; disclaimer.", "09:00"), _st(7, "email", "Part 2: alerts", "Tool.", "09:00"), _st(14, "email", "Part 3: watchlists", "Tool.", "09:00"), _st(21, "email", "Part 4: risk basics", "Education; derivatives disclaimer block.", "09:00"), _st(28, "email", "Part 5: reading your statement", "Own numbers.", "09:00")],
         35, "once_per_cohort_version", 1, 20, "sessions_per_week", "+0.2 vs holdout", "unsubscribe_rate", 42, ["unsubscribe_rate > 0.4% on any part → pause series"], ["Cards mirror of each part", "Regional language series"], disclaimer=("email",), min_reach=2000),
    _sop("sop_push_alert_digest", "Push alert digest (own assets, once a day max)", "retention", "activated_habitual", "One daily push at most that bundles the user's own alerts and watchlist moves; replaces multiple pings.",
         "Alert-setters and watchlist users with ≥3 tracked assets (family: WATCHLIST_HEAVY)", "WATCHLIST_HEAVY",
         [_st(0, "push", "Your day: 3 assets moved >5%, 1 alert hit", "Own numbers; one screen; TTL 4h.", "19:00", ttl=4)],
         1, "weekly", 5, 20, "session_rate_after_digest", "+5 pp vs individual pushes", "notification_disable_rate", 14, ["notification_disable_rate > 0.2%"], ["Cards version for push-off users", "Threshold personalised to volatility"],
         exclusions=DERIV_EXCL + ["received a market push today"], disclaimer=("push_landing",), banned=("forecast", "direction"), extra_checks=["ttl"]),
    _sop("sop_category_explorer", "Category explorer (viewed a category, no trade) → guided first step", "activation", "activated_habitual", "Users who browse a category (spot / perps / tokenised / earn) without acting get one guided, compliant first step for that category.",
         "≥3 views of one category screen in 7 days, 0 actions in it (family: CATEGORY_INTENT)", "CATEGORY_INTENT",
         [_st(0, "in-app", "Bottom sheet for the category: what it is, first step, risks", "Category-specific; derivatives → education path only; disclaimer.", "on_open"), _st(3, "push", "The one tool for that category (watchlist / calculator / plan)", "Fact + tool.", "19:00", "still no action")],
         5, "event_triggered", 2, 20, "category_first_action_rate_7d", "+3 pp vs holdout", "notification_disable_rate", 14, ["derivatives category to UK/US → stop"], ["Per-category copy bank", "Intent decay: stop after 7 days"],
         exclusions=DERIV_EXCL, banned=("leverage_upsell", "asset_recommendation"), extra_checks=["jurisdiction"]),
    _sop("sop_global_announcement_lenses", "Tier-0 global announcement with product lenses (major move / event)", "compliance", "activated_habitual",
         "When something big happens (major move, geopolitical or regulatory event, incident), every product cohort gets the same verified fact with its own lens; promotional angles are off.",
         "All active users, split by product affinity: spot, SIP, crypto perps, US-stock/index/commodity perps, options, earn, web3 (family: *)", "*",
         [_st(0, "in-app", "Bottom sheet: the fact + your product lens", "Core fact (one sentence, time-stamped) + lens per cohort from announcement_lenses; service tone; disclaimer block.", "+30m"),
          _st(0, "push", "Push only where a tool applies (perps: margin; spot: alerts; web3: safety)", "Fact + tool; TTL 4h; 1 push.", "+45m", "cohort has a tool to act on", ttl=4),
          _st(0, "email", "Same-day summary for email opt-ins", "Fact, what it means per product, what is unaffected, support; disclaimer footer.", "18:00"), _st(1, "cards", "Persistent recap card", "Facts and links; no outlook.", "09:00")],
         2, "event_triggered", 4, 5, "support_contacts_avoided", "support_ticket_rate ≤ 1.5× baseline; notification_disable_rate ≤ baseline", "notification_disable_rate", 7,
         ["any lens contains a forecast or direction → stop", "regime capitulation → in-app + email service copy only", "notification_disable_rate > 0.3%"],
         ["Lens copy bank per product", "Auto-trigger from |BTC 24h| ≥ 8%, regime flip, or ≥3 risk headlines with geopolitical/regulatory words", "Hindi variant of the core fact"],
         exclusions=["unsubscribed / DND", "liquidated 14d → in-app service card only", "loss-dormant → email only"], banned=("forecast", "direction", "fomo", "promo", "venue_name"), min_reach=0, extra_checks=["regime_allows"]),
    _sop("sop_geopolitical_event_brief", "Geopolitical / macro shock brief (facts, risk tools, silence on promos)", "risk", "slipping",
         "On war, sanctions, major regulatory or macro shocks: one factual brief with risk tools; freeze promotions 24h; commodities/indices cohorts get the macro context lens.",
         "All active users; perps/options/commodities cohorts first (family: *)", "*",
         [_st(0, "in-app", "What happened, what it usually does to volatility, your tools", "Facts from reputable sources (no links to venues); margin/alerts/SIP-continues lens; disclaimer.", "+1h"),
          _st(1, "email", "Day-after brief: what changed, what did not, support", "Service tone; no outlook.", "10:00")],
         2, "event_triggered", 2, 5, "support_contacts_avoided", "support_ticket_rate ≤ 1.5× baseline", "complaint_rate", 7, ["promotional send detected during the freeze → stop and report"],
         ["Commodity/index lens for gold/oil/SPX cohorts", "Options expiry note if within 48h", "Autopilot protect mission handles suppression"],
         exclusions=["unsubscribed / DND"], banned=("forecast", "direction", "promo", "safe_haven_claim"), min_reach=0),
    _sop("sop_web3_trending_watch", "Web3 trending watch (unverified tokens, education + watchlist only)", "market", "activated_habitual",
         "Web3 users see what is trending on their chains as volume/liquidity facts with a safety line; never a pick, never paid boosts.",
         "Web3-active users (swap/wallet event 30d) split by chain: Solana, Base, BNB Chain, Ethereum, Robinhood Chain (family: WEB3_ACTIVE)", "WEB3_ACTIVE",
         [_st(0, "in-app", "Trending on Solana today: X, Y (24h volume, liquidity) — unverified tokens", "Quality-gated list only; 'unverified'; slippage/contract risk line; watchlist CTA; disclaimer.", "12:00", ttl=6),
          _st(0, "push", "Push once a day at most, only to users who opted into trend alerts", "Same facts; TTL 6h.", "12:15", "web3_trend_alerts opt-in", ttl=6)],
         1, "event_triggered", 5, 20, "watchlist_add_rate", "≥ 3% of delivered; complaint_rate ≤ baseline", "complaint_rate", 7, ["regime stress → blocked by policy", "any boosted (paid) token included → stop", "complaint about a scam token → stop and review the gate"],
         ["Chain-specific timing (US hours for Base/ETH, Asia for Solana)", "Weekly 'what trended and what happened after' education", "Safety quiz before enabling trend alerts"],
         exclusions=DERIV_EXCL + ["received a web3 trend message today"], banned=("token_pick", "100x", "airdrop_hype", "venue_name"), extra_checks=["angle_policy", "ttl"]),
    _sop("sop_web3_onboarding_safety", "Web3 onboarding and safety (wallet, gas, slippage, scams)", "onboarding", "funded_activated",
         "New Web3 users get a 5-day safety-first onboarding: wallet basics, gas, slippage, contract risk, watchlist — before any trend content.",
         "Web3 wallet created < 7 days, ≤ 1 swap (family: WEB3_NEW)", "WEB3_NEW",
         [_st(0, "in-app", "Your wallet: what is custodial vs on-chain, how to stay safe", "Education; disclaimer.", "+1h"), _st(1, "push", "Gas and slippage in 30 seconds", "Tool: slippage setting; deep link.", "19:00"),
          _st(3, "email", "How to spot a scam token (checklist)", "Education; report link; disclaimer.", "09:00"), _st(5, "in-app", "Build a chain watchlist", "Habit tool; no picks.", "on_open")],
         6, "event_triggered", 4, 20, "second_swap_rate_14d", "+3 pp vs holdout with complaint_rate guardrail", "complaint_rate", 14, ["complaints > 0 about content", "notification_disable_rate > 0.3%"],
         ["Chain-specific gas explainer", "Hindi variant"], exclusions=["unsubscribed / DND"], banned=("token_pick", "airdrop_hype")),
    _sop("sop_product_cohort_monthly", "Product cohort monthly programme (each cohort gets its own product content)", "retention", "habitual_core",
         "Every month each product cohort receives its product's programme (pillars, cadence, never-list from the product matrix); cross-sell happens only on intent signals.",
         "Product cohorts from segment_study by_product: spot, SIP, crypto perps, US-stock perps, indices, commodities, options, earn, web3 (family: *)", "*",
         [_st(0, "email", "(per cohort) Monthly statement with the product's own numbers", "Own numbers; product pillars; disclaimer footer.", "09:00"), _st(7, "in-app", "(per cohort) One product tool or explainer", "From the cohort's pillars; bottom sheet.", "13:00"),
          _st(14, "push", "(per cohort) One product-specific habit nudge", "Fact + tool; product never-list respected.", "19:00"), _st(21, "in-app", "(intent only) Cross-sell path for users with intent signals", "Only cross_sell_on_intent_only paths; education-first for derivatives.", "13:00", "intent signal present")],
         30, "monthly", 1, 20, "weekly_active_weeks_4w", "+0.3 vs holdout per cohort", "unsubscribe_rate", 30, ["unsubscribe_rate > 0.4% in any cohort → pause that cohort", "a cohort exceeds its product cadence → stop"],
         ["Per-product copy banks", "Cohort-level peace index review before step 3", "Retire steps that a cohort ignores two months running"], extra_checks=["previous_version_readout"]),
    _sop("sop_options_education", "Options education (defined risk, expiry notes)", "education", "habitual_core",
         "Options users and intent users get defined-risk education and expiry-day notes; no strategy recommendations, no 'cheap premium' lures.",
         "Options traders or options screen viewed ≥2× in 14d, not UK/US (family: OPTIONS_USERS)", "OPTIONS_USERS",
         [_st(0, "in-app", "Max loss, max gain: how defined risk works", "Education; disclaimer block.", "13:00"), _st(2, "email", "Expiry, IV and time decay in plain words", "Education; calendar; disclaimer.", "09:00", "opened step 0"), _st(6, "push", "Expiry day tomorrow: check open positions", "Tool; no direction.", "18:00", "open options position and expiry within 24h")],
         7, "monthly", 3, 20, "options_risk_tool_open_rate", "≥ 6% of delivered", "complaint_rate", 21, ["strategy tip detected in copy → stop"], ["Greeks glossary card", "Paper-trade options demo"],
         exclusions=DERIV_EXCL, jurisdictions=["UK", "US"], banned=("strategy_tip", "cheap_premium", "direction")),
    _sop("sop_sip_nurture", "SIP nurture (plan continuity through volatility)", "retention", "activated_habitual",
         "SIP users get continuity and DCA education, especially when markets fall; never a prompt to time or pause the plan.",
         "Active SIP plan (family: SIP_ACTIVE)", "SIP_ACTIVE",
         [_st(0, "email", "Your SIP this month: units, average cost, next date", "Own numbers; DCA education; disclaimer.", "09:00"), _st(10, "in-app", "Markets fell this week: what it means for a recurring buyer (education)", "Education, no advice; only in trending_down/high_vol_down.", "13:00", "regime trending_down or high_volatility_down"),
          _st(20, "push", "Next SIP tomorrow — balance check", "Utility; deposit link if balance short.", "10:30", "balance < next SIP amount")],
         30, "monthly", 1, 20, "sip_continuation_rate_90d", "+2 pp vs holdout", "sip_pause_rate", 90, ["sip_pause_rate treated > holdout"], ["Salary-week alignment", "Annual SIP statement in April"], banned=("market_timing", "pause_suggestion", "asset_recommendation")),
]

def init_sop_tables() -> None:
    conn = get_db()
    conn.execute("""CREATE TABLE IF NOT EXISTS sops (id TEXT PRIMARY KEY, name TEXT, campaign_type TEXT, spec_json TEXT, source TEXT, version INTEGER DEFAULT 1, active INTEGER DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS sop_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, sop_id TEXT, segment_name TEXT, segment_id TEXT, start_date TEXT, status TEXT DEFAULT 'proposed',
                    proposal_ids TEXT, checks_json TEXT, notes TEXT, created_by TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
    conn.commit()
    have = {r["id"] for r in conn.execute("SELECT id FROM sops").fetchall()}
    for spec in LIBRARY:
        if spec["id"] not in have:
            conn.execute("INSERT OR IGNORE INTO sops (id, name, campaign_type, spec_json, source, version) VALUES (?,?,?,?,?,?)", (spec["id"], spec["name"], spec["campaign_type"], json.dumps(spec), "library", 1))
    conn.commit(); conn.close()


def list_sops(include_inactive: bool = False) -> List[Dict[str, Any]]:
    init_sop_tables()
    conn = get_db()
    rows = [dict(r) for r in conn.execute("SELECT * FROM sops" + ("" if include_inactive else " WHERE active=1") + " ORDER BY campaign_type, name").fetchall()]
    conn.close()
    out = []
    for r in rows:
        spec = json.loads(r.pop("spec_json") or "{}")
        chk = sop_check(spec)
        out.append({**spec, "id": r["id"], "source": r["source"], "version": r["version"], "active": r["active"], "updated_at": r["updated_at"], "framework_ok": chk["ok"], "framework_problems": chk["problems"], "steps_count": len(spec.get("steps") or [])})
    return out


def get_sop(sop_id: str) -> Optional[Dict[str, Any]]:
    for s in list_sops(include_inactive=True):
        if s["id"] == sop_id:
            return s
    return None


# ── framework ──────────────────────────────────────────────────────────────────
def sop_check(spec: Dict[str, Any]) -> Dict[str, Any]:
    """The framework every SOP must satisfy before it can run. Problems block; warnings inform."""
    p: List[str] = []; w: List[str] = []
    for k in ("id", "name", "campaign_type", "transition", "objective", "audience", "steps", "duration_days", "frequency", "holdout_pct", "primary_kpi", "target", "guardrail_metric", "measurement_window_days", "kill_criteria", "checks"):
        if spec.get(k) in (None, "", [], {}):
            p.append(f"{k} missing")
    if spec.get("campaign_type") and spec["campaign_type"] not in CAMPAIGN_TYPES:
        p.append(f"campaign_type must be one of {CAMPAIGN_TYPES}")
    try:
        h = float(spec.get("holdout_pct", 0))
        if h < 5:
            p.append("holdout_pct must be ≥ 5 (20 for a new programme)")
        elif h < 20 and spec.get("source") != "library":
            w.append("holdout_pct < 20 on a non-library SOP: prefer 20 until the programme has a readout")
    except (TypeError, ValueError):
        p.append("holdout_pct must be numeric")
    steps = spec.get("steps") or []
    freq = spec.get("frequency") or {}
    cap = freq.get("max_messages_per_user_per_week")
    dur = spec.get("duration_days") or 0
    if steps:
        for i, s in enumerate(steps):
            if s.get("channel") not in CHANNELS:
                p.append(f"step {i}: channel must be one of {CHANNELS}")
            if not s.get("purpose") or not s.get("copy_brief"):
                p.append(f"step {i}: purpose and copy_brief required")
            if s.get("channel") == "push" and isinstance(s.get("send_time_ist"), str) and re.fullmatch(r"\d{2}:\d{2}", s["send_time_ist"]):
                hh = int(s["send_time_ist"][:2])
                if hh < 8 or hh >= 22:
                    p.append(f"step {i}: push at {s['send_time_ist']} IST is inside DND (08:00–22:00 allowed)")
            if s.get("channel") == "sms" and isinstance(s.get("send_time_ist"), str) and re.fullmatch(r"\d{2}:\d{2}", s["send_time_ist"]) and not (10 <= int(s["send_time_ist"][:2]) < 21):
                p.append(f"step {i}: promotional SMS must be 10:00–21:00 IST (DLT/TRAI)")
        if cap is not None and dur:
            weeks = max(1, -(-int(dur) // 7))
            user_msgs = [s for s in steps if not str(s.get("purpose", "")).startswith("(internal)")]
            if len(user_msgs) / weeks > float(cap) and cap > 0:
                p.append(f"{len(user_msgs)} messages over {weeks} week(s) exceeds max_messages_per_user_per_week={cap}")
    try:
        from .guardrails import limits as _limits
        L = _limits()
        if cap is not None and float(cap) > float(L.get("total_per_week", 8)):
            p.append(f"max_messages_per_user_per_week={cap} exceeds the global limit total_per_week={L.get('total_per_week')}")
        by_ch: Dict[str, int] = {}
        for st in steps:
            if not str(st.get("purpose", "")).startswith("(internal)"):
                by_ch[st.get("channel")] = by_ch.get(st.get("channel"), 0) + 1
        weeks = max(1, -(-int(dur or 1) // 7))
        for ch, n in by_ch.items():
            capw = ((L.get("per_user") or {}).get(ch) or {}).get("per_week")
            if capw is not None and n / weeks > capw:
                p.append(f"{n} {ch} step(s) over {weeks} week(s) exceeds the global {ch} limit of {capw}/week")
    except Exception:
        pass
    if dur and dur > 90 and freq.get("cadence") != "event_triggered":
        w.append("duration > 90 days: split into a standing flow with re-entry rules")
    aud = spec.get("audience") or {}
    excl = [str(x).lower() for x in aud.get("exclusions") or []]
    if spec.get("campaign_type") in DERIVATIVES_TYPES or spec.get("campaign_type") == "market":
        if not any("liquidat" in x for x in excl) and spec.get("campaign_type") != "risk":
            p.append("derivatives/market SOP must exclude users liquidated in the last 14d")
        if not any("loss" in x for x in excl) and spec.get("campaign_type") != "risk":
            p.append("derivatives/market SOP must exclude loss-dormant users")
    if spec.get("campaign_type") in ("risk", "education", "market", "competition") and "UK" not in (aud.get("jurisdictions_excluded") or []) and spec.get("campaign_type") != "market":
        w.append("UK residents should be excluded from derivatives / incentive content (FCA)")
    comp = spec.get("compliance") or {}
    email_steps = [s for s in steps if s.get("channel") in ("email", "whatsapp", "in-app") and not str(s.get("purpose", "")).startswith("(internal)")]
    if email_steps and not comp.get("disclaimer_channels") and spec.get("campaign_type") != "compliance":
        p.append("compliance.disclaimer_channels must list where the ASCI VDA disclaimer is shown (email/whatsapp/in-app)")
    if spec.get("measurement_window_days") and spec.get("duration_days") and int(spec["measurement_window_days"]) < int(spec["duration_days"]):
        w.append("measurement window shorter than the sequence; late steps will not be read")
    if not (spec.get("kill_criteria") or []):
        p.append("kill_criteria required (at least one numeric stop rule)")
    if isinstance(spec.get("primary_kpi"), str) and ("," in spec["primary_kpi"] or " and " in spec["primary_kpi"]):
        p.append("exactly one primary_kpi")
    return {"ok": not p, "problems": p, "warnings": w}


def define_sop(spec: Dict[str, Any], author: str = "user") -> Dict[str, Any]:
    """Create or update an SOP (validated against the framework; stored with a bumped version)."""
    init_sop_tables()
    if not spec.get("id"):
        spec["id"] = "sop_" + re.sub(r"[^a-z0-9]+", "_", (spec.get("name") or "custom").lower()).strip("_")[:40]
    spec.setdefault("source", author); spec.setdefault("checks", dict(preflight=["framework", "segment_exists", "exclusions_present", "brief_per_step", "compliance"], midflight=["kill_criteria_daily"], postflight=["readout_with_ci", "lesson_to_feed"]))
    spec.setdefault("compliance", dict(disclaimer_channels=["email", "whatsapp", "in-app"], banned_angles=[]))
    chk = sop_check(spec)
    if not chk["ok"]:
        return {"ok": False, "problems": chk["problems"], "warnings": chk["warnings"], "hint": "fix the problems and call define_sop again"}
    conn = get_db()
    row = conn.execute("SELECT version FROM sops WHERE id=?", (spec["id"],)).fetchone()
    ver = (row["version"] + 1) if row else 1
    spec["version"] = ver
    conn.execute("""INSERT INTO sops (id, name, campaign_type, spec_json, source, version, active) VALUES (?,?,?,?,?,?,1)
                    ON CONFLICT(id) DO UPDATE SET name=excluded.name, campaign_type=excluded.campaign_type, spec_json=excluded.spec_json, version=excluded.version, active=1, updated_at=CURRENT_TIMESTAMP""",
                 (spec["id"], spec["name"], spec["campaign_type"], json.dumps(spec, default=str), author, ver))
    conn.commit(); conn.close()
    audit("sop.defined", {"id": spec["id"], "version": ver, "author": author}, actor=author)
    return {"ok": True, "id": spec["id"], "version": ver, "warnings": chk["warnings"]}


def set_active(sop_id: str, active: bool) -> None:
    conn = get_db(); conn.execute("UPDATE sops SET active=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (1 if active else 0, sop_id)); conn.commit(); conn.close()


# ── pre-flight + run ───────────────────────────────────────────────────────────
def preflight(sop: Dict[str, Any], segment: Optional[Dict[str, Any]], regime: Optional[str], reach: Optional[int]) -> Dict[str, Any]:
    from .market.hooks import ANGLE_POLICY
    checks = []
    fw = sop_check(sop); checks.append({"check": "framework", "ok": fw["ok"], "detail": "; ".join(fw["problems"] + fw["warnings"])[:400]})
    fam = (sop.get("audience") or {}).get("segment_family")
    if fam and fam != "*":
        checks.append({"check": "segment_exists", "ok": segment is not None, "detail": (f"resolved to {segment['name']} (version {segment.get('version')})" + (f" — closest match for family {segment['closest_match_for']}; confirm it is the intended cohort" if segment.get("closest_match_for") else "") if segment else f"no segment in the registry for family {fam}; upload it or pass an explicit segment name")})
    mr = (sop.get("audience") or {}).get("min_reach") or 0
    if mr:
        checks.append({"check": "min_reach", "ok": reach is None or reach >= mr, "detail": (f"reach {reach:,} vs min {mr:,}" if reach is not None else f"reach unknown (no size API); min {mr:,} — verify in the dashboard")})
    excl = (sop.get("audience") or {}).get("exclusions") or []
    checks.append({"check": "exclusions_present", "ok": bool(excl), "detail": f"{len(excl)} exclusion rule(s)"})
    if regime and sop.get("campaign_type") in ("market", "competition", "winback"):
        blocked = set(ANGLE_POLICY.get(regime, ANGLE_POLICY["unknown"])["block"])
        bad = sorted(blocked & {"new_listing", "fomo", "win_framing", "referral", "trend_following"}) if sop["campaign_type"] != "market" else []
        stress = regime in ("capitulation", "high_volatility_down")
        checks.append({"check": "regime_allows", "ok": not stress, "detail": f"regime {regime}" + (f"; blocked angles {bad}" if bad else "")})
    try:
        from .guardrails import effective_limits, planned_touches, _stage_for_family
        famkey = (segment or {}).get("family") or fam or ""
        eff = effective_limits(regime, _stage_for_family(famkey)); existing = sum(planned_touches(famkey).values()) if famkey and famkey != "*" else 0
        weeks = max(1, -(-int(sop.get("duration_days") or 1) // 7)); adds = len([s for s in sop["steps"] if not str(s.get("purpose", "")).startswith("(internal)")]) / weeks
        checks.append({"check": "limits", "ok": adds + existing <= eff["total_per_week"], "detail": f"adds {adds:.1f}/user/week + {existing} already planned vs cap {eff['total_per_week']} (regime {regime}, ×{eff['multiplier']})"})
    except Exception as e:
        checks.append({"check": "limits", "ok": True, "detail": "limits check unavailable: " + str(e)[:80]})
    comp = sop.get("compliance") or {}
    checks.append({"check": "compliance", "ok": True, "detail": f"disclaimer on {comp.get('disclaimer_channels') or 'n/a'}; banned angles {comp.get('banned_angles') or []}"})
    return {"ok": all(c["ok"] for c in checks), "checks": checks}


def run_sop(sop_id: str, segment_name: Optional[str] = None, start_date: Optional[str] = None, variants_by_step: Optional[Dict[str, List[Dict[str, Any]]]] = None,
            created_by: str = "user", dry_run: bool = False) -> Dict[str, Any]:
    """Resolve the cohort, run pre-flight, and queue one approval-gated campaign proposal per step (plus the run record)."""
    from . import approvals, segments
    from .llm.tools import campaign_brief_check
    sop = get_sop(sop_id)
    if not sop:
        return {"ok": False, "error": f"unknown SOP {sop_id}", "available": [s["id"] for s in list_sops()]}
    fam = (sop.get("audience") or {}).get("segment_family")
    seg = segments.resolve(segment_name) if segment_name else (segments.resolve(fam) if fam and fam != "*" else None)
    regime = None
    try:
        from .market.context import _latest
        c = _latest(6 * 3600)
        regime = ((c or {}).get("hooks") or {}).get("regime")
    except Exception:
        pass
    reach = (seg or {}).get("reach")
    pf = preflight(sop, seg, regime, reach)
    start = date.fromisoformat(start_date) if start_date else date.today()
    plan = []
    for i, st in enumerate(sop["steps"]):
        if str(st.get("purpose", "")).startswith("(internal)"):
            plan.append({"step": i, "internal": True, "purpose": st["purpose"]}); continue
        variants = (variants_by_step or {}).get(str(i)) or (variants_by_step or {}).get(i) or [
            {"label": "A (needs copy)", "title": st["purpose"][:60], "body": st["copy_brief"][:140], "cta": "Open"}]
        goal = {"transition": sop["transition"], "hypothesis": f"If we run '{sop['name']}' step {i} ({st['purpose']}) to {seg['name'] if seg else fam}, {sop['primary_kpi']} moves {sop['target']} because {sop['objective']}",
                "primary_kpi": sop["primary_kpi"], "target": sop["target"], "guardrail_metric": sop["guardrail_metric"], "control_group_pct": sop["holdout_pct"],
                "measurement_window_days": sop["measurement_window_days"], "kill_criteria": "; ".join(sop["kill_criteria"]), "suppressions": (sop.get("audience") or {}).get("exclusions") or []}
        market_linked = sop["campaign_type"] == "market"
        bc = campaign_brief_check(goal, variants, channel=st["channel"], market_linked=market_linked, ttl_hours=st.get("ttl_hours"), audience_countries=None, disclaimer_included=st["channel"] in (sop.get("compliance") or {}).get("disclaimer_channels", []))
        sched_day = start + timedelta(days=int(st.get("day") or 0))
        plan.append({"step": i, "channel": st["channel"], "purpose": st["purpose"], "send_on": sched_day.isoformat(), "send_time_ist": st.get("send_time_ist"), "condition": st.get("condition"), "variants": variants, "goal": goal, "brief_check": bc})
    ok = pf["ok"] and all(p.get("internal") or p["brief_check"]["ok"] for p in plan)
    if dry_run or not ok:
        return {"ok": ok, "dry_run": dry_run, "sop": sop["id"], "segment": seg, "preflight": pf, "plan": plan, "note": None if ok else "pre-flight or brief problems; nothing queued"}
    init_sop_tables()
    pids = []
    for p in plan:
        if p.get("internal"):
            continue
        st = sop["steps"][p["step"]]
        title = f"{sop['name']} · step {p['step']} · {st['channel']} → {seg['name'] if seg else fam}"
        prop = approvals.propose("create_campaign", title, {"name": f"SOP_{sop['id']}_{p['step']}_{(seg or {}).get('name') or fam}_{start.strftime('%d%b%y')}", "channel": st["channel"], "target_segment": (seg or {}).get("name") or fam,
                                                          "variants": p["variants"], "goal": p["goal"], "schedule": {"date": p["send_on"], "time_ist": st.get("send_time_ist"), "condition": st.get("condition")},
                                                          "ttl_hours": st.get("ttl_hours"), "exclusions": p["goal"]["suppressions"], "frequency_cap": f"{sop['frequency'].get('max_messages_per_user_per_week')}/week", "sop_id": sop["id"]},
                                 rationale=f"SOP {sop['id']} v{sop.get('version')}: {sop['objective']} Step purpose: {st['purpose']}", risk="medium", created_by=created_by)
        pids.append(prop["id"])
    conn = get_db()
    cur = conn.execute("INSERT INTO sop_runs (sop_id, segment_name, segment_id, start_date, status, proposal_ids, checks_json, created_by) VALUES (?,?,?,?,?,?,?,?)",
                       (sop["id"], (seg or {}).get("name") or fam, (seg or {}).get("segment_id"), start.isoformat(), "proposed", json.dumps(pids), json.dumps(pf), created_by))
    run_id = cur.lastrowid; conn.commit(); conn.close()
    audit("sop.run", {"run_id": run_id, "sop": sop["id"], "segment": (seg or {}).get("name") or fam, "proposals": pids}, actor=created_by)
    return {"ok": True, "run_id": run_id, "sop": sop["id"], "segment": (seg or {}).get("name") or fam, "proposal_ids": pids, "preflight": pf, "plan": plan, "next": "review and approve each step in Approvals; mid-flight checks run daily"}


def list_runs(limit: int = 30) -> List[Dict[str, Any]]:
    init_sop_tables()
    from . import approvals
    conn = get_db()
    rows = [dict(r) for r in conn.execute("SELECT * FROM sop_runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]
    conn.close()
    props = {p["id"]: p for p in approvals.list_proposals(limit=400)}
    for r in rows:
        try:
            r["proposal_ids"] = json.loads(r.pop("proposal_ids") or "[]"); r["checks"] = json.loads(r.pop("checks_json") or "{}")
        except Exception:
            r["proposal_ids"], r["checks"] = [], {}
        sts = [props[p]["status"] for p in r["proposal_ids"] if p in props]
        r["proposal_status"] = {s: sts.count(s) for s in set(sts)}
    return rows


def midflight_checks() -> Dict[str, Any]:
    """Daily: for runs whose proposals executed, evaluate numeric kill rules against the experiment readouts / snapshots; flag in the growth feed."""
    from . import approvals, growth, experiments
    init_sop_tables()
    out = []
    props = {p["id"]: p for p in approvals.list_proposals(limit=400)}
    exps = {e.get("proposal_id"): e for e in experiments.list_experiments(limit=200)}
    conn = get_db()
    runs = [dict(r) for r in conn.execute("SELECT * FROM sop_runs WHERE status IN ('proposed','active')").fetchall()]
    for r in runs:
        pids = json.loads(r.get("proposal_ids") or "[]")
        sts = [props[p]["status"] for p in pids if p in props]
        status = "active" if sts and all(s in ("executed", "rejected") for s in sts) and "executed" in sts else ("abandoned" if sts and all(s == "rejected" for s in sts) else r["status"])
        flags = []
        sop = get_sop(r["sop_id"]) or {}
        for pid in pids:
            e = exps.get(pid)
            ro = (e or {}).get("readout") or {}
            if not ro or ro.get("value") is None:
                continue
            for rule in sop.get("kill_criteria") or []:
                m = re.match(r"\s*([a-z_]+)\s*(<|>)\s*([\d.]+)\s*%?", rule)
                if not m:
                    continue
                metric, op, thr = m.group(1), m.group(2), float(m.group(3))
                val = ro.get("value") if metric in (ro.get("metric"), "ctr", "click_rate") else None
                if metric == "delivery_rate":
                    val = None  # readout carries the primary metric only; delivery is checked by anomaly rules
                if val is None:
                    continue
                if (op == "<" and val < thr) or (op == ">" and val > thr):
                    flags.append(f"proposal #{pid}: kill rule hit — {rule} (observed {val})")
        if flags:
            growth.upsert_ideas([{"kind": "fix", "title": f"SOP run #{r['id']} ({r['sop_id']}) hit a kill rule", "why": " | ".join(flags)[:600], "how": "Review in Approvals; propose_pause_campaign for the affected step; keep the holdout intact.", "priority": 90,
                                  "segment": r.get("segment_name"), "kpi": sop.get("primary_kpi", ""), "effort": "low", "expected_impact": "stops a failing step", "data": {"sop_run": r["id"]}}], "rules")
        conn.execute("UPDATE sop_runs SET status=?, notes=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (status, "; ".join(flags)[:1000] if flags else r.get("notes"), r["id"]))
        out.append({"run": r["id"], "status": status, "flags": flags})
    conn.commit(); conn.close()
    return {"runs": out}


TRADER_STATES = ["Spot-only", "First-perp", "Leverage climber", "Habitual perp", "Hedger", "Liquidated (14d)", "Loss-dormant", "Tokenised explorer", "New (no deposit)", "Dormant 30–90d", "Core / VIP"]
CHANNEL_INFO = {
    "push": {"formats": ["standard", "rich (image)", "carousel"], "limits": "1/day, 4/week; quiet hours 22:00–08:00 IST; market-linked TTL ≤ 4h", "compliance": "cannot carry the ASCI disclaimer → landing screen must; no promo push to India users without it"},
    "email": {"formats": ["statement", "explainer", "digest", "series"], "limits": "1/day, 3/week; unsubscribe link mandatory", "compliance": "disclaimer footer; UK risk warning if any UK user"},
    "whatsapp": {"formats": ["utility template", "marketing template (opt-in only)"], "limits": "1/day, 2/week; 10:00–21:00 IST; approved templates only", "compliance": "opt-in; template body carries disclaimer for marketing"},
    "sms": {"formats": ["DLT template"], "limits": "1/week; 10:00–21:00 IST", "compliance": "DLT registration; transactional vs promotional headers"},
    "in-app": {"formats": ["bottom sheet", "card", "full-screen", "nudge/tooltip", "survey"], "limits": "2/day, 6/week; never blocks trading; dismissible", "compliance": "disclaimer block on derivatives content; no bottom sheets during order flow"},
    "cards": {"formats": ["inbox card (persistent)", "digest"], "limits": "1/day, 7/week", "compliance": "same as in-app"},
}
STATE_RULES = {
    "Spot-only": {"purpose": ["alerts", "watchlist", "SIP / recurring buy", "fees/TDS", "education (spot)", "perp education only on intent"], "avoid": ["perps promotion", "leverage", "asset recommendation"], "sops": ["sop_first_week_habit", "sop_cross_sell_recurring_buy", "sop_push_alert_digest", "sop_category_explorer"]},
    "First-perp": {"purpose": ["position hygiene", "funding explainer", "SL/TP tools", "isolated margin"], "avoid": ["size-up", "leverage upsell", "competitions"], "sops": ["sop_first_perp_hygiene", "sop_funding_crowding_nudge"]},
    "Leverage climber": {"purpose": ["risk education", "personal max-loss scenarios"], "avoid": ["any encouragement", "market pushes"], "sops": ["sop_leverage_climber_risk", "sop_macro_print_brief"]},
    "Habitual perp": {"purpose": ["fee tiers", "funding digest", "OI/funding notes", "tokenised cross-sell", "macro briefs"], "avoid": ["P&L leaderboards", "promos in stress regimes"], "sops": ["sop_fee_tier_nudge", "sop_cross_sell_crypto_to_tokenised", "sop_oi_crowding_note", "sop_macro_print_brief"]},
    "Hedger": {"purpose": ["portfolio views", "basis/funding tools", "hedge education"], "avoid": ["direction talk"], "sops": ["sop_hedger_education"]},
    "Liquidated (14d)": {"purpose": ["service card", "plain explainer", "tools", "support"], "avoid": ["every promo", "market pushes", "competitions", "referral"], "sops": ["sop_liquidation_recovery"]},
    "Loss-dormant": {"purpose": ["service", "statement", "education"], "avoid": ["promos", "market content", "offers"], "sops": ["sop_loss_dormant_service"]},
    "Tokenised explorer": {"purpose": ["after-hours facts", "24/7 education", "earnings-week risk notes (once a calendar exists)"], "avoid": ["'trade earnings'", "forecasts"], "sops": ["sop_tokenised_after_hours", "sop_weekend_tokenised", "sop_asset_spotlight"]},
    "New (no deposit)": {"purpose": ["deposit path", "objection removal", "failure recovery", "KYC help"], "avoid": ["bonuses by default", "market content"], "sops": ["sop_verified_to_funded", "sop_deposit_failure_recovery", "sop_kyc_completion", "sop_bottom_sheet_onboarding_tour", "sop_whatsapp_utility_journey"]},
    "Dormant 30–90d": {"purpose": ["cause-matched win-back", "habit tool re-entry"], "avoid": ["blasts in stress regimes", "offers to lossy users"], "sops": ["sop_dormant_by_cause", "sop_friction_dormant_fixed", "sop_market_dormant_return"]},
    "Core / VIP": {"purpose": ["monthly statement", "concierge", "early access", "fee review"], "avoid": ["more than 4 touches/week", "generic promos"], "sops": ["sop_hvt_retention", "sop_vip_concierge_quarterly", "sop_feature_launch_adoption"]},
}


def channel_matrix() -> Dict[str, Any]:
    """What can be done for each user state on each channel: purposes, formats, caps, compliance and the SOPs that implement it."""
    from .guardrails import limits
    L = limits()
    rows = []
    for st in TRADER_STATES:
        r = STATE_RULES.get(st, {})
        cells = {}
        for ch, info in CHANNEL_INFO.items():
            cap = (L.get("per_user") or {}).get(ch, {})
            allowed = True
            note = ""
            if st in ("Liquidated (14d)", "Loss-dormant") and ch in ("push", "sms", "whatsapp"):
                allowed = ch == "push" and st == "Liquidated (14d)"; note = "service only" if allowed else "not used for this state"
            if st == "Core / VIP":
                note = "fewest messages of any state"
            cells[ch] = {"allowed": allowed, "cap": f"{cap.get('per_day', '—')}/day · {cap.get('per_week', '—')}/week", "formats": info["formats"], "note": note}
        rows.append({"state": st, "purposes": r.get("purpose", []), "avoid": r.get("avoid", []), "sops": r.get("sops", []), "channels": cells})
    return {"states": rows, "channels": CHANNEL_INFO, "limits_source": "comms_limits (stage overrides and regime multipliers apply on top)"}


def product_cohort_matrix() -> Dict[str, Any]:
    """How each product cohort is treated: pillars, cadence, channels, cross-sell (intent only), never-list, announcement lens, SOPs."""
    from .products import PRODUCTS, treatment
    sop_map = {"spot": ["sop_first_week_habit", "sop_push_alert_digest", "sop_cross_sell_recurring_buy", "sop_asset_spotlight"], "sip": ["sop_sip_nurture", "sop_cross_sell_recurring_buy"],
               "perps_crypto": ["sop_first_perp_hygiene", "sop_funding_crowding_nudge", "sop_oi_crowding_note", "sop_macro_print_brief", "sop_fee_tier_nudge", "sop_liquidation_recovery"],
               "perps_us_stocks": ["sop_tokenised_after_hours", "sop_weekend_tokenised", "sop_cross_sell_crypto_to_tokenised"], "perps_indices": ["sop_macro_print_brief", "sop_geopolitical_event_brief"],
               "perps_commodities": ["sop_cross_sell_to_commodities", "sop_geopolitical_event_brief"], "options": ["sop_options_education"], "earn": ["sop_cross_sell_earn"], "web3": ["sop_web3_onboarding_safety", "sop_web3_trending_watch"]}
    rows = []
    for pid in PRODUCTS:
        t = treatment(pid)
        rows.append({**t, "channels": {"push": "yes (tool-led)" if pid != "earn" else "rare", "email": "monthly statement + education", "whatsapp": "utility only", "in-app": "bottom sheets / cards (primary)", "cards": "recaps"},
                     "sops": sop_map.get(pid, []), "tier0": "receives every Tier-0 announcement with this lens"})
    return {"cohorts": rows, "rules": ["a user with several products gets the most specific product's programme (web3 > options > tokenised > crypto perps > earn > SIP > spot) and Tier-0 lenses for each",
                                        "cross-sell only on intent signals (screen views), education-first for derivatives, never to liquidated-14d or loss-dormant users",
                                        "communication limits and regime multipliers apply on top of product cadence", "no venue or competitor names in any cohort's copy"]}


BANNED_IN_ALERT_COPY = re.compile(r"\b(buy|sell|long|short|pump|moon|rally|crash|breakout|surge)\b", re.I)


def _alert_copy(product: str, headline: str, detail: str, cta: str) -> List[Dict[str, Any]]:
    """Two fact + tool variants from an alert; venue names and direction words are stripped; lengths respect push limits."""
    def clean(t: str) -> str:
        t = re.sub(r"\b(binance|hyperliquid|bybit|okx|bitget|coinbase|delta|kraken|kucoin|gate|mexc)\b", "", t, flags=re.I)
        t = BANNED_IN_ALERT_COPY.sub("moved", t)
        return re.sub(r"\s+", " ", t).strip(" ·-—")
    h = clean(headline)[:60]; d = clean(detail)[:100]
    tool = {"spot": "Set a 5% alert so you don't have to watch the screen.", "sip": "Your plan continues; here is what volatility means for a recurring buyer.", "perps_crypto": "Check your margin buffer and funding on the position screen.",
            "perps_us_stocks": "It trades 24/7 on CoinDCX — see how it's pricing now.", "perps_indices": "Review your index exposure and the macro calendar.", "perps_commodities": "See how it trades here, 24/7.", "options": "Defined risk: know your max loss before expiry.",
            "earn": "Rates are variable; see what changes for your position.", "web3": "Unverified token: check liquidity and slippage before anything."}.get(product, "Open the app to review.")
    return [{"label": "A · fact + tool", "title": h, "body": (d + " " + tool)[:140], "cta": cta}, {"label": "B · tool first", "title": (tool.split(".")[0])[:60], "body": (h + ". " + d)[:140], "cta": cta}]


def run_from_alert(product: str, headline: str, detail: str = "", sop_id: Optional[str] = None, segment_name: Optional[str] = None, created_by: str = "user", dry_run: bool = False) -> Dict[str, Any]:
    """On-the-go campaign from a market alert: pick the product's SOP, build compliant placeholder copy, queue approval-gated proposals (copy marked for edit)."""
    from .market.feed import campaign_hint, CTA
    hint = campaign_hint(product, {"category": ""}) if not sop_id else {"sop": sop_id, "cta": CTA.get(product, "Open")}
    sop = get_sop(sop_id or hint["sop"])
    if not sop:
        return {"ok": False, "error": f"unknown SOP {sop_id or hint['sop']}"}
    variants = _alert_copy(product, headline, detail, hint["cta"])
    vbs = {str(i): variants for i, st in enumerate(sop["steps"]) if not str(st.get("purpose", "")).startswith("(internal)")}
    r = run_sop(sop["id"], segment_name=segment_name, variants_by_step=vbs, created_by=created_by, dry_run=dry_run)
    r["variants"] = variants; r["source_alert"] = {"product": product, "headline": headline, "detail": detail}
    if r.get("ok") and r.get("run_id"):
        audit("sop.run_from_alert", {"run_id": r["run_id"], "product": product, "sop": sop["id"], "headline": headline[:80]}, actor=created_by)
    return r
