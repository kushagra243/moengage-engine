"""
Ownership, hand-offs and escalation for every SOP — the layer that lets the
SOP assistant answer "who owns the next step", "who hands off what to whom and
when", and "who do I contact at 11 PM when I'm blocked".

Our SOPs describe *what* runs; this module derives *who* and *when*:
  roles        the standing cast (CRM Ops, Growth Lead, Compliance, …), each
               with a real name/handle once the team fills `sop_owners_json`.
  segments     the ten process segments every campaign SOP passes through,
               from trigger to learning, with owner, inputs, outputs, the
               engine surface that does it, and what to do when blocked.
  handoffs     segment i's output → segment i+1's input, with the owner pair
               and the timing, so inter-team dependencies are explicit.
  escalation   a ladder per blocker: who first, who after N hours, who owns
               the decision if it stays stuck.
Everything is deterministic and overridable: `sop_owners_json` (Engine →
Settings) can rename roles, attach people, and override owners per SOP or per
campaign type without touching code.
"""
from __future__ import annotations
import json
from typing import Any, Dict, List, Optional

from .database import get_setting

ROLES: Dict[str, Dict[str, str]] = {
    "crm_ops": {"role": "CRM Ops", "does": "builds and schedules the campaign in MoEngage, watches delivery"},
    "growth_lead": {"role": "Growth Lead", "does": "owns the KPI, approves the brief and the send"},
    "compliance": {"role": "Compliance", "does": "clears copy against ASCI/VDA, derivatives posture and channel rules"},
    "data": {"role": "Data / Analytics", "does": "builds the cohort, sets the holdout, reads the experiment out"},
    "product": {"role": "Product", "does": "owns app surfaces, deep links, listings and the events we need"},
    "support": {"role": "Support", "does": "handles user replies, complaints and incident comms"},
    "design": {"role": "Design", "does": "creative for rich push, cards, email and in-app"},
    "market_ops": {"role": "Market Ops", "does": "listings, delistings, market data and venue-side facts"},
    "eng": {"role": "Engineering", "does": "instrumentation, MoEngage integration, business events"},
}
# primary owner per campaign type; reviewers are added from channels and compliance rules
TYPE_OWNER = {
    "onboarding": "crm_ops", "activation": "crm_ops", "retention": "crm_ops", "winback": "crm_ops",
    "risk": "growth_lead", "compliance": "compliance", "market": "growth_lead", "newsletter": "crm_ops",
    "competition": "growth_lead", "cohort_upload": "data", "education": "crm_ops",
}
CHANNEL_OWNER = {
    "push": ["crm_ops"], "in-app": ["crm_ops", "product"], "inapp": ["crm_ops", "product"], "cards": ["crm_ops", "design"],
    "email": ["crm_ops", "design"], "whatsapp": ["crm_ops", "compliance"], "sms": ["crm_ops", "compliance"],
}
SEGMENTS: List[Dict[str, Any]] = [
    dict(n=1, name="Trigger & eligibility", what="the event, schedule or market signal that starts the run, and who qualifies", owner="growth_lead", inputs="signal or calendar slot", outputs="decision to run + the SOP id",
         surface="Signal Bridge / Ideas board / run_sop", blocked="no clear trigger or the regime blocks the angle", escalate_to="growth_lead"),
    dict(n=2, name="Cohort resolution", what="resolve the segment family to this month's version and check reach", owner="data", inputs="segment family from the SOP", outputs="segment name + reach",
         surface="segment_study / propose_segment", blocked="segment missing or below min reach", escalate_to="data"),
    dict(n=3, name="Pre-flight checks", what="framework, exclusions, compliance, comms limits and regime policy", owner="crm_ops", inputs="SOP + segment", outputs="pre-flight verdict",
         surface="run_sop(dry_run=true) / preflight", blocked="a check fails", escalate_to="compliance"),
    dict(n=4, name="Copy & compliance", what="two variants per step, disclaimer where the channel carries it, Hinglish for mass cohorts", owner="crm_ops", inputs="copy brief per step", outputs="approved variants",
         surface="campaign_brief_check / copy agent", blocked="banned wording, missing disclaimer, venue named", escalate_to="compliance"),
    dict(n=5, name="Approval", what="a human approves each queued step before anything reaches MoEngage", owner="growth_lead", inputs="proposals with preview", outputs="approved proposal",
         surface="Ideas board → Proposed", blocked="council verdict is revise/reject, or nobody has approved", escalate_to="growth_lead"),
    dict(n=6, name="Scheduling & send", what="the sequence goes live on the days and IST times in the SOP", owner="crm_ops", inputs="approved proposal", outputs="live campaign ids",
         surface="executors → MoEngage", blocked="API error, template not approved, quiet hours", escalate_to="eng"),
    dict(n=7, name="Mid-flight monitoring", what="kill criteria daily, peace index, delivery and complaint rates", owner="crm_ops", inputs="daily stats", outputs="continue / adjust / kill",
         surface="midflight_checks / Anomalies", blocked="a kill criterion trips", escalate_to="growth_lead"),
    dict(n=8, name="Kill / adjust", what="pause or re-point the run, suppress the affected cohort", owner="growth_lead", inputs="mid-flight finding", outputs="pause or revised step",
         surface="propose_pause_campaign / revise_proposal", blocked="unclear whether the cause is the campaign or the market", escalate_to="data"),
    dict(n=9, name="Readout", what="KPI vs baseline with the holdout, stated honestly, inside the measurement window", owner="data", inputs="campaign stats + holdout", outputs="verdict",
         surface="experiment_readouts / Analysis", blocked="not enough sample or no control figures", escalate_to="data"),
    dict(n=10, name="Learning & next version", what="the lesson lands in our-learnings and the SOP gets a new version", owner="growth_lead", inputs="readout", outputs="lesson + SOP version",
         surface="refresh_learnings / define_sop", blocked="nobody owns the rewrite", escalate_to="growth_lead"),
]


