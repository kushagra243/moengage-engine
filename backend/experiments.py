"""
Experiment ledger: every executed campaign proposal becomes an experiment with
its goal brief (KPI, target, holdout, window, kill criteria). Each refresh
reads the campaign's stats, compares them with the pre-period baseline (and
with the control group when the stats API exposes one), and writes a readout
in plain language with a Wilson interval, so the loop closes: propose →
approve → run → read → learn. Lessons are pushed back into the growth feed.
"""
from __future__ import annotations
import json
import math
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from .database import get_db
from .security import redact


def init_experiment_tables() -> None:
    conn = get_db()
    conn.execute("""CREATE TABLE IF NOT EXISTS experiments (
        id INTEGER PRIMARY KEY AUTOINCREMENT, proposal_id INTEGER, campaign_name TEXT, campaign_id TEXT, source TEXT,
        started_on TEXT, window_days INTEGER, primary_kpi TEXT, target TEXT, guardrail_metric TEXT, control_group_pct REAL, kill_criteria TEXT,
        baseline_json TEXT, status TEXT DEFAULT 'running', readout_json TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
    conn.commit(); conn.close()


def wilson(p: float, n: float, z: float = 1.96):
    if n <= 0:
        return (None, None)
    pp = max(0.0, min(1.0, p))
    denom = 1 + z * z / n
    centre = (pp + z * z / (2 * n)) / denom
    half = z * math.sqrt(pp * (1 - pp) / n + z * z / (4 * n * n)) / denom
    return (round((centre - half) * 100, 2), round((centre + half) * 100, 2))


KPI_TO_METRIC = {"ctr": "ctr", "click_rate": "ctr", "open_rate": "ctr", "conversion_rate": "conversion_rate", "first_trade_rate_7d": "conversion_rate", "reactivation_rate_14d": "conversion_rate",
                 "delivery_rate": "delivery_rate", "revenue": "revenue_generated", "incremental_gmv_vs_holdout": "revenue_generated"}


def register_from_proposal(proposal: Dict[str, Any], result: Dict[str, Any], source: str) -> Optional[int]:
    """Called after a create_campaign proposal executes."""
    if proposal.get("kind") != "create_campaign":
        return None
    init_experiment_tables()
    payload = proposal.get("payload") or {}; goal = payload.get("goal") or {}
    campaign_id = str(result.get("campaign_id") or "")
    # pre-period baseline from any campaign with the same name (re-runs) or empty
    baseline = {}
    try:
        from .anomaly.store import list_tracked_campaigns, get_history
        for t in list_tracked_campaigns(source):
            if t["campaign_name"] == payload.get("name"):
                hist = get_history(t["campaign_id"], source, 28)
                vals = [h.get(KPI_TO_METRIC.get(goal.get("primary_kpi", ""), "ctr")) for h in hist if h.get(KPI_TO_METRIC.get(goal.get("primary_kpi", ""), "ctr")) is not None]
                if vals:
                    baseline = {"metric": KPI_TO_METRIC.get(goal.get("primary_kpi", ""), "ctr"), "mean": sum(vals) / len(vals), "n_days": len(vals)}
    except Exception:
        pass
    conn = get_db()
    cur = conn.execute("""INSERT INTO experiments (proposal_id, campaign_name, campaign_id, source, started_on, window_days, primary_kpi, target, guardrail_metric, control_group_pct, kill_criteria, baseline_json)
                          VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                       (proposal["id"], payload.get("name"), campaign_id, source, date.today().isoformat(), int(goal.get("measurement_window_days") or 7), goal.get("primary_kpi"), str(goal.get("target") or ""),
                        goal.get("guardrail_metric"), float(goal.get("control_group_pct") or 0), goal.get("kill_criteria"), json.dumps(baseline)))
    eid = cur.lastrowid
    conn.commit(); conn.close()
    return eid


