"""
North star, communication limits, peace index and SOP monitor.

* north_star: the one sentence the agent optimises for (editable setting).
* comms_limits: hard per-user caps by channel (per day / per week), stage
  overrides (best users hear least) and regime multipliers (stress regimes
  lower every cap). Set by the team (UI / set_comms_limits) and enforced in
  the SOP framework, run pre-flight and brief checks.
* peace_index: per cohort family, planned + observed touches per user per
  week by channel versus the effective limit → too_much / in_band / too_little.
  Touches are estimated from campaign sends over reach (MoEngage does not
  expose per-user send logs through the public API); the estimate is labelled.
* sop_monitor: misses (a step's send date passed, proposal still pending),
  breaches (promotional step executed in a stress regime; planned touches over
  cap) → priority items in the growth feed.
"""
from __future__ import annotations
import re
import json
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from .database import get_setting, set_setting
from .security import audit

DEFAULT_NORTH_STAR = ("Incremental active trading weeks per user at the lowest message load: maximise weekly_active_weeks_4w lift versus holdout "
                      "while every cohort stays inside the communication limits and no user is contacted during a stress regime except for service.")

DEFAULT_LIMITS: Dict[str, Any] = {
    "per_user": {"push": {"per_day": 1, "per_week": 4}, "email": {"per_day": 1, "per_week": 3}, "in-app": {"per_day": 2, "per_week": 6},
                 "cards": {"per_day": 1, "per_week": 7}, "whatsapp": {"per_day": 1, "per_week": 2}, "sms": {"per_day": 1, "per_week": 1}},
    "total_per_week": 8,
    "market_linked_push_per_day": 1,
    "quiet_hours_ist": {"start": "22:00", "end": "08:00"},
    "stage_overrides": {"Core": {"push": {"per_week": 2}, "email": {"per_week": 2}, "total_per_week": 4},
                        "Liquidated": {"push": {"per_week": 1}, "total_per_week": 2},
                        "Loss-dormant": {"push": {"per_week": 0}, "email": {"per_week": 1}, "total_per_week": 1}},
    "regime_multiplier": {"capitulation": 0.0, "high_volatility_down": 0.5, "trending_down": 0.75, "chop": 1.0, "trending_up": 1.0, "high_volatility_up": 1.0, "unknown": 0.75},
    "service_exempt": True,
    "too_little_days": 14,
}
CHANNEL_ALIASES = {"inapp": "in-app", "in_app": "in-app", "in-app": "in-app", "push": "push", "email": "email", "whatsapp": "whatsapp", "sms": "sms", "cards": "cards", "card": "cards"}


def north_star() -> str:
    return get_setting("north_star", "") or DEFAULT_NORTH_STAR


def set_north_star(text: str, actor: str = "user") -> Dict[str, Any]:
    text = (text or "").strip()[:600]
    if len(text) < 20:
        return {"error": "north star must be a full sentence"}
    set_setting("north_star", text); audit("north_star.set", {"chars": len(text)}, actor=actor)
    return {"ok": True, "north_star": text}


def limits() -> Dict[str, Any]:
    try:
        cur = json.loads(get_setting("comms_limits", "") or "{}")
    except Exception:
        cur = {}
    out = json.loads(json.dumps(DEFAULT_LIMITS))
    _deep_update(out, cur)
    return out


def _deep_update(base: Dict[str, Any], patch: Dict[str, Any]) -> None:
    for k, v in (patch or {}).items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_update(base[k], v)
        else:
            base[k] = v


def set_limits(patch: Dict[str, Any], actor: str = "user") -> Dict[str, Any]:
    """Merge a validated patch into comms_limits. Caps are non-negative ints; multipliers 0–1."""
    if not isinstance(patch, dict):
        return {"error": "patch must be an object"}
    def _chk(node: Any, path: str = "") -> Optional[str]:
        if isinstance(node, dict):
            for k, v in node.items():
                e = _chk(v, f"{path}.{k}" if path else k)
                if e:
                    return e
        elif isinstance(node, bool):
            return None
        elif isinstance(node, (int, float)):
            if "multiplier" in path and not (0 <= node <= 1):
                return f"{path}: multiplier must be between 0 and 1"
            if "multiplier" not in path and (node < 0 or node > 50):
                return f"{path}: cap must be 0–50"
        elif isinstance(node, str):
            if "quiet_hours" not in path and "start" not in path and "end" not in path:
                return f"{path}: unexpected text value"
        return None
    err = _chk(patch)
    if err:
        return {"error": err}
    before = limits()
    try:
        cur = json.loads(get_setting("comms_limits", "") or "{}")
    except Exception:
        cur = {}
    _deep_update(cur, patch)
    set_setting("comms_limits", json.dumps(cur))
    after = limits()
    audit("comms_limits.set", {"patch": patch}, actor=actor)
    return {"ok": True, "before": before, "after": after, "effective": "immediately: SOP framework, run pre-flight and brief checks read the limits on every call"}


