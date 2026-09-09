"""
Autopilot: the engine does the marketer's first pass on its own.

After every refresh it runs a small set of *missions*. Each mission looks at
the data, decides whether there is something worth doing, and asks the agent
(bulk model tier — free on OpenRouter) to produce a complete, approval-ready
proposal: audience as structured criteria, copy variants, holdout, KPI,
window, kill criteria, suppressions. Nothing is sent; the human sees an
*action queue* on the Overview and approves or rejects with one click.

Missions (in priority order, budgeted per run):
  1. stop_the_bleed   – Act-today anomalies → pause / audience clean / variant test
  2. close_the_gap    – top uncovered lifecycle transition → standing campaign draft
  3. ride_the_market  – best allowed market hook today → market-linked draft (TTL)
  4. best_idea        – highest-priority new growth idea → proposal
  5. protect          – risk headlines / stress regime → suppression proposal (pause promos)
Every mission is idempotent per day: a mission that already produced a pending
proposal for the same target is skipped.
"""
from __future__ import annotations
import json
from datetime import date
from typing import Any, Dict, List, Optional

from .database import get_db, get_setting
from .security import redact, audit
from . import approvals


def init_autopilot_tables() -> None:
    conn = get_db()
    conn.execute("""CREATE TABLE IF NOT EXISTS autopilot_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, run_date TEXT,
        mission TEXT, target TEXT, outcome TEXT, proposal_ids TEXT, model TEXT, detail TEXT)""")
    conn.commit(); conn.close()


def _done_today(mission: str, target: str) -> bool:
    init_autopilot_tables()
    conn = get_db()
    r = conn.execute("SELECT 1 FROM autopilot_runs WHERE run_date=? AND mission=? AND target=? AND outcome IN ('proposed','skipped_pending')", (date.today().isoformat(), mission, target)).fetchone()
    conn.close()
    return bool(r)


def _log(mission: str, target: str, outcome: str, proposal_ids: List[int], model: Optional[str], detail: str) -> None:
    conn = get_db()
    conn.execute("INSERT INTO autopilot_runs (run_date, mission, target, outcome, proposal_ids, model, detail) VALUES (?,?,?,?,?,?,?)",
                 (date.today().isoformat(), mission, target, outcome, json.dumps(proposal_ids), model, redact(detail)[:1000]))
    conn.commit(); conn.close()


def _pending_for(target: str) -> bool:
    return any(target.lower() in (p["title"] or "").lower() or target.lower() in json.dumps(p.get("payload") or {}).lower() for p in approvals.list_proposals(status="pending", limit=200))


def _agent(tier_note: str):
    from .llm.agent import MarketerAgent
    from .llm.provider import LLMClient, llm_settings, bulk_models
    cfg = llm_settings()
    if not (cfg["api_key"] or cfg["provider"] == "claude_cli"):
        return None
    # bulk tier for drafting: first candidate that answers becomes the agent's model
    for cand in bulk_models(cfg):
        try:
            client = LLMClient({**cfg, "model": cand})
            client.chat([{"role": "user", "content": "Reply with the single word: ready"}], tools=None, max_tokens=5)
            return MarketerAgent(client, purpose="autopilot")
        except Exception:
            continue
    return MarketerAgent(purpose="autopilot")


def _run_mission(agent, mission: str, target: str, prompt: str) -> Dict[str, Any]:
    before = {p["id"] for p in approvals.list_proposals(limit=300)}
    out = agent.chat(prompt, history=[], persist=False)
    after = {p["id"] for p in approvals.list_proposals(limit=300)}
    new_ids = sorted(after - before)
    outcome = "proposed" if new_ids else "no_action"
    _log(mission, target, outcome, new_ids, out.get("model"), out.get("reply", "")[:600])
    return {"mission": mission, "target": target, "outcome": outcome, "proposal_ids": new_ids, "model": out.get("model"), "summary": (out.get("reply") or "")[:400]}


