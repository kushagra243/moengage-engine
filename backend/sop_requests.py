"""
"We need an SOP for this" — the request → draft → peer-approval path.

Anyone (any team, or the brain when it finds a gap) asks for a procedure that
does not exist yet. The brain drafts it framework-complete from the product
treatment, the lifecycle playbook and the India rules, and the draft goes to
**peer sign-off**: two colleagues other than the requester must approve before
it can be written into the library, and the review council (compliance,
experimentation, analyst) leaves its verdict on the same proposal first.

  request()  a need, in plain words, with the product and stage it belongs to
  draft()    the brain turns it into a full SOP spec + a `sop_new` proposal
  signoff()  peers approve or ask for changes; `sop_peer_signoffs` (default 2)
  approve    the normal queue writes it into the library as version 1
"""
from __future__ import annotations
import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .database import get_db, get_setting
from .security import audit, redact

STATUSES = ("open", "drafted", "changes_requested", "delivered", "declined")
# a sensible starting sequence per lifecycle stage; the brain edits it, peers approve it
TEMPLATES: Dict[str, Dict[str, Any]] = {
    "acquired_verified": {"type": "onboarding", "kpi": "kyc_completion_rate_72h", "target": "+6 pp vs holdout", "guard": "unsubscribe_rate", "days": 5,
                          "steps": [(0, "in-app", "Finish verification — what is left and why", "Progress framing (2 of 3 done); the exact document that failed; no fear framing."),
                                    (1, "push", "Document help at the step that failed", "Reason-specific tip (blurry PAN, lighting for selfie). Tool, not a reminder."),
                                    (3, "whatsapp", "Utility nudge with a support hand-off", "Utility template (opted-in): status + one tap to support after the second failure.")]},
    "verified_funded": {"type": "onboarding", "kpi": "first_deposit_rate_7d", "target": "+3 pp vs holdout", "guard": "unsubscribe_rate", "days": 7,
                        "steps": [(0, "in-app", "UPI deposit in about 20 seconds", "Rails: UPI, IMPS fallback, bank-timeout explanation. Fees and 1% TDS stated plainly. No bonus."),
                                  (2, "push", "Money is never stuck — instant withdrawals", "Objection removal; tool is the withdrawals screen."),
                                  (5, "email", "What a small first deposit lets you do", "Education, fee transparency, ASCI disclaimer in the footer.")]},
    "funded_activated": {"type": "activation", "kpi": "first_trade_rate_7d", "target": "+4 pp vs holdout", "guard": "notification_disable_rate", "days": 7,
                         "steps": [(0, "in-app", "Three ways to start, you pick the asset", "Guided first action from the user's own watchlist; never name an asset."),
                                   (1, "push", "Follow before you trade", "Alerts as the low-anxiety first action."),
                                   (3, "email", "Fees and TDS on your first trade", "Worked example; disclaimer in the footer.")]},
    "activated_habitual": {"type": "activation", "kpi": "second_trade_within_7d", "target": "+3 pp vs holdout", "guard": "notification_disable_rate", "days": 7,
                           "steps": [(1, "push", "One more action makes it a habit", "Two tool paths (alert, recurring buy); no asset recommendation."),
                                     (3, "cards", "Your week so far, in your own numbers", "Own-numbers recap in the inbox rather than a push.")]},
    "habitual_core": {"type": "retention", "kpi": "products_per_user", "target": "+0.2 vs holdout", "guard": "unsubscribe_rate", "days": 14,
                      "steps": [(0, "email", "What this product does for you, with your numbers", "Education first; intent-led, never an upsell lure."),
                                (7, "in-app", "The one tool that makes it safer", "Risk or planning tool relevant to the product.")]},
    "slipping": {"type": "retention", "kpi": "trade_frequency_recovery_14d", "target": "+5 pp vs holdout", "guard": "unsubscribe_rate", "days": 14,
                 "steps": [(0, "email", "Portfolio review from your own numbers", "Service tone, no offer; cause-aware (market, friction, loss)."),
                           (7, "in-app", "The habit tool you stopped using", "Re-entry through a tool, not a trade.")]},
    "dormant_activated": {"type": "winback", "kpi": "reactivation_rate_14d", "target": "+1 pp vs holdout", "guard": "unsubscribe_rate", "days": 21,
                          "steps": [(0, "email", "What changed since you last logged in", "Facts only, cause-matched; never an offer to a loss-dormant user."),
                                    (7, "push", "One tool to come back through", "Watchlist or alert, not a trade prompt.")]},
    "churned": {"type": "winback", "kpi": "reactivation_rate_30d", "target": "+1 pp vs holdout", "guard": "unsubscribe_rate", "days": 30,
                "steps": [(0, "email", "A single, honest re-introduction", "One attempt per month; facts, no incentive.")]},
}
DEFAULT = TEMPLATES["activated_habitual"]


