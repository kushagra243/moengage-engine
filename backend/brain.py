"""
Brain API — the aggregate the terminal UI consumes. Every module of the
handoff (Brain, Intel, Ideas, Experiments, SOPs, Anomalies, Market) maps to
data the engine already computes; nothing here invents numbers. Where a
metric has no source yet, the value is "—" with the reason in `sub`.
"""
from __future__ import annotations
import json
import re
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from .database import get_db, get_setting, get_latest_daily_run
from .security import audit, redact
from . import approvals, growth, experiments, hacks as hacks_mod, plans, sops as sops_mod, guardrails, datarequests, ice as ice_mod

SEV_COLOR = {"act_now": "#ff5c9e", "high_ev": "#6fe3ff", "counter": "#ffb84d", "cleanup": "#4dffa8", "watch": "#ffb84d", "good": "#4dffa8", "info": "#7fd8ec"}
SEV_LABEL = {"act_now": "ACT NOW", "high_ev": "HIGH EV", "counter": "COUNTER", "cleanup": "CLEANUP", "watch": "WATCH", "good": "GOOD"}


def _ctx() -> Dict[str, Any]:
    try:
        from .market.context import _latest
        return _latest(6 * 3600) or {}
    except Exception:
        return {}


def _client():
    from .moengage import MoEngageClient
    return MoEngageClient()


def _anoms(limit: int = 40) -> Dict[str, Any]:
    try:
        from .anomaly import detect_anomalies
        return detect_anomalies(source=_client().mode, persist=False)
    except Exception as e:
        return {"anomalies": [], "error": redact(str(e))}


def _ago(ts: Optional[str]) -> str:
    if not ts:
        return "—"
    try:
        d = datetime.fromisoformat(str(ts).replace(" ", "T").replace("Z", "+00:00"))
        now = datetime.now(d.tzinfo) if d.tzinfo else datetime.utcnow()
        s = (now - d).total_seconds()
        return f"{int(s // 60)}m ago" if s < 3600 else f"{int(s // 3600)}h ago" if s < 86400 else f"{int(s // 86400)}d ago"
    except Exception:
        return str(ts)[:16]


# ── state / bus / load / pipeline / brief ─────────────────────────────────────
def state() -> Dict[str, Any]:
    from .llm.provider import llm_settings, cli_model
    from .anomaly import snapshot_count
    c = _client(); cfg = llm_settings()
    mode = c.mode
    pend = approvals.list_proposals(status="pending", limit=300)
    exps = experiments.list_experiments(300)
    running = [e for e in exps if e.get("status") == "running"]; read = [e for e in exps if e.get("status") == "window_complete"]
    days = snapshot_count(mode)
    limits = guardrails.limits()
    sched_t = get_setting("schedule_time", "09:00"); sched_on = get_setting("schedule_enabled", "true").lower() == "true"
    latest = get_latest_daily_run() or {}
    runs_n = 0
    try:
        runs_n = get_db().execute("SELECT COUNT(*) FROM daily_runs").fetchone()[0]
    except Exception:
        pass
    bus = [
        {"k": "MODE", "v": "DEMO" if mode == "mock" else "GUARDED", "c": "#ffb84d"},
        {"k": "BRAIN", "v": (cli_model(cfg["model"]) if cfg["provider"] == "claude_cli" else cfg["model"]) if (cfg.get("api_key") or cfg["provider"] == "claude_cli") else "no model", "c": "#dff8ff" if (cfg.get("api_key") or cfg["provider"] == "claude_cli") else "#ff5c9e"},
        {"k": "MEMORY", "v": f"{runs_n} runs · {len(exps)} exp", "c": "#dff8ff"},
        {"k": "GUARDRAILS", "v": f"armed ✓ · ≤{limits.get('total_per_week')}/wk", "c": "#4dffa8"},
        {"k": "SCHEDULER", "v": f"{sched_t} · {'idle' if sched_on else 'off'}", "c": "#dff8ff" if sched_on else "#ffb84d"},
        {"k": "HISTORY", "v": f"{days}d", "c": "#dff8ff"},
    ]
    # core load: honest utilisation proxies
    try:
        board = c.get_campaigns(); n_c = len(board)
        from .anomaly.store import normalise_campaign
        with_stats = sum(1 for x in board if not normalise_campaign(x)["stats_missing"])
        ingest = round(with_stats / n_c * 100) if n_c else 0
    except Exception:
        ingest = 0
    try:
        from .autopilot import recent
        today = date.today().isoformat(); runs_today = [r for r in recent(100) if r["run_date"] == today]
        reasoning = min(100, round(len({r["mission"] for r in runs_today}) / 8 * 100))
    except Exception:
        reasoning = 0
    memory = round(len([e for e in exps if (e.get("readout") or {}).get("state")]) / len(exps) * 100) if exps else 0
    executed_today = [p for p in approvals.list_proposals(limit=300) if p["status"] == "executed" and str(p.get("executed_at") or "")[:10] == date.today().isoformat()]
    actuation = min(100, round(len(executed_today) / max(1, len(executed_today) + len(pend)) * 100))
    load = [{"name": "INGEST", "val": f"{ingest}%", "pct": ingest, "c": "#6fe3ff", "note": "campaigns with stats"}, {"name": "REASONING", "val": f"{reasoning}%", "pct": reasoning, "c": "#6fe3ff", "note": "missions run today"},
            {"name": "MEMORY", "val": f"{memory}%", "pct": memory, "c": "#4dffa8", "note": "experiments with a readout"}, {"name": "ACTUATION", "val": f"{actuation}%", "pct": actuation, "c": "#ffb84d", "note": "approved today vs waiting"}]
    ideas_new = growth.counts().get("new", 0)
    month_plans = plans.month_summary().get("plans", 0)
    lessons = len([i for i in growth.list_ideas(status=None, limit=500) if str(i.get("title", "")).startswith("Lesson:")])
    pipeline = [{"stage": "IDEATED", "count": ideas_new, "note": "ideas in feed", "c": "#6fe3ff"}, {"stage": "PLANNED", "count": month_plans, "note": "flight plans this month", "c": "#6fe3ff"},
                {"stage": "APPROVAL", "count": len(pend), "note": "human gated", "c": "#ffb84d"}, {"stage": "LIVE", "count": len(running), "note": "experiments running", "c": "#4dffa8"}, {"stage": "LEARNED", "count": len(read) + lessons, "note": "read + lessons in memory", "c": "#4dffa8"}]
    mx = max([p["count"] for p in pipeline] + [1])
    for p in pipeline:
        p["pct"] = round(p["count"] / mx * 100)
    ctx = _ctx(); an = _anoms(); bu = an.get("by_urgency", {}) if isinstance(an, dict) else {}
    ci = ctx.get("competitors") or {}
    brief = _brief(latest, an, ctx, pend, read)
    badges = {"exp": len(pend), "ideas": len(pend), "anom": bu.get("act_today", 0), "lab": sum(1 for a in (ci.get("actions") or []) if a.get("priority", 0) >= 80) or (1 if ctx.get("tier0") else 0)}
    try:
        from . import refresher
        _rs = refresher.status(); freshness = {"market_age_min": _rs.get("market_age_min"), "overdue": _rs.get("overdue"), "failing": _rs.get("failing"), "enabled": _rs.get("enabled"), "next": min((r["next_due_min"] for r in _rs.get("jobs", []) if not r["running"]), default=None)}
    except Exception:
        freshness = {}
    return {"bus": bus, "load": load, "pipeline": pipeline, "brief": brief, "autopilot": get_setting("autopilot_enabled", "true").lower() == "true", "badges": badges, "mode": mode, "freshness": freshness,
            "north_star": guardrails.north_star(), "stats": _stats(mode, pend, exps, running, read, days, an, ctx, ci, ideas_new), "region": get_setting("moengage_region", ""), "generated_at": datetime.utcnow().isoformat()}


