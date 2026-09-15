"""
What data we are missing to run and measure campaigns properly — derived, not
guessed, and turned into requests the team can action.

The requirement list is not a wish list: every item is demanded by something we
already run or want to run — an SOP's audience or step condition, a structural
play, an approved signal rule, a measurement we cannot do today. Each gap says
what to instrument, why, and exactly what it unblocks, so engineering and data
see the cost of not having it.

detect()  the requirement list with status (present / missing / unknown)
sync()    files the missing ones as data requests (deduplicated)
"""
from __future__ import annotations
import re
from typing import Any, Dict, List, Optional

from .database import get_setting
from .security import audit

# canonical instrumentation from the trading-event-taxonomy skill, with what each unlocks here
EVENTS: List[Dict[str, Any]] = [
    {"name": "deposit_initiated", "spec": "deposit_initiated{amount, method, bank}", "why": "the start of the funding funnel; without it we cannot see abandonment at all", "unblocks": ["sop_verified_to_funded", "sop_deposit_failure_recovery"], "severity": "high"},
    {"name": "deposit_failed", "spec": "deposit_failed{amount, method, bank, reason, utr}", "why": "recovery within minutes converts 25–40% of failures; the next day it is under 10%", "unblocks": ["sop_deposit_failure_recovery", "sop_whatsapp_utility_journey"], "severity": "high"},
    {"name": "deposit_completed", "spec": "deposit_completed{amount, method, first_deposit}", "why": "stops the funding journey for users who funded, and starts the activation one", "unblocks": ["sop_verified_to_funded", "sop_funded_to_first_trade"], "severity": "high"},
    {"name": "kyc_failed", "spec": "kyc_failed{step, reason}", "why": "document-specific help at the failure point beats reminders 2–3×", "unblocks": ["sop_kyc_completion", "sop_rekyc"], "severity": "high"},
    {"name": "trade_executed", "spec": "trade_executed{product, symbol, side, notional, leverage, fee, tds}", "why": "the spine of activation, habit and cross-sell segmentation", "unblocks": ["sop_funded_to_first_trade", "sop_second_trade_72h", "sop_first_week_habit"], "severity": "high"},
    {"name": "liquidation", "spec": "liquidation{symbol, side, size_usd, loss_usd, margin_mode}", "why": "the 14-day suppression list and the recovery sequence both depend on it", "unblocks": ["sop_liquidation_recovery", "sop_first_perp_hygiene", "signal: oi_flush"], "severity": "high"},
    {"name": "position_opened", "spec": "position_opened{symbol, side, leverage, margin_mode}", "why": "funding-cost and margin-buffer nudges must go to people who actually hold the position", "unblocks": ["sop_funding_crowding_nudge", "sop_oi_crowding_note"], "severity": "medium"},
    {"name": "watchlist_added", "spec": "watchlist_added{symbol}", "why": "own-asset alerts are the highest-CTR message class we have", "unblocks": ["sop_market_move_alert", "sop_asset_spotlight", "signal: asset_move"], "severity": "high"},
    {"name": "price_alert_set", "spec": "price_alert_set{symbol, direction, threshold}", "why": "alert adoption is the habit KPI for the activated → habitual stage", "unblocks": ["sop_push_alert_digest", "sop_first_week_habit"], "severity": "medium"},
    {"name": "product_view", "spec": "product_view{product, screen}", "why": "graduation to derivatives must follow intent, never a blanket push", "unblocks": ["sop_perp_intent_education", "sop_options_education"], "severity": "medium"},
    {"name": "sip_created", "spec": "sip_created{amount, frequency, symbol} + sip_paused / sip_failed{reason}", "why": "SIP continuity is our most valuable retention cohort and we cannot see churn in it", "unblocks": ["sop_sip_nurture", "sop_cross_sell_recurring_buy"], "severity": "medium"},
    {"name": "withdrawal_completed", "spec": "withdrawal_completed{amount, full_balance}", "why": "a full withdrawal is the strongest churn signal we have", "unblocks": ["sop_full_withdrawal_service"], "severity": "medium"},
    {"name": "notification_disabled", "spec": "notification_disabled{channel}", "why": "the guardrail on every push campaign; without it fatigue is invisible", "unblocks": ["comms limits", "sop_channel_recovery_push_off"], "severity": "high"},
]
ATTRIBUTES: List[Dict[str, Any]] = [
    {"name": "trader_state", "spec": "trader_state ∈ {new, funded, activated, habitual, core, slipping, dormant, loss_dormant, liquidated}", "why": "every lifecycle SOP targets a state; today we infer it from segment names uploaded monthly", "unblocks": ["all lifecycle SOPs", "peace index"], "severity": "high"},
    {"name": "value_tier", "spec": "value_tier ∈ {HVT, MVT, LVT} refreshed monthly", "why": "cadence and creative differ by tier; the best users must hear from us least", "unblocks": ["sop_hvt_retention", "comms limits by stage"], "severity": "high"},
    {"name": "fee_tier_distance_pct", "spec": "fee_tier_distance_pct + current_tier + next_tier", "why": "the distance-to-tier nudge converts 10–20% of near-threshold users", "unblocks": ["sop_fee_tier_nudge"], "severity": "medium"},
    {"name": "push_enabled", "spec": "push_enabled boolean + last_push_delivered_at", "why": "15–25% of the base has push off; for high-value users that is silent churn", "unblocks": ["sop_channel_recovery_push_off"], "severity": "medium"},
    {"name": "app_language", "spec": "app_language (en, hi, ta, te, bn…)", "why": "Hinglish and regional variants are required for mass cohorts in India", "unblocks": ["every mass push and WhatsApp step"], "severity": "medium"},
    {"name": "held_symbols", "spec": "held_symbols[] and watchlist_symbols[]", "why": "market messages must go to holders and watchers of that asset, never broadcast", "unblocks": ["sop_market_move_alert", "sop_asset_spotlight", "Signal Bridge asset events"], "severity": "high"},
    {"name": "liquidated_at", "spec": "liquidated_at timestamp + realised_loss_pct", "why": "the 14-day promo suppression and loss-dormant exclusion depend on it", "unblocks": ["every market and promo SOP's exclusions"], "severity": "high"},
    {"name": "products_used", "spec": "products_used[] (spot, sip, perps, options, earn, web3)", "why": "cross-sell only on intent, and product lenses for Tier-0 announcements", "unblocks": ["product cohort matrix", "cross-sell SOPs"], "severity": "medium"},
    {"name": "kyc_status", "spec": "kyc_status + kyc_failed_reason", "why": "the onboarding ladder cannot branch without it", "unblocks": ["sop_kyc_completion", "sop_verified_to_funded"], "severity": "high"},
]
CAPABILITIES: List[Dict[str, Any]] = [
    {"name": "global control group", "kind": "config", "spec": "MoEngage global control group at 5% (Settings → Global control group), plus per-campaign holdouts", "why": "without a control we report pre/post movement, not incremental lift; a third of 'wins' are noise or market", "unblocks": ["every experiment readout"], "severity": "high"},
    {"name": "campaign control-group stats via API", "kind": "api_access", "spec": "campaign stats that expose control-group figures (campaign meta v5 / global control group endpoints)", "why": "the readouts we publish today must say 'directional' because the API gives us no control arm", "unblocks": ["experiment_readouts", "Analysis module verdicts"], "severity": "high"},
    {"name": "business events created in MoEngage", "kind": "config", "spec": "one business event per approved Signal Bridge rule, with the listed attributes, plus a Business-Event-Triggered campaign on each", "why": "the engine fires the event; MoEngage must have it defined or the fire lands nowhere", "unblocks": ["Signal Bridge (real-time delivery)"], "severity": "high"},
    {"name": "WhatsApp utility templates approved", "kind": "config", "spec": "approved utility templates for KYC status, deposit failure, first-trade receipt", "why": "WhatsApp utility is read by 70%+ in India; without approved templates the step cannot run", "unblocks": ["sop_whatsapp_utility_journey", "sop_deposit_failure_recovery"], "severity": "medium"},
    {"name": "DLT-registered SMS templates", "kind": "config", "spec": "DLT template ids for any SMS step, 10:00–21:00 only", "why": "SMS without a DLT template is undeliverable and non-compliant in India", "unblocks": ["any SMS step"], "severity": "low"},
    {"name": "monthly cohort upload with user ids", "kind": "export", "spec": "CSV per month: user_id, trader_state, value_tier, products_used", "why": "we can decode segment names but cannot see migration between months without ids", "unblocks": ["cohort migration study", "trader_state attribute"], "severity": "medium"},
    {"name": "Content API endpoint for live numbers", "kind": "api_access", "spec": "an endpoint MoEngage can call at send time for price, fees and own-numbers content", "why": "market-linked copy goes stale between build and send", "unblocks": ["sop_weekly_digest", "market SOPs"], "severity": "low"},
]


