"""
The v3 "quiet terminal" view layer: five screens, every piece of information carrying exactly one next action.

Nothing here computes anything new. It reads what the brain already produces (directives, anomalies, ideas, experiments,
rivals, SOPs) and rewrites it into plain English with a verb on every row: decisions instead of directives, words instead
of codes, consequences instead of severity labels. Model internals (z-scores, raw probabilities, endpoint names) never
reach the page; `plain()` is the last line of defence and the tests check the output for them.
"""
from __future__ import annotations
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from . import approvals, brain
from .database import get_db, get_setting
from .security import audit, redact

IST = timezone(timedelta(hours=5, minutes=30))

SEV = {  # brain severity → (tag, colour semantic)
    "act_now": ("LOSING MONEY NOW", "magenta"), "high_ev": ("BIGGEST OPPORTUNITY", "amber"), "counter": ("COMPETITOR PRESSURE", "amber"),
    "cleanup": ("NEEDS A TIDY-UP", "dim"), "watch": ("WATCH", "amber"), "good": ("HANDLED", "green"),
}
GENERIC_VERBS = {"APPROVE", "SUBMIT", "VIEW", "OK", "GO", "RUN", "OPEN", "CLICK"}
LEAKS = [
    (re.compile(r"\bz\s*=\s*-?\d+(\.\d+)?", re.I), "a large move against its own baseline"),
    (re.compile(r"\bEV\s*₹\s*([\d.]+\s*[LKCr]*)", re.I), r"worth ₹\1 / month"),
    (re.compile(r"\bpressure index \d+\b", re.I), "high pressure"),
    (re.compile(r"\bSOV\b"), "share of voice"),
    (re.compile(r"\b(create_segment|create_campaign|create_flow|pause_campaign|code_change|custom_segment_upload|signal_rule|skill_update|sop_change|sop_new|ma2_pilot|ma2_discovery)\b"),
     lambda m: {"create_segment": "a new cohort", "create_campaign": "a campaign draft", "create_flow": "a flow draft", "pause_campaign": "pausing a campaign", "code_change": "an engine change",
                "custom_segment_upload": "a cohort upload", "signal_rule": "a standing trigger", "skill_update": "a playbook lesson", "sop_change": "a playbook change", "sop_new": "a new playbook",
                "ma2_pilot": "the alerts pilot", "ma2_discovery": "the market alerts programme"}[m.group(1)]),
    (re.compile(r"\bactuation\b", re.I), "sending"),
    (re.compile(r"\bn=\d+\b"), ""),
    (re.compile(r"\s{2,}"), " "),
]


def plain(text: Any) -> str:
    s = str(text or "")
    for rx, rep in LEAKS:
        s = rx.sub(rep, s)
    return s.replace("▚", "").replace("!", ".").strip()


def confidence_word(x: Any) -> str:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return "Medium"
    if v > 1:                                  # ICE 1–10
        v = v / 10.0
    return "High" if v >= 0.78 else "Medium-high" if v >= 0.65 else "Medium" if v >= 0.5 else "Low"


def _ist(dt: Optional[str] = None) -> datetime:
    if dt:
        try:
            d = datetime.fromisoformat(str(dt).replace("Z", "+00:00"))
            return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).astimezone(IST)
        except Exception:
            pass
    return datetime.now(IST)


# ── deferrals ─────────────────────────────────────────────────────────────────
def _deferred() -> Dict[str, str]:
    try:
        d = json.loads(get_setting("v3_deferred_json", "") or "{}")
        today = datetime.now(IST).strftime("%Y-%m-%d")
        return {k: v for k, v in d.items() if v >= today}          # a deferral lasts until the day it names
    except Exception:
        return {}


def defer(did: str, actor: str = "user") -> Dict[str, Any]:
    from .database import set_setting
    d = _deferred(); until = (datetime.now(IST) + timedelta(days=1)).strftime("%Y-%m-%d")
    d[did] = until
    set_setting("v3_deferred_json", json.dumps(d))
    audit("decision.defer", {"id": did, "until": until}, actor=actor)
    return {"ok": True, "until": until}