def _stats(mode, pend, exps, running, read, days, an, ctx, ci, ideas_new) -> Dict[str, List[Dict[str, Any]]]:
    bu = an.get("by_urgency", {}) if isinstance(an, dict) else {}
    hooks = (ctx.get("hooks") or {}); regime = hooks.get("regime") or "unknown"
    reg_c = "#4dffa8" if regime in ("trending_up", "high_volatility_up") else "#ff5c9e" if regime in ("capitulation", "high_volatility_down") else "#ffb84d"
    tokens = {}
    try:
        from .llm.usage import summary
        tokens = summary(1).get("today", {})
    except Exception:
        pass
    n_sops = len(sops_mod.list_sops()); runs = sops_mod.list_runs(50); runs_today = [r for r in runs if str(r.get("created_at", ""))[:10] == date.today().isoformat()]
    camps_all, cov_pct, n_crit, pa_gaps, pa_bs, wa_last = [], 0, 0, [], 0, None
    try:
        from .llm.tools import clm_program_audit, campaign_diagnosis
        from . import workspace_analysis as _wa
        camps_all = _client().get_campaigns(); cov_pct = round(100 * sum(1 for x in camps_all if not x.get("stats_missing")) / max(1, len(camps_all)))
        _pa = clm_program_audit(); pa_gaps = _pa.get("uncovered_transitions") or []; pa_bs = _pa.get("broadcast_share_pct") or 0
        n_crit = sum(1 for d in (campaign_diagnosis().get("campaigns") or []) if d.get("severity") == "critical")
        _h = _wa.history(1); wa_last = _h[0] if _h else None
    except Exception:
        pass
    fam_cov = "—"; fams = []; n_segs = 0; peace_counts = {}; peace_v = "—"; ra_v, ra_sub, ra_c = "—", "money flow composite", "#eaf7fc"
    try:
        from .market import moneyflow
        _ra = (moneyflow.flow(ctx) if ctx else {}).get("risk_appetite") or {}
        if _ra.get("score") is not None:
            ra_v = f"{_ra['score']} · {_ra['label'].upper()}"; ra_sub = "; ".join(_ra.get("reasons", [])[:2]); ra_c = "#4dffa8" if _ra["label"] == "risk-on" else "#ff5c9e" if _ra["label"] == "risk-off" else "#ffb84d"
    except Exception:
        pass
    try:
        _pi = guardrails.peace_index(_client().get_campaigns(), regime); peace_counts = _pi.get("counts") or {}
        peace_v = f"{peace_counts.get('too_much', 0)} / {peace_counts.get('in_band', 0)} / {peace_counts.get('too_little', 0)}"
    except Exception:
        pass
    try:
        from . import segments
        st = segments.study(_client().get_campaigns()); fams = st.get("families") or []; n_segs = st.get("segments") or 0
        fam_cov = f"{round(sum(1 for f in fams if f.get('campaigns_attached')) / len(fams) * 100)}%" if fams else "—"
    except Exception:
        pass
    ex_table = ci.get("exchanges") or []; ours = next((t for t in ex_table if t["exchange"] == "coindcx"), {})
    surges = ci.get("surges") or []; acts = ci.get("actions") or []
    tracked = 0
    try:
        from .anomaly import list_tracked_campaigns
        tracked = len(list_tracked_campaigns(mode))
    except Exception:
        pass
    web3 = ctx.get("web3") or {}
    return {
        "brain": [{"k": "AWAITING HUMAN", "v": str(len(pend)), "sub": f"{sum(1 for p in pend if p.get('kind') == 'create_campaign')} campaigns", "c": "#ffb84d"},
                  {"k": "ACT TODAY", "v": str(bu.get("act_today", 0)), "sub": "anomalies vs own baseline", "c": "#ff5c9e" if bu.get("act_today") else "#4dffa8"},
                  {"k": "LIVE EXPERIMENTS", "v": str(len(running)), "sub": f"{len(read)} read", "c": "#6fe3ff"},
                  {"k": "IDEAS IN FEED", "v": str(ideas_new), "sub": "expire when stale", "c": "#eaf7fc"},
                  {"k": "TOKENS TODAY", "v": f"{int(tokens.get('prompt_tokens', 0) + tokens.get('completion_tokens', 0)):,}", "sub": f"${tokens.get('cost_usd', 0):.3f} · {tokens.get('calls', 0)} calls", "c": "#eaf7fc"}],
        "intel": [{"k": "VENUES TRACKED", "v": str(max(0, len(ex_table) - 1)), "sub": "public tickers · free", "c": "#eaf7fc"},
                  {"k": "SURGES / 24H", "v": str(len(surges)), "sub": f"{sum(1 for s in surges if s.get('we_list_it'))} on pairs we list", "c": "#ffb84d"},
                  {"k": "ACTIONS", "v": str(len(acts)), "sub": f"{sum(1 for a in acts if a.get('priority', 0) >= 80)} priority", "c": "#ff5c9e" if any(a.get('priority', 0) >= 80 for a in acts) else "#6fe3ff"},
                  {"k": "INR-SPOT SHARE", "v": f"{ours.get('share_of_tracked_inr_spot_pct', '—')}%" if ours.get('share_of_tracked_inr_spot_pct') is not None else "—", "sub": "own INR markets vs tracked", "c": "#4dffa8"},
                  {"k": "LISTING GAPS", "v": str(len(ci.get("listing_gaps") or [])), "sub": "pairs they have, we don't · benchmarks below", "c": "#eaf7fc"}],
        "ideas": [{"k": "IDEAS", "v": str(ideas_new), "sub": "in the backlog (feed + recommendations)", "c": "#eaf7fc"}, {"k": "PROPOSED", "v": str(len(pend)), "sub": "awaiting approval", "c": "#ffb84d"},
                  {"k": "RUNNING", "v": str(len(running)), "sub": f"{sum(1 for e in running if (e.get('control_group_pct') or 0) >= 5)} with holdout", "c": "#6fe3ff"}, {"k": "READ", "v": str(len(read)), "sub": "window complete", "c": "#4dffa8"},
                  {"k": "HACKS", "v": str(len(hacks_mod.LIBRARY)), "sub": "sourced tactics", "c": "#7fd8ec"}],
        "exp": [{"k": "PROPOSED", "v": str(len(pend)), "sub": "awaiting approval", "c": "#ffb84d"}, {"k": "RUNNING", "v": str(len(running)), "sub": f"{sum(1 for e in running if (e.get('control_group_pct') or 0) >= 5)} with holdout", "c": "#6fe3ff"},
                {"k": "READ", "v": str(len(read)), "sub": "window complete", "c": "#4dffa8"}, {"k": "ABANDONED", "v": str(sum(1 for e in exps if e.get('status') == 'abandoned')), "sub": "rejected or expired", "c": "#7d95a3"},
                {"k": "EXECUTED", "v": str(sum(1 for p in approvals.list_proposals(limit=400) if p['status'] == 'executed')), "sub": "all time", "c": "#eaf7fc"}],
        "sops": [{"k": "SOPS", "v": str(n_sops), "sub": f"{len({s['campaign_type'] for s in sops_mod.list_sops()})} types", "c": "#eaf7fc"}, {"k": "FRAMEWORK OK", "v": f"{sum(1 for s in sops_mod.list_sops() if s['framework_ok'])}/{n_sops}", "sub": "checks passing", "c": "#4dffa8"},
                 {"k": "RUNS TODAY", "v": str(len(runs_today)), "sub": f"{len(runs)} total", "c": "#ffb84d"}, {"k": "COHORT COVERAGE", "v": fam_cov, "sub": "families with a campaign", "c": "#eaf7fc"},
                 {"k": "TEAM EDITS", "v": str(sum(1 for s in sops_mod.list_sops(include_inactive=True) if s.get('source') not in ('library',))), "sub": "versioned SOPs", "c": "#6fe3ff"}],
        "anom": [{"k": "TRACKED", "v": str(tracked), "sub": "campaigns", "c": "#eaf7fc"}, {"k": "ACT TODAY", "v": str(bu.get("act_today", 0)), "sub": "severity high", "c": "#ff5c9e"},
                 {"k": "WATCH", "v": str(bu.get("watch", 0)), "sub": "drifting", "c": "#ffb84d"}, {"k": "GOOD SURPRISE", "v": str(bu.get("good_surprise", 0)), "sub": "above baseline", "c": "#4dffa8"}, {"k": "HISTORY", "v": f"{days}d", "sub": "snapshot depth", "c": "#eaf7fc"}],
        "analysis": [{"k": "CAMPAIGNS", "v": str(len(camps_all)), "sub": f"{cov_pct}% with stats · {mode}", "c": "#eaf7fc"}, {"k": "CRITICAL", "v": str(n_crit), "sub": "diagnosis severity", "c": "#ff5c9e" if n_crit else "#4dffa8"},
                     {"k": "UNCOVERED", "v": str(len(pa_gaps)), "sub": "lifecycle transitions", "c": "#ffb84d" if pa_gaps else "#4dffa8"}, {"k": "BROADCAST", "v": f"{pa_bs}%", "sub": "share of campaigns · cap 30%", "c": "#ff5c9e" if (pa_bs or 0) > 30 else "#4dffa8"},
                     {"k": "LAST REPORT", "v": _ago(wa_last.get("at")) if wa_last else "never", "sub": ((wa_last or {}).get("tier") or "") + (" · " + wa_last["model"] if wa_last and wa_last.get("model") else ""), "c": "#eaf7fc"}],
        "lab": [{"k": "RISK APPETITE", "v": ra_v, "sub": ra_sub, "c": ra_c},
                {"k": "REGIME", "v": regime.replace("_", " ").upper(), "sub": f"{len(hooks.get('hooks') or [])} hooks allowed", "c": reg_c},
                {"k": "TIER-0", "v": "ARMED" if ctx.get("tier0") else "none", "sub": (ctx.get("tier0") or {}).get("kind", "no major event").replace("_", " "), "c": "#ff5c9e" if ctx.get("tier0") else "#4dffa8"},
                {"k": "RIVAL ACTIONS", "v": str(len(acts)), "sub": f"{len(surges)} surges on pairs we list" if surges else "no surges", "c": "#ff5c9e" if any(a.get('priority', 0) >= 80 for a in acts) else "#6fe3ff"},
                {"k": "LAST SYNC", "v": _ago(ctx.get("generated_at")), "sub": "auto every 15 min", "c": "#eaf7fc"}],
        "atlas": [{"k": "COHORT FAMILIES", "v": str(len(fams)), "sub": f"{n_segs} segments decoded", "c": "#eaf7fc"}, {"k": "COVERAGE", "v": fam_cov, "sub": "families with a campaign", "c": "#4dffa8"},
                  {"k": "PEACE", "v": peace_v, "sub": "too much / in band / too little", "c": "#ff5c9e" if peace_counts.get("too_much") else "#4dffa8"}, {"k": "LIMITS", "v": f"≤{guardrails.limits().get('total_per_week')}/wk", "sub": "per user, regime-adjusted", "c": "#6fe3ff"}, {"k": "NORTH STAR", "v": "set", "sub": guardrails.north_star()[:60], "c": "#eaf7fc"}],
        "market": [{"k": "REGIME", "v": regime.replace("_", " ").upper(), "sub": f"BTC {((ctx.get('crypto') or {}).get('assets') or {}).get('BTC', {}).get('chg_24h', '—')}% 24h", "c": reg_c},
                   {"k": "HOOKS", "v": str(len(hooks.get("hooks") or [])), "sub": f"{len(hooks.get('blocked_hook_ids') or [])} blocked by policy", "c": "#6fe3ff"},
                   {"k": "TIER-0", "v": "ARMED" if ctx.get("tier0") else "none", "sub": (ctx.get("tier0") or {}).get("kind", "no major event").replace("_", " "), "c": "#ff5c9e" if ctx.get("tier0") else "#4dffa8"},
                   {"k": "WEB3 TRENDING", "v": str(len(web3.get("trending") or [])), "sub": "quality-gated, unverified", "c": "#eaf7fc"},
                   {"k": "LAST SYNC", "v": _ago(ctx.get("generated_at")), "sub": "cached" if ctx.get("cached") else "fresh", "c": "#eaf7fc"}],
    }


