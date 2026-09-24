"""
Slack: the approval click, from the channel the team already lives in.

When a draft is queued (a campaign, an alert waiting to go out, the alerts launch), the engine posts it to one Slack
channel with the copy, the audience, the plan and the id. A named approver answers in the thread — "approve" or
"reject" (or a ✅ / ❌ reaction on the post) — and the engine, which polls the thread every minute over Slack's Web API,
executes the approval exactly as a click on Today would: the draft is created in MoEngage, the alert is fired. The
approver's Slack user id becomes the decided_by in the audit log.

No inbound endpoint: the engine stays loopback-only and reads Slack, never the other way round. Settings: `slack_bot_token`
(secret, xoxb-…, needs chat:write, channels:history or groups:history, reactions:read), `slack_channel_id`,
`slack_approvers` (comma-separated Slack user ids; nobody else's word counts), `slack_ask_approval` (on/off). Outbound
only to slack.com through the `slack` scope; every text is redacted; no agent tool can post here.
"""
from __future__ import annotations
import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .database import get_db, get_setting
from .security import audit, guarded_session, redact

APPROVE = re.compile(r"^\s*(approve|approved|yes|go|ship it|lgtm)\b", re.I)
REJECT = re.compile(r"^\s*(reject|rejected|no|stop|hold)\b", re.I)
ASK_KINDS = ("create_campaign", "alert_send", "ma2_discovery", "create_segment", "create_flow", "custom_segment_upload", "telegram_post")


def init_tables() -> None:
    conn = get_db()
    conn.execute("""CREATE TABLE IF NOT EXISTS slack_posts (id INTEGER PRIMARY KEY AUTOINCREMENT, proposal_id INTEGER UNIQUE, channel TEXT, ts TEXT, status TEXT DEFAULT 'open',
                    posted_at TEXT DEFAULT CURRENT_TIMESTAMP, decided_by TEXT, decided_at TEXT)""")
    conn.commit(); conn.close()


def token() -> str:
    return (get_setting("slack_bot_token", "") or "").strip()


def channel() -> str:
    return (get_setting("slack_channel_id", "") or "").strip()


def approvers() -> List[str]:
    return [x.strip() for x in (get_setting("slack_approvers", "") or "").split(",") if x.strip()]


def configured() -> bool:
    return bool(token() and channel())


def enabled() -> bool:
    return configured() and str(get_setting("slack_ask_approval", "on")).lower() != "off"


def _api(method: str, body: Optional[Dict[str, Any]] = None, get: bool = False) -> Dict[str, Any]:
    tok = token()
    if not tok:
        raise ValueError("no Slack bot token is saved")
    s = guarded_session("slack")
    url = f"https://slack.com/api/{method}"
    h = {"Authorization": f"Bearer {tok}"}
    r = s.get(url, headers=h, params=body or {}, timeout=15) if get else s.post(url, headers={**h, "Content-Type": "application/json; charset=utf-8"}, json=body or {}, timeout=15)
    try:
        d = r.json()
    except ValueError:
        d = {}
    if r.status_code >= 400 or not d.get("ok"):
        raise ValueError(redact(str(d.get("error") or f"HTTP {r.status_code}"))[:160])
    return d


def status() -> Dict[str, Any]:
    init_tables(); conn = get_db()
    open_n = conn.execute("SELECT COUNT(*) FROM slack_posts WHERE status='open'").fetchone()[0]
    last = conn.execute("SELECT proposal_id, status, decided_by, decided_at FROM slack_posts ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    return {"token_set": bool(token()), "channel_set": bool(channel()), "approvers": len(approvers()), "configured": configured(), "on": enabled(), "awaiting": int(open_n),
            "last": dict(last) if last else None}


