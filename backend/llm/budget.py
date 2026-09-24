"""
Credit rationing: the brain lives on the organisation's Claude credits, and credits are spent on knowing, not on busywork.

Three rules, all enforced before a request leaves the machine:
  1. Basic models by default. On the Anthropic provider everything runs on Haiku unless a purpose is named in
     `llm_premium_purposes` (e.g. "copy,review"), which routes just those to `llm_premium_model`.
  2. A daily budget (`llm_daily_budget_usd`, default 2.00). Past it, only an operator's own question (chat) still gets an
     answer, and only until 150% of the budget; everything else waits for tomorrow.
  3. Background work (autopilot, analysis, briefs, classification, tests, research, council) may use at most
     `llm_background_share` (default 35%) of the day's budget. Deterministic paths — detection, rules, lint, coverage —
     never touch a model at all, so rationing the model costs the alerts nothing.
`status()` shows the spend against the budget on the Engine screen and in `./cli.py doctor`.
"""
from __future__ import annotations
from typing import Any, Dict

from ..database import get_setting

BACKGROUND = {"autopilot", "analysis", "brief", "classification", "test", "research", "council"}
ESSENTIAL = {"chat", "copy", "review", "code"}


class BudgetExceeded(RuntimeError):
    pass


def daily_budget() -> float:
    try:
        return max(0.0, float(get_setting("llm_daily_budget_usd", "2.0") or 2.0))
    except ValueError:
        return 2.0


def background_share() -> float:
    try:
        return min(1.0, max(0.0, float(get_setting("llm_background_share", "0.35") or 0.35)))
    except ValueError:
        return 0.35


def spend_today() -> Dict[str, float]:
    from .usage import summary
    s = summary(1)
    bg = sum((v.get("cost_usd") or 0.0) for k, v in (s.get("by_purpose") or {}).items() if k in BACKGROUND)
    return {"total": float((s.get("today") or {}).get("cost_usd") or 0.0), "background": round(bg, 4), "calls": int((s.get("today") or {}).get("calls") or 0)}


def check(purpose: str) -> None:
    """Raise BudgetExceeded when this purpose may not spend right now."""
    b = daily_budget()
    if b <= 0:
        return                                   # 0 = unmetered (not recommended)
    sp = spend_today()
    if purpose in BACKGROUND and sp["background"] >= b * background_share():
        raise BudgetExceeded(f"credit ration: background work has used ${sp['background']:.2f} of its ${b * background_share():.2f} share today; {purpose} waits for tomorrow")
    if sp["total"] >= b * 1.5:
        raise BudgetExceeded(f"credit ration: ${sp['total']:.2f} spent today against a ${b:.2f} budget; the brain is paused until tomorrow (raise llm_daily_budget_usd if this is wrong)")
    if sp["total"] >= b and purpose not in ESSENTIAL:
        raise BudgetExceeded(f"credit ration: the daily budget of ${b:.2f} is spent; only direct questions are still answered today")


def status() -> Dict[str, Any]:
    b = daily_budget(); sp = spend_today()
    left = max(0.0, b - sp["total"]) if b else None
    return {"daily_budget_usd": b, "spent_today_usd": round(sp["total"], 4), "background_today_usd": sp["background"], "background_share": background_share(), "calls_today": sp["calls"],
            "left_usd": round(left, 4) if left is not None else None, "state": "unmetered" if not b else "paused" if sp["total"] >= b * 1.5 else "essential only" if sp["total"] >= b else "ok",
            "premium_purposes": [x.strip() for x in (get_setting("llm_premium_purposes", "") or "").split(",") if x.strip()], "premium_model": get_setting("llm_premium_model", "claude-sonnet-5")}