def _brief(latest, an, ctx, pend, read) -> List[Dict[str, str]]:
    out = []
    top = [e for e in (an.get("anomalies") or []) if not e.get("secondary")]
    act = [e for e in top if e.get("urgency") == "act_today"]
    if act:
        out.append({"sev": "act_now", "t": f"{len(act)} campaign(s) need a decision today; the sharpest is {act[0].get('campaign_name')}: {act[0].get('headline', '').split(':', 1)[-1].strip()}"})
    else:
        out.append({"sev": "good", "t": "No campaign is outside its own baseline band today."})
    if pend:
        camp = next((p for p in pend if p["kind"] == "create_campaign"), pend[0])
        out.append({"sev": "high_ev", "t": f"{len(pend)} proposal(s) await approval; the first in line is “{camp['title']}”."})
    ci = ctx.get("competitors") or {}
    a0 = next((a for a in (ci.get("actions") or []) if a.get("owner", "").startswith("marketing")), None)
    if a0:
        out.append({"sev": "counter", "t": a0["what"]})
    reg = (ctx.get("hooks") or {}).get("regime")
    if reg:
        out.append({"sev": "good" if reg in ("trending_up", "chop") else "watch", "t": f"Regime is {reg.replace('_', ' ')}; the angle policy allows {', '.join((ctx.get('hooks') or {}).get('angle_policy', {}).get('prefer', [])[:3])}."})
    if read:
        out.append({"sev": "good", "t": f"{len(read)} experiment(s) have a complete window; read them before repeating a tactic."})
    ex = (latest.get("report_data") or {}).get("executive_summary") if isinstance(latest, dict) else None
    if ex:
        out.insert(0, {"sev": "info", "t": str(ex)[:220]})
    return out[:5]


# ── directives ────────────────────────────────────────────────────────────────
def directives(limit: int = 12) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    ctx = _ctx()
    t0 = ctx.get("tier0")
    if t0:
        out.append({"id": "tier0", "severity": "act_now", "title": f"Tier-0 event: {t0['kind'].replace('_', ' ')}", "meta": "market · now", "rationale": t0["detail"] + ". Promotions freeze; the global announcement SOP with product lenses is the response.", "actions": ["simulate", "hold"], "rank": 100})
    an = _anoms()
    for e in [x for x in (an.get("anomalies") or []) if x.get("urgency") == "act_today" and not x.get("secondary")][:3]:
        out.append({"id": f"anomaly:{e.get('campaign_id')}:{e.get('metric')}", "severity": "act_now", "title": e.get("headline", "").split(":", 1)[0], "meta": f"{e.get('metric')} {e.get('delta_text') or ''} · n={e.get('n_history')}",
                    "rationale": (e.get("headline") or "") + " " + (e.get("how_we_know") or ""), "actions": ["simulate", "hold"], "rank": 90})
    for p in approvals.list_proposals(status="pending", limit=50):
        goal = (p.get("payload") or {}).get("goal") or {}
        sev = "cleanup" if p["kind"] in ("pause_campaign", "code_change") else "high_ev"
        meta = f"#{p['id']} · {p.get('category') or p['kind']}" + (f" · holdout {goal.get('control_group_pct')}%" if goal else "") + f" · {_ago(p.get('created_at'))}"
        out.append({"id": f"proposal:{p['id']}", "severity": sev, "title": p["title"], "meta": meta, "rationale": (p.get("rationale") or "")[:280], "actions": ["approve", "simulate", "hold"], "rank": 70 + (5 if p.get("created_by") == "autopilot" else 0)})
    ci = ctx.get("competitors") or {}
    for a in [x for x in (ci.get("actions") or []) if x.get("priority", 0) >= 70][:3]:
        out.append({"id": f"compete:{a['type']}:{a['symbol']}", "severity": "counter", "title": f"{a['type'].replace('_', ' ')} · {a['symbol']}", "meta": f"owner {a.get('owner')} · intel", "rationale": a["what"], "actions": ["simulate", "hold"], "rank": a.get("priority", 60)})
    try:
        mon = guardrails.sop_monitor(((ctx.get("hooks") or {}).get("regime")))
        for f in (mon.get("findings") or [])[:2]:
            out.append({"id": f"sop:{f['run']}:{f['proposal']}", "severity": "cleanup", "title": f"SOP {f['type']}: run #{f['run']} {f['sop']}", "meta": f"proposal #{f['proposal']}", "rationale": f["detail"], "actions": ["hold"], "rank": 65})
    except Exception:
        pass
    out.sort(key=lambda d: -d["rank"])
    for d in out:
        d["sev_label"] = SEV_LABEL.get(d["severity"], d["severity"].upper()); d["color"] = SEV_COLOR.get(d["severity"], "#7fd8ec")
    return out[:limit]