def _card(p: Dict[str, Any]) -> Dict[str, Any]:
    pl = p.get("payload") or {}
    kind = p.get("kind")
    v = (pl.get("variants") or [{}])[0] if isinstance(pl.get("variants"), list) else {}
    title = {"create_campaign": f"Campaign draft · {pl.get('name', '')}", "alert_send": f"Market alert · {pl.get('title', '')}", "ma2_discovery": "Market alerts · permission to fire",
             "create_segment": f"Cohort · {pl.get('name', '')}", "telegram_post": f"Community post · {pl.get('product') or 'all'}"}.get(kind, p.get("title") or kind)
    if kind == "telegram_post":
        pl = {**pl, "title": "Telegram community channel", "body": pl.get("text", "")}
    copy = f"*{pl.get('title') or v.get('title') or ''}*\n{pl.get('body') or v.get('body') or ''}".strip()
    who = pl.get("target_segment") or ", ".join(pl.get("cohorts") or []) or pl.get("audience") or (pl.get("audience") or {}).get("segment_name") if isinstance(pl.get("audience"), dict) else pl.get("audience") or ""
    facts = [f"id `{p['id']}` · {kind}", f"to: {who or 'see draft'}"]
    goal = pl.get("goal") or {}
    if goal:
        facts.append(f"holdout {goal.get('control_group_pct', '?')}% · KPI {goal.get('primary_kpi', '?')} · read after {goal.get('measurement_window_days', '?')} days")
    if kind == "alert_send":
        facts.append(f"{pl.get('signal', '')} · {pl.get('token', '')} · goes stale at {str(pl.get('stale_at') or '')[11:16]} IST")
    text = f"{title}\n{copy}\n" + " · ".join(facts)
    blocks = [{"type": "header", "text": {"type": "plain_text", "text": redact(title)[:150]}},
              {"type": "section", "text": {"type": "mrkdwn", "text": redact(copy)[:2900] or "_no copy_"}},
              {"type": "context", "elements": [{"type": "mrkdwn", "text": redact(" · ".join(facts))[:2900]}]},
              {"type": "section", "text": {"type": "mrkdwn", "text": f"Reply *approve* or *reject* in this thread (or react ✅ / ❌). Rationale: {redact(str(p.get('rationale') or ''))[:400]}"}}]
    return {"text": redact(text)[:3000], "blocks": blocks}


def post_proposal(pid: int) -> Dict[str, Any]:
    """Post one pending proposal for approval. Idempotent per proposal; silent when Slack is not set up."""
    from . import approvals
    if not enabled():
        return {"ok": False, "skipped": "slack not configured"}
    p = approvals.get_proposal(pid)
    if not p or p.get("status") != "pending" or p.get("kind") not in ASK_KINDS:
        return {"ok": False, "skipped": "not a pending proposal Slack asks about"}
    init_tables(); conn = get_db()
    if conn.execute("SELECT 1 FROM slack_posts WHERE proposal_id=?", (pid,)).fetchone():
        conn.close(); return {"ok": True, "already": True}
    conn.close()
    try:
        r = _api("chat.postMessage", {"channel": channel(), **_card(p), "unfurl_links": False})
    except Exception as e:
        audit("slack.post_failed", {"proposal_id": pid, "error": redact(str(e))[:160]}, actor="slack")
        return {"ok": False, "error": redact(str(e))[:160]}
    conn = get_db()
    conn.execute("INSERT OR IGNORE INTO slack_posts (proposal_id, channel, ts) VALUES (?,?,?)", (pid, r.get("channel") or channel(), r.get("ts")))
    conn.commit(); conn.close()
    audit("slack.asked", {"proposal_id": pid, "kind": p["kind"], "ts": r.get("ts")}, actor="slack")
    return {"ok": True, "ts": r.get("ts")}


def _reply(ch: str, ts: str, text: str) -> None:
    try:
        _api("chat.postMessage", {"channel": ch, "thread_ts": ts, "text": redact(text)[:2000]})
    except Exception:
        pass


