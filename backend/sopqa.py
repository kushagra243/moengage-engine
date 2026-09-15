"""
Ask the SOP library — a grounded, cited answer to a plain-English question
about how we run things, in the shape the team already knows from VIDHI.

  ask("what are the steps in the liquidation recovery flow?")
  ask("who's the POC if a run is stuck at approval?")
  ask("who hands off what between the cohort and the send?")

How it works: every SOP is chunked into passages (objective, audience, each
step, cadence, measurement, compliance, ideas) alongside the ownership /
hand-off / escalation layer, the channel and product matrices, guardrails and
the skills. A BM25 search (no external dependencies) retrieves the passages
that matter, and the bulk-tier model distils them into an answer that may only
use those passages and must cite them as `sop_id § section`. With no model
configured the ranked passages are returned verbatim, clearly labelled.

Answers are private (everything is local), each query records what it cost
(shown in ₹/paise like the team's Slack assistant) and a daily cap keeps the
spend bounded. Questions the library could not answer are kept as knowledge
gaps — the queue of SOPs worth writing.
"""
from __future__ import annotations
import json
import math
import re
import time
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from .database import get_db, get_setting
from .security import audit, redact

STOP = {"the", "a", "an", "and", "or", "of", "to", "in", "for", "on", "is", "are", "was", "be", "do", "does", "how", "what", "when", "which", "that", "this", "it", "we", "our", "us", "i", "my", "you", "your", "with", "at", "by", "from", "as", "if", "then", "there", "their", "can", "should", "would", "will", "have", "has", "not", "no", "yes", "me", "please", "about", "any", "all", "get", "got"}
SYN = {
    "poc": ["owner", "contact", "escalate", "escalation"], "owner": ["owner", "escalate", "accountable"], "who": ["owner", "reviewer", "accountable"],
    "stuck": ["blocked", "escalate", "escalation"], "blocked": ["blocked", "escalate", "escalation"], "escalate": ["escalation", "blocked", "owner"],
    "handoff": ["handoff", "artefact", "segment"], "handover": ["handoff", "artefact", "segment"], "dependency": ["handoff", "segment", "owner"],
    "step": ["step", "segment", "sequence"], "steps": ["step", "segment", "sequence"], "process": ["segment", "walkthrough", "sequence"], "walkthrough": ["segment", "sequence"],
    "next": ["segment", "handoff"], "approval": ["approval", "approve", "proposal"], "approve": ["approval", "proposal"],
    "cohort": ["segment", "audience", "family"], "segment": ["audience", "cohort", "family"], "audience": ["segment", "cohort"],
    "holdout": ["holdout", "control"], "kpi": ["kpi", "target", "measurement"], "kill": ["kill", "guardrail"], "cap": ["frequency", "limit", "cadence"],
    "disclaimer": ["disclaimer", "compliance", "asci"], "compliance": ["compliance", "disclaimer", "banned"], "whatsapp": ["whatsapp", "utility", "dlt"],
    "onboard": ["segment", "owner", "walkthrough"], "joiner": ["segment", "owner", "walkthrough"], "new": ["segment", "owner"],
    "liquidation": ["liquidated", "liquidation", "recovery"], "deposit": ["deposit", "upi", "funded"], "dormant": ["dormant", "winback", "reactivation"],
    "delist": ["listing", "product", "request"], "listing": ["listing", "new_listing", "product"],
}
FIELD_BOOST = 3          # title/section tokens count this many times
MAX_PASSAGE = 900
_CORPUS: Dict[str, Any] = {"sig": None, "docs": [], "df": Counter(), "avg_len": 1.0}


# ── tokenisation ──────────────────────────────────────────────────────────────
def _stem(w: str) -> str:
    for suf in ("ing", "ed", "es", "s"):
        if len(w) > 4 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def _tok(text: str) -> List[str]:
    return [_stem(w) for w in re.findall(r"[a-z0-9_]+", (text or "").lower()) if w not in STOP and len(w) > 1]


def _expand(q: str) -> List[str]:
    toks = _tok(q)
    out = list(toks)
    for w in re.findall(r"[a-z]+", (q or "").lower()):
        for extra in SYN.get(w, []):
            out.append(_stem(extra))
    return out