def act_on_directive(did: str, action: str, actor: str = "user", note: str = "") -> Dict[str, Any]:
    kind, _, rest = did.partition(":")
    if kind == "proposal":
        pid = int(rest)
        if action == "approve":
            r = approvals.approve_and_execute(pid, decided_by=actor, note=note or "approved from terminal")
            return {"ok": r["status"] == "executed", "status": r["status"], "error": r.get("error"), "trace": f"directive/approve #{pid} → {r['status']}"}
        if action == "hold":
            approvals.add_comment(pid, note or "held by operator — needs discussion", actor=actor)
            return {"ok": True, "status": "pending", "trace": f"directive/hold #{pid} · comment added"}
        if action == "simulate":
            p = approvals.get_proposal(pid) or {}
            pl = p.get("payload") or {}
            from .llm.tools import campaign_brief_check
            chk = campaign_brief_check(pl.get("goal") or {}, pl.get("variants") or [], channel=str(pl.get("channel") or "push"), market_linked=bool(pl.get("market_hook_id") or pl.get("ttl_hours")), ttl_hours=pl.get("ttl_hours")) if p.get("kind") == "create_campaign" else {"ok": True, "problems": [], "warnings": []}
            audit("directive.simulate", {"id": pid, "ok": chk["ok"]}, actor=actor)
            return {"ok": chk["ok"], "preview": p.get("preview"), "brief_check": chk, "trace": f"directive/simulate #{pid} · brief {'ok' if chk['ok'] else 'problems'} · preview {(p.get('preview') or {}).get('mode', '—')}"}
    if kind == "anomaly":
        cid = rest.split(":")[0]
        if action == "simulate":
            from .anomaly.diagnose import diagnose_campaign
            try:
                d = diagnose_campaign(cid, _client().mode)
            except Exception as e:
                d = {"error": redact(str(e))}
            audit("directive.simulate", {"anomaly": cid}, actor=actor)
            return {"ok": True, "diagnosis": d, "trace": f"anomaly/diagnose {cid} · {len((d or {}).get('options') or [])} options"}
        audit("directive.hold", {"anomaly": cid}, actor=actor)
        return {"ok": True, "trace": f"anomaly/hold {cid}"}
    if kind == "compete":
        sym = rest.split(":")[-1]
        if action == "simulate":
            from .market.competitors import pair_battle
            return {"ok": True, "pair_battle": pair_battle(sym), "trace": f"intel/pair-battle {sym}"}
        audit("directive.hold", {"compete": rest}, actor=actor)
        return {"ok": True, "trace": f"intel/hold {sym}"}
    if kind == "tier0":
        audit("directive." + action, {"tier0": True}, actor=actor)
        return {"ok": True, "trace": f"tier0/{action} · run sop_global_announcement_lenses via the brain", "agent_prompt": "Tier-0 event: call announcement_lenses, write the core fact and product lenses, run_sop('sop_global_announcement_lenses') dry-run then for real."}
    if kind == "sop":
        audit("directive.hold", {"sop": rest}, actor=actor)
        return {"ok": True, "trace": f"sop/hold {rest}"}
    return {"ok": False, "error": "unknown directive"}


# ── ideas / experiments / intel / anomalies / market / trace ──────────────────
CATEGORY_OF = {"trending_campaign": "audience", "growth_hack": "sop", "market_play": "market", "moengage_activity": "cadence", "fix": "cadence"}


def _effort(v) -> str:
    v = str(v or "medium").lower()
    return "LOW" if v.startswith("l") else "HIGH" if v.startswith("h") else "MED"


def ideas(filter_: str = "all") -> List[Dict[str, Any]]:
    out = []
    for i in growth.list_ideas(status="new", limit=60):
        cat = "counter" if "compet" in (i.get("title") or "").lower() or (i.get("data") or {}).get("competitive") else CATEGORY_OF.get(i.get("kind"), "audience")
        conf = 0.72 if i.get("source") == "rules" else 0.55
        data = i.get("data") or {}
        structural = bool(data.get("structural")) or i.get("kind") == "structural_gap"
        if structural:
            cat = "structural"; conf = 0.8
        ic = ice_mod.score(**{k: data["ice"][k] for k in ("impact", "confidence", "ease")}) if isinstance(data.get("ice"), dict) and all(k in data["ice"] for k in ("impact", "confidence", "ease")) else ice_mod.infer(i.get("expected_impact") or "", conf, i.get("effort"), i.get("source") or "", i.get("priority"))
        out.append({"id": f"#IDEA-{i['id']}", "raw_id": i["id"], "kind": "idea", "title": i["title"], "tags": [t for t in [i.get("kind"), i.get("channel"), i.get("angle")] if t][:3], "hypothesis": (i.get("why") or "")[:320],
                    "projectedLift": i.get("expected_impact") or "—", "confidence": f"{conf:.2f}", "effort": _effort(i.get("effort")), "sourceSignal": f"{i.get('source')} · {i.get('transition') or i.get('kpi') or 'feed'}", "category": cat, "how": (i.get("how") or "")[:300], "priority": i.get("priority", 50),
                    "ice": ic, "tagline": data.get("tagline") or ice_mod.tagline(i["title"], i.get("kpi") or "", i.get("segment") or ""), "structural": structural, "sop": data.get("sop")})
    try:
        rk = hacks_mod.ranked(); hack_rows = (rk.get("hacks") if isinstance(rk, dict) else rk) or []
    except Exception:
        hack_rows = []
    for h in hack_rows[:12]:
        if h.get("status") in ("dismissed",):
            continue
        hconf = 0.8 if h.get("source_kind") != "model" else 0.5
        out.append({"id": f"#HACK-{h['id']}", "raw_id": h["id"], "kind": "hack", "title": h["title"], "tags": [h.get("category")] + (h.get("channels") or [])[:2], "hypothesis": (h.get("why") or "")[:320], "projectedLift": h.get("kpi") or "—",
                    "ice": ice_mod.infer(h.get("kpi") or "", hconf, h.get("effort"), "library", h.get("relevance") if isinstance(h.get("relevance"), (int, float)) else 50), "tagline": ice_mod.tagline(h["title"], h.get("kpi") or "", ""), "structural": False,
                    "confidence": "0.80" if h.get("source_kind") != "model" else "0.50", "effort": _effort(h.get("effort")), "sourceSignal": f"hack · {h.get('source') or 'library'}", "category": "sop" if h.get("category") in ("retention", "activation") else "market" if h.get("category") in ("risk",) else "audience", "how": (h.get("how") or "")[:300], "priority": h.get("relevance", 50) if isinstance(h.get("relevance"), (int, float)) else 50})
    if filter_ and filter_ != "all":
        out = [o for o in out if o["category"] == filter_]
    out.sort(key=lambda o: (-(1 if o.get("structural") else 0), -((o.get("ice") or {}).get("score") or 0), -(o.get("priority") or 0)))
    return out