def effective_limits(regime: Optional[str] = None, stage: Optional[str] = None) -> Dict[str, Any]:
    L = limits()
    mult = float((L.get("regime_multiplier") or {}).get(regime or "unknown", 1.0)) if regime else 1.0
    per = json.loads(json.dumps(L["per_user"]))
    total = L.get("total_per_week", 8)
    ov = (L.get("stage_overrides") or {}).get(stage or "", {})
    for ch, caps in ov.items():
        if ch == "total_per_week":
            total = caps
        elif isinstance(caps, dict) and ch in per:
            per[ch].update(caps)
    for ch in per:
        for k in per[ch]:
            per[ch][k] = int(round(per[ch][k] * mult))
    return {"per_user": per, "total_per_week": int(round(total * mult)), "regime": regime, "multiplier": mult, "stage": stage, "service_exempt": L.get("service_exempt", True)}


def _stage_for_family(family: str) -> Optional[str]:
    """Lifecycle stage from the family tokens (nomenclature). Order matters: risk states first, then funnel position, then value tiers."""
    f = (family or "").upper()
    toks = set(re.split(r"[^A-Z0-9]+", f))
    if "LIQUIDAT" in f or "LIQUIDATED" in toks:
        return "Liquidated"
    if "LOSS" in f:
        return "Loss-dormant"
    if any(t in toks for t in ("DORMANT", "RES", "RESURRECTION", "INACTIVE", "LAPSED", "CHURN", "CHURNED", "WINBACK")):
        return "Dormant"
    if any(t in toks for t in ("SLIPPING", "SLIP", "DECLINING", "ATRISK", "AT_RISK")):
        return "Slipping"
    if any(t in toks for t in ("KYC", "REKYC", "PENDING", "UNVERIFIED", "SIGNUP", "NODEP", "NODEPOSIT", "NEW", "FTD", "NOTRADE", "FTT", "ONBOARD", "ONBOARDING")):
        return "New"
    if any(t in f for t in ("HVT", "VIP", "WHALE", "TOP", "CORE", "HVS")):
        return "Core"
    if any(t in toks for t in ("HFT", "FUTURES", "PERP", "PERPS", "LEV", "LEVERAGE", "MARGIN", "OPTIONS", "OPT")):
        return "Habitual perp"
    if any(t in f for t in ("MVT", "LVT", "LVS", "LIS", "BC", "LIF", "SIP", "SPOT", "TG", "XQ", "ACTIVE", "HABIT", "RET", "RETENTION", "CS")):
        return "Habitual"
    return None


def planned_touches(family: str, days: int = 7) -> Dict[str, int]:
    """Pending/approved campaign proposals scheduled in the next `days` that target this family, by channel."""
    from . import approvals
    from .segments import decode
    out: Dict[str, int] = {}
    horizon = (date.today() + timedelta(days=days)).isoformat()
    for p in approvals.list_proposals(limit=400):
        if p["kind"] != "create_campaign" or p["status"] not in ("pending", "approved", "executed"):
            continue
        pl = p.get("payload") or {}
        tgt = str(pl.get("target_segment") or "")
        if not tgt or decode(tgt)["family"] != family:
            continue
        sched = (pl.get("schedule") or {}).get("date")
        if p["status"] == "executed" or not sched or sched <= horizon:
            ch = CHANNEL_ALIASES.get(str(pl.get("channel") or "push").lower(), str(pl.get("channel") or "push").lower())
            out[ch] = out.get(ch, 0) + 1
    return out


def observed_touches(campaigns: List[Dict[str, Any]], family: str, reach: Optional[int], days: int = 7) -> Dict[str, float]:
    """Sends over the last `days` from campaigns targeting the family, divided by reach → touches per user (estimate)."""
    from .segments import _campaign_matches
    from .anomaly.store import normalise_campaign
    out: Dict[str, float] = {}
    cutoff = (datetime.utcnow() - timedelta(days=days)).date().isoformat()
    for c in campaigns:
        if not _campaign_matches(c, "", family):
            continue
        st = str(c.get("status") or "").lower()
        last = str(c.get("last_sent_at") or c.get("updated_at") or c.get("created_at") or "")[:10]
        if st in ("draft", "stopped", "paused", "archived") and (not last or last < cutoff):
            continue
        n = normalise_campaign(c)
        sent = n.get("sent_count") or 0
        if not sent:
            continue
        ch = CHANNEL_ALIASES.get(str(c.get("channel") or "push").lower(), str(c.get("channel") or "push").lower())
        per_user = (sent / reach) if reach else 1.0
        out[ch] = out.get(ch, 0.0) + min(per_user, 7.0)
    return out