# ── corpus ────────────────────────────────────────────────────────────────────
def _sig() -> str:
    from .sops import list_sops
    rows = list_sops(include_inactive=True)
    return f"{len(rows)}:{max((str(r.get('updated_at') or '') for r in rows), default='')}:{len(get_setting('sop_owners_json', '') or '')}"


def _doc(source: str, source_id: str, title: str, section: str, text: str) -> Dict[str, Any]:
    text = re.sub(r"\s+", " ", str(text or "")).strip()[:MAX_PASSAGE]
    return {"source": source, "source_id": source_id, "title": title, "section": section, "text": text, "ref": f"{source_id} § {section}"}


def _sop_docs(sop: Dict[str, Any]) -> List[Dict[str, Any]]:
    from . import sop_ownership as own
    sid, name = sop["id"], sop["name"]
    aud = sop.get("audience") or {}; fr = sop.get("frequency") or {}; comp = sop.get("compliance") or {}
    d = [_doc("sop", sid, name, "objective", f"{name}. {sop.get('objective', '')} Campaign type {sop.get('campaign_type')}, lifecycle transition {sop.get('transition')}."),
         _doc("sop", sid, name, "who it targets", f"{sop.get('user', '')} Segment family {aud.get('segment_family')}. {aud.get('description', '')} Minimum reach {aud.get('min_reach')}. Exclusions: {'; '.join(aud.get('exclusions') or [])}. Jurisdictions excluded: {'; '.join(aud.get('jurisdictions_excluded') or []) or 'none'}."),
         _doc("sop", sid, name, "cadence and limits", f"Runs over {sop.get('duration_days')} days, cadence {fr.get('cadence')}, at most {fr.get('max_messages_per_user_per_week')} messages per user per week, holdout {sop.get('holdout_pct')}%."),
         _doc("sop", sid, name, "measurement", f"Primary KPI {sop.get('primary_kpi')} target {sop.get('target')}, guardrail {sop.get('guardrail_metric')}, measured over {sop.get('measurement_window_days')} days. Kill criteria: {'; '.join(sop.get('kill_criteria') or [])}."),
         _doc("sop", sid, name, "compliance", f"Disclaimer carried on {', '.join(comp.get('disclaimer_channels') or []) or 'no channel'}. Banned angles: {', '.join(comp.get('banned_angles') or []) or 'none'}. Checks: {json.dumps(sop.get('checks') or {})}.")]
    for i, st in enumerate(sop.get("steps") or []):
        d.append(_doc("sop", sid, name, f"step {i + 1} ({st.get('channel')}, day {st.get('day')})",
                      f"Step {i + 1} of {name}: on day {st.get('day')} at {st.get('send_time_ist') or 'no fixed time'} IST on {st.get('channel')}, purpose {st.get('purpose')}. Copy brief: {st.get('copy_brief')}. {('Condition: ' + str(st.get('condition'))) if st.get('condition') else ''} {('TTL ' + str(st.get('ttl_hours')) + 'h.') if st.get('ttl_hours') else ''}"))
    if sop.get("ideas"):
        d.append(_doc("sop", sid, name, "ideas to test", "; ".join(sop["ideas"])))
    w = own.walkthrough(sop)
    o = w["owners"]
    d.append(_doc("sop", sid, name, "ownership", f"Primary owner {o['primary']}; accountable {o['accountable']}; reviewers {', '.join(o['reviewers']) or 'none'}. Per step: " + "; ".join(f"step {s['step'] + 1} {s['channel']} owned by {s['owner']} reviewed by {s['reviewer']}" for s in o["steps"])))
    d.append(_doc("sop", sid, name, "process segments", " ".join(f"{s['n']}. {s['name']}: {s['what']} — owner {s['owner']}, on {s['surface']} ({s['detail']})." for s in w["segments"])))
    d.append(_doc("sop", sid, name, "hand-offs", " ".join(f"{h['from']} hands {h['artefact']} to {h['to']} ({h['from_owner']} → {h['to_owner']}, {h['when']})." for h in w["handoffs"])))
    d.append(_doc("sop", sid, name, "escalation", " ".join(f"Blocked at {e['blocked_at']} ({e['symptom']}): contact {e['first']}, then {e['then']}, SLA {e['sla']}." for e in w["escalation"])))
    return d


