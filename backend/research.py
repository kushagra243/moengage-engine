"""
Methodology radar — hunts what is being released every day in lifecycle /
CRM / growth marketing (platform releases, practitioner blogs, papers, case
studies, crypto-exchange growth news), scores it for *our* context (Indian
crypto exchange on MoEngage), and turns the ones that matter into experiment
ideas or proposed skill updates the team approves.

Free sources only (RSS/Atom + arXiv API + Google News queries) through the
`research` network scope. Items persist in `research_items`; the researcher
persona reads `radar()`; `propose_skill_update()` queues a dated addition to a
skill file as proposal kind `skill_update` (approve → the skill grows, and the
agent loads it on the next call).
"""
from __future__ import annotations
import json
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

from .database import get_db, get_setting
from .security import audit, redact

UA = "Mozilla/5.0 (compatible; moengage-engine research radar)"
FEEDS: List[Dict[str, str]] = [
    {"name": "MoEngage blog", "url": "https://www.moengage.com/feed/", "kind": "platform", "weight": "1.0"},
    {"name": "MoEngage help – release notes", "url": "https://news.google.com/rss/search?q=MoEngage+release+OR+launches+OR+Merlin&hl=en-IN&gl=IN&ceid=IN:en", "kind": "platform", "weight": "0.9"},
    {"name": "Google News · Braze / Iterable / Klaviyo releases", "url": "https://news.google.com/rss/search?q=(Braze+OR+Iterable+OR+Klaviyo+OR+Insider+OR+CleverTap)+(launches+OR+agent+OR+release+OR+AI)&hl=en-US&gl=US&ceid=US:en", "kind": "platform", "weight": "0.8"},
    {"name": "Google News · Salesforce / Adobe / HubSpot marketing agents", "url": "https://news.google.com/rss/search?q=(Agentforce+OR+%22Adobe+Journey+Optimizer%22+OR+%22HubSpot+Breeze%22)+marketing&hl=en-US&gl=US&ceid=US:en", "kind": "platform", "weight": "0.6"},
    {"name": "CXL", "url": "https://cxl.com/blog/feed/", "kind": "practice", "weight": "0.9"},
    {"name": "GrowthHackers", "url": "https://growthhackers.com/feed", "kind": "practice", "weight": "0.7"},
    {"name": "Reforge", "url": "https://www.reforge.com/blog/rss.xml", "kind": "practice", "weight": "0.9"},
    {"name": "Google News · lifecycle marketing", "url": "https://news.google.com/rss/search?q=%22lifecycle+marketing%22+OR+%22CRM+marketing%22+OR+%22retention+marketing%22&hl=en-IN&gl=IN&ceid=IN:en", "kind": "news", "weight": "0.6"},
    {"name": "Google News · incrementality", "url": "https://news.google.com/rss/search?q=incrementality+testing+OR+%22holdout+group%22+OR+%22uplift+modeling%22+marketing&hl=en-US&gl=US&ceid=US:en", "kind": "news", "weight": "0.8"},
    {"name": "Google News · marketing AI agents", "url": "https://news.google.com/rss/search?q=%22marketing+agent%22+OR+%22agentic+marketing%22+OR+%22AI+agents%22+CRM+lifecycle&hl=en-US&gl=US&ceid=US:en", "kind": "news", "weight": "0.7"},
    {"name": "Google News · crypto exchange growth India", "url": "https://news.google.com/rss/search?q=crypto+exchange+India+users+OR+retention+OR+onboarding+OR+campaign&hl=en-IN&gl=IN&ceid=IN:en", "kind": "news", "weight": "0.8"},
    {"name": "Google News · ASCI VDA / crypto ads India", "url": "https://news.google.com/rss/search?q=ASCI+crypto+OR+VDA+advertising+OR+%22crypto+ads%22+India&hl=en-IN&gl=IN&ceid=IN:en", "kind": "compliance", "weight": "1.0"},
    {"name": "arXiv · uplift & lifecycle", "url": "https://export.arxiv.org/api/query?search_query=all:%22uplift+modeling%22+OR+all:%22customer+churn%22+OR+all:%22push+notification%22+OR+all:%22incrementality%22&sortBy=submittedDate&sortOrder=descending&max_results=25", "kind": "paper", "weight": "0.7"},
]
TAGS: Dict[str, List[str]] = {
    "incrementality": ["incrementality", "holdout", "uplift", "causal", "lift test", "control group", "geo test", "ghost ads"],
    "experimentation": ["a/b", "ab test", "experiment", "multi-armed", "bandit", "sequential test", "sample size", "power"],
    "personalisation": ["personaliz", "personalis", "next best action", "recommendation", "1:1", "one-to-one", "affinity", "propensity", "churn prediction", "predictive"],
    "agentic": ["agent", "agentic", "copilot", "autonomous", "llm", "generative", "gpt", "claude", "merlin", "sherpa", "mcp"],
    "send_time": ["send time", "send-time", "best time", "frequency cap", "fatigue", "cadence", "quiet hours", "sto"],
    "channel_push": ["push notification", "in-app", "app inbox", "cards", "rich push"],
    "channel_whatsapp": ["whatsapp", "rcs", "sms", "dlt", "utility template"],
    "channel_email": ["email", "deliverability", "subject line", "open rate", "click rate"],
    "activation": ["onboarding", "activation", "first trade", "first deposit", "kyc", "time to value", "aha moment"],
    "retention": ["retention", "churn", "win-back", "winback", "reactivation", "dormant", "lifecycle"],
    "compliance": ["asci", "compliance", "regulat", "sebi", "rbi", "fiu", "disclaimer", "vda", "advertis"],
    "crypto_india": ["coindcx", "coinswitch", "wazirx", "zebpay", "mudrex", "delta exchange", "india crypto", "indian crypto", "tds", "30%"],
    "crypto_global": ["binance", "coinbase", "bybit", "okx", "kraken", "exchange", "perpetual", "perps", "stablecoin", "hyperliquid"],
    "measurement": ["attribution", "mmm", "marketing mix", "incremental", "roi", "ltv", "cohort analysis", "north star"],
    "journeys": ["journey", "flow", "orchestration", "trigger", "event-based", "real-time", "business event"],
    "copy": ["copywriting", "subject line", "creative", "hinglish", "vernacular", "tone", "brand voice"],
    "benchmarks": ["benchmark", "industry report", "state of", "survey", "index"],
}
OUR_CONTEXT_BOOST = {"compliance": 1.6, "crypto_india": 1.6, "incrementality": 1.4, "activation": 1.3, "retention": 1.3, "journeys": 1.3, "agentic": 1.2, "send_time": 1.2, "personalisation": 1.1, "channel_whatsapp": 1.2, "channel_push": 1.1}
SKILL_FOR_TAG = {"incrementality": "trader-analytics-playbook", "experimentation": "clm-operator", "personalisation": "product-cohort-playbook", "agentic": "agent-rulebook", "send_time": "campaign-sops", "channel_push": "crypto-copywriting", "channel_whatsapp": "crypto-compliance-copy", "channel_email": "crypto-copywriting",
                 "activation": "clm-campaign-playbook", "retention": "clm-campaign-playbook", "compliance": "crypto-compliance-copy", "crypto_india": "competitive-intelligence", "crypto_global": "competitive-intelligence", "measurement": "trader-analytics-playbook", "journeys": "campaign-sops", "copy": "crypto-copywriting", "benchmarks": "clm-campaign-playbook"}


