"""
Recommended changes to our SOPs — the development-phase loop.

Every SOP is read against evidence we already hold (our runs and readouts, the
cohort registry, comms limits and the peace index, the playbook's benchmark
ranges and anti-patterns, the methodology radar) and turned into *concrete
edits*: a field path, the current value, the proposed value, why, and what the
evidence is. Each recommendation is ICE-scored, and the ones that are
mechanical carry `auto: true`.

Nothing changes by itself. `apply()` writes a new framework-checked SOP
version; `propose()` queues the same edit as a `sop_change` proposal with a
diff preview so the team accepts or rejects it in the normal queue.

This complements the two checks we already had: `sops.sop_check` says whether
an SOP is *valid*, `sop_india` says whether it is *compliant here* — this says
whether it is *good*, and how to make it better.
"""
from __future__ import annotations
import copy
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from .security import audit, redact

SEV_WEIGHT = {"high": 3, "medium": 2, "low": 1}
KPI_WINDOW = {"_24h": 1, "_72h": 3, "_7d": 7, "_14d": 14, "_30d": 30, "_90d": 90, "_4w": 28}
CHANNEL_GUARDRAIL = {"push": ["notification_disable_rate", "uninstall_rate", "complaint_rate"], "email": ["unsubscribe_rate", "spam_complaint_rate"],
                     "whatsapp": ["block_rate", "opt_out_rate", "complaint_rate"], "sms": ["complaint_rate", "opt_out_rate"],
                     "in-app": ["dismissal_rate", "session_length", "support_ticket_rate"], "cards": ["dismissal_rate", "card_click_rate"]}
# realistic ceilings from clm-campaign-playbook; a target far above these is a promise we cannot keep
TARGET_CEILING = {"first_deposit_rate_7d": 6, "first_trade_rate_7d": 10, "second_trade_within_7d": 12, "kyc_completion_rate_72h": 15, "reactivation_rate_14d": 4,
                  "reactivation_rate_30d": 5, "trade_frequency_recovery_14d": 25, "return_to_trade_rate_30d": 10, "deposit_recovery_rate_24h": 40,
                  "sessions_per_week": 0.5, "alert_adoption_rate": 30, "products_per_user": 0.5, "fee_tier_upgrade_rate": 20, "sip_continuation_rate_90d": 90}
CONVERSION_STOP = {"verified_funded": "deposit_completed", "funded_activated": "first_trade", "activated_habitual": "second_trade", "acquired_verified": "kyc_completed",
                   "slipping": "trade in the last 7 days", "dormant_activated": "any session", "habitual_core": "the cross-sell action"}
IDEA_BANK = {
    "onboarding": ["Test the objection order (money stuck → safety → how much) against the current order", "Try a WhatsApp utility step before the second push"],
    "activation": ["Test watchlist-based first action against a generic one", "Move the D2 education step to Cards and measure push volume saved"],
    "retention": ["Test own-numbers recap against a market recap", "Try a lighter cadence on the top decile and read weekly active weeks"],
    "winback": ["Segment by cause (market / friction / loss) and test cause-matched copy", "Test email-only against email + push on the same cohort"],
    "risk": ["Test the funding-cost line with and without the margin-buffer number", "Try the explainer at T+24h against T+48h"],
    "market": ["Test fact-only against fact + tool", "Shorten the TTL to 2h and read click-through"],
    "education": ["Test a three-part series against a single explainer", "Try the same content as Cards for non-openers"],
    "newsletter": ["Test best-time-to-send against the fixed slot", "Test a shorter digest with one deep link"],
    "compliance": ["Test escalating channels against a single channel", "Measure support contacts avoided, not clicks"],
    "competition": ["Test progress framing against leaderboard framing (never returns)", "Cap entries and read complaint rate"],
    "cohort_upload": ["Compare version-over-version response before re-pointing campaigns", "Test re-pointing standing campaigns immediately vs after a week"],
}


