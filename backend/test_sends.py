"""
Test sends: every campaign can be seen on a real phone before anyone else gets it.

A short list of test users (up to ten, MoEngage's own limit) is kept once and used by default everywhere: the Alerts
screen can send any alert's copy to them, any campaign draft on Today can be test-sent before it is approved, and when
an approved draft is created in MoEngage the same people get a test of it automatically.

MoEngage's documented `POST /v5/campaigns/test` does the sending: inline (the copy itself) or by `draft_id`, to
recipients named by email (`USER_ATTRIBUTE_USER_EMAIL`) or by customer id (`USER_ATTRIBUTE_UNIQUE_ID`).

Privacy: the list is personal data, so it is stored under a `_secret` key (Fernet, Keychain-held key), the page only
ever sees masked values and a count, no agent tool can read or use it, `redact()` masks it in every log, and the
proposal that records a test send stores the count and the masked list, never the addresses. A test send is a MoEngage
write, so it is a proposal of kind `test_send` that the operator's own click creates and approves in one step: it is in
the queue's history and the audit log like every other write, and the agent cannot trigger one.
"""
from __future__ import annotations
import json
import re
import uuid
from typing import Any, Dict, List, Optional

from .database import get_setting, set_setting
from .security import audit, redact

SETTING = "moengage_test_users_secret"
MAX = 10
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class TestSendError(ValueError):
    pass


def users() -> List[str]:
    try:
        v = json.loads(get_setting(SETTING, "") or "[]")
        return [str(x) for x in v if str(x).strip()][:MAX]
    except Exception:
        return []


def _mask(v: str) -> str:
    if "@" in v:
        name, dom = v.split("@", 1)
        return (name[:1] + "•••" + (name[-1:] if len(name) > 2 else "")) + "@" + dom
    return v[:3] + "•••" + v[-2:] if len(v) > 6 else "•••"


def meta() -> Dict[str, Any]:
    u = users()
    return {"count": len(u), "masked": [_mask(x) for x in u], "max": MAX, "by_email": sum(1 for x in u if "@" in x), "by_id": sum(1 for x in u if "@" not in x)}


def save_users(values: Any, actor: str = "user") -> Dict[str, Any]:
    raw = values if isinstance(values, list) else re.split(r"[\n,;]+", str(values or ""))      # never on spaces: "+91 98765 43210" must be judged whole
    out: List[str] = []
    for x in raw:
        x = str(x).strip()
        if not x:
            continue
        if " " in x and "@" in x:
            raise TestSendError("one tester per line")
        if "@" in x and not EMAIL.match(x):
            raise TestSendError(f"{_mask(x)} does not look like an email address")
        if re.fullmatch(r"\+?\d[\d\s().-]{8,}", x):
            raise TestSendError("phone numbers are not accepted: use the email or the customer id MoEngage knows the tester by")
        if x.lower() not in [o.lower() for o in out]:
            out.append(x)
    if len(out) > MAX:
        raise TestSendError(f"MoEngage sends a test to at most {MAX} people at a time; {len(out)} were given")
    set_setting(SETTING, json.dumps(out))
    audit("test_users.saved", {"count": len(out)}, actor=actor)
    return meta()


# ── the MoEngage request ──────────────────────────────────────────────────────
def _bodies(title: str, body: str, name: str, draft_id: str = "", deeplink: str = "") -> List[Dict[str, Any]]:
    """One request per identifier type (MoEngage takes one type per call)."""
    from .moengage.executors import _platforms, _push_content
    u = users()
    groups = [("USER_ATTRIBUTE_USER_EMAIL", [x for x in u if "@" in x]), ("USER_ATTRIBUTE_UNIQUE_ID", [x for x in u if "@" not in x])]
    out = []
    for ident, vals in groups:
        if not vals:
            continue
        b: Dict[str, Any] = {"request_id": str(uuid.uuid4()), "test_campaign_meta": {"identifier": ident, "identifier_values": vals}}
        if draft_id:
            b["draft_id"] = draft_id
        else:
            b.update({"channel": "PUSH", "basic_details": {"name": name[:80], "platforms": [p for p in _platforms() if p in ("ANDROID", "IOS", "WEB")] or ["ANDROID"]},
                      "campaign_content": _push_content({"title": title, "body": body, "deeplink": deeplink})})
        out.append(b)
    return out


