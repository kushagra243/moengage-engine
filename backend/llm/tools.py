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
            sig = ""
            try:
                from ..selfheal import record_error
                sig = record_error("tool", fn.__name__, e, kw)
            except Exception:
                pass
            return {"error": redact(str(e)), "error_type": type(e).__name__, "error_signature": sig, "self_repair": "call self_diagnose, then propose_code_change with the fix_request for this signature"}
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


def campaign_diagnosis(campaign_id: Optional[str] = None) -> Dict[str, Any]:
    """Deep diagnosis: funnel decomposition, trend stats, ranked likely causes with what to check first, and practical options (effort, effect, risk). One campaign or all."""
    from ..anomaly.diagnose import diagnose_campaign, diagnose_all
    src = _client().mode
    if campaign_id:
        return diagnose_campaign(campaign_id, src)
    return {"source": src, "campaigns": [{k: v for k, v in d.items() if k != "stats"} for d in diagnose_all(src, 12)]}


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


def market_campaign_hooks(limit: int = 8) -> Dict[str, Any]:
    from ..market import campaign_hooks
    h = campaign_hooks()
    keep = ("id", "trigger", "asset_class", "segments", "clm_stages", "channel", "angle", "copy_direction", "timing", "guardrails", "kpi")
    return {"regime": h.get("regime"), "angle_policy": h.get("angle_policy"), "compliance": h.get("compliance"),
            "hooks": [{k: hk.get(k) for k in keep} for hk in (h.get("hooks") or [])[:limit]], "blocked_hook_ids": h.get("blocked_hook_ids"),
            "total_hooks": len(h.get("hooks") or [])}


def moengage_guidance(topic: str) -> Dict[str, Any]:
    """Keyword search over the local knowledge base (channel playbooks, CLM stages, segmentation, measurement, market intelligence)."""
    words = [w for w in re.findall(r"[a-z0-9]+", topic.lower()) if len(w) > 2]
    hits = []
    skills_dir = os.path.join(os.path.dirname(os.path.dirname(KNOWLEDGE_DIR)), ".claude", "skills")
    for path in sorted(glob.glob(os.path.join(KNOWLEDGE_DIR, "*.md"))) + sorted(glob.glob(os.path.join(skills_dir, "*", "SKILL.md"))):
        text = open(path, encoding="utf-8").read()
        low = text.lower()
        score = sum(low.count(w) for w in words)
        if score:
            # return the best-matching sections
            sections = re.split(r"\n(?=#{1,3} )", text)
            ranked = sorted(sections, key=lambda s: -sum(s.lower().count(w) for w in words))
            label = os.path.basename(path) if os.path.basename(path) != "SKILL.md" else "skill:" + os.path.basename(os.path.dirname(path))
            hits.append({"doc": label, "score": score, "excerpt": "\n\n".join(ranked[:2])[:1500]})
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
    ("habitual_core", "Habitual → Core", [r"\bvip\b", r"loyalty", r"\btier", r"points", r"\bfee", r"premium", r"graduat", r"futures", r"\bperps?\b", r"leverage", r"tokeni[sz]ed", r"hedg", r"cross-?sell"]),
    ("slipping", "Slipping → recovered", [r"slipping", r"declin", r"portfolio review", r"check-?in", r"liquidat", r"margin call", r"recovery"]),
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


DERIVATIVE_WORDS = re.compile(r"\b(perp|perps|perpetual|futures|leverage|\d+x|margin|short|long)\b", re.I)
BANNED_COPY = re.compile(r"\b(guaranteed|will (rise|pump|moon|double)|buy now|sell now|can'?t lose|risk[- ]free|100%|to the moon|last chance( to buy)?|don'?t miss|passive income|earn while you sleep|safe (bet|investment)|sure ?shot|get in early|listing pump|moon|pump)\b", re.I)
VENUE_WORDS = re.compile(r"\b(hyperliquid|binance|bybit|okx|coinbase|kraken|kucoin|bitget|gate\.io|mexc|coinswitch|wazirx|zebpay|mudrex|delta exchange|zerodha|groww|upstox|robinhood|etoro)\b", re.I)
LEVERAGE_LURE = re.compile(r"\bup to \d+x\b|\b\d{2,3}x leverage\b", re.I)
STANDARD_SUPPRESSIONS = ("liquidat", "loss-dormant", "kyc", "dnd", "unsub", "ticket")


def campaign_brief_check(goal: Dict[str, Any], variants: Optional[List[Dict[str, Any]]] = None, channel: str = "push", market_linked: bool = False, ttl_hours: Optional[int] = None,
                         audience_countries: Optional[List[str]] = None, disclaimer_included: Optional[bool] = None) -> Dict[str, Any]:
    """Validate a campaign brief against the goal discipline, compliance (India ASCI, UK FCA, US) and copy rules before proposing.
    audience_countries: ISO codes of the audience (default India); disclaimer_included: whether the ASCI VDA disclaimer is carried by the message/landing."""
    problems: List[str] = []; warnings: List[str] = []
    countries = {c.upper() for c in (audience_countries or ["IN"])}
    text_all = " ".join(str(v.get("title", "")) + " " + str(v.get("body", "")) for v in (variants or []))
    derivative_content = bool(DERIVATIVE_WORDS.search(text_all)) or market_linked
    if "GB" in countries or "UK" in countries:
        if derivative_content:
            problems.append("UK residents in audience: crypto derivatives marketing to UK retail is banned (FCA); exclude GB from this send")
        if re.search(r"refer|bonus|reward|cashback|free", text_all, re.I):
            problems.append("UK residents in audience: incentives to invest are banned under the FCA cryptoasset promotion rules")
    if "US" in countries and derivative_content:
        problems.append("US persons in audience: perps/derivatives content must be geo-fenced away from US users")
    if "IN" in countries and derivative_content and not market_linked and re.search(r"trade (perps?|futures) now|open a (long|short)|start (trading )?with leverage", text_all, re.I):
        problems.append("India: derivatives content must be education-only until counsel clears acquisition (see crypto-compliance-copy)")
    if VENUE_WORDS.search(text_all):
        problems.append("copy names a liquidity venue or competitor (%s): we are CoinDCX; venue data is intelligence only and never appears in user copy" % VENUE_WORDS.search(text_all).group(0))
    if LEVERAGE_LURE.search(text_all):
        problems.append("leverage figures used as a lure ('up to 50x') are not allowed in marketing copy")
    if channel.lower() in ("email", "whatsapp", "in-app", "inapp", "in_app", "cards") and "IN" in countries and disclaimer_included is False:
        problems.append("India audience on a channel that can carry it: the ASCI VDA disclaimer must be included (email footer / template body / card)")
    if channel.lower() == "push" and "IN" in countries and disclaimer_included is None and re.search(r"offer|bonus|reward|% off|cashback", text_all, re.I):
        warnings.append("promotional push to India users: the landing screen must carry the ASCI disclaimer (push cannot)")
    supp = " ".join(str(x).lower() for x in (goal.get("suppressions") or []))
    if (market_linked or derivative_content) and not any(k in supp for k in ("liquidat",)):
        problems.append("market/derivatives send must suppress users liquidated in the last 14 days")
    if (market_linked or derivative_content) and "loss" not in supp:
        problems.append("market/derivatives send must suppress loss-dormant users")
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
    banned = BANNED_COPY
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
def proposal_detail(proposal_id: int) -> Dict[str, Any]:
    """Full proposal: payload, goal brief, preview, classification (category/product/stage), revisions and operator comments. Read before improving one."""
    from .. import approvals
    p = approvals.get_proposal(int(proposal_id))
    return p or {"error": "not found"}


def revise_proposal(proposal_id: int, changes: Dict[str, Any], note: str) -> Dict[str, Any]:
    """Improve a pending proposal before approval: partial payload merge (e.g. {"variants": [...]} or {"goal": {"control_group_pct": 20}} or {"exclusions": [...]}). Re-validated (brief check) and re-previewed; the revision and your note are recorded."""
    from .. import approvals
    try:
        p = approvals.update_payload(int(proposal_id), changes, actor="agent", note=note)
        return {"ok": True, "id": p["id"], "changed": sorted(changes.keys()), "stage": p.get("stage"), "preview_mode": (p.get("preview") or {}).get("mode")}
    except (approvals.ApprovalError, ValueError) as e:
        return {"ok": False, "error": str(e)}