def init_tables() -> None:
    conn = get_db()
    conn.execute("""CREATE TABLE IF NOT EXISTS research_items (id INTEGER PRIMARY KEY AUTOINCREMENT, url TEXT UNIQUE, title TEXT, source TEXT, kind TEXT, published TEXT, summary TEXT, tags_json TEXT, relevance REAL, status TEXT DEFAULT 'new', first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP, note TEXT)""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_research_rel ON research_items(status, relevance)")
    conn.commit(); conn.close()


def classify(title: str, summary: str = "") -> Dict[str, Any]:
    text = f"{title} {summary}".lower()
    hits: Dict[str, int] = {}
    for tag, words in TAGS.items():
        n = sum(1 for w in words if w in text)
        if n:
            hits[tag] = n
    score = 0.0
    for tag, n in hits.items():
        score += min(3, n) * OUR_CONTEXT_BOOST.get(tag, 1.0)
    return {"tags": sorted(hits, key=lambda t: -hits[t])[:6], "score": round(score, 2)}


def _fetch_feed(f: Dict[str, str], limit: int = 20) -> List[Dict[str, Any]]:
    import feedparser
    from .security import guarded_session
    s = guarded_session("research")
    r = s.get(f["url"], timeout=20, headers={"User-Agent": UA, "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*"})
    r.raise_for_status()
    fp = feedparser.parse(r.content)
    out = []
    for e in fp.entries[:limit]:
        ts = None
        for k in ("published_parsed", "updated_parsed"):
            if getattr(e, k, None):
                ts = datetime.fromtimestamp(time.mktime(getattr(e, k)), tz=timezone.utc).isoformat(); break
        title = re.sub(r"\s+", " ", (getattr(e, "title", "") or "")).strip()
        summ = re.sub(r"<[^>]+>", " ", getattr(e, "summary", "") or getattr(e, "description", "") or "")
        summ = re.sub(r"\s+", " ", summ).strip()[:600]
        link = getattr(e, "link", "") or ""
        if title and link:
            out.append({"title": title[:220], "url": link[:500], "published": ts, "summary": summ, "source": f["name"], "kind": f["kind"], "weight": float(f.get("weight", "0.7"))})
    return out


def refresh(max_feeds: Optional[int] = None) -> Dict[str, Any]:
    """Pull every feed, classify, persist new items (URL-unique), decay old ones. Failures per feed are recorded, never raised."""
    init_tables(); added = 0; seen = 0; errors: List[str] = []
    conn = get_db()
    for f in FEEDS[: (max_feeds or len(FEEDS))]:
        try:
            items = _fetch_feed(f)
        except Exception as e:
            errors.append(f"{f['name']}: {redact(str(e))[:80]}"); continue
        for it in items:
            seen += 1
            c = classify(it["title"], it["summary"])
            if not c["tags"]:
                continue
            age_days = 0.0
            try:
                age_days = max(0.0, (datetime.now(timezone.utc) - datetime.fromisoformat(it["published"])).total_seconds() / 86400) if it["published"] else 7.0
            except Exception:
                age_days = 7.0
            rel = round(c["score"] * it["weight"] * (1.0 if age_days <= 7 else 0.7 if age_days <= 30 else 0.4), 2)
            try:
                cur = conn.execute("INSERT OR IGNORE INTO research_items (url, title, source, kind, published, summary, tags_json, relevance) VALUES (?,?,?,?,?,?,?,?)", (it["url"], it["title"], it["source"], it["kind"], it["published"], it["summary"], json.dumps(c["tags"]), rel))
                added += cur.rowcount or 0
            except Exception:
                pass
    conn.execute("DELETE FROM research_items WHERE status='new' AND first_seen < datetime('now', '-60 days')")
    conn.commit(); conn.close()
    audit("research.refresh", {"seen": seen, "added": added, "errors": len(errors)}, actor="research")
    return {"seen": seen, "added": added, "errors": errors, "feeds": len(FEEDS)}


def _why(tags: List[str]) -> str:
    bits = {"incrementality": "our readouts are pre/post; anything that hardens incrementality raises the credibility of every win", "compliance": "India rules move; a change here can invalidate live copy", "crypto_india": "direct competitor or regulator context", "activation": "activation is our largest structural gap (funded → first trade, second trade)",
            "retention": "retention journeys carry the P0 backlog", "journeys": "feeds the Signal Bridge / business-event design", "agentic": "how the platforms are wiring agents — what to adopt, what to avoid", "send_time": "we run cohort-level caps; per-user timing is where MoEngage Sherpa can add lift", "personalisation": "own-numbers content is the next step after cohort lenses",
            "channel_whatsapp": "WhatsApp utility is our best-read channel in India", "channel_push": "push is our main channel; format changes matter", "experimentation": "every proposal is an experiment; better designs = faster learning", "measurement": "north-star and Flight Plan reporting", "copy": "variants and Hinglish quality", "benchmarks": "recalibrate our directional ranges"}
    return "; ".join(bits[t] for t in tags[:3] if t in bits) or "adjacent method worth a look"


def _experiment(tags: List[str]) -> str:
    if "incrementality" in tags or "experimentation" in tags:
        return "re-run one live journey with a 20% holdout and the new design; read incremental lift, not CTR"
    if "activation" in tags:
        return "apply to the funded → first-trade SOP as a one-variable test on the D1 step"
    if "retention" in tags:
        return "test on the slipping check-in cohort vs the current SOP, 14-day window"
    if "send_time" in tags:
        return "enable best-time-to-send on the weekly recap with a fixed-time 10% control"
    if "journeys" in tags:
        return "prototype as a Signal Bridge rule + business-event campaign on one cohort"
    if "compliance" in tags:
        return "run compliance_sweep and sop_india_review against the new rule; fix flags before any send"
    if "copy" in tags or "channel_push" in tags:
        return "two-variant copy test on the highest-volume push, 20% holdout"
    return "write the hypothesis with ICE and pick the smallest cohort where the effect would be readable in 14 days"


def radar(limit: int = 30, status: Optional[str] = "new", tag: Optional[str] = None) -> Dict[str, Any]:
    init_tables(); conn = get_db()
    q = "SELECT * FROM research_items" + (" WHERE status=?" if status else "") + " ORDER BY relevance DESC, first_seen DESC LIMIT ?"
    rows = [dict(r) for r in conn.execute(q, ((status, limit * 3) if status else (limit * 3,))).fetchall()]
    counts = {r["status"]: r["n"] for r in conn.execute("SELECT status, COUNT(*) n FROM research_items GROUP BY status").fetchall()}
    last = conn.execute("SELECT MAX(first_seen) FROM research_items").fetchone()[0]
    conn.close()
    items = []
    tag_counts: Dict[str, int] = {}
    for r in rows:
        tags = json.loads(r.pop("tags_json") or "[]")
        for t in tags:
            tag_counts[t] = tag_counts.get(t, 0) + 1
        if tag and tag not in tags:
            continue
        items.append({**r, "tags": tags, "why_it_matters": _why(tags), "experiment": _experiment(tags), "skill": SKILL_FOR_TAG.get(tags[0]) if tags else None})
    return {"items": items[:limit], "counts": counts, "tags": sorted(tag_counts.items(), key=lambda kv: -kv[1])[:14], "last_fetch": last, "feeds": [{"name": f["name"], "kind": f["kind"]} for f in FEEDS],
            "note": "free sources only; relevance = tag weight × source weight × recency; the scout persona turns items into ideas or skill updates you approve"}


def set_status(item_id: int, status: str, note: str = "", actor: str = "user") -> Dict[str, Any]:
    if status not in ("new", "reviewed", "adopted", "dismissed"):
        return {"error": "bad status"}
    init_tables(); conn = get_db(); conn.execute("UPDATE research_items SET status=?, note=? WHERE id=?", (status, note[:500], item_id)); conn.commit(); conn.close()
    audit("research.status", {"id": item_id, "status": status}, actor=actor)
    return {"ok": True, "id": item_id, "status": status}


# ── skill updates (approval-gated learning) ───────────────────────────────────
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def propose_skill_update(skill: str, section_title: str, text: str, source_url: str = "", rationale: str = "", created_by: str = "agent") -> Dict[str, Any]:
    from . import approvals
    from .skills import list_skills
    names = {s["name"] for s in list_skills()}
    safe = re.sub(r"[^a-z0-9_-]", "", (skill or "").lower())
    if safe not in names:
        return {"error": f"unknown skill {skill}", "available": sorted(names)}
    if len((text or "").strip()) < 40:
        return {"error": "text too short: write the method, when it applies to us, and what evidence would prove it"}
    payload = {"skill": safe, "section_title": section_title[:120], "text": text[:4000], "source_url": source_url[:400]}
    p = approvals.propose("skill_update", f"Skill update: {safe} · {section_title[:60]}", payload, rationale or f"New methodology for {safe}: {section_title}", risk="low", created_by=created_by)
    return {"proposal_id": p["id"], "status": p["status"], "preview": p.get("preview"), "note": "approve → the section is appended to the skill and the agent loads it on its next call"}


def _su_validate(p: Dict[str, Any]) -> None:
    from .skills import list_skills
    if p.get("skill") not in {s["name"] for s in list_skills()}:
        raise ValueError("unknown skill")
    if not p.get("text") or not p.get("section_title"):
        raise ValueError("section_title and text are required")


def _su_preview(p: Dict[str, Any]) -> Dict[str, Any]:
    path = os.path.join(ROOT, ".claude", "skills", p["skill"], "SKILL.md")
    size = os.path.getsize(path) if os.path.exists(path) else 0
    return {"status": "ready", "file": os.path.relpath(path, ROOT), "current_chars": size, "adds_chars": len(p.get("text") or "") + 60, "section": f"## {p['section_title']} (added {datetime.now().date().isoformat()})", "source": p.get("source_url")}


def _su_execute(p: Dict[str, Any]) -> Dict[str, Any]:
    path = os.path.join(ROOT, ".claude", "skills", p["skill"], "SKILL.md")
    block = f"\n\n## {p['section_title']} (added {datetime.now().date().isoformat()}, via methodology radar)\n{p['text'].strip()}\n" + (f"\nSource: {p['source_url']}\n" if p.get("source_url") else "")
    with open(path, "a", encoding="utf-8") as f:
        f.write(block)
    audit("skill.updated", {"skill": p["skill"], "section": p["section_title"], "chars": len(block)}, actor="skill_update")
    return {"ok": True, "file": os.path.relpath(path, ROOT), "appended_chars": len(block)}


def register() -> None:
    from .approvals import register_executor
    register_executor("skill_update", _su_execute, _su_preview, _su_validate)
