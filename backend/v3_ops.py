"""
The working half of the quiet terminal: Asks, Alerts and Engine.

The first five screens say what to decide. These three make sure a decision can actually run:

  asks()    every input the engine is missing, each as one question with its own field. A campaign draft short of a segment,
            a holdout or its copy; a workspace key; the creator email MoEngage insists on; the business event the alerts
            fire; the employee ids for direct sends. Answering writes the value to the right place (settings, encrypted
            when secret, or the pending proposal's payload), re-checks, and says what is now unblocked. Nothing is sent by
            answering: the human click on the decision still does that.
  alerts()  Market Alerts in the row-and-verb pattern: state in words, what stands between it and running, every push word
            for word, the cohorts, what went out today, and the stop control.
  engine()  connection, model, schedule and background jobs in words, with every setting editable in place.

Secrets are typed into a password field, posted to the loopback server, encrypted with the Keychain-held key and never
read back: the page only ever learns "set" or "not set".
"""
from __future__ import annotations
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from . import approvals
from .database import get_all_settings, get_setting
from .security import audit, redact
from .v3 import IST, plain

SaveFn = Callable[[Dict[str, Any]], Dict[str, Any]]

TRANSITIONS = ["acquired_verified", "verified_funded", "funded_activated", "activated_habitual", "habitual_expanded", "slipping_recovered", "dormant_reactivated"]
KPIS = ["first_deposit_rate_7d", "first_trade_rate_7d", "second_trade_rate_72h", "sessions_per_week", "trades_per_active_user", "d30_retention", "reactivation_rate_14d", "feature_adoption_rate"]
GUARDRAILS = ["unsubscribe_rate", "push_opt_out_rate", "uninstall_rate", "complaint_rate", "delivery_rate"]


# ── the questions a setting can turn into ─────────────────────────────────────
def _f(key: str, label: str, kind: str = "text", placeholder: str = "", options: Optional[List[str]] = None, value: str = "") -> Dict[str, Any]:
    return {"key": key, "label": label, "type": kind, "placeholder": placeholder, "options": options or [], "value": value}


SETTING_ASKS: List[Dict[str, Any]] = [
    {"key": "moengage_app_id", "title": "Tell the engine which MoEngage workspace is ours", "why": "Every call to MoEngage is signed with the workspace id. Without it the engine can only practise on sample data.",
     "where": "MoEngage → Settings → Account → APIs → Workspace ID (also called App ID)", "unblocks": "every campaign, cohort and report", "field": _f("moengage_app_id", "WORKSPACE ID", placeholder="e.g. ABCD1234EFGH5678")},
    {"key": "moengage_dc", "title": "Say which MoEngage data centre the workspace lives in", "why": "The engine talks to api-0X.moengage.com; the wrong number answers with an authentication error.",
     "where": "Look at the dashboard address: dashboard-03.moengage.com means 03", "unblocks": "every call to MoEngage", "field": _f("moengage_dc", "DATA CENTRE", placeholder="03")},
    {"key": "moengage_data_api_key", "title": "Add the Data API key so events and alerts can be sent", "why": "Market alerts, standing triggers and the heartbeat are all events posted with this key.",
     "where": "MoEngage → Settings → Account → APIs → Data API key", "unblocks": "market alerts and standing triggers", "field": _f("moengage_data_api_key", "DATA API KEY", "secret")},
    {"key": "moengage_campaign_key", "title": "Add the Campaigns API key so drafts can be created", "why": "Approving a campaign creates a real draft in MoEngage. Without this key the approval has nowhere to go.",
     "where": "MoEngage → Settings → Account → APIs → Campaigns API key", "unblocks": "every campaign draft", "field": _f("moengage_campaign_key", "CAMPAIGNS API KEY", "secret")},
    {"key": "moengage_segmentation_key", "title": "Add the Segmentation API key so cohorts can be created", "why": "New cohorts and the monthly uploads are created over the Segmentation API.",
     "where": "MoEngage → Settings → Account → APIs → Segmentation API key", "unblocks": "cohort drafts and uploads", "field": _f("moengage_segmentation_key", "SEGMENTATION API KEY", "secret")},
    {"key": "moengage_created_by", "title": "Give the dashboard email that should own the drafts", "why": "MoEngage refuses a campaign draft that does not name its creator, and the name must be a real dashboard login.",
     "where": "The email you sign in to the MoEngage dashboard with", "unblocks": "every campaign draft", "field": _f("moengage_created_by", "CREATOR EMAIL", "email", "you@coindcx.com")},
]


def _has(settings: Dict[str, str], key: str) -> bool:
    return str(settings.get(key + "_set", "")).lower() == "true" or bool(str(settings.get(key, "") or "").strip())


def _mock() -> bool:
    return get_setting("mock_mode", "true").lower() == "true"


