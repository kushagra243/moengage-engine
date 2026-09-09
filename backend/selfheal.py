"""
Self-diagnosis and self-repair.

The engine records its own failures — tool exceptions (tool_errors), failed
scheduler steps, failed proposals, autopilot errors, server-log tracebacks —
and turns them into a health report with concrete fix requests. The agent
(or the self_heal autopilot mission) reads the report and files precise
code-change proposals (devagent) that Claude Code implements on a branch,
with tests; the operator approves the diff. If a merged change breaks
startup, start.py rolls the merge back automatically (data/last_merge.json).
"""
from __future__ import annotations
import hashlib
import json
import os
import re
import subprocess
import time
import traceback
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from .database import get_db
from .security import redact, audit

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(ROOT, "data", "logs", "server.log")
LAST_MERGE = os.path.join(ROOT, "data", "last_merge.json")


def init_selfheal_tables() -> None:
    conn = get_db()
    conn.execute("""CREATE TABLE IF NOT EXISTS tool_errors (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, source TEXT, name TEXT, args TEXT,
                    error TEXT, error_type TEXT, trace TEXT, signature TEXT)""")
    conn.commit(); conn.close()


def record_error(source: str, name: str, err: BaseException, args: Optional[Dict[str, Any]] = None) -> str:
    """Store a failure with a stable signature (type + top frame + message head) for de-duplication."""
    try:
        init_selfheal_tables()
        tb = traceback.format_exc()
        frames = re.findall(r'File "([^"]+)", line (\d+), in (\w+)', tb)
        top = frames[-1] if frames else ("", "", "")
        sig = hashlib.sha1(f"{type(err).__name__}|{os.path.basename(top[0])}|{top[2]}|{str(err)[:60]}".encode()).hexdigest()[:12]
        conn = get_db()
        conn.execute("INSERT INTO tool_errors (source, name, args, error, error_type, trace, signature) VALUES (?,?,?,?,?,?,?)",
                     (source, name, redact(json.dumps(args or {}, default=str))[:800], redact(str(err))[:600], type(err).__name__, redact(tb)[-2500:], sig))
        conn.commit(); conn.close()
        return sig
    except Exception:
        return ""


def recent_errors(hours: int = 48, limit: int = 100) -> List[Dict[str, Any]]:
    init_selfheal_tables()
    conn = get_db()
    rows = [dict(r) for r in conn.execute("SELECT * FROM tool_errors WHERE created_at >= datetime('now', ?) ORDER BY id DESC LIMIT ?", (f"-{int(hours)} hours", limit)).fetchall()]
    conn.close()
    return rows


def _log_tracebacks(max_lines: int = 4000) -> List[Dict[str, Any]]:
    if not os.path.exists(LOG):
        return []
    try:
        with open(LOG, errors="replace") as f:
            lines = f.readlines()[-max_lines:]
    except Exception:
        return []
    out, buf = [], []
    for ln in lines:
        if ln.startswith("Traceback (most recent call last)"):
            buf = [ln]
        elif buf:
            buf.append(ln)
            if re.match(r"^[A-Za-z_.]+(Error|Exception)[:\s]", ln):
                out.append({"tail": redact("".join(buf[-14:]))[-1500:]}); buf = []
    return out[-8:]


