"""
Tool registry for the marketer agent. Every tool returns JSON-serialisable
data; outputs are redacted and truncated before reaching the model. Write
tools only create *proposals* (approval queue); nothing here touches MoEngage
write endpoints directly.
"""
from __future__ import annotations
import glob
import json
import os
import re
from typing import Any, Callable, Dict, List, Optional

from ..security import redact
from .. import approvals

KNOWLEDGE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "knowledge")


def _client():
    from ..moengage import MoEngageClient
    return MoEngageClient()


def _safe(fn: Callable[..., Any]) -> Callable[..., Dict[str, Any]]:
    def wrapper(**kw):
        try:
            out = fn(**kw)
            return out if isinstance(out, dict) else {"result": out}
        except Exception as e:
            return {"error": redact(str(e)), "error_type": type(e).__name__}
    return wrapper


# ── read tools ────────────────────────────────────────────────────────────────
def get_status() -> Dict[str, Any]:
    from ..moengage import registry_status
    c = _client()
    st = c.verify_session()
    reg = registry_status()
    return {"mode": c.mode, "session": st, "endpoints": {"usable_reads": reg["usable_reads"], "usable_writes": reg["usable_writes"]},
            "note": "mode=mock means every number below is simulated demo data, not the customer's workspace."}


def list_campaigns(status: Optional[str] = None, channel: Optional[str] = None) -> Dict[str, Any]:
    rows = _client().get_campaigns(status_filter=status, channel_filter=channel)
    return {"source": (rows[0].get("_source") if rows else _client().mode), "count": len(rows), "campaigns": rows[:50]}


def get_campaign_stats(campaign_id: str) -> Dict[str, Any]:
    return _client().get_campaign_stats(campaign_id)


def list_segments() -> Dict[str, Any]:
    rows = _client().get_segments()
    return {"source": (rows[0].get("_source") if rows else _client().mode), "count": len(rows), "segments": rows[:100]}


def list_flows() -> Dict[str, Any]:
    rows = _client().get_flows()
    return {"source": (rows[0].get("_source") if rows else _client().mode), "count": len(rows), "flows": rows[:50]}


def get_analytics() -> Dict[str, Any]:
    return _client().get_analytics_summary()


def estimate_segment(criteria: Dict[str, Any]) -> Dict[str, Any]:
    return _client().estimate_segment(criteria)


def rule_based_audit() -> Dict[str, Any]:
    from ..clm_intelligence import CLMIntelligenceEngine
    c = _client()
    return {"source": c.mode, "audit": CLMIntelligenceEngine().audit_active_campaigns(c.get_campaigns())}


def anomaly_report(source: Optional[str] = None) -> Dict[str, Any]:
    from ..anomaly import detect_anomalies, snapshot_count
    from ..anomaly.store import recent_events
    src = source or _client().mode
    rep = detect_anomalies(source=src, persist=False)
    rep["days_of_history"] = snapshot_count(src)
    rep["recent_events"] = recent_events(20, src)
    return rep


def campaign_history(campaign_id: str, days: int = 30) -> Dict[str, Any]:
    from ..anomaly import get_history
    return {"campaign_id": campaign_id, "history": get_history(campaign_id, _client().mode, days)}


def market_snapshot(force: bool = False) -> Dict[str, Any]:
    from ..market import market_context
    ctx = market_context(force=force)
    slim = {k: ctx.get(k) for k in ("generated_at", "narrative", "fear_greed", "crypto_movers", "equity_movers", "commodity_movers", "macro", "crypto_global", "crypto_trending", "inr_marks", "errors")}
    slim["regime"] = (ctx.get("crypto") or {}).get("regime")
    slim["btc"] = ((ctx.get("crypto") or {}).get("assets") or {}).get("BTC")
    slim["calendar_high_impact"] = [e for e in (ctx.get("calendar") or []) if e.get("impact") == "High"][:8]
    return slim


def market_news(category: Optional[str] = None, query: Optional[str] = None, limit: int = 12) -> Dict[str, Any]:
    from ..market import market_news as mn
    out = mn(category=category, query=query, limit=limit)
    if "categories" in out:
        out["categories"] = {k: v[:limit] for k, v in out["categories"].items()}
    return out


