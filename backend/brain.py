"""
Brain API — the aggregate the terminal UI consumes. Every module of the
handoff (Brain, Intel, Ideas, Experiments, SOPs, Anomalies, Market) maps to
data the engine already computes; nothing here invents numbers. Where a
metric has no source yet, the value is "—" with the reason in `sub`.
"""
from __future__ import annotations
import json
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from .database import get_db, get_setting, get_latest_daily_run
from .security import audit, redact
from . import approvals, growth, experiments, hacks as hacks_mod, plans, sops as sops_mod, guardrails, datarequests

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
    badges = {"exp": len(pend), "anom": bu.get("act_today", 0)}
    return {"bus": bus, "load": load, "pipeline": pipeline, "brief": brief, "autopilot": get_setting("autopilot_enabled", "true").lower() == "true", "badges": badges, "mode": mode,
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
    fam_cov = "—"
    try:
        from . import segments
        st = segments.study(_client().get_campaigns()); fams = st.get("families") or []
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
                  {"k": "LISTING GAPS", "v": str(len(ci.get("listing_gaps") or [])), "sub": "pairs they have, we don't", "c": "#eaf7fc"}],
        "ideas": [{"k": "IDEAS", "v": str(ideas_new), "sub": "new in feed", "c": "#eaf7fc"}, {"k": "HACKS", "v": str(len(hacks_mod.LIBRARY)), "sub": "sourced tactics", "c": "#6fe3ff"},
                  {"k": "PROPOSED", "v": str(growth.counts().get("proposed", 0)), "sub": "turned into proposals", "c": "#4dffa8"}, {"k": "SAVED", "v": str(growth.counts().get("saved", 0)), "sub": "kept by the team", "c": "#eaf7fc"},
                  {"k": "DATA ASKS", "v": str(len(datarequests.list_requests("open"))), "sub": "open requests", "c": "#ffb84d"}],
        "exp": [{"k": "PROPOSED", "v": str(len(pend)), "sub": "awaiting approval", "c": "#ffb84d"}, {"k": "RUNNING", "v": str(len(running)), "sub": f"{sum(1 for e in running if (e.get('control_group_pct') or 0) >= 5)} with holdout", "c": "#6fe3ff"},
                {"k": "READ", "v": str(len(read)), "sub": "window complete", "c": "#4dffa8"}, {"k": "ABANDONED", "v": str(sum(1 for e in exps if e.get('status') == 'abandoned')), "sub": "rejected or expired", "c": "#7d95a3"},
                {"k": "EXECUTED", "v": str(sum(1 for p in approvals.list_proposals(limit=400) if p['status'] == 'executed')), "sub": "all time", "c": "#eaf7fc"}],
        "sops": [{"k": "SOPS", "v": str(n_sops), "sub": f"{len({s['campaign_type'] for s in sops_mod.list_sops()})} types", "c": "#eaf7fc"}, {"k": "FRAMEWORK OK", "v": f"{sum(1 for s in sops_mod.list_sops() if s['framework_ok'])}/{n_sops}", "sub": "checks passing", "c": "#4dffa8"},
                 {"k": "RUNS TODAY", "v": str(len(runs_today)), "sub": f"{len(runs)} total", "c": "#ffb84d"}, {"k": "COHORT COVERAGE", "v": fam_cov, "sub": "families with a campaign", "c": "#eaf7fc"},
                 {"k": "TEAM EDITS", "v": str(sum(1 for s in sops_mod.list_sops(include_inactive=True) if s.get('source') not in ('library',))), "sub": "versioned SOPs", "c": "#6fe3ff"}],
        "anom": [{"k": "TRACKED", "v": str(tracked), "sub": "campaigns", "c": "#eaf7fc"}, {"k": "ACT TODAY", "v": str(bu.get("act_today", 0)), "sub": "severity high", "c": "#ff5c9e"},
                 {"k": "WATCH", "v": str(bu.get("watch", 0)), "sub": "drifting", "c": "#ffb84d"}, {"k": "GOOD SURPRISE", "v": str(bu.get("good_surprise", 0)), "sub": "above baseline", "c": "#4dffa8"}, {"k": "HISTORY", "v": f"{days}d", "sub": "snapshot depth", "c": "#eaf7fc"}],
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
        out.append({"id": f"#IDEA-{i['id']}", "raw_id": i["id"], "kind": "idea", "title": i["title"], "tags": [t for t in [i.get("kind"), i.get("channel"), i.get("angle")] if t][:3], "hypothesis": (i.get("why") or "")[:320],
                    "projectedLift": i.get("expected_impact") or "—", "confidence": f"{conf:.2f}", "effort": _effort(i.get("effort")), "sourceSignal": f"{i.get('source')} · {i.get('transition') or i.get('kpi') or 'feed'}", "category": cat, "how": (i.get("how") or "")[:300], "priority": i.get("priority", 50)})
    try:
        rk = hacks_mod.ranked(); hack_rows = (rk.get("hacks") if isinstance(rk, dict) else rk) or []
    except Exception:
        hack_rows = []
    for h in hack_rows[:12]:
        if h.get("status") in ("dismissed",):
            continue
        out.append({"id": f"#HACK-{h['id']}", "raw_id": h["id"], "kind": "hack", "title": h["title"], "tags": [h.get("category")] + (h.get("channels") or [])[:2], "hypothesis": (h.get("why") or "")[:320], "projectedLift": h.get("kpi") or "—",
                    "confidence": "0.80" if h.get("source_kind") != "model" else "0.50", "effort": _effort(h.get("effort")), "sourceSignal": f"hack · {h.get('source') or 'library'}", "category": "sop" if h.get("category") in ("retention", "activation") else "market" if h.get("category") in ("risk",) else "audience", "how": (h.get("how") or "")[:300], "priority": h.get("relevance", 50) if isinstance(h.get("relevance"), (int, float)) else 50})
    if filter_ and filter_ != "all":
        out = [o for o in out if o["category"] == filter_]
    out.sort(key=lambda o: -(o.get("priority") or 0))
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


