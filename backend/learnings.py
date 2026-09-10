"""
Own-learnings skill — what *our* programme has taught us, compiled from the
experiment ledger, readouts, deep dives, QA trend, India-fit review and the
workspace analysis into `.claude/skills/our-learnings/SKILL.md`. The agent
loads it like any other skill, so our own evidence outranks generic benchmarks
in every brief. Refreshed by the daily cycle; safe to regenerate any time.
"""
from __future__ import annotations
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List

from .security import audit

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, ".claude", "skills", "our-learnings", "SKILL.md")


def _lines(items: List[str], empty: str) -> str:
    return "\n".join(f"- {x}" for x in items) if items else f"- {empty}"


def compile_learnings() -> Dict[str, Any]:
    from . import experiments, growth, qa, analysis as deep, sop_india
    exps = experiments.list_experiments(300)
    read = [e for e in exps if e.get("status") == "window_complete" and (e.get("readout") or {}).get("verdict")]
    running = [e for e in exps if e.get("status") == "running"]
    wins, losses, flat = [], [], []
    for e in read:
        r = e.get("readout") or {}; v = str(r.get("verdict") or ""); kpi = e.get("primary_kpi") or "kpi"
        line = f"**{e.get('campaign_name')}** ({kpi}, {e.get('window_days')}d, holdout {e.get('control_group_pct')}%): {v[:220]}"
        vl = v.lower()
        (wins if any(w in vl for w in ("up", "improv", "lift", "better", "won", "positive")) and "no " not in vl[:12] else losses if any(w in vl for w in ("down", "worse", "fell", "negative", "kill")) else flat).append(line)
    lessons = [i for i in growth.list_ideas(status=None, limit=300) if (i.get("title") or "").startswith("Lesson:")][:20]
    deep_rows = []
    try:
        for d in deep.list_analyses(40):
            if d.get("verdict") and d.get("verdict") not in ("ready", ""):
                deep_rows.append(f"**{d.get('campaign_name')}**: {str(d.get('verdict'))[:180]}" + (f" → {d.get('next_action')}" if d.get("next_action") else ""))
    except Exception:
        pass
    qa_hist = qa.history(14)
    try:
        ir = sop_india.review(); india = f"{ir['counts'].get('india-ready', 0)} india-ready · {ir['counts'].get('needs edits', 0)} need edits · {ir['counts'].get('rework', 0)} rework · avg {ir['avg_score']}"; india_rules = [f"{r} ×{n}" for r, n in ir.get("top_rules", [])[:5]]
    except Exception:
        india, india_rules = "n/a", []
    by_channel: Dict[str, List[str]] = {}
    try:
        from . import workspace_analysis as wa
        f = wa.facts()
        for ch in f.get("channels") or []:
            by_channel[ch["channel"]] = [f"{ch['verdict']}: {ch['why']}"]
        best = [f"{x['name']} click {x['ctr']}% ({x['channel']})" for x in (f.get("campaigns") or {}).get("best_ctr", [])[:3]]
        worst = [f"{x['name']} click {x['ctr']}% ({x['channel']})" for x in (f.get("campaigns") or {}).get("worst_ctr", [])[:3]]
        uncovered = [t["transition"] for t in f.get("lifecycle") or [] if t["status"] == "uncovered"]
        source = (f.get("programme") or {}).get("source")
    except Exception:
        best, worst, uncovered, source = [], [], [], "unknown"
    return {"wins": wins, "losses": losses, "flat": flat, "running": [f"{e.get('campaign_name')} ({e.get('primary_kpi')}, day {((e.get('readout') or {}).get('days_run') or 0)} of {e.get('window_days')})" for e in running[:10]],
            "lessons": [f"{i['title'].replace('Lesson: ', '')}: {(i.get('why') or '')[:200]}" for i in lessons[:12]], "deep": deep_rows[:10], "qa": [f"{h['at'][:10]} score {h['score']} ({h.get('fail', 0)} fail / {h.get('warn', 0)} warn)" for h in qa_hist[:7]],
            "india": india, "india_rules": india_rules, "channels": by_channel, "best": best, "worst": worst, "uncovered": uncovered, "source": source, "n_read": len(read), "n_running": len(running)}


def render(L: Dict[str, Any]) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    caveat = "Numbers below come from the mock workspace and are illustrative." if L.get("source") == "mock" else "Readouts are pre/post against each campaign's own baseline unless a holdout figure is stated; treat them as directional."
    return f"""---
name: our-learnings
description: What CoinDCX's own MoEngage programme has taught us so far — experiment readouts (wins, losses, flat), lessons recorded by the brain, deep-dive verdicts per campaign, channel health in our data, QA trend and India-fit status. Cite this BEFORE any generic benchmark; regenerated automatically by the daily cycle (backend/learnings.py).
---

# Our learnings (auto-compiled {now})

{caveat} Source workspace: {L.get('source')}. {L.get('n_read')} experiments read out, {L.get('n_running')} running.

## What worked here
{_lines(L['wins'], 'no positive readout yet — every proposal carries a holdout so this fills in')}

## What did not
{_lines(L['losses'], 'no negative readout yet')}

## Flat / inconclusive
{_lines(L['flat'], 'none')}

## Lessons the brain recorded
{_lines(L['lessons'], 'none yet')}

## Deep-dive verdicts (per campaign)
{_lines(L['deep'], 'none yet — the daily cycle analyses the campaigns needing attention first')}

## Channels in our data
{_lines([f"**{k}** — {'; '.join(v)}" for k, v in L['channels'].items()], 'no channel stats')}
- best click: {', '.join(L['best']) or '—'}
- worst click: {', '.join(L['worst']) or '—'}
- uncovered transitions: {', '.join(L['uncovered']) or 'none'}

## Running now
{_lines(L['running'], 'nothing running')}

## Quality trend (QA score)
{_lines(L['qa'], 'no QA runs yet')}

## India fit of the SOP library
- {L['india']}
- most common flags: {', '.join(L['india_rules']) or '—'}

## How to use this skill
1. Before proposing, check *What worked here* and *What did not* for the same transition or channel; cite the readout in the rationale.
2. Prefer repeating a win with one variable changed over a new idea with no evidence; prefer killing a loser over tweaking it.
3. If a claim needs a number that is not here, run `experiment_readouts` or `workspace_analysis` — do not quote a benchmark as if it were ours.
"""


def refresh() -> Dict[str, Any]:
    L = compile_learnings()
    text = render(L)
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    with open(PATH, "w", encoding="utf-8") as f:
        f.write(text)
    audit("learnings.refresh", {"read": L.get("n_read"), "running": L.get("n_running"), "chars": len(text)}, actor="learnings")
    return {"ok": True, "path": os.path.relpath(PATH, ROOT), "chars": len(text), "read": L.get("n_read"), "running": L.get("n_running")}
