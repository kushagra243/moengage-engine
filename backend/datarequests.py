"""
Data requests: what the agent needs but does not have — a segment that should
be uploaded, an event or attribute to instrument, an export, an API key or a
dashboard capture — with why it matters and which campaign it unblocks. The
team fulfils or declines them in the Agent tab; flight plans link to them.
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional

from .database import get_db
from .security import audit, redact

KINDS = ("segment", "event", "attribute", "export", "api_access", "dashboard_capture", "content", "other")


def init_datarequest_tables() -> None:
    conn = get_db()
    conn.execute("""CREATE TABLE IF NOT EXISTS data_requests (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, title TEXT, why TEXT, spec TEXT, unblocks TEXT, priority INTEGER DEFAULT 50,
                    status TEXT DEFAULT 'open', requested_by TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, note TEXT)""")
    conn.commit(); conn.close()


def request(kind: str, title: str, why: str, spec: str = "", unblocks: Optional[List[str]] = None, priority: int = 50, requested_by: str = "agent") -> Dict[str, Any]:
    init_datarequest_tables()
    kind = kind if kind in KINDS else "other"
    title = redact((title or "").strip())[:160]
    if len(title) < 6:
        return {"error": "title too short"}
    conn = get_db()
    dup = conn.execute("SELECT id, status FROM data_requests WHERE lower(title)=lower(?) AND status='open'", (title,)).fetchone()
    if dup:
        conn.close(); return {"id": dup["id"], "duplicate": True, "status": dup["status"]}
    cur = conn.execute("INSERT INTO data_requests (kind, title, why, spec, unblocks, priority, requested_by) VALUES (?,?,?,?,?,?,?)",
                       (kind, title, redact(why or "")[:1500], redact(spec or "")[:3000], ", ".join(unblocks or [])[:600], int(priority), requested_by))
    rid = cur.lastrowid; conn.commit(); conn.close()
    audit("data_request.created", {"id": rid, "kind": kind, "title": title}, actor=requested_by)
    return {"id": rid, "status": "open", "kind": kind, "title": title}


def list_requests(status: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
    init_datarequest_tables()
    conn = get_db()
    q = "SELECT * FROM data_requests" + (" WHERE status=?" if status else "") + " ORDER BY CASE status WHEN 'open' THEN 0 ELSE 1 END, priority DESC, id DESC LIMIT ?"
    rows = [dict(r) for r in conn.execute(q, ((status, limit) if status else (limit,))).fetchall()]
    conn.close()
    return rows


def set_status(rid: int, status: str, note: str = "", actor: str = "user") -> Optional[Dict[str, Any]]:
    if status not in ("open", "in_progress", "fulfilled", "declined"):
        return None
    conn = get_db()
    conn.execute("UPDATE data_requests SET status=?, note=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (status, redact(note or "")[:600], rid))
    conn.commit()
    r = conn.execute("SELECT * FROM data_requests WHERE id=?", (rid,)).fetchone(); conn.close()
    audit("data_request.status", {"id": rid, "status": status}, actor=actor)
    return dict(r) if r else None
