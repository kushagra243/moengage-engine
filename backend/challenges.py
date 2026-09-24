"""
Challenges: what the operating agent could not do, written down so a builder can fix it.

The operating machine runs the brain on the enterprise Anthropic key against real MoEngage data. When a task cannot be
finished — a tool fails, a draft is refused, the engine lacks a tool or a permission, the model runs out of steps, a
job keeps failing — the challenge is recorded here with what was tried and what blocked it. The build machine (a Claude
Code session with this repository) pulls the open challenges as prompts, builds the fix, runs the tests, ships it, and
marks the challenge resolved with the commit; the operating machine picks the fix up with `./cli.py update`.

Two channels between the machines: a JSON export/import for a file carried by hand, and GitHub issues in the private
repository (label `challenge`, one per challenge signature) through `gh`, which is what `push` and `pull` use.

Nothing here carries workspace data: every text is `redact()`ed and then scrubbed of long identifiers; only the shape of
the failure travels. An agent tool (`log_challenge`) lets the brain write one deliberately; the automatic capture
points are tool errors, approval failures, blocked drafts, rejected briefs, out-of-steps replies and failing jobs.
"""
from __future__ import annotations
import hashlib
import json
import platform
import re
import shutil
import subprocess
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .database import get_db
from .security import audit, redact

KINDS = ("tool_error", "execution_failed", "blocked_draft", "brief_rejected", "out_of_steps", "unknown_tool", "model_error", "job_failed", "missing_capability", "agent")
STATUSES = ("open", "building", "resolved", "wontfix")
LABEL = "challenge"
_ID = re.compile(r"\b(?=[A-Za-z0-9_-]{16,}\b)(?=[A-Za-z0-9_-]*\d)[A-Za-z0-9_-]{16,}\b")     # long mixed identifiers (customer ids, hashes, request ids)


def init_tables() -> None:
    conn = get_db()
    conn.execute("""CREATE TABLE IF NOT EXISTS challenges (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    signature TEXT UNIQUE, kind TEXT, task TEXT, tried TEXT, blocked_by TEXT, suggestion TEXT, source TEXT, persona TEXT, purpose TEXT, context TEXT,
                    count INTEGER DEFAULT 1, status TEXT DEFAULT 'open', resolution TEXT, commit_sha TEXT, issue_number INTEGER, machine TEXT)""")
    conn.commit(); conn.close()


def scrub(text: Any) -> str:
    return _ID.sub("<id>", redact(str(text or "")))


def machine() -> str:
    return platform.node().split(".")[0][:40]


