"""
Segment registry and cohort studies.

The team uploads cohorts to MoEngage every month as custom / file segments
whose names encode meaning (HVT_Sep26 = high-value traders, September 2026
upload). MoEngage's segment API exposes names, ids and created time — not
sizes — so this module:
  * decodes each segment name with the shared taxonomy codes (editable),
  * groups versions of the same cohort into a *family* (HVT → Aug26, Sep26 …),
  * remembers every segment seen (segment_registry) so new monthly uploads are
    detected, and
  * studies families: campaigns that targeted them, their volume-weighted
    performance, version-over-version deltas, gaps, and suggested studies.
Reach uses the best available proxy: an explicit reach field, else the
largest 'sent' of a one-time campaign that targeted the segment.
"""
from __future__ import annotations
import json
import re
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional

from .database import get_db
from .taxonomy import classify, tokens, MONTH, DATE_TOKEN, codes

MONTHS = {m: i for i, m in enumerate(["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)}


def init_segment_tables() -> None:
    conn = get_db()
    conn.execute("""CREATE TABLE IF NOT EXISTS segment_registry (
        segment_id TEXT PRIMARY KEY, name TEXT, family TEXT, version TEXT, source TEXT, seg_type TEXT, created_time TEXT,
        first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP, last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP, reach INTEGER, meta_json TEXT)""")
    conn.commit(); conn.close()


def _version(tok: str) -> Optional[str]:
    u = tok.upper().replace("'", "")
    m = MONTH.match(u)
    if m:
        mon = m.group(1)[:3]; yr = m.group(2)
        yr = ("20" + yr) if len(yr) == 2 else yr
        return f"{yr}-{MONTHS[mon]:02d}"
    m = DATE_TOKEN.match(u)
    if m and m.group(4):
        yr = m.group(4); yr = ("20" + yr) if len(yr) == 2 else yr
        return f"{yr}-{MONTHS[m.group(3)[:3]]:02d}"
    if re.fullmatch(r"(20\d{2})[-_]?(0[1-9]|1[0-2])", u):
        return u[:4] + "-" + u[-2:]
    return None


def decode(name: str) -> Dict[str, Any]:
    """Nomenclature → facets + family key (name without period tokens) + version (YYYY-MM)."""
    cls = classify({"name": name})
    fam_tokens, version = [], None
    for t in tokens(name):
        v = _version(t)
        if v:
            version = version or v; continue
        u = t.upper().replace("'", "")
        if u.isdigit() and len(u) <= 2:
            continue
        fam_tokens.append(u)
    family = "_".join(fam_tokens) or name.upper()
    meaning = []
    for k, vals in cls["facets"].items():
        if k in ("period", "segment", "tag", "channel", "delivery"):
            continue
        meaning.extend(vals)
    return {"name": name, "family": family, "version": version, "facets": cls["facets"], "meaning": meaning, "unknown_tokens": cls["unknown_tokens"], "group_key": cls["group_key"]}


def sync(segments: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Upsert the segment list; return newly seen segments (empty on the first, seeding run)."""
    init_segment_tables()
    conn = get_db()
    known = {r["segment_id"] for r in conn.execute("SELECT segment_id FROM segment_registry").fetchall()}
    seeding = not known
    new = []
    for s in segments or []:
        sid = str(s.get("id") or s.get("_id") or s.get("name"))
        if not sid:
            continue
        d = decode(s.get("name") or "")
        reach = s.get("estimated_reach") or s.get("reach") or s.get("user_count") or s.get("count")
        meta = {k: s.get(k) for k in ("description", "criteria", "type", "source", "created_time", "created_at", "updated_time") if s.get(k) is not None}
        if sid in known:
            conn.execute("UPDATE segment_registry SET name=?, family=?, version=?, last_seen=CURRENT_TIMESTAMP, reach=COALESCE(?, reach), meta_json=? WHERE segment_id=?",
                         (s.get("name"), d["family"], d["version"], int(reach) if reach else None, json.dumps(meta, default=str), sid))
        else:
            conn.execute("INSERT INTO segment_registry (segment_id, name, family, version, source, seg_type, created_time, reach, meta_json) VALUES (?,?,?,?,?,?,?,?,?)",
                         (sid, s.get("name"), d["family"], d["version"], s.get("source") or s.get("_source") or "", s.get("type") or "", str(s.get("created_time") or s.get("created_at") or ""), int(reach) if reach else None, json.dumps(meta, default=str)))
            if not seeding:
                new.append({"id": sid, "name": s.get("name"), "family": d["family"], "version": d["version"]})
    conn.commit(); conn.close()
    return {"new": new, "seeded": seeding, "total": len(segments or [])}


def registry(limit: int = 500) -> List[Dict[str, Any]]:
    init_segment_tables()
    conn = get_db()
    rows = [dict(r) for r in conn.execute("SELECT * FROM segment_registry ORDER BY first_seen DESC LIMIT ?", (limit,)).fetchall()]
    conn.close()
    for r in rows:
        try:
            r["meta"] = json.loads(r.pop("meta_json") or "{}")
        except Exception:
            r["meta"] = {}
    return rows


def resolve(name_or_family: str) -> Optional[Dict[str, Any]]:
    """Exact segment name, else the latest version of a family (HVT → HVT_Sep26)."""
    rows = registry()
    for r in rows:
        if (r["name"] or "").lower() == name_or_family.lower():
            return r
    fam = decode(name_or_family)["family"]
    cands = [r for r in rows if r["family"] == fam]
    if not cands:
        return None
    cands.sort(key=lambda r: (r.get("version") or "", r.get("first_seen") or ""), reverse=True)
    return cands[0]


def _campaign_matches(c: Dict[str, Any], seg_name: str, family: str) -> bool:
    ts = str(c.get("target_segment") or "")
    if ts and ts.lower() == seg_name.lower():
        return True
    if ts and decode(ts)["family"] == family:
        return True
    ctoks = {t.upper() for t in tokens(c.get("name") or "")}
    ftoks = [t for t in family.split("_") if t in codes()]
    return bool(ftoks) and all(t in ctoks for t in ftoks)


def study(campaigns: List[Dict[str, Any]], days: int = 30) -> Dict[str, Any]:
    """Cohort families: versions, reach proxy, attached campaigns and their performance, version deltas, gaps, studies to run."""
    from .anomaly.store import normalise_campaign
    rows = registry()
    fams: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        dd = decode(r["name"] or "")
        f = fams.setdefault(r["family"], {"family": r["family"], "meaning": dd["meaning"], "facets": {k: v for k, v in dd["facets"].items() if k != "period"}, "versions": [], "unknown_tokens": dd["unknown_tokens"]})
        f["versions"].append({"segment_id": r["segment_id"], "name": r["name"], "version": r.get("version"), "first_seen": r.get("first_seen"), "reach": r.get("reach")})
    out = []
    now = datetime.utcnow()
    for f in fams.values():
        f["versions"].sort(key=lambda v: (v.get("version") or "", v.get("first_seen") or ""))
        latest = f["versions"][-1]
        perf_by_version: Dict[str, Dict[str, float]] = {}
        attached = []
        for v in f["versions"]:
            agg = {"campaigns": 0, "with_stats": 0, "sent": 0.0, "delivered": 0.0, "clicks": 0.0, "conv": 0.0, "max_sent": 0.0}
            for c in campaigns:
                if not _campaign_matches(c, v["name"] or "", f["family"]):
                    continue
                agg["campaigns"] += 1
                n = normalise_campaign(c)
                if v is latest:
                    attached.append({"id": c.get("id"), "name": c.get("name"), "channel": c.get("channel"), "status": c.get("status"), "sent": n.get("sent_count"), "ctr": n.get("ctr"), "delivery_rate": n.get("delivery_rate")})
                if n["stats_missing"]:
                    continue
                d = n["delivered_count"] or 0.0
                agg["with_stats"] += 1; agg["sent"] += n["sent_count"] or 0.0; agg["delivered"] += d; agg["max_sent"] = max(agg["max_sent"], n["sent_count"] or 0.0)
                agg["clicks"] += n["opened_count"] if n["opened_count"] is not None else d * (n["ctr"] or 0) / 100.0
                agg["conv"] += n["conversions"] if n["conversions"] is not None else d * (n["conversion_rate"] or 0) / 100.0
            perf_by_version[v.get("version") or v["name"]] = {"campaigns": agg["campaigns"], "with_stats": agg["with_stats"], "delivered": round(agg["delivered"]),
                                                              "click_rate": round(agg["clicks"] / agg["delivered"] * 100, 2) if agg["delivered"] else None,
                                                              "conversion_rate": round(agg["conv"] / agg["delivered"] * 100, 2) if agg["delivered"] and agg["conv"] else None,
                                                              "delivery_rate": round(agg["delivered"] / agg["sent"] * 100, 1) if agg["sent"] else None, "reach_proxy": int(agg["max_sent"]) or None}
            if not v.get("reach") and agg["max_sent"]:
                v["reach_proxy"] = int(agg["max_sent"])
        keys = list(perf_by_version.keys())
        cur = perf_by_version[keys[-1]] if keys else {}
        prev = perf_by_version[keys[-2]] if len(keys) > 1 else {}
        delta = None
        if cur.get("click_rate") is not None and prev.get("click_rate") is not None:
            delta = {"click_rate_pp": round(cur["click_rate"] - prev["click_rate"], 2), "vs_version": keys[-2], "reach_change_pct": (round((cur["reach_proxy"] / prev["reach_proxy"] - 1) * 100, 1) if cur.get("reach_proxy") and prev.get("reach_proxy") else None)}
        flags = []
        first_seen = latest.get("first_seen") or ""
        try:
            age_days = (now - datetime.fromisoformat(str(first_seen).replace(" ", "T"))).days
        except Exception:
            age_days = None
        if age_days is not None and age_days <= 7:
            flags.append("new_this_week")
        if not attached:
            flags.append("no_campaign_attached")
        if delta and delta["click_rate_pp"] <= -1.0:
            flags.append("worse_than_previous_version")
        if delta and delta.get("reach_change_pct") is not None and delta["reach_change_pct"] <= -20:
            flags.append("reach_shrank")
        if f["unknown_tokens"]:
            flags.append("undefined_codes")
        out.append({**f, "latest": latest, "reach": latest.get("reach") or latest.get("reach_proxy"), "campaigns_attached": attached[:12], "performance": cur, "previous": prev, "delta": delta, "flags": flags, "age_days": age_days})
    out.sort(key=lambda f: (-(f["reach"] or 0), f["family"]))
    unknown: Dict[str, int] = defaultdict(int)
    for f in out:
        for t in f["unknown_tokens"]:
            unknown[t] += 1
    return {"families": out, "segments": len(rows), "unknown_tokens": sorted(unknown.items(), key=lambda kv: -kv[1])[:20], "studies": suggest_studies(out)}


def suggest_studies(families: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    s: List[Dict[str, Any]] = []
    by_value = [f for f in families if any("value" in k for k in f["facets"].keys()) or any(t in f["family"] for t in ("HVT", "MVT", "LVT", "HVS", "LVS"))]
    if len(by_value) >= 2:
        s.append({"id": "value_tier_response", "title": "Value-tier response gap", "question": "Do HVT/MVT/LVT cohorts respond differently to the same programme, and is the gap widening month over month?",
                  "method": "campaign_taxonomy by=value + segment_study performance per family; two-proportion test on click and conversion; read against regime.",
                  "action_if_true": "Split creative and cadence by tier; cap HVT to fewer, richer messages; LVT gets habit tools not promos."})
    new = [f["family"] for f in families if "new_this_week" in f["flags"]]
    if new:
        s.append({"id": "new_cohort_baseline", "title": f"Baseline the new uploads ({', '.join(new[:5])})", "question": "How large are they versus last month's versions and which campaigns should inherit them?",
                  "method": "segment_study delta.reach_change_pct; map last version's campaigns; check exclusions overlap (liquidated, KYC pending).", "action_if_true": "Re-point standing campaigns to the new version via an SOP run; retire the old version."})
    gaps = [f["family"] for f in families if "no_campaign_attached" in f["flags"]]
    if gaps:
        s.append({"id": "orphan_cohorts", "title": f"Cohorts nobody messages ({len(gaps)})", "question": "Why were these uploaded, and which lifecycle transition do they serve?",
                  "method": "decode meaning; clm_program_audit for the transition; check if another family already covers them.", "action_if_true": "Run the matching SOP or archive the segment."})
    worse = [f["family"] for f in families if "worse_than_previous_version" in f["flags"]]
    if worse:
        s.append({"id": "version_decay", "title": f"Versions performing worse than last month ({', '.join(worse[:4])})", "question": "Cohort composition change, creative fatigue, or market regime?",
                  "method": "compare facets and reach; campaign_diagnosis on attached campaigns; regime tag of both months.", "action_if_true": "Refresh creative with a 2-variant test before changing audience."})
    s.append({"id": "migration_matrix", "title": "Cohort migration month over month", "question": "What share of last month's HVT are still HVT, dropped to MVT/LVT, or went dormant?",
              "method": "Needs user ids per version (file segments) — ask for the monthly upload files or emit trader_state as a user attribute; then a retention/behavior query split by trader_state.",
              "action_if_true": "Downgrade risk becomes the primary retention KPI for HVT programmes."})
    return s[:6]