def _validate(p: Dict[str, Any]) -> None:
    if not users():
        raise ValueError("no test users are saved yet: add up to ten emails or customer ids first")
    if p.get("draft_id"):
        return
    title, body = str(p.get("title") or "").strip(), str(p.get("body") or "").strip()
    if not title or not body:
        raise ValueError("a test needs a title and a body")
    if "{{" in title + body:
        raise ValueError("the copy still has placeholders; a test must show real words")
    from .alerts2 import rules
    bad = [x for x in rules.lint(title, body) if x["severity"] in ("high", "medium")]
    if bad:
        raise ValueError("the copy breaks a rule and is not sent even as a test: " + "; ".join(f"{x['rule']} ({x['snippet']})" for x in bad[:3]))


def _preview(p: Dict[str, Any]) -> Dict[str, Any]:
    m = meta()
    return {"to": m["masked"], "count": m["count"], "title": p.get("title"), "body": p.get("body"), "draft_id": p.get("draft_id") or None, "endpoint": "POST /v5/campaigns/test"}


def _execute(p: Dict[str, Any]) -> Dict[str, Any]:
    from .moengage import MoEngageClient
    c = MoEngageClient()
    m = meta()
    bodies = _bodies(str(p.get("title") or ""), str(p.get("body") or ""), str(p.get("name") or "Test send"), str(p.get("draft_id") or ""), str(p.get("deeplink") or ""))
    if c.mock_mode:
        return {"success": True, "sent_to": m["count"], "status": "practice mode: recorded, nothing was sent", "_source": "mock"}
    api = c.api()
    if not api.has("campaigns"):
        raise ValueError("the Campaigns API key is needed for a test send")
    results = []
    for b in bodies:
        r = api.call("campaign_test_v5", body=b, write=True)
        d = r.get("data") if isinstance(r.get("data"), dict) else {}
        results.append({"http": r.get("status"), "keys": list(d.keys())[:8], "message": redact(str(d.get("message") or d.get("status") or ""))[:160]})
    return {"success": True, "sent_to": m["count"], "status": "test sent through MoEngage", "results": results, "_source": "live:public_api"}


def register() -> None:
    from .approvals import register_executor
    register_executor("test_send", _execute, _preview, _validate)


def send(title: str = "", body: str = "", name: str = "Test send", actor: str = "user", draft_id: str = "", deeplink: str = "", source: str = "") -> Dict[str, Any]:
    """The operator's click: queue the test and approve it in one step, so it is recorded like every other MoEngage write."""
    from . import approvals
    m = meta()
    payload = {"name": name[:80], "title": title[:120], "body": body[:1000], "draft_id": draft_id, "deeplink": deeplink, "to_count": m["count"], "to_masked": m["masked"], "source": source, "nonce": uuid.uuid4().hex[:8]}
    try:
        p = approvals.propose("test_send", f"Test send: {name[:60]} · {payload['nonce']}", payload, rationale=f"test to {m['count']} saved test user(s)", risk="low", created_by=actor)
        done = approvals.approve_and_execute(p["id"], decided_by=actor, note="test send from the operator's own click")
    except Exception as e:
        return {"ok": False, "error": redact(str(e))[:300]}
    ok = done.get("status") == "executed"
    res = done.get("result") or {}
    return {"ok": ok, "proposal_id": done["id"], "sent_to": res.get("sent_to", 0), "status": res.get("status") or "", "error": redact(str(done.get("error") or ""))[:300], "to": m["masked"]}


def after_draft_created(proposal: Dict[str, Any], result: Dict[str, Any], actor: str = "system") -> Optional[Dict[str, Any]]:
    """By default the saved test users get a test of every draft the moment it exists in MoEngage. Never fails the draft."""
    if str(get_setting("moengage_test_on_create", "true")).lower() == "false" or not users():
        return None
    cid = str(result.get("campaign_id") or "")
    pl = proposal.get("payload") or {}
    v = (pl.get("variants") or [{}])[0]
    try:
        if cid and not cid.startswith("mock_") and re.fullmatch(r"[0-9a-f]{24}", cid):
            return send(name=str(pl.get("name") or "draft"), actor=actor, draft_id=cid, source=f"proposal:{proposal.get('id')}")
        if "{{" in str(v.get("title", "")) + str(v.get("body", "")):
            return None                                   # a template of placeholders has nothing readable to show; the Alerts screen tests real copy
        return send(str(v.get("title") or ""), str(v.get("body") or ""), name=str(pl.get("name") or "draft"), actor=actor, source=f"proposal:{proposal.get('id')}")
    except Exception as e:
        return {"ok": False, "error": redact(str(e))[:200]}