def _library_docs() -> List[Dict[str, Any]]:
    from . import sop_ownership as own, guardrails
    from .sops import channel_matrix, product_cohort_matrix
    out: List[Dict[str, Any]] = []
    try:
        cm = channel_matrix()
        for r in cm.get("states") or []:
            out.append(_doc("matrix", "channel_matrix", "User state × channel", r["state"], f"For {r['state']} users: purposes {', '.join(r.get('purposes') or [])}. Avoid {', '.join(r.get('avoid') or [])}. SOPs {', '.join(r.get('sops') or [])}. Channels: " + "; ".join(f"{c} {'allowed' if v.get('allowed') else 'not used'} cap {v.get('cap')} {v.get('note') or ''}" for c, v in (r.get("channels") or {}).items())))
    except Exception:
        pass
    try:
        for c in product_cohort_matrix().get("cohorts") or []:
            out.append(_doc("matrix", "product_matrix", "Product cohort treatment", c.get("name", "product"), f"{c.get('name')}: lens {c.get('announcement_lens')}. Pillars {', '.join(c.get('pillars') or [])}. Cadence {c.get('cadence')}. Never {', '.join(c.get('never') or [])}. Cross-sell on intent only: {', '.join(c.get('cross_sell_on_intent_only') or [])}. SOPs {', '.join(c.get('sops') or [])}."))
    except Exception:
        pass
    try:
        L = guardrails.limits()
        out.append(_doc("guardrails", "comms_limits", "Communication limits", "hard caps", f"North star: {guardrails.north_star()}. Total {L.get('total_per_week')} touches per user per week. Per channel: " + "; ".join(f"{k} {v.get('per_day')}/day {v.get('per_week')}/week" for k, v in (L.get('per_user') or {}).items()) + f". Quiet hours {json.dumps(L.get('quiet_hours') or {})}. Stage overrides {json.dumps(L.get('stage_overrides') or {})}."))
    except Exception:
        pass
    d = own.directory()
    out.append(_doc("ownership", "roles", "Roles and who they are", "directory", "; ".join(f"{r['role']}: {r['does']}" + (f" — {r['person']}" if r.get('person') else "") for r in d["roles"].values())))
    for sg in own.SEGMENTS:
        sla = "2 working hours during market hours, next morning otherwise" if sg["n"] <= 6 else "same working day"
        out.append(_doc("ownership", "escalation", "Escalation ladder (any SOP, any run)", f"blocked at {sg['n']}. {sg['name']}",
                        f"Blocked at segment {sg['n']}, {sg['name']} — symptom: {sg['blocked']}. The POC is {own.who(sg['escalate_to'])}; that segment is owned by {own.who(sg['owner'])} and runs on {sg['surface']}. If it is still stuck, escalate to whoever is accountable for that SOP's KPI. SLA {sla}."))
    out.append(_doc("ownership", "escalation", "Escalation ladder (any SOP, any run)", "summary",
                    "Who to contact when blocked, by segment: " + "; ".join(f"{s['n']}. {s['name']} → {own.who(s['escalate_to'])}" for s in own.SEGMENTS) + "."))
    out.append(_doc("ownership", "handoffs", "Hand-offs between segments (any SOP)", "who hands what to whom",
                    " ".join(f"{a['n']}. {a['name']} ({own.who(a['owner'])}) hands '{a['outputs']}' to {b['n']}. {b['name']} ({own.who(b['owner'])})." for a, b in zip(own.SEGMENTS, own.SEGMENTS[1:]))))
    out.append(_doc("ownership", "segments", "The ten process segments every SOP passes through", "overview", " ".join(f"{s['n']}. {s['name']}: {s['what']} — owner {s['owner']}, surface {s['surface']}." for s in d["segments"])))
    for owner, info in (d.get("by_owner") or {}).items():
        out.append(_doc("ownership", "by_owner", "Who owns which SOPs", owner, f"{owner} is the primary owner of {info['count']} SOPs: {', '.join(info['sops'])}."))
    try:
        from .skills import list_skills, read_skill
        for s in list_skills():
            body = (read_skill(s["name"], max_chars=6000) or {}).get("content") or ""
            for chunk in re.split(r"\n##\s+", body)[:12]:
                head = chunk.strip().split("\n", 1)[0][:60] or "overview"
                if len(chunk.strip()) > 80:
                    out.append(_doc("skill", s["name"], s["name"], head, chunk))
    except Exception:
        pass
    return out


