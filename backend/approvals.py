"""
Human-in-the-loop approval queue.

The agent (or a UI action) can only *propose* writes to MoEngage. Nothing is
sent until a person clicks Approve in the local UI. Each proposal carries a
dry-run preview of the exact HTTP request(s) that will be made, produced by
the executor without sending anything. Executors are registered by the
MoEngage layer; unknown kinds cannot be approved.
"""
from __future__ import annotations
import json
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from .database import get_db
from .security import audit, redact

KINDS = ("create_segment", "create_campaign", "create_flow", "pause_campaign", "resume_campaign", "update_segment", "custom_segment_upload")

_executors: Dict[str, Dict[str, Callable[..., Dict[str, Any]]]] = {}


class ApprovalError(RuntimeError):
    pass


def init_approval_tables() -> None:
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS proposals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,
            title TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            rationale TEXT,
            risk TEXT DEFAULT 'medium',
            preview_json TEXT,
            status TEXT NOT NULL DEFAULT 'pending',   -- pending | approved | rejected | executed | failed | expired
            created_by TEXT DEFAULT 'agent',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            decided_at TIMESTAMP, decided_by TEXT, decision_note TEXT,
            executed_at TIMESTAMP, result_json TEXT, error TEXT
        )
    """)
    conn.commit()
    conn.close()


def register_executor(kind: str, execute: Callable[[Dict[str, Any]], Dict[str, Any]], preview: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None, validate: Optional[Callable[[Dict[str, Any]], None]] = None) -> None:
    if kind not in KINDS:
        raise ValueError(f"unknown proposal kind {kind}")
    _executors[kind] = {"execute": execute, "preview": preview or (lambda p: {}), "validate": validate or (lambda p: None)}


def _row(r) -> Dict[str, Any]:
    d = dict(r)
    for k in ("payload_json", "preview_json", "result_json"):
        try:
            d[k[:-5]] = json.loads(d.pop(k) or "null")
        except Exception:
            d[k[:-5]] = None
    return d


def propose(kind: str, title: str, payload: Dict[str, Any], rationale: str = "", risk: str = "medium", created_by: str = "agent") -> Dict[str, Any]:
    init_approval_tables()
    if kind not in KINDS:
        raise ApprovalError(f"unknown proposal kind {kind}")
    ex = _executors.get(kind)
    preview: Dict[str, Any] = {}
    if ex:
        ex["validate"](payload)          # raises on bad payload
        try:
            preview = ex["preview"](payload)
        except Exception as e:           # preview failure must not block proposing
            preview = {"preview_error": redact(str(e))}
    else:
        preview = {"note": "no executor registered for this kind yet; approval will be blocked until one exists"}
    conn = get_db()
    c = conn.cursor()
    c.execute("""INSERT INTO proposals (kind, title, payload_json, rationale, risk, preview_json, created_by)
                 VALUES (?,?,?,?,?,?,?)""",
              (kind, title[:200], json.dumps(payload, default=str), rationale[:4000], risk, json.dumps(preview, default=str), created_by))
    pid = c.lastrowid
    conn.commit()
    row = _row(conn.execute("SELECT * FROM proposals WHERE id=?", (pid,)).fetchone())
    conn.close()
    audit("proposal.created", {"id": pid, "kind": kind, "title": title, "created_by": created_by}, actor=created_by)
    return row


def list_proposals(status: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
    init_approval_tables()
    conn = get_db()
    if status:
        rows = conn.execute("SELECT * FROM proposals WHERE status=? ORDER BY id DESC LIMIT ?", (status, limit)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM proposals ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [_row(r) for r in rows]


def get_proposal(pid: int) -> Optional[Dict[str, Any]]:
    init_approval_tables()
    conn = get_db()
    r = conn.execute("SELECT * FROM proposals WHERE id=?", (pid,)).fetchone()
    conn.close()
    return _row(r) if r else None


def reject(pid: int, note: str = "", decided_by: str = "user") -> Dict[str, Any]:
    p = get_proposal(pid)
    if not p:
        raise ApprovalError("proposal not found")
    if p["status"] != "pending":
        raise ApprovalError(f"proposal is {p['status']}, not pending")
    conn = get_db()
    conn.execute("UPDATE proposals SET status='rejected', decided_at=CURRENT_TIMESTAMP, decided_by=?, decision_note=? WHERE id=?", (decided_by, note[:1000], pid))
    conn.commit(); conn.close()
    audit("proposal.rejected", {"id": pid, "note": note}, actor=decided_by)
    return get_proposal(pid)  # type: ignore[return-value]


def approve_and_execute(pid: int, decided_by: str = "user", note: str = "") -> Dict[str, Any]:
    """Approve then immediately execute. Only callable from the local UI (token-protected route)."""
    p = get_proposal(pid)
    if not p:
        raise ApprovalError("proposal not found")
    if p["status"] != "pending":
        raise ApprovalError(f"proposal is {p['status']}, not pending")
    ex = _executors.get(p["kind"])
    if not ex:
        raise ApprovalError(f"no executor registered for kind {p['kind']}")
    conn = get_db()
    conn.execute("UPDATE proposals SET status='approved', decided_at=CURRENT_TIMESTAMP, decided_by=?, decision_note=? WHERE id=?", (decided_by, note[:1000], pid))
    conn.commit(); conn.close()
    audit("proposal.approved", {"id": pid, "kind": p["kind"], "title": p["title"]}, actor=decided_by)
    try:
        result = ex["execute"](p["payload"])
        conn = get_db()
        conn.execute("UPDATE proposals SET status='executed', executed_at=CURRENT_TIMESTAMP, result_json=? WHERE id=?", (json.dumps(result, default=str), pid))
        conn.commit(); conn.close()
        audit("proposal.executed", {"id": pid, "kind": p["kind"], "ok": result.get("success", True)}, actor=decided_by)
    except Exception as e:
        err = redact(str(e))
        conn = get_db()
        conn.execute("UPDATE proposals SET status='failed', executed_at=CURRENT_TIMESTAMP, error=? WHERE id=?", (err[:4000], pid))
        conn.commit(); conn.close()
        audit("proposal.failed", {"id": pid, "kind": p["kind"], "error": err}, actor=decided_by)
    return get_proposal(pid)  # type: ignore[return-value]


def registered_kinds() -> List[str]:
    return sorted(_executors.keys())