def log(kind: str, task: str, blocked_by: str, tried: str = "", suggestion: str = "", source: str = "engine", persona: str = "", purpose: str = "", context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Record one challenge; the same shape again only bumps its count. Never raises."""
    try:
        init_tables()
        kind = kind if kind in KINDS else "agent"
        task, blocked_by, tried, suggestion = scrub(task)[:400], scrub(blocked_by)[:600], scrub(tried)[:600], scrub(suggestion)[:400]
        sig = hashlib.sha1(f"{kind}|{task[:80].lower()}|{re.sub(r'[0-9]+', '#', blocked_by[:120].lower())}".encode()).hexdigest()[:12]
        ctx = scrub(json.dumps(context or {}, default=str))[:1500]
        conn = get_db()
        row = conn.execute("SELECT id, status, count FROM challenges WHERE signature=?", (sig,)).fetchone()
        if row:
            conn.execute("UPDATE challenges SET count=count+1, updated_at=CURRENT_TIMESTAMP, blocked_by=?, context=?, status=CASE WHEN status='resolved' THEN 'open' ELSE status END WHERE id=?", (blocked_by, ctx, row["id"]))
            cid = row["id"]; reopened = row["status"] == "resolved"
        else:
            cur = conn.execute("INSERT INTO challenges (signature, kind, task, tried, blocked_by, suggestion, source, persona, purpose, context, machine) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                               (sig, kind, task, tried, blocked_by, suggestion, source, persona or "", purpose or "", ctx, machine()))
            cid = cur.lastrowid; reopened = False
        conn.commit(); conn.close()
        audit("challenge.logged", {"id": cid, "kind": kind, "signature": sig, "reopened": reopened}, actor=source)
        return {"ok": True, "id": cid, "signature": sig, "reopened": reopened}
    except Exception as e:
        return {"ok": False, "error": redact(str(e))[:120]}


def list_open(status: str = "open", limit: int = 100) -> List[Dict[str, Any]]:
    init_tables()
    conn = get_db()
    q = "SELECT * FROM challenges" + (" WHERE status=?" if status and status != "all" else "") + " ORDER BY CASE status WHEN 'open' THEN 0 WHEN 'building' THEN 1 ELSE 2 END, count DESC, updated_at DESC LIMIT ?"
    rows = [dict(r) for r in conn.execute(q, ((status, limit) if status and status != "all" else (limit,))).fetchall()]
    conn.close()
    return rows


def get(cid: int) -> Optional[Dict[str, Any]]:
    init_tables(); conn = get_db()
    r = conn.execute("SELECT * FROM challenges WHERE id=?", (cid,)).fetchone(); conn.close()
    return dict(r) if r else None


def set_status(cid: int, status: str, resolution: str = "", commit_sha: str = "", actor: str = "user") -> Optional[Dict[str, Any]]:
    if status not in STATUSES:
        return None
    init_tables(); conn = get_db()
    conn.execute("UPDATE challenges SET status=?, resolution=COALESCE(NULLIF(?, ''), resolution), commit_sha=COALESCE(NULLIF(?, ''), commit_sha), updated_at=CURRENT_TIMESTAMP WHERE id=?", (status, scrub(resolution)[:600], commit_sha[:40], cid))
    conn.commit(); conn.close()
    audit("challenge.status", {"id": cid, "status": status, "commit": commit_sha[:12]}, actor=actor)
    c = get(cid)
    if c and c.get("issue_number") and shutil.which("gh"):
        try:
            if status in ("resolved", "wontfix"):
                _gh(["issue", "close", str(c["issue_number"]), "--comment", f"{status}: {c.get('resolution') or ''} {('commit ' + commit_sha[:12]) if commit_sha else ''}".strip()])
            elif status == "building":
                _gh(["issue", "comment", str(c["issue_number"]), "--body", "building: a Claude Code session has picked this up"])
        except Exception:
            pass
    return c


# ── prompts for the build machine ────────────────────────────────────────────
def prompt(c: Dict[str, Any]) -> str:
    ctx = {}
    try:
        ctx = json.loads(c.get("context") or "{}")
    except Exception:
        pass
    lines = [f"# Challenge #{c['id']} · {c['kind']} · seen {c['count']}× · {c['status']}",
             f"machine {c.get('machine') or '?'} · first {str(c['created_at'])[:16]} · last {str(c['updated_at'])[:16]} · source {c.get('source')}" + (f" · persona {c['persona']}" if c.get("persona") else ""), "",
             "## What the agent was trying to do", c.get("task") or "-", "",
             "## What blocked it", c.get("blocked_by") or "-", ""]
    if c.get("tried"):
        lines += ["## What it tried", c["tried"], ""]
    if c.get("suggestion"):
        lines += ["## What it asked for", c["suggestion"], ""]
    if ctx:
        lines += ["## Context (redacted)", "```json", json.dumps(ctx, indent=1)[:1500], "```", ""]
    lines += ["## Build instructions",
              "- Reproduce it with a test first (the fake-server / monkeypatch patterns in tests/), then fix the cause, not the symptom.",
              "- Keep the invariants in CLAUDE.md: loopback only, approvals for every MoEngage write, redact everything, no venue names in copy.",
              "- Run `.venv/bin/python -m pytest -q tests`, commit with a message that says why, push to main, then `./cli.py challenges resolve %d --commit <sha> --note '<what changed>'`." % c["id"],
              "- The operating machine picks it up with `./cli.py update`."]
    return "\n".join(lines)


def prompts(limit: int = 20) -> str:
    rows = list_open("open", limit)
    if not rows:
        return "No open challenges."
    return "\n\n---\n\n".join(prompt(c) for c in rows)


# ── moving between machines ──────────────────────────────────────────────────
EXPORT_KEYS = ("signature", "kind", "task", "tried", "blocked_by", "suggestion", "source", "persona", "purpose", "context", "count", "status", "resolution", "commit_sha", "machine", "created_at", "updated_at")


def export(status: str = "all") -> List[Dict[str, Any]]:
    return [{k: c.get(k) for k in EXPORT_KEYS} for c in list_open(status, 500)]


def import_rows(rows: List[Dict[str, Any]], actor: str = "import") -> Dict[str, int]:
    """Merge by signature: a newer status wins, counts take the larger, nothing is duplicated."""
    init_tables(); conn = get_db(); n_new = n_upd = 0
    for r in rows:
        if not r.get("signature"):
            continue
        clean = {k: (scrub(r.get(k)) if isinstance(r.get(k), str) else r.get(k)) for k in EXPORT_KEYS}
        cur = conn.execute("SELECT id, count, updated_at FROM challenges WHERE signature=?", (clean["signature"],)).fetchone()
        if cur:
            if str(clean.get("updated_at") or "") >= str(cur["updated_at"] or ""):
                conn.execute("UPDATE challenges SET status=?, resolution=?, commit_sha=?, count=MAX(count, ?), updated_at=? WHERE id=?",
                             (clean.get("status") or "open", clean.get("resolution"), clean.get("commit_sha"), int(clean.get("count") or 1), clean.get("updated_at"), cur["id"]))
                n_upd += 1
        else:
            conn.execute("INSERT INTO challenges (signature, kind, task, tried, blocked_by, suggestion, source, persona, purpose, context, count, status, resolution, commit_sha, machine, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         tuple(clean.get(k) for k in EXPORT_KEYS))
            n_new += 1
    conn.commit(); conn.close()
    audit("challenge.imported", {"new": n_new, "updated": n_upd}, actor=actor)
    return {"new": n_new, "updated": n_upd}


def _repo() -> str:
    try:
        url = subprocess.run(["git", "remote", "get-url", "origin"], capture_output=True, text=True, timeout=10).stdout.strip()
        m = re.search(r"github\.com[:/]([^/]+/[^/.]+)", url)
        return m.group(1) if m else ""
    except Exception:
        return ""


def _gh(args: List[str]) -> str:
    repo = _repo()
    r = subprocess.run(["gh"] + args + (["--repo", repo] if repo and args[0] in ("issue", "label") else []), capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(redact((r.stderr or r.stdout).strip())[:300])
    return r.stdout


def push_issues(limit: int = 30) -> Dict[str, Any]:
    """One GitHub issue per open challenge signature (label `challenge`); an existing one gets a comment with the new count."""
    if not shutil.which("gh"):
        return {"ok": False, "error": "gh (GitHub CLI) is not installed; use `challenges export` and carry the file"}
    try:
        _gh(["label", "create", LABEL, "--color", "5319e7", "--description", "logged by the operating agent; a build session fixes it", "--force"])
    except Exception as e:
        return {"ok": False, "error": f"cannot reach the repository's issues: {e}"}
    created = updated = 0; errors = []
    conn = get_db()
    for c in list_open("open", limit):
        title = f"[chal:{c['signature']}] {c['kind']}: {c['task'][:70]}"
        body = prompt(c)
        try:
            if c.get("issue_number"):
                _gh(["issue", "comment", str(c["issue_number"]), "--body", f"seen again · now {c['count']}× · last {str(c['updated_at'])[:16]}\n\n{c['blocked_by'][:400]}"]); updated += 1
            else:
                found = json.loads(_gh(["issue", "list", "--search", f"[chal:{c['signature']}] in:title", "--state", "all", "--json", "number", "--limit", "1"]) or "[]")
                num = found[0]["number"] if found else int(re.search(r"/(\d+)\s*$", _gh(["issue", "create", "--title", title, "--body", body, "--label", LABEL])).group(1))
                conn.execute("UPDATE challenges SET issue_number=? WHERE id=?", (num, c["id"])); conn.commit()
                created += 1
        except Exception as e:
            errors.append(f"#{c['id']}: {e}")
    conn.close()
    audit("challenge.pushed", {"created": created, "updated": updated, "errors": len(errors)}, actor="cli")
    return {"ok": not errors, "created": created, "updated": updated, "errors": errors[:5]}


def pull_issues() -> Dict[str, Any]:
    """The build machine: read open challenge issues into the local ledger (their bodies are the prompts)."""
    if not shutil.which("gh"):
        return {"ok": False, "error": "gh (GitHub CLI) is not installed"}
    try:
        items = json.loads(_gh(["issue", "list", "--label", LABEL, "--state", "open", "--json", "number,title,body,createdAt,updatedAt", "--limit", "100"]) or "[]")
    except Exception as e:
        return {"ok": False, "error": str(e)}
    rows = []
    for it in items:
        m = re.match(r"\[chal:([0-9a-f]{12})\]\s*(\w+):\s*(.*)", it.get("title") or "")
        if not m:
            continue
        body = it.get("body") or ""
        sec = lambda h: (re.search(r"## " + h + r"\n(.*?)(?:\n## |\Z)", body, re.S) or [None, ""])[1].strip()  # noqa: E731
        rows.append({"signature": m.group(1), "kind": m.group(2), "task": sec("What the agent was trying to do"), "blocked_by": sec("What blocked it"), "tried": sec("What it tried"), "suggestion": sec("What it asked for"),
                     "source": "issue", "count": int((re.search(r"seen (\d+)×", body) or [0, 1])[1]), "status": "open", "machine": "issue", "created_at": str(it.get("createdAt"))[:19].replace("T", " "),
                     "updated_at": str(it.get("updatedAt"))[:19].replace("T", " "), "context": "{}", "persona": "", "purpose": "", "resolution": None, "commit_sha": None})
    r = import_rows(rows, actor="pull")
    conn = get_db()
    for it, row in zip(items, rows):
        conn.execute("UPDATE challenges SET issue_number=? WHERE signature=?", (it["number"], row["signature"]))
    conn.commit(); conn.close()
    return {"ok": True, "issues": len(items), **r}


def summary() -> Dict[str, Any]:
    rows = list_open("all", 300)
    by = {}
    for c in rows:
        by[c["status"]] = by.get(c["status"], 0) + 1
    return {"open": by.get("open", 0), "building": by.get("building", 0), "resolved": by.get("resolved", 0), "wontfix": by.get("wontfix", 0),
            "top": [{"id": c["id"], "kind": c["kind"], "task": c["task"][:90], "count": c["count"], "last": str(c["updated_at"])[:16]} for c in rows if c["status"] == "open"][:8]}
