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

KINDS = ("create_segment", "create_campaign", "create_flow", "pause_campaign", "resume_campaign", "update_segment", "custom_segment_upload", "code_change", "signal_rule")

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
    try:
        conn.execute("ALTER TABLE proposals ADD COLUMN revisions_json TEXT")
        conn.commit()
    except Exception:
        pass
    conn.close()


def register_executor(kind: str, execute: Callable[[Dict[str, Any]], Dict[str, Any]], preview: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None, validate: Optional[Callable[[Dict[str, Any]], None]] = None) -> None:
    if kind not in KINDS:
        raise ValueError(f"unknown proposal kind {kind}")
    _executors[kind] = {"execute": execute, "preview": preview or (lambda p: {}), "validate": validate or (lambda p: None)}


def _row(r) -> Dict[str, Any]:
    d = dict(r)
    for k in ("payload_json", "preview_json", "result_json", "revisions_json"):
        try:
            d[k[:-5]] = json.loads(d.pop(k) or "null")
        except Exception:
            d[k[:-5]] = None
    d["revisions"] = d.get("revisions") or []
    d.update(classify(d))
    return d


TRANSITION_CATEGORY = {"acquired_verified": "onboarding", "verified_funded": "onboarding", "funded_activated": "activation", "activated_habitual": "activation", "habitual_core": "retention",
                       "slipping": "retention", "dormant_activated": "winback", "churned": "winback", "promotional": "promotion", "intent_dropoff": "activation"}


def classify(p: Dict[str, Any]) -> Dict[str, Any]:
    """Experiment classification for the board: category (campaign type), product (affinity), stage."""
    pl = p.get("payload") or {}
    category = None
    if pl.get("sop_id"):
        try:
            from .sops import get_sop
            category = (get_sop(pl["sop_id"]) or {}).get("campaign_type")
        except Exception:
            category = None
    if not category:
        tr = (pl.get("goal") or {}).get("transition")
        category = TRANSITION_CATEGORY.get(tr) if tr else None
    if not category:
        category = {"create_segment": "audience", "create_flow": "journey", "pause_campaign": "risk", "resume_campaign": "risk", "code_change": "engine", "custom_segment_upload": "audience"}.get(p.get("kind"), "campaign")
    if pl.get("market_hook_id") or pl.get("ttl_hours"):
        category = "market" if category in ("campaign", "activation", "retention") else category
    product = None
    try:
        from .products import affinity_from_tokens
        from .taxonomy import tokens
        words = tokens(str(pl.get("target_segment") or "")) + tokens(str(pl.get("name") or p.get("title") or ""))
        aff = affinity_from_tokens(words, {})
        product = aff[0] if aff else None
    except Exception:
        product = None
    st = p.get("status")
    stage = {"pending": "awaiting_approval", "approved": "approved", "rejected": "rejected", "failed": "failed", "expired": "expired"}.get(st, st)
    if st == "executed":
        stage = "running"
        try:
            conn = get_db()
            e = conn.execute("SELECT status FROM experiments WHERE proposal_id=? ORDER BY id DESC LIMIT 1", (p.get("id"),)).fetchone()
            conn.close()
            if e and e["status"] == "window_complete":
                stage = "read"
        except Exception:
            pass
    return {"category": category, "product": product, "stage": stage}


def _push_revision(pid: int, entry: Dict[str, Any]) -> None:
    conn = get_db()
    r = conn.execute("SELECT revisions_json FROM proposals WHERE id=?", (pid,)).fetchone()
    try:
        revs = json.loads((r["revisions_json"] if r else None) or "[]")
    except Exception:
        revs = []
    revs.append({**entry, "at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")})
    conn.execute("UPDATE proposals SET revisions_json=? WHERE id=?", (json.dumps(revs[-50:], default=str), pid))
    conn.commit(); conn.close()


