"""
One bundle, zero model calls: everything the builder needs to see why the engine is not working on a machine.

`bundle()` collects the doctor, the refresh schedule, failing jobs with their errors, tool errors, failed proposals, the
last tracebacks from the server log, where credits went (spend by purpose, model, hour; the calls that cost most), the
non-secret model configuration, the budget state, challenges and telemetry — all through `redact()` and the challenge
scrubber. `push()` files it as a GitHub issue titled `[diagnose] <machine>` (updated in place on the next run), which the
builder reads with `./cli.py challenges pull`-style `gh` access. Nothing here spends a credit or touches a user record.

`pause()` / `resume()` stop every model call at once (`llm_paused=true`, checked by the budget ration before any request)
so a misbehaving loop cannot burn credits while it is being debugged; deterministic paths (alerts, refresh, dashboards)
keep running.
"""
from __future__ import annotations
import json
import os
import platform
import re
import subprocess
from datetime import datetime, timezone
from typing import Any, Dict, List

from .database import get_db, get_setting, set_setting
from .security import audit, redact

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _q(sql: str, *args) -> List[Dict[str, Any]]:
    try:
        conn = get_db(); rows = [dict(r) for r in conn.execute(sql, args).fetchall()]; conn.close(); return rows
    except Exception as e:
        return [{"error": redact(str(e))[:120]}]


