"""
Telemetry the operator machine may share with the builder: shapes and counts, never content.

What goes out: model spend and calls per purpose, tool error rates by tool name, background-job health, alert counts by
signal and outcome, skill usage, challenge counts, approval throughput, engine version. What never goes out: user ids,
segment or campaign names, copy, keys, hostnames beyond a short machine label. `snapshot()` is what `./cli.py telemetry`
prints and what `challenges push` attaches to the pinned `[telemetry]` issue.
"""
from __future__ import annotations
import subprocess
from datetime import datetime, timezone
from typing import Any, Dict

from .database import get_db, get_setting


def _q(sql: str, *args) -> list:
    try:
        conn = get_db(); rows = [dict(r) for r in conn.execute(sql, args).fetchall()]; conn.close(); return rows
    except Exception:
        return []


def snapshot(days: int = 7) -> Dict[str, Any]:
    from .llm.usage import summary
    from .llm import budget
    from . import challenges, skill_router
    from .llm.provider import llm_settings, bulk_models
    cfg = llm_settings()
    try:
        build = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        build = ""
    u = summary(days)
    since = f"-{int(days)} days"
    return {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "build": build, "role": get_setting("engine_role", "operator"), "days": days,
        "model": {"provider": cfg["provider"], "main": cfg["model"], "heavy_lifting": bulk_models(cfg)[0], "budget": {k: budget.status().get(k) for k in ("daily_budget_usd", "spent_today_usd", "background_today_usd", "state")}},
        "spend": {"period_usd": u["period"]["cost_usd"], "calls": u["period"]["calls"], "by_purpose": {k: {"calls": v["calls"], "usd": v["cost_usd"], "cached_tokens": v["cached_tokens"]} for k, v in u["by_purpose"].items()}},
        "tool_errors": {r["name"]: r["n"] for r in _q("SELECT name, COUNT(*) n FROM tool_errors WHERE created_at >= datetime('now', ?) GROUP BY name ORDER BY n DESC LIMIT 25", since)},
        "jobs": {r["job"]: {"runs": r["n"], "failed": r["f"], "p50_ms": r["ms"]} for r in _q("SELECT job, COUNT(*) n, SUM(CASE WHEN ok=0 THEN 1 ELSE 0 END) f, CAST(AVG(ms) AS INTEGER) ms FROM refresh_runs WHERE started_at >= datetime('now', ?) GROUP BY job", since)},
        "alerts": {"fires_by_signal_status": {f"{r['signal']}:{r['status']}": r["n"] for r in _q("SELECT signal, status, COUNT(*) n FROM ma2_discovery_fires WHERE created_at >= datetime('now', ?) GROUP BY signal, status", since)},
                   "detections_by_outcome": {r["outcome"]: r["n"] for r in _q("SELECT outcome, COUNT(*) n FROM ma2_detections WHERE created_at >= datetime('now', ?) GROUP BY outcome", since)}},
        "approvals": {r["status"]: r["n"] for r in _q("SELECT status, COUNT(*) n FROM proposals WHERE created_at >= datetime('now', ?) GROUP BY status", since)},
        "approvals_by_kind": {r["kind"]: r["n"] for r in _q("SELECT kind, COUNT(*) n FROM proposals WHERE created_at >= datetime('now', ?) GROUP BY kind", since)},
        "skills": {s["skill"]: s["auto"] + s["by_the_agent"] for s in skill_router.usage(days)["used"]},
        "challenges": {k: v for k, v in challenges.summary().items() if k != "top"},
        "slack": {k: v for k, v in (_slack() or {}).items() if k in ("on", "awaiting")},
        "telegram": {k: v for k, v in (_tg() or {}).items() if k in ("on", "sent_today", "tried_today")},
    }


def _slack():
    try:
        from . import slack_out
        return slack_out.status()
    except Exception:
        return {}


def _tg():
    try:
        from . import telegram_out
        return telegram_out.status()
    except Exception:
        return {}