def corpus() -> Dict[str, Any]:
    from .sops import list_sops, get_sop
    sig = _sig()
    if _CORPUS.get("sig") == sig and _CORPUS.get("docs"):
        return _CORPUS
    docs: List[Dict[str, Any]] = []
    for row in list_sops(include_inactive=True):
        sop = get_sop(row["id"])
        if sop:
            docs.extend(_sop_docs(sop))
    docs.extend(_library_docs())
    df: Counter = Counter()
    total = 0
    for d in docs:
        toks = _tok(d["text"]) + _tok(d["title"] + " " + d["section"]) * FIELD_BOOST
        d["_toks"] = toks
        d["_tf"] = Counter(toks)
        total += len(toks)
        for t in set(toks):
            df[t] += 1
    _CORPUS.update({"sig": sig, "docs": docs, "df": df, "avg_len": (total / max(1, len(docs)))})
    return _CORPUS


def search(question: str, k: int = 8) -> List[Dict[str, Any]]:
    c = corpus()
    docs, df, avg = c["docs"], c["df"], c["avg_len"]
    n = max(1, len(docs))
    q = _expand(question)
    if not q:
        return []
    k1, b = 1.5, 0.75
    scored = []
    phrase = re.sub(r"\s+", " ", (question or "").lower()).strip()
    for d in docs:
        tf, dl = d["_tf"], max(1, len(d["_toks"]))
        s = 0.0
        for t in q:
            f = tf.get(t, 0)
            if not f:
                continue
            idf = math.log(1 + (n - df.get(t, 0) + 0.5) / (df.get(t, 0) + 0.5))
            s += idf * (f * (k1 + 1)) / (f + k1 * (1 - b + b * dl / avg))
        if s and len(phrase) > 12 and phrase[:40] in d["text"].lower():
            s *= 1.25
        if s:
            scored.append((s, d))
    scored.sort(key=lambda x: -x[0])
    out = []
    seen_sops: Counter = Counter()
    for s, d in scored:
        if seen_sops[d["source_id"]] >= 4:          # keep the answer from being one SOP repeated
            continue
        seen_sops[d["source_id"]] += 1
        out.append({"score": round(s, 2), "ref": d["ref"], "source": d["source"], "source_id": d["source_id"], "title": d["title"], "section": d["section"], "text": d["text"]})
        if len(out) >= k:
            break
    return out


def coverage_of(question: str, hits: List[Dict[str, Any]]) -> float:
    """How much of the question the library actually covers: mostly term overlap with the retrieved passages
    (a question about things we have no SOP for scores near zero even when generic words match), plus the top score."""
    if not hits:
        return 0.0
    terms = set(_tok(question))
    if not terms:
        return 0.0
    found = set()
    for h in hits[:4]:
        toks = set(_tok(h["text"]) + _tok(h["title"] + " " + h["section"]))
        found |= terms & toks
    term_cov = len(found) / len(terms)
    score_cov = min(1.0, hits[0]["score"] / 25.0)
    return round(min(1.0, 0.7 * term_cov + 0.3 * score_cov), 2)