def health_report(run_tests: bool = False) -> Dict[str, Any]:
    from .database import get_latest_daily_run
    from .security.audit import tail as audit_tail
    from . import approvals
    errs = recent_errors()
    by_sig: Dict[str, Dict[str, Any]] = {}
    for e in errs:
        b = by_sig.setdefault(e["signature"], {"signature": e["signature"], "count": 0, "source": e["source"], "name": e["name"], "error_type": e["error_type"], "error": e["error"], "last": e["created_at"], "trace": e["trace"]})
        b["count"] += 1
    tool_groups = sorted(by_sig.values(), key=lambda b: -b["count"])
    latest = get_latest_daily_run() or {}
    rep = {}
    try:
        rep = json.loads(latest.get("report_json") or latest.get("report") or "{}") if isinstance(latest.get("report_json") or latest.get("report"), str) else (latest.get("report") or {})
    except Exception:
        rep = {}
    job_errors = {k: redact(str(v))[:300] for k, v in (rep or {}).items() if isinstance(k, str) and k.endswith("_error") and v}
    failed_props = [{"id": p["id"], "kind": p["kind"], "title": p["title"], "error": (p.get("error") or "")[:200]} for p in approvals.list_proposals(status="failed", limit=50)
                    if p.get("executed_at") and str(p["executed_at"]) >= (datetime.utcnow() - timedelta(days=2)).strftime("%Y-%m-%d")]
    audit_fail = [a for a in audit_tail(300) if str(a.get("event", "")).endswith(("failed", "_error", "cli_failed_fallback"))][-10:]
    tests = None
    if run_tests:
        py = os.path.join(ROOT, ".venv", "bin", "python")
        try:
            r = subprocess.run([py, "-m", "pytest", "-q", "tests", "-x", "--no-header", "-p", "no:cacheprovider"], cwd=ROOT, capture_output=True, text=True, timeout=600)
            tests = {"passed": r.returncode == 0, "tail": redact((r.stdout + r.stderr)[-1200:])}
        except Exception as e:
            tests = {"passed": False, "tail": redact(str(e))[:300]}
    fixes = []
    for g in tool_groups[:6]:
        fixes.append({"signature": g["signature"], "title": f"Fix {g['error_type']} in {g['name']} ({g['count']}× in 48h)",
                      "request": (f"The tool/job '{g['name']}' (source {g['source']}) raised {g['error_type']}: {g['error']}. Traceback tail:\n{g['trace'][-900:]}\n"
                                  f"Find the root cause, fix it in the backend, add a regression test that reproduces the failure, run the suite."), "scope": "backend"})
    for k, v in job_errors.items():
        fixes.append({"signature": hashlib.sha1((k + v[:40]).encode()).hexdigest()[:12], "title": f"Fix scheduler step {k}", "request": f"The daily run reported {k}: {v}. Trace the step in backend/scheduler.py and the module it calls, fix the cause, add a test.", "scope": "backend"})
    for tb in _log_tracebacks():
        sig = hashlib.sha1(tb["tail"][-200:].encode()).hexdigest()[:12]
        if not any(f["signature"] == sig for f in fixes):
            fixes.append({"signature": sig, "title": "Fix traceback seen in server log", "request": f"The server log contains this traceback:\n{tb['tail']}\nFix the cause and add a regression test.", "scope": "backend"})
    if tests and not tests["passed"]:
        fixes.append({"signature": "tests", "title": "Make the test suite pass", "request": f"pytest fails:\n{tests['tail']}\nFix the code (not the assertion, unless the assertion is wrong) and re-run.", "scope": "tests"})
    status = "healthy" if not (tool_groups or job_errors or failed_props or fixes) else ("degraded" if len(fixes) <= 2 else "unhealthy")
    return {"status": status, "tool_errors_48h": len(errs), "error_groups": tool_groups[:10], "job_errors": job_errors, "failed_proposals": failed_props[:10], "audit_failures": audit_fail,
            "log_tracebacks": _log_tracebacks(), "tests": tests, "fix_requests": fixes[:8], "last_merge": last_merge(), "advice": "file each fix_request with propose_code_change (it drafts on a branch with tests); approve the diff; re-run self_diagnose after the restart"}


def last_merge() -> Optional[Dict[str, Any]]:
    try:
        return json.load(open(LAST_MERGE))
    except Exception:
        return None


def note_merge(proposal_id: int, pre_commit: str, post_commit: str) -> None:
    os.makedirs(os.path.dirname(LAST_MERGE), exist_ok=True)
    json.dump({"proposal_id": proposal_id, "pre_commit": pre_commit, "post_commit": post_commit, "at": time.strftime("%Y-%m-%d %H:%M:%S"), "rolled_back": False}, open(LAST_MERGE, "w"))


def rollback_last_merge(reason: str = "manual") -> Dict[str, Any]:
    """Revert the last agent merge commit (keeps history); used by start.py when import fails after a change, or by the CLI."""
    lm = last_merge()
    if not lm or lm.get("rolled_back"):
        return {"ok": False, "error": "nothing to roll back"}
    try:
        st = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
        if st:
            return {"ok": False, "error": "working tree dirty; commit or stash first"}
        r = subprocess.run(["git", "revert", "--no-edit", "-m", "1", lm["post_commit"]], cwd=ROOT, capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            subprocess.run(["git", "revert", "--abort"], cwd=ROOT, capture_output=True)
            return {"ok": False, "error": redact((r.stderr or r.stdout)[-400:])}
        lm["rolled_back"] = True; lm["rollback_reason"] = reason; json.dump(lm, open(LAST_MERGE, "w"))
        audit("devagent.rolled_back", {"proposal_id": lm.get("proposal_id"), "reason": reason, "reverted": lm["post_commit"]}, actor="system")
        return {"ok": True, "reverted": lm["post_commit"], "reason": reason}
    except Exception as e:
        return {"ok": False, "error": redact(str(e))[:300]}