def _overrides() -> Dict[str, Any]:
    try:
        return json.loads(get_setting("sop_owners_json", "") or "{}")
    except Exception:
        return {}


def roles() -> Dict[str, Dict[str, str]]:
    """The cast, with people attached where the team has filled them in."""
    ov = (_overrides().get("roles") or {})
    out = {}
    for key, r in ROLES.items():
        o = ov.get(key) or {}
        out[key] = {**r, "person": o.get("person") or o.get("name") or "", "handle": o.get("handle") or "", "role": o.get("role") or r["role"]}
    return out


def who(key: str) -> str:
    r = roles().get(key) or {"role": key}
    person = (r.get("person") or "").strip(); handle = (r.get("handle") or "").strip()
    return r["role"] + (f" ({person}{' · ' + handle if handle else ''})" if person or handle else "")


def owners_for(sop: Dict[str, Any]) -> Dict[str, Any]:
    """Primary owner, reviewers and per-step owners for one SOP (type + channels + compliance needs)."""
    ov = _overrides()
    by_sop = (ov.get("by_sop") or {}).get(sop.get("id")) or {}
    by_type = (ov.get("by_type") or {}).get(sop.get("campaign_type")) or {}
    primary = by_sop.get("owner") or by_type.get("owner") or TYPE_OWNER.get(str(sop.get("campaign_type")), "crm_ops")
    chans = [str(s.get("channel") or "").lower() for s in (sop.get("steps") or [])]
    reviewers: List[str] = []
    for c in chans:
        for r in CHANNEL_OWNER.get(c, ["crm_ops"]):
            if r not in reviewers and r != primary:
                reviewers.append(r)
    comp = sop.get("compliance") or {}
    if (comp.get("disclaimer_channels") or comp.get("banned_angles") or sop.get("campaign_type") in ("risk", "compliance", "market")) and "compliance" not in reviewers:
        reviewers.append("compliance")
    if float(sop.get("holdout_pct") or 0) > 0 and "data" not in reviewers:
        reviewers.append("data")
    for extra in (by_sop.get("reviewers") or by_type.get("reviewers") or []):
        if extra not in reviewers:
            reviewers.append(extra)
    steps = []
    for i, st in enumerate(sop.get("steps") or []):
        ch = str(st.get("channel") or "").lower()
        owners = CHANNEL_OWNER.get(ch, ["crm_ops"])
        steps.append({"step": i, "day": st.get("day"), "channel": st.get("channel"), "purpose": st.get("purpose"),
                      "owner": who(owners[0]), "reviewer": who(owners[1]) if len(owners) > 1 else who("growth_lead"),
                      "send_time_ist": st.get("send_time_ist"), "condition": st.get("condition")})
    return {"primary": who(primary), "primary_key": primary, "reviewers": [who(r) for r in reviewers], "reviewer_keys": reviewers, "steps": steps,
            "accountable": who(by_sop.get("accountable") or by_type.get("accountable") or "growth_lead"),
            "note": "set real names and handles in Engine → Settings → sop_owners_json; they appear here and in every assistant answer"}