def _verdict(ch: str, ts: str) -> Optional[Dict[str, str]]:
    """The first answer from an approver: a thread reply or a reaction on the post."""
    allow = set(approvers())
    d = _api("conversations.replies", {"channel": ch, "ts": ts, "limit": 50}, get=True)
    msgs = d.get("messages") or []
    if msgs:
        for rx in msgs[0].get("reactions") or []:
            users = [u for u in (rx.get("users") or []) if u in allow]
            if users and rx.get("name") in ("white_check_mark", "heavy_check_mark", "+1", "thumbsup"):
                return {"decision": "approve", "user": users[0], "how": f":{rx['name']}:"}
            if users and rx.get("name") in ("x", "-1", "thumbsdown", "no_entry"):
                return {"decision": "reject", "user": users[0], "how": f":{rx['name']}:"}
    for m in msgs[1:]:
        u = m.get("user") or ""
        if u not in allow:
            continue
        t = str(m.get("text") or "")
        if APPROVE.match(t):
            return {"decision": "approve", "user": u, "how": t[:60]}
        if REJECT.match(t):
            return {"decision": "reject", "user": u, "how": t[:60]}
    return None


def poll(limit: int = 20) -> Dict[str, Any]:
    """Every minute: read each open thread; an approver's word executes or rejects the proposal, and the thread is told what happened."""
    from . import approvals
    if not enabled():
        return {"ok": True, "skipped": "slack off"}
    if not approvers():
        return {"ok": False, "error": "slack_approvers is empty: nobody's word counts until Slack user ids are listed"}
    init_tables(); conn = get_db()
    rows = [dict(r) for r in conn.execute("SELECT * FROM slack_posts WHERE status='open' ORDER BY id LIMIT ?", (limit,)).fetchall()]
    conn.close()
    done = []; errors = []
    for row in rows:
        p = approvals.get_proposal(row["proposal_id"])
        if not p or p.get("status") != "pending":                     # decided elsewhere (Today, CLI) or expired
            _close(row["proposal_id"], (p or {}).get("decided_by") or "elsewhere", (p or {}).get("status") or "gone")
            _reply(row["channel"], row["ts"], f"Decided elsewhere: {(p or {}).get('status')} by {(p or {}).get('decided_by') or '?'}.")
            continue
        try:
            v = _verdict(row["channel"], row["ts"])
        except Exception as e:
            errors.append(redact(str(e))[:120]); continue
        if not v:
            continue
        who = f"slack:{v['user']}"
        try:
            if v["decision"] == "approve":
                r = approvals.approve_and_execute(p["id"], decided_by=who, note=f"approved in Slack ({v['how']})")
                st = r.get("status")
                _reply(row["channel"], row["ts"], f"Approved by <@{v['user']}> → {st}." + (f" Error: {str(r.get('error'))[:300]}" if st == "failed" else " Done in MoEngage." if st == "executed" else ""))
            else:
                r = approvals.reject(p["id"], note=f"rejected in Slack ({v['how']})", decided_by=who)
                st = "rejected"
                _reply(row["channel"], row["ts"], f"Rejected by <@{v['user']}>. Nothing was sent.")
            _close(p["id"], who, st)
            done.append({"proposal_id": p["id"], "decision": v["decision"], "status": st, "by": who})
        except approvals.ApprovalError as e:
            _reply(row["channel"], row["ts"], f"Cannot run yet: {redact(str(e))[:300]}. It stays pending.")
            errors.append(f"#{p['id']}: {redact(str(e))[:120]}")
        except Exception as e:
            errors.append(f"#{p['id']}: {redact(str(e))[:120]}")
    if done or errors:
        audit("slack.polled", {"decided": len(done), "errors": len(errors)}, actor="slack")
    return {"ok": not errors, "decided": done, "errors": errors[:5], "open": len(rows) - len(done)}


def _close(pid: int, who: str, st: str) -> None:
    conn = get_db()
    conn.execute("UPDATE slack_posts SET status=?, decided_by=?, decided_at=CURRENT_TIMESTAMP WHERE proposal_id=?", (st, who, pid))
    conn.commit(); conn.close()


def hello(actor: str = "user") -> Dict[str, Any]:
    try:
        me = _api("auth.test", {})
        _api("chat.postMessage", {"channel": channel(), "text": "The CLM engine is connected. Drafts and market alerts will be posted here for approval; an approver replies *approve* or *reject* in the thread."})
        audit("slack.hello", {"ok": True}, actor=actor)
        return {"ok": True, "bot": me.get("user"), "team": me.get("team")}
    except Exception as e:
        return {"ok": False, "error": redact(str(e))[:200]}