def market_campaign_hooks() -> Dict[str, Any]:
    from ..market import campaign_hooks
    return campaign_hooks()


def moengage_guidance(topic: str) -> Dict[str, Any]:
    """Keyword search over the local knowledge base (channel playbooks, CLM stages, segmentation, measurement, market intelligence)."""
    words = [w for w in re.findall(r"[a-z0-9]+", topic.lower()) if len(w) > 2]
    hits = []
    for path in sorted(glob.glob(os.path.join(KNOWLEDGE_DIR, "*.md"))):
        text = open(path).read()
        low = text.lower()
        score = sum(low.count(w) for w in words)
        if score:
            # return the best-matching sections
            sections = re.split(r"\n(?=#{1,3} )", text)
            ranked = sorted(sections, key=lambda s: -sum(s.lower().count(w) for w in words))
            hits.append({"doc": os.path.basename(path), "score": score, "excerpt": "\n\n".join(ranked[:2])[:2500]})
    hits.sort(key=lambda h: -h["score"])
    return {"topic": topic, "docs_available": [os.path.basename(p) for p in glob.glob(os.path.join(KNOWLEDGE_DIR, "*.md"))], "matches": hits[:3]}


def integration_status() -> Dict[str, Any]:
    from ..moengage import registry_status
    return registry_status()


def list_proposals(status: Optional[str] = None) -> Dict[str, Any]:
    rows = approvals.list_proposals(status=status, limit=30)
    return {"count": len(rows), "proposals": [{k: v for k, v in r.items() if k not in ("preview",)} for r in rows]}


# ── CLM doctrine tools (deterministic) ────────────────────────────────────────
TRANSITIONS = [
    # order matters: more specific first; matched on word boundaries
    ("dormant_activated", "Dormant → Activated", [r"re-?activat", r"win-?back", r"dormant", r"inactive", r"miss you", r"come back", r"lapsed"]),
    ("intent_dropoff", "Intent drop-off → converted", [r"cart", r"abandon", r"checkout", r"drop-?off", r"unfinished", r"incomplete order", r"pending order"]),
    ("acquired_verified", "Acquired → Verified", [r"\bkyc\b", r"verif", r"sign-?up", r"welcome", r"onboard"]),
    ("verified_funded", "Verified → Funded", [r"deposit", r"\bfund", r"add money", r"\bbank\b", r"\bupi\b"]),
    ("funded_activated", "Funded → Activated", [r"first trade", r"\bactivat", r"start trading", r"first order", r"getting started", r"guide"]),
    ("activated_habitual", "Activated → Habitual", [r"second", r"habit", r"streak", r"daily", r"watchlist", r"\balert", r"price drop", r"wishlist"]),
    ("habitual_core", "Habitual → Core", [r"\bvip\b", r"loyalty", r"\btier", r"points", r"\bfee", r"premium", r"graduat", r"futures", r"cross-?sell"]),
    ("slipping", "Slipping → recovered", [r"slipping", r"declin", r"portfolio review", r"check-?in"]),
    ("promotional", "Promotional / broadcast", [r"\bsale\b", r"flash", r"offer", r"discount", r"% off", r"festival", r"weekend", r"blast"]),
]
REQUIRED_GOAL = ["transition", "hypothesis", "primary_kpi", "target", "guardrail_metric", "control_group_pct", "measurement_window_days", "kill_criteria"]
KPI_BY_TRANSITION = {
    "acquired_verified": ["kyc_completion_rate_72h"], "verified_funded": ["first_deposit_rate_7d", "time_to_first_deposit"],
    "funded_activated": ["first_trade_rate_7d"], "activated_habitual": ["second_trade_within_7d", "sessions_per_week", "alert_adoption_rate"],
    "habitual_core": ["products_per_user", "weekly_active_weeks_4w", "fee_tier_upgrade_rate"], "slipping": ["trade_frequency_recovery_14d"],
    "dormant_activated": ["reactivation_rate_14d"], "churned": ["reactivation_rate_30d"], "promotional": ["conversion_rate", "incremental_gmv_vs_holdout"],
    "intent_dropoff": ["recovery_rate_24h", "checkout_completion_rate"],
}


