"""
QA layer — verifies the facts the engine shows and the agent says, and
improves thin information.

Three jobs:
1. `run()`  — fact checks over live data: freshness, cross-source agreement,
   sanity bounds, references that must exist (SOPs, segments, KPIs), copy
   claims in pending proposals, source health, and information completeness.
   Every check is {id, area, status pass|warn|fail, fact, evidence, fix}. Safe
   improvements are applied on the spot (enrich ideas with KPI/segment/tagline,
   retire stale ideas) and listed under `improvements`; nothing external is
   touched. Results persist in `qa_runs` so quality has a trend.
2. `check_reply()` — every figure in an agent reply is traced back to the tool
   outputs of that conversation; untraceable figures, venue names and banned
   words are flagged and a short QA line is appended to the reply.
3. `verify_claims()` — a tool for the agent (and people) to check a list of
   claims against the live context before asserting them.
"""
from __future__ import annotations
import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .database import get_db, get_setting, get_latest_daily_run
from .security import audit, redact

NUM_RE = re.compile(r"(?<![\w.])[-+]?\$?\d[\d,]*(?:\.\d+)?\s?(%|pp|×|x\b|[KMBkmb]\b|bn\b|mn\b)?", re.U)
BENCHMARKS = {  # directional ranges from clm-campaign-playbook; used to fill recommendations that lack one
    "sop_funding_crowding_nudge": "liquidation rate of nudged −10–20% vs holdout", "sop_asset_spotlight": "alert CTR 10–20%; watchlist adds +20–30%", "sop_slipping_checkin": "15–25% recovery vs holdout",
    "sop_verified_to_funded": "+3–6 pp first-deposit rate", "sop_hvt_retention": "weekly active weeks 4w flat or up", "sop_cross_sell_crypto_to_tokenised": "5–8% of cohort makes a first fill",
    "sop_sip_nurture": "continuation 80%+ at 90d", "sop_web3_trending_watch": "watchlist adds; complaint rate flat", "sop_market_dormant_return": "1–4% incremental over holdout",
    "sop_liquidation_recovery": "20–30% return within 30d", "sop_global_announcement_lenses": "support contacts avoided; disable rate ≤ baseline",
}


def init_qa_tables() -> None:
    conn = get_db()
    conn.execute("""CREATE TABLE IF NOT EXISTS qa_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, score INTEGER, counts_json TEXT, checks_json TEXT, improvements_json TEXT)""")
    conn.commit(); conn.close()