BRIEF_RULES = ("Rules: run campaign_brief_check and experiment_plan before proposing; every campaign proposal needs the full goal brief with holdout >= 20%, "
               "exclusions (loss-dormant, friction-dormant, KYC pending, open ticket), a frequency cap, and for market-linked sends a TTL <= 4h. "
               "Copy: title <= 60 chars, body <= 140 chars, fact + tool, no forecasts, no buy/sell instructions, add the required risk disclaimer where relevant. "
               "Record every idea with record_ideas. Finish with a 3-line summary of what was queued.")


def run(max_actions: int = 3, force: bool = False) -> Dict[str, Any]:
    if get_setting("autopilot_enabled", "true").lower() != "true" and not force:
        return {"enabled": False, "actions": []}
    from .llm.tools import anomaly_report, clm_program_audit, market_campaign_hooks
    from . import growth
    results: List[Dict[str, Any]] = []
    agent = _agent("bulk")
    if agent is None:
        return {"enabled": True, "actions": [], "note": "no model configured; autopilot needs a model (free bulk tier is enough)"}
    budget = max_actions

    # 0. self-heal: engine errors become code-change proposals
    try:
        if budget > 0 and get_setting("devagent_enabled", "true").lower() == "true":
            from .selfheal import health_report
            hr = health_report(run_tests=False)
            fixes = [f for f in hr.get("fix_requests", []) if f.get("signature") != "tests"]
            if fixes:
                target = "errors:" + ",".join(sorted(f["signature"] for f in fixes[:4]))
                if not _done_today("self_heal", target) and not _pending_for("Fix "):
                    prompt = (f"MISSION self_heal. The engine reports status '{hr['status']}' with {len(fixes)} fix request(s). Call self_diagnose, then for the top 2 requests call propose_code_change with the exact fix_request text "
                              f"(scope backend, draft_now true). Do not change behaviour beyond the fix; each change must add a regression test. Summarise what was filed and what the operator should approve.")
                    results.append(_run_mission(agent, "self_heal", target, prompt)); budget -= 1
    except Exception as ex:
        results.append({"mission": "self_heal", "outcome": "error", "detail": redact(str(ex))[:200]})

    # 1. stop the bleed
    try:
        rep = anomaly_report()
        acts = [e for e in rep.get("anomalies", []) if e.get("urgency") == "act_today" and not e.get("secondary")]
        for e in acts[:2]:
            if budget <= 0:
                break
            target = f"{e.get('campaign_name')}:{e.get('metric')}"
            if _done_today("stop_the_bleed", target) or _pending_for(str(e.get("campaign_name"))):
                _log("stop_the_bleed", target, "skipped_pending", [], None, "pending proposal exists"); continue
            prompt = (f"MISSION stop_the_bleed. Campaign '{e.get('campaign_name')}' (id {e.get('campaign_id')}): {e.get('headline')}. {e.get('how_we_know')} "
                      f"Run campaign_diagnosis for it. If delivery is collapsing (< 85%), propose_pause_campaign with the rationale and, separately, propose_segment for a cleaned audience. "
                      f"If engagement fell with delivery stable, propose_campaign as a 2-variant creative test on the same audience with a 20% holdout. {BRIEF_RULES}")
            results.append(_run_mission(agent, "stop_the_bleed", target, prompt)); budget -= 1
    except Exception as ex:
        results.append({"mission": "stop_the_bleed", "outcome": "error", "detail": redact(str(ex))[:200]})

    # 2. close the gap
    try:
        if budget > 0:
            pa = clm_program_audit()
            gaps = pa.get("uncovered_transitions") or []
            if gaps:
                target = gaps[0]
                if not _done_today("close_the_gap", target) and not _pending_for(target.split(" → ")[0]):
                    tid = next((k for k, v in pa["coverage"].items() if v["transition"] == target), "")
                    prompt = (f"MISSION close_the_gap. The programme has no standing campaign for the transition '{target}' (stage id {tid}). "
                              f"Use campaign_taxonomy to pick the right cohort facets and growth_hacks for the best-fit tactic, then propose_segment (structured criteria) and propose_campaign "
                              f"(triggered, not blast; channel appropriate to the stage; 2 copy variants). {BRIEF_RULES}")
                    results.append(_run_mission(agent, "close_the_gap", target, prompt)); budget -= 1
                else:
                    _log("close_the_gap", target, "skipped_pending", [], None, "already handled today")
    except Exception as ex:
        results.append({"mission": "close_the_gap", "outcome": "error", "detail": redact(str(ex))[:200]})

    # 5. protect (before market plays so suppression wins on risk days)
    try:
        if budget > 0:
            hk = market_campaign_hooks()
            hooks = hk.get("hooks") or []
            risk = next((h for h in hooks if h.get("angle") == "suppression"), None)
            regime = hk.get("regime")
            if risk or regime in ("capitulation", "high_volatility_down"):
                target = f"protect:{date.today().isoformat()}"
                if not _done_today("protect", target):
                    prompt = (f"MISSION protect. Today: {(risk or {}).get('trigger', 'stress regime ' + str(regime))}. Identify acquisition/upsell/FOMO campaigns from list_campaigns + campaign_taxonomy "
                              f"and propose_pause_campaign for the top 1-2 that should not run today, with rationale citing the headline or regime. Also record_ideas for a service/safety in-app message. {BRIEF_RULES}")
                    results.append(_run_mission(agent, "protect", target, prompt)); budget -= 1
    except Exception as ex:
        results.append({"mission": "protect", "outcome": "error", "detail": redact(str(ex))[:200]})

    # 3. ride the market
    try:
        if budget > 0:
            hk = market_campaign_hooks()
            hooks = [h for h in (hk.get("hooks") or []) if h.get("angle") not in ("suppression",)]
            if hooks and not any(x["mission"] == "protect" and x.get("outcome") == "proposed" for x in results):
                h = hooks[0]; target = h["id"]
                if not _done_today("ride_the_market", target):
                    prompt = (f"MISSION ride_the_market. Hook {h['id']}: {h['trigger']}. Segments: {h.get('segments')}. Channel {h.get('channel')}, angle {h.get('angle')}, timing {h.get('timing')}, "
                              f"guardrails {h.get('guardrails')}. Copy direction: {h.get('copy_direction')}. propose_segment + propose_campaign (market_hook_id='{h['id']}', ttl_hours 4). {BRIEF_RULES}")
                    results.append(_run_mission(agent, "ride_the_market", target, prompt)); budget -= 1
    except Exception as ex:
        results.append({"mission": "ride_the_market", "outcome": "error", "detail": redact(str(ex))[:200]})

    # 6. new cohort uploads → study + re-point (monthly)
    try:
        if budget > 0:
            from . import segments as _seg
            reg = _seg.registry(limit=300)
            fresh = [r for r in reg if r.get("first_seen") and str(r["first_seen"])[:10] >= (date.today().replace(day=1)).isoformat()]
            fams = sorted({r["family"] for r in fresh})
            if fams:
                target = f"cohorts:{date.today().strftime('%Y-%m')}:{len(fams)}"
                if not _done_today("study_new_cohorts", target):
                    prompt = (f"MISSION study_new_cohorts. New segment versions arrived this month for families {fams[:12]}. Run segment_study; for each new family: state reach vs previous version, "
                              f"which standing campaigns should be re-pointed, and any undefined codes (ask, do not guess). Then run_sop('sop_monthly_cohort_upload', dry_run=True) and, for the two most valuable families, "
                              f"run the matching SOP (list_sops) with copy per step. {BRIEF_RULES}")
                    results.append(_run_mission(agent, "study_new_cohorts", target, prompt)); budget -= 1
    except Exception as ex:
        results.append({"mission": "study_new_cohorts", "outcome": "error", "detail": redact(str(ex))[:200]})

    # 7. monthly flight plans (first 5 days of the month, or when none exist)
    try:
        if budget > 0:
            from . import plans as _plans
            month = date.today().strftime("%Y-%m")
            ms = _plans.month_summary(month)
            if (date.today().day <= 5 or ms["plans"] == 0) and not _done_today("monthly_flight_plans", month):
                prompt = (f"MISSION monthly_flight_plans for {month}. Read north_star, comms_limits, clm_program_audit, segment_study, experiment_readouts and flight_plans(month='{month}'). "
                          f"Transitions without a plan: {ms['transitions_without_plan']}. Write up to 3 Flight Plans (write_flight_plan) for the highest-value opportunities this month, each tied to an SOP where one fits, "
                          f"with month_on_month grounded in last month's readouts and a candid whats_possible section. Do not queue proposals; plans first. {BRIEF_RULES}")
                results.append(_run_mission(agent, "monthly_flight_plans", month, prompt)); budget -= 1
    except Exception as ex:
        results.append({"mission": "monthly_flight_plans", "outcome": "error", "detail": redact(str(ex))[:200]})

    # 4. best idea
    try:
        if budget > 0:
            ideas = [i for i in growth.list_ideas(status="new", limit=10) if i["kind"] in ("trending_campaign", "growth_hack")]
            if ideas:
                i = ideas[0]; target = f"idea:{i['id']}"
                if not _done_today("best_idea", target):
                    prompt = (f"MISSION best_idea. Idea #{i['id']}: {i['title']}. Why: {i['why']} How: {i['how']} Segment {i.get('segment')}, channel {i.get('channel')}, KPI {i.get('kpi')}, transition {i.get('transition')}. "
                              f"Turn it into propose_segment + propose_campaign. {BRIEF_RULES}")
                    res = _run_mission(agent, "best_idea", target, prompt); results.append(res); budget -= 1
                    if res.get("proposal_ids"):
                        growth.set_status(i["id"], "proposed")
    except Exception as ex:
        results.append({"mission": "best_idea", "outcome": "error", "detail": redact(str(ex))[:200]})

    audit("autopilot.run", {"actions": [(r.get("mission"), r.get("outcome"), r.get("proposal_ids")) for r in results]}, actor="autopilot")
    return {"enabled": True, "actions": results, "pending_total": len(approvals.list_proposals(status="pending"))}