def promote_idea(raw: str, actor: str = "user") -> Dict[str, Any]:
    if str(raw).isdigit():
        growth.set_status(int(raw), "proposed")
        i = next((x for x in growth.list_ideas(status="proposed", limit=200) if x["id"] == int(raw)), None) or {}
        audit("idea.promote", {"id": raw}, actor=actor)
        return {"ok": True, "trace": f"idea/promote #IDEA-{raw} → brain drafting proposals", "agent_prompt": f"Turn growth idea #{raw} '{i.get('title', '')}' into an experiment: campaign_brief_check, experiment_plan, then propose_segment/propose_campaign (or run the matching SOP) with two compliant variants and a 20% holdout. Why: {i.get('why', '')[:300]} How: {i.get('how', '')[:300]}"}
    hacks_mod.set_status(raw, "proposed")
    audit("hack.promote", {"id": raw}, actor=actor)
    return {"ok": True, "trace": f"idea/promote #HACK-{raw} → brain drafting proposals", "agent_prompt": f"Turn growth hack '{raw}' into an experiment via the matching SOP (list_sops) or propose_campaign with a full goal brief and two compliant variants."}


def experiments_board(filter_: str = "all") -> Dict[str, Any]:
    props = approvals.list_proposals(limit=400)
    exps = {e["proposal_id"]: e for e in experiments.list_experiments(400)}
    cols = {"proposed": [], "simulated": [], "live": [], "read": [], "archive": []}
    for p in props:
        e = exps.get(p["id"]) or {}
        pl = p.get("payload") or {}
        goal = pl.get("goal") or {}
        metric = (f"holdout {goal.get('control_group_pct')}% · {goal.get('primary_kpi')}" if goal else p.get("kind", ""))
        try:
            from .llm.roster import council_summary
            council = council_summary(p)
        except Exception:
            council = None
        card = {"id": p["id"], "title": p["title"], "tag": p.get("category") or p["kind"], "product": p.get("product"), "note": (p.get("rationale") or "")[:140], "metric": metric, "created_at": p.get("created_at"), "council": council,
                "ice": ice_mod.from_payload(pl, p.get("created_by") or "agent") if p["kind"] in ("create_campaign", "create_flow", "create_segment") else None,
                "tagline": (pl.get("ice") or {}).get("tagline") or (ice_mod.tagline(pl.get("name") or p["title"], goal.get("primary_kpi") or "", pl.get("target_segment") or "") if goal else None)}
        st = p["status"]; pv = p.get("preview") or {}
        if st == "pending":
            if p["kind"] == "code_change" and pv.get("status") == "drafted":
                card["metric"] = "diff drafted"; cols["simulated"].append(card)
            else:
                cols["proposed"].append(card)
        elif st == "approved":
            cols["simulated"].append(card)
        elif st == "executed":
            ro = e.get("readout") or {}
            if e.get("status") == "window_complete":
                card["metric"] = (ro.get("verdict") or "read")[:80]; cols["read"].append(card)
            else:
                card["metric"] = f"day {ro.get('days_run', 0)} of {ro.get('window_days', goal.get('measurement_window_days', '—'))}" if ro else "running"; cols["live"].append(card)
        else:
            card["metric"] = st; cols["archive"].append(card)
    for k in cols:
        cols[k].sort(key=lambda c: (-((c.get("ice") or {}).get("score") or 0), -(c["id"] or 0)) if k == "proposed" else -(c["id"] or 0))
    idea_cards: List[Dict[str, Any]] = []
    try:
        if filter_ in ("all", "structural", "market", "recommended"):
            for r in recommendations():
                if r.get("on_board") or (filter_ == "structural" and not r.get("structural")) or (filter_ == "market" and r.get("structural")):
                    continue
                idea_cards.append({"id": r["id"], "kind": "recommendation", "title": r["title"], "tag": "P0 structural" if r.get("structural") else f"P{r.get('priority')} market", "product": r.get("product"), "note": (r.get("why") or "")[:220], "metric": f"ICE {r['ice']['score']}", "ice": r.get("ice"), "tagline": r.get("tagline"),
                                   "who": r.get("who"), "what": r.get("what"), "sop": r.get("sop"), "kpi": r.get("kpi"), "benchmark": r.get("benchmark"), "urgency": r.get("urgency"), "avoid": r.get("avoid"), "structural": bool(r.get("structural")), "category": "structural" if r.get("structural") else "market"})
    except Exception as e:
        idea_cards.append({"id": "rec:error", "kind": "recommendation", "title": "recommendations unavailable", "note": redact(str(e))[:120], "metric": "—", "tag": "error"})
    rec_ids = {c["title"] for c in idea_cards}
    for i in ideas(filter_ if filter_ != "recommended" else "__none__"):
        if i["title"] in rec_ids:
            continue
        idea_cards.append({"id": i["id"], "raw_id": i["raw_id"], "kind": i["kind"], "title": i["title"], "tag": i["category"], "note": i.get("hypothesis") or "", "metric": f"ICE {(i.get('ice') or {}).get('score', '—')}", "ice": i.get("ice"), "tagline": i.get("tagline"), "how": i.get("how"),
                           "projectedLift": i.get("projectedLift"), "confidence": i.get("confidence"), "effort": i.get("effort"), "sourceSignal": i.get("sourceSignal"), "structural": bool(i.get("structural")), "category": i["category"], "sop": i.get("sop"), "tags": i.get("tags")})
    idea_cards.sort(key=lambda c: (-(1 if c.get("structural") else 0), -((c.get("ice") or {}).get("score") or 0)))
    return {"filter": filter_, "filters": ["all", "structural", "market", "audience", "counter", "sop", "cadence"], "columns": [{"key": "ideas", "name": "IDEAS", "c": "#ff5c9e", "cards": idea_cards[:60]}, {"key": "proposed", "name": "PROPOSED", "c": "#ffb84d", "cards": cols["proposed"][:30]}, {"key": "simulated", "name": "SIMULATED", "c": "#6fe3ff", "cards": cols["simulated"][:30]},
                        {"key": "live", "name": "LIVE", "c": "#4dffa8", "cards": cols["live"][:30]}, {"key": "read", "name": "READ", "c": "#7fd8ec", "cards": cols["read"][:30]}, {"key": "archive", "name": "ARCHIVE", "c": "#7d95a3", "cards": cols["archive"][:30]}]}