# ── decisions (Today) ─────────────────────────────────────────────────────────
def _proposal_decision(d: Dict[str, Any]) -> Dict[str, Any]:
    pid = int(d["id"].split(":")[1]); p = approvals.get_proposal(pid) or {}
    pl = p.get("payload") or {}; goal = pl.get("goal") or {}; kind = p.get("kind") or "create_campaign"
    name = pl.get("name") or p.get("title") or "this draft"
    seg = pl.get("target_segment") or (pl.get("audience") or {}).get("segment_name") or ""
    ice = pl.get("ice") or {}
    verb, title, primary = {
        "create_campaign": ("start", f"Start the campaign “{name}”" + (f" for {seg}" if seg else ""), "START THE CAMPAIGN"),
        "create_segment": ("create", f"Create the “{name}” cohort so its journey can run", "CREATE THE COHORT"),
        "create_flow": ("start", f"Start the “{name}” journey", "START THE JOURNEY"),
        "pause_campaign": ("pause", f"Pause {pl.get('campaign_id') or 'the campaign'} before it costs more", "PAUSE IT"),
        "code_change": ("merge", f"Merge the engine change “{p.get('title', '')[:60]}”", "MERGE THE CHANGE"),
        "signal_rule": ("arm", f"Arm the trigger “{p.get('title', '').split(':', 1)[-1].strip()[:60]}”", "ARM THE TRIGGER"),
        "skill_update": ("teach", "Teach the brain a new method from the research radar", "ADD THE LESSON"),
        "sop_change": ("revise", f"Revise the playbook {pl.get('sop_id', '')}", "PUBLISH THE NEW VERSION"),
        "sop_new": ("add", f"Put the new playbook “{(pl.get('spec') or {}).get('name', name)}” in the library", "PUT IT IN THE LIBRARY"),
        "ma2_pilot": ("start", f"Start the market alerts pilot “{name}”", "START THE PILOT"),
        "ma2_discovery": ("start", f"Let the engine fire market alerts for {pl.get('audience') or 'the employee cohort'}", "START FIRING ALERTS"),
        "custom_segment_upload": ("upload", f"Upload the “{name}” cohort to MoEngage", "UPLOAD THE COHORT"),
    }.get(kind, ("approve", plain(p.get("title") or "Approve this draft"), "APPROVE THE DRAFT"))
    worth = (f"worth {goal['target']}" if goal.get("target") else f"{plain(pl.get('expected_impact'))}" if pl.get("expected_impact") else "Not measured yet")
    plan = []
    if kind == "create_campaign":
        plan = [f"A draft called “{name}” is created in MoEngage" + (f" for the {seg} segment" if seg else ""),
                f"{goal.get('control_group_pct', 20)}% of them are held out as a control group",
                f"The experiment is registered and read after {goal.get('measurement_window_days', 14)} days"]
    elif kind == "create_segment":
        plan = [f"A cohort called “{name}” is created in MoEngage", "The journeys waiting for it can be proposed next", "Nothing is sent to anyone"]
    elif kind in ("sop_change", "sop_new"):
        plan = ["A new version of the playbook is written to the library", "Every future run follows it", "Nothing is sent to anyone"]
    elif kind == "ma2_discovery":
        plan = [f"The engine starts firing market alerts to {pl.get('audience') or 'the employee cohort'} through MoEngage", "Every alert passes the copy rules and the daily caps first", "The kill switch stops it at once"]
    elif kind == "code_change":
        plan = ["The change is merged and the engine restarts itself", "The tests already passed on the draft", "Roll back from Engine → self-heal if anything regresses"]
    else:
        plan = ["The proposal is executed exactly as previewed", "It is recorded in the audit log", "You can read it back on the Running screen"]
    return {"id": d["id"], "kind": kind, "tag": SEV.get(d["severity"], ("NEEDS YOU", "amber"))[0], "tone": SEV.get(d["severity"], ("", "amber"))[1],
            "title": title, "body": plain(d.get("rationale") or p.get("rationale") or ""),
            "facts": [{"k": "WORTH", "v": worth, "tone": "green"}, {"k": "CONFIDENCE", "v": confidence_word(ice.get("confidence", 6)), "tone": "text"},
                      {"k": "YOUR EFFORT", "v": "One click", "tone": "text"}],
            "plan": plan, "primary": {"label": primary, "action": "approve"},
            "evidence": {"label": "READ THE DRAFT", "go": f"#running?focus={pid}"}, "defer": {"label": "DECIDE TOMORROW"}, "rank": d.get("rank", 70),
            **({"test": {"label": "SEND A TEST TO THE TEST USERS", "proposal_id": pid}} if kind == "create_campaign" and "{{" not in json.dumps(pl.get("variants") or []) else {})}