def _classify_campaign(c: Dict[str, Any]) -> str:
    text = " ".join(str(c.get(k, "")) for k in ("name", "target_segment", "description", "campaign_name")).lower()
    for tid, _, kws in TRANSITIONS:
        if any(re.search(k, text) for k in kws):
            return tid
    return "unmapped"


def clm_program_audit() -> Dict[str, Any]:
    """Map live campaigns to lifecycle transitions; show coverage, gaps and per-transition performance."""
    c = _client()
    camps = c.get_campaigns()
    from ..anomaly.store import normalise_campaign
    cov: Dict[str, Dict[str, Any]] = {tid: {"transition": label, "campaigns": [], "kpi_options": KPI_BY_TRANSITION.get(tid, [])} for tid, label, _ in TRANSITIONS}
    cov["unmapped"] = {"transition": "Unmapped (needs a goal)", "campaigns": [], "kpi_options": []}
    for camp in camps:
        n = normalise_campaign(camp); tid = _classify_campaign(camp)
        cov[tid]["campaigns"].append({"id": n["campaign_id"], "name": n["campaign_name"], "channel": n["channel"], "ctr": n["ctr"], "conversion_rate": n["conversion_rate"], "delivery_rate": n["delivery_rate"], "sent": n["sent_count"]})
    gaps = [v["transition"] for k, v in cov.items() if k not in ("promotional", "unmapped") and not v["campaigns"]]
    broadcasts = len(cov["promotional"]["campaigns"]); unmapped = len(cov["unmapped"]["campaigns"])
    total = max(1, len(camps))
    return {"source": c.mode, "campaigns_total": len(camps), "coverage": cov, "uncovered_transitions": gaps,
            "broadcast_share_pct": round(100 * broadcasts / total, 1), "unmapped_count": unmapped,
            "verdict": ("Programme is broadcast-heavy" if broadcasts / total > 0.4 else "Programme is transition-led") + (f"; {len(gaps)} transitions have no standing campaign" if gaps else ""),
            "next": ["Give every unmapped campaign a transition and a primary KPI or retire it", "Stand up one triggered campaign per uncovered transition, each with a holdout"][: (2 if (gaps or unmapped) else 0)]}


def experiment_plan(baseline_rate_pct: float, min_detectable_lift_pct_points: float, daily_eligible_users: int, control_group_pct: float = 10.0, power: float = 0.8, alpha: float = 0.05) -> Dict[str, Any]:
    """Sample size per arm for a two-proportion test, and how many days the audience needs to reach it."""
    import math
    from statistics import NormalDist
    p1 = max(0.0001, min(0.9999, baseline_rate_pct / 100.0)); p2 = max(0.0001, min(0.9999, p1 + min_detectable_lift_pct_points / 100.0))
    z_a = NormalDist().inv_cdf(1 - alpha / 2); z_b = NormalDist().inv_cdf(power)
    pbar = (p1 + p2) / 2
    n = ((z_a * math.sqrt(2 * pbar * (1 - pbar)) + z_b * math.sqrt(p1 * (1 - p1) + p2 * (1 - p2))) ** 2) / ((p2 - p1) ** 2)
    n = int(math.ceil(n))
    cg = max(0.01, min(0.5, control_group_pct / 100.0))
    # holdout arm receives cg share of eligible users per day; treatment gets the rest; the smaller arm bounds the timeline
    per_day_small = max(1, int(daily_eligible_users * min(cg, 1 - cg)))
    days = math.ceil(n / per_day_small)
    return {"baseline_rate_pct": baseline_rate_pct, "target_rate_pct": round(p2 * 100, 3), "required_per_arm": n, "alpha": alpha, "power": power,
            "control_group_pct": control_group_pct, "days_to_reach_n": days,
            "feasible_within_14d": days <= 14,
            "advice": ("Run it: the holdout reaches the required size in %d days." % days) if days <= 14 else
                      ("Not measurable in 14 days at this reach. Options: widen audience, raise MDE to %.1f pts, lengthen window to %d days, or treat as non-experimental." % (min_detectable_lift_pct_points * math.sqrt(days / 14), days))}