def intel() -> Dict[str, Any]:
    ci = (_ctx().get("competitors")) or {}
    ex = [t for t in (ci.get("exchanges") or []) if t["exchange"] != "coindcx"]; ours = next((t for t in (ci.get("exchanges") or []) if t["exchange"] == "coindcx"), {})
    surges = ci.get("surges") or []; gaps = ci.get("listing_gaps") or []; acts = ci.get("actions") or []; battles = ci.get("pair_battles") or []
    rivals = []
    for t in ex:
        idx = 0.0
        our_v = ours.get("vol_24h_usd") or 1
        idx += min(40, 10 * max(0, (t.get("vol_24h_usd") or 0) / max(our_v, 1)) ** 0.5)
        chg = t.get("cmc_vol_chg_24h_pct")
        idx += min(20, max(0, float(chg or 0)) / 5)
        s_n = sum(1 for s in surges if s["competitor"] == t["exchange"]); idx += min(20, s_n * 7)
        g_n = sum(1 for g in gaps if g["competitor"] == t["exchange"]); idx += min(10, g_n * 2)
        def _num(x):
            try:
                return float(x or 0)
            except (TypeError, ValueError):
                return 0.0
        if _num(t.get("weekly_visits")) > _num(ours.get("weekly_visits")):
            idx += 10
        idx = int(min(100, round(idx)))
        threat = "high" if idx >= 70 else "elevated" if idx >= 45 else "watch" if idx >= 25 else "low"
        color = {"high": "#ff5c9e", "elevated": "#ffb84d", "watch": "#6fe3ff", "low": "#4dffa8"}[threat]
        move = (f"{s_n} pair(s) surging in 24h" if s_n else "") or (f"total volume {float(chg):+.0f}% in 24h (CMC)" if chg is not None else "") or f"{t.get('pairs', 0)} pairs listed; {t.get('kind')}"
        tactics = []
        if s_n: tactics.append("volume surge")
        if g_n: tactics.append(f"{g_n} listings we lack")
        if t.get("taker_fee_pct") is not None and ours.get("taker_fee_pct") is not None and float(t["taker_fee_pct"]) < float(ours["taker_fee_pct"]): tactics.append("lower taker fee")
        if t.get("kind"): tactics.append(t["kind"])
        mine_s = [s for s in surges if s["competitor"] == t["exchange"] and s.get("we_list_it")]
        camp_act = next((a for a in acts if a.get("type") == "counter_campaign" and a.get("symbol") == t["exchange"]), None)
        if camp_act:
            counter = camp_act["what"].split("→", 1)[-1].strip()
        elif mine_s:
            counter = f"spotlight {', '.join(list(dict.fromkeys(x['symbol'] for x in mine_s))[:3])} to our watchers/holders today (they are surging there)"
        elif g_n:
            counter = f"listing asks for {g_n} pair(s) they have; spotlight what we already list"
        elif t.get("taker_fee_pct") is not None and ours.get("taker_fee_pct") is not None and float(t["taker_fee_pct"]) < float(ours["taker_fee_pct"]):
            counter = "total-cost transparency and fee tiers, not a headline fee cut"
        else:
            counter = "hold; monitor cadence and app rank"
        minor = t["exchange"] not in ("delta", "bybit", "binance", "okx", "bitget", "coinbase", "mudrex") and (t.get("vol_24h_usd") or 0) < 5e6 and idx < 30
        rivals.append({"id": t["exchange"], "name": t["name"], "threat": threat.upper(), "color": color, "pressureIndex": idx, "latestMove": move, "tactics": tactics[:4], "counterPlay": counter[:160], "vol_24h_usd": t.get("vol_24h_usd"), "share": t.get("share_of_tracked_inr_spot_pct"), "visits": t.get("weekly_visits"), "source": t.get("source"), "minor": minor})
    rivals.sort(key=lambda r: -r["pressureIndex"])
    moves = []
    for s in surges[:8]:
        moves.append({"t": "24h", "what": f"{s['symbol']} {s['product']} volume +{s['surge_pct']}% at {s['competitor']}", "chan": s["product"], "impact": "MATERIAL" if s.get("we_list_it") else "OPPORTUNITY", "c": "#ff5c9e" if s.get("we_list_it") else "#4dffa8"})
    for a in [x for x in acts if x["type"] == "venue_volume_jump"][:2]:
        moves.append({"t": "24h", "what": a["what"][:110], "chan": "venue", "impact": "WATCH", "c": "#ffb84d"})
    for g in gaps[:6]:
        moves.append({"t": _ago(g.get("first_seen_there")) if g.get("first_seen_there") else "—", "what": f"{g['symbol']} {g['product']} trades ${(g['their_vol_usd'] or 0) / 1e6:.1f}M/24h at {g['competitor']}; not listed here", "chan": "listing", "impact": "OPPORTUNITY", "c": "#4dffa8"})
    sov = [{"name": t["name"] if t["exchange"] != "coindcx" else "Ours", "pct": t.get("share_of_tracked_inr_spot_pct"), "c": "#6fe3ff" if t["exchange"] == "coindcx" else "#7d95a3"} for t in (ci.get("exchanges") or []) if t.get("share_of_tracked_inr_spot_pct") is not None]
    sov.sort(key=lambda s: -(s["pct"] or 0))
    camps = []; apps = {}
    try:
        from .market.campaign_intel import campaigns as _camps
        cc = _camps(hours=48)
        camps = cc.get("campaigns") or []; apps = cc.get("apps") or {}
        for c in camps[:6]:
            moves.insert(0, {"t": _ago(c.get("published_at") or c.get("first_seen")), "what": f"{c['venue']}: {c['title'][:100]}", "chan": c["type"].replace("_", " "), "impact": c["impact"], "c": "#ff5c9e" if c["impact"] == "MATERIAL" else "#ffb84d" if c["impact"] == "WATCH" else "#7d95a3"})
    except Exception:
        pass
    return {"rivals": rivals, "moves": moves[:14], "sov": sov, "actions": acts[:10], "campaigns": camps[:20], "apps": apps, "note": ci.get("note"), "generated_at": ci.get("generated_at")}


def anomalies_view() -> List[Dict[str, Any]]:
    an = _anoms(); mode = _client().mode
    out = []
    try:
        from .anomaly.diagnose import diagnose_all
        diags = {d.get("campaign_id"): d for d in (diagnose_all(mode) or {}).get("campaigns", [])} if callable(diagnose_all) else {}
    except Exception:
        diags = {}
    from .anomaly import get_history
    for e in [x for x in (an.get("anomalies") or []) if not x.get("secondary")][:12]:
        d = diags.get(e.get("campaign_id")) or {}
        opts = d.get("options") or []
        first = next((o for o in opts if o.get("do_first")), opts[0] if opts else {})
        cause = ((d.get("likely_causes") or [{}])[0].get("cause") if d.get("likely_causes") else None) or ((d.get("funnel") or {}).get("moved_most") and f"{(d.get('funnel') or {}).get('moved_most')} moved most in the funnel") or e.get("how_we_know") or ""
        try:
            hist = get_history(e.get("campaign_id"), mode, 14)
            trend = [h.get(e.get("metric")) for h in hist if h.get(e.get("metric")) is not None][-14:]
        except Exception:
            trend = []
        urg = e.get("urgency") or "normal"
        sev = {"act_today": ("ACT TODAY", "#ff5c9e"), "watch": ("WATCH", "#ffb84d"), "good_surprise": ("GOOD", "#4dffa8")}.get(urg, ("NORMAL", "#7d95a3"))
        out.append({"campaign": e.get("campaign_name"), "campaign_id": e.get("campaign_id"), "metric": e.get("metric"), "baseline": e.get("baseline"), "value": e.get("value"), "delta": e.get("delta_text") or "", "severity": sev[0], "color": sev[1],
                    "cause": str(cause)[:160], "option": (first.get("action") + (" — " + first.get("how_in_moengage", "")[:120] if first.get("how_in_moengage") else "")) if first else "run campaign_diagnosis", "trend": trend, "n_history": e.get("n_history"), "how_we_know": e.get("how_we_know")})
    return out