# ── tiny path helpers (dotted, with steps[i]) ────────────────────────────────
def _get(obj: Any, path: str) -> Any:
    cur = obj
    for part in path.split("."):
        m = re.fullmatch(r"([a-zA-Z_]+)\[(\d+)\]", part)
        try:
            if m:
                cur = (cur.get(m.group(1)) or [])[int(m.group(2))]
            else:
                cur = cur.get(part) if isinstance(cur, dict) else None
        except Exception:
            return None
    return cur


def _set(obj: Any, path: str, value: Any) -> None:
    parts = path.split("."); cur = obj
    for part in parts[:-1]:
        m = re.fullmatch(r"([a-zA-Z_]+)\[(\d+)\]", part)
        if m:
            cur = cur.setdefault(m.group(1), [])[int(m.group(2))]
        else:
            cur = cur.setdefault(part, {})
    last = parts[-1]
    m = re.fullmatch(r"([a-zA-Z_]+)\[(\d+)\]", last)
    if m:
        cur.setdefault(m.group(1), [])[int(m.group(2))] = value
    else:
        cur[last] = value


def _num(text: Any) -> Optional[float]:
    m = re.search(r"[-+]?\d+(?:\.\d+)?", str(text or ""))
    return float(m.group(0)) if m else None


def _kpi_window(kpi: str) -> Optional[int]:
    for suf, days in KPI_WINDOW.items():
        if str(kpi or "").endswith(suf):
            return days
    return None


# ── evidence ──────────────────────────────────────────────────────────────────
def evidence() -> Dict[str, Any]:
    """Everything the rules read, fetched once per review."""
    from . import sops as sops_mod, experiments, segments, guardrails
    runs_by_sop: Dict[str, List[Dict[str, Any]]] = {}
    prop_to_sop: Dict[int, Tuple[str, Optional[int]]] = {}
    try:
        for r in sops_mod.list_runs(300):
            runs_by_sop.setdefault(r["sop_id"], []).append(r)
            for pid in (r.get("proposal_ids") or []):
                prop_to_sop[int(pid)] = (r["sop_id"], None)
    except Exception:
        pass
    readouts: Dict[str, List[Dict[str, Any]]] = {}
    try:
        for e in experiments.list_experiments(300):
            sid, step = None, None
            name = str(e.get("campaign_name") or "")
            m = re.match(r"SOP_(sop_[a-z0-9_]+?)_(\d+)_", name)
            if m:
                sid, step = m.group(1), int(m.group(2))
            elif e.get("proposal_id") in prop_to_sop:
                sid = prop_to_sop[int(e["proposal_id"])][0]
            if sid:
                readouts.setdefault(sid, []).append({"step": step, "status": e.get("status"), "kpi": e.get("primary_kpi"), "holdout": e.get("control_group_pct"), "verdict": (e.get("readout") or {}).get("verdict"), "state": (e.get("readout") or {}).get("state")})
    except Exception:
        pass
    fams: Dict[str, Dict[str, Any]] = {}
    try:
        for f in (segments.study([]) or {}).get("families") or []:
            fams[str(f.get("family"))] = f
    except Exception:
        pass
    peace: Dict[str, Dict[str, Any]] = {}
    try:
        for r in (guardrails.peace_index([]) or {}).get("families") or []:
            peace[str(r.get("family"))] = r
    except Exception:
        pass
    radar: List[Dict[str, Any]] = []
    try:
        from . import research
        radar = (research.radar(limit=40) or {}).get("items") or []
    except Exception:
        pass
    india: Dict[str, Dict[str, Any]] = {}
    try:
        from . import sop_india
        india = {x["id"]: x for x in (sop_india.review() or {}).get("sops") or []}
    except Exception:
        pass
    limits = {}
    try:
        limits = guardrails.limits()
    except Exception:
        pass
    return {"runs": runs_by_sop, "readouts": readouts, "families": fams, "peace": peace, "radar": radar, "india": india, "limits": limits}