def campaign_brief_check(goal: Dict[str, Any], variants: Optional[List[Dict[str, Any]]] = None, channel: str = "push", market_linked: bool = False, ttl_hours: Optional[int] = None) -> Dict[str, Any]:
    """Validate a campaign brief against the goal discipline and copy rules before proposing."""
    problems: List[str] = []; warnings: List[str] = []
    for k in REQUIRED_GOAL:
        if goal.get(k) in (None, "", []):
            problems.append(f"goal.{k} missing")
    tid = goal.get("transition")
    if tid and tid not in KPI_BY_TRANSITION and tid not in ("churned",):
        warnings.append(f"transition '{tid}' is not a known stage id ({', '.join(KPI_BY_TRANSITION)})")
    if tid in KPI_BY_TRANSITION and goal.get("primary_kpi") and goal["primary_kpi"] not in KPI_BY_TRANSITION[tid]:
        warnings.append(f"primary_kpi '{goal['primary_kpi']}' is unusual for {tid}; expected one of {KPI_BY_TRANSITION[tid]}")
    try:
        cg = float(goal.get("control_group_pct", 0))
        if cg < 5:
            problems.append("control_group_pct must be >= 5 (20 recommended for a new programme)")
    except (TypeError, ValueError):
        problems.append("control_group_pct must be numeric")
    if market_linked and not ttl_hours:
        problems.append("market-linked campaigns need ttl_hours (2-6)")
    if market_linked and ttl_hours and ttl_hours > 6:
        warnings.append("ttl_hours > 6 on a market-linked send risks stale price facts")
    banned = re.compile(r"\b(guaranteed|will (rise|pump|moon|double)|buy now|sell now|can'?t lose|risk[- ]free|100%|to the moon|last chance to buy)\b", re.I)
    for i, v in enumerate(variants or []):
        t, b = str(v.get("title", "")), str(v.get("body", ""))
        if channel.lower() == "push":
            if len(t) > 60: warnings.append(f"variant {i}: push title {len(t)} chars (> 60 may truncate)")
            if len(b) > 140: warnings.append(f"variant {i}: push body {len(b)} chars (> 140 may truncate)")
        if banned.search(t + " " + b):
            problems.append(f"variant {i}: copy contains forecast/urgency language that is not compliant")
        if not v.get("cta"):
            warnings.append(f"variant {i}: no CTA")
    if not goal.get("suppressions"):
        warnings.append("no suppressions listed; standing rules (loss-dormant, friction-dormant, over-cap, DND) should be explicit")
    return {"ok": not problems, "problems": problems, "warnings": warnings, "required_goal_fields": REQUIRED_GOAL}


# ── write tools (proposals only) ──────────────────────────────────────────────
def propose_segment(name: str, criteria: Dict[str, Any], rationale: str, description: str = "", estimated_reach: Optional[int] = None) -> Dict[str, Any]:
    p = approvals.propose("create_segment", f"Segment: {name}", {"name": name, "description": description, "criteria": criteria, "estimated_reach": estimated_reach}, rationale, risk="low")
    return {"proposal_id": p["id"], "status": p["status"], "preview": p.get("preview"), "note": "Awaiting human approval in the Approvals tab."}