def _anomaly_decision(d: Dict[str, Any], anomaly_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    cid = d["id"].split(":")[1]
    row = next((a for a in anomaly_rows if str(a.get("campaign_id")) == cid), {})
    camp = row.get("campaign") or d.get("title") or "A campaign"
    metric = str(row.get("metric") or "").replace("_", " ")
    delta = plain(row.get("delta") or "")
    return {"id": d["id"], "kind": "anomaly", "tag": "LOSING MONEY NOW", "tone": "magenta",
            "title": f"{camp} is off its own baseline and needs a decision",
            "body": plain(f"{metric} {delta}. {row.get('cause') or d.get('rationale') or ''} The brain's first option: {row.get('option') or 'run the diagnosis'}."),
            "facts": [{"k": "COSTING YOU", "v": delta or "Being measured", "tone": "magenta"}, {"k": "CONFIDENCE", "v": "Medium-high" if (row.get("n_history") or 0) >= 14 else "Medium", "tone": "text"},
                      {"k": "YOUR EFFORT", "v": "Read the diagnosis", "tone": "text"}],
            "plan": ["The brain runs the full diagnosis on this campaign", "It posts the cause and the best fix to the Ideas queue", "Nothing is sent to users until you approve the fix"],
            "primary": {"label": "RUN THE DIAGNOSIS", "action": "approve"}, "evidence": {"label": "SEE THE TREND", "go": "#today?moved=1"},
            "defer": {"label": "DECIDE TOMORROW"}, "rank": d.get("rank", 90)}


def _compete_decision(d: Dict[str, Any]) -> Dict[str, Any]:
    parts = d["id"].split(":"); rival = parts[-1]
    what = plain(d.get("rationale") or "")
    counter = what.split("→", 1)[-1].strip() if "→" in what else "our own message"
    clause = re.split(r"[;.]", counter)[0].strip()                     # the first clause, ending on a word
    if len(clause) > 72:
        clause = clause[:72].rsplit(" ", 1)[0]
    return {"id": d["id"], "kind": "compete", "tag": "COMPETITOR PRESSURE", "tone": "amber",
            "title": f"{rival.title()} moved on our users; answer with {clause or 'our own message'}",
            "body": what.split("→", 1)[0].strip(),
            "facts": [{"k": "AT STAKE", "v": "Share on pairs we list", "tone": "amber"}, {"k": "CONFIDENCE", "v": "Medium", "tone": "text"}, {"k": "YOUR EFFORT", "v": "Read 1 message", "tone": "text"}],
            "plan": ["The brain drafts the counter message in our voice, never naming them", "It picks the cohort that actually trades those pairs", "The draft lands here for your approval before anything sends"],
            "primary": {"label": "DRAFT THE COUNTER", "action": "approve"}, "evidence": {"label": "SEE WHAT THEY DID", "go": f"#rivals?focus={rival}"},
            "defer": {"label": "DECIDE TOMORROW"}, "rank": d.get("rank", 75)}


def _tier0_decision(d: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": d["id"], "kind": "tier0", "tag": "MARKET EVENT NOW", "tone": "magenta", "title": "A major market event is under way; freeze promotions and send the calm note",
            "body": plain(d.get("rationale") or ""),
            "facts": [{"k": "COSTING YOU", "v": "Trust, if we sell into it", "tone": "magenta"}, {"k": "CONFIDENCE", "v": "High", "tone": "text"}, {"k": "YOUR EFFORT", "v": "Nothing to send yet", "tone": "text"}],
            "plan": ["Promotions pause for the day", "The brain writes the one factual note with a product lens each", "Each note waits here for your approval"],
            "primary": {"label": "WRITE THE CALM NOTE", "action": "approve"}, "evidence": {"label": "SEE THE EVENT", "go": "#rivals"}, "defer": {"label": "DECIDE TOMORROW"}, "rank": 100}


def _sop_decision(d: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": d["id"], "kind": "sop", "tag": "NEEDS A TIDY-UP", "tone": "dim", "title": plain(d.get("title") or "A playbook run has drifted"), "body": plain(d.get("rationale") or ""),
            "facts": [{"k": "AT STAKE", "v": "Cadence discipline", "tone": "amber"}, {"k": "CONFIDENCE", "v": "High", "tone": "text"}, {"k": "YOUR EFFORT", "v": "One click", "tone": "text"}],
            "plan": ["The run is marked as reviewed", "Nothing is sent", "The playbook keeps its rules"], "primary": {"label": "MARK IT REVIEWED", "action": "approve"},
            "evidence": {"label": "OPEN THE PLAYBOOK", "go": "#plays"}, "defer": {"label": "DECIDE TOMORROW"}, "rank": 60}


def decisions() -> List[Dict[str, Any]]:
    seen = set(); out = []
    deferred = _deferred()
    try:
        anomaly_rows = brain.anomalies_view()
    except Exception:
        anomaly_rows = []
    for d in brain.directives(limit=20):
        if d["id"] in seen or d["id"] in deferred:
            continue
        seen.add(d["id"])
        kind = d["id"].split(":")[0]
        try:
            row = {"proposal": _proposal_decision, "anomaly": lambda x: _anomaly_decision(x, anomaly_rows), "compete": _compete_decision, "tier0": _tier0_decision, "sop": _sop_decision}.get(kind, _sop_decision)(d)
        except Exception as e:
            row = _sop_decision({**d, "rationale": redact(str(e))[:120]})
        out.append(row)
    out.sort(key=lambda r: -r["rank"])
    for i, r in enumerate(out):
        r["n"] = i + 1
    return out


def resolve(did: str, action: str, actor: str = "user") -> Dict[str, Any]:
    """approve = do the thing the decision names; defer = hide it until tomorrow. Returns a plain-language toast."""
    if action == "defer":
        r = defer(did, actor)
        return {"ok": True, "toast": "Parked until tomorrow · it will come back at the top", **r}
    kind = did.split(":")[0]
    if kind == "proposal":
        r = brain.act_on_directive(did, "approve", actor=actor)
        pid = did.split(":")[1]
        if r.get("ok"):
            return {"ok": True, "toast": f"Done · #{pid} executed; the brain reports back after the measurement window"}
        return {"ok": False, "toast": f"Not done · {plain(r.get('error') or r.get('status') or 'it could not run')}", "error": r.get("error")}
    if kind == "anomaly":
        r = brain.act_on_directive(did, "simulate", actor=actor)
        n = len(((r.get("diagnosis") or {}).get("options") or []))
        defer(did, actor)                                          # off the list for today; the diagnosis is the answer
        return {"ok": True, "toast": f"Diagnosis run · {n} option(s) posted; the fix waits for your approval", "diagnosis": r.get("diagnosis")}
    if kind in ("compete", "tier0"):
        r = brain.act_on_directive(did, "simulate" if kind == "compete" else "approve", actor=actor)
        defer(did, actor)
        prompt = r.get("agent_prompt") or ("Draft the counter to this competitor move as a compliant campaign proposal for the cohort that trades those pairs; never name the venue. " + did)
        return {"ok": True, "toast": "The brain is drafting it · the draft lands here for approval", "ask": prompt}
    if kind == "sop":
        brain.act_on_directive(did, "hold", actor=actor); defer(did, actor)
        return {"ok": True, "toast": "Marked as reviewed"}
    return {"ok": False, "toast": "Unknown decision"}


# ── Today ─────────────────────────────────────────────────────────────────────
def _moved() -> List[Dict[str, Any]]:
    rows = []
    try:
        for a in brain.anomalies_view()[:8]:
            metric = str(a.get("metric") or "").replace("_", " ")
            tone = "magenta" if a.get("severity") == "ACT TODAY" else "green" if a.get("severity") == "GOOD" else "amber"
            verb, go = ("FIX NOW →", f"#today?decision=anomaly:{a.get('campaign_id')}") if a.get("severity") == "ACT TODAY" else ("EXTEND →", "#ideas") if a.get("severity") == "GOOD" else ("RE-RUN →", "#running")
            rows.append({"kind": "anomaly", "delta": plain(a.get("delta") or "moved"), "tone": tone, "what": plain(f"{a.get('campaign')}: {metric} moved against its own baseline. {a.get('cause') or ''}"),
                         "do": plain(a.get("option") or "read the diagnosis"), "verb": verb, "go": go})
    except Exception:
        pass
    try:
        for m in (brain.intel().get("moves") or [])[:5]:
            what = re.sub(r"^[a-z0-9_]+:\s*", "", str(m.get("what") or ""))                        # drop the "venue:" slug prefix from the feed
            rows.append({"kind": "rival", "delta": m.get("impact", "WATCH").title(), "tone": "magenta" if m.get("impact") == "MATERIAL" else "amber", "what": plain(what),
                         "do": "answer where we already list the pair" if m.get("impact") == "MATERIAL" else "watch cadence and app rank", "verb": "REBUILD →" if m.get("impact") == "MATERIAL" else "SEE WHY →", "go": "#rivals"})
    except Exception:
        pass
    return rows[:10]


def _handled() -> List[str]:
    out = []
    today = datetime.now(IST).strftime("%Y-%m-%d")
    try:
        for p in approvals.list_proposals(limit=100):
            if p.get("status") == "executed" and str(p.get("executed_at") or "")[:10] == today:
                out.append(plain(f"Executed “{p.get('title', '')[:70]}”"))
    except Exception:
        pass
    try:
        from . import refresher
        for j in refresher.status().get("jobs") or []:
            if j.get("ok") and str(j.get("last_started") or "")[:10] == today:
                out.append({"prices": "Prices refreshed every minute", "market_context": "Market picture rebuilt", "signals": "Standing triggers evaluated", "qa": "Fact checks run on every board",
                            "compliance": "Live copy checked against the rules", "market_alerts": "Market alerts judged", "research": "Methodology radar refreshed"}.get(j["job"], f"{j['job'].replace('_', ' ').title()} refreshed"))
    except Exception:
        pass
    try:
        from .alerts2 import discovery
        n = discovery.fired_today(datetime.now(IST)).get("_total", 0)
        if n:
            out.append(f"{n} market alert(s) sent to the employee cohort")
    except Exception:
        pass
    return list(dict.fromkeys(out))[:8]


def headline(decs: List[Dict[str, Any]], moved: List[Dict[str, Any]]) -> str:
    try:
        board = brain.experiments_board()
        cols = {c["key"]: c["cards"] for c in board["columns"]}
        live, read = len(cols.get("live") or []), len(cols.get("read") or [])
    except Exception:
        live, read = 0, 0
    if not decs:
        return f"Everything is decided. {live or 'No'} experiment{'s' if live != 1 else ''} keep{'s' if live == 1 else ''} running and the brain reports back at 09:00."
    anomaly = next((m for m in moved if m.get("kind") == "anomaly"), None)
    rival_n = sum(1 for m in moved if m.get("kind") == "rival")
    if anomaly:
        lead = anomaly["what"].split(".")[0]
    elif rival_n:
        lead = f"Nothing is outside its own baseline today, but rivals made {rival_n} move{'s' if rival_n != 1 else ''} worth a look"
    else:
        lead = "Nothing is outside its own baseline today"
    tail = f" and {read} experiment{'s' if read != 1 else ''} read out overnight" if read else ""
    return f"{lead}{tail}. {len(decs)} decision{'s' if len(decs) != 1 else ''} {'are' if len(decs) != 1 else 'is'} waiting — the costly one first."


def today() -> Dict[str, Any]:
    now = datetime.now(IST)
    decs = decisions(); moved = _moved()
    asks_open = 0
    try:                                           # a decision whose draft is missing an input cannot run: send the reader to the question instead of a button that fails
        from . import v3_ops
        a = v3_ops.asks(); asks_open = a["open"]
        blocked = {x["id"].split(":")[1]: x for x in a["asks"] if x["id"].startswith("proposal:")}
        live_blockers = [x for x in a["asks"] if x["group"] == "CONNECTION" and x["tone"] == "magenta"]
        for d in decs:
            pid = d["id"].split(":")[1] if d["id"].startswith("proposal:") else None
            hit = blocked.get(pid) if pid else None
            if not hit and pid and d.get("kind") in ("create_campaign", "create_segment", "create_flow", "custom_segment_upload") and live_blockers:
                hit = live_blockers[0]
            if hit:
                d["blocked"] = plain(hit["title"])
                d["primary"] = {"label": "FILL IN WHAT IS MISSING", "go": f"#asks?focus={hit['id']}"}
                d["facts"][2] = {"k": "YOUR EFFORT", "v": "One answer, then one click", "tone": "text"}
    except Exception:
        pass
    return {"dateline": now.strftime("%A %d %B").upper(), "headline": headline(decs, moved) + (f" {asks_open} answer{'s are' if asks_open != 1 else ' is'} still missing before everything can run." if asks_open and decs else ""),
            "asks_open": asks_open, "decisions": decs, "open": len(decs),
            "moved": moved, "handled": _handled(), "synced": now.strftime("%H:%M"), "next": str(get_setting("schedule_time", "09:00") or "09:00"),
            "all_clear": {"line": "Nothing left to decide today.", "context": headline([], moved).replace("Everything is decided. ", ""), "button": {"label": "OPEN THE IDEAS QUEUE", "go": "#ideas"}}}


# ── Rivals ────────────────────────────────────────────────────────────────────
def rivals() -> Dict[str, Any]:
    it = brain.intel()
    decs = {d["id"].split(":")[-1]: d for d in decisions() if d["kind"] == "compete"}
    rows = []
    for i, r in enumerate([x for x in it.get("rivals") or [] if not x.get("minor")][:8]):
        threat = "HIGHEST PRESSURE" if i == 0 and r.get("threat") in ("HIGH", "ELEVATED") else {"HIGH": "RISING", "ELEVATED": "RISING", "WATCH": "WATCHING", "LOW": "AN OPENING"}.get(r.get("threat"), "WATCHING")
        counter = plain(r.get("counterPlay") or "")
        if r["id"] in decs:
            link = {"label": "REVIEW THE DECISION →", "go": f"#today?decision={decs[r['id']]['id']}"}
        elif "spotlight" in counter or "push" in counter:
            link = {"label": "BUILD THE PUSH →", "ask": f"Build the counter push for {r['name']}: {counter}. Compliant copy, right cohort, never name them; propose it for approval."}
        elif "listing" in counter:
            link = {"label": "OPEN THAT PLAYBOOK →", "go": "#plays?sop=sop_new_listing_announce"}
        else:
            link = {"label": "DRAFT THE COUNTER →", "ask": f"Draft our answer to {r['name']}'s latest move ({plain(r.get('latestMove'))}): {counter}. Propose it for approval."}
        rows.append({"id": r["id"], "name": r["name"], "threat": threat, "tone": "magenta" if threat == "HIGHEST PRESSURE" else "amber" if threat == "RISING" else "green" if threat == "AN OPENING" else "dim",
                     "did": plain(r.get("latestMove") or "no notable move in 24 hours") + (f"; {', '.join(r.get('tactics') or [])}" if r.get("tactics") else ""),
                     "answer": counter or "hold; monitor cadence and app rank", "link": link})
    top = rows[0] if rows else None
    lead = (f"{top['name']} is the rival to watch: {top['did'].split(';')[0]}. Our answer is ready below." if top else "No rival made a move worth answering in the last 24 hours.")
    return {"lead": lead, "rivals": rows, "generated_at": it.get("generated_at")}


# ── Ideas ─────────────────────────────────────────────────────────────────────
def _idea_button(i: Dict[str, Any]) -> Dict[str, Any]:
    cat = i.get("category"); kind = i.get("kind")
    if i.get("structural"):
        return {"label": "MAKE IT AN EXPERIMENT", "action": "promote"}
    if cat == "market":
        return {"label": "ARM THE TRIGGER", "action": "promote"}
    if cat == "cadence":
        return {"label": "START THE FIX", "action": "promote"}
    if kind == "hack":
        return {"label": "TRY THIS TACTIC", "action": "promote"}
    if cat == "counter":
        return {"label": "DRAFT THE COUNTER", "action": "promote"}
    return {"label": "MAKE IT AN EXPERIMENT", "action": "promote"}


def _effort_words(e: Any) -> str:
    return {"LOW": "One click to start", "MED": "An afternoon to set up", "HIGH": "A week of work"}.get(str(e or "").upper(), "One click to start")


def ideas() -> Dict[str, Any]:
    rows = []
    for i in brain.ideas()[:40]:
        lift = plain(i.get("projectedLift") or "")
        line = " · ".join(x for x in [(f"{lift}" if lift and lift != "—" else ""), f"{confidence_word(i.get('confidence'))} confidence", _effort_words(i.get("effort"))] if x)
        rows.append({"id": i["id"], "raw_id": i.get("raw_id"), "kind": i.get("kind"), "title": plain(i["title"]), "hypothesis": plain(i.get("hypothesis") or i.get("tagline") or ""),
                     "line": line, "button": _idea_button(i), "structural": bool(i.get("structural"))})
    n_struct = sum(1 for r in rows if r["structural"])
    lead = (f"{len(rows)} ideas are queued; {n_struct} of them are plays every lifecycle programme should already have. Start with those." if n_struct
            else f"{len(rows)} ideas are queued, ranked by expected lift against effort.")
    return {"lead": lead, "ideas": rows}


def promote(raw: str, actor: str = "user") -> Dict[str, Any]:
    r = brain.promote_idea(raw, actor=actor)
    return {"ok": bool(r.get("ok")), "toast": "Handed to the brain · the experiment draft lands on Today for approval", "ask": r.get("agent_prompt")}


# ── Running ───────────────────────────────────────────────────────────────────
def running() -> Dict[str, Any]:
    board = brain.experiments_board()
    cols = {c["key"]: c["cards"] for c in board["columns"]}
    rows = []
    for c in cols.get("live") or []:
        m = re.search(r"day (\d+) of (\d+)", str(c.get("metric") or "")); day, of = (int(m.group(1)), int(m.group(2))) if m else (None, None)
        early = day is not None and day < 3
        rows.append({"id": c["id"], "title": plain(c["title"]), "when": f"day {day} of {of}" if day is not None else "running", "metric": plain(c.get("tagline") or c.get("note") or ""),
                     "status": "Too early to read" if early else "On track", "tone": "dim" if early else "green",
                     "move": {"label": "READ IT EARLY →", "ask": f"Read experiment (proposal) #{c['id']} now with experiment_readouts; say what can and cannot be claimed yet."}})
    for c in cols.get("read") or []:
        verdict = str(c.get("metric") or "").lower()
        proven = "above" in verdict or "won" in verdict
        rows.append({"id": c["id"], "title": plain(c["title"]), "when": "window complete", "metric": plain(c.get("metric") or ""), "status": "Proven" if proven else "Read, no clear lift", "tone": "green" if proven else "amber",
                     "move": {"label": "ROLL OUT EVERYWHERE →", "ask": f"Experiment #{c['id']} proved out; propose the roll-out to the full cohort with the same holdout discipline."} if proven
                             else {"label": "SEE WHY →", "ask": f"Experiment #{c['id']} did not show clear lift. Explain why from experiment_readouts and campaign_deep_dive, and what to change."}})
    for c in cols.get("simulated") or []:
        rows.append({"id": c["id"], "title": plain(c["title"]), "when": "approved", "metric": plain(c.get("metric") or ""), "status": "Approved, not yet live", "tone": "amber",
                     "move": {"label": "PUBLISH IN MOENGAGE →", "ask": f"Proposal #{c['id']} is approved but not live. Tell me exactly what to publish in MoEngage and confirm the draft exists."}})
    for c in cols.get("proposed") or []:
        rows.append({"id": c["id"], "title": plain(c["title"]), "when": "waiting", "metric": plain(c.get("tagline") or c.get("metric") or ""), "status": "Waiting for you", "tone": "amber",
                     "move": {"label": "DECIDE →", "go": f"#today?decision=proposal:{c['id']}"}})
    for c in (cols.get("archive") or [])[:10]:
        rows.append({"id": c["id"], "title": plain(c["title"]), "when": "stopped", "metric": plain(c.get("metric") or ""), "status": "Stopped" + (", no lift" if "reject" in str(c.get("metric")) else ""), "tone": "dim",
                     "move": {"label": "SEE WHY →", "ask": f"Why was proposal #{c['id']} stopped or rejected, and is there a lesson to record?"}})
    live = sum(1 for r in rows if r["status"] in ("On track", "Too early to read")); waiting = sum(1 for r in rows if r["status"] == "Waiting for you")
    lead = f"{live} experiment{'s are' if live != 1 else ' is'} running and {waiting} {'are' if waiting != 1 else 'is'} waiting for a decision. Every row below has a next move."
    return {"lead": lead, "rows": rows}


# ── Playbooks ─────────────────────────────────────────────────────────────────
def plays(selected: Optional[str] = None) -> Dict[str, Any]:
    from . import sops
    lib = [s for s in sops.list_sops() if s.get("active") != 0]
    order = ["onboarding", "activation", "retention", "winback", "market", "competition", "education", "risk", "compliance"]
    lib.sort(key=lambda s: (order.index(s.get("campaign_type")) if s.get("campaign_type") in order else len(order), plain(s["name"]).lower()))
    tabs = [{"id": s["id"], "name": plain(s["name"]), "short": _short_name(plain(s["name"])), "version": f"v{s.get('version') or 1}",
             "group": PLAY_GROUPS.get(s.get("campaign_type"), "Other"), "steps": s.get("steps_count") or len(s.get("steps") or [])} for s in lib]
    sid = selected if any(t["id"] == selected for t in tabs) else (tabs[0]["id"] if tabs else None)
    idx = next((i for i, t in enumerate(tabs) if t["id"] == sid), 0)
    detail = None
    if sid:
        s = sops.get_sop(sid) or {}
        steps = []
        for i, st in enumerate(s.get("steps") or [], 1):
            internal = str(st.get("purpose") or "").startswith("(internal)")
            title = plain(str(st.get("purpose") or "").replace("(internal)", "").strip())
            action = ({"label": "SEE THE NOTE →", "ask": f"Playbook {sid} step {i}: {title}. What does the brain do at this step and what did it produce last time?"} if internal
                      else {"label": "PREVIEW THE COHORT →", "ask": f"Preview who would receive step {i} of {sid} ({title}): segment_study on {((s.get('audience') or {}).get('segment_family'))}, reach and exclusions."} if i == 1
                      else {"label": "APPROVE COPY →", "go": "#running"} if st.get("channel") in ("push", "email", "whatsapp", "sms", "in-app") else {"label": "SEE LAST READ →", "go": "#running"})
            steps.append({"n": i, "title": title, "detail": plain(f"{st.get('channel')} · day {st.get('day')}" + (f" · {st.get('send_time_ist')} IST" if st.get("send_time_ist") else "") + (f" · {st.get('copy_brief')}" if st.get("copy_brief") else "")),
                          "gate": "automatic" if internal else "you approve", "action": action})
        fr = s.get("frequency") or {}
        rules = [{"k": "SEND CAP", "v": f"{fr.get('max_messages_per_user_per_week', '—')} per user per week · {str(fr.get('cadence', '')).replace('_', ' ')}"},
                 {"k": "HOLDOUT", "v": f"{s.get('holdout_pct', '—')}% held back · read after {s.get('measurement_window_days', '—')} days"},
                 {"k": "STOPS IF", "v": plain("; ".join(s.get("kill_criteria") or []) or "no stop rule written")},
                 {"k": "SUCCESS IS", "v": plain(f"{s.get('primary_kpi', '')} {s.get('target', '')}".strip())}]
        detail = {"id": sid, "name": plain(s.get("name") or sid), "version": f"v{s.get('version') or 1}", "meta": plain(f"{s.get('campaign_type', '')} · {str(s.get('transition', '')).replace('_', ' → ')} · {(s.get('audience') or {}).get('segment_family', '')}"),
                  "objective": plain(s.get("objective") or ""), "steps": steps, "rules": rules,
                  "position": f"{idx + 1} of {len(tabs)}", "prev": tabs[idx - 1]["id"] if idx > 0 else None, "next": tabs[idx + 1]["id"] if idx + 1 < len(tabs) else None,
                  "run": {"label": "RUN THIS PLAYBOOK NOW", "ask": f"run_sop('{sid}') as a dry run first, then propose the steps for approval with two compliant variants each; respect the caps and the holdout."}}
    lead = f"{len(tabs)} playbooks, each with its cap, holdout and stop rule written down. The one in front is complete on this screen; the rest are listed below it."
    return {"lead": lead, "tabs": tabs, "selected": sid, "detail": detail, "groups": [g for g in dict.fromkeys(t["group"] for t in tabs)]}


PLAY_GROUPS = {"onboarding": "Onboarding", "activation": "Activation", "retention": "Retention", "winback": "Winback", "market": "Market moments",
               "competition": "Competition", "education": "Education", "risk": "Risk", "compliance": "Compliance", "cohort_upload": "Cohorts", "newsletter": "Newsletter"}


def _short_name(name: str, limit: int = 46) -> str:
    """The tab label: the playbook name without its parenthetical, cut at a word if it is still long."""
    n = re.sub(r"\s*\([^)]*\)", "", name).strip(" ·")
    if len(n) <= limit:
        return n
    cut = n[:limit].rsplit(" ", 1)[0].rstrip(" ,;:·→")
    return cut + "…"
