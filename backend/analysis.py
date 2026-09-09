"""
Deep per-campaign analysis.

For a campaign we assemble a *dossier* from everything the engine knows —
taxonomy facets, the live row (segment, tags, delivery type, goals, content
type), 30-day snapshot history, trend statistics, funnel diagnosis, peers in
the same facet groups, and today's market regime — and ask the bulk-tier
model (free on OpenRouter) for a structured expert read: segment insight,
user insight, content review, process review, what worked / what did not,
ranked next actions, an experiment, risks and confidence. Results are stored
per campaign with the dossier hash so we only re-analyse when data changed.
With no model configured, the deterministic dossier + diagnosis is returned
as the analysis (clearly labelled).
"""
from __future__ import annotations
import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .database import get_db
from .security import redact


def init_analysis_tables() -> None:
    conn = get_db()
    conn.execute("""CREATE TABLE IF NOT EXISTS campaign_analyses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        campaign_id TEXT NOT NULL, campaign_name TEXT, source TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        dossier_hash TEXT, model TEXT, tier TEXT,
        analysis_json TEXT, dossier_json TEXT
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_an_campaign ON campaign_analyses(campaign_id, created_at)")
    conn.commit(); conn.close()


def _hash(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:16]


def build_dossier(campaign_id: str) -> Dict[str, Any]:
    from .moengage import MoEngageClient
    from .taxonomy import classify, group
    from .anomaly.store import normalise_campaign, get_history
    from .anomaly.diagnose import diagnose_campaign
    from .market import market_context
    c = MoEngageClient()
    rows = c.get_campaigns()
    camp = next((r for r in rows if str(r.get("id")) == str(campaign_id)), None)
    if not camp:
        raise ValueError(f"campaign {campaign_id} not found")
    tax = classify(camp); n = normalise_campaign(camp)
    hist = get_history(campaign_id, c.mode, 30)
    diag = diagnose_campaign(campaign_id, c.mode) if hist else {}
    # peers: same programme+cohort, and same value/propensity facets
    peers = []
    for r in rows:
        if str(r.get("id")) == str(campaign_id):
            continue
        t = classify(r)
        if t["group_key"] == tax["group_key"] or (t["cohort"] == tax["cohort"] and t["programme"] == tax["programme"]):
            pn = normalise_campaign(r)
            if not pn["stats_missing"]:
                peers.append({"name": t["name"], "group_key": t["group_key"], "delivered": pn["delivered_count"], "ctr": pn["ctr"], "delivery_rate": pn["delivery_rate"], "conversion_rate": pn["conversion_rate"]})
    peers.sort(key=lambda p: -(p["delivered"] or 0))
    facet_groups = {f: [g for g in group(rows, f) if g["with_stats"]][:6] for f in ("propensity", "value", "trader", "cohort")}
    try:
        mk = market_context(force=False)
        regime = {"label": ((mk.get("crypto") or {}).get("regime") or {}).get("label"), "narrative": (mk.get("narrative") or "")[:600],
                  "angle_policy": (mk.get("hooks") or {}).get("angle_policy")}
    except Exception:
        regime = {}
    dossier = {
        "campaign": {k: camp.get(k) for k in ("id", "name", "channel", "status", "target_segment", "tags", "delivery_type", "content_type", "conversion_goals", "created_by", "last_run", "is_all_user_campaign", "segment_count", "stats_missing")},
        "taxonomy": tax,
        "metrics_today": {k: n.get(k) for k in ("sent_count", "delivered_count", "delivery_rate", "opened_count", "ctr", "conversions", "conversion_rate", "revenue_generated")},
        "raw_stats": camp.get("_stats_raw") or {},
        "history_days": len(hist),
        "history": [{k: h.get(k) for k in ("snapshot_date", "sent_count", "delivery_rate", "ctr", "conversion_rate", "revenue_generated")} for h in hist[-14:]],
        "diagnosis": {k: diag.get(k) for k in ("headline", "severity", "funnel_lines", "likely_causes", "options", "so_what")} if diag else {},
        "peers_same_group": peers[:8],
        "facet_groups": facet_groups,
        "market": regime,
        "source": c.mode,
    }
    dossier["hash"] = _hash({k: v for k, v in dossier.items() if k not in ("market",)})
    return dossier


ANALYSIS_SCHEMA = {
    "verdict": "one sentence: what this campaign is doing and whether to keep / fix / scale / stop",
    "segment_insight": "who is really being targeted (from taxonomy facets, segment, peers) and whether that audience fits the message; name the facet contrasts that matter",
    "user_insight": "what these users are likely doing / feeling given lifecycle stage, product, propensity/value tier and today's market regime",
    "content_review": "what the message type/content_type/goals imply about copy, offer and CTA; specific critique and rewrite direction (no forecasts, no buy/sell instructions)",
    "process_review": "delivery type, trigger vs blast, frequency, timing, holdout/goals, deliverability hygiene — what is missing procedurally",
    "what_worked": ["evidence-backed positives"],
    "what_did_not": ["evidence-backed negatives"],
    "next_actions": [{"action": "", "why": "cite a number or facet", "how_in_moengage": "", "effort": "low|medium|high", "expected_effect": "", "priority": 1}],
    "experiment": {"hypothesis": "", "primary_kpi": "", "holdout_pct": 20, "window_days": 7, "kill_criteria": ""},
    "risks": ["compliance / fatigue / measurement risks"],
    "confidence": "low|medium|high with one reason (history depth, stats coverage)",
}


def analyse(campaign_id: str, force: bool = False, tier: str = "bulk") -> Dict[str, Any]:
    init_analysis_tables()
    dossier = build_dossier(campaign_id)
    conn = get_db()
    prev = conn.execute("SELECT * FROM campaign_analyses WHERE campaign_id=? ORDER BY id DESC LIMIT 1", (campaign_id,)).fetchone()
    conn.close()
    if prev and not force and prev["dossier_hash"] == dossier["hash"]:
        return {"campaign_id": campaign_id, "cached": True, "created_at": prev["created_at"], "model": prev["model"], "tier": prev["tier"],
                "analysis": json.loads(prev["analysis_json"] or "{}"), "dossier": dossier}
    from .llm.provider import LLMClient, llm_settings, LLMError
    cfg = llm_settings()
    have_model = bool(cfg["api_key"]) or cfg["provider"] == "claude_cli"
    analysis: Dict[str, Any]; model_used = None; tier_used = tier
    if have_model:
        system = ("You are a senior CRM/lifecycle analyst for a crypto and stocks trading app operating in MoEngage. Be specific, cite the numbers and facets in the dossier, "
                  "compare against peers in the same facet groups, respect the market angle policy, never forecast prices or instruct buying/selling. Output STRICT JSON only, matching the schema keys exactly.")
        prompt = "SCHEMA:\n" + json.dumps(ANALYSIS_SCHEMA) + "\n\nDOSSIER:\n" + json.dumps(dossier, default=str)[:14000] + "\n\nReturn the JSON object."
        try:
            client = LLMClient()
            out = client.chat([{"role": "system", "content": system}, {"role": "user", "content": prompt}], tools=None, max_tokens=1800, temperature=0.2, tier=tier)
            text = (out.get("content") or "").strip()
            s, e = text.find("{"), text.rfind("}")
            parsed = json.loads(text[s:e + 1]) if s >= 0 else {}
            if not isinstance(parsed, dict) or not ({"segment_insight", "next_actions"} & set(parsed)):
                raise ValueError("model returned no structured analysis")
            analysis = parsed
            model_used = out.get("model")
        except (LLMError, ValueError, json.JSONDecodeError) as ex:
            analysis = _fallback(dossier, note=f"model unavailable: {redact(str(ex))[:120]}"); tier_used = "deterministic"
    else:
        analysis = _fallback(dossier, note="no model configured"); tier_used = "deterministic"
    analysis.setdefault("verdict", "")
    conn = get_db()
    conn.execute("INSERT INTO campaign_analyses (campaign_id, campaign_name, source, dossier_hash, model, tier, analysis_json, dossier_json) VALUES (?,?,?,?,?,?,?,?)",
                 (campaign_id, dossier["campaign"].get("name"), dossier["source"], dossier["hash"], model_used, tier_used, json.dumps(analysis, default=str), json.dumps(dossier, default=str)[:60000]))
    conn.commit(); conn.close()
    try:
        from . import growth
        ideas = [{"kind": "growth_hack", "title": f"{dossier['campaign'].get('name')}: {a.get('action')}"[:150], "why": a.get("why", ""), "how": a.get("how_in_moengage", ""),
                  "segment": dossier["campaign"].get("target_segment") or dossier["taxonomy"]["group_key"], "channel": dossier["campaign"].get("channel", ""), "kpi": (analysis.get("experiment") or {}).get("primary_kpi", ""),
                  "effort": a.get("effort", "medium"), "expected_impact": a.get("expected_effect", ""), "priority": 70 - int(a.get("priority", 1)) * 3,
                  "data": {"campaign_id": campaign_id, "from": "deep_dive"}} for a in (analysis.get("next_actions") or [])[:3] if a.get("action")]
        if ideas:
            growth.upsert_ideas(ideas, "agent" if model_used else "rules")
    except Exception:
        pass
    return {"campaign_id": campaign_id, "cached": False, "created_at": datetime.now(timezone.utc).isoformat(), "model": model_used, "tier": tier_used, "analysis": analysis, "dossier": dossier}


def _fallback(dossier: Dict[str, Any], note: str = "") -> Dict[str, Any]:
    d = dossier.get("diagnosis") or {}; t = dossier.get("taxonomy") or {}; m = dossier.get("metrics_today") or {}
    peers = dossier.get("peers_same_group") or []
    peer_ctr = [p["ctr"] for p in peers if p.get("ctr") is not None]
    cmp = ""
    if peer_ctr and m.get("ctr") is not None:
        import statistics
        med = statistics.median(peer_ctr)
        cmp = f"Click rate {m['ctr']}% vs peer median {med:.2f}% in '{t.get('group_key')}'."
    return {
        "verdict": (d.get("headline") or f"{t.get('name')}: no history yet; {cmp}").strip(),
        "segment_insight": f"Programme {t.get('programme')}, cohort {t.get('cohort')}; facets {json.dumps(t.get('facets', {}), default=str)[:300]}. {cmp}",
        "user_insight": "Deterministic mode: user-level read needs a model. Use the lifecycle stage implied by the cohort facet.",
        "content_review": f"Content type: {dossier.get('campaign', {}).get('content_type')}; goals: {dossier.get('campaign', {}).get('conversion_goals')}. Review copy against fact-plus-tool rule.",
        "process_review": f"Delivery type: {dossier.get('campaign', {}).get('delivery_type')}; status {dossier.get('campaign', {}).get('status')}; holdout unknown.",
        "what_worked": [], "what_did_not": [c.get("cause") for c in (d.get("likely_causes") or []) if c.get("cause") and c.get("cause") != "No dominant driver"],
        "next_actions": [{"action": o.get("action"), "why": "from diagnosis", "how_in_moengage": o.get("how_in_moengage"), "effort": o.get("effort"), "expected_effect": o.get("expected_effect"), "priority": i + 1} for i, o in enumerate((d.get("options") or [])[:3])],
        "experiment": {"hypothesis": "", "primary_kpi": "ctr", "holdout_pct": 20, "window_days": 7, "kill_criteria": "guardrail breach"},
        "risks": [], "confidence": f"low — {note}",
        "_deterministic": True,
    }


def list_analyses(limit: int = 50) -> List[Dict[str, Any]]:
    init_analysis_tables()
    conn = get_db()
    rows = conn.execute("""SELECT a.* FROM campaign_analyses a JOIN (SELECT campaign_id, MAX(id) mid FROM campaign_analyses GROUP BY campaign_id) l ON a.id=l.mid ORDER BY a.id DESC LIMIT ?""", (limit,)).fetchall()
    conn.close()
    out = []
    for r in rows:
        try:
            an = json.loads(r["analysis_json"] or "{}")
        except Exception:
            an = {}
        out.append({"campaign_id": r["campaign_id"], "campaign_name": r["campaign_name"], "created_at": r["created_at"], "model": r["model"], "tier": r["tier"],
                    "verdict": an.get("verdict"), "confidence": an.get("confidence"), "next_action": ((an.get("next_actions") or [{}])[0]).get("action")})
    return out


def latest(campaign_id: str) -> Optional[Dict[str, Any]]:
    init_analysis_tables()
    conn = get_db()
    r = conn.execute("SELECT * FROM campaign_analyses WHERE campaign_id=? ORDER BY id DESC LIMIT 1", (campaign_id,)).fetchone()
    conn.close()
    if not r:
        return None
    return {"campaign_id": campaign_id, "created_at": r["created_at"], "model": r["model"], "tier": r["tier"], "analysis": json.loads(r["analysis_json"] or "{}"), "dossier": json.loads(r["dossier_json"] or "{}")}


def analyse_priority(limit: int = 8, force: bool = False) -> Dict[str, Any]:
    """Daily batch: campaigns needing attention first, then the largest by volume."""
    from .moengage import MoEngageClient
    from .anomaly.diagnose import diagnose_all
    from .anomaly.store import normalise_campaign
    c = MoEngageClient()
    rows = [r for r in c.get_campaigns() if not r.get("stats_missing")]
    urgent = [d["campaign_id"] for d in diagnose_all(c.mode, 40) if d.get("severity") in ("critical", "watch")]
    by_vol = [str(r.get("id")) for r in sorted(rows, key=lambda r: -(normalise_campaign(r)["delivered_count"] or 0))]
    picked: List[str] = []
    for cid in urgent + by_vol:
        if cid not in picked:
            picked.append(cid)
        if len(picked) >= limit:
            break
    done, errors = [], []
    for cid in picked:
        try:
            res = analyse(cid, force=force)
            done.append({"campaign_id": cid, "cached": res["cached"], "tier": res["tier"], "model": res["model"]})
        except Exception as e:
            errors.append({"campaign_id": cid, "error": redact(str(e))[:160]})
    return {"analysed": done, "errors": errors}
