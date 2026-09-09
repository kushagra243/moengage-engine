"""
Complete MoEngage workspace analysis — the analyst's report on the whole
programme, not one campaign.

`report()` assembles, from data the engine already computes:
  programme    totals, mix by channel/status, coverage of stats, broadcast share
  lifecycle    campaigns per transition with performance; uncovered transitions
  channels     KPIs per channel against the playbook benchmark ranges → verdict
  campaigns    league table: health score, verdict, diagnosis severity, anomaly
               urgency, deep-dive verdict, recommendation; best/worst; no-stats;
               audience overlap (who is hit by the most campaigns)
  facets       taxonomy programmes/cohorts and which facet responds
  cohorts      decoded families, coverage, peace index
  experiments  running / read / verdicts
  guardrails   SOP monitor findings, comms breaches
  deep_dives   the per-campaign model analyses already stored
  narrative    the analyst report written by the model (bulk tier): executive
               summary, what works, what is broken, ranked actions with ICE,
               risks, data gaps — or a deterministic narrative when no model
               answers (clearly labelled). Persisted in workspace_reports with a
               hash of the inputs so it is only rewritten when data changed.
Nothing here writes to MoEngage; actions flow through proposals as usual.
"""
from __future__ import annotations
import hashlib
import json
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .database import get_db, get_setting
from .security import audit, redact
from . import ice as ice_mod

# directional ranges from clm-campaign-playbook (own history beats them)
BENCH = {
    "push":     {"engagement": (2.0, 8.0), "delivery": 90.0, "label": "push CTR 2–8% (alerts 10–20%)"},
    "email":    {"engagement": (2.0, 5.0), "delivery": 90.0, "label": "email click 2–5% (open 18–30%)"},
    "in-app":   {"engagement": (5.0, 15.0), "delivery": None, "label": "in-app click 5–15%"},
    "whatsapp": {"engagement": (20.0, 70.0), "delivery": 90.0, "label": "WhatsApp utility read 70%+"},
    "sms":      {"engagement": (1.0, 4.0), "delivery": 90.0, "label": "SMS click 1–4%"},
    "cards":    {"engagement": (5.0, 15.0), "delivery": None, "label": "cards click 5–15%"},
}
NARRATIVE_SCHEMA = {
    "executive_summary": "3–5 sentences: state of the programme, the biggest lever, the biggest risk",
    "whats_working": ["fact with numbers → why it works → keep/scale"],
    "whats_broken": ["fact with numbers → likely cause → what to check first"],
    "structural_gaps": ["missing lifecycle journey or measurement discipline and its cost"],
    "top_actions": [{"action": "", "why": "", "who": "segment", "channel": "", "sop": "sop_id or null", "kpi": "", "impact": 1, "confidence": 1, "ease": 1}],
    "risks": ["compliance / fatigue / trust risks visible in the data"],
    "data_gaps": ["what the analysis could not see and which export or event would fix it"],
    "confidence": "low|medium|high with one reason",
}


def init_tables() -> None:
    conn = get_db()
    conn.execute("""CREATE TABLE IF NOT EXISTS workspace_reports (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, source TEXT, inputs_hash TEXT, model TEXT, tier TEXT, narrative_json TEXT, facts_json TEXT)""")
    conn.commit(); conn.close()