def propose_campaign(name: str, channel: str, target_segment: str, variants: List[Dict[str, Any]], rationale: str, goal: Dict[str, Any],
                     schedule: Optional[Dict[str, Any]] = None, ttl_hours: Optional[int] = None, market_hook_id: Optional[str] = None,
                     exclusions: Optional[List[str]] = None, frequency_cap: Optional[str] = None) -> Dict[str, Any]:
    check = campaign_brief_check(goal, variants, channel, market_linked=bool(market_hook_id), ttl_hours=ttl_hours)
    if not check["ok"]:
        return {"error": "brief rejected", "problems": check["problems"], "warnings": check["warnings"], "hint": "Fix the goal brief and call propose_campaign again."}
    payload = {"name": name, "channel": channel, "target_segment": target_segment, "variants": variants, "schedule": schedule, "ttl_hours": ttl_hours,
               "market_hook_id": market_hook_id, "goal": goal, "exclusions": exclusions or [], "frequency_cap": frequency_cap}
    p = approvals.propose("create_campaign", f"Campaign draft: {name}", payload, rationale, risk="medium")
    return {"proposal_id": p["id"], "status": p["status"], "preview": p.get("preview"), "brief_warnings": check["warnings"], "note": "Draft only. A human must approve before anything reaches MoEngage."}


def propose_flow(name: str, entry_trigger: str, steps: List[Dict[str, Any]], rationale: str, exit_rules: Optional[List[str]] = None) -> Dict[str, Any]:
    p = approvals.propose("create_flow", f"Flow draft: {name}", {"name": name, "entry_trigger": entry_trigger, "steps": steps, "exit_rules": exit_rules or []}, rationale, risk="medium")
    return {"proposal_id": p["id"], "status": p["status"], "preview": p.get("preview")}


def propose_pause_campaign(campaign_id: str, rationale: str) -> Dict[str, Any]:
    p = approvals.propose("pause_campaign", f"Pause campaign {campaign_id}", {"campaign_id": campaign_id}, rationale, risk="high")
    return {"proposal_id": p["id"], "status": p["status"], "preview": p.get("preview")}


# ── schema ────────────────────────────────────────────────────────────────────
def _fn(name, desc, props=None, required=None):
    return {"type": "function", "function": {"name": name, "description": desc,
            "parameters": {"type": "object", "properties": props or {}, "required": required or []}}}

OBJ = {"type": "object", "additionalProperties": True}
STR = {"type": "string"}