def _connection_asks(settings: Dict[str, str]) -> List[Dict[str, Any]]:
    out = []
    mock = _mock()
    for a in SETTING_ASKS:
        if a["key"] == "moengage_dc" and (_has(settings, "moengage_dc") or _has(settings, "moengage_region")):
            continue
        if _has(settings, a["key"]):
            continue
        out.append({"id": f"setting:{a['key']}", "group": "CONNECTION", "tone": "amber" if mock else "magenta", "title": a["title"], "why": a["why"], "where": a["where"],
                    "unblocks": a["unblocks"], "fields": [a["field"]], "button": "SAVE AND CHECK"})
    provider = get_setting("llm_provider", "openrouter")
    if provider != "claude_cli" and not _has(settings, "llm_api_key"):
        out.append({"id": "setting:llm_api_key", "group": "CONNECTION", "tone": "amber", "title": "Add a model key so the brain can write and reason", "why": "Drafting copy, answering questions and the daily brief all need a model. Free models are used wherever they are good enough.",
                    "where": "openrouter.ai → Keys → Create key (starts with sk-or-)", "unblocks": "Ask, copy drafts, the daily brief and the alerts agent", "fields": [_f("llm_api_key", "MODEL KEY", "secret", "sk-or-…")], "button": "SAVE AND CHECK"})
    if mock:
        missing = [a["field"]["label"].title() for a in SETTING_ASKS if a["key"] not in ("moengage_segmentation_key", "moengage_dc") and not _has(settings, a["key"])]
        out.append({"id": "setting:mock_mode", "group": "CONNECTION", "tone": "amber", "title": "Leave practice mode and work on the real workspace",
                    "why": "In practice mode every approval is simulated on sample data and nothing reaches MoEngage. That is why no draft appears there."
                           + (f" Still needed first: {', '.join(missing)}." if missing else " Everything it needs is in place."),
                    "where": "This switch. It can be turned back at any time.", "unblocks": "real drafts, real cohorts, real alerts", "fields": [], "confirm": True,
                    "blocked_by": missing, "button": "GO LIVE ON THE REAL WORKSPACE"})
    return out