# ── the rules ─────────────────────────────────────────────────────────────────
def _rec(out: List[Dict[str, Any]], sop: Dict[str, Any], rule: str, area: str, sev: str, title: str, why: str, ev: str,
         path: Optional[str] = None, to: Any = None, impact: int = 6, confidence: int = 7, ease: int = 8) -> None:
    from . import ice as ice_mod
    frm = _get(sop, path) if path else None
    out.append({"id": f"{sop['id']}::{rule}" + (f"::{path}" if path else ""), "sop_id": sop["id"], "rule": rule, "area": area, "severity": sev, "title": title, "why": why, "evidence": ev,
                "change": ({"path": path, "from": frm, "to": to} if path is not None else None), "auto": path is not None, "ice": ice_mod.score(impact, confidence, ease)})


def review_one(sop: Dict[str, Any], ev: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    ev = ev if ev is not None else evidence()
    out: List[Dict[str, Any]] = []
    sid = sop["id"]; ctype = str(sop.get("campaign_type") or ""); steps = sop.get("steps") or []
    aud = sop.get("audience") or {}; fam = str(aud.get("segment_family") or ""); fr = sop.get("frequency") or {}
    runs = ev["runs"].get(sid) or []; reads = ev["readouts"].get(sid) or []
    done = [r for r in reads if r.get("state") == "window_complete" or r.get("verdict")]

    # A. does it run at all, and can we read it
    fam_missing = bool(fam and fam != "*" and not ev["families"].get(fam))
    if not runs and not fam_missing:
        _rec(out, sop, "never_run", "adoption", "low", "Never run — dry-run it on its cohort or retire it",
             "an SOP nobody has run is a document, not a procedure; a dry run surfaces the pre-flight problems while they are cheap to fix",
             "no rows in sop_runs for this SOP", impact=6, confidence=8, ease=9)
    elif runs and not done:
        _rec(out, sop, "no_readout", "measurement", "low", "Runs exist but nothing has read out yet",
             "without a readout the next version is guesswork; check the measurement window has actually elapsed",
             f"{len(runs)} run(s), {len(reads)} experiment(s), none complete", impact=4, confidence=7, ease=7)
    # A4/D13. window shorter than the KPI it measures
    kw = _kpi_window(str(sop.get("primary_kpi") or ""))
    win = int(sop.get("measurement_window_days") or 0)
    if kw and win and win < kw:
        _rec(out, sop, "window_shorter_than_kpi", "measurement", "high", f"Measurement window {win}d is shorter than the KPI's own window ({kw}d)",
             "the KPI cannot be observed inside the window, so every readout will understate the result",
             f"primary_kpi {sop.get('primary_kpi')} implies {kw} days", path="measurement_window_days", to=kw, impact=8, confidence=9, ease=9)
    # D14. holdout discipline until there is evidence
    if not done and float(sop.get("holdout_pct") or 0) < 20:
        _rec(out, sop, "holdout_below_20", "measurement", "medium", f"Raise the holdout to 20% until this SOP has read out once",
             "a new programme needs a 20% control to separate the campaign from the market; drop to 10% once there is a readout",
             f"holdout {sop.get('holdout_pct')}%, {len(done)} completed readouts", path="holdout_pct", to=20, impact=7, confidence=8, ease=9)
    # D15. guardrail that matches the channels used
    chans = [str(s.get("channel") or "").lower() for s in steps]
    guard = str(sop.get("guardrail_metric") or "")
    allowed = {g for c in chans for g in CHANNEL_GUARDRAIL.get(c, [])}
    if chans and allowed and guard and guard not in allowed and guard not in ("support_ticket_rate", "complaint_rate"):
        pick = CHANNEL_GUARDRAIL.get(chans[0], ["complaint_rate"])[0]
        _rec(out, sop, "guardrail_channel_mismatch", "measurement", "medium", f"Guardrail '{guard}' does not match the channels used ({', '.join(sorted(set(chans)))})",
             "the guardrail must be the metric that breaks first on these channels, otherwise harm is invisible",
             f"channels {sorted(set(chans))} → expected one of {sorted(allowed)}", path="guardrail_metric", to=pick, impact=6, confidence=7, ease=9)
    # D16. kill criteria you can actually measure
    kc = sop.get("kill_criteria") or []
    if kc and not any(_num(k) is not None for k in kc):
        _rec(out, sop, "kill_not_numeric", "measurement", "medium", "Kill criteria have no number to trip on",
             "a kill rule without a threshold is never enforced; mid-flight checks need a comparison",
             f"kill_criteria {kc}", impact=6, confidence=8, ease=7)
    # F22. target inside the range the playbook supports
    ceil = TARGET_CEILING.get(str(sop.get("primary_kpi") or ""))
    tnum = _num(sop.get("target"))
    if ceil and tnum and tnum > ceil * 1.5:
        _rec(out, sop, "target_unrealistic", "measurement", "medium", f"Target {sop.get('target')} is far above the range this KPI delivers (≤ ~{ceil})",
             "an unreachable target kills a working programme at the first readout; set it where the playbook says the lift lives",
             f"playbook ceiling for {sop.get('primary_kpi')} ≈ {ceil}", path="target", to=f"+{ceil} pp vs holdout" if ceil < 50 else f"≥ {ceil}% ", impact=6, confidence=7, ease=9)

    # B. cohort reality
    if fam and fam != "*":
        f = ev["families"].get(fam)
        if not f:
            close = sorted(ev["families"], key=lambda k: -len(set(k.split("_")) & set(fam.split("_"))))[:1]
            hint = close[0] if close and set(close[0].split("_")) & set(fam.split("_")) else None
            _rec(out, sop, "family_missing", "cohort", "high", f"Cohort family {fam} does not exist in the registry" + (f" — closest is {hint}" if hint else ""),
                 "the SOP cannot run until the cohort exists; either propose the segment or point the SOP at the family we actually have",
                 f"{len(ev['families'])} families in the registry", path=("audience.segment_family" if hint else None), to=hint, impact=8, confidence=8, ease=6)
        else:
            reach = f.get("reach")
            if reach and int(aud.get("min_reach") or 0) > int(reach):
                _rec(out, sop, "min_reach_above_actual", "cohort", "medium", f"min_reach {aud.get('min_reach')} is above the cohort's actual reach ({reach:,})",
                     "pre-flight will block every run; lower the floor or widen the audience",
                     f"{fam} reach {reach:,}", path="audience.min_reach", to=max(50, int(reach * 0.8)), impact=7, confidence=9, ease=9)
        p = ev["peace"].get(fam)
        cap = fr.get("max_messages_per_user_per_week")
        if p and p.get("status") == "too_much" and cap and float(cap) > 2:
            _rec(out, sop, "family_over_cap", "cadence", "high", f"{fam} is already over its touch cap — cut this SOP to {int(float(cap)) - 1}/week",
                 "the peace index counts every programme on the cohort; the best users hear from us least",
                 f"peace index: {'; '.join(p.get('breaches') or [])[:120]}", path="frequency.max_messages_per_user_per_week", to=int(float(cap)) - 1, impact=7, confidence=8, ease=9)

    # C. sequence design
    for i, st in enumerate(steps):
        ch = str(st.get("channel") or "").lower()
        if i > 0 and not st.get("condition") and str(st.get("purpose", "")).strip() and not str(st.get("purpose", "")).startswith("(internal)"):
            stop = CONVERSION_STOP.get(str(sop.get("transition") or ""), "the conversion event")
            _rec(out, sop, "no_stop_condition", "sequence", "medium", f"Step {i + 1} keeps sending to users who already converted",
                 "later steps need a stop condition, otherwise the cohort that did what we asked is messaged again",
                 f"step {i + 1} ({ch}, day {st.get('day')}) has no condition", path=f"steps[{i}].condition", to=f"no {stop} yet", impact=7, confidence=8, ease=9)
        if ctype in ("market", "risk") and not st.get("ttl_hours") and ch in ("push", "in-app", "cards"):
            _rec(out, sop, "no_ttl_market_step", "sequence", "medium", f"Step {i + 1} is market-linked with no TTL",
                 "a market message that lands late is wrong; expire it instead of delivering a stale fact",
                 f"campaign_type {ctype}, step {i + 1} on {ch}", path=f"steps[{i}].ttl_hours", to=4, impact=6, confidence=8, ease=9)
    days = [int(s.get("day") or 0) for s in steps]
    if len(days) > 1:
        gaps = [(b - a, i) for i, (a, b) in enumerate(zip(days, days[1:]))]
        big = [g for g in gaps if g[0] > 7]
        if big:
            _rec(out, sop, "long_gap", "sequence", "low", f"{big[0][0]}-day silence between step {big[0][1] + 1} and {big[0][1] + 2}",
                 "after a week the context is gone; either close the gap or make the later step a fresh trigger",
                 f"days {days}", impact=5, confidence=6, ease=6)
    if len(chans) >= 3 and len(set(chans)) == 1:
        _rec(out, sop, "single_channel_repeat", "sequence", "medium", f"All {len(chans)} steps are on {chans[0]} — fatigue risk",
             "the playbook moves non-urgent steps to Cards or email; one channel repeated is the fastest way to a disabled notification",
             f"channels {chans}", impact=6, confidence=7, ease=7)

    # F. playbook anti-patterns
    excl = " ".join(str(x).lower() for x in (aud.get("exclusions") or []))
    if ctype in ("market", "competition") and "loss" not in excl:
        _rec(out, sop, "missing_loss_dormant_exclusion", "audience", "high", "Market-linked SOP does not exclude loss-dormant users",
             "market and competition content to users sitting on realised losses is the fastest way to lose them for good",
             f"exclusions {aud.get('exclusions')}", path="audience.exclusions", to=list(aud.get("exclusions") or []) + ["loss-dormant (realised loss >20% of deposits, no trade 21d)"], impact=8, confidence=9, ease=9)
    if not (sop.get("ideas") or []):
        bank = IDEA_BANK.get(ctype) or ["Test one variable against the current version with a 20% holdout"]
        _rec(out, sop, "no_ideas", "learning", "low", "No ideas to test recorded",
             "the ideas list is what turns a run into a next version; without it the SOP never improves",
             f"campaign_type {ctype}", path="ideas", to=bank, impact=4, confidence=7, ease=9)

    # G. freshness and adoption
    upd = str(sop.get("updated_at") or "")[:10]
    if runs and int(sop.get("version") or 1) == 1 and done:
        _rec(out, sop, "version_never_revised", "learning", "medium", "Has read out but is still on version 1",
             "a readout that changes nothing is a wasted experiment; fold the lesson into the SOP",
             f"{len(done)} readout(s), last updated {upd}", impact=6, confidence=7, ease=7)
    ind = ev["india"].get(sid)
    if ind and ind.get("status") != "india-ready":
        _rec(out, sop, "india_fit", "compliance", "high" if ind.get("status") == "rework" else "medium", f"India fit {ind.get('score')}/100 — {len(ind.get('flags') or [])} flag(s) to clear",
             "the India rules are not optional; SOP Library → India fit applies the mechanical ones",
             "flags: " + (", ".join(str(f.get("rule") if isinstance(f, dict) else f) for f in (ind.get("flags") or []))[:120] or "none"), impact=8, confidence=9, ease=8)
    hits = [r for r in ev["radar"] if float(r.get("relevance") or 0) >= 6 and (r.get("tags") or [])[:1] and (r.get("tags") or [])[0] in _radar_tags(sop)][:1]
    if hits:
        r = hits[0]
        _rec(out, sop, "radar_method", "learning", "low", f"New method worth testing here: {str(r.get('title'))[:70]}",
             str(r.get("why_it_matters") or "")[:200] or "the methodology radar flagged this as relevant to this kind of SOP",
             f"{r.get('source')} · relevance {r.get('relevance')} · {r.get('url') or ''}", impact=5, confidence=5, ease=6)

    out.sort(key=lambda r: (-SEV_WEIGHT[r["severity"]], -r["ice"]["score"]))
    score = max(0, 100 - min(70, sum(SEV_WEIGHT[r["severity"]] * 6 for r in out)))
    return {"sop_id": sid, "name": sop.get("name"), "version": sop.get("version"), "campaign_type": ctype, "score": score,
            "status": "good" if score >= 85 else "improve" if score >= 60 else "rework", "recommendations": out,
            "auto_applicable": sum(1 for r in out if r["auto"]), "counts": {k: sum(1 for r in out if r["severity"] == k) for k in ("high", "medium", "low")}}


def _radar_tags(sop: Dict[str, Any]) -> List[str]:
    t = str(sop.get("transition") or ""); c = str(sop.get("campaign_type") or "")
    tags = []
    if t.startswith(("acquired", "verified", "funded")) or c in ("onboarding", "activation"):
        tags.append("activation")
    if t in ("slipping", "dormant_activated") or c in ("retention", "winback"):
        tags.append("retention")
    if c == "market":
        tags.append("journeys")
    if c == "risk":
        tags.append("personalisation")
    chans = {str(s.get("channel") or "").lower() for s in (sop.get("steps") or [])}
    if "whatsapp" in chans or "sms" in chans:
        tags.append("channel_whatsapp")
    if "push" in chans:
        tags.append("channel_push")
    if "email" in chans:
        tags.append("channel_email")
    return tags or ["experimentation"]


def review(sop_id: Optional[str] = None) -> Dict[str, Any]:
    from .sops import list_sops, get_sop
    ev = evidence()
    ids = [sop_id] if sop_id else [s["id"] for s in list_sops()]
    rows = []
    for i in ids:
        sop = get_sop(i)
        if sop:
            rows.append(review_one(sop, ev))
    rows.sort(key=lambda r: (r["score"], -len(r["recommendations"])))
    by_rule: Dict[str, int] = {}
    for r in rows:
        for rec in r["recommendations"]:
            by_rule[rec["rule"]] = by_rule.get(rec["rule"], 0) + 1
    total = sum(len(r["recommendations"]) for r in rows)
    return {"sops": rows if sop_id else [r for r in rows if r["recommendations"]], "reviewed": len(rows), "recommendations": total,
            "counts": {k: sum(r["counts"][k] for r in rows) for k in ("high", "medium", "low")},
            "top_rules": sorted(by_rule.items(), key=lambda kv: -kv[1])[:10], "auto_applicable": sum(r["auto_applicable"] for r in rows),
            "avg_score": round(sum(r["score"] for r in rows) / max(1, len(rows)), 1),
            "note": "recommendations are derived from our own runs and readouts, the cohort registry, comms limits and the playbook; nothing is applied until you apply or approve it"}


# ── applying ──────────────────────────────────────────────────────────────────
def _patched(sop: Dict[str, Any], recs: List[Dict[str, Any]]) -> Tuple[Dict[str, Any], List[str]]:
    spec = copy.deepcopy(sop)
    for k in ("framework_ok", "framework_problems", "steps_count", "active", "updated_at"):
        spec.pop(k, None)
    applied = []
    for r in recs:
        ch = r.get("change")
        if not ch:
            continue
        _set(spec, ch["path"], ch["to"])
        applied.append(f"{ch['path']}: {ch['from']!r} → {ch['to']!r}")
    return spec, applied


def plan(sop_id: str, rec_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    """What would change, with the framework verdict — no writes."""
    from .sops import get_sop, sop_check
    from . import docs_io
    sop = get_sop(sop_id)
    if not sop:
        return {"ok": False, "error": f"unknown SOP {sop_id}"}
    rv = review_one(sop)
    wanted = [r for r in rv["recommendations"] if r["auto"] and (rec_ids is None or r["id"] in set(rec_ids))]
    if not wanted:
        return {"ok": False, "error": "no applicable change selected", "recommendations": rv["recommendations"]}
    spec, applied = _patched(sop, wanted)
    chk = sop_check(spec)
    base = {k: v for k, v in sop.items() if k not in ("framework_ok", "framework_problems", "steps_count", "active", "updated_at")}
    after = review_one({**spec, "version": sop.get("version"), "active": 1})
    return {"ok": True, "sop_id": sop_id, "applied": applied, "diff": docs_io.diff(base, spec)[:60], "framework": chk,
            "score": {"before": rv["score"], "after": after["score"]}, "selected": [{"id": r["id"], "title": r["title"], "severity": r["severity"]} for r in wanted]}


def apply(sop_id: str, rec_ids: Optional[List[str]] = None, actor: str = "user") -> Dict[str, Any]:
    """Write the selected changes as a new framework-checked SOP version."""
    from .sops import get_sop, define_sop
    p = plan(sop_id, rec_ids)
    if not p.get("ok"):
        return p
    if not p["framework"]["ok"]:
        return {"ok": False, "error": "framework check failed after the change", "problems": p["framework"]["problems"], "applied": p["applied"]}
    sop = get_sop(sop_id)
    rv = review_one(sop)
    wanted = [r for r in rv["recommendations"] if r["auto"] and (rec_ids is None or r["id"] in set(rec_ids))]
    spec, applied = _patched(sop, wanted)
    res = define_sop(spec, author=f"improve:{actor}")
    if not res.get("ok"):
        return {"ok": False, "error": "; ".join(res.get("problems") or ["could not save"]), "applied": applied}
    audit("sop.improved", {"id": sop_id, "version": res.get("version"), "changes": applied, "score": p["score"]}, actor=actor)
    return {"ok": True, "sop_id": sop_id, "version": res.get("version"), "applied": applied, "score": p["score"], "diff": p["diff"]}


# ── proposing (the team accepts or rejects) ──────────────────────────────────
def propose(sop_id: str, rec_ids: Optional[List[str]] = None, note: str = "", created_by: str = "agent") -> Dict[str, Any]:
    from . import approvals
    p = plan(sop_id, rec_ids)
    if not p.get("ok"):
        return p
    titles = "; ".join(x["title"] for x in p["selected"])[:160]
    payload = {"sop_id": sop_id, "rec_ids": [x["id"] for x in p["selected"]], "applied": p["applied"], "note": note[:500]}
    pr = approvals.propose("sop_change", f"SOP change: {sop_id} · {titles}", payload,
                           rationale=note or f"{len(p['selected'])} recommended change(s) from the SOP review; score {p['score']['before']} → {p['score']['after']}. " + " | ".join(p["applied"])[:600],
                           risk="low", created_by=created_by)
    return {"proposal_id": pr["id"], "status": pr["status"], "preview": pr.get("preview"), "plan": p,
            "note": "approve on the Ideas board to write the new SOP version; nothing changes until then"}


def _validate(payload: Dict[str, Any]) -> None:
    from .sops import get_sop
    if not payload.get("sop_id") or not get_sop(payload["sop_id"]):
        raise ValueError("unknown SOP")
    if not payload.get("rec_ids"):
        raise ValueError("no recommendations selected")


def _preview(payload: Dict[str, Any]) -> Dict[str, Any]:
    p = plan(payload["sop_id"], payload.get("rec_ids"))
    return {"status": "ready" if p.get("ok") else "stale", "applied": p.get("applied"), "diff": p.get("diff"), "score": p.get("score"),
            "framework_ok": (p.get("framework") or {}).get("ok"), "error": p.get("error")}


def _execute(payload: Dict[str, Any]) -> Dict[str, Any]:
    r = apply(payload["sop_id"], payload.get("rec_ids"), actor=payload.get("_approved_by", "user"))
    if not r.get("ok"):
        raise ValueError(r.get("error") or "could not apply the SOP change")
    return r


def register() -> None:
    from .approvals import register_executor
    register_executor("sop_change", _execute, _preview, _validate)