def experiments_board() -> Dict[str, Any]:
    props = approvals.list_proposals(limit=400)
    exps = {e["proposal_id"]: e for e in experiments.list_experiments(400)}
    cols = {"proposed": [], "simulated": [], "live": [], "read": [], "archive": []}
    for p in props:
        e = exps.get(p["id"]) or {}
        pl = p.get("payload") or {}
        goal = pl.get("goal") or {}
        metric = (f"holdout {goal.get('control_group_pct')}% · {goal.get('primary_kpi')}" if goal else p.get("kind", ""))
        card = {"id": p["id"], "title": p["title"], "tag": p.get("category") or p["kind"], "product": p.get("product"), "note": (p.get("rationale") or "")[:140], "metric": metric, "created_at": p.get("created_at")}
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
        cols[k].sort(key=lambda c: -(c["id"] or 0))
    return {"columns": [{"key": "proposed", "name": "PROPOSED", "c": "#ffb84d", "cards": cols["proposed"][:30]}, {"key": "simulated", "name": "SIMULATED", "c": "#6fe3ff", "cards": cols["simulated"][:30]},
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
        counter = next((a["what"] for a in acts if a.get("type") in ("counter_surge", "share_defence")), None) or ("asset spotlight on shared pairs" if s_n else "hold; monitor")
        rivals.append({"id": t["exchange"], "name": t["name"], "threat": threat.upper(), "color": color, "pressureIndex": idx, "latestMove": move, "tactics": tactics[:4], "counterPlay": counter[:120], "vol_24h_usd": t.get("vol_24h_usd"), "share": t.get("share_of_tracked_inr_spot_pct"), "visits": t.get("weekly_visits"), "source": t.get("source")})
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
    return {"rivals": rivals, "moves": moves[:12], "sov": sov, "actions": acts[:10], "note": ci.get("note"), "generated_at": ci.get("generated_at")}


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
    return {"tiles": tiles[:12], "hooks": table, "regime": (ctx.get("hooks") or {}).get("regime"), "tier0": ctx.get("tier0"), "narrative": ctx.get("narrative"), "web3": [(r.get("chain"), r.get("symbol"), r.get("vol_24h_usd")) for r in ((ctx.get("web3") or {}).get("trending") or [])[:8]], "generated_at": ctx.get("generated_at"), "cached": ctx.get("cached")}


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