def _h(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _ch(name: str) -> str:
    n = str(name or "").lower().replace("_", "-").replace(" ", "-")
    return {"inapp": "in-app", "in-app": "in-app", "in-apps": "in-app", "card": "cards"}.get(n, n)


# ── facts ─────────────────────────────────────────────────────────────────────
def facts() -> Dict[str, Any]:
    from .moengage import MoEngageClient
    from .anomaly.store import normalise_campaign
    from .llm.tools import clm_program_audit, rule_based_audit, campaign_diagnosis, anomaly_report, KPI_BY_TRANSITION
    from . import metrics, taxonomy, segments, guardrails, experiments, analysis as deep
    c = MoEngageClient(); camps = c.get_campaigns(); mode = c.mode
    norm = [normalise_campaign(x) for x in camps]
    with_stats = [n for n in norm if not n.get("stats_missing")]
    tot = lambda k: sum((n.get(k) or 0) for n in with_stats)
    sent, delivered, clicks, conv, revenue = tot("sent_count"), tot("delivered_count"), tot("opened_count"), tot("conversions"), tot("revenue_generated")
    by_channel = Counter(_ch(n.get("channel")) for n in norm); by_status = Counter(str(n.get("status") or "unknown").lower() for n in norm)
    pa = clm_program_audit()
    programme = {"source": mode, "campaigns": len(camps), "with_stats": len(with_stats), "coverage_pct": round(100 * len(with_stats) / max(1, len(camps))), "by_channel": dict(by_channel), "by_status": dict(by_status),
                 "sent": sent, "delivered": delivered, "delivery_rate": round(100 * delivered / sent, 1) if sent else None, "click_rate": round(100 * clicks / delivered, 2) if delivered else None,
                 "conversion_rate": round(100 * conv / delivered, 2) if delivered else None, "revenue": revenue, "broadcast_share_pct": pa.get("broadcast_share_pct"), "unmapped": pa.get("unmapped_count"), "verdict": pa.get("verdict"),
                 "caveat": ("mock data: numbers are illustrative" if mode == "mock" else "stats from the campaign-stats API; no control-group figures are exposed, so lifts are pre/post, not incremental")}
    # lifecycle
    lifecycle = []
    for tid, cov in (pa.get("coverage") or {}).items():
        rows = cov.get("campaigns") or []
        ctrs = [r["ctr"] for r in rows if r.get("ctr") is not None]; convs = [r["conversion_rate"] for r in rows if r.get("conversion_rate") is not None]; dels = [r["delivery_rate"] for r in rows if r.get("delivery_rate") is not None]
        lifecycle.append({"id": tid, "transition": cov.get("transition"), "campaigns": len(rows), "names": [r["name"] for r in rows][:6], "sent": sum((r.get("sent") or 0) for r in rows), "ctr_median": round(statistics.median(ctrs), 2) if ctrs else None,
                          "conv_median": round(statistics.median(convs), 2) if convs else None, "delivery_median": round(statistics.median(dels), 1) if dels else None, "kpi_options": cov.get("kpi_options") or KPI_BY_TRANSITION.get(tid, []),
                          "status": "uncovered" if not rows and tid not in ("promotional", "unmapped") else ("broadcast" if tid == "promotional" else "needs_goal" if tid == "unmapped" else "covered")})
    # channels vs benchmark
    channels = []
    for k in metrics.channel_kpis(camps):
        ch = _ch(k.get("channel")); b = BENCH.get(ch, {"engagement": (2.0, 8.0), "delivery": 90.0, "label": "no benchmark"})
        eng = k.get("engagement_rate"); dl = k.get("delivery_rate"); lo, hi = b["engagement"]
        verdict, why = "in range", f"{k.get('engagement_label', 'engagement').lower()} {eng}% within {lo}–{hi}%"
        if eng is None:
            verdict, why = "no data", "no engagement stats"
        elif eng > hi:
            verdict, why = "strong", f"{eng}% above the {lo}–{hi}% range"
        elif eng < lo:
            verdict, why = "below", f"{eng}% under the {lo}–{hi}% floor"
        if b.get("delivery") and dl is not None and dl < 85:
            verdict, why = "investigate", f"delivery {dl}% < 85% (list hygiene / token health); " + why
        channels.append({**k, "channel": ch, "benchmark": b["label"], "verdict": verdict, "why": why})
    # campaign league table
    audit_rows = {str(r.get("id")): r for r in (rule_based_audit().get("audit") or [])}
    diag_rows = {str(d.get("campaign_id")): d for d in (campaign_diagnosis().get("campaigns") or [])}
    try:
        an = anomaly_report(); anoms = {str(a.get("campaign_id")): a for a in (an.get("anomalies") or []) if not a.get("secondary")}
        history_days = an.get("days_of_history")
    except Exception:
        anoms, history_days = {}, None
    deep_by = {}
    try:
        for d in deep.list_analyses(60):
            deep_by.setdefault(str(d.get("campaign_id")), d)
    except Exception:
        pass
    league = []
    for raw, n in zip(camps, norm):
        cid = n["campaign_id"]; a = audit_rows.get(cid) or {}; dg = diag_rows.get(cid) or {}; am = anoms.get(cid) or {}; dd = deep_by.get(cid) or {}
        league.append({"id": cid, "name": n["campaign_name"], "channel": _ch(n.get("channel")), "status": n.get("status"), "segment": raw.get("target_segment") or raw.get("segment") or "", "transition": next((t["id"] for t in lifecycle if n["campaign_name"] in t["names"]), None),
                       "sent": n.get("sent_count"), "delivered": n.get("delivered_count"), "delivery_rate": n.get("delivery_rate"), "ctr": n.get("ctr"), "conversion_rate": n.get("conversion_rate"), "revenue": n.get("revenue_generated"), "stats_missing": bool(n.get("stats_missing")),
                       "health": a.get("health_score"), "verdict": a.get("verdict"), "recommendation": a.get("recommendation"), "diagnosis": dg.get("headline"), "severity": dg.get("severity"), "cause": ((dg.get("likely_causes") or [{}])[0] or {}).get("cause"),
                       "do_first": ((dg.get("options") or [{}])[0] or {}).get("action"), "anomaly": am.get("urgency"), "deep_verdict": dd.get("verdict"), "deep_next": dd.get("next_action"), "deep_at": dd.get("created_at")})
    sev_rank = {"critical": 0, "act_today": 0, "watch": 1, "warn": 1}
    league.sort(key=lambda r: (sev_rank.get(r.get("severity") or "", 2), sev_rank.get(r.get("anomaly") or "", 2), (r.get("health") if r.get("health") is not None else 101)))
    scored = [r for r in league if not r["stats_missing"] and r.get("delivered")]
    best_ctr = sorted(scored, key=lambda r: -(r.get("ctr") or 0))[:5]; worst_ctr = sorted(scored, key=lambda r: (r.get("ctr") or 0))[:5]
    best_conv = sorted(scored, key=lambda r: -(r.get("conversion_rate") or 0))[:5]
    overlap = Counter(r["segment"] for r in league if r["segment"])
    campaigns = {"league": league, "best_ctr": [{"name": r["name"], "ctr": r["ctr"], "channel": r["channel"]} for r in best_ctr], "worst_ctr": [{"name": r["name"], "ctr": r["ctr"], "channel": r["channel"]} for r in worst_ctr],
                 "best_conversion": [{"name": r["name"], "conversion_rate": r["conversion_rate"], "channel": r["channel"]} for r in best_conv], "no_stats": [r["name"] for r in league if r["stats_missing"]][:20],
                 "audience_overlap": [{"segment": s, "campaigns": n} for s, n in overlap.most_common(8)], "critical": [r["name"] for r in league if r.get("severity") == "critical"], "watch": [r["name"] for r in league if r.get("severity") == "watch"],
                 "history_days": history_days}
    # facets
    cat = taxonomy.catalog(camps)
    facets = {"programmes": [{k: g.get(k) for k in ("key", "campaigns", "delivered", "click_rate", "conversion_rate", "delivery_rate")} for g in (cat.get("programmes") or [])[:10]],
              "cohorts": [{k: g.get(k) for k in ("key", "campaigns", "delivered", "click_rate", "conversion_rate", "delivery_rate")} for g in (cat.get("cohorts") or [])[:10]],
              "comparisons": (cat.get("comparisons") or [])[:8], "unknown_tokens": (cat.get("unknown_tokens") or [])[:12]}
    # cohorts + guardrails
    try:
        st = segments.study(camps); pi = guardrails.peace_index(camps)
        cohorts = {"segments": st.get("segments"), "families": len(st.get("families") or []), "with_campaign": sum(1 for f in st.get("families") or [] if f.get("campaigns_attached")), "worse_than_previous": [f["family"] for f in st.get("families") or [] if "worse_than_previous_version" in (f.get("flags") or [])],
                   "orphans": [f["family"] for f in st.get("families") or [] if "no_campaign_attached" in (f.get("flags") or [])][:8], "peace": pi.get("counts"), "breaches": [{"family": r["family"], "breaches": r["breaches"]} for r in pi.get("families") or [] if r.get("breaches")][:6], "studies": [s.get("title") for s in st.get("studies") or []][:5]}
    except Exception as e:
        cohorts = {"error": redact(str(e))[:120]}
    try:
        mon = guardrails.sop_monitor(None); guard = {"sop_findings": (mon.get("findings") or [])[:6], "limits": guardrails.limits().get("total_per_week"), "north_star": guardrails.north_star()}
    except Exception as e:
        guard = {"error": redact(str(e))[:120]}
    exps = experiments.list_experiments(100)
    experiments_ = {"proposed": sum(1 for e in exps if e.get("status") == "proposed"), "running": sum(1 for e in exps if e.get("status") == "running"), "read": sum(1 for e in exps if e.get("status") == "window_complete"),
                    "with_holdout": sum(1 for e in exps if (e.get("control_group_pct") or 0) >= 5), "verdicts": [{"name": e.get("campaign_name"), "verdict": (e.get("readout") or {}).get("verdict")} for e in exps if (e.get("readout") or {}).get("verdict")][:6]}
    deep_dives = [{k: d.get(k) for k in ("campaign_id", "campaign_name", "created_at", "model", "tier", "verdict", "confidence", "next_action")} for d in deep_by.values()][:20]
    return {"generated_at": datetime.now(timezone.utc).isoformat(), "programme": programme, "lifecycle": lifecycle, "channels": channels, "campaigns": campaigns, "facets": facets, "cohorts": cohorts, "guardrails": guard, "experiments": experiments_, "deep_dives": deep_dives}


# ── narrative ─────────────────────────────────────────────────────────────────
def _deterministic_narrative(f: Dict[str, Any], note: str) -> Dict[str, Any]:
    p = f["programme"]; ch = f["channels"]; c = f["campaigns"]; lc = f["lifecycle"]; co = f.get("cohorts") or {}
    working = [f"{x['channel']}: {x['why']}" for x in ch if x["verdict"] == "strong"] + [f"{r['name']} clicks at {r['ctr']}% ({r['channel']})" for r in c["best_ctr"][:2]]
    broken = [f"{x['channel']}: {x['why']}" for x in ch if x["verdict"] in ("below", "investigate")] + [f"{r['name']}: {r.get('diagnosis') or r.get('recommendation')}" for r in c["league"] if r.get("severity") == "critical"][:3]
    if (p.get("broadcast_share_pct") or 0) > 30:
        broken.append(f"broadcast share {p['broadcast_share_pct']}% of campaigns (playbook cap 30%): promos train users to disable notifications")
    gaps = [f"no standing campaign for {t['transition']} (KPI {', '.join(t['kpi_options'][:1]) or '—'})" for t in lc if t["status"] == "uncovered"][:6]
    if co.get("orphans"):
        gaps.append(f"cohorts uploaded but never messaged: {', '.join(co['orphans'][:4])}")
    actions = []
    for r in [x for x in c["league"] if x.get("severity") == "critical"][:2]:
        actions.append({"action": r.get("do_first") or r.get("recommendation") or f"diagnose {r['name']}", "why": r.get("diagnosis") or "", "who": r.get("segment") or "", "channel": r.get("channel"), "sop": None, "kpi": "delivery_rate" if "deliver" in (r.get("diagnosis") or "").lower() else "ctr", "impact": 8, "confidence": 8, "ease": 7})
    for t in [x for x in lc if x["status"] == "uncovered"][:3]:
        actions.append({"action": f"stand up a triggered campaign for {t['transition']} with a 20% holdout", "why": "largest uncovered lifecycle transition", "who": t["id"], "channel": "in-app + push", "sop": None, "kpi": (t["kpi_options"] or ["—"])[0], "impact": 8, "confidence": 7, "ease": 6})
    for x in [y for y in ch if y["verdict"] in ("below", "investigate")][:2]:
        actions.append({"action": f"fix {x['channel']}: {'list hygiene and token health' if x['verdict'] == 'investigate' else 'creative and audience test with holdout'}", "why": x["why"], "who": "all recipients on the channel", "channel": x["channel"], "sop": None, "kpi": "delivery_rate" if x["verdict"] == "investigate" else "ctr", "impact": 7, "confidence": 7, "ease": 6})
    summary = (f"{p['campaigns']} campaigns ({p['coverage_pct']}% with stats) delivering {p['delivery_rate']}% with click {p['click_rate']}% and conversion {p['conversion_rate']}%. {p.get('verdict') or ''} "
               f"{len([t for t in lc if t['status'] == 'uncovered'])} lifecycle transitions have no standing campaign; {len(c['critical'])} campaign(s) are critical today.").strip()
    return {"executive_summary": summary, "whats_working": working[:6] or ["nothing stands out above benchmark yet"], "whats_broken": broken[:6] or ["no critical issue in the data"], "structural_gaps": gaps or ["every transition has a standing campaign"],
            "top_actions": actions[:8], "risks": ([f"broadcast-heavy mix ({p.get('broadcast_share_pct')}%)"] if (p.get("broadcast_share_pct") or 0) > 30 else []) + ([f"comms breaches in {len(co.get('breaches') or [])} families"] if co.get("breaches") else []),
            "data_gaps": ["no control-group figures from the stats API (lifts are pre/post)", "no per-user send logs (peace index is cohort-level)"] + (["stats missing for: " + ", ".join(c["no_stats"][:4])] if c["no_stats"] else []),
            "confidence": f"medium — deterministic narrative ({note})", "_deterministic": True}


def _model_narrative(f: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    from .llm.provider import LLMClient, llm_settings, LLMError
    cfg = llm_settings()
    if not (cfg["api_key"] or cfg["provider"] == "claude_cli"):
        return None
    slim = {"programme": f["programme"], "lifecycle": [{k: t[k] for k in ("transition", "campaigns", "ctr_median", "conv_median", "delivery_median", "status")} for t in f["lifecycle"]], "channels": [{k: c.get(k) for k in ("channel", "campaigns", "delivery_rate", "engagement_rate", "conversion_rate", "verdict", "why")} for c in f["channels"]],
            "campaigns": [{k: r.get(k) for k in ("name", "channel", "segment", "delivered", "delivery_rate", "ctr", "conversion_rate", "health", "verdict", "diagnosis", "cause", "do_first")} for r in f["campaigns"]["league"][:25]],
            "audience_overlap": f["campaigns"]["audience_overlap"], "facets": f["facets"]["comparisons"][:6], "cohorts": f.get("cohorts"), "experiments": f.get("experiments"), "guardrails": f.get("guardrails")}
    system = ("You are the senior lifecycle-marketing analyst for CoinDCX (crypto and tokenised-markets exchange) reviewing the whole MoEngage programme. Be specific: cite the campaign names, channels, cohorts and numbers given. "
              "Compare against the playbook ranges (push CTR 2–8%, alerts 10–20%, delivery ≥ 90%, email click 2–5%, in-app 5–15%, broadcast ≤ 30%). Never name competitor venues, never forecast prices, no leverage lures. "
              "Score each action with ICE 1–10 (impact, confidence, ease). Output STRICT JSON only, matching the schema keys exactly.")
    prompt = "SCHEMA:\n" + json.dumps(NARRATIVE_SCHEMA) + "\n\nFACTS:\n" + json.dumps(slim, default=str)[:16000] + "\n\nReturn the JSON object."
    try:
        out = LLMClient().chat([{"role": "system", "content": system}, {"role": "user", "content": prompt}], tools=None, max_tokens=2200, temperature=0.2, tier="analysis")
        text = (out.get("content") or "").strip(); s, e = text.find("{"), text.rfind("}")
        parsed = json.loads(text[s:e + 1]) if s >= 0 else {}
        if not isinstance(parsed, dict) or "executive_summary" not in parsed:
            return None
        parsed["_model"] = out.get("model")
        return parsed
    except (LLMError, ValueError, json.JSONDecodeError):
        return None
    except Exception:
        return None


def report(force: bool = False, want_model: bool = True) -> Dict[str, Any]:
    """Facts are always fresh; the narrative is reused while the inputs hash is unchanged (or regenerated with force)."""
    init_tables()
    f = facts()
    key = {"programme": f["programme"], "channels": [(c["channel"], c["verdict"]) for c in f["channels"]], "league": [(r["id"], r.get("severity"), r.get("health")) for r in f["campaigns"]["league"]], "lifecycle": [(t["id"], t["campaigns"]) for t in f["lifecycle"]]}
    h = _h(key)
    conn = get_db(); prev = conn.execute("SELECT * FROM workspace_reports ORDER BY id DESC LIMIT 1").fetchone(); conn.close()
    narrative, model, tier, created = None, None, None, None
    if prev and not force and prev["inputs_hash"] == h:
        narrative, model, tier, created = json.loads(prev["narrative_json"] or "{}"), prev["model"], prev["tier"], prev["created_at"]
    if narrative is None:
        n = _model_narrative(f) if want_model else None
        if n:
            narrative, model, tier = n, n.pop("_model", None), "analysis"
        else:
            narrative, model, tier = _deterministic_narrative(f, "model unavailable or not configured"), None, "deterministic"
        for a in narrative.get("top_actions") or []:
            a["ice"] = ice_mod.score(a.get("impact", 6), a.get("confidence", 6), a.get("ease", 6))
        narrative["top_actions"] = sorted(narrative.get("top_actions") or [], key=lambda a: -a["ice"]["score"])
        conn = get_db()
        conn.execute("INSERT INTO workspace_reports (source, inputs_hash, model, tier, narrative_json, facts_json) VALUES (?,?,?,?,?,?)", (f["programme"]["source"], h, model, tier, json.dumps(narrative, default=str), json.dumps(f, default=str)[:200000]))
        conn.execute("DELETE FROM workspace_reports WHERE id NOT IN (SELECT id FROM workspace_reports ORDER BY id DESC LIMIT 60)")
        conn.commit(); conn.close()
        created = datetime.now(timezone.utc).isoformat()
        audit("workspace_analysis.run", {"model": model, "tier": tier, "campaigns": f["programme"]["campaigns"]}, actor="analyst")
    return {**f, "narrative": narrative, "narrative_meta": {"model": model, "tier": tier, "created_at": created, "inputs_hash": h}, "history": history(10)}


def history(limit: int = 10) -> List[Dict[str, Any]]:
    try:
        init_tables(); conn = get_db()
        rows = conn.execute("SELECT id, created_at, source, model, tier, narrative_json FROM workspace_reports ORDER BY id DESC LIMIT ?", (limit,)).fetchall(); conn.close()
        out = []
        for r in rows:
            n = json.loads(r["narrative_json"] or "{}")
            out.append({"id": r["id"], "at": r["created_at"], "source": r["source"], "model": r["model"], "tier": r["tier"], "summary": (n.get("executive_summary") or "")[:220], "actions": len(n.get("top_actions") or [])})
        return out
    except Exception:
        return []
