"""
Questions from the brain to the operator, and the answers it keeps.

The agent often needs a fact only the team has: which cohort a name refers to, what a KPI is called in this workspace,
whether a product is live in a region, which segment to use for a test, what the weekly send cap should be. Instead of
guessing or stalling, it asks: the question appears on Setup (with a count), in `./cli.py asks`, and in Slack when that
is set up. The operator answers in plain words; the answer is stored as standing guidance (`agent_guidance`), so every
later conversation already knows it, and the question is marked answered. Unanswered questions are also challenges in
spirit: the builder can see what the brain keeps asking.
"""
from __future__ import annotations
import json
from typing import Any, Dict, List, Optional

from .database import get_db
from .security import audit, redact


def init_tables() -> None:
    conn = get_db()
    conn.execute("""CREATE TABLE IF NOT EXISTS operator_questions (id INTEGER PRIMARY KEY AUTOINCREMENT, asked_at TEXT DEFAULT CURRENT_TIMESTAMP, question TEXT NOT NULL, why TEXT, options TEXT,
                    scope TEXT DEFAULT 'general', context TEXT, asked_by TEXT, status TEXT DEFAULT 'open', answer TEXT, answered_by TEXT, answered_at TEXT, slack_ts TEXT)""")
    conn.commit(); conn.close()


def ask(question: str, why: str = "", options: Optional[List[str]] = None, scope: str = "general", context: Optional[Dict[str, Any]] = None, asked_by: str = "agent") -> Dict[str, Any]:
    question = redact(str(question or "")).strip()[:400]
    if not question:
        return {"ok": False, "error": "empty question"}
    init_tables(); conn = get_db()
    dup = conn.execute("SELECT id FROM operator_questions WHERE status='open' AND lower(question)=lower(?)", (question,)).fetchone()
    if dup:
        conn.close(); return {"ok": True, "id": dup["id"], "duplicate": True, "note": "already asked; waiting for the operator"}
    cur = conn.execute("INSERT INTO operator_questions (question, why, options, scope, context, asked_by) VALUES (?,?,?,?,?,?)",
                       (question, redact(str(why or ""))[:600], json.dumps([str(o)[:80] for o in (options or [])][:6]), scope[:40], redact(json.dumps(context or {}, default=str))[:1200], asked_by))
    qid = cur.lastrowid; conn.commit(); conn.close()
    audit("operator_question.asked", {"id": qid, "scope": scope}, actor=asked_by)
    try:
        from . import slack_out
        if slack_out.enabled():
            r = slack_out._api("chat.postMessage", {"channel": slack_out.channel(), "text": f"The brain asks (question #{qid}): {question}\n{why[:300]}" + (f"\nOptions: {', '.join(options)}" if options else "") + f"\nAnswer on Setup or with: ./cli.py answer question:{qid} answer=\"…\""})
            conn = get_db(); conn.execute("UPDATE operator_questions SET slack_ts=? WHERE id=?", (r.get("ts"), qid)); conn.commit(); conn.close()
    except Exception:
        pass
    return {"ok": True, "id": qid, "note": "asked; the operator sees it on Setup, in the CLI and in Slack. Continue with the best available path and say what you assumed."}


def open_questions(limit: int = 30) -> List[Dict[str, Any]]:
    init_tables(); conn = get_db()
    rows = [dict(r) for r in conn.execute("SELECT * FROM operator_questions WHERE status='open' ORDER BY id LIMIT ?", (limit,)).fetchall()]
    conn.close()
    for r in rows:
        try:
            r["options"] = json.loads(r.get("options") or "[]")
        except Exception:
            r["options"] = []
    return rows


def answer(qid: int, text: str, actor: str = "user") -> Dict[str, Any]:
    text = str(text or "").strip()[:1000]
    init_tables(); conn = get_db()
    q = conn.execute("SELECT * FROM operator_questions WHERE id=?", (qid,)).fetchone()
    if not q:
        conn.close(); return {"ok": False, "error": "no such question"}
    if not text:
        conn.close(); return {"ok": False, "error": "empty answer"}
    conn.execute("UPDATE operator_questions SET status='answered', answer=?, answered_by=?, answered_at=CURRENT_TIMESTAMP WHERE id=?", (text, actor, qid))
    conn.commit(); conn.close()
    remembered = False
    try:
        from . import guidance
        guidance.add(f"When you need to know: {q['question']} → the operator's answer: {text}", author=actor, scope=q["scope"] if q["scope"] in getattr(guidance, "SCOPES", ("general",)) else "general")
        remembered = True
    except Exception:
        pass
    audit("operator_question.answered", {"id": qid, "remembered": remembered}, actor=actor)
    return {"ok": True, "id": qid, "remembered": remembered}


def dismiss(qid: int, actor: str = "user") -> Dict[str, Any]:
    init_tables(); conn = get_db()
    conn.execute("UPDATE operator_questions SET status='dismissed', answered_by=?, answered_at=CURRENT_TIMESTAMP WHERE id=?", (actor, qid)); conn.commit(); conn.close()
    return {"ok": True}


def recently_answered(limit: int = 10) -> List[Dict[str, Any]]:
    init_tables(); conn = get_db()
    rows = [dict(r) for r in conn.execute("SELECT id, question, answer, answered_by, answered_at FROM operator_questions WHERE status='answered' ORDER BY answered_at DESC LIMIT ?", (limit,)).fetchall()]
    conn.close(); return rows