def peace_index(campaigns: List[Dict[str, Any]], regime: Optional[str] = None) -> Dict[str, Any]:
    from .segments import study
    st = study(campaigns)
    rows = []
    L = limits()
    for f in st["families"]:
        stage = _stage_for_family(f["family"])
        eff = effective_limits(regime, stage)
        planned = planned_touches(f["family"])
        observed = observed_touches(campaigns, f["family"], f.get("reach"))
        chans = set(planned) | set(observed)
        total = sum(planned.values()) + sum(observed.values())
        breaches = []
        for ch in chans:
            cap = (eff["per_user"].get(ch) or {}).get("per_week")
            if cap is not None and (planned.get(ch, 0) + observed.get(ch, 0)) > cap:
                breaches.append(f"{ch}: {planned.get(ch, 0) + observed.get(ch, 0):.1f}/user/week vs cap {cap}")
        if total > eff["total_per_week"]:
            breaches.append(f"total {total:.1f}/user/week vs cap {eff['total_per_week']}")
        age = f.get("age_days")
        too_little = (total == 0 and stage != "Core" and stage != "Loss-dormant" and "no_campaign_attached" in f["flags"] and (age is None or age >= L.get("too_little_days", 14)))
        status = "too_much" if breaches else ("too_little" if too_little else "in_band")
        rows.append({"family": f["family"], "meaning": f.get("meaning"), "stage": stage, "reach": f.get("reach"), "planned_7d": planned, "observed_7d_per_user": {k: round(v, 2) for k, v in observed.items()},
                     "touches_per_user_week": round(total, 2), "limit_total": eff["total_per_week"], "status": status, "breaches": breaches})
    counts = {k: sum(1 for r in rows if r["status"] == k) for k in ("too_much", "in_band", "too_little")}
    return {"north_star": north_star(), "regime": regime, "multiplier": effective_limits(regime)["multiplier"], "families": rows, "counts": counts,
            "method": "touches = planned proposals (next 7d) + observed sends/reach (last 7d) per family; per-user send logs are not exposed by the MoEngage public API, so this is a cohort-level estimate"}


def sop_monitor(regime: Optional[str] = None) -> Dict[str, Any]:
    """Misses and breaches across SOP runs; posts priority items to the growth feed."""
    from . import approvals, growth
    from .sops import list_runs, get_sop
    props = {p["id"]: p for p in approvals.list_proposals(limit=400)}
    today = date.today().isoformat()
    findings = []
    stress = regime in ("capitulation", "high_volatility_down")
    for r in list_runs(60):
        sop = get_sop(r["sop_id"]) or {}
        for pid in r["proposal_ids"]:
            p = props.get(pid)
            if not p:
                continue
            sched = ((p.get("payload") or {}).get("schedule") or {}).get("date")
            if p["status"] == "pending" and sched and sched < today:
                findings.append({"run": r["id"], "sop": r["sop_id"], "proposal": pid, "type": "miss", "detail": f"step scheduled {sched} still awaiting approval"})
            if p["status"] == "executed" and stress and sop.get("campaign_type") in ("market", "competition", "winback", "activation"):
                ex = str(p.get("executed_at") or "")[:10]
                if ex == today:
                    findings.append({"run": r["id"], "sop": r["sop_id"], "proposal": pid, "type": "breach", "detail": f"promotional step executed during {regime}"})
    if findings:
        growth.upsert_ideas([{"kind": "fix", "title": f"SOP monitor: {sum(1 for f in findings if f['type']=='miss')} miss(es), {sum(1 for f in findings if f['type']=='breach')} breach(es)",
                              "why": "; ".join(f"{f['type']} run #{f['run']} {f['sop']} #{f['proposal']}: {f['detail']}" for f in findings[:6])[:700],
                              "how": "Approve or reject overdue steps; pause promotional steps in stress regimes (propose_pause_campaign); re-plan the run.", "priority": 95, "kpi": "sop_adherence", "effort": "low", "expected_impact": "keeps SOP adherence at 100%", "data": {"findings": findings[:20]}}], "rules")
    return {"findings": findings, "regime": regime, "checked_runs": len(list_runs(60))}