def segments_for(sop: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The ten process segments with this SOP's specifics filled in."""
    ow = owners_for(sop)
    fam = (sop.get("audience") or {}).get("segment_family") or "—"
    detail = {1: f"trigger: {sop.get('user') or sop.get('objective', '')[:90]}", 2: f"segment family {fam} (min reach {(sop.get('audience') or {}).get('min_reach')})",
              3: f"checks: {', '.join((sop.get('checks') or {}).get('preflight') or [])}", 4: f"{len(sop.get('steps') or [])} step(s); disclaimer on {', '.join((sop.get('compliance') or {}).get('disclaimer_channels') or []) or 'no channel'}",
              5: f"holdout {sop.get('holdout_pct')}%", 6: "; ".join(f"D{s.get('day')} {s.get('channel')} {s.get('send_time_ist') or ''}".strip() for s in (sop.get("steps") or [])),
              7: f"kill: {'; '.join(sop.get('kill_criteria') or [])}", 8: f"guardrail {sop.get('guardrail_metric')}",
              9: f"{sop.get('primary_kpi')} {sop.get('target')} over {sop.get('measurement_window_days')}d", 10: f"ideas to test: {'; '.join((sop.get('ideas') or [])[:2]) or '—'}"}
    out = []
    for s in SEGMENTS:
        # segments keep their functional owner; only the two decision gates follow the SOP's owner
        owner_key = ow["primary_key"] if s["n"] in (1, 5) else s["owner"]
        out.append({**s, "owner": who(owner_key), "owner_key": owner_key, "escalate_to": who(s["escalate_to"]), "escalate_key": s["escalate_to"], "detail": detail.get(s["n"], "")})
    return out


def handoffs_for(sop: Dict[str, Any]) -> List[Dict[str, Any]]:
    segs = segments_for(sop)
    out = []
    for a, b in zip(segs, segs[1:]):
        out.append({"from": f"{a['n']}. {a['name']}", "to": f"{b['n']}. {b['name']}", "artefact": a["outputs"], "from_owner": a["owner"], "to_owner": b["owner"], "when": "as soon as the artefact exists; same day for event-triggered SOPs"})
    return out


def escalation_for(sop: Dict[str, Any]) -> List[Dict[str, Any]]:
    ow = owners_for(sop)
    segs = segments_for(sop)
    ladder = []
    for s in segs:
        then = ow["accountable"] if ow["accountable"] != s["escalate_to"] else "the on-call lead (CRM ops channel) — then whoever is accountable for the KPI"
        ladder.append({"blocked_at": f"{s['n']}. {s['name']}", "symptom": s["blocked"], "first": s["escalate_to"], "then": then,
                       "sla": "2 working hours during market hours, next morning otherwise" if s["n"] <= 6 else "same working day"})
    return ladder


def walkthrough(sop: Dict[str, Any]) -> Dict[str, Any]:
    """Everything the assistant needs to answer process / ownership / escalation questions about one SOP."""
    return {"sop": sop.get("id"), "name": sop.get("name"), "owners": owners_for(sop), "segments": segments_for(sop), "handoffs": handoffs_for(sop), "escalation": escalation_for(sop)}


def directory() -> Dict[str, Any]:
    """Who owns what across the whole library (for onboarding answers)."""
    from .sops import list_sops, get_sop
    by_owner: Dict[str, List[str]] = {}
    rows = []
    for s in list_sops():
        full = get_sop(s["id"]) or s
        ow = owners_for(full)
        by_owner.setdefault(ow["primary"], []).append(s["id"])
        rows.append({"id": s["id"], "name": s["name"], "type": s.get("campaign_type"), "owner": ow["primary"], "reviewers": ow["reviewers"]})
    return {"roles": roles(), "sops": rows, "by_owner": {k: {"count": len(v), "sops": v[:12]} for k, v in sorted(by_owner.items(), key=lambda kv: -len(kv[1]))},
            "segments": [{"n": s["n"], "name": s["name"], "what": s["what"], "owner": who(s["owner"]), "surface": s["surface"]} for s in SEGMENTS],
            "note": "defaults derived from campaign type and channels; override per SOP or per type in sop_owners_json"}