def _scrub(o: Any) -> Any:
    from .challenges import scrub
    if isinstance(o, dict):
        return {k: _scrub(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_scrub(v) for v in o]
    if isinstance(o, str):
        return scrub(o)
    return o


def paused() -> bool:
    return str(get_setting("llm_paused", "false")).lower() == "true"


def pause(actor: str = "user", why: str = "") -> Dict[str, Any]:
    set_setting("llm_paused", "true"); set_setting("llm_paused_why", why[:200])
    audit("brain.paused", {"why": why[:120]}, actor=actor)
    return {"paused": True, "note": "every model call is refused until resume; alerts, refresh jobs and dashboards keep running"}


def resume(actor: str = "user") -> Dict[str, Any]:
    set_setting("llm_paused", "false"); set_setting("llm_paused_why", "")
    audit("brain.resumed", {}, actor=actor)
    return {"paused": False}


def credits_report(days: int = 2) -> Dict[str, Any]:
    """Where the credits went: by purpose, by model, by hour, and the single most expensive calls."""
    since = f"-{int(days)} days"
    return {"days": days,
            "by_purpose": _q("SELECT purpose, tier, COUNT(*) calls, ROUND(SUM(COALESCE(cost_usd,0)),4) usd, SUM(prompt_tokens) p, SUM(completion_tokens) c, SUM(cached_tokens) cached FROM llm_usage WHERE created_at >= datetime('now', ?) GROUP BY purpose, tier ORDER BY usd DESC", since),
            "by_model": _q("SELECT model, COUNT(*) calls, ROUND(SUM(COALESCE(cost_usd,0)),4) usd FROM llm_usage WHERE created_at >= datetime('now', ?) GROUP BY model ORDER BY usd DESC LIMIT 12", since),
            "by_hour": _q("SELECT strftime('%Y-%m-%d %H:00', created_at) h, COUNT(*) calls, ROUND(SUM(COALESCE(cost_usd,0)),4) usd FROM llm_usage WHERE created_at >= datetime('now', ?) GROUP BY h ORDER BY h DESC LIMIT 48", since),
            "biggest_calls": _q("SELECT created_at, purpose, model, prompt_tokens, completion_tokens, ROUND(COALESCE(cost_usd,0),4) usd FROM llm_usage WHERE created_at >= datetime('now', ?) ORDER BY COALESCE(cost_usd,0) DESC, prompt_tokens DESC LIMIT 10", since),
            "calls_per_conversation_hint": _q("SELECT COUNT(*) n FROM chat_messages WHERE role='assistant' AND created_at >= datetime('now', ?)", since)}


def _tracebacks(limit: int = 6) -> List[str]:
    out: List[str] = []
    for name in ("server.log", "engine.log"):
        p = os.path.join(ROOT, "data", "logs", name)
        if not os.path.exists(p):
            continue
        try:
            with open(p, "rb") as f:
                f.seek(max(0, os.path.getsize(p) - 400_000)); text = f.read().decode("utf-8", "replace")
        except Exception:
            continue
        for m in re.finditer(r"Traceback \(most recent call last\):.*?(?=\n\S|\Z)", text, re.S):
            out.append(redact(m.group(0))[-1800:])
    return out[-limit:]


def bundle(days: int = 2) -> Dict[str, Any]:
    from . import challenges, telemetry, roles
    from .llm import budget
    from .llm.provider import llm_settings, bulk_models, routes, fallback_cfg
    cfg = llm_settings()
    try:
        build = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=5, cwd=ROOT).stdout.strip()
    except Exception:
        build = ""
    try:
        from . import refresher
        rs = refresher.status()
        jobs = {"enabled": rs.get("enabled"), "failing": rs.get("failing"), "overdue": rs.get("overdue"), "market_age_min": rs.get("market_age_min"),
                "rows": [{k: j.get(k) for k in ("job", "interval_min", "age_min", "ok", "ms", "error", "overdue", "never", "running")} for j in rs.get("jobs") or []]}
    except Exception as e:
        jobs = {"error": redact(str(e))[:200]}
    try:
        from .alerts2 import discovery
        preflight = discovery.preflight()
    except Exception as e:
        preflight = [{"check": "preflight", "ok": False, "detail": redact(str(e))[:200]}]
    try:
        from .database import get_all_settings
        st = get_all_settings()
        settings_view = {k: v for k, v in st.items() if k.endswith("_set") or k in ("llm_provider", "llm_model", "llm_model_bulk", "llm_base_url", "llm_fallback_provider", "llm_fallback_model", "llm_fallback_on", "llm_daily_budget_usd", "llm_background_share",
                                                                              "llm_premium_purposes", "llm_max_rounds", "llm_max_rounds_autopilot", "llm_history_messages", "llm_tool_output_chars", "llm_autoload_skills", "llm_paused", "llm_paused_why",
                                                                              "mock_mode", "engine_role", "schedule_enabled", "schedule_time", "refresh_enabled", "autopilot_enabled", "ma2_alert_approval", "ma2_internal_delivery", "moengage_dc", "moengage_region", "telegram_alerts", "slack_ask_approval")}
    except Exception:
        settings_view = {}
    out = {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "machine": platform.node().split(".")[0][:40], "build": build, "python": platform.python_version(), "role": roles.role(),
        "paused": paused(), "paused_why": get_setting("llm_paused_why", ""),
        "model": {"provider": cfg["provider"], "main": cfg["model"], "heavy_lifting": bulk_models(cfg)[0], "base_url": cfg["base_url"], "key_set": bool(cfg["api_key"]), "routes": routes(cfg), "fallback": bool(fallback_cfg(cfg))},
        "budget": budget.status(), "credits": credits_report(days), "settings": settings_view, "jobs": jobs, "preflight": preflight,
        "tool_errors": _q("SELECT created_at, name, error_type, error, signature FROM tool_errors WHERE created_at >= datetime('now', ?) ORDER BY id DESC LIMIT 30", f"-{int(days)} days"),
        "tool_error_groups": _q("SELECT name, error_type, COUNT(*) n, MAX(created_at) last FROM tool_errors WHERE created_at >= datetime('now', ?) GROUP BY name, error_type ORDER BY n DESC LIMIT 15", f"-{int(days)} days"),
        "failed_proposals": _q("SELECT id, kind, status, title, error, created_at, executed_at FROM proposals WHERE status IN ('failed') AND created_at >= datetime('now', ?) ORDER BY id DESC LIMIT 15", f"-{int(days)} days"),
        "proposals_by_status": _q("SELECT status, COUNT(*) n FROM proposals GROUP BY status"),
        "recent_audit_failures": _audit_failures(40),
        "tracebacks": _tracebacks(),
        "challenges": {"summary": challenges.summary(), "open": [{k: c.get(k) for k in ("id", "kind", "task", "blocked_by", "count", "updated_at")} for c in challenges.list_open("open", 20)]},
        "telemetry": telemetry.snapshot(7),
    }
    return _scrub(out)