def market_view() -> Dict[str, Any]:
    ctx = _ctx()
    tiles = []
    cr = (ctx.get("crypto") or {}).get("assets") or {}
    for sym in ("BTC", "ETH", "SOL"):
        a = cr.get(sym) or next((m for m in (ctx.get("crypto_markets") or []) if m.get("symbol") == sym), None)
        if a:
            tiles.append({"symbol": f"{sym}/USDT", "price": a.get("price"), "change": a.get("chg_24h"), "note": f"7d {a.get('chg_7d', '—')}% · 30d {a.get('chg_30d', '—')}%" if a.get("chg_7d") is not None else "spot ∪ perps", "spark": []})
    for m in (ctx.get("crypto_movers") or [])[:3]:
        tiles.append({"symbol": m.get("symbol"), "price": m.get("price"), "change": m.get("chg_24h"), "note": f"mover · ${(m.get('vol_24h_usd') or 0) / 1e6:.0f}M vol", "spark": []})
    for grp, label in (("equity_movers", "US stock perp"), ("index_movers", "index perp"), ("commodity_movers", "commodity perp")):
        for m in (ctx.get(grp) or [])[:2]:
            tiles.append({"symbol": m.get("name") or m.get("symbol"), "price": m.get("price"), "change": m.get("chg_24h"), "note": f"{label} · 24/7", "spark": []})
    fg = ctx.get("fear_greed") or {}
    if fg.get("value") is not None:
        tiles.append({"symbol": "FEAR & GREED", "price": fg.get("value"), "change": None, "note": fg.get("label", ""), "spark": []})
    hooks = (ctx.get("hooks") or {}).get("hooks") or []
    table = [{"regime": (h.get("angle") or "").upper(), "hook": h.get("trigger"), "cohort": " · ".join((h.get("segments") or [])[:2]), "id": h.get("id"), "sop": h.get("sop"), "c": "#ff5c9e" if h.get("angle") == "suppression" else "#ffb84d" if h.get("angle") == "risk_education" else "#4dffa8"} for h in hooks[:14]]
    from .market import feed
    try:
        layers = {"flash": feed.flash(ctx), "news": feed.biggest_news(ctx), "top_oi": feed.top_oi(ctx), "by_category": feed.top_by_category(ctx), "by_product": feed.by_product(ctx)}
    except Exception as e:
        layers = {"flash": [], "news": [], "top_oi": {}, "by_category": {}, "by_product": [], "feed_error": redact(str(e))}
    return {"tiles": tiles[:12], "hooks": table, "regime": (ctx.get("hooks") or {}).get("regime"), "tier0": ctx.get("tier0"), "narrative": ctx.get("narrative"), "web3": [(r.get("chain"), r.get("symbol"), r.get("vol_24h_usd")) for r in ((ctx.get("web3") or {}).get("trending") or [])[:8]], "generated_at": ctx.get("generated_at"), "cached": ctx.get("cached"), **layers}


def _rec_ice(r: Dict[str, Any]) -> Dict[str, Any]:
    pr = r.get("priority") or 50
    return ice_mod.score(9 if pr >= 90 else 8 if pr >= 75 else 6 if pr >= 60 else 5, 8 if r.get("urgency") in ("now", "today") else 6, 8 if r.get("sop") else 5)