def recent(limit: int = 30) -> List[Dict[str, Any]]:
    init_autopilot_tables()
    conn = get_db()
    rows = [dict(r) for r in conn.execute("SELECT * FROM autopilot_runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]
    conn.close()
    for r in rows:
        try:
            r["proposal_ids"] = json.loads(r["proposal_ids"] or "[]")
        except Exception:
            r["proposal_ids"] = []
    return rows


def action_queue() -> Dict[str, Any]:
    """What a human can approve right now, grouped by mission, newest first."""
    pend = approvals.list_proposals(status="pending", limit=100)
    runs = recent(100)
    by_pid: Dict[int, str] = {}
    for r in runs:
        for pid in r["proposal_ids"]:
            by_pid.setdefault(pid, r["mission"])
    items = []
    for p in pend:
        items.append({"id": p["id"], "kind": p["kind"], "title": p["title"], "rationale": (p.get("rationale") or "")[:300], "risk": p.get("risk"),
                      "mission": by_pid.get(p["id"], "agent" if p.get("created_by") == "agent" else "manual"), "created_at": p["created_at"],
                      "goal": (p.get("payload") or {}).get("goal"), "preview_mode": (p.get("preview") or {}).get("mode"), "blocked": (p.get("preview") or {}).get("blocked")})
    return {"pending": items, "runs_today": [r for r in runs if r["run_date"] == date.today().isoformat()][:20]}