def _deep_merge(base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for k, v in (patch or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def update_payload(pid: int, changes: Dict[str, Any], actor: str = "user", note: str = "", replace: bool = False) -> Dict[str, Any]:
    """Edit a pending proposal before approval: merge (or replace) the payload, re-validate, re-preview, record the revision."""
    p = get_proposal(pid)
    if not p:
        raise ApprovalError("proposal not found")
    if p["status"] != "pending":
        raise ApprovalError(f"proposal is {p['status']}; only pending proposals can be edited")
    new_payload = dict(changes) if replace else _deep_merge(p["payload"] or {}, changes or {})
    ex = _executors.get(p["kind"])
    preview = p.get("preview") or {}
    if ex:
        ex["validate"](new_payload)
        try:
            preview = ex["preview"](new_payload)
        except Exception as e:
            preview = {"preview_error": redact(str(e))}
    if p["kind"] == "create_campaign":
        from .llm.tools import campaign_brief_check
        chk = campaign_brief_check(new_payload.get("goal") or {}, new_payload.get("variants") or [], channel=str(new_payload.get("channel") or "push"), market_linked=bool(new_payload.get("market_hook_id") or new_payload.get("ttl_hours")), ttl_hours=new_payload.get("ttl_hours"))
        if not chk["ok"]:
            raise ApprovalError("edit rejected by the brief check: " + "; ".join(chk["problems"]))
    changed = sorted(k for k in (changes or {}).keys())
    conn = get_db()
    conn.execute("UPDATE proposals SET payload_json=?, preview_json=? WHERE id=?", (json.dumps(new_payload, default=str), json.dumps(preview, default=str), pid))
    conn.commit(); conn.close()
    _push_revision(pid, {"type": "edit", "actor": actor, "note": redact(note or "")[:500], "changed": changed})
    audit("proposal.edited", {"id": pid, "actor": actor, "changed": changed}, actor=actor)
    return get_proposal(pid)  # type: ignore[return-value]


def add_comment(pid: int, text: str, actor: str = "user") -> Dict[str, Any]:
    p = get_proposal(pid)
    if not p:
        raise ApprovalError("proposal not found")
    text = redact((text or "").strip())[:1000]
    if len(text) < 2:
        raise ApprovalError("empty comment")
    _push_revision(pid, {"type": "comment", "actor": actor, "text": text})
    audit("proposal.commented", {"id": pid, "actor": actor}, actor=actor)
    return get_proposal(pid)  # type: ignore[return-value]


def propose(kind: str, title: str, payload: Dict[str, Any], rationale: str = "", risk: str = "medium", created_by: str = "agent") -> Dict[str, Any]:
    init_approval_tables()
    if kind not in KINDS:
        raise ApprovalError(f"unknown proposal kind {kind}")
    # de-duplicate: an identical pending proposal (same kind + title) is returned instead of queued twice
    conn = get_db()
    dup = conn.execute("SELECT * FROM proposals WHERE status='pending' AND kind=? AND title=? ORDER BY id DESC LIMIT 1", (kind, title[:200])).fetchone()
    conn.close()
    if dup:
        row = _row(dup); row["duplicate_of_pending"] = True
        return row
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
    if kind == "create_campaign":
        try:
            from .experiments import register_proposed
            from .database import get_setting as _gs
            row["experiment_id"] = register_proposed(row, "mock" if _gs("mock_mode", "true").lower() == "true" else "live")
        except Exception:
            pass
    return row


def update_preview(pid: int, preview: Dict[str, Any]) -> None:
    """Executors that draft asynchronously (code changes) refresh the stored preview."""
    conn = get_db()
    conn.execute("UPDATE proposals SET preview_json=? WHERE id=?", (json.dumps(preview, default=str), pid))
    conn.commit(); conn.close()


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
    if p["kind"] == "create_campaign":
        try:
            conn = get_db(); conn.execute("UPDATE experiments SET status='abandoned', updated_at=CURRENT_TIMESTAMP WHERE proposal_id=? AND status='proposed'", (pid,)); conn.commit(); conn.close()
        except Exception:
            pass
    if p["kind"] == "code_change":
        try:
            from .devagent import cleanup
            cleanup(pid)
        except Exception:
            pass
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
        result = ex["execute"]({**p["payload"], "_proposal_id": pid})
        conn = get_db()
        conn.execute("UPDATE proposals SET status='executed', executed_at=CURRENT_TIMESTAMP, result_json=? WHERE id=?", (json.dumps(result, default=str), pid))
        conn.commit(); conn.close()
        audit("proposal.executed", {"id": pid, "kind": p["kind"], "ok": result.get("success", True)}, actor=decided_by)
        if p["kind"] == "create_campaign" and result.get("success", True):
            try:
                from .experiments import register_from_proposal
                from .database import get_setting as _gs
                register_from_proposal(p, result, "mock" if _gs("mock_mode", "true").lower() == "true" else "live")
            except Exception as ex:
                audit("experiment.register_failed", {"id": pid, "error": redact(str(ex))}, actor="system")
    except Exception as e:
        err = redact(str(e))
        conn = get_db()
        conn.execute("UPDATE proposals SET status='failed', executed_at=CURRENT_TIMESTAMP, error=? WHERE id=?", (err[:4000], pid))
        conn.commit(); conn.close()
        audit("proposal.failed", {"id": pid, "kind": p["kind"], "error": err}, actor=decided_by)
    return get_proposal(pid)  # type: ignore[return-value]


def expire_stale() -> Dict[str, int]:
    """Pending proposals that can no longer be sent as designed: market-linked ones older than 2× their TTL, and scheduled ones whose date passed by more than a day."""
    init_approval_tables()
    n = 0
    for p in list_proposals(status="pending", limit=500):
        pl = p.get("payload") or {}
        created = str(p.get("created_at") or "")
        try:
            age_h = (datetime.utcnow() - datetime.fromisoformat(created.replace(" ", "T"))).total_seconds() / 3600
        except Exception:
            age_h = 0
        ttl = pl.get("ttl_hours")
        sched = ((pl.get("schedule") or {}).get("date")) if isinstance(pl.get("schedule"), dict) else None
        stale = (ttl and age_h > 2 * float(ttl)) or (sched and sched < (datetime.utcnow().date() - __import__("datetime").timedelta(days=1)).isoformat())
        if stale:
            conn = get_db()
            conn.execute("UPDATE proposals SET status='expired', decided_at=CURRENT_TIMESTAMP, decided_by='system', decision_note=? WHERE id=?", ("expired: market fact stale (TTL) or scheduled date passed", p["id"]))
            conn.commit(); conn.close()
            if p["kind"] == "create_campaign":
                try:
                    conn = get_db(); conn.execute("UPDATE experiments SET status='abandoned', updated_at=CURRENT_TIMESTAMP WHERE proposal_id=? AND status='proposed'", (p["id"],)); conn.commit(); conn.close()
                except Exception:
                    pass
            audit("proposal.expired", {"id": p["id"], "kind": p["kind"], "ttl_hours": ttl, "schedule": sched}, actor="system")
            n += 1
    return {"expired": n}


def registered_kinds() -> List[str]:
    return sorted(_executors.keys())