def _known_families() -> Dict[str, Any]:
    try:
        from .segments import registry
        rows = registry(limit=500)
        return {str(r.get("family")): r for r in rows}
    except Exception:
        return {}


def _mock() -> bool:
    try:
        from .moengage import MoEngageClient
        return MoEngageClient().mode == "mock"
    except Exception:
        return True


def _cohort_gaps() -> List[Dict[str, Any]]:
    """Segment families our SOPs target that do not exist in the registry."""
    from .sops import list_sops, get_sop
    fams = _known_families()
    need: Dict[str, List[str]] = {}
    for row in list_sops():
        sop = get_sop(row["id"])
        fam = str(((sop or {}).get("audience") or {}).get("segment_family") or "")
        if fam and fam != "*" and fam not in fams:
            need.setdefault(fam, []).append(row["id"])
    out = []
    for fam, sids in sorted(need.items(), key=lambda kv: -len(kv[1])):
        out.append({"id": f"segment::{fam}", "kind": "segment", "name": fam, "spec": f"a MoEngage segment (or monthly upload) named {fam}_<Mon><YY> with the criteria in the SOP's audience block",
                    "why": f"{len(sids)} SOP(s) target this cohort and cannot run without it", "unblocks": sids[:6], "severity": "high" if len(sids) > 1 else "medium",
                    "status": "missing", "blocks": len(sids)})
    return out