def readout(exp: Dict[str, Any]) -> Dict[str, Any]:
    """Compute the current readout from snapshots; honest about what can and cannot be claimed."""
    from .anomaly.store import get_history, list_tracked_campaigns
    from .metrics import fmt_value, delta
    metric = KPI_TO_METRIC.get(exp.get("primary_kpi") or "", "ctr")
    cid = exp.get("campaign_id") or ""
    if not cid or cid.startswith("mock_") or cid.startswith("draft"):
        # match by name if the draft got a real id later
        for t in list_tracked_campaigns(exp.get("source") or "live"):
            if t["campaign_name"] == exp.get("campaign_name"):
                cid = t["campaign_id"]; break
    hist = [h for h in get_history(cid, exp.get("source") or "live", 60) if h["snapshot_date"] >= (exp.get("started_on") or "")] if cid else []
    started = datetime.fromisoformat(exp["started_on"]).date() if exp.get("started_on") else date.today()
    days_run = (date.today() - started).days
    window = int(exp.get("window_days") or 7)
    base = json.loads(exp.get("baseline_json") or "{}")
    if not hist:
        return {"state": "waiting_for_data", "days_run": days_run, "window_days": window, "message": "No snapshots for this campaign yet (draft not published, or first stats not in the 30-day window)."}
    latest = hist[-1]
    val = latest.get(metric); denom = latest.get("delivered_count") or latest.get("sent_count") or 0
    ci = wilson((val or 0) / 100.0, denom) if metric in ("ctr", "conversion_rate", "delivery_rate") and val is not None else (None, None)
    d = delta(metric, val, base.get("mean")) if base.get("mean") is not None else {"text": "no pre-period baseline"}
    claim = ("Control-group results are not exposed by the campaign-stats API in this workspace; this readout compares against the pre-period only and is NOT incremental lift."
             if exp.get("control_group_pct") else "No holdout was set; this cannot be read as incremental.")
    state = "running" if days_run < window else "window_complete"
    verdict = ""
    if val is not None and base.get("mean") is not None and ci[0] is not None:
        if ci[0] > base["mean"]:
            verdict = f"{metric} {fmt_value(metric, val)} is above the pre-period mean {fmt_value(metric, base['mean'])} even at the low end of its 95% interval ({ci[0]}–{ci[1]}%)."
        elif ci[1] < base["mean"]:
            verdict = f"{metric} {fmt_value(metric, val)} is below the pre-period mean {fmt_value(metric, base['mean'])} across its whole 95% interval ({ci[0]}–{ci[1]}%) — check the kill criteria."
        else:
            verdict = f"{metric} {fmt_value(metric, val)} vs pre-period {fmt_value(metric, base['mean'])}: the 95% interval ({ci[0]}–{ci[1]}%) still overlaps; too early to call."
    elif val is not None:
        verdict = f"{metric} today {fmt_value(metric, val)} on {int(denom):,} delivered; no pre-period baseline to compare."
    return {"state": state, "days_run": days_run, "window_days": window, "metric": metric, "value": val, "delivered": denom, "ci95": ci, "delta": d.get("text"),
            "baseline": base, "verdict": verdict, "claim_limits": claim, "snapshots": len(hist)}


def refresh_all() -> Dict[str, Any]:
    init_experiment_tables()
    conn = get_db()
    rows = [dict(r) for r in conn.execute("SELECT * FROM experiments WHERE status IN ('running','window_complete') ORDER BY id DESC LIMIT 100").fetchall()]
    conn.close()
    out = []
    for e in rows:
        try:
            r = readout(e)
            conn = get_db()
            conn.execute("UPDATE experiments SET readout_json=?, status=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (json.dumps(r, default=str), r["state"] if r["state"] in ("running", "window_complete") else e["status"], e["id"]))
            conn.commit(); conn.close()
            out.append({"id": e["id"], "campaign": e["campaign_name"], "state": r["state"], "verdict": r.get("verdict") or r.get("message")})
            if r["state"] == "window_complete" and r.get("verdict"):
                from . import growth
                growth.upsert_ideas([{"kind": "growth_hack", "title": f"Lesson: {e['campaign_name']} ({e.get('primary_kpi')})", "why": r["verdict"], "how": f"Window {e.get('window_days')}d complete. {r.get('claim_limits','')}", "priority": 55,
                                      "segment": e.get("campaign_name"), "kpi": e.get("primary_kpi") or "", "effort": "low", "expected_impact": "informs the next brief", "data": {"experiment_id": e["id"]}}], "rules")
        except Exception as ex:
            out.append({"id": e["id"], "error": redact(str(ex))[:160]})
    return {"refreshed": out}


def list_experiments(limit: int = 50) -> List[Dict[str, Any]]:
    init_experiment_tables()
    conn = get_db()
    rows = [dict(r) for r in conn.execute("SELECT * FROM experiments ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]
    conn.close()
    for r in rows:
        for k in ("baseline_json", "readout_json"):
            try:
                r[k[:-5]] = json.loads(r.pop(k) or "{}")
            except Exception:
                r[k[:-5]] = {}
    return rows