def init_tables() -> None:
    conn = get_db()
    conn.execute("""CREATE TABLE IF NOT EXISTS sop_requests (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, requested_by TEXT, title TEXT, need TEXT,
                    product TEXT, team TEXT, transition TEXT, situation TEXT, status TEXT DEFAULT 'open', proposal_id INTEGER, sop_id TEXT, note TEXT, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS sop_signoffs (id INTEGER PRIMARY KEY AUTOINCREMENT, request_id INTEGER, actor TEXT, verdict TEXT, note TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
    conn.commit(); conn.close()


def peers_required() -> int:
    try:
        return max(0, int(get_setting("sop_peer_signoffs", "2") or 2))
    except Exception:
        return 2


def _row(r) -> Dict[str, Any]:
    d = dict(r)
    d["signoffs"] = signoffs(d["id"])
    d["peers_required"] = peers_required()
    d["peers_ok"] = len([s for s in d["signoffs"] if s["verdict"] == "approve"]) >= d["peers_required"]
    return d


def create(title: str, need: str, product: str = "all", team: str = "", transition: str = "", situation: str = "", requested_by: str = "user") -> Dict[str, Any]:
    """Anyone asks for a procedure that does not exist. No model, no writes beyond the request row."""
    init_tables()
    title = (title or "").strip()[:160]; need = (need or "").strip()[:2000]
    if len(title) < 6 or len(need) < 20:
        return {"error": "give the request a title and a couple of sentences about the need (who it is for and what should happen)"}
    conn = get_db()
    cur = conn.execute("INSERT INTO sop_requests (requested_by, title, need, product, team, transition, situation) VALUES (?,?,?,?,?,?,?)",
                       (requested_by, title, need, product or "all", team or "", transition or "", (situation or "")[:1000]))
    rid = cur.lastrowid; conn.commit(); conn.close()
    audit("sop_request.created", {"id": rid, "title": title, "product": product, "transition": transition}, actor=requested_by)
    return {"ok": True, "id": rid, "status": "open", "next": "the brain drafts it (draft), then two peers sign off before it enters the library"}


def list_requests(status: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
    init_tables(); conn = get_db()
    q = "SELECT * FROM sop_requests" + (" WHERE status=?" if status else "") + " ORDER BY id DESC LIMIT ?"
    rows = [_row(r) for r in conn.execute(q, ((status, limit) if status else (limit,))).fetchall()]
    conn.close(); return rows


def get_request(rid: int) -> Optional[Dict[str, Any]]:
    init_tables(); conn = get_db(); r = conn.execute("SELECT * FROM sop_requests WHERE id=?", (rid,)).fetchone(); conn.close()
    return _row(r) if r else None


def signoffs(rid: int) -> List[Dict[str, Any]]:
    conn = get_db(); rows = [dict(r) for r in conn.execute("SELECT actor, verdict, note, created_at FROM sop_signoffs WHERE request_id=? ORDER BY id", (rid,)).fetchall()]; conn.close()
    return rows


def signoff(rid: int, actor: str = "user", verdict: str = "approve", note: str = "") -> Dict[str, Any]:
    """Peer sign-off. The requester cannot sign off their own request; one vote per person."""
    req = get_request(rid)
    if not req:
        return {"error": "unknown request"}
    if verdict not in ("approve", "changes"):
        return {"error": "verdict must be approve or changes"}
    if actor and actor == req.get("requested_by") and peers_required() > 0:
        return {"error": "peer sign-off must come from someone other than the requester"}
    conn = get_db()
    conn.execute("DELETE FROM sop_signoffs WHERE request_id=? AND actor=?", (rid, actor))
    conn.execute("INSERT INTO sop_signoffs (request_id, actor, verdict, note) VALUES (?,?,?,?)", (rid, actor, verdict, (note or "")[:500]))
    if verdict == "changes":
        conn.execute("UPDATE sop_requests SET status='changes_requested', note=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", ((note or "")[:500], rid))
    conn.commit(); conn.close()
    audit("sop_request.signoff", {"id": rid, "verdict": verdict}, actor=actor)
    out = get_request(rid)
    if out and out.get("proposal_id"):
        try:
            from . import approvals
            approvals.add_comment(out["proposal_id"], f"[signoff:{actor}] {verdict}" + (f" · {note}" if note else ""), actor=actor)
        except Exception:
            pass
    ap = len([x for x in (out or {}).get("signoffs") or [] if x["verdict"] == "approve"])
    need = peers_required()
    return {"ok": True, "request": out, "approvals": ap, "peers_required": need, "ready": ap >= need,
            "status": (out or {}).get("status"),
            "note": ("the proposal can be approved now" if ap >= need else f"{need - ap} more peer sign-off(s) needed before the SOP can enter the library")}


# ── drafting ──────────────────────────────────────────────────────────────────
def _scaffold(req: Dict[str, Any]) -> Dict[str, Any]:
    """A framework-complete SOP spec built from the request, the product treatment and the playbook."""
    from .products import PRODUCTS
    from .sops import STANDARD_EXCLUSIONS
    from . import sop_improve
    t = str(req.get("transition") or "").strip() or "activated_habitual"
    tpl = TEMPLATES.get(t) or DEFAULT
    pid = str(req.get("product") or "all")
    prod = PRODUCTS.get(pid)
    slug = re.sub(r"[^a-z0-9]+", "_", (req.get("title") or "new procedure").lower()).strip("_")[:44]
    steps = []
    for day, ch, purpose, brief in tpl["steps"]:
        st = {"day": day, "channel": ch, "purpose": purpose[:80], "copy_brief": brief, "send_time_ist": "11:00" if ch in ("email", "whatsapp", "in-app", "cards") else "19:00"}
        if day > 0:
            st["condition"] = f"no {sop_improve.CONVERSION_STOP.get(t, 'conversion')} yet"
        steps.append(st)
    if prod:
        steps[0]["copy_brief"] = (steps[0]["copy_brief"] + f" Product lens: {prod['lens']}. Never: {', '.join(prod['never'])}.")[:400]
    excl = list(STANDARD_EXCLUSIONS)
    if pid in ("perps_crypto", "perps_us_stocks", "perps_indices", "perps_commodities", "options"):
        excl.append("liquidated in last 14d")
    kpi_days = sop_improve._kpi_window(tpl["kpi"]) or 7
    spec = {
        "id": f"sop_{slug}", "name": (req.get("title") or "New procedure")[:90], "campaign_type": tpl["type"], "transition": t,
        "objective": (req.get("need") or "")[:400],
        "user": (req.get("situation") or req.get("need") or "")[:200],
        "audience": {"segment_family": (req.get("team") or "").upper()[:24] or "TO_BE_DEFINED", "description": (req.get("situation") or req.get("need") or "")[:200],
                     "exclusions": excl, "min_reach": 200, "jurisdictions_excluded": []},
        "steps": steps, "duration_days": tpl["days"],
        "frequency": {"cadence": "event_triggered" if t in ("verified_funded", "funded_activated", "acquired_verified") else "scheduled", "max_messages_per_user_per_week": min(4, len(steps) + 1)},
        "holdout_pct": 20, "primary_kpi": tpl["kpi"], "target": tpl["target"], "guardrail_metric": tpl["guard"],
        "measurement_window_days": max(kpi_days, tpl["days"]),
        "kill_criteria": ["delivery_rate < 85%", f"{tpl['guard']} > 0.5%"],
        "ideas": (sop_improve.IDEA_BANK.get(tpl["type"]) or [])[:2],
        "compliance": {"disclaimer_channels": sorted({s["channel"] for s in steps} & {"email", "whatsapp", "in-app", "cards", "sms"}),
                       "banned_angles": ["bonus"] + (["leverage_upsell", "size_up", "first_futures_trade"] if pid.startswith("perps") or pid == "options" else [])},
        "checks": {"preflight": ["framework", "segment_exists", "exclusions_present", "brief_per_step", "compliance", "limits"], "midflight": ["kill_criteria_daily", "peace_index"], "postflight": ["readout_with_ci", "lesson_to_feed"]},
        "source": "request", "version": 1,
    }
    return spec


def _polish(spec: Dict[str, Any], req: Dict[str, Any]) -> Dict[str, Any]:
    """Optional: let the bulk tier sharpen purposes and copy briefs. Structure, limits and compliance stay as scaffolded."""
    from .llm.provider import LLMClient, llm_settings, LLMError
    cfg = llm_settings()
    if not (cfg["api_key"] or cfg["provider"] == "claude_cli"):
        return spec
    try:
        client = LLMClient(); client.purpose = "copy"
        payload = {"request": {k: req.get(k) for k in ("title", "need", "product", "transition", "situation")},
                   "steps": [{"day": s["day"], "channel": s["channel"], "purpose": s["purpose"], "copy_brief": s["copy_brief"]} for s in spec["steps"]]}
        r = client.chat([{"role": "system", "content": "You are CoinDCX's lifecycle copy lead. Rewrite each step's purpose (≤ 70 chars) and copy_brief (≤ 220 chars) so they are specific to the request. Fact + tool, no venue names, no direction, no incentives, no leverage lures; India rules apply. Return STRICT JSON: {\"steps\":[{\"purpose\":\"\",\"copy_brief\":\"\"}]} with one entry per step, same order."},
                         {"role": "user", "content": json.dumps(payload, default=str)[:4000]}], tools=None, max_tokens=700, temperature=0.2, tier="bulk")
        text = (r.get("content") or ""); s, e = text.find("{"), text.rfind("}")
        got = json.loads(text[s:e + 1]).get("steps") if s >= 0 else None
        if isinstance(got, list) and len(got) == len(spec["steps"]):
            for st, g in zip(spec["steps"], got):
                if g.get("purpose"):
                    st["purpose"] = str(g["purpose"])[:80]
                if g.get("copy_brief"):
                    st["copy_brief"] = str(g["copy_brief"])[:400]
            spec["_polished_by"] = r.get("model")
    except (LLMError, ValueError, json.JSONDecodeError):
        pass
    except Exception:
        pass
    return spec


def draft(rid: int, actor: str = "agent", polish: bool = True) -> Dict[str, Any]:
    """Draft the SOP and queue it as a `sop_new` proposal that peers sign off before it enters the library."""
    from . import approvals
    from .sops import sop_check, get_sop
    req = get_request(rid)
    if not req:
        return {"error": "unknown request"}
    if req.get("status") == "delivered":
        return {"error": f"already delivered as {req.get('sop_id')}"}
    spec = _scaffold(req)
    if get_sop(spec["id"]):
        spec["id"] = f"{spec['id']}_v2"
    if polish:
        spec = _polish(spec, req)
    chk = sop_check(spec)
    if not chk["ok"]:
        return {"error": "the draft does not pass the framework check", "problems": chk["problems"], "spec": spec}
    india = {}
    try:
        from . import sop_india
        india = sop_india.review_one(spec)
    except Exception:
        pass
    p = approvals.propose("sop_new", f"New SOP: {spec['name']}", {"request_id": rid, "spec": spec},
                          rationale=f"Requested by {req.get('requested_by')} for {req.get('product')} / {req.get('transition') or 'unspecified stage'}: {req.get('need')[:300]}",
                          risk="low", created_by=actor)
    conn = get_db()
    conn.execute("UPDATE sop_requests SET status='drafted', proposal_id=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (p["id"], rid))
    conn.commit(); conn.close()
    audit("sop_request.drafted", {"id": rid, "proposal": p["id"], "sop": spec["id"]}, actor=actor)
    return {"ok": True, "request_id": rid, "proposal_id": p["id"], "sop_id": spec["id"], "spec": spec, "framework": chk,
            "india_fit": {"score": india.get("score"), "status": india.get("status"), "flags": [f["rule"] for f in (india.get("flags") or [])]},
            "peers_required": peers_required(), "next": f"{peers_required()} peer sign-off(s), then approve the proposal to write it into the library"}


def suggest() -> Dict[str, Any]:
    """Requests worth raising: uncovered product × stage cells and questions the library could not answer."""
    from . import sop_catalog
    out: List[Dict[str, Any]] = []
    try:
        for g in (sop_catalog.gaps() or {}).get("gaps", [])[:40]:
            out.append({"source": "product coverage", "title": g["title"], "product": g["product"], "transition": g["transition"],
                        "need": f"{g['why']} The message for this product is: {g['lens']}. Cadence {g['cadence']}. Never: {', '.join(g['never'])}."})
    except Exception:
        pass
    try:
        from . import sopqa
        for q in (sopqa.gaps(15) or {}).get("gaps", []):
            out.append({"source": "unanswered question", "title": q["question"][:90], "product": "all", "transition": "",
                        "need": f"The team asked '{q['question']}' {q['asked']}× and the library could not answer it."})
    except Exception:
        pass
    existing = {r["title"].lower() for r in list_requests(limit=200)}
    return {"suggestions": [s for s in out if s["title"].lower() not in existing][:30], "note": "one click turns a suggestion into a request; the brain drafts it and peers sign it off"}


# ── executor: peers first, then the library ──────────────────────────────────
def _peer_gate(payload: Dict[str, Any]) -> None:
    """Checked when someone approves, not when the draft is queued — the draft has to exist for peers to read it.
    Raised from _validate on the approval path, so a premature approval leaves the proposal pending."""
    need = peers_required()
    if not need:
        return
    rid = payload.get("request_id")
    votes = signoffs(int(rid)) if rid else []
    approve = [s for s in votes if s["verdict"] == "approve"]
    if len(approve) < need:
        changed = [s["actor"] for s in votes if s["verdict"] == "changes"]
        raise ValueError(f"needs {need} peer sign-off(s) before approval — {len(approve)} so far"
                         + (f"; changes requested by {', '.join(changed)}" if changed else ""))


def _validate(payload: Dict[str, Any]) -> None:
    from .sops import sop_check
    spec = payload.get("spec") or {}
    if not spec.get("id") or not spec.get("name"):
        raise ValueError("draft is missing the SOP spec")
    chk = sop_check(spec)
    if not chk["ok"]:
        raise ValueError("draft fails the framework check: " + "; ".join(chk["problems"][:3]))
    if payload.get("_approving"):
        _peer_gate(payload)


def _preview(payload: Dict[str, Any]) -> Dict[str, Any]:
    from . import docs_io
    from .sops import sop_check
    spec = payload.get("spec") or {}
    rid = payload.get("request_id")
    sg = signoffs(int(rid)) if rid else []
    india = {}
    try:
        from . import sop_india
        india = sop_india.review_one(spec)
    except Exception:
        pass
    return {"status": "ready", "sop_id": spec.get("id"), "name": spec.get("name"), "steps": len(spec.get("steps") or []), "kpi": spec.get("primary_kpi"),
            "holdout_pct": spec.get("holdout_pct"), "framework_ok": sop_check(spec)["ok"], "india_fit": india.get("score"),
            "peers_required": peers_required(), "signoffs": sg, "peers_ok": len([s for s in sg if s["verdict"] == "approve"]) >= peers_required(),
            "markdown": docs_io.sop_markdown({**spec, "version": 1}, machine_block=False)[:4000]}


def _execute(payload: Dict[str, Any]) -> Dict[str, Any]:
    from .sops import define_sop
    _peer_gate(payload)
    spec = payload["spec"]
    res = define_sop(spec, author=f"request:{payload.get('_approved_by', 'user')}")
    if not res.get("ok"):
        raise ValueError("; ".join(res.get("problems") or ["could not write the SOP"]))
    rid = payload.get("request_id")
    if rid:
        conn = get_db()
        conn.execute("UPDATE sop_requests SET status='delivered', sop_id=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (spec["id"], int(rid)))
        conn.commit(); conn.close()
    audit("sop_request.delivered", {"request": rid, "sop": spec["id"], "version": res.get("version")}, actor=payload.get("_approved_by", "user"))
    return {"ok": True, "sop_id": spec["id"], "version": res.get("version"), "request_id": rid}


def register() -> None:
    from .approvals import register_executor
    register_executor("sop_new", _execute, _preview, _validate)