# ── storage, cost and the daily cap ───────────────────────────────────────────
def init_tables() -> None:
    conn = get_db()
    conn.execute("""CREATE TABLE IF NOT EXISTS sop_queries (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, actor TEXT, question TEXT, answer TEXT,
                    citations_json TEXT, coverage REAL, cost_usd REAL, model TEXT, ms INTEGER, gap INTEGER DEFAULT 0)""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sopq_day ON sop_queries(created_at)")
    conn.commit(); conn.close()


def _ledger_total() -> float:
    try:
        from .llm.usage import init_usage_tables
        init_usage_tables()
        conn = get_db(); v = conn.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM llm_usage").fetchone()[0]; conn.close()
        return float(v or 0)
    except Exception:
        return 0.0


def _inr(usd: float) -> float:
    try:
        rate = float(get_setting("usd_inr", "88") or 88)
    except Exception:
        rate = 88.0
    return round(usd * rate, 4)


def money(usd: float) -> str:
    inr = _inr(usd)
    if inr <= 0:
        return "no model cost"
    return f"{int(round(inr * 100))} paise" if inr < 1 else f"₹{inr:.2f}"


def quota() -> Dict[str, Any]:
    init_tables()
    try:
        limit = int(get_setting("sopqa_daily_limit", "50") or 50)
    except Exception:
        limit = 50
    conn = get_db()
    used = conn.execute("SELECT COUNT(*) FROM sop_queries WHERE date(created_at)=date('now') AND model IS NOT NULL").fetchone()[0]
    spend = conn.execute("SELECT COALESCE(SUM(cost_usd),0) FROM sop_queries WHERE date(created_at)=date('now')").fetchone()[0]
    conn.close()
    return {"limit": limit, "used": int(used), "left": max(0, limit - int(used)), "spend_today_usd": round(float(spend or 0), 4), "spend_today": money(float(spend or 0))}


# ── answering ─────────────────────────────────────────────────────────────────
SYSTEM = ("You are the SOP assistant for CoinDCX's lifecycle-marketing team. Answer ONLY from the numbered passages provided; they are our own SOP library and operating docs. "
          "Be concrete and practical: name the steps in order, the owner of each, the hand-off, the KPI, the exclusions. Cite every claim as [sop_id § section] using the refs given. "
          "If the passages do not cover the question, say exactly what is missing and which SOP should be written — never invent a process. Never name a competitor or liquidity venue in user-facing copy examples. "
          "Format: a one-line direct answer, then numbered steps or bullets, then a short 'who to contact' line when the question is about ownership or being blocked.")


def _deterministic(question: str, hits: List[Dict[str, Any]]) -> str:
    if not hits:
        return "Nothing in the SOP library matches that question. Write the SOP (SOP Library → define) or ask the team; the question has been logged as a knowledge gap."
    lines = ["**No model configured, so here are the source passages themselves (not a distilled answer).**", ""]
    for h in hits[:5]:
        lines.append(f"**{h['title']} · {h['section']}** [{h['ref']}]")
        lines.append(h["text"][:600])
        lines.append("")
    return "\n".join(lines)


def ask(question: str, actor: str = "user", k: int = 8, force: bool = False) -> Dict[str, Any]:
    """Search the library, distil a cited answer, record what it cost. Private, local, approval-free (it only reads)."""
    init_tables()
    question = (question or "").strip()
    if len(question) < 4:
        return {"error": "ask a full question, e.g. 'what are the steps in the liquidation recovery flow?'"}
    q = quota()
    hits = search(question, k=k)
    coverage = coverage_of(question, hits)
    t0 = time.time()
    from .llm.provider import LLMClient, llm_settings, LLMError
    cfg = llm_settings()
    have_model = bool(cfg["api_key"]) or cfg["provider"] == "claude_cli"
    answer, model, cost = None, None, 0.0
    if have_model and hits and (q["left"] > 0 or force):
        before = _ledger_total()
        passages = "\n\n".join(f"[{i + 1}] ref={h['ref']} | {h['title']} · {h['section']}\n{h['text']}" for i, h in enumerate(hits))
        try:
            client = LLMClient(); client.purpose = "analysis"
            r = client.chat([{"role": "system", "content": SYSTEM},
                             {"role": "user", "content": f"QUESTION: {question}\n\nPASSAGES (the only source you may use):\n{passages}"}],
                            tools=None, max_tokens=900, temperature=0.1, tier="bulk")
            answer = (r.get("content") or "").strip() or None
            model = r.get("model")
        except (LLMError, ValueError) as e:
            answer = None
            model = None
            hits = hits or []
            answer = f"_Model unavailable ({redact(str(e))[:120]}); showing the source passages._\n\n" + _deterministic(question, hits)
        cost = max(0.0, _ledger_total() - before)
    elif have_model and q["left"] <= 0 and not force:
        answer = (f"**Daily query limit reached** ({q['used']}/{q['limit']} model-answered questions today, {q['spend_today']} spent). "
                  "Here are the source passages so you are not blocked; raise `sopqa_daily_limit` in Engine → Settings if the team needs more.\n\n") + _deterministic(question, hits)
    if not answer:
        answer = _deterministic(question, hits)
    ms = int((time.time() - t0) * 1000)
    gap = 1 if (coverage < 0.3 or not hits) else 0
    cites = [{"ref": h["ref"], "source": h["source"], "source_id": h["source_id"], "title": h["title"], "section": h["section"], "score": h["score"], "snippet": h["text"][:220]} for h in hits[:6]]
    conn = get_db()
    conn.execute("INSERT INTO sop_queries (actor, question, answer, citations_json, coverage, cost_usd, model, ms, gap) VALUES (?,?,?,?,?,?,?,?,?)",
                 (actor, question[:500], (answer or "")[:8000], json.dumps(cites, default=str), coverage, cost, model, ms, gap))
    conn.execute("DELETE FROM sop_queries WHERE id NOT IN (SELECT id FROM sop_queries ORDER BY id DESC LIMIT 2000)")
    conn.commit(); conn.close()
    audit("sopqa.ask", {"chars": len(question), "coverage": coverage, "model": model, "cost_usd": round(cost, 5), "gap": bool(gap)}, actor=actor)
    return {"question": question, "answer": answer, "citations": cites, "coverage": coverage, "gap": bool(gap), "model": model, "grounded": bool(model),
            "cost_usd": round(cost, 5), "cost": money(cost), "ms": ms, "quota": quota(),
            "note": "answers come only from our own SOP library and operating docs; nothing here is sent anywhere and nothing is queued to MoEngage"}


def history(limit: int = 20, actor: Optional[str] = None) -> List[Dict[str, Any]]:
    init_tables(); conn = get_db()
    q = "SELECT id, created_at, actor, question, coverage, cost_usd, model, gap FROM sop_queries" + (" WHERE actor=?" if actor else "") + " ORDER BY id DESC LIMIT ?"
    rows = [dict(r) for r in conn.execute(q, ((actor, limit) if actor else (limit,))).fetchall()]
    conn.close()
    for r in rows:
        r["cost"] = money(float(r.pop("cost_usd") or 0))
    return rows


def gaps(limit: int = 20) -> Dict[str, Any]:
    """Questions the library could not answer — the queue of SOPs worth writing."""
    init_tables(); conn = get_db()
    rows = [dict(r) for r in conn.execute("""SELECT question, COUNT(*) asked, MAX(created_at) last_asked, AVG(coverage) cov FROM sop_queries WHERE gap=1
                                             GROUP BY lower(trim(question)) ORDER BY asked DESC, last_asked DESC LIMIT ?""", (limit,)).fetchall()]
    total = conn.execute("SELECT COUNT(*) FROM sop_queries").fetchone()[0]
    answered = conn.execute("SELECT COUNT(*) FROM sop_queries WHERE gap=0").fetchone()[0]
    conn.close()
    return {"gaps": [{**r, "cov": round(float(r["cov"] or 0), 2)} for r in rows], "questions_total": int(total), "answered": int(answered),
            "answer_rate": round(100 * int(answered) / max(1, int(total))), "next": "write the SOP (define_sop) or add the section to the skill that should cover it"}


def topics() -> Dict[str, Any]:
    """What the library knows — the 'WHAT IT KNOWS' line for the team."""
    from .sops import list_sops
    from . import sop_ownership as own
    rows = list_sops()
    types = Counter(r.get("campaign_type") for r in rows)
    trans = Counter(r.get("transition") for r in rows)
    fams = sorted({(r.get("audience") or {}).get("segment_family") for r in rows if (r.get("audience") or {}).get("segment_family")})
    chans = Counter()
    for r in rows:
        for s in r.get("steps") or []:
            chans[str(s.get("channel") or "").lower()] += 1
    c = corpus()
    return {"sops": len(rows), "passages": len(c["docs"]), "campaign_types": dict(types.most_common()), "transitions": dict(trans.most_common()), "channels": dict(chans.most_common()),
            "cohort_families": fams[:40], "roles": [r["role"] for r in own.roles().values()],
            "answers": ["process walkthroughs (the steps, in order, with days and channels)", "inter-team dependencies (who hands off what, to whom, when)",
                        "ownership and escalation (who is the POC when you are blocked, and the SLA)", "next-step clarity mid-process",
                        "onboarding (how a new joiner runs their first campaign end to end)", "limits, exclusions, holdouts, KPIs and kill rules"],
            "quota": quota()}