def recommendations(ctx: Optional[Dict[str, Any]] = None, lab: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """The experiment backlog: structural P0 gaps first, then market-driven recommendations; every row carries ICE, tagline, who/what/why, SOP, KPI, benchmark and a stable id."""
    from .market import moneyflow
    from . import structural
    ctx = ctx if ctx is not None else _ctx()
    L = lab or moneyflow.lab(ctx)
    recs = list(L.get("recommendations") or [])
    for r in recs:
        r["ice"] = _rec_ice(r); r["tagline"] = ice_mod.tagline(r["title"], r.get("kpi") or "", r.get("who") or "", r.get("what") or "")
        r["id"] = "market:" + (r.get("sop") or re.sub(r"[^a-z0-9]+", "_", r["title"].lower())[:40])
    try:
        recs = structural.as_recommendations(6) + recs
    except Exception:
        pass
    for r in recs:
        if r.get("structural"):
            r["id"] = "structural:" + str(r.get("id"))
    recs.sort(key=lambda r: (-(1 if r.get("structural") else 0), -((r.get("ice") or {}).get("score") or 0), -(r.get("priority") or 0)))
    try:
        from . import qa as qa_mod
        qa_mod.enrich_recommendations(recs)
    except Exception:
        pass
    # hide what is already on the board (a pending or executed proposal for the same SOP)
    live = " ".join(((p.get("payload") or {}).get("sop_id") or "") + " " + p["title"] for p in approvals.list_proposals(limit=300) if p["status"] in ("pending", "approved", "executed"))
    for r in recs:
        r["on_board"] = bool(r.get("sop")) and r["sop"] in live
    return recs[:14]


def queue_recommendation(rec_id: str, actor: str = "user") -> Dict[str, Any]:
    """Turn a recommendation into approval-gated proposals through its SOP (dry-run first; nothing sends)."""
    rec = next((r for r in recommendations() if r.get("id") == rec_id), None)
    if not rec:
        return {"ok": False, "error": "recommendation not found (the backlog is regenerated from live data; it may have expired)"}
    if not rec.get("sop"):
        return {"ok": False, "error": "this recommendation has no SOP; ask the brain to draft it with propose_campaign"}
    ice = {**{k: rec["ice"][k] for k in ("impact", "confidence", "ease")}, "tagline": rec.get("tagline")}
    dry = sops_mod.run_sop(rec["sop"], created_by=actor, dry_run=True, ice=ice)
    if not dry.get("ok"):
        problems = [c for c in (dry.get("preflight") or {}).get("checks", []) if not c.get("ok")] + [p["brief_check"]["problems"] for p in dry.get("plan", []) if not p.get("internal") and not p["brief_check"]["ok"]]
        return {"ok": False, "error": "pre-flight blocked", "problems": problems, "hint": "usually the cohort segment is missing: approve or create it first, or ask the brain to propose_segment"}
    real = sops_mod.run_sop(rec["sop"], created_by=actor, dry_run=False, ice=ice)
    for pid in real.get("proposal_ids", []):
        approvals.add_comment(pid, f"Queued from the Experiments backlog: {rec['title']}. Why: {rec.get('why')} ICE {rec['ice']['score']} ({rec['ice']['impact']}·{rec['ice']['confidence']}·{rec['ice']['ease']}). KPI {rec.get('kpi')}; benchmark {rec.get('benchmark') or '—'}. Copy is a compliant placeholder unless variants were written; edit before approval.", actor=actor)
    audit("recommendation.queue", {"id": rec_id, "sop": rec["sop"], "proposals": real.get("proposal_ids")}, actor=actor)
    return {"ok": True, "run_id": real.get("run_id"), "proposal_ids": real.get("proposal_ids", []), "sop": rec["sop"]}


GLOBAL_LEADERS = ["binance", "okx", "bitget", "coinbase", "kraken", "bybit"]


def global_leaders(bench: Dict[str, Any], campaigns: List[Dict[str, Any]], exclude: Optional[set] = None) -> List[Dict[str, Any]]:
    """Reference cards for the global leaders: per-category volume, OI, rank and gap to us, top pairs, campaigns detected in 48h and the counter play."""
    cats = bench.get("categories") or {}
    ours = {cat: ((c.get("ours") or {}).get("vol_24h_usd") if not (c.get("ours") or {}).get("unknown") else None) for cat, c in cats.items()}
    out = []
    for vid in GLOBAL_LEADERS:
        if vid in (exclude or set()):
            continue
        per, top_pairs, total = {}, [], 0.0
        name = None
        for cat, c in cats.items():
            for rank, v in enumerate(c.get("venues") or [], 1):
                if v.get("venue") == vid:
                    name = v.get("name"); vol = v.get("vol_24h_usd") or 0; total += vol
                    per[cat] = {"vol_24h_usd": vol, "oi_usd": v.get("oi_usd"), "rank": rank, "gap_x": round(vol / ours[cat], 1) if ours.get(cat) and vol else None, "taker_fee_pct": v.get("taker_fee_pct"), "markets": v.get("markets")}
                    if cat in ("perps", "spot") and not top_pairs:
                        top_pairs = [p.get("symbol") for p in (v.get("top_pairs") or [])[:5]]
        if not name:
            continue
        camps = [c for c in campaigns if c.get("venue") == vid][:4]
        material = next((c for c in camps if c.get("impact") == "MATERIAL" and c.get("counter_sop")), None) or next((c for c in camps if c.get("counter_sop")), None)
        worst = max(((cat, d["gap_x"]) for cat, d in per.items() if d.get("gap_x")), key=lambda kv: kv[1], default=None)
        if material:
            how = (material.get("counter") or "").strip()
            counter = f"counter their {material['type'].replace('_', ' ')} with {material['counter_sop'].replace('sop_', '')}" + (f": {how[:90]}" if how else " (compliant, no matching offer)")
        elif worst:
            counter = f"match their numbers in {worst[0].replace('_', ' / ')} ({worst[1]}× us): pair-level battles on shared top pairs, product asks where liquidity is the gap"
        else:
            counter = "reference only; watch listings and campaign cadence"
        out.append({"id": vid, "name": name, "scope": "global reference", "vol_24h_usd": total, "categories": per, "top_pairs": top_pairs, "campaigns_48h": len([c for c in campaigns if c.get("venue") == vid]), "latest_campaign": ({"title": camps[0].get("title"), "type": camps[0].get("type"), "impact": camps[0].get("impact"), "url": camps[0].get("url")} if camps else None), "counterPlay": counter[:200]})
    out.sort(key=lambda r: -r["vol_24h_usd"])
    return out


def lab_view() -> Dict[str, Any]:
    """Brain Lab: one intel-heavy page — situation, money flow, trader behaviour, recommendations (structural P0 first, ICE-ranked), global events, rivals that matter, campaigns, markets, benchmarks summary, HL vs CEX summary."""
    from .market import moneyflow, feed
    from . import structural
    ctx = _ctx()
    L = moneyflow.lab(ctx)
    L["recommendations"] = recommendations(ctx, L)
    mv = market_view()
    it = intel()
    majors = [r for r in it.get("rivals", []) if not r.get("minor")]; minors = [r for r in it.get("rivals", []) if r.get("minor")]
    bench_summary = {}
    try:
        from .market.benchmarks import benchmarks
        b = benchmarks()
        for cat, c in (b.get("categories") or {}).items():
            bench_summary[cat] = {"leader": (c.get("leader") or {}).get("name"), "india_leader": (c.get("india_leader") or {}).get("name"), "gap_india_x": c.get("gap_to_india_leader_x"), "gap_global_x": c.get("gap_to_leader_x"), "ours": (c.get("ours") or {}).get("vol_24h_usd"), "top_target": next((t for t in (c.get("targets") or []) if t.get("pair")), None)}
    except Exception:
        pass
    ocx = {}
    try:
        from .market.onchain_cex import compare
        o = compare(); ocx = {"hl": o.get("hyperliquid"), "vs": o.get("hl_vs_cex"), "onchain_top": (o.get("onchain") or {}).get("protocols", [])[:5], "per_coin": (o.get("per_coin") or [])[:6]}
    except Exception:
        pass
    try:
        from .market.benchmarks import benchmarks as _bm
        globals_ = global_leaders(_bm(), it.get("campaigns", []), exclude={r["id"] for r in majors})
    except Exception:
        globals_ = []
    return {"generated_at": ctx.get("generated_at"), "situation": {"regime": (ctx.get("hooks") or {}).get("regime"), "tier0": ctx.get("tier0"), "flash": feed.flash(ctx)[:10], "risk_appetite": (L["flow"] or {}).get("risk_appetite")},
            "flow": L["flow"], "reads": L["reads"], "recommendations": L["recommendations"], "events": {"news": feed.biggest_news(ctx, 10), "market_moving": feed.market_moving_news(ctx, 25), "calendar": [e for e in (ctx.get("calendar") or []) if e.get("impact") == "High"][:8], "risk_flags": ((ctx.get("news") or {}).get("risk_flags") or [])[:6]},
            "rivals": {"major": majors, "minor": minors, "global": globals_, "moves": it.get("moves", [])[:12], "campaigns": it.get("campaigns", [])[:12], "apps": it.get("apps", {}), "actions": it.get("actions", [])[:8], "sov": it.get("sov", [])},
            "benchmarks": bench_summary, "hl_vs_cex": ocx, "market": {k: mv.get(k) for k in ("tiles", "hooks", "regime", "narrative", "top_oi", "by_category", "by_product")},
            "note": "internal intelligence from free public sources; venue data is never named in user copy"}


def atlas_view() -> Dict[str, Any]:
    """Cohort Atlas: decoded segment families, product cohorts and their treatment, peace index, north star and limits, studies, open data requests."""
    from . import segments
    from .products import PRODUCTS
    camps = []
    try:
        camps = _client().get_campaigns()
    except Exception:
        pass
    regime = (_ctx().get("hooks") or {}).get("regime")
    try:
        st = segments.study(camps)
    except Exception as e:
        st = {"families": [], "segments": 0, "unknown_tokens": [], "studies": [], "by_product": [], "error": redact(str(e))[:200]}
    try:
        pi = guardrails.peace_index(camps, regime)
    except Exception as e:
        pi = {"families": [], "counts": {}, "error": redact(str(e))[:200]}
    stage_of = {r["family"]: r.get("stage") for r in (pi.get("families") or [])}
    fams = []
    for f in st.get("families") or []:
        fams.append({"family": f["family"], "meaning": f.get("meaning"), "products": f.get("products"), "stage": stage_of.get(f["family"]), "reach": f.get("reach"), "latest": {"name": (f.get("latest") or {}).get("name"), "version": (f.get("latest") or {}).get("version"), "first_seen": (f.get("latest") or {}).get("first_seen")},
                     "versions": [{"version": v.get("version"), "name": v.get("name")} for v in (f.get("versions") or [])][-6:], "performance": f.get("performance"), "delta": f.get("delta"), "flags": f.get("flags"), "campaigns": len(f.get("campaigns_attached") or [])})
    byp = {b["product"]: b for b in (st.get("by_product") or [])}
    products = []
    for pid, p in PRODUCTS.items():
        b = byp.get(pid) or {}
        products.append({"id": pid, "name": p["name"], "lens": p["lens"], "never": p["never"], "cadence": p["cadence"], "cross_sell": p["cross_sell"], "families": len(b.get("families") or []), "reach": b.get("reach"), "campaigns": b.get("campaigns"), "click_rate": b.get("click_rate")})
    try:
        reqs = [r for r in datarequests.list_requests(status="open", limit=20)]
    except Exception:
        reqs = []
    return {"segments": st.get("segments", 0), "families": fams, "unknown_tokens": st.get("unknown_tokens") or [], "studies": st.get("studies") or [], "products": products,
            "peace": {"counts": pi.get("counts"), "families": pi.get("families"), "multiplier": pi.get("multiplier"), "method": pi.get("method")}, "north_star": guardrails.north_star(), "limits": guardrails.limits(), "regime": regime, "requests": reqs, "error": st.get("error") or pi.get("error")}


def trace(limit: int = 40) -> List[Dict[str, str]]:
    from .security.audit import tail
    out = []
    for a in tail(limit):
        ev = str(a.get("event", "")); d = a.get("detail") or {}
        level = "alert" if any(k in ev for k in ("failed", "rejected", "breach", "rolled_back")) else "ok" if any(k in ev for k in ("executed", "approved", "merged", "drafted", "created")) else "warn" if any(k in ev for k in ("expired", "hold", "simulate")) else "info"
        mod, _, verb = ev.partition(".")
        bits = []
        for k in ("id", "kind", "title", "mission", "outcome", "status", "sop", "segment", "key", "actions", "run_id", "proposals", "reason"):
            if k in d and d[k] not in (None, "", [], {}):
                v = d[k]; bits.append(f"{k}={json.dumps(v, default=str)[:60]}" if not isinstance(v, (str, int)) else f"{k}={str(v)[:60]}")
        out.append({"ts": str(a.get("ts", ""))[11:19] or str(a.get("ts", ""))[:19], "text": f"{mod}/{verb or ev} " + " · ".join(bits[:4]), "level": level, "actor": a.get("actor")})
    return out