def _audit_failures(limit: int = 40) -> List[Dict[str, Any]]:
    from .security.audit import AUDIT_FILE
    if not os.path.exists(AUDIT_FILE):
        return []
    out: List[Dict[str, Any]] = []
    try:
        with open(AUDIT_FILE, "rb") as f:
            f.seek(max(0, os.path.getsize(AUDIT_FILE) - 600_000)); lines = f.read().decode("utf-8", "replace").splitlines()
    except Exception:
        return []
    for line in reversed(lines):
        try:
            e = json.loads(line)
        except Exception:
            continue
        ev = str(e.get("event") or "")
        if any(k in ev for k in ("fail", "error", "refused", "blocked", "offline", "paused", "budget", "challenge", "expired")):
            out.append({"ts": e.get("ts"), "event": ev, "actor": e.get("actor"), "detail": redact(json.dumps(e.get("detail") or {}, default=str))[:300]})
        if len(out) >= limit:
            break
    return out


def markdown(b: Dict[str, Any]) -> str:
    j = lambda o: "```json\n" + json.dumps(o, indent=1, default=str)[:12000] + "\n```"   # noqa: E731
    parts = [f"# Diagnose · {b['machine']} · build {b['build']} · role {b['role']} · {b['at']}",
             f"brain paused: {b['paused']} {b.get('paused_why') or ''}", "", "## Model and credits", j({"model": b["model"], "budget": b["budget"]}), j(b["credits"]),
             "## Settings (secrets shown as set / not set)", j(b["settings"]), "## Background jobs", j(b["jobs"]), "## Alerts preflight", j(b["preflight"]),
             "## Tool errors", j({"groups": b["tool_error_groups"], "recent": b["tool_errors"][:12]}), "## Failed proposals", j({"failed": b["failed_proposals"], "by_status": b["proposals_by_status"]}),
             "## Recent audit failures", j(b["recent_audit_failures"]), "## Tracebacks", "\n\n".join("```\n" + t + "\n```" for t in b["tracebacks"]) or "_none in the last 400 KB of logs_",
             "## Challenges", j(b["challenges"]), "## Telemetry", j(b["telemetry"])]
    return "\n\n".join(parts)


def push(b: Dict[str, Any]) -> Dict[str, Any]:
    """File or update the `[diagnose] <machine>` issue in the private repo. Counts, errors and configuration only."""
    import shutil
    from .challenges import _gh, LABEL
    if not shutil.which("gh"):
        return {"ok": False, "error": "gh (GitHub CLI) is not installed; use --out and send the file"}
    title = f"[diagnose] {b['machine']}"
    body = markdown(b)
    if len(body) > 60000:
        body = body[:60000] + "\n\n_(truncated)_"
    try:
        found = json.loads(_gh(["issue", "list", "--search", f"{title} in:title", "--state", "open", "--json", "number", "--limit", "1"]) or "[]")
        if found:
            _gh(["issue", "edit", str(found[0]["number"]), "--body", body]); num = found[0]["number"]; action = "updated"
        else:
            url = _gh(["issue", "create", "--title", title, "--body", body, "--label", LABEL]).strip(); num = int(url.rsplit("/", 1)[-1]); action = "created"
        audit("diagnose.pushed", {"issue": num}, actor="cli")
        return {"ok": True, "issue": num, "action": action}
    except Exception as e:
        return {"ok": False, "error": redact(str(e))[:300]}