# ── market alerts ─────────────────────────────────────────────────────────────
def _alerts_asks(settings: Dict[str, str]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    try:
        from .alerts2 import discovery
        pre = {r["check"]: r for r in discovery.preflight()}
        d = discovery.internal_delivery()
        ev = pre.get("Business event") or {}
        if ev.get("ok") is False:
            names = ", ".join(c["event"] for c in discovery.cohorts()[:1]) or discovery.EVENT
            out.append({"id": "alerts:business_event", "group": "MARKET ALERTS", "tone": "magenta", "title": f"Create the business event {names} in MoEngage",
                        "why": plain(ev.get("detail") or "") + ". The engine fires this event and MoEngage's campaign listens for it, so it has to exist before the draft can be created.",
                        "where": "MoEngage → Settings → Business Events → Create. Attributes: title, body, token, product, signal, deep_link (all text).",
                        "unblocks": "the market alerts campaign draft", "fields": [], "confirm": True, "button": "I HAVE CREATED IT · CHECK AGAIN"})
        if d.get("requested") == "inform":
            if not _has(settings, "moengage_inform_key"):
                out.append({"id": "setting:moengage_inform_key", "group": "MARKET ALERTS", "tone": "amber", "title": "Add the Inform API key for direct alerts to employees",
                            "why": "The employee cohort is set to receive alerts through MoEngage Inform, which has its own key.", "where": "MoEngage → Settings → Account → APIs → Inform API key",
                            "unblocks": "direct alerts to the employee cohort", "fields": [_f("moengage_inform_key", "INFORM API KEY", "secret")], "button": "SAVE AND CHECK"})
            if not d.get("alert_id_set"):
                out.append({"id": "setting:ma2_inform_alert_id", "group": "MARKET ALERTS", "tone": "amber", "title": "Give the id of the Inform alert the engine should send through",
                            "why": "Inform sends through an alert template created once in the dashboard. Its push title and body should be the two personalised attributes title and body.",
                            "where": "MoEngage → Inform → the alert → Alert ID", "unblocks": "direct alerts to the employee cohort", "fields": [_f("ma2_inform_alert_id", "INFORM ALERT ID")], "button": "SAVE AND CHECK"})
            if not d.get("users"):
                out.append({"id": "alerts:employee_ids", "group": "MARKET ALERTS", "tone": "amber", "title": "List the employees who should receive the test alerts",
                            "why": "Direct sends need each employee's MoEngage customer id. Ids only: an email, phone number, PAN or Aadhaar is refused and nothing is saved.",
                            "where": "MoEngage → Segment → INTERNAL_EMPLOYEES → export the ID column", "unblocks": "direct alerts to the employee cohort",
                            "fields": [_f("user_ids", "CUSTOMER IDS · ONE PER LINE", "list", "cdx_100234\ncdx_100871")], "button": "SAVE THE LIST"})
        svc = pre.get("Engine as a service") or {}
        if svc.get("ok") is False:
            out.append({"id": "alerts:service", "group": "ENGINE", "tone": "dim", "title": "Keep the engine running when this window closes",
                        "why": "The engine watches the market and posts the alerts. Started from a terminal, it stops when the terminal does; installed as a service it restarts on its own.",
                        "where": "Run this once in the project folder.", "unblocks": "alerts that keep going overnight", "command": "./cli.py service install", "fields": [], "confirm": True, "button": "I HAVE RUN IT · CHECK AGAIN"})
    except Exception as e:
        audit("v3.asks_alerts_failed", {"error": redact(str(e))[:200]}, actor="system")
    return out


# ── drafts that cannot run yet ────────────────────────────────────────────────
def _get(d: Dict[str, Any], path: str) -> Any:
    cur: Any = d
    for part in path.split("."):
        cur = (cur or {}).get(part) if isinstance(cur, dict) else None
    return cur


CAMPAIGN_FIELDS: List[Dict[str, Any]] = [
    {"path": "target_segment", "q": "Which cohort should receive it", "field": lambda p: _f("target_segment", "COHORT NAME IN MOENGAGE", placeholder="e.g. KYC_APPROVED_NODEP")},
    {"path": "channel", "q": "Which channel it goes out on", "field": lambda p: _f("channel", "CHANNEL", "choice", options=["push", "email"])},
    {"path": "goal.transition", "q": "Which step of the lifecycle it is meant to move", "field": lambda p: _f("goal.transition", "LIFECYCLE STEP", "choice", options=TRANSITIONS)},
    {"path": "goal.primary_kpi", "q": "The one number it will be judged on", "field": lambda p: _f("goal.primary_kpi", "SUCCESS MEASURE", "choice", options=KPIS)},
    {"path": "goal.guardrail_metric", "q": "The number that must not get worse", "field": lambda p: _f("goal.guardrail_metric", "GUARDRAIL", "choice", options=GUARDRAILS)},
    {"path": "goal.control_group_pct", "q": "How many are held back so the lift can be read", "field": lambda p: _f("goal.control_group_pct", "HOLDOUT %", "number", "20", value="20")},
    {"path": "goal.measurement_window_days", "q": "How long before it is read", "field": lambda p: _f("goal.measurement_window_days", "DAYS UNTIL IT IS READ", "number", "14", value="14")},
    {"path": "goal.kill_criteria", "q": "What stops it early", "field": lambda p: _f("goal.kill_criteria", "STOP RULES · ONE PER LINE", "list", "unsubscribe_rate > 0.4%")},
]


def _proposal_missing(p: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The inputs a pending draft still needs, as fields. Empty when it can run."""
    pl = p.get("payload") or {}
    fields: List[Dict[str, Any]] = []
    if p.get("kind") == "create_campaign":
        for c in CAMPAIGN_FIELDS:
            if _get(pl, c["path"]) in (None, "", []):
                f = c["field"](pl); f["q"] = c["q"]; fields.append(f)
        variants = pl.get("variants") or []
        if not variants and not pl.get("content"):
            fields += [{**_f("variants.0.title", "PUSH TITLE · UP TO 60 CHARACTERS", placeholder="Your account is ready"), "q": "The words users will read"},
                       {**_f("variants.0.body", "PUSH BODY · UP TO 140 CHARACTERS", "long", "Add funds with UPI in under a minute."), "q": ""}]
    elif p.get("kind") == "create_segment":
        if not (pl.get("name") or "").strip():
            fields.append({**_f("name", "COHORT NAME", placeholder="FTT_NOSECOND_Sep26"), "q": "What the cohort is called in MoEngage"})
    return fields


def _validation_error(p: Dict[str, Any]) -> str:
    ex = approvals._executors.get(p.get("kind"))                     # noqa: SLF001 (same package; the registry is the single source)
    if not ex:
        return "the engine has no way to carry this kind of draft out yet"
    pl = p.get("payload") or {}
    try:
        ex["validate"]({**pl, "_approving": True})
    except Exception as e:
        return redact(str(e))[:300]
    if p.get("kind") == "create_campaign":                            # the same rule check an edit goes through, so a draft that breaks one is named here rather than at the click
        try:
            from .llm.tools import campaign_brief_check
            chk = campaign_brief_check(pl.get("goal") or {}, pl.get("variants") or [], channel=str(pl.get("channel") or "push"), market_linked=bool(pl.get("market_hook_id") or pl.get("ttl_hours")), ttl_hours=pl.get("ttl_hours"))
            if not chk["ok"]:
                return "; ".join(chk["problems"])[:300]
        except Exception:
            pass
    return ""


def _proposal_asks() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    pending = approvals.list_proposals(status="pending", limit=60)
    for p in pending:
        title = plain(str(p.get("title") or "")).replace("Campaign draft: ", "").replace("Segment draft: ", "")[:70]
        fields = _proposal_missing(p)
        if fields:
            qs = [f["q"] for f in fields if f.get("q")]
            out.append({"id": f"proposal:{p['id']}", "group": "DRAFTS", "tone": "amber", "title": f"“{title}” cannot start until {len(qs)} thing{'s are' if len(qs) != 1 else ' is'} filled in",
                        "why": "Still missing: " + "; ".join(q[0].lower() + q[1:] for q in qs) + ". The brain left these open rather than guess.",
                        "where": "Fill them here. The draft is re-checked against the copy and brief rules as soon as you save.", "unblocks": f"starting “{title}”",
                        "fields": fields, "button": "SAVE AND RE-CHECK THE DRAFT", "evidence": {"label": "READ THE DRAFT →", "go": f"#running?focus={p['id']}"}})
            continue
        err = _validation_error(p)
        if not err:
            continue
        low = err.lower()
        if "moengage_created_by" in low or "dashboard email" in low:
            continue                                                  # already asked once under CONNECTION; no need to repeat per draft
        if "sign-off" in low or "signoff" in low or "peer" in low:
            out.append({"id": f"proposal:{p['id']}:signoff", "group": "DRAFTS", "tone": "dim", "title": f"“{title}” is waiting for a colleague to sign it off",
                        "why": plain(err) + ". The person who asked for a playbook cannot be the one who signs it.", "where": "A teammate opens the playbook library and signs it.",
                        "unblocks": f"publishing “{title}”", "fields": [], "link": {"label": "OPEN THE SIGN-OFF →", "go": "#tool?m=sops"}})
        else:
            out.append({"id": f"proposal:{p['id']}:blocked", "group": "DRAFTS", "tone": "amber", "title": f"“{title}” would be refused if you approved it now",
                        "why": plain(err), "where": "Tell the brain what to change and it will revise the draft.", "unblocks": f"starting “{title}”", "fields": [],
                        "link": {"label": "ASK THE BRAIN TO FIX IT →", "ask": f"Proposal #{p['id']} fails validation with: {err}. Revise the draft so it passes, keeping the goal and the cohort, and tell me what you changed."}})
    titles_alive = {p["title"] for p in pending} | {p["title"] for p in approvals.list_proposals(status="executed", limit=100)}
    week = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
    for p in approvals.list_proposals(status="failed", limit=30):
        if p["title"] in titles_alive or str(p.get("executed_at") or p.get("created_at") or "")[:10] < week:
            continue
        titles_alive.add(p["title"])
        title = plain(str(p.get("title") or "")).replace("Campaign draft: ", "")[:70]
        out.append({"id": f"failed:{p['id']}", "group": "DRAFTS", "tone": "magenta", "title": f"“{title}” was approved but MoEngage did not take it",
                    "why": plain(str(p.get("error") or "no reason was recorded"))[:260], "where": "Fix the cause above it in this list, then queue the same draft again. It will wait on Today for your approval.",
                    "unblocks": f"a second attempt at “{title}”", "fields": [], "confirm": True, "button": "QUEUE THE SAME DRAFT AGAIN"})
    return out


def _waiting() -> List[Dict[str, Any]]:
    rows = []
    try:
        from . import datarequests
        for r in datarequests.list_requests("open", limit=12):
            rows.append({"id": f"request:{r['id']}", "title": plain(r.get("title") or ""), "why": plain(r.get("why") or "")[:180], "unblocks": plain(r.get("unblocks") or "")[:140],
                         "verb": "MARK AS DELIVERED →"})
    except Exception:
        pass
    return rows


def asks() -> Dict[str, Any]:
    settings = get_all_settings()
    rows = _connection_asks(settings) + _alerts_asks(settings) + _proposal_asks()
    order = {"magenta": 0, "amber": 1, "dim": 2}
    rows.sort(key=lambda a: (order.get(a["tone"], 1), {"CONNECTION": 0, "MARKET ALERTS": 1, "DRAFTS": 2, "ENGINE": 3}.get(a["group"], 4)))
    for i, a in enumerate(rows, 1):
        a["n"] = i
    waiting = _waiting()
    n = len(rows)
    if not n:
        lead = "Nothing is missing. Every draft on Today can run the moment you approve it."
    else:
        need_me = sum(1 for a in rows if a.get("fields") or a.get("confirm"))
        lead = (f"{n} thing{'s' if n != 1 else ''} stand{'s' if n == 1 else ''} between the engine and a campaign that really runs. "
                f"{need_me} need{'s' if need_me == 1 else ''} an answer only you have; each one says where to find it and what it unblocks.")
    return {"lead": lead, "asks": rows, "open": n, "waiting": waiting, "mock": _mock()}


# ── answering ─────────────────────────────────────────────────────────────────
def _coerce(path: str, value: Any, kind: str) -> Any:
    if kind == "list":
        return [x.strip() for x in re.split(r"[\n;]+", str(value or "")) if x.strip()]
    if kind == "number":
        try:
            f = float(str(value).strip())
        except ValueError:
            raise ValueError(f"{path.split('.')[-1].replace('_', ' ')} must be a number")
        return int(f) if f == int(f) else f
    return str(value or "").strip()


def _nest(path: str, value: Any, base: Dict[str, Any]) -> Dict[str, Any]:
    """'goal.kill_criteria' → {'goal': {'kill_criteria': value}}; 'variants.0.title' edits the first variant."""
    parts = path.split(".")
    if parts[0] == "variants":
        v = [dict(x) for x in (base.get("variants") or [])] or [{}]
        v[0][parts[2]] = value
        return {"variants": v}
    out: Dict[str, Any] = {}
    cur = out
    for part in parts[:-1]:
        cur[part] = {}; cur = cur[part]
    cur[parts[-1]] = value
    return out


def answer(aid: str, values: Dict[str, Any], save: SaveFn, actor: str = "user") -> Dict[str, Any]:
    """Put the answer where it belongs, re-check, and say what that unblocked. Never sends anything."""
    values = values or {}
    kind, _, rest = aid.partition(":")
    before = {a["id"] for a in asks()["asks"]}
    toast = "Saved"
    nxt: Optional[Dict[str, Any]] = None
    try:
        if kind == "setting":
            key = rest
            if key == "mock_mode":
                s = get_all_settings()
                missing = [a["field"]["label"].title() for a in SETTING_ASKS if a["key"] not in ("moengage_segmentation_key", "moengage_dc") and not _has(s, a["key"])]
                if missing:
                    return {"ok": False, "toast": f"Not yet · still needed first: {', '.join(missing)}"}
                save({"mock_mode": "false"})
                toast = "Live · approvals now create real drafts in MoEngage"
            else:
                v = str(values.get(key) or "").strip()
                if not v:
                    return {"ok": False, "toast": "Nothing was typed"}
                if key == "moengage_created_by" and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", v):
                    return {"ok": False, "toast": "That does not look like an email address"}
                r = save({key: v})
                if key in (r.get("rejected") or []):
                    return {"ok": False, "toast": "The engine does not accept that setting"}
                toast = "Saved · encrypted on this Mac and never shown again" if key.endswith("_key") else "Saved"
        elif aid == "alerts:employee_ids":
            from .alerts2 import cohort
            ids = _coerce("user_ids", values.get("user_ids"), "list")
            if not ids:
                return {"ok": False, "toast": "Nothing was typed"}
            r = cohort.save_internal_users(ids, actor=actor)
            toast = f"Saved · {r.get('count', len(ids))} employee id(s); only a keyed hash is ever shown"
        elif kind == "alerts":
            toast = "Checked again"                                   # confirm-type asks: the re-check below is the answer
        elif kind == "request":
            from . import datarequests
            datarequests.set_status(int(rest), "fulfilled", "marked delivered from the quiet terminal", actor=actor)
            toast = "Marked as delivered · drafts that waited on it can be proposed now"
        elif kind == "failed":
            p = approvals.get_proposal(int(rest))
            if not p:
                return {"ok": False, "toast": "That draft no longer exists"}
            np = approvals.propose(p["kind"], p["title"], p["payload"], rationale=p.get("rationale") or "", risk=p.get("risk") or "medium", created_by=f"retry:{actor}")
            toast = "Queued again · it is waiting on Today for your approval"
            nxt = {"label": "REVIEW IT ON TODAY", "go": f"#today?decision=proposal:{np['id']}"}
        elif kind == "proposal":
            pid = int(rest.split(":")[0])
            p = approvals.get_proposal(pid)
            if not p or p["status"] != "pending":
                return {"ok": False, "toast": "That draft is no longer waiting"}
            wanted = {f["key"]: f for f in _proposal_missing(p)}
            changes: Dict[str, Any] = {}
            base = dict(p.get("payload") or {})
            for path, raw in values.items():
                if path not in wanted or raw in (None, ""):
                    continue
                patch = _nest(path, _coerce(path, raw, wanted[path]["type"]), {**base, **changes})
                changes = approvals._deep_merge(changes, patch)       # noqa: SLF001
            if not changes:
                return {"ok": False, "toast": "Nothing was typed"}
            still = [f for f in wanted if f not in values or values.get(f) in (None, "")]
            merged = approvals._deep_merge(base, changes)              # noqa: SLF001
            if still:                                                 # partial answers are kept without the full re-validation, which would refuse an unfinished brief
                from .database import get_db
                conn = get_db(); conn.execute("UPDATE proposals SET payload_json=? WHERE id=? AND status='pending'", (json.dumps(merged, default=str), pid)); conn.commit(); conn.close()
                audit("proposal.edited", {"id": pid, "actor": actor, "changed": sorted(changes), "partial": True}, actor=actor)
                toast = f"Saved · {len(still)} more to fill in before it can start"
            else:
                try:
                    approvals.update_payload(pid, changes, actor=actor, note="filled in from Asks")
                    toast = "Complete · the draft passed its checks and can start"
                    nxt = {"label": "START IT FROM TODAY", "go": f"#today?decision=proposal:{pid}"}
                except Exception as e:                                # every input is there but a rule is still broken: keep the answers, name the rule
                    from .database import get_db
                    conn = get_db(); conn.execute("UPDATE proposals SET payload_json=? WHERE id=? AND status='pending'", (json.dumps(merged, default=str), pid)); conn.commit(); conn.close()
                    audit("proposal.edited", {"id": pid, "actor": actor, "changed": sorted(changes), "rule_broken": True}, actor=actor)
                    toast = f"Saved, but the draft still breaks a rule · {plain(redact(str(e)))[:160]}"
        else:
            return {"ok": False, "toast": "Unknown question"}
    except approvals.ApprovalError as e:
        return {"ok": False, "toast": f"Not saved · {plain(str(e))[:220]}"}
    except Exception as e:
        return {"ok": False, "toast": f"Not saved · {plain(redact(str(e)))[:220]}"}
    after = asks()
    still_open = aid in {a["id"] for a in after["asks"]}
    cleared = sorted(before - {a["id"] for a in after["asks"]} - {aid})
    if still_open and kind in ("alerts",):
        row = next(a for a in after["asks"] if a["id"] == aid)
        return {"ok": False, "toast": f"Checked · still not there: {row['why'][:160]}", "asks": after}
    audit("v3.ask_answered", {"id": aid, "cleared": cleared[:6]}, actor=actor)
    if cleared:
        toast += f" · that also cleared {len(cleared)} other question{'s' if len(cleared) != 1 else ''}"
    return {"ok": True, "toast": toast, "next": nxt, "asks": after}


# ── Alerts ────────────────────────────────────────────────────────────────────
def alerts() -> Dict[str, Any]:
    from .alerts2 import discovery, service, brief as brief_mod
    st = discovery.status()
    kill = service.kill_switch()
    live = bool(st.get("live")) and not kill
    stage = str(st.get("stage") or "internal")
    promo = st.get("promotion") or {}
    today = st.get("today") or {}
    sent = int((today.get("fired") or {}).get("_total", 0) if isinstance(today.get("fired"), dict) else today.get("total", 0) or 0)
    if kill:
        lead = "Market alerts are stopped by the stop switch. Nothing goes out until you lift it."
    elif live:
        lead = f"Market alerts are running for the {'employee cohort' if stage == 'internal' else 'whole base'}. {sent or 'None'} went out today; MoEngage delivers and holds the per-user cap."
    else:
        lead = "Market alerts are built and waiting. Nothing has been sent; the engine starts only after you read the launch brief and approve it."
    blockers = []
    for r in st.get("preflight") or []:
        if r.get("ok") is False:
            blockers.append({"check": r["check"], "detail": plain(r.get("detail") or ""), "fix": plain(r.get("fix") or ""), "verb": "ANSWER IT →", "go": "#asks"})
    b = brief_mod.build(0, None, "", 10, with_dry_run=False)
    copy = [{"signal": str(c["signal"]).replace("_", " "), "title": c["sample_title"], "body": c["sample_body"], "ok": not c.get("blocks"), "lint": plain(c.get("lint") or "")} for c in b.get("copy") or []]
    signals = [{"label": s["label"], "when": plain(s["when"]), "cap": f"up to {s['daily_cap']} a day", "lane": "at once" if s.get("lane") == "now" else "in the day's send window"} for s in b.get("signals") or []]
    cohorts = [{"id": c["id"], "label": c["label"], "segment": c["segment"], "state": "RUNNING" if c.get("active") else "LOCKED" if c.get("locked") else "READY", "tone": "green" if c.get("active") else "dim" if c.get("locked") else "amber",
                "why": plain(c.get("why") or ""), "control": f"{c['control_pct']}% held back"} for c in st.get("cohorts") or []]
    fires = [{"at": str(f.get("created_at") or f.get("at") or "")[11:16], "title": plain(f.get("title") or ""), "body": plain(f.get("body") or ""), "to": str(f.get("cohort") or f.get("event") or ""),
              "source": "the agent" if f.get("source") == "agent" else "the rules"} for f in (st.get("fires") or [])[:12]]
    summary = b.get("summary") or {}
    camp = st.get("campaign") if isinstance(st.get("campaign"), dict) else {}
    launch = {"label": "READ THE LAUNCH BRIEF", "state": "launched" if st.get("experiment") or camp.get("state") in ("pending", "executed") else "ready",
              "note": plain({"pending": f"The campaign draft is waiting for your approval on Today (draft {camp.get('proposal_id')}).", "executed": "The campaign draft was created.",
                             "failed": "The last campaign draft failed; see Asks."}.get(camp.get("state"), "Nothing has been queued yet."))}
    return {"lead": lead, "live": live, "kill": kill, "stage": stage, "blockers": blockers, "summary": [{"k": k.upper(), "v": plain(v)} for k, v in summary.items()],
            "signals": signals, "copy": copy, "copy_blocking": len(b.get("copy_blocking") or []), "cohorts": cohorts, "fires": fires, "launch": launch,
            "promotion": {"ready": bool(promo.get("ready")), "line": plain(promo.get("why") or ""), "progress": f"{promo.get('fires', 0)} of {promo.get('need_fires', 5)} alerts · {promo.get('days', 0):.0f} of {promo.get('need_days', 3)} days"},
            "experiment": (st.get("experiment") or {}).get("name") if isinstance(st.get("experiment"), dict) else None,
            "autonomy": plain((st.get("autonomy") or {}).get("note") or ""), "ops": {"label": "OPEN EVERY ALERTS CONTROL →", "go": "#tool?m=alerts"}}


# ── Engine ────────────────────────────────────────────────────────────────────
ENGINE_GROUPS: List[Dict[str, Any]] = [
    {"title": "MoEngage workspace", "note": "Keys are encrypted on this Mac and never shown again.", "fields": [
        ("moengage_app_id", "WORKSPACE ID", "text", ""), ("moengage_dc", "DATA CENTRE", "text", "03"), ("moengage_created_by", "CREATOR EMAIL", "email", "you@coindcx.com"),
        ("moengage_data_api_key", "DATA API KEY", "secret", ""), ("moengage_campaign_key", "CAMPAIGNS API KEY", "secret", ""), ("moengage_segmentation_key", "SEGMENTATION API KEY", "secret", ""),
        ("moengage_inform_key", "INFORM API KEY", "secret", ""), ("moengage_push_platforms", "PUSH PLATFORMS", "text", "ANDROID,IOS"), ("mock_mode", "PRACTICE MODE", "choice:true,false", "")]},
    {"title": "Market alerts", "note": "How the employee cohort receives its alerts.", "fields": [
        ("ma2_internal_delivery", "EMPLOYEE DELIVERY", "choice:event,inform", ""), ("ma2_inform_alert_id", "INFORM ALERT ID", "text", ""), ("ma2_inform_alert_name", "INFORM ALERT NAME", "text", "MA2_Discovery_Internal"),
        ("ma2_agent_autonomy", "AGENT WRITES ITS OWN ALERTS", "choice:true,false", "")]},
    {"title": "The brain's model", "note": "Free models do the heavy reading; the main model writes and reviews.", "fields": [
        ("llm_provider", "PROVIDER", "choice:openrouter,openai_compatible,claude_cli", ""), ("llm_model", "MAIN MODEL", "text", "anthropic/claude-sonnet-4.5"), ("llm_model_bulk", "BULK MODEL", "text", "auto-free"),
        ("llm_api_key", "MODEL KEY", "secret", "sk-or-…"), ("llm_data_collection", "PROVIDERS MAY KEEP PROMPTS", "choice:deny,allow", ""),
        ("llm_autoload_skills", "LOAD THE RIGHT SKILLS AUTOMATICALLY", "choice:true,false", "")]},
    {"title": "Daily rhythm", "note": "When the brain does its morning run, and whether background refresh is on.", "fields": [
        ("schedule_enabled", "MORNING RUN", "choice:true,false", ""), ("schedule_time", "MORNING RUN TIME · IST", "text", "09:00"), ("refresh_enabled", "BACKGROUND REFRESH", "choice:true,false", "")]},
]

JOB_WORDS = {"prices": "Prices", "market_context": "Market picture", "market_alerts": "Market alerts", "signals": "Standing triggers", "benchmarks": "Category benchmarks", "campaign_intel": "Rival campaigns",
             "onchain_cex": "On-chain versus exchanges", "money_flow": "Money flow", "workspace": "Workspace facts", "structural": "Lifecycle gaps", "qa": "Fact checks", "app_rankings": "App rankings",
             "housekeeping": "Housekeeping", "compliance": "Live copy check", "research": "Methodology radar", "council": "Draft reviews", "data_gaps": "Missing data"}


def engine(status: Dict[str, Any]) -> Dict[str, Any]:
    settings = get_all_settings()
    mock = _mock()
    moe = status.get("moengage") or {}; llm = status.get("llm") or {}; sch = status.get("scheduler") or {}; sec = status.get("security") or {}
    connected = bool(moe.get("ok") or moe.get("valid"))
    state = [
        {"k": "MOENGAGE", "v": "Practice mode" if mock else "Connected" if connected else "Not connected", "tone": "amber" if mock else "green" if connected else "magenta",
         "note": "sample data; nothing reaches MoEngage" if mock else plain(moe.get("detail") or moe.get("mode") or "")[:90]},
        {"k": "THE BRAIN", "v": "Ready" if llm.get("configured") else "No model key", "tone": "green" if llm.get("configured") else "magenta", "note": f"{llm.get('provider', '')} · {llm.get('model', '')}"},
        {"k": "MORNING RUN", "v": str(sch.get("time") or "09:00") if sch.get("enabled") else "Off", "tone": "green" if sch.get("enabled") else "amber", "note": f"last run {str(sch.get('last_run_time') or 'never')[:16]}"},
        {"k": "SECRETS", "v": "Encrypted", "tone": "green", "note": f"{sec.get('secrets_backend', 'keychain')} · audit log {'intact' if (sec.get('audit') or {}).get('ok', True) else 'broken'}"},
    ]
    groups = []
    for g in ENGINE_GROUPS:
        fields = []
        for key, label, kind, ph in g["fields"]:
            opts = kind.split(":", 1)[1].split(",") if kind.startswith("choice:") else []
            k = "choice" if opts else kind
            is_set = str(settings.get(key + "_set", "")).lower() == "true"
            fields.append({"key": key, "label": label, "type": k, "options": opts, "placeholder": "saved · leave empty to keep it" if (k == "secret" and is_set) else ph,
                           "value": "" if k == "secret" else str(settings.get(key, "") or ""), "set": is_set if k == "secret" else None})
        groups.append({"title": g["title"], "note": g["note"], "fields": fields})
    jobs = []
    try:
        from . import refresher
        rs = refresher.status()
        for j in rs.get("jobs") or []:
            word = JOB_WORDS.get(j["job"], j["job"].replace("_", " ").title())
            if j.get("ok") is False:
                st, tone = "failing: " + plain(j.get("error") or "")[:90], "magenta"
            elif j.get("never"):
                st, tone = "has not run yet", "dim"
            elif j.get("overdue"):
                st, tone = f"overdue, last ran {j.get('age_min')} min ago", "amber"
            else:
                st, tone = f"ran {j.get('age_min')} min ago, every {j.get('interval_min'):g} min", "green"
            jobs.append({"job": j["job"], "name": word, "status": st, "tone": tone, "verb": "RUN IT NOW →"})
        jobs.sort(key=lambda r: {"magenta": 0, "amber": 1, "dim": 2, "green": 3}[r["tone"]])
    except Exception:
        pass
    failing = sum(1 for j in jobs if j["tone"] == "magenta")
    n_asks = asks()["open"]
    lead = ("The engine is in practice mode on sample data. " if mock else "The engine is working on the real workspace. " if connected else "The engine cannot reach MoEngage. ") + \
           (f"{n_asks} answer{'s are' if n_asks != 1 else ' is'} still missing; " if n_asks else "Nothing is missing; ") + (f"{failing} background job{'s are' if failing != 1 else ' is'} failing." if failing else "every background job is healthy.")
    skills_rows: List[Dict[str, Any]] = []
    skills_note = ""
    try:
        from . import skill_router
        u = skill_router.usage(7)
        skills_rows = [{"name": r["skill"], "status": f"read {r['auto'] + r['by_the_agent']} time{'s' if r['auto'] + r['by_the_agent'] != 1 else ''} this week · {r['auto']} placed by the engine, {r['by_the_agent']} asked for by the brain",
                        "verb": "READ IT →", "go": "#tool?m=skills"} for r in u["used"][:10]]
        never = u["never_opened"]
        skills_note = (f"{len(u['used'])} of {u['installed']} skills were read this week. " + (f"Never opened: {', '.join(never[:8])}{'…' if len(never) > 8 else ''}." if never else "Every installed skill was opened.")
                       + ("" if u["enabled"] else " Automatic loading is switched off."))
    except Exception:
        pass
    return {"lead": lead, "state": state, "groups": groups, "jobs": jobs, "asks_open": n_asks, "skills": skills_rows, "skills_note": skills_note,
            "more": [{"label": "TEACH THE BRAIN A RULE →", "go": "#tool?m=engine"}, {"label": "ASK FOR AN ENGINE CHANGE →", "go": "#tool?m=engine"}, {"label": "SEE EVERY TOOL AND SKILL →", "go": "#tool?m=skills"},
                     {"label": "OPEN THE WHOLE WORKBENCH →", "go": "#bench"}]}


# ── Workbench: every module of the operator console, opened inside this shell ─
MODULES: List[Dict[str, str]] = [
    {"m": "brain", "name": "Brain", "what": "The full directive board with previews, the trace of what the brain did, fact checks on every board, autopilot and the daily cycle."},
    {"m": "lab", "name": "Brain Lab", "what": "News that moves markets, top assets and open interest, what traders are doing, money flow, every rival with its dossier, benchmarks against the leaders."},
    {"m": "ideas", "name": "Ideas board", "what": "Every idea and draft as a board: ideas, proposed, simulated, live, read, archive. Edit a draft, read the council's review, download or upload a revision, approve or reject."},
    {"m": "sops", "name": "Playbook library", "what": "Recommended changes per playbook, India fit, ownership and escalation, ask the library a question, request a missing playbook, peer sign-off, download and upload."},
    {"m": "anom", "name": "Anomalies", "what": "Every campaign that left its own baseline, with the diagnosis, the cause and the options."},
    {"m": "atlas", "name": "Cohorts", "what": "Every cohort decoded from its name, families and versions, reach, overlap, and the product view of the base."},
    {"m": "analysis", "name": "Analysis", "what": "The complete programme report: lifecycle coverage, channel health, the campaign league table, the compliance sweep of live copy."},
    {"m": "chat", "name": "Ask the brain", "what": "The full saved conversation, with a choice of specialist: strategist, analyst, copywriter, compliance, experimenter, intel, cohorts, ops, researcher."},
    {"m": "alerts", "name": "Market alerts", "what": "Every alerts control: the per-user pilot and its cohort file, the discovery experiment, templates, the whale feed, timing windows, coverage replay, the stop switch."},
    {"m": "engine", "name": "Engine", "what": "Every setting, engine knobs, teach the agent, change requests, the MoEngage integration and HAR capture, data requests, self-heal, the signal bridge, data refresh."},
    {"m": "skills", "name": "Skills", "what": "The skill library as the agent reads it, every tool with its budget, and the methodology radar with proposed skill updates."},
]


def workbench() -> Dict[str, Any]:
    return {"lead": "Everything the operator console could do, opened here. Nothing was removed: the quiet screens are the short path, these are the full controls.",
            "modules": [{**m, "verb": f"OPEN {m['name'].upper()} →", "go": f"#tool?m={m['m']}"} for m in MODULES]}