def _event_conditions() -> Dict[str, List[str]]:
    """Events named in SOP step conditions — these must exist for the sequence to branch."""
    from .sops import list_sops, get_sop
    found: Dict[str, List[str]] = {}
    for row in list_sops():
        sop = get_sop(row["id"]) or {}
        for st in sop.get("steps") or []:
            cond = str(st.get("condition") or "")
            for m in re.findall(r"\b([a-z]+_[a-z_]+)\b", cond):
                if len(m) > 6:
                    found.setdefault(m, []).append(row["id"])
    return found


def detect() -> Dict[str, Any]:
    """The requirement list: what we need, why, what it unblocks, and whether we can see it today."""
    mock = _mock()
    status_default = "unknown" if mock else "missing"
    note = ("mock workspace: presence cannot be verified, so everything required is listed as 'unknown — confirm on the live workspace'"
            if mock else "listed as missing where the engine has no evidence of the event/attribute; confirm against the live workspace before asking engineering")
    conds = _event_conditions()
    items: List[Dict[str, Any]] = []
    for e in EVENTS:
        used_by = sorted(set(e["unblocks"]) | set(conds.get(e["name"], [])))
        items.append({"id": f"event::{e['name']}", "kind": "event", "name": e["name"], "spec": e["spec"], "why": e["why"], "unblocks": used_by,
                      "severity": e["severity"], "status": status_default, "blocks": len(used_by)})
    for a in ATTRIBUTES:
        items.append({"id": f"attribute::{a['name']}", "kind": "attribute", "name": a["name"], "spec": a["spec"], "why": a["why"], "unblocks": a["unblocks"],
                      "severity": a["severity"], "status": status_default, "blocks": len(a["unblocks"])})
    items += _cohort_gaps()
    for c in CAPABILITIES:
        st = "missing"
        if c["name"] == "business events created in MoEngage":
            try:
                from . import signals
                st = "missing" if [r for r in signals.rules() if r.get("enabled")] else "not needed yet"
            except Exception:
                pass
        items.append({"id": f"capability::{c['name']}", "kind": c["kind"], "name": c["name"], "spec": c["spec"], "why": c["why"], "unblocks": c["unblocks"],
                      "severity": c["severity"], "status": st, "blocks": len(c["unblocks"])})
    # what has already been asked for
    asked = {}
    try:
        from . import datarequests
        for r in datarequests.list_requests(limit=200):
            asked[str(r.get("title", "")).lower()] = r
    except Exception:
        pass
    for it in items:
        name = str(it["name"]).lower()
        hit = asked.get(_title(it).lower()) or next((r for t, r in asked.items() if name in t or name.replace("_", " ") in t), None)
        it["requested"] = bool(hit)
        it["request_id"] = (hit or {}).get("id")
        it["request_status"] = (hit or {}).get("status")
    order = {"high": 0, "medium": 1, "low": 2}
    items.sort(key=lambda i: (order.get(i["severity"], 3), -i["blocks"]))
    open_items = [i for i in items if i["status"] != "not needed yet"]
    return {"items": items, "counts": {k: sum(1 for i in open_items if i["severity"] == k) for k in ("high", "medium", "low")},
            "by_kind": {k: sum(1 for i in open_items if i["kind"] == k) for k in sorted({i["kind"] for i in open_items})},
            "requested": sum(1 for i in items if i["requested"]), "outstanding": sum(1 for i in open_items if not i["requested"]),
            "mock": mock, "note": note}


def _title(item: Dict[str, Any]) -> str:
    kind = item["kind"]
    if kind == "event":
        return f"Event: {item['name']}"
    if kind == "attribute":
        return f"User attribute: {item['name']}"
    if kind == "segment":
        return f"Segment / monthly upload: {item['name']}"
    return f"Capability: {item['name']}"


def sync(min_severity: str = "medium", actor: str = "agent") -> Dict[str, Any]:
    """File every outstanding requirement at or above `min_severity` as a data request (deduplicated by title)."""
    from . import datarequests
    order = {"high": 0, "medium": 1, "low": 2}
    d = detect()
    filed, skipped = [], 0
    for it in d["items"]:
        if it["requested"] or it["status"] == "not needed yet" or order.get(it["severity"], 3) > order.get(min_severity, 1):
            skipped += 1
            continue
        kind = it["kind"] if it["kind"] in datarequests.KINDS else ("api_access" if it["kind"] == "config" else "other")
        r = datarequests.request(kind, _title(it), it["why"], spec=it["spec"],
                                 unblocks=[str(x) for x in it["unblocks"]][:8],
                                 priority=90 if it["severity"] == "high" else 60 if it["severity"] == "medium" else 40, requested_by=actor)
        if r.get("id") and not r.get("duplicate"):
            filed.append({"id": r["id"], "title": _title(it), "severity": it["severity"]})
    if filed:
        audit("data_gaps.sync", {"filed": len(filed), "min_severity": min_severity}, actor=actor)
    return {"filed": filed, "count": len(filed), "skipped": skipped, "outstanding_after": detect()["outstanding"]}
