"""
Dev agent: the engine changes its own code, UI and CLI on request — with the
same approval discipline as MoEngage writes.

Flow
  1. propose  – operator (Agent tab → "Change request") or the agent
                (propose_code_change) queues a `code_change` proposal.
  2. draft    – the change is implemented on an isolated git worktree/branch
                agent/change-<id>: engine `claude_cli` runs Claude Code headless
                with edit permissions limited to that worktree; fallback engine
                `model_patch` asks the configured model for a unified diff and
                applies it. Tests run in the worktree; diff + test result are
                stored on the proposal as the preview. Nothing touches the
                running code.
  3. approve  – merges the branch into the checked-out branch, records the
                commit, then restarts the server process (execv start.py) so the
                change is live. Reject deletes the branch and worktree.
Safety: drafting never sees engine secrets (environment scrubbed), can only
write inside the worktree, and the human reads the diff before it merges.
"""
from __future__ import annotations
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from .database import get_setting
from .security import audit, redact

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKTREES = os.path.join(ROOT, "data", "worktrees")
KIND = "code_change"
SCOPES = ("frontend", "backend", "cli", "docs", "tests", "any")
DRAFT_TIMEOUT_S = 15 * 60
RESTART = True          # tests switch this off
ENGINE_OVERRIDE: Optional[Callable[[str, Dict[str, Any]], Dict[str, Any]]] = None   # tests inject a fake engine
_drafting: Dict[int, bool] = {}
_lock = threading.Lock()

TEXT_EXT = (".py", ".js", ".css", ".html", ".md", ".txt", ".json", ".sh", ".toml", ".yaml", ".yml")


def _git(args: List[str], cwd: Optional[str] = None, check: bool = True, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(["git"] + args, cwd=cwd or ROOT, capture_output=True, text=True, timeout=timeout, check=check)


def _scrubbed_env() -> Dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not re.match(r"(MOE_|OPENROUTER|OPENAI|ANTHROPIC_API|.*KEY|.*TOKEN|.*SECRET)", k, re.I) or k in ("PATH", "HOME")}
    env["PATH"] = os.environ.get("PATH", "/usr/bin:/bin:/usr/local/bin") + ":" + os.path.expanduser("~/.local/bin")
    env["HOME"] = os.environ.get("HOME", "")
    env["CI"] = "1"
    return env


def cli_available() -> bool:
    return shutil.which("claude") is not None or os.path.exists(os.path.expanduser("~/.local/bin/claude"))


def engines() -> Dict[str, Any]:
    from .llm.provider import llm_settings
    cfg = llm_settings()
    model_ok = bool(cfg.get("api_key")) and cfg.get("provider") != "claude_cli"
    return {"claude_cli": cli_available(), "model_patch": model_ok,
            "preferred": "claude_cli" if cli_available() else ("model_patch" if model_ok else None),
            "enabled": get_setting("devagent_enabled", "true").lower() == "true",
            "git_clean": _repo_clean(), "branch": _current_branch()}


