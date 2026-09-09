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
                dict(day=6, channel="email", purpose="What ₹500 lets you do: spot, alerts, recurring buy", copy_brief="Education, fee transparency, TDS one-liner; ASCI disclaimer in footer.", send_time_ist="09:00")],
         duration_days=7, frequency=dict(cadence="event_triggered", max_messages_per_user_per_week=4), holdout_pct=10,
         primary_kpi="first_deposit_rate_7d", target="+3 pp vs holdout", guardrail_metric="unsubscribe_rate", measurement_window_days=14,
         kill_criteria=["delivery_rate < 85% on any push step", "unsubscribe_rate > 0.5% on email step", "support_ticket_rate of cohort > 2× baseline"],
         checks=dict(preflight=["framework", "segment_exists", "exclusions_present", "brief_per_step", "compliance"], midflight=["kill_criteria_daily", "holdout_intact"], postflight=["readout_with_ci", "lesson_to_feed"]),
         compliance=dict(disclaimer_channels=["email", "whatsapp", "in-app"], banned_angles=["bonus", "fomo"]), source="library", version=1),
    dict(id="sop_funded_to_first_trade", name="Funded → first trade (72h)", campaign_type="activation", transition="funded_activated",
         objective="First spot trade within 72h of first deposit; small size, no leverage.",
         audience=dict(segment_family="FTD_NOTRADE", description="first deposit completed, no order_filled", exclusions=STANDARD_EXCLUSIONS, min_reach=300, jurisdictions_excluded=[]),
         steps=[dict(day=0, channel="in-app", purpose="Your ₹ is in: three ways to start (recurring buy, watchlist, ₹100 trade)", copy_brief="Fact: deposit landed. Tool: guided first trade. No asset recommendation — user picks from watchlist/top by volume.", send_time_ist="+1h"),
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


def init_sop_tables() -> None:
    conn = get_db()
    conn.execute("""CREATE TABLE IF NOT EXISTS sops (id TEXT PRIMARY KEY, name TEXT, campaign_type TEXT, spec_json TEXT, source TEXT, version INTEGER DEFAULT 1, active INTEGER DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS sop_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, sop_id TEXT, segment_name TEXT, segment_id TEXT, start_date TEXT, status TEXT DEFAULT 'proposed',
                    proposal_ids TEXT, checks_json TEXT, notes TEXT, created_by TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
    conn.commit()
    n = conn.execute("SELECT COUNT(*) FROM sops").fetchone()[0]
    if n == 0:
        for s in LIBRARY:
            conn.execute("INSERT OR IGNORE INTO sops (id, name, campaign_type, spec_json, source, version) VALUES (?,?,?,?,?,?)", (s["id"], s["name"], s["campaign_type"], json.dumps(s), "library", 1))
        conn.commit()
    conn.close()


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