# ── helpers ───────────────────────────────────────────────────────────────────
def _age_min(ts: Optional[str]) -> Optional[float]:
    if not ts:
        return None
    try:
        if isinstance(ts, (int, float)) or re.fullmatch(r"\d{9,11}(\.\d+)?", str(ts)):
            d = datetime.fromtimestamp(float(ts), tz=timezone.utc)
        else:
            d = datetime.fromisoformat(str(ts).replace(" ", "T").replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return round((datetime.now(timezone.utc) - d).total_seconds() / 60, 1)
    except Exception:
        return None


def _num(v: Any) -> Optional[float]:
    try:
        return float(v)
    except Exception:
        return None


def _numbers_in(text: str) -> List[Tuple[str, float, str]]:
    """(raw, value, unit) for every figure in a text; years and small bare integers are skipped (they are labels, not claims)."""
    out = []
    for m in NUM_RE.finditer(text or ""):
        raw = m.group(0).strip(); unit = (m.group(1) or "").strip().lower()
        body = raw.replace("$", "").replace(",", "").rstrip("%×xKMBkmbpn ").strip()
        v = _num(body)
        if v is None:
            continue
        if not unit and "." not in body and "$" not in raw and (v < 100 or 1900 <= v <= 2100):
            continue
        out.append((raw, v, unit))
    return out


def _flatten_numbers(obj: Any, acc: List[float], cap: int = 60000) -> None:
    if len(acc) >= cap:
        return
    if isinstance(obj, bool):
        return
    if isinstance(obj, (int, float)):
        acc.append(float(obj))
    elif isinstance(obj, str):
        for _, v, _u in _numbers_in(obj)[:50]:
            acc.append(v)
    elif isinstance(obj, dict):
        for v in obj.values():
            _flatten_numbers(v, acc, cap)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _flatten_numbers(v, acc, cap)


def _matches(v: float, unit: str, pool: List[float], tol: float = 0.005) -> bool:
    """A figure counts as traced when a pool number is within `tol` relative (or 0.005 absolute for fractions). Unit suffixes are expanded (2.1B → 2.1e9; 12% also as 0.12 when ≤ 100)."""
    cands = {v}
    if unit in ("k",):
        cands.add(v * 1e3)
    if unit in ("m", "mn"):
        cands.add(v * 1e6)
    if unit in ("b", "bn"):
        cands.add(v * 1e9)
    if unit in ("%", "pp") and abs(v) <= 100:
        cands.add(v / 100.0)
    for c in cands:
        for p in pool:
            if p == c or (c != 0 and abs(p - c) / abs(c) <= tol) or (abs(c) < 1 and abs(p - c) <= 0.005):
                return True
    return False


# ── agent reply QA ────────────────────────────────────────────────────────────
def check_reply(text: str, tool_outputs: Iterable[str]) -> Dict[str, Any]:
    """Trace every figure in a reply to the tool outputs of the same conversation; flag venue names and banned words."""
    from .llm.tools import VENUE_WORDS, BANNED_COPY
    pool: List[float] = []
    for t in tool_outputs:
        for _, v, _u in _numbers_in(t):
            pool.append(v)
            if len(pool) > 80000:
                break
    figs = _numbers_in(text or "")
    unverified = [raw for raw, v, u in figs if not _matches(v, u, pool)]
    venues = sorted({m.group(0) for m in VENUE_WORDS.finditer(text or "")})
    banned = sorted({m.group(0) for m in BANNED_COPY.finditer(text or "")})
    return {"figures": len(figs), "verified": len(figs) - len(unverified), "unverified": unverified[:12], "venue_names": venues, "banned_phrases": banned,
            "ok": not unverified and not banned, "note": "figures are traced to this conversation's tool outputs; unverified means the number did not come from a tool"}


def qa_line(q: Dict[str, Any]) -> str:
    if not q or not q.get("figures") and not q.get("banned_phrases"):
        return ""
    parts = [f"{q['verified']}/{q['figures']} figures traced to tool data"]
    if q.get("unverified"):
        parts.append("unverified: " + ", ".join(q["unverified"][:6]))
    if q.get("banned_phrases"):
        parts.append("banned wording: " + ", ".join(q["banned_phrases"]))
    if q.get("venue_names"):
        parts.append("venue names mentioned (internal only): " + ", ".join(q["venue_names"]))
    return "QA · " + " · ".join(parts)


def verify_claims(claims: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Check claims like {claim:'BTC 24h change', value:0.37, unit:'%'} against the live market context, benchmarks and competitor snapshot."""
    from .market.context import _latest
    ctx = _latest(6 * 3600) or {}
    pool: List[float] = []
    _flatten_numbers(ctx, pool)
    try:
        from .market.benchmarks import benchmarks
        _flatten_numbers(benchmarks(), pool)
    except Exception:
        pass
    out = []
    for c in (claims or [])[:40]:
        v = _num(c.get("value")); u = str(c.get("unit") or "").lower()
        ok = v is not None and _matches(v, u, pool, tol=0.003)
        out.append({"claim": str(c.get("claim") or "")[:160], "value": c.get("value"), "unit": u, "verified": ok, "how": "matched a live figure within 0.3%" if ok else "no live figure within 0.3% — cite the tool that produced it or drop the number"})
    return {"claims": out, "verified": sum(1 for o in out if o["verified"]), "total": len(out), "context_age_min": _age_min(ctx.get("generated_at"))}


# ── fact checks over live data ────────────────────────────────────────────────
def _chk(checks: List[Dict[str, Any]], cid: str, area: str, ok: Optional[bool], fact: str, evidence: str = "", fix: str = "", warn: bool = False) -> None:
    status = "pass" if ok else ("warn" if warn or ok is None else "fail")
    checks.append({"id": cid, "area": area, "status": status, "fact": fact[:200], "evidence": redact(str(evidence))[:300], "fix": fix[:200] if status != "pass" else ""})


def run(persist: bool = True, apply_improvements: bool = True) -> Dict[str, Any]:
    from .market.context import _latest
    from . import sops as sops_mod, growth, approvals, segments, guardrails
    from .llm.tools import VENUE_WORDS, BANNED_COPY, KPI_BY_TRANSITION
    checks: List[Dict[str, Any]] = []; improvements: List[Dict[str, Any]] = []
    ctx = _latest(12 * 3600) or {}

    # A. freshness
    age = _age_min(ctx.get("generated_at"))
    _chk(checks, "ctx_fresh", "freshness", age is not None and age <= 45, "market context is fresh (≤ 45 min)", f"age {age} min" if age is not None else "no context", "run the daily cycle or wait for the 15-min refresh", warn=age is not None and age <= 180)
    ci = ctx.get("competitors") or {}
    cage = _age_min(ci.get("generated_at") or ci.get("fetched_at") or (ctx.get("generated_at") if ci else None))
    _chk(checks, "competitors_fresh", "freshness", cage is not None and cage <= 90, "competitor snapshot is fresh (≤ 90 min)", f"age {cage} min" if cage is not None else "no snapshot", "competitor sources refresh with the context; check netguard allowlist", warn=cage is not None)
    dr = None
    try:
        dr = get_latest_daily_run()
    except Exception:
        pass
    dage = _age_min((dr or {}).get("started_at") or (dr or {}).get("created_at")) if dr else None
    _chk(checks, "daily_run", "freshness", dage is not None and dage <= 36 * 60, "daily run happened in the last 36h", f"{dage / 60:.1f} h ago" if dage else "never", "run the daily cycle", warn=dage is not None)

    # B. cross-source agreement
    assets = (ctx.get("crypto") or {}).get("assets") or {}
    mk = {m.get("symbol"): m for m in (ctx.get("crypto_markets") or [])}
    for sym in ("BTC", "ETH", "SOL"):
        a, m = assets.get(sym) or {}, mk.get(sym) or {}
        if a.get("price") and m.get("price"):
            diff = abs(a["price"] - m["price"]) / m["price"] * 100
            _chk(checks, f"price_agree_{sym}", "cross-source", diff <= 1.5, f"{sym} price agrees across CoinGecko and the liquidity venue (≤ 1.5%)", f"{a['price']} vs {m['price']} ({diff:.2f}%)", "one source is stale; prefer the venue price in copy and refresh the context")
        if m.get("binance_chg_24h") is not None and m.get("hl_chg_24h") is not None:
            d2 = abs(m["binance_chg_24h"] - m["hl_chg_24h"])
            _chk(checks, f"chg_agree_{sym}", "cross-source", d2 <= 2.0, f"{sym} 24h change agrees across venues (≤ 2 pp)", f"{m['binance_chg_24h']} vs {m['hl_chg_24h']}", "a venue is lagging; cite the change as a range")
    g = ctx.get("crypto_global") or {}
    if g.get("btc_dominance") is not None:
        s = (g.get("btc_dominance") or 0) + (g.get("eth_dominance") or 0)
        _chk(checks, "dominance_bounds", "sanity", 40 <= s <= 95, "BTC + ETH dominance within 40–95%", f"{s:.1f}%", "global data corrupt; refresh")
    if g.get("total_mcap_usd"):
        _chk(checks, "mcap_bounds", "sanity", 5e11 <= g["total_mcap_usd"] <= 2e13, "total crypto market cap within $0.5T–$20T", f"${g['total_mcap_usd'] / 1e12:.2f}T", "global data corrupt; refresh")
    fg = (ctx.get("fear_greed") or {}).get("value")
    if fg is not None:
        _chk(checks, "fng_bounds", "sanity", 0 <= float(fg) <= 100, "Fear & Greed within 0–100", str(fg), "source returned garbage")

    # C. bounds on signals we surface
    surges = ci.get("surges") or ci.get("intel", {}).get("surges") or []
    absurd = [s for s in surges if (s.get("surge_pct") or 0) > 500 or s.get("product") == "option"]
    _chk(checks, "surge_sanity", "sanity", not absurd, "no absurd surges (> 500% or option contracts) surface", f"{len(absurd)} absurd of {len(surges)}", "exclude option rows and require a sane prior volume in competitors.intel")
    funding_bad = [m["symbol"] for m in mk.values() if abs(m.get("funding_apr_pct") or 0) > 300]
    _chk(checks, "funding_bounds", "sanity", not funding_bad, "funding APR within ±300% for all listed perps", ", ".join(funding_bad[:6]) or "all within", "cap or drop the outlier before it reaches a hook")
    try:
        from .market import moneyflow
        f = moneyflow.flow(ctx) if ctx else {}
        ra = (f.get("risk_appetite") or {}).get("score")
        _chk(checks, "risk_score_bounds", "sanity", ra is None or 0 <= ra <= 100, "risk-appetite score within 0–100", str(ra), "clamp in moneyflow.flow")
        sh = (f.get("venue_share") or {}).get("now") or {}
        tot = sum(sh.values()) if sh else 100
        _chk(checks, "share_sums", "sanity", abs(tot - 100) <= 0.5 if sh else None, "venue asset-class shares sum to 100%", f"{tot:.1f}%", "rounding or a missing class", warn=not sh)
        att_bad = [a["symbol"] for a in (f.get("attention") or []) if (a.get("vs_7d_avg_x") or 0) > 20]
        _chk(checks, "attention_bounds", "sanity", not att_bad, "attention multiples ≤ 20× (else a history gap, not attention)", ", ".join(att_bad) or "ok", "require ≥ 3 history days before showing the multiple")
        st = f.get("stablecoins") or {}
        if st.get("total_now_usd"):
            _chk(checks, "stables_bounds", "sanity", 1e11 <= st["total_now_usd"] <= 1.5e12, "stablecoin supply within $100B–$1.5T", f"${st['total_now_usd'] / 1e9:.0f}B", "DefiLlama payload changed shape")
    except Exception as e:
        _chk(checks, "moneyflow_runs", "sanity", False, "money-flow module runs without error", str(e)[:120], "self_diagnose → propose_code_change")

    # D. references that must exist
    sop_ids = {s["id"] for s in sops_mod.list_sops(include_inactive=True)}
    try:
        from . import structural
        missing = sorted({p["sop"] for p in structural.PLAYS if p.get("sop") and p["sop"] not in sop_ids})
        _chk(checks, "structural_sops_exist", "references", not missing, "every structural play points at an existing SOP", ", ".join(missing) or "all exist", "define the SOP or fix the id in structural.PLAYS")
    except Exception as e:
        _chk(checks, "structural_sops_exist", "references", False, "structural audit importable", str(e)[:100], "fix import")
    try:
        from .market import moneyflow
        src = open(moneyflow.__file__, encoding="utf-8").read()
        refs = set(re.findall(r'"(sop_[a-z0-9_]+)"', src))
        miss = sorted(refs - sop_ids)
        _chk(checks, "moneyflow_sops_exist", "references", not miss, "every money-flow recommendation SOP exists", ", ".join(miss) or "all exist", "fix the SOP id in moneyflow.recommendations")
    except Exception:
        pass
    pm = sops_mod.product_cohort_matrix()
    miss = sorted({s for c in pm.get("cohorts", []) for s in (c.get("sops") or []) if s not in sop_ids} | {s for r in sops_mod.channel_matrix().get("states", []) for s in (r.get("sops") or []) if s not in sop_ids})
    _chk(checks, "matrix_sops_exist", "references", not miss, "product and channel matrices reference existing SOPs", ", ".join(miss) or "all exist", "fix sop ids in sops.STATE_RULES / product_cohort_matrix")
    hooks = ((ctx.get("hooks") or {}).get("hooks") or [])
    hmiss = sorted({h.get("sop") for h in hooks if h.get("sop") and h["sop"] not in sop_ids})
    _chk(checks, "hook_sops_exist", "references", not hmiss, "every compiled hook maps to an existing SOP", ", ".join(hmiss) or "all exist", "fix the map in market/hooks.py or feed.ALERT_SOP")
    reg = {r["name"] for r in segments.registry(limit=500)} | {r["family"] for r in segments.registry(limit=500)}
    pend = approvals.list_proposals(status="pending", limit=300)
    pend_segs = {(p.get("payload") or {}).get("name") for p in pend if p["kind"] == "create_segment"}
    orphan = [(p["id"], (p.get("payload") or {}).get("target_segment")) for p in pend if p["kind"] == "create_campaign" and (p.get("payload") or {}).get("target_segment") and (p["payload"]["target_segment"] not in reg) and (p["payload"]["target_segment"] not in pend_segs)]
    _chk(checks, "proposal_segments_exist", "references", not orphan, "pending campaign drafts target a segment that exists or is itself proposed", "; ".join(f"#{i} → {s}" for i, s in orphan[:5]) or "all resolvable", "propose_segment for the missing cohort before approving the campaign", warn=True)
    known_kpis = {k for v in KPI_BY_TRANSITION.values() for k in v} | {s.get("primary_kpi") for s in sops_mod.list_sops(include_inactive=True)}
    ideas = growth.list_ideas(status="new", limit=300)
    unknown_kpi = [i["id"] for i in ideas if i.get("kpi") and i["kpi"] not in known_kpis and " " not in i["kpi"]]
    _chk(checks, "idea_kpis_known", "references", len(unknown_kpi) <= max(3, len(ideas) // 5), "idea KPIs come from the KPI vocabulary", f"{len(unknown_kpi)} of {len(ideas)} use ad-hoc KPI names", "map to KPI_BY_TRANSITION names or add them to the vocabulary", warn=True)

    # E. copy and claims in pending drafts
    venue_hits, banned_hits, long_push, unverified = [], [], [], []
    for p in pend:
        if p["kind"] != "create_campaign":
            continue
        pl = p.get("payload") or {}; goal = pl.get("goal") or {}
        allowed_pool: List[float] = []
        _flatten_numbers({"goal": goal, "rationale": p.get("rationale"), "schedule": pl.get("schedule")}, allowed_pool)
        for v in pl.get("variants") or []:
            txt = f"{v.get('title', '')} {v.get('body', '')}"
            if VENUE_WORDS.search(txt):
                venue_hits.append(p["id"])
            if BANNED_COPY.search(txt):
                banned_hits.append(p["id"])
            if str(pl.get("channel", "")).lower() == "push" and (len(str(v.get("title", ""))) > 60 or len(str(v.get("body", ""))) > 140):
                long_push.append(p["id"])
            for raw, val, unit in _numbers_in(txt):
                if unit in ("%", "x", "×") and not _matches(val, unit, allowed_pool) and val not in (1, 100):
                    unverified.append(f"#{p['id']} '{raw}'")
    _chk(checks, "copy_no_venues", "copy", not venue_hits, "no pending draft names a venue or competitor", f"proposals {sorted(set(venue_hits))}" if venue_hits else "clean", "rewrite the variant; campaign_brief_check should have blocked it")
    _chk(checks, "copy_no_banned", "copy", not banned_hits, "no pending draft uses banned wording", f"proposals {sorted(set(banned_hits))}" if banned_hits else "clean", "rewrite per crypto-compliance-copy")
    _chk(checks, "copy_push_length", "copy", not long_push, "push drafts respect title ≤ 60 / body ≤ 140", f"proposals {sorted(set(long_push))}" if long_push else "ok", "shorten before approval", warn=True)
    _chk(checks, "copy_figures_traceable", "copy", not unverified, "percentages and multiples in drafts trace to the goal brief or rationale", "; ".join(unverified[:6]) or "all traceable", "remove the figure or put its source in the rationale", warn=True)

    # F. source health
    errs = ctx.get("errors") or []
    _chk(checks, "ctx_errors", "sources", len(errs) == 0, "context assembled without source errors", "; ".join(str(e)[:60] for e in errs[:4]) or "none", "check netguard allowlist and source status", warn=len(errs) <= 3)
    inactive = [s["name"] for s in ((ctx.get("sources") or {}).get("sources") or []) if not s.get("active")]
    _chk(checks, "sources_active", "sources", not inactive, "every configured market source is active", ", ".join(inactive) or "all active", "keyed sources need a free key in Settings (e.g. market_finnhub_key)", warn=True)

    # G. information completeness + improvements
    thin = [i for i in ideas if not i.get("kpi") or not i.get("segment")]
    _chk(checks, "ideas_complete", "completeness", not thin, "every idea carries a KPI and a segment", f"{len(thin)} thin of {len(ideas)}", "enriched automatically where the transition or SOP implies them", warn=True)
    if apply_improvements and thin:
        n = _enrich_ideas(thin, KPI_BY_TRANSITION, sops_mod)
        if n:
            improvements.append({"what": f"enriched {n} ideas with KPI / segment / tagline from their transition or SOP", "where": "growth_ideas"})
    try:
        st = segments.study([])
        undefined = st.get("unknown_tokens") or []
        _chk(checks, "nomenclature_defined", "completeness", not undefined, "every token in segment names is defined in the nomenclature", ", ".join(f"{t}×{n}" for t, n in undefined[:6]) or "all defined", "define the codes (Cohorts → define them) or ask the team via request_data", warn=True)
        if apply_improvements and undefined:
            try:
                from . import datarequests
                title = "Define segment-name codes: " + ", ".join(t for t, _ in undefined[:8])
                if title not in {r.get("title") for r in datarequests.list_requests(limit=200)}:
                    datarequests.request("nomenclature", title, "These tokens appear in uploaded segment names but have no meaning in the nomenclature, so the cohorts cannot be staged, capped or matched to SOPs.", spec="one line per code: CODE = meaning, facet (value / product / stage / propensity)", unblocks=["segment_study", "peace_index"], priority=60, requested_by="qa")
                    improvements.append({"what": f"asked the team to define {len(undefined)} undefined segment code(s)", "where": "data requests"})
            except Exception:
                pass
    except Exception:
        pass
    fam_no_stage = 0
    try:
        pi = guardrails.peace_index([])
        fam_no_stage = sum(1 for r in pi.get("families", []) if not r.get("stage"))
    except Exception:
        pass
    _chk(checks, "families_staged", "completeness", fam_no_stage == 0, "every cohort family maps to a lifecycle stage", f"{fam_no_stage} unstaged", "extend guardrails._stage_for_family or the nomenclature", warn=True)
    if apply_improvements:
        try:
            ex = growth.expire_stale()
            if ex.get("expired"):
                improvements.append({"what": f"retired {ex['expired']} stale ideas", "where": "growth_ideas"})
        except Exception:
            pass
    stale_props = [p["id"] for p in pend if (_age_min(p.get("created_at")) or 0) > 14 * 24 * 60]
    _chk(checks, "proposals_not_stale", "freshness", not stale_props, "no pending proposal older than 14 days", f"{stale_props[:6]}" if stale_props else "none", "approve, revise or reject them; market-linked ones are already meaningless", warn=True)

    counts = {k: sum(1 for c in checks if c["status"] == k) for k in ("pass", "warn", "fail")}
    score = int(round(100 * (counts["pass"] + 0.5 * counts["warn"]) / max(1, len(checks))))
    order = {"fail": 0, "warn": 1, "pass": 2}
    checks.sort(key=lambda c: order[c["status"]])
    out = {"generated_at": datetime.now(timezone.utc).isoformat(), "score": score, "counts": counts, "checks": checks, "improvements": improvements,
           "verdict": ("facts verified" if counts["fail"] == 0 else f"{counts['fail']} fact check(s) failing") + (f"; {counts['warn']} to improve" if counts["warn"] else ""), "areas": sorted({c["area"] for c in checks})}
    if persist:
        try:
            init_qa_tables(); conn = get_db()
            conn.execute("INSERT INTO qa_runs (score, counts_json, checks_json, improvements_json) VALUES (?,?,?,?)", (score, json.dumps(counts), json.dumps(checks)[:200000], json.dumps(improvements)))
            conn.execute("DELETE FROM qa_runs WHERE id NOT IN (SELECT id FROM qa_runs ORDER BY id DESC LIMIT 400)")
            conn.commit(); conn.close()
            audit("qa.run", {"score": score, **counts, "improvements": len(improvements)}, actor="qa")
        except Exception:
            pass
    return out


def _enrich_ideas(thin: List[Dict[str, Any]], kpi_by_transition: Dict[str, List[str]], sops_mod) -> int:
    from . import ice as ice_mod
    conn = get_db(); n = 0
    sop_by_id = {s["id"]: s for s in sops_mod.list_sops(include_inactive=True)}
    for i in thin:
        data = i.get("data") or {}
        sop = sop_by_id.get(data.get("sop") or "") or {}
        kpi = i.get("kpi") or sop.get("primary_kpi") or (kpi_by_transition.get(i.get("transition") or "") or [None])[0]
        seg = i.get("segment") or ((sop.get("audience") or {}).get("segment_family")) or ""
        if not kpi and not seg:
            continue
        data.setdefault("tagline", ice_mod.tagline(i.get("title") or "", kpi or "", seg or ""))
        data.setdefault("qa_enriched", True)
        conn.execute("UPDATE growth_ideas SET kpi=COALESCE(NULLIF(kpi,''), ?), segment=COALESCE(NULLIF(segment,''), ?), data_json=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (kpi or "", seg or "", json.dumps(data, default=str)[:4000], i["id"]))
        n += 1
    conn.commit(); conn.close()
    return n


def enrich_recommendations(recs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Fill benchmark and KPI gaps on recommendations from the playbook ranges (in memory)."""
    for r in recs:
        if not r.get("benchmark") and r.get("sop") in BENCHMARKS:
            r["benchmark"] = BENCHMARKS[r["sop"]]
        if not r.get("kpi") and r.get("sop"):
            r["kpi"] = "see SOP primary KPI"
    return recs


def latest(max_age_min: int = 30) -> Optional[Dict[str, Any]]:
    try:
        init_qa_tables(); conn = get_db()
        r = conn.execute("SELECT * FROM qa_runs ORDER BY id DESC LIMIT 1").fetchone(); conn.close()
        if not r:
            return None
        age = _age_min(r["created_at"])
        if age is not None and age > max_age_min:
            return None
        return {"generated_at": r["created_at"], "score": r["score"], "counts": json.loads(r["counts_json"] or "{}"), "checks": json.loads(r["checks_json"] or "[]"), "improvements": json.loads(r["improvements_json"] or "[]"), "cached": True}
    except Exception:
        return None


def history(limit: int = 30) -> List[Dict[str, Any]]:
    try:
        init_qa_tables(); conn = get_db()
        rows = conn.execute("SELECT id, created_at, score, counts_json FROM qa_runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall(); conn.close()
        return [{"id": r["id"], "at": r["created_at"], "score": r["score"], **json.loads(r["counts_json"] or "{}")} for r in rows]
    except Exception:
        return []


def report(force: bool = False) -> Dict[str, Any]:
    cur = None if force else latest()
    if cur is None:
        cur = run()
    cur["history"] = history(14)
    return cur