def _current_branch() -> str:
    try:
        return _git(["rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()
    except Exception:
        return "?"


def _repo_clean() -> bool:
    try:
        return _git(["status", "--porcelain", "--untracked-files=no"]).stdout.strip() == ""
    except Exception:
        return False


def branch_name(pid: int) -> str:
    return f"agent/change-{pid}"


def worktree_path(pid: int) -> str:
    return os.path.join(WORKTREES, f"change-{pid}")


# ── proposal contract ─────────────────────────────────────────────────────────
def validate(p: Dict[str, Any]) -> None:
    if not (p.get("request") or "").strip() or len(p["request"].strip()) < 12:
        raise ValueError("request must describe the change (≥ 12 chars)")
    if len(p["request"]) > 6000:
        raise ValueError("request too long (6000 chars max)")
    if p.get("scope") and p["scope"] not in SCOPES:
        raise ValueError(f"scope must be one of {SCOPES}")


def preview(p: Dict[str, Any]) -> Dict[str, Any]:
    e = engines()
    if not e["enabled"]:
        return {"mode": "code", "status": "disabled", "blocked": "code changes are switched off (devagent_enabled=false)", "unlock": "set devagent_enabled to true in Settings"}
    if not e["preferred"]:
        return {"mode": "code", "status": "no_engine", "blocked": "no drafting engine: install Claude Code (claude login) or set an OpenRouter key",
                "unlock": "npm install -g @anthropic-ai/claude-code && claude login   — or Settings → LLM key"}
    return {"mode": "code", "status": "draft_pending", "engine": e["preferred"], "note": "click 'Draft' (or the agent drafts automatically) to implement on an isolated branch; you approve the diff"}


# ── drafting ──────────────────────────────────────────────────────────────────
def _prepare_worktree(pid: int) -> str:
    os.makedirs(WORKTREES, exist_ok=True)
    wt = worktree_path(pid); br = branch_name(pid)
    if os.path.exists(wt):
        _git(["worktree", "remove", "--force", wt], check=False)
        shutil.rmtree(wt, ignore_errors=True)
    _git(["branch", "-D", br], check=False)
    _git(["worktree", "add", "-b", br, wt, "HEAD"])
    os.makedirs(os.path.join(wt, "data", "logs"), exist_ok=True)
    return wt


def _prompt(p: Dict[str, Any], pid: int) -> str:
    scope = p.get("scope") or "any"
    return (f"You are modifying moengage-engine (local MoEngage CLM console: FastAPI backend in backend/, vanilla JS console in frontend/, CLI cli.py, tests in tests/). "
            f"Read CLAUDE.md first for conventions. Implement this change request #{pid} (scope: {scope}):\n\n{p['request'].strip()}\n\n"
            f"{('Context from the operator: ' + p['context'].strip()) if p.get('context') else ''}\n"
            "Rules: keep the change minimal and consistent with existing style; never add external CDN resources to the frontend (CSP is self-only); "
            "never weaken security (loopback bind, local token, redaction, approval gating, host allowlists); never read or print files under data/; "
            "update or add tests when behaviour changes; run `.venv/bin/python -m pytest -q tests` (use ../../../.venv/bin/python if .venv is missing here) and fix failures; "
            "do not commit — leave changes in the working tree. Finish with a 5-line summary: what changed, files touched, how to verify.")


def _engine_claude_cli(prompt: str, wt: str) -> Dict[str, Any]:
    exe = shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")
    allowed = "Read,Edit,Write,MultiEdit,Grep,Glob,LS,Bash(git diff*),Bash(git status*),Bash(.venv/bin/python*),Bash(../../../.venv/bin/python*),Bash(python3 -m pytest*),Bash(ls*),Bash(cat*),Bash(sed -n*)"
    cmd = [exe, "-p", prompt, "--output-format", "json", "--permission-mode", "acceptEdits", "--allowedTools", allowed, "--max-turns", "60"]
    r = subprocess.run(cmd, cwd=wt, capture_output=True, text=True, timeout=DRAFT_TIMEOUT_S, env=_scrubbed_env())
    out = r.stdout.strip()
    summary = out
    try:
        j = json.loads(out)
        summary = j.get("result") or j.get("content") or out
        if j.get("is_error"):
            return {"ok": False, "summary": str(summary)[:2000], "stderr": r.stderr[-1500:]}
    except Exception:
        pass
    if r.returncode != 0:
        return {"ok": False, "summary": str(summary)[:2000], "stderr": r.stderr[-1500:]}
    return {"ok": True, "summary": str(summary)[:4000]}


def _context_files(request: str, wt: str, limit: int = 6, max_chars: int = 70000) -> List[Dict[str, str]]:
    words = {w for w in re.findall(r"[a-zA-Z_][a-zA-Z0-9_]{3,}", request.lower())}
    scored = []
    for base, dirs, files in os.walk(wt):
        rel = os.path.relpath(base, wt)
        if any(rel.startswith(x) for x in (".git", "data", ".venv", "node_modules", "backend/knowledge/moengage-api")):
            dirs[:] = []; continue
        for f in files:
            if not f.endswith(TEXT_EXT):
                continue
            path = os.path.join(base, f)
            try:
                text = open(path, encoding="utf-8").read()
            except Exception:
                continue
            low = text.lower(); score = sum(low.count(w) for w in words) + (5 if f in ("app.js", "index.html", "main.py", "cli.py") else 0)
            if score:
                scored.append((score, os.path.relpath(path, wt), text))
    scored.sort(key=lambda t: -t[0])
    out, used = [], 0
    for _, rel, text in scored[:limit]:
        chunk = text[: max(4000, max_chars - used)]
        out.append({"path": rel, "content": chunk}); used += len(chunk)
        if used >= max_chars:
            break
    return out


def _engine_model_patch(prompt: str, wt: str, request: str) -> Dict[str, Any]:
    from .llm.provider import LLMClient
    files = _context_files(request, wt)
    sys_msg = ("You are a careful senior engineer. Output ONLY a unified diff (git format, paths relative to repo root, with ---/+++ headers and @@ hunks) inside one ```diff fence. "
               "No prose outside the fence. Small, minimal changes. New files use --- /dev/null.")
    user = prompt + "\n\nRelevant files (full or partial contents):\n" + "\n\n".join(f"=== {f['path']} ===\n{f['content']}" for f in files)
    client = LLMClient()
    resp = client.chat([{"role": "system", "content": sys_msg}, {"role": "user", "content": user}], tools=None, max_tokens=8000)
    text = resp.get("content") or ""
    m = re.search(r"```diff\n(.*?)```", text, re.S)
    if not m:
        return {"ok": False, "summary": "model did not return a diff", "raw": text[:1500]}
    patch = m.group(1)
    pf = os.path.join(wt, "data", "agent.patch")
    open(pf, "w").write(patch)
    chk = subprocess.run(["git", "apply", "--check", "--whitespace=nowarn", pf], cwd=wt, capture_output=True, text=True)
    if chk.returncode != 0:
        return {"ok": False, "summary": "diff does not apply: " + chk.stderr[-800:], "raw": patch[:1500]}
    subprocess.run(["git", "apply", "--whitespace=nowarn", pf], cwd=wt, capture_output=True, text=True, check=True)
    os.remove(pf)
    return {"ok": True, "summary": f"applied model diff touching {len(files)} context files", "model": resp.get("model")}


def _run_tests(wt: str) -> Dict[str, Any]:
    py = os.path.join(ROOT, ".venv", "bin", "python")
    if not os.path.exists(py):
        py = sys.executable
    try:
        r = subprocess.run([py, "-m", "pytest", "-q", "tests", "-x", "--no-header", "-p", "no:cacheprovider"], cwd=wt, capture_output=True, text=True, timeout=600, env={**_scrubbed_env(), "PYTHONPATH": wt})
        tail = (r.stdout + r.stderr)[-1500:]
        return {"passed": r.returncode == 0, "tail": redact(tail)}
    except Exception as e:
        return {"passed": False, "tail": redact(str(e))[:500]}


def draft(pid: int, actor: str = "user") -> Dict[str, Any]:
    """Implement the proposal on its branch; store diff + tests in the proposal preview. Synchronous (minutes)."""
    from . import approvals
    p = approvals.get_proposal(pid)
    if not p or p["kind"] != KIND:
        raise ValueError("not a code_change proposal")
    if p["status"] != "pending":
        raise ValueError(f"proposal is {p['status']}")
    with _lock:
        if _drafting.get(pid):
            return {"status": "drafting", "note": "already in progress"}
        _drafting[pid] = True
    e = engines()
    try:
        if not e["enabled"] or not e["preferred"]:
            pv = preview(p["payload"]); approvals.update_preview(pid, pv); return pv
        approvals.update_preview(pid, {**(p.get("preview") or {}), "status": "drafting", "engine": e["preferred"], "started_at": time.strftime("%Y-%m-%d %H:%M:%S")})
        audit("devagent.draft_started", {"id": pid, "engine": e["preferred"]}, actor=actor)
        wt = _prepare_worktree(pid)
        prompt = _prompt(p["payload"], pid)
        if ENGINE_OVERRIDE:
            res = ENGINE_OVERRIDE(prompt, {"worktree": wt, "payload": p["payload"]})
        elif e["preferred"] == "claude_cli":
            res = _engine_claude_cli(prompt, wt)
            if not res.get("ok") and e.get("model_patch") and not _git(["status", "--porcelain"], cwd=wt).stdout.strip():
                audit("devagent.cli_failed_fallback", {"id": pid, "why": redact(str(res.get("summary") or res.get("stderr") or ""))[:300]}, actor=actor)
                res = _engine_model_patch(prompt, wt, p["payload"]["request"]); res["engine"] = "model_patch (cli failed: %s)" % redact(str(res.get("summary") or ""))[:80] if not res.get("ok") else "model_patch"
                e["preferred"] = "model_patch"
        else:
            res = _engine_model_patch(prompt, wt, p["payload"]["request"])
        _git(["add", "-A", "."], cwd=wt)
        stat = _git(["diff", "--cached", "--stat"], cwd=wt).stdout.strip()
        full = _git(["diff", "--cached"], cwd=wt).stdout
        files = [l.split("|")[0].strip() for l in stat.splitlines()[:-1]] if stat else []
        if not full.strip():
            pv = {"mode": "code", "status": "failed", "engine": e["preferred"], "blocked": "engine produced no changes", "engine_summary": redact(str(res.get("summary") or ""))[:2000], "stderr": redact(str(res.get("stderr") or ""))[:800]}
            approvals.update_preview(pid, pv); return pv
        tests = _run_tests(wt) if not ENGINE_OVERRIDE else {"passed": True, "tail": "skipped (fake engine)"}
        _git(["-c", "user.name=moengage-engine agent", "-c", "user.email=agent@local", "commit", "-q", "-m", f"agent change #{pid}: {p['title'][:70]}\n\n{p['payload']['request'][:1500]}"], cwd=wt)
        commit = _git(["rev-parse", "--short", "HEAD"], cwd=wt).stdout.strip()
        pv = {"mode": "code", "status": "drafted", "engine": e["preferred"], "branch": branch_name(pid), "commit": commit, "files": files, "diff_stat": stat[:3000],
              "diff": redact(full[:120000]) + ("\n…[diff truncated]" if len(full) > 120000 else ""), "tests": tests,
              "engine_summary": redact(str(res.get("summary") or ""))[:3000], "engine_ok": bool(res.get("ok", True)),
              "note": "Approve = merge this branch into the running checkout and restart the server. Reject = discard branch."}
        if not tests["passed"]:
            pv["blocked"] = "tests failed on the drafted branch — read the tail, reject and re-request with more detail, or approve knowingly"
        approvals.update_preview(pid, pv)
        audit("devagent.drafted", {"id": pid, "files": files[:20], "tests_passed": tests["passed"], "commit": commit}, actor=actor)
        return pv
    except subprocess.TimeoutExpired:
        pv = {"mode": "code", "status": "failed", "blocked": f"drafting timed out after {DRAFT_TIMEOUT_S // 60} min"}
        approvals.update_preview(pid, pv); return pv
    except Exception as ex:
        pv = {"mode": "code", "status": "failed", "blocked": redact(str(ex))[:600]}
        approvals.update_preview(pid, pv); return pv
    finally:
        with _lock:
            _drafting.pop(pid, None)


def draft_async(pid: int, actor: str = "agent") -> None:
    threading.Thread(target=lambda: draft(pid, actor), daemon=True, name=f"devagent-draft-{pid}").start()


# ── execute (approve) ─────────────────────────────────────────────────────────
def execute(p: Dict[str, Any]) -> Dict[str, Any]:
    from . import approvals
    pid = p.get("_proposal_id")
    prop = approvals.get_proposal(int(pid)) if pid else None
    pv = (prop or {}).get("preview") or {}
    if pv.get("status") != "drafted":
        raise RuntimeError("nothing drafted yet: click Draft first, then approve the diff")
    if not _repo_clean():
        raise RuntimeError("the running checkout has uncommitted changes; commit or stash them, then approve again")
    br = pv["branch"]; target = _current_branch()
    try:
        _git(["merge", "--no-ff", "--no-edit", "-m", f"Merge agent change #{pid}: {prop['title'][:70]}", br])
    except subprocess.CalledProcessError as e:
        _git(["merge", "--abort"], check=False)
        raise RuntimeError("merge conflict with the current branch: " + redact((e.stderr or e.stdout)[-600:]))
    head = _git(["rev-parse", "--short", "HEAD"]).stdout.strip()
    cleanup(int(pid), keep_branch=True)
    audit("devagent.merged", {"id": pid, "into": target, "commit": head, "files": pv.get("files", [])[:20]}, actor="user")
    deps_changed = any(f.strip() == "requirements.txt" for f in pv.get("files", []))
    if deps_changed:
        subprocess.run([os.path.join(ROOT, ".venv", "bin", "python"), "-m", "pip", "install", "-q", "-r", os.path.join(ROOT, "requirements.txt")], capture_output=True, text=True, timeout=600)
    restart = schedule_restart() if RESTART else False
    return {"success": True, "merged_into": target, "commit": head, "files": pv.get("files", []), "restart_scheduled": restart,
            "message": "merged; server restarting to load the change" if restart else "merged; restart the server to load the change"}


def cleanup(pid: int, keep_branch: bool = False) -> None:
    wt = worktree_path(pid)
    _git(["worktree", "remove", "--force", wt], check=False)
    shutil.rmtree(wt, ignore_errors=True)
    _git(["worktree", "prune"], check=False)
    if not keep_branch:
        _git(["branch", "-D", branch_name(pid)], check=False)


def schedule_restart(delay_s: float = 1.5) -> bool:
    """Replace this process with a fresh start.py (same PORT). Sockets are close-on-exec, so the port is re-bound cleanly."""
    start = os.path.join(ROOT, "start.py")
    if not os.path.exists(start):
        return False
    def _go():
        time.sleep(delay_s)
        audit("engine.restart", {"reason": "code change merged"}, actor="system")
        py = os.path.join(ROOT, ".venv", "bin", "python")
        if not os.path.exists(py):
            py = sys.executable
        os.chdir(ROOT)
        os.execv(py, [py, start])
    threading.Thread(target=_go, daemon=True, name="engine-restart").start()
    return True


def register() -> None:
    from .approvals import register_executor
    register_executor(KIND, execute, preview, validate)


def status() -> Dict[str, Any]:
    from . import approvals
    e = engines()
    props = [p for p in approvals.list_proposals(limit=100) if p["kind"] == KIND]
    return {**e, "drafting": sorted(_drafting.keys()), "recent": [{"id": p["id"], "title": p["title"], "status": p["status"], "draft": ((p.get("preview") or {}).get("status")), "files": (p.get("preview") or {}).get("files", [])[:6]} for p in props[:12]]}