TOOL_SCHEMAS: List[Dict[str, Any]] = [
    _fn("get_status", "Connection mode (mock vs live), session validity and how many MoEngage endpoints are usable. Call first when unsure whether data is real."),
    _fn("list_campaigns", "List campaigns with metrics (sent, delivered, delivery_rate, ctr, conversions, revenue).", {"status": STR, "channel": STR}),
    _fn("get_campaign_stats", "Detailed stats for one campaign.", {"campaign_id": STR}, ["campaign_id"]),
    _fn("list_segments", "List user segments with criteria and reach."),
    _fn("list_flows", "List flows / journeys with status and stats."),
    _fn("get_analytics", "Workspace analytics overview (events, funnels). May be unavailable in live mode."),
    _fn("estimate_segment", "Estimate reach for segment criteria (live only when the estimate endpoint is learned).", {"criteria": OBJ}, ["criteria"]),
    _fn("rule_based_audit", "Deterministic threshold audit of every campaign (CTR/delivery/conversion verdicts)."),
    _fn("anomaly_report", "Statistical outliers per campaign metric vs the campaign's own history (modified z-score / IQR). Includes days of history available.", {"source": STR}),
    _fn("campaign_history", "Daily metric snapshots for one campaign.", {"campaign_id": STR, "days": {"type": "integer"}}, ["campaign_id"]),
    _fn("market_snapshot", "Crypto regime + movers, equities/commodities/macro quotes, Fear & Greed, INR marks, high-impact calendar, data gaps.", {"force": {"type": "boolean"}}),
    _fn("market_news", "Headlines by category (crypto|stocks|commodities|macro|regulatory) or a keyword query. Risk-flagged headlines included.", {"category": STR, "query": STR, "limit": {"type": "integer"}}),
    _fn("market_campaign_hooks", "Market-derived campaign hooks with segments, angle, timing, guardrails, and the angle policy for today's regime."),
    _fn("moengage_guidance", "Search the local MoEngage/CLM playbooks (channels, lifecycle stages, segmentation, measurement, market intelligence).", {"topic": STR}, ["topic"]),
    _fn("integration_status", "Which dashboard endpoints are learned/verified/unknown and what is needed to unlock them."),
    _fn("list_proposals", "Pending/approved/executed proposals in the approval queue.", {"status": STR}),
    _fn("propose_segment", "Propose a new segment for human approval. criteria is a structured filter object.",
        {"name": STR, "description": STR, "criteria": OBJ, "rationale": STR, "estimated_reach": {"type": "integer"}}, ["name", "criteria", "rationale"]),
    _fn("clm_program_audit", "Map every campaign to the lifecycle transition it serves; returns coverage per transition, uncovered transitions, broadcast share and unmapped campaigns. Start here for any programme-level question."),
    _fn("experiment_plan", "Sample size per arm and days-to-reach for a rate KPI (two-proportion test, 80% power, alpha 0.05). Use before proposing any campaign with a target.",
        {"baseline_rate_pct": {"type": "number"}, "min_detectable_lift_pct_points": {"type": "number"}, "daily_eligible_users": {"type": "integer"}, "control_group_pct": {"type": "number"}},
        ["baseline_rate_pct", "min_detectable_lift_pct_points", "daily_eligible_users"]),
    _fn("campaign_brief_check", "Validate a goal brief + copy against the goal discipline and compliance rules. Returns problems (blocking) and warnings.",
        {"goal": OBJ, "variants": {"type": "array", "items": OBJ}, "channel": STR, "market_linked": {"type": "boolean"}, "ttl_hours": {"type": "integer"}}, ["goal"]),
    _fn("propose_campaign", "Propose a DRAFT campaign for human approval. REQUIRES a complete goal brief: {transition, hypothesis, primary_kpi, target, guardrail_metric, control_group_pct (>=5), measurement_window_days, kill_criteria, suppressions[]}. variants: [{label,title,body,cta}]. Include ttl_hours for market-linked sends and exclusions[].",
        {"name": STR, "channel": STR, "target_segment": STR, "variants": {"type": "array", "items": OBJ}, "rationale": STR, "goal": OBJ, "schedule": OBJ,
         "ttl_hours": {"type": "integer"}, "market_hook_id": STR, "exclusions": {"type": "array", "items": STR}, "frequency_cap": STR},
        ["name", "channel", "target_segment", "variants", "rationale", "goal"]),
    _fn("propose_flow", "Propose a DRAFT flow/journey for approval. steps: [{type: wait|split|message|cohort, ...}].",
        {"name": STR, "entry_trigger": STR, "steps": {"type": "array", "items": OBJ}, "exit_rules": {"type": "array", "items": STR}, "rationale": STR}, ["name", "entry_trigger", "steps", "rationale"]),
    _fn("propose_pause_campaign", "Propose pausing a campaign (e.g. deliverability collapse or market suppression rule).", {"campaign_id": STR, "rationale": STR}, ["campaign_id", "rationale"]),
]

TOOLS: Dict[str, Callable[..., Dict[str, Any]]] = {
    "get_status": _safe(get_status), "list_campaigns": _safe(list_campaigns), "get_campaign_stats": _safe(get_campaign_stats),
    "list_segments": _safe(list_segments), "list_flows": _safe(list_flows), "get_analytics": _safe(get_analytics),
    "estimate_segment": _safe(estimate_segment), "rule_based_audit": _safe(rule_based_audit), "anomaly_report": _safe(anomaly_report),
    "campaign_history": _safe(campaign_history), "market_snapshot": _safe(market_snapshot), "market_news": _safe(market_news),
    "market_campaign_hooks": _safe(market_campaign_hooks), "moengage_guidance": _safe(moengage_guidance), "integration_status": _safe(integration_status),
    "list_proposals": _safe(list_proposals), "propose_segment": _safe(propose_segment), "propose_campaign": _safe(propose_campaign),
    "clm_program_audit": _safe(clm_program_audit), "experiment_plan": _safe(experiment_plan), "campaign_brief_check": _safe(campaign_brief_check),
    "propose_flow": _safe(propose_flow), "propose_pause_campaign": _safe(propose_pause_campaign),
}