def comment_proposal(proposal_id: int, text: str) -> Dict[str, Any]:
    """Leave a review note on a proposal (a suggestion the operator can accept, or a reply to their comment)."""
    from .. import approvals
    try:
        approvals.add_comment(int(proposal_id), text, actor="agent"); return {"ok": True}
    except approvals.ApprovalError as e:
        return {"ok": False, "error": str(e)}


def propose_segment(name: str, criteria: Dict[str, Any], rationale: str, description: str = "", estimated_reach: Optional[int] = None) -> Dict[str, Any]:
    p = approvals.propose("create_segment", f"Segment: {name}", {"name": name, "description": description, "criteria": criteria, "estimated_reach": estimated_reach}, rationale, risk="low")
    _capture_proposal_idea("create_segment", f"Segment: {name}", {"name": name}, rationale, p["id"])
    return {"proposal_id": p["id"], "status": p["status"], "preview": p.get("preview"), "note": "Awaiting human approval in the Approvals tab."}


def propose_campaign(name: str, channel: str, target_segment: str, variants: List[Dict[str, Any]], rationale: str, goal: Dict[str, Any],
                     schedule: Optional[Dict[str, Any]] = None, ttl_hours: Optional[int] = None, market_hook_id: Optional[str] = None,
                     exclusions: Optional[List[str]] = None, frequency_cap: Optional[str] = None, ice: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    check = campaign_brief_check(goal, variants, channel, market_linked=bool(market_hook_id), ttl_hours=ttl_hours)
    if not check["ok"]:
        return {"error": "brief rejected", "problems": check["problems"], "warnings": check["warnings"], "hint": "Fix the goal brief and call propose_campaign again."}
    from .. import ice as ice_mod
    ice_in = ice or {}
    ice_row = {**ice_mod.score(ice_in.get("impact", 6), ice_in.get("confidence", 6), ice_in.get("ease", 6)), "tagline": str(ice_in.get("tagline") or ice_mod.tagline(name, goal.get("primary_kpi") or "", target_segment, goal.get("hypothesis") or ""))[:220]}
    payload = {"name": name, "channel": channel, "target_segment": target_segment, "variants": variants, "schedule": schedule, "ttl_hours": ttl_hours,
               "market_hook_id": market_hook_id, "goal": goal, "exclusions": exclusions or [], "frequency_cap": frequency_cap, "ice": ice_row}
    p = approvals.propose("create_campaign", f"Campaign draft: {name}", payload, rationale, risk="medium")
    _capture_proposal_idea("create_campaign", f"Campaign: {name}", payload, rationale, p["id"])
    return {"proposal_id": p["id"], "status": p["status"], "preview": p.get("preview"), "brief_warnings": check["warnings"], "note": "Draft only. A human must approve before anything reaches MoEngage."}


def propose_flow(name: str, entry_trigger: str, steps: List[Dict[str, Any]], rationale: str, exit_rules: Optional[List[str]] = None) -> Dict[str, Any]:
    p = approvals.propose("create_flow", f"Flow draft: {name}", {"name": name, "entry_trigger": entry_trigger, "steps": steps, "exit_rules": exit_rules or []}, rationale, risk="medium")
    return {"proposal_id": p["id"], "status": p["status"], "preview": p.get("preview")}


def propose_pause_campaign(campaign_id: str, rationale: str) -> Dict[str, Any]:
    p = approvals.propose("pause_campaign", f"Pause campaign {campaign_id}", {"campaign_id": campaign_id}, rationale, risk="high")
    return {"proposal_id": p["id"], "status": p["status"], "preview": p.get("preview")}


def campaign_content(campaign_id: str) -> Dict[str, Any]:
    """Subject/title/body text preview, CTA, personalisation tokens, schedule/trigger details and segment filters for one campaign (never the HTML)."""
    c = _client()
    for r in c.get_campaigns():
        if str(r.get("id")) == str(campaign_id):
            return {k: r.get(k) for k in ("id", "name", "channel", "status", "target_segment", "segment_filters", "content_preview", "schedule", "conversion_goals", "tags", "delivery_type", "content_type")}
    return {"error": f"campaign {campaign_id} not found"}


def segment_detail(segment_id: str) -> Dict[str, Any]:
    """Definition/filters of a saved segment (public Segmentation API; mock returns criteria)."""
    c = _client()
    if c.mock_mode:
        for s in c.get_segments():
            if s.get("id") == segment_id:
                return s
        return {"error": "not found"}
    r = c.api().segment_get(segment_id)
    return {"data": r.get("data")}


def analytics_query(kind: str, body: Dict[str, Any]) -> Dict[str, Any]:
    """MoEngage Analytics Query API (funnels | retention | behavior | user-analysis). Experimental; needs Dashboard & Analyze permission on the Campaigns key. Body follows the MoEngage v5 analytics-query spec."""
    c = _client()
    if c.mock_mode:
        return {"note": "mock mode: analytics query not simulated", "kind": kind}
    return c.api().analytics_query(kind, body)


def experiment_readouts(limit: int = 20) -> Dict[str, Any]:
    """Experiments created from executed campaign proposals, with current readouts (Wilson intervals, pre-period comparison, claim limits)."""
    from ..experiments import list_experiments
    return {"experiments": [{k: e.get(k) for k in ("id", "campaign_name", "primary_kpi", "target", "control_group_pct", "window_days", "started_on", "status", "readout")} for e in list_experiments(limit)]}


def campaign_taxonomy(by: str = "group_key") -> Dict[str, Any]:
    """Campaigns grouped by naming-convention facets (programme, cohort, propensity, value tier, trader type, product, channel) with volume-weighted rates, plus the within-facet contrasts that carry insight."""
    from ..taxonomy import catalog, group
    camps = _client().get_campaigns()
    cat = catalog(camps)
    return {"source": _client().mode, "campaigns": cat["campaigns"], "programmes": cat["programmes"][:10], "cohorts": cat["cohorts"][:10],
            "groups": group(camps, by)[:20], "comparisons": cat["comparisons"][:8], "unknown_tokens": cat["unknown_tokens"][:15]}


def campaign_deep_dive(campaign_id: str, force: bool = False) -> Dict[str, Any]:
    """Structured expert analysis of one campaign (segment, users, content, process, next actions, experiment) from its full dossier; cached until data changes; runs on the bulk model tier."""
    from ..analysis import analyse
    r = analyse(campaign_id, force=force)
    d = r["dossier"]
    return {"campaign_id": campaign_id, "cached": r["cached"], "model": r["model"], "tier": r["tier"], "analysis": r["analysis"],
            "dossier_summary": {"taxonomy": d.get("taxonomy"), "metrics_today": d.get("metrics_today"), "history_days": d.get("history_days"), "peers": d.get("peers_same_group", [])[:4]}}


def growth_hacks(status: Optional[str] = None, limit: int = 10) -> Dict[str, Any]:
    """Curated, sourced growth hacks for crypto/fintech CLM ranked for this workspace's regime, gaps and channel mix (plus model-suggested ones marked unverified)."""
    from ..hacks import ranked
    r = ranked(status)
    return {"context": r["context"], "hacks": [{k: h.get(k) for k in ("id", "title", "category", "what", "why", "source", "how", "kpi", "effort", "relevance", "relevance_reasons", "source_kind", "verified", "status")} for h in r["hacks"][:limit]]}


def record_ideas(ideas: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Capture recommendations (campaigns, segments, experiments, growth hacks, fixes) into the persistent growth feed."""
    from .. import growth
    clean = []
    for i in ideas or []:
        if not isinstance(i, dict) or not i.get("title"):
            continue
        i = dict(i)
        i.setdefault("kind", "growth_hack"); i.setdefault("priority", 60)
        clean.append(i)
    res = growth.upsert_ideas(clean, "agent")
    return {"captured": res["added"], "already_known": res["refreshed"], "feed_counts": growth.counts()}


def moengage_api_reference(query: Optional[str] = None, method: Optional[str] = None, path: Optional[str] = None) -> Dict[str, Any]:
    """Local catalog of every documented MoEngage API (131 operations, 32 OpenAPI specs). Search by words, or give method+path for full detail."""
    from .. import api_catalog
    if method and path:
        return api_catalog.operation_detail(method, path)
    if query:
        return {"matches": api_catalog.search(query, limit=12), "next": "call again with method + path for parameters, body schema and auth", "overview": api_catalog.overview()["specs"]}
    return api_catalog.overview()


def moengage_api_read(method: str, path: str, path_vars: Optional[Dict[str, str]] = None, params: Optional[Dict[str, Any]] = None, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Call a documented READ-SAFE MoEngage API directly (GET, or POST search/meta/stats). Writes are refused; propose them instead."""
    c = _client()
    if c.mock_mode:
        return {"mock": True, "note": "mock mode: no live API call; switch Demo/mock off in Settings to use real endpoints", "would_call": f"{method.upper()} {path}"}
    return c.api().call_documented(method, path, path_vars=path_vars, params=params, body=body)


def skill(name: str) -> Dict[str, Any]:
    """Load one of the shared skills (.claude/skills) into context: moengage, moengage-api, moengage-engine, clm-operator."""
    from ..skills import read_skill
    return read_skill(name)


def remember_guidance(text: str, scope: str = "general") -> Dict[str, Any]:
    """Persist a standing instruction the operator gave ('from now on…', 'always…', 'never…'). It is injected into every future system prompt and shown in the Agent tab."""
    from .. import guidance
    try:
        return {"saved": guidance.add(text, author="agent", scope=scope)}
    except ValueError as e:
        return {"error": str(e)}


def set_engine_setting(key: str, value: Any) -> Dict[str, Any]:
    """Change an allowlisted engine knob immediately (autopilot, schedule, refresh cadence, analysis batch, market universe, taxonomy codes, model temperature…). Secrets/provider/security are not changeable here."""
    from .. import guidance
    return guidance.set_engine_setting(key, value, actor="agent")


def propose_code_change(title: str, request: str, scope: str = "any", rationale: str = "", context: str = "", draft_now: bool = True) -> Dict[str, Any]:
    """Queue a change to the engine's own code/UI/CLI (new view, column, chart, command, rule, tool). It is implemented on an isolated git branch by Claude Code (or the model), tests run, and the human approves the diff before it merges and the server restarts."""
    from .. import approvals, devagent
    p = approvals.propose("code_change", title[:120], {"request": request, "scope": scope if scope in devagent.SCOPES else "any", "context": context[:3000]}, rationale=rationale or request[:400], risk="medium", created_by="agent")
    if draft_now and not p.get("duplicate_of_pending") and (p.get("preview") or {}).get("status") == "draft_pending":
        devagent.draft_async(p["id"], actor="agent")
        p["drafting"] = "started in background; the diff appears on the proposal in a few minutes"
    _capture_proposal_idea("fix", title, {"request": request[:500], "scope": scope}, rationale or request[:300], p["id"])
    return {"proposal_id": p["id"], "status": p["status"], "preview": p.get("preview"), "drafting": p.get("drafting"), "duplicate_of_pending": p.get("duplicate_of_pending", False)}


def segment_study(refresh: bool = False, days: int = 30) -> Dict[str, Any]:
    """Cohort families decoded from segment nomenclature (HVT_Sep26 → family HVT, version 2026-09): versions, reach, attached campaigns and performance, version-over-version deltas, flags and studies to run."""
    from .. import segments
    c = _client()
    if refresh:
        segments.sync(c.get_segments())
    elif not segments.registry(limit=1):
        segments.sync(c.get_segments())
    return segments.study(c.get_campaigns(), days=days)


def define_nomenclature(code: str, meaning: str) -> Dict[str, Any]:
    """Teach the engine a segment/campaign name code, e.g. code='HVT', meaning='value:High value trader' (facet:label). Applies immediately to every decode."""
    from .. import guidance
    if ":" not in meaning:
        meaning = "cohort:" + meaning
    return guidance.set_engine_setting("taxonomy_codes", {code.upper(): meaning}, actor="agent")


def list_sops(campaign_type: Optional[str] = None) -> Dict[str, Any]:
    """Standard operating procedures for campaign types (sequence, cohort family, duration, frequency, holdout, KPI, kill rules, checks)."""
    from .. import sops
    rows = sops.list_sops()
    if campaign_type:
        rows = [r for r in rows if r["campaign_type"] == campaign_type]
    return {"sops": [{k: r.get(k) for k in ("id", "name", "campaign_type", "transition", "objective", "steps_count", "duration_days", "frequency", "holdout_pct", "primary_kpi", "framework_ok", "version", "source")} | {"segment_family": (r.get("audience") or {}).get("segment_family")} for r in rows], "types": list(sops.CAMPAIGN_TYPES)}


def sop_detail(sop_id: str) -> Dict[str, Any]:
    from .. import sops
    return sops.get_sop(sop_id) or {"error": f"unknown SOP {sop_id}"}


def define_sop(spec: Dict[str, Any]) -> Dict[str, Any]:
    """Create or update an SOP. spec: {id?, name, campaign_type, transition, objective, audience{segment_family, exclusions[], min_reach, jurisdictions_excluded[]}, steps[{day, channel, purpose, copy_brief, send_time_ist, condition?, ttl_hours?}], duration_days, frequency{cadence, max_messages_per_user_per_week}, holdout_pct, primary_kpi, target, guardrail_metric, measurement_window_days, kill_criteria[], compliance{disclaimer_channels[], banned_angles[]}}. Validated against the framework."""
    from .. import sops
    return sops.define_sop(spec, author="agent")


def run_sop(sop_id: str, segment_name: Optional[str] = None, start_date: Optional[str] = None, variants_by_step: Optional[Dict[str, Any]] = None, dry_run: bool = False, ice: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Run an SOP on a cohort: resolves the segment (exact name or latest version of the family), pre-flight checks, then one approval-gated campaign proposal per step. Provide variants_by_step {"0": [{label,title,body,cta}], ...} written per crypto-copywriting; use dry_run first. Pass ice {impact, confidence, ease, tagline} so the board ranks the run."""
    from .. import sops
    return sops.run_sop(sop_id, segment_name=segment_name, start_date=start_date, variants_by_step=variants_by_step, created_by="agent", dry_run=dry_run, ice=ice)


def competitor_intel(force: bool = False) -> Dict[str, Any]:
    """Internal competitive intelligence: tracked Indian and global venues vs CoinDCX — exchange volume table with INR-spot share, pair battles (our share per pair), volume surges at competitors vs earlier today, listing gaps (pairs they have, we don't), our edges, funding edges, and ranked actions with an owner (marketing / product / liquidity) and the SOP to run. Never name competitors in copy."""
    from ..market import competitors
    from ..market.context import _latest
    ctx = _latest(6 * 3600) or {}
    return competitors.intel({"crypto_markets": ctx.get("crypto_markets") or []}, force=force)


def competitor_benchmarks(category: Optional[str] = None, force: bool = False) -> Dict[str, Any]:
    """Best in industry per category (spot, perps, options, commodities_tokenised) in India and internationally — Binance, OKX, Bybit, Bitget, Coinbase, Kraken, KuCoin, Gate, MEXC, HTX, Deribit, Delta India, Indian INR venues — with 24h volume, OI, markets, fees, our figure, the multiple to the leader, and concrete 'match their numbers' targets by pair. Free sources only; internal."""
    from ..market import benchmarks
    return benchmarks.benchmarks(category, force=force)


def competitor_campaigns(hours: int = 48, venue: Optional[str] = None, force: bool = False) -> Dict[str, Any]:
    """What competitors are running right now (last N hours): announcements (Binance, Bybit, OKX, Bitget), blog posts, Google News per Indian venue, App Store release notes — classified (trading_competition, fee_promo, cashback_bonus, stock_perps, options, listing, product_launch, earn_apy, referral, festival_offer…) and ranked by impact with our counter SOP; plus App Store Finance rank / rating / version for us and rivals. Internal only."""
    from ..market import campaign_intel
    return campaign_intel.campaigns(hours=hours, venue=venue, force=force)


def onchain_vs_cex(force: bool = False) -> Dict[str, Any]:
    """Hyperliquid (the venue behind our perps) versus centralised exchanges: HL daily volume, OI and users vs Binance/OKX/Bybit/Bitget/Gate/MEXC (CMC), rank and share; on-chain perps OI landscape (DefiLlama, free); per-coin OI share and funding (per hour) across Hyperliquid, Binance, Bybit, OKX with the cheapest venue for longs; Coinglass section if a key is set. Internal."""
    from ..market import onchain_cex
    return onchain_cex.compare(force=force)


def signal_catalog() -> Dict[str, Any]:
    """Signal Bridge: which market/behaviour signals can fire MoEngage Business Events (regime flip, tier-0, asset move, new listing, funding crowding, OI flush, salary week, macro print T−24h, competitor surge on a pair we list), what is live right now, which rules are approved, today's fires and the last evaluation."""
    from .. import signals
    c = signals.catalog(); c["fires"] = c["fires"][:10]
    return c


def propose_signal_rule(signal_id: str, max_per_day: Optional[int] = None, rationale: str = "") -> Dict[str, Any]:
    """Propose a standing rule (human approves once) so the engine fires the MoEngage business event automatically whenever the signal is detected, within the daily cap, quiet hours and regime policy."""
    from .. import signals
    return signals.propose_rule(signal_id, max_per_day, rationale=rationale, created_by="agent")


def signal_fires(limit: int = 30) -> Dict[str, Any]:
    """Ledger of business events the engine fired (or would have, in mock mode)."""
    from .. import signals
    return {"fires": signals.fires(limit)}


def compliance_sweep(force: bool = False) -> Dict[str, Any]:
    """Lint the copy of every live campaign against the India rules (venue names, banned claims, ASCI words, leverage lures, direction, price urgency, push length, missing disclaimer, onboarding incentives). Findings carry the fix."""
    from .. import compliance_sweep as cs
    return cs.sweep() if force else (cs.latest() or cs.sweep())


def refresh_learnings() -> Dict[str, Any]:
    """Recompile the our-learnings skill from the experiment ledger, readouts, deep dives, QA trend and India-fit review; then load it with skill('our-learnings')."""
    from .. import learnings
    return learnings.refresh()


def sop_india_review(sop_id: Optional[str] = None) -> Dict[str, Any]:
    """India-fit review of SOPs against our own skills (ASCI VDA disclaimer and forbidden words, TDS/tax framing, derivatives education-only posture, DLT/WhatsApp windows, DND, Hinglish for mass cohorts, salary-week timing, no onboarding bonuses): score, flags with fixes, missing India SOPs."""
    from .. import sop_india
    r = sop_india.review(sop_id)
    if not sop_id:
        r["sops"] = [{k: x[k] for k in ("id", "score", "status", "auto_fixable")} | {"flags": [f["rule"] for f in x["flags"]]} for x in r["sops"] if x["status"] != "india-ready"][:30]
    return r


def sop_india_fix(sop_id: str) -> Dict[str, Any]:
    """Apply the deterministic India fixes to one SOP (new framework-checked version): disclaimer channels, banned angles, exclusions, TDS line, UPI rails, send windows, WhatsApp utility, Hinglish note, salary-week timing."""
    from .. import sop_india
    return sop_india.apply_fixes(sop_id, actor="agent")


def workspace_analysis(force: bool = False) -> Dict[str, Any]:
    """The complete MoEngage programme analysis: programme totals and coverage, lifecycle coverage per transition, channel health vs benchmarks, campaign league table (health, diagnosis, cause, do-first, deep-dive verdict), facets that respond, cohorts, experiments, guardrails, and the analyst narrative (what works, what is broken, structural gaps, ICE-ranked actions, risks, data gaps). Start here for any 'how is the programme doing' question."""
    from .. import workspace_analysis as wa
    r = wa.report(force=force)
    r["campaigns"]["league"] = r["campaigns"]["league"][:25]
    r.pop("history", None)
    return r


def qa_report(force: bool = False) -> Dict[str, Any]:
    """Fact-check report over everything the boards show: freshness, cross-source agreement, sanity bounds, references (SOPs/segments/KPIs), copy claims in pending drafts, source health, information completeness; safe improvements are applied and listed. Fails first; each carries a fix."""
    from .. import qa
    r = qa.report(force=force)
    r["checks"] = [c for c in r["checks"] if c["status"] != "pass"][:30] + [{"note": f"{r['counts'].get('pass', 0)} checks passing (hidden)"}]
    return r


def verify_claims(claims: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Verify numeric claims against the live context before asserting them: [{claim, value, unit}] → verified true/false with how. Use for any figure you did not just read from a tool."""
    from .. import qa
    return qa.verify_claims(claims)


def structural_audit() -> Dict[str, Any]:
    """Which must-have lifecycle campaigns (from the CLM playbooks) are missing from the live programme: KYC rescue, deposit-failure recovery, funded→first trade, second trade 72h, own-asset alerts, weekly recap, fee-tier nudge, intent-based graduation, tokenised cross-sell, slipping vs own baseline, dormant by cause, liquidation recovery, funding nudges, stress mode, holdouts, broadcast cap, frequency caps, web3 safety, SIP nurture, WhatsApp utility, Cards, channel recovery, re-KYC, cohort refresh. Each gap carries ICE, tagline, segment, SOP, KPI, benchmark. These are the P0 recommendations."""
    from .. import structural
    return structural.audit()


def money_flow() -> Dict[str, Any]:
    """Where capital and attention are going today and what traders are doing: risk-appetite composite, BTC/ETH dominance shift, stablecoin net minting (7d) and chain inflows, sector rotation in/out (CoinGecko categories), venue volume share crypto vs US-stock vs index vs commodity perps, attention spikes vs 7-day average, leverage build/flush, web3 speculation gauge — plus plain-English behaviour reads and prioritised recommendations with segment, SOP, KPI and what to avoid."""
    from ..market import moneyflow
    from ..market.context import _latest
    return moneyflow.lab(_latest(6 * 3600) or {})


def market_flash(hours: int = 6) -> Dict[str, Any]:
    """Breaking now: Tier-0, regime, big moves across crypto / US-stock / index / commodity perps, OI surges, listings, funding extremes, risk headlines, competitor actions, macro prints in 24h, web3 spikes — each with the products it touches. Plus biggest news, top OI assets and intel per CoinDCX product."""
    from ..market import feed
    from ..market.context import _latest
    ctx = _latest(6 * 3600) or {}
    return {"flash": feed.flash(ctx, hours=hours), "news": feed.biggest_news(ctx, 8), "top_oi": feed.top_oi(ctx, 8), "by_product": feed.by_product(ctx)}


def campaign_from_alert(product: str, headline: str, detail: str = "", sop_id: Optional[str] = None, segment_name: Optional[str] = None, dry_run: bool = True) -> Dict[str, Any]:
    """Turn a market alert into an approval-gated campaign on the go: picks the product's default SOP (or sop_id), builds two compliant fact+tool variants (rewrite them with revise_proposal if you can do better), runs pre-flight and queues proposals. dry_run first."""
    from .. import sops
    return sops.run_from_alert(product, headline, detail, sop_id=sop_id, segment_name=segment_name, created_by="agent", dry_run=dry_run)


def competitor_dossier(venue: str) -> Dict[str, Any]:
    """Marketing dossier for one rival (delta, binance, bybit, okx, bitget, coinbase, kraken, kucoin, gate, mexc, htx, wazirx, zebpay, giottus, koinbx, unocoin, hyperliquid): market position by category, share and 7-day changes, fees, traffic, app rank/rating/version, campaigns in 7 days (types, cadence, channels, audience focus), inferred playbook, latest pair moves, our counters and how to beat them. Internal."""
    from ..market import dossiers, competitors, benchmarks
    from .. import brain
    try:
        it = brain.intel()
    except Exception:
        it = {}
    return dossiers.dossier(venue, {**competitors.intel({"crypto_markets": []}), "rivals": it.get("rivals") or []}, benchmarks.benchmarks())


def pair_battle(symbol: str) -> Dict[str, Any]:
    """One pair across every tracked venue: 24h volume, share, OI, funding, 24h change; whether we list it."""
    from ..market import competitors
    return competitors.pair_battle(symbol)


def web3_trending(chain: Optional[str] = None, force: bool = False) -> Dict[str, Any]:
    """Trending on-chain tokens for our Web3 chains (Solana, Base, BNB Chain, Ethereum, Robinhood Chain), quality-gated (liquidity, volume, age) and labelled unverified; paid boosts listed separately. Data only — never a recommendation, never name the data venue in copy."""
    from ..market.onchain import trending
    t = trending(force=force)
    if chain:
        t = {**t, "trending": [r for r in t.get("trending", []) if r.get("chain") == chain.lower()], "rejected": [r for r in t.get("rejected", []) if r.get("chain") == chain.lower()]}
    t.pop("profiles", None)
    return t


def product_cohorts() -> Dict[str, Any]:
    """Product affinity view: families grouped by product (spot, SIP, crypto perps, US-stock/index/commodity perps, options, earn, web3) with reach and performance, plus the treatment matrix (pillars, cadence, never-list, cross-sell on intent, SOPs, Tier-0 lens)."""
    from .. import sops
    st = segment_study()
    return {"by_product": st.get("by_product", []), "matrix": sops.product_cohort_matrix()}


def announcement_lenses(event: str) -> Dict[str, Any]:
    """Tier-0 plan for a major move / geopolitical / regulatory event: core-fact rules, one lens per product cohort, suppressions, channel plan, regime rule. Fill the lenses, then run_sop('sop_global_announcement_lenses')."""
    from ..products import announcement_lenses as _al
    return _al(event)


def request_data(kind: str, title: str, why: str, spec: str = "", unblocks: Optional[List[str]] = None, priority: int = 50) -> Dict[str, Any]:
    """Ask the team for a segment, event, attribute, export, API access, dashboard capture or content you need (kind), with why it matters and which campaigns it unblocks. Shown in the Agent tab; link it in flight plans."""
    from .. import datarequests
    return datarequests.request(kind, title, why, spec=spec, unblocks=unblocks, priority=priority, requested_by="agent")


def data_requests(status: Optional[str] = None) -> Dict[str, Any]:
    from .. import datarequests
    return {"requests": datarequests.list_requests(status)}


def channel_matrix() -> Dict[str, Any]:
    """User state × channel capability matrix: allowed purposes, formats (push / email / WhatsApp / SMS / in-app bottom sheets / cards), caps, compliance and the SOPs for each."""
    from .. import sops
    return sops.channel_matrix()


def sop_runs(limit: int = 20) -> Dict[str, Any]:
    from .. import sops
    return {"runs": sops.list_runs(limit)}


def north_star() -> Dict[str, Any]:
    """The sentence the programme optimises for, plus current communication limits and this month's flight-plan summary."""
    from .. import guardrails, plans
    return {"north_star": guardrails.north_star(), "limits": guardrails.limits(), "month": plans.month_summary()}


def set_north_star(text: str) -> Dict[str, Any]:
    from .. import guardrails
    return guardrails.set_north_star(text, actor="agent")


def comms_limits(regime: Optional[str] = None, stage: Optional[str] = None) -> Dict[str, Any]:
    """Global hard communication limits per user (per channel per day/week, stage overrides, regime multipliers) and the effective caps for a regime/stage."""
    from .. import guardrails
    return {"limits": guardrails.limits(), "effective": guardrails.effective_limits(regime, stage)}


def set_comms_limits(patch: Dict[str, Any]) -> Dict[str, Any]:
    """Hard-set global limits from the team, e.g. {"per_user": {"push": {"per_week": 3}}, "total_per_week": 6, "stage_overrides": {"Core": {"total_per_week": 3}}}. Enforced immediately in SOP checks, run pre-flight and brief checks."""
    from .. import guardrails
    return guardrails.set_limits(patch, actor="agent")


def peace_index() -> Dict[str, Any]:
    """Per cohort family: planned + observed touches per user per week vs the effective limit (regime- and stage-adjusted) → too_much / in_band / too_little, with breaches. Cohort-level estimate (no per-user send logs in the public API)."""
    from .. import guardrails
    c = _client()
    regime = None
    try:
        from ..market.context import _latest
        regime = (((_latest(6 * 3600) or {}).get("hooks") or {}).get("regime"))
    except Exception:
        pass
    return guardrails.peace_index(c.get_campaigns(), regime)


def guardrail_monitor() -> Dict[str, Any]:
    """SOP misses (overdue steps awaiting approval) and breaches (promotional steps executed in stress regimes); posts priority fixes to the growth feed."""
    from .. import guardrails
    regime = None
    try:
        from ..market.context import _latest
        regime = (((_latest(6 * 3600) or {}).get("hooks") or {}).get("regime"))
    except Exception:
        pass
    return guardrails.sop_monitor(regime)


def write_flight_plan(spec: Dict[str, Any]) -> Dict[str, Any]:
    """Write the full campaign requirement document (Flight Plan) for a programme. spec: {title, month 'YYYY-MM', objective, north_star_link, transition, cohort_family, sop_id?, audience{definition, version, reach, exclusions[], jurisdictions_excluded[]}, sequence[{day, channel, purpose, condition?, send_time_ist, variants[{label,title,body,cta}]}], kpi{primary, target, baseline, guardrail, holdout_pct, window_days}, experiment{sample_size, days_to_read, kill_criteria[]}, timeline[{date, what}], month_on_month{last_month_result, change, expected_gain}, risks[], compliance[], whats_possible{now[], needs_data[], needs_api[]}}. Stored, rendered to Markdown, limits-checked, and recorded in the growth feed."""
    from .. import plans
    return plans.create_plan(spec, author="agent")


def flight_plans(month: Optional[str] = None, plan_id: Optional[int] = None) -> Dict[str, Any]:
    """List flight plans (optionally for a month) or fetch one with its Markdown document."""
    from .. import plans
    if plan_id:
        return plans.get_plan(int(plan_id)) or {"error": "not found"}
    return {"plans": plans.list_plans(month), "summary": plans.month_summary(month)}


def model_routes() -> Dict[str, Any]:
    """Which model handles which purpose (chat, autopilot, analysis, brief, copy, classification, code, review, test) with fallback chains, the free-tier list, and 7-day spend per model. Change with set_engine_setting('llm_routes', {...})."""
    from .provider import routes, llm_settings, FREE_BULK_MODELS
    from .usage import summary
    cfg = llm_settings()
    u = summary(7)
    return {"provider": cfg["provider"], "main_model": cfg["model"], "routes": routes(cfg), "free_models": FREE_BULK_MODELS if cfg["provider"] == "openrouter" else [],
            "spend_7d_by_tier": u.get("by_tier"), "how_to_change": "set_engine_setting('llm_routes', {\"copy\": [\"anthropic/claude-sonnet-4.5\"], \"analysis\": [\"deepseek/deepseek-chat-v3-0324:free\"]}) — first model that answers wins; main model is always the last fallback",
            "note": "skills and tools are model-agnostic; the same prompts run on any OpenAI-compatible model"}


def token_usage(days: int = 7) -> Dict[str, Any]:
    """Model spend ledger: calls, prompt/completion/cached tokens and estimated cost by purpose and tier, plus the current budgets (tool output chars, history turns, rounds)."""
    from .usage import summary
    return summary(days)


def self_diagnose(run_tests: bool = False) -> Dict[str, Any]:
    """Health report of the engine itself: grouped tool errors (48h), failed scheduler steps, failed proposals, server-log tracebacks, optional test run, and ready-to-file fix requests for propose_code_change."""
    from ..selfheal import health_report
    return health_report(run_tests=run_tests)


def rollback_last_change(reason: str = "requested by operator") -> Dict[str, Any]:
    """Revert the last merged code change (git revert of the merge commit) and restart. Use only when the operator asks or a merged change is clearly broken."""
    from ..selfheal import rollback_last_merge
    from .. import devagent
    r = rollback_last_merge(reason)
    if r.get("ok") and devagent.RESTART:
        r["restart_scheduled"] = devagent.schedule_restart()
    return r


def _capture_proposal_idea(kind: str, title: str, payload: Dict[str, Any], rationale: str, proposal_id: int) -> None:
    try:
        from .. import growth
        goal = payload.get("goal") or {}
        growth.upsert_ideas([{"kind": "trending_campaign" if kind == "create_campaign" else "growth_hack", "title": title, "why": rationale,
                              "how": f"Proposal #{proposal_id} in the Approvals tab. " + (f"Goal: {goal.get('transition')} · KPI {goal.get('primary_kpi')} · holdout {goal.get('control_group_pct')}%" if goal else ""),
                              "segment": payload.get("target_segment") or payload.get("name", ""), "channel": payload.get("channel", ""), "kpi": goal.get("primary_kpi", ""),
                              "transition": goal.get("transition", ""), "priority": 75, "data": {"proposal_id": proposal_id}}], "agent")
        conn_ids = [r["id"] for r in growth.list_ideas(status=None, limit=200) if r.get("data", {}).get("proposal_id") == proposal_id]
        for cid in conn_ids:
            growth.set_status(cid, "proposed")
    except Exception:
        pass


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
    _fn("campaign_diagnosis", "Deep diagnosis of a campaign (or all): which funnel stage moved vs 28d, trend stats, ranked likely causes with evidence and what to check first, and practical options with effort/effect/risk. Use before recommending any fix.", {"campaign_id": STR}),
    _fn("market_snapshot", "Crypto regime + movers, equities/commodities/macro quotes, Fear & Greed, INR marks, high-impact calendar, data gaps.", {"force": {"type": "boolean"}}),
    _fn("market_news", "Headlines by category (crypto|stocks|commodities|macro|regulatory) or a keyword query. Risk-flagged headlines included.", {"category": STR, "query": STR, "limit": {"type": "integer"}}),
    _fn("market_campaign_hooks", "Market-derived campaign hooks with segments, angle, timing, guardrails, and the angle policy for today's regime.", {"limit": {"type": "integer"}}),
    _fn("moengage_guidance", "Search the local MoEngage/CLM playbooks (channels, lifecycle stages, segmentation, measurement, market intelligence).", {"topic": STR}, ["topic"]),
    _fn("integration_status", "Which dashboard endpoints are learned/verified/unknown and what is needed to unlock them."),
    _fn("list_proposals", "Pending/approved/executed proposals in the approval queue.", {"status": STR}),
    _fn("proposal_detail", "Full proposal with goal brief, preview, classification, revisions and operator comments.", {"proposal_id": {"type": "integer"}}, ["proposal_id"]),
    _fn("revise_proposal", "Improve a pending proposal before approval by merging changes into its payload (variants, goal fields, exclusions, schedule, target_segment…). Brief check re-runs; revision recorded with your note.", {"proposal_id": {"type": "integer"}, "changes": OBJ, "note": STR}, ["proposal_id", "changes", "note"]),
    _fn("comment_proposal", "Leave a suggestion or reply on a proposal thread.", {"proposal_id": {"type": "integer"}, "text": STR}, ["proposal_id", "text"]),
    _fn("propose_segment", "Propose a new segment for human approval. criteria is a structured filter object.",
        {"name": STR, "description": STR, "criteria": OBJ, "rationale": STR, "estimated_reach": {"type": "integer"}}, ["name", "criteria", "rationale"]),
    _fn("clm_program_audit", "Map every campaign to the lifecycle transition it serves; returns coverage per transition, uncovered transitions, broadcast share and unmapped campaigns. Start here for any programme-level question."),
    _fn("experiment_plan", "Sample size per arm and days-to-reach for a rate KPI (two-proportion test, 80% power, alpha 0.05). Use before proposing any campaign with a target.",
        {"baseline_rate_pct": {"type": "number"}, "min_detectable_lift_pct_points": {"type": "number"}, "daily_eligible_users": {"type": "integer"}, "control_group_pct": {"type": "number"}},
        ["baseline_rate_pct", "min_detectable_lift_pct_points", "daily_eligible_users"]),
    _fn("campaign_brief_check", "Validate a goal brief + copy against the goal discipline and compliance rules. Returns problems (blocking) and warnings.",
        {"goal": OBJ, "variants": {"type": "array", "items": OBJ}, "channel": STR, "market_linked": {"type": "boolean"}, "ttl_hours": {"type": "integer"}, "audience_countries": {"type": "array", "items": STR}, "disclaimer_included": {"type": "boolean"}}, ["goal"]),
    _fn("propose_campaign", "Propose a DRAFT campaign for human approval. REQUIRES a complete goal brief: {transition, hypothesis, primary_kpi, target, guardrail_metric, control_group_pct (>=5), measurement_window_days, kill_criteria, suppressions[]}. variants: [{label,title,body,cta}]. Include ttl_hours for market-linked sends and exclusions[]. ALWAYS pass ice: {impact 1-10, confidence 1-10, ease 1-10, tagline} (GrowthHackers ICE) — the board ranks by it.",
        {"name": STR, "channel": STR, "target_segment": STR, "variants": {"type": "array", "items": OBJ}, "rationale": STR, "goal": OBJ, "schedule": OBJ,
         "ttl_hours": {"type": "integer"}, "market_hook_id": STR, "exclusions": {"type": "array", "items": STR}, "frequency_cap": STR, "ice": OBJ},
        ["name", "channel", "target_segment", "variants", "rationale", "goal"]),
    _fn("propose_flow", "Propose a DRAFT flow/journey for approval. steps: [{type: wait|split|message|cohort, ...}].",
        {"name": STR, "entry_trigger": STR, "steps": {"type": "array", "items": OBJ}, "exit_rules": {"type": "array", "items": STR}, "rationale": STR}, ["name", "entry_trigger", "steps", "rationale"]),
    _fn("campaign_content", "Content preview (subject/title/body text, CTA, personalisation tokens), schedule/trigger details and segment filters for one campaign. Use before critiquing copy or process.", {"campaign_id": STR}, ["campaign_id"]),
    _fn("segment_detail", "Definition and filters of a saved segment.", {"segment_id": STR}, ["segment_id"]),
    _fn("analytics_query", "MoEngage Analytics Query API (funnels|retention|behavior|user-analysis). Experimental; body per the v5 spec.", {"kind": STR, "body": OBJ}, ["kind", "body"]),
    _fn("experiment_readouts", "Readouts of experiments created from executed proposals: KPI vs pre-period with 95% intervals and what may or may not be claimed.", {"limit": {"type": "integer"}}),
    _fn("campaign_taxonomy", "Group campaigns by naming-convention facets (programme, cohort, propensity, value, trader, product, channel) with volume-weighted rates and the biggest within-facet contrasts. Use for any segment-level or 'which cohorts respond' question.", {"by": STR}),
    _fn("campaign_deep_dive", "Deep, structured analysis of one campaign from its full dossier (taxonomy, metrics, history, diagnosis, peers, market). Cached until data changes. Use before recommending changes to a specific campaign.", {"campaign_id": STR, "force": {"type": "boolean"}}, ["campaign_id"]),
    _fn("growth_hacks", "Sourced growth tactics working in crypto/fintech CLM, ranked for this workspace today; use when asked what is trending or what to try next.", {"status": STR, "limit": {"type": "integer"}}),
    _fn("record_ideas", "Capture every recommendation you make (campaign, segment, experiment, growth hack, fix) into the persistent growth feed so nothing is lost. ideas: [{title, kind: trending_campaign|growth_hack|market_play|moengage_activity|fix, why (cite data), how (MoEngage steps), segment, channel, angle, kpi, transition, effort, expected_impact, priority}].",
        {"ideas": {"type": "array", "items": OBJ}}, ["ideas"]),
    _fn("propose_pause_campaign", "Propose pausing a campaign (e.g. deliverability collapse or market suppression rule).", {"campaign_id": STR, "rationale": STR}, ["campaign_id", "rationale"]),
    _fn("model_routes", "Purpose → model routing over OpenRouter (free and paid), fallback chains, spend per tier; change with set_engine_setting('llm_routes', …)."),
    _fn("token_usage", "Model spend by purpose/tier for the last N days with current budgets. Use when asked about cost or before running expensive analyses.", {"days": {"type": "integer"}}),
    _fn("self_diagnose", "Diagnose the engine itself: grouped tool errors, failed jobs/proposals, log tracebacks, optional test run, with ready-to-file fix requests. Call whenever a tool returned an error or a job failed; then propose_code_change with the fix_request.", {"run_tests": {"type": "boolean"}}),
    _fn("rollback_last_change", "Revert the last merged code change and restart (only when asked, or when the change is clearly broken).", {"reason": STR}),
    _fn("north_star", "The north star sentence, current communication limits and this month's flight-plan coverage. Read before planning."),
    _fn("set_north_star", "Replace the north star sentence (only when the team asks).", {"text": STR}, ["text"]),
    _fn("comms_limits", "Global hard per-user communication limits (channel × per day/week, stage overrides, regime multipliers) and effective caps for a regime/stage.", {"regime": STR, "stage": STR}),
    _fn("set_comms_limits", "Hard-set limits from the team (merge patch, validated). Applies immediately to SOP checks, pre-flight and brief checks.", {"patch": OBJ}, ["patch"]),
    _fn("peace_index", "Are we reaching each cohort too much or too little? Planned + observed touches per user/week vs effective caps by family; breaches listed."),
    _fn("guardrail_monitor", "SOP misses and breaches now (overdue steps, promotional steps in stress regimes); posts fixes to the feed."),
    _fn("write_flight_plan", "Write the complete campaign requirement document (Flight Plan) for a programme: objective ↔ north star, cohort & sizing, journey, copy, KPI/experiment, limits & peace check, month timeline, month-on-month, risks/compliance, checks, what's possible now vs needs data/API. Use for every programme you propose and for the monthly plan.", {"spec": OBJ}, ["spec"]),
    _fn("flight_plans", "List flight plans for a month with coverage summary, or fetch one (Markdown).", {"month": STR, "plan_id": {"type": "integer"}}),
    _fn("segment_study", "Cohort families decoded from segment names (HVT_Sep26 → HVT / 2026-09): versions, reach, attached campaigns + performance, month-over-month deltas, flags (new, orphan, worse, shrank, undefined codes) and suggested studies. Use for any cohort or 'new upload' question.", {"refresh": {"type": "boolean"}, "days": {"type": "integer"}}),
    _fn("define_nomenclature", "Teach a name code: code='HVT', meaning='value:High value trader' (facet:label; facets: value|cohort|trader|product|programme|propensity|risk|message|window). Immediate.", {"code": STR, "meaning": STR}, ["code", "meaning"]),
    _fn("list_sops", "List campaign SOPs (standard operating procedures) by type with sequence length, cohort family, duration, frequency, holdout, KPI and framework status.", {"campaign_type": STR}),
    _fn("sop_detail", "Full SOP: audience, steps (day/channel/purpose/copy brief), kill criteria, checks, compliance.", {"sop_id": STR}, ["sop_id"]),
    _fn("define_sop", "Create or update an SOP from a full spec; validated against the framework (one KPI, holdout, caps, DND windows, exclusions for derivatives, disclaimers, kill criteria).", {"spec": OBJ}, ["spec"]),
    _fn("run_sop", "Run an SOP on a cohort: resolve segment (name or family → latest version), pre-flight, then one approval-gated proposal per step. Write variants_by_step per crypto-copywriting; dry_run first to see the plan.", {"sop_id": STR, "segment_name": STR, "start_date": STR, "variants_by_step": OBJ, "dry_run": {"type": "boolean"}, "ice": OBJ}, ["sop_id"]),
    _fn("competitor_intel", "Internal competitive picture vs tracked Indian/global venues: volume table, pair battles (our share), surges elsewhere, listing gaps, our edges, funding edges, ranked actions with owner + SOP. Internal only.", {"force": {"type": "boolean"}}),
    _fn("competitor_benchmarks", "Category leaderboards (spot / perps / options / commodities_tokenised) across Indian and global venues with our gap multiple and pair-level targets to match. Internal.", {"category": {"type": "string", "enum": ["spot", "perps", "options", "commodities_tokenised"]}, "force": {"type": "boolean"}}),
    _fn("competitor_campaigns", "Competitor campaigns detected in the last N hours from announcements, blogs, news and App Store notes — type, impact, counter SOP; App Store ranks. Internal.", {"hours": {"type": "integer"}, "venue": STR, "force": {"type": "boolean"}}),
    _fn("onchain_vs_cex", "Hyperliquid vs centralised venues (volume, OI, users, rank, share), on-chain perps OI landscape, per-coin OI share and funding edges. Internal.", {"force": {"type": "boolean"}}),
    _fn("signal_catalog", "Signal Bridge catalog: signals that can fire MoEngage business events, what is live now, approved rules, fires.", {}),
    _fn("propose_signal_rule", "Propose a standing rule so a signal fires its MoEngage business event automatically (approval once; caps, quiet hours, regime policy enforced).", {"signal_id": STR, "max_per_day": {"type": "integer"}, "rationale": STR}, ["signal_id"]),
    _fn("signal_fires", "Ledger of business events fired by the Signal Bridge.", {"limit": {"type": "integer"}}),
    _fn("compliance_sweep", "Lint every live campaign's copy against the India rules; findings with fixes.", {"force": {"type": "boolean"}}),
    _fn("refresh_learnings", "Recompile the our-learnings skill from our own readouts and reviews.", {}),
    _fn("sop_india_review", "India-fit review of SOPs by our compliance/copy/calendar skills: scores, flags, fixes, missing India SOPs.", {"sop_id": STR}),
    _fn("sop_india_fix", "Apply the deterministic India fixes to one SOP as a new version.", {"sop_id": STR}, ["sop_id"]),
    _fn("workspace_analysis", "Complete MoEngage programme analysis with the analyst narrative and ICE-ranked actions. Start here for programme-level questions.", {"force": {"type": "boolean"}}),
    _fn("qa_report", "Fact-check report over the boards (freshness, cross-source agreement, bounds, references, copy claims, sources, completeness) with fixes; safe improvements auto-applied.", {"force": {"type": "boolean"}}),
    _fn("verify_claims", "Verify numeric claims against live data before asserting them.", {"claims": {"type": "array", "items": OBJ}}, ["claims"]),
    _fn("structural_audit", "Structural misses in the CRM / funnel journey vs the CLM playbooks, ICE-ranked with tagline, segment, SOP, KPI, benchmark. These are P0; propose them first (run_sop or propose_campaign with ice + tagline).", {}),
    _fn("money_flow", "Money flow + trader behaviour reads + prioritised recommendations (segment, SOP, KPI, avoid). Start here for 'what should we do today'.", {}),
    _fn("market_flash", "Breaking-now market flash with product lenses, biggest news, top OI assets and intel per product.", {"hours": {"type": "integer"}}),
    _fn("campaign_from_alert", "Queue an approval-gated campaign from a market alert via the product's SOP with compliant placeholder copy; dry_run first, then revise_proposal to improve copy.", {"product": STR, "headline": STR, "detail": STR, "sop_id": STR, "segment_name": STR, "dry_run": {"type": "boolean"}}, ["product", "headline"]),
    _fn("competitor_dossier", "Marketing dossier for one rival: position by category, 7-day changes, app presence, campaigns and playbook, counters, how to beat them. Internal.", {"venue": STR}, ["venue"]),
    _fn("pair_battle", "One pair across all tracked venues: volume share, OI, funding, change; whether we list it.", {"symbol": STR}, ["symbol"]),
    _fn("web3_trending", "Trending on-chain tokens on our Web3 chains (quality-gated, unverified, paid boosts separate). Data for analysis; copy never picks tokens or names the data venue.", {"chain": STR, "force": {"type": "boolean"}}),
    _fn("product_cohorts", "Families grouped by product affinity with reach/performance and the product treatment matrix (pillars, cadence, never-list, cross-sell on intent, SOPs, Tier-0 lens)."),
    _fn("announcement_lenses", "Tier-0 plan for a major move / geopolitical / regulatory event: one fact, one lens per product cohort, suppressions, channels, regime rule.", {"event": STR}, ["event"]),
    _fn("request_data", "Ask the team for data you lack (segment upload, event, attribute, export, API access, dashboard capture, content) with why and what it unblocks.", {"kind": {"type": "string", "enum": ["segment", "event", "attribute", "export", "api_access", "dashboard_capture", "content", "other"]}, "title": STR, "why": STR, "spec": STR, "unblocks": {"type": "array", "items": STR}, "priority": {"type": "integer"}}, ["kind", "title", "why"]),
    _fn("data_requests", "Open / fulfilled data requests.", {"status": STR}),
    _fn("channel_matrix", "What can be done for each user state on each channel (push, email, WhatsApp, SMS, in-app bottom sheets, cards): purposes, formats, caps, compliance, SOPs."),
    _fn("sop_runs", "Recent SOP runs with proposal statuses and mid-flight flags.", {"limit": {"type": "integer"}}),
    _fn("moengage_api_reference", "Search the complete local catalog of documented MoEngage APIs (131 operations across data, segments, campaigns v1/v5, stats, flows, templates, content blocks, catalog, coupons, inform, analytics, subscriptions, GDPR…). query → matches; method+path → parameters, body schema, auth key, rate limit, doc URL. No network.",
        {"query": STR, "method": STR, "path": STR}),
    _fn("moengage_api_read", "Call a documented READ-SAFE MoEngage API directly (any GET, or POST search/meta/stats endpoints), e.g. GET /v5/flows/{flow_id}, POST /v5/campaigns/search, GET /v5/analytics/dashboards. Writes are refused — propose them. Live mode only.",
        {"method": STR, "path": STR, "path_vars": OBJ, "params": OBJ, "body": OBJ}, ["method", "path"]),
    _fn("skill", "Load a shared skill into context before specialised work: 'moengage' (product + when-to-use), 'moengage-api' (auth, endpoints, key scopes, limits, how this engine calls them), 'moengage-engine' (this codebase: views, CLI, tools, how to change it), 'clm-operator' (goal discipline, brief format, compliance).",
        {"name": STR}, ["name"]),
    _fn("remember_guidance", "Save a standing instruction from the operator so it applies to every future conversation (brand voice, exclusions, channel rules, cadence, process). Use whenever the operator says 'from now on', 'always', 'never', 'remember'. scope: general|copy|audience|channel|measurement|market|process|ui.",
        {"text": STR, "scope": STR}, ["text"]),
    _fn("set_engine_setting", "Change an engine knob right now (allowlisted, non-secret): autopilot_enabled, autopilot_max_actions, schedule_enabled, schedule_time, refresh_interval_hours, analysis_batch, market_universe_mode, market_top_n, taxonomy_codes (merge), llm_temperature, llm_max_tokens, llm_model_bulk, mock_scenario, devagent_enabled. Returns before/after.",
        {"key": STR, "value": {}}, ["key", "value"]),
    _fn("propose_code_change", "Ask for a change to the engine itself — a new view/column/chart in the console, a CLI command, a new tool, a detection rule, a report. Give a precise, testable request. It is implemented on an isolated branch (Claude Code headless, or the model), tests run, and the operator approves the diff; the server then restarts with the change. Use when the operator asks for something the current UI/tools cannot do.",
        {"title": STR, "request": STR, "scope": {"type": "string", "enum": ["frontend", "backend", "cli", "docs", "tests", "any"]}, "rationale": STR, "context": STR, "draft_now": {"type": "boolean"}}, ["title", "request"]),
]

TOOLS: Dict[str, Callable[..., Dict[str, Any]]] = {
    "get_status": _safe(get_status), "list_campaigns": _safe(list_campaigns), "get_campaign_stats": _safe(get_campaign_stats),
    "list_segments": _safe(list_segments), "list_flows": _safe(list_flows), "get_analytics": _safe(get_analytics),
    "estimate_segment": _safe(estimate_segment), "rule_based_audit": _safe(rule_based_audit), "anomaly_report": _safe(anomaly_report),
    "campaign_history": _safe(campaign_history), "campaign_diagnosis": _safe(campaign_diagnosis), "market_snapshot": _safe(market_snapshot), "market_news": _safe(market_news),
    "market_campaign_hooks": _safe(market_campaign_hooks), "moengage_guidance": _safe(moengage_guidance), "integration_status": _safe(integration_status),
    "list_proposals": _safe(list_proposals), "proposal_detail": _safe(proposal_detail), "revise_proposal": _safe(revise_proposal), "comment_proposal": _safe(comment_proposal), "propose_segment": _safe(propose_segment), "propose_campaign": _safe(propose_campaign),
    "record_ideas": _safe(record_ideas), "growth_hacks": _safe(growth_hacks), "campaign_content": _safe(campaign_content), "segment_detail": _safe(segment_detail),
    "analytics_query": _safe(analytics_query), "experiment_readouts": _safe(experiment_readouts), "campaign_taxonomy": _safe(campaign_taxonomy), "campaign_deep_dive": _safe(campaign_deep_dive), "clm_program_audit": _safe(clm_program_audit), "experiment_plan": _safe(experiment_plan), "campaign_brief_check": _safe(campaign_brief_check),
    "propose_flow": _safe(propose_flow), "propose_pause_campaign": _safe(propose_pause_campaign),
    "moengage_api_reference": _safe(moengage_api_reference), "moengage_api_read": _safe(moengage_api_read), "skill": _safe(skill),
    "remember_guidance": _safe(remember_guidance), "set_engine_setting": _safe(set_engine_setting), "propose_code_change": _safe(propose_code_change),
    "model_routes": _safe(model_routes), "token_usage": _safe(token_usage), "self_diagnose": _safe(self_diagnose), "rollback_last_change": _safe(rollback_last_change),
    "north_star": _safe(north_star), "set_north_star": _safe(set_north_star), "comms_limits": _safe(comms_limits), "set_comms_limits": _safe(set_comms_limits), "peace_index": _safe(peace_index),
    "guardrail_monitor": _safe(guardrail_monitor), "write_flight_plan": _safe(write_flight_plan), "flight_plans": _safe(flight_plans),
    "segment_study": _safe(segment_study), "define_nomenclature": _safe(define_nomenclature), "list_sops": _safe(list_sops), "sop_detail": _safe(sop_detail), "define_sop": _safe(define_sop), "run_sop": _safe(run_sop), "sop_runs": _safe(sop_runs), "channel_matrix": _safe(channel_matrix),
    "competitor_intel": _safe(competitor_intel), "competitor_benchmarks": _safe(competitor_benchmarks), "competitor_campaigns": _safe(competitor_campaigns), "onchain_vs_cex": _safe(onchain_vs_cex), "signal_catalog": _safe(signal_catalog), "propose_signal_rule": _safe(propose_signal_rule), "signal_fires": _safe(signal_fires), "compliance_sweep": _safe(compliance_sweep), "refresh_learnings": _safe(refresh_learnings), "sop_india_review": _safe(sop_india_review), "sop_india_fix": _safe(sop_india_fix), "workspace_analysis": _safe(workspace_analysis), "qa_report": _safe(qa_report), "verify_claims": _safe(verify_claims), "structural_audit": _safe(structural_audit), "money_flow": _safe(money_flow), "market_flash": _safe(market_flash), "campaign_from_alert": _safe(campaign_from_alert), "competitor_dossier": _safe(competitor_dossier), "pair_battle": _safe(pair_battle), "web3_trending": _safe(web3_trending), "product_cohorts": _safe(product_cohorts), "announcement_lenses": _safe(announcement_lenses), "request_data": _safe(request_data), "data_requests": _safe(data_requests),
}
