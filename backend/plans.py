"""
Flight Plans — the campaign requirement document the agent writes for every
programme it proposes: objective tied to the north star, cohort and sizing,
the journey (sequence), copy, experiment design, limits/peace check, month
timeline, risks and compliance, checks, and an honest "what's possible now vs
what needs data or API access" section. Stored as structured JSON + rendered
Markdown; every plan is also an idea in the growth feed so it is never lost.
Month summaries roll plans up for month-on-month optimisation.
"""
from __future__ import annotations
import json
import re
from datetime import date
from typing import Any, Dict, List, Optional

from .database import get_db
from .security import audit, redact

REQUIRED = ("title", "month", "objective", "transition", "cohort_family", "sequence", "kpi")


def init_plan_tables() -> None:
    conn = get_db()
    conn.execute("""CREATE TABLE IF NOT EXISTS flight_plans (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, month TEXT, transition TEXT, cohort_family TEXT, sop_id TEXT,
                    status TEXT DEFAULT 'draft', spec_json TEXT, doc_md TEXT, created_by TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
    conn.commit(); conn.close()


def _md_list(items: Any) -> str:
    if not items:
        return "- (none)\n"
    if isinstance(items, dict):
        return "".join(f"- **{k}**: {v}\n" for k, v in items.items())
    return "".join(f"- {x}\n" for x in items)


def render(spec: Dict[str, Any], limits_check: Optional[Dict[str, Any]] = None) -> str:
    k = spec.get("kpi") or {}
    aud = spec.get("audience") or {}
    ex = spec.get("experiment") or {}
    mom = spec.get("month_on_month") or {}
    wp = spec.get("whats_possible") or {}
    seq = spec.get("sequence") or []
    lines = [f"# Flight Plan · {spec.get('title')}", "",
             f"**Month:** {spec.get('month')} · **Transition:** {spec.get('transition')} · **Cohort family:** `{spec.get('cohort_family')}`" + (f" · **SOP:** `{spec.get('sop_id')}`" if spec.get("sop_id") else ""), "",
             "## 1. Objective and north-star link", spec.get("objective", ""), "", f"*North star:* {spec.get('north_star_link') or 'weekly_active_weeks_4w lift vs holdout at the lowest message load'}", "",
             "## 2. Cohort and sizing", _md_list({"definition": aud.get("definition") or aud.get("description") or "see cohort family", "version": aud.get("version") or "latest upload", "reach": aud.get("reach") or "unknown (verify in dashboard)",
                                                   "exclusions": ", ".join(aud.get("exclusions") or []) or "standard set", "jurisdictions excluded": ", ".join(aud.get("jurisdictions_excluded") or []) or "none"}),
             "## 3. Journey (sequence)", "| day | channel | purpose | condition | send (IST) |", "|---|---|---|---|---|"]
    for s in seq:
        lines.append(f"| D{s.get('day', 0)} | {s.get('channel')} | {s.get('purpose')} | {s.get('condition') or '—'} | {s.get('send_time_ist') or '—'} |")
    lines += ["", "## 4. Copy (variants per step)"]
    for i, s in enumerate(seq):
        vs = s.get("variants") or []
        if vs:
            lines.append(f"**Step {i} · {s.get('channel')}**")
            for v in vs:
                lines.append(f"- *{v.get('label', 'A')}*: **{v.get('title', '')}** — {v.get('body', '')} → _{v.get('cta', '')}_")
    lines += ["", "## 5. KPI and experiment design", _md_list({"primary KPI": k.get("primary"), "target": k.get("target"), "baseline": k.get("baseline") or "from campaign history", "guardrail": k.get("guardrail"), "holdout": f"{k.get('holdout_pct', 20)}%",
                                                              "measurement window": f"{k.get('window_days', 14)} days", "sample size / days to read": ex.get("sample_size") or ex.get("days_to_read") or "run experiment_plan", "kill criteria": "; ".join(ex.get("kill_criteria") or spec.get("kill_criteria") or [])}),
              "## 6. Communication limits and peace check"]
    if limits_check:
        lines.append(_md_list({"touches this plan adds per user/week": limits_check.get("plan_touches_per_week"), "cohort touches already planned/observed": limits_check.get("existing_touches_per_week"),
                               "effective cap (regime/stage)": limits_check.get("cap_total"), "status": limits_check.get("status"), "notes": "; ".join(limits_check.get("notes") or []) or "—"}))
    else:
        lines.append("- run peace_index for the cohort before approval\n")
    lines += ["## 7. Month timeline", _md_list([f"{t.get('date')}: {t.get('what')}" for t in (spec.get("timeline") or [])] or ["D0 launch; daily kill-rule checks; readout at window end"]),
              "## 8. Month-on-month", _md_list({"last month": mom.get("last_month_result") or "no prior run", "what changes": mom.get("change") or "—", "expected gain": mom.get("expected_gain") or "—"}),
              "## 9. Risks and compliance", _md_list(spec.get("risks") or []), _md_list(spec.get("compliance") or ["ASCI VDA disclaimer on email/WhatsApp/in-app; no forecasts; derivatives content education-only for India; UK/US excluded from derivatives and incentives"]),
              "## 10. Checks", _md_list((spec.get("checks") or {"preflight": ["framework", "segment_exists", "exclusions_present", "brief_per_step", "compliance", "limits"], "midflight": ["kill_criteria_daily", "peace_index"], "postflight": ["readout_with_ci", "lesson_to_feed"]})),
              "## 11. What's possible now vs what is needed", "**Now:**", _md_list(wp.get("now") or ["everything above using existing segments and campaign APIs"]), "**Needs data:**", _md_list(wp.get("needs_data") or []), "**Needs API/dashboard access:**", _md_list(wp.get("needs_api") or [])]
    return "\n".join(lines)


def create_plan(spec: Dict[str, Any], author: str = "agent") -> Dict[str, Any]:
    init_plan_tables()
    missing = [k for k in REQUIRED if not spec.get(k)]
    if missing:
        return {"ok": False, "error": f"flight plan missing: {', '.join(missing)}"}
    if not re.fullmatch(r"20\d{2}-(0[1-9]|1[0-2])", str(spec.get("month"))):
        return {"ok": False, "error": "month must be YYYY-MM"}
    limits_check = None
    try:
        from . import guardrails
        from .market.context import _latest
        regime = (((_latest(6 * 3600) or {}).get("hooks") or {}).get("regime"))
        stage = guardrails._stage_for_family(spec["cohort_family"])
        eff = guardrails.effective_limits(regime, stage)
        weeks = max(1, -(-int(max([s.get("day", 0) for s in spec["sequence"]] + [0]) + 1) // 7))
        plan_touches = round(len([s for s in spec["sequence"] if not str(s.get("purpose", "")).startswith("(internal)")]) / weeks, 2)
        existing = guardrails.planned_touches(spec["cohort_family"])
        ex_total = sum(existing.values())
        status = "over_cap" if plan_touches + ex_total > eff["total_per_week"] else "in_band"
        limits_check = {"plan_touches_per_week": plan_touches, "existing_touches_per_week": ex_total, "cap_total": eff["total_per_week"], "status": status,
                        "notes": [f"regime {regime} multiplier {eff['multiplier']}", f"stage {stage or 'default'}"] + (["reduce steps or lengthen the sequence before approval"] if status == "over_cap" else [])}
    except Exception as e:
        limits_check = {"status": "unknown", "notes": [redact(str(e))[:120]]}
    doc = render(spec, limits_check)
    conn = get_db()
    cur = conn.execute("INSERT INTO flight_plans (title, month, transition, cohort_family, sop_id, status, spec_json, doc_md, created_by) VALUES (?,?,?,?,?,?,?,?,?)",
                       (spec["title"][:160], spec["month"], spec["transition"], spec["cohort_family"], spec.get("sop_id"), "draft", json.dumps({**spec, "limits_check": limits_check}, default=str), doc, author))
    pid = cur.lastrowid; conn.commit(); conn.close()
    try:
        from . import growth
        k = spec.get("kpi") or {}
        growth.upsert_ideas([{"kind": "trending_campaign", "title": f"Flight plan: {spec['title']}", "why": spec.get("objective", "")[:600], "how": f"Flight plan #{pid} ({spec['month']}), {len(spec['sequence'])} steps, KPI {k.get('primary')} → {k.get('target')}; open in SOPs → Flight plans.",
                              "segment": spec["cohort_family"], "kpi": k.get("primary") or "", "transition": spec["transition"], "priority": 70, "effort": "medium", "expected_impact": k.get("target") or "", "data": {"flight_plan_id": pid, "limits": limits_check}}], "agent" if author == "agent" else "rules")
    except Exception:
        pass
    audit("flight_plan.created", {"id": pid, "title": spec["title"], "month": spec["month"], "limits": (limits_check or {}).get("status")}, actor=author)
    return {"ok": True, "id": pid, "limits_check": limits_check, "doc_md": doc}


def list_plans(month: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
    init_plan_tables()
    conn = get_db()
    q = "SELECT id, title, month, transition, cohort_family, sop_id, status, created_by, created_at, spec_json FROM flight_plans" + (" WHERE month=?" if month else "") + " ORDER BY id DESC LIMIT ?"
    rows = [dict(r) for r in conn.execute(q, ((month, limit) if month else (limit,))).fetchall()]
    conn.close()
    for r in rows:
        try:
            sp = json.loads(r.pop("spec_json") or "{}")
            r["kpi"] = (sp.get("kpi") or {}).get("primary"); r["target"] = (sp.get("kpi") or {}).get("target"); r["steps"] = len(sp.get("sequence") or []); r["limits_status"] = (sp.get("limits_check") or {}).get("status")
        except Exception:
            r.pop("spec_json", None)
    return rows


def get_plan(pid: int) -> Optional[Dict[str, Any]]:
    init_plan_tables()
    conn = get_db()
    r = conn.execute("SELECT * FROM flight_plans WHERE id=?", (pid,)).fetchone()
    conn.close()
    if not r:
        return None
    d = dict(r); d["spec"] = json.loads(d.pop("spec_json") or "{}")
    return d


def set_status(pid: int, status: str) -> None:
    conn = get_db(); conn.execute("UPDATE flight_plans SET status=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (status, pid)); conn.commit(); conn.close()


def month_summary(month: Optional[str] = None) -> Dict[str, Any]:
    month = month or date.today().strftime("%Y-%m")
    plans = list_plans(month, limit=100)
    by_tr: Dict[str, int] = {}
    for p in plans:
        by_tr[p["transition"]] = by_tr.get(p["transition"], 0) + 1
    from .llm.tools import KPI_BY_TRANSITION
    uncovered = [t for t in KPI_BY_TRANSITION if t not in by_tr and t not in ("promotional", "churned", "intent_dropoff")]
    over = [p["title"] for p in plans if p.get("limits_status") == "over_cap"]
    return {"month": month, "plans": len(plans), "by_transition": by_tr, "transitions_without_plan": uncovered, "over_cap_plans": over,
            "reading": (f"{len(plans)} flight plan(s) for {month}; {len(uncovered)} lifecycle transition(s) have none; {len(over)} plan(s) exceed communication limits." if plans else f"No flight plans for {month} yet — the monthly_flight_plans mission drafts them in the first days of the month.")}
