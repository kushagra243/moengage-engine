"""
Outlier detection for campaign metrics against each campaign's own history.

Policy (defaults, tunable via DEFAULT_POLICY / settings):
  * Baseline window: up to 30 prior daily snapshots, excluding the day under test.
  * Minimum history: 7 points. Below that we fall back to a cross-sectional
    peer comparison (same channel, same day) flagged with lower confidence.
  * Primary detector: modified z-score using median and MAD
    (Iglewicz & Hoaglin): M = 0.6745 * (x - median) / MAD. |M| >= 3.5 critical,
    >= 2.5 warning. Robust to the small, skewed samples typical of campaigns.
  * Secondary: Tukey fences on the IQR. Beyond Q3 + 3*IQR / Q1 - 3*IQR is
    critical ("far out"), beyond 1.5*IQR is warning. Used when MAD == 0
    (flat history) and as a confirmation signal.
  * Rate metrics (ctr, delivery_rate, conversion_rate) additionally require the
    day's denominator to be >= min_denominator so a 3-send day cannot trigger.
  * Day-of-week adjustment when >= 21 points: baseline restricted to the same
    weekday if that leaves >= 4 points.
Volume metrics are also checked on a log scale to tame heavy tails.
"""
from __future__ import annotations
import math
import statistics
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from .store import METRICS, get_history, list_tracked_campaigns, save_events

DEFAULT_POLICY = {
    "window_days": 30,
    "min_history": 7,
    "modz_warning": 2.5,
    "modz_critical": 3.5,
    "iqr_k_warning": 1.5,
    "iqr_k_critical": 3.0,
    "min_denominator": {"delivery_rate": 200, "ctr": 500, "conversion_rate": 500},   # Wilson-style measurability gate
    "max_halfwidth_pp": 2.0,      # skip a rate if its 95% Wilson half-width exceeds max(2pp, 50% of baseline)
    "rate_scale_floor": 0.005,    # absolute floor (in rate units, i.e. 0.5pp) when MAD collapses
    "one_sided_low": ["delivery_rate"],   # only drops matter for these
    "dow_min_points": 21,
    "metrics": {
        # metric: (kind, "higher_is_better")
        "delivery_rate": ("rate", True),
        "ctr": ("rate", True),
        "conversion_rate": ("rate", True),
        "sent_count": ("volume", None),
        "delivered_count": ("volume", None),
        "opened_count": ("volume", True),
        "conversions": ("volume", True),
        "revenue_generated": ("volume", True),
    },
}

_RATE_DENOM = {"delivery_rate": "sent_count", "ctr": "delivered_count", "conversion_rate": "delivered_count"}


def wilson_halfwidth(p: float, n: float, z: float = 1.96) -> float:
    """Half-width (in percentage points) of the Wilson 95% interval for rate p (percent) with n trials."""
    if n <= 0:
        return 100.0
    pp = max(0.0, min(1.0, p / 100.0))
    denom = 1 + z * z / n
    centre_adj = z * math.sqrt(pp * (1 - pp) / n + z * z / (4 * n * n))
    return (centre_adj / denom) * 100.0


def _mad(xs: List[float], med: float) -> float:
    return statistics.median([abs(x - med) for x in xs]) if xs else 0.0


def _quartiles(xs: List[float]):
    s = sorted(xs)
    n = len(s)
    if n < 4:
        return s[0], s[-1]
    def q(p):
        k = (n - 1) * p
        f = math.floor(k); c = math.ceil(k)
        return s[f] if f == c else s[f] + (s[c] - s[f]) * (k - f)
    return q(0.25), q(0.75)


def _score_point(x: float, hist: List[float], policy: Dict[str, Any], kind: str, metric_name: str = "", floor_hist: Optional[List[float]] = None) -> Optional[Dict[str, Any]]:
    """Return anomaly details or None."""
    vals = list(hist)
    if kind == "volume":
        # log1p scale for heavy-tailed counts
        tx = math.log1p(max(x, 0.0)); tv = [math.log1p(max(v, 0.0)) for v in vals]
    else:
        tx, tv = x, vals
    med = statistics.median(tv)
    mad = _mad(tv, med)
    q1, q3 = _quartiles(tv)
    iqr = q3 - q1
    if floor_hist and len(floor_hist) >= 7:
        fv = [math.log1p(max(v, 0.0)) for v in floor_hist] if kind == "volume" else list(floor_hist)
        fmed = statistics.median(fv); fmad = _mad(fv, fmed); fq1, fq3 = _quartiles(fv)
        mad = max(mad, 0.6 * fmad)              # a weekday subset may not be tighter than 60% of the full window
        iqr = max(iqr, 0.6 * (fq3 - fq1))

    modz = None
    scale = mad
    if kind == "rate":
        scale = max(mad, policy.get("rate_scale_floor", 0.005) * 100 / 1.4826)   # floor expressed in pct points / 1.4826
    if scale > 0:
        modz = 0.6745 * (tx - med) / scale
    sev = None; method = None; score = None
    if modz is not None:
        if abs(modz) >= policy["modz_critical"]:
            sev, method, score = "critical", "modified_z", modz
        elif abs(modz) >= policy["modz_warning"]:
            sev, method, score = "warning", "modified_z", modz
    # IQR fences — primary when MAD==0, confirmation otherwise
    if iqr > 0:
        if tx > q3 + policy["iqr_k_critical"] * iqr or tx < q1 - policy["iqr_k_critical"] * iqr:
            fence_sev = "critical"
        elif tx > q3 + policy["iqr_k_warning"] * iqr or tx < q1 - policy["iqr_k_warning"] * iqr:
            fence_sev = "warning"
        else:
            fence_sev = None
        if sev is None and fence_sev:
            sev, method = fence_sev, "iqr_fence"
            score = (tx - (q3 if tx > q3 else q1)) / iqr
    elif mad == 0 and iqr == 0:
        # perfectly flat history: any change beyond 20% relative is warning, 50% critical
        base = statistics.median(vals)
        if base > 0:
            rel = (x - base) / base
            if abs(rel) >= 0.5:
                sev, method, score = "critical", "flat_history_relative", rel
            elif abs(rel) >= 0.2:
                sev, method, score = "warning", "flat_history_relative", rel
    if sev is None:
        return None
    baseline = statistics.median(vals)
    if metric_name in policy.get("one_sided_low", []) and x > baseline:
        return None
    return {"severity": sev, "method": method, "score": round(float(score), 3), "baseline": round(baseline, 4),
            "direction": "up" if x > baseline else "down", "n_history": len(vals)}


def _peer_check(today_rows: List[Dict[str, Any]], row: Dict[str, Any], metric: str, policy: Dict[str, Any], kind: str) -> Optional[Dict[str, Any]]:
    peers = [r[metric] for r in today_rows if r is not row and r.get("channel") == row.get("channel") and r.get(metric) is not None]
    if len(peers) < 4:
        return None
    res = _score_point(float(row[metric]), [float(p) for p in peers], policy, kind, metric)
    if res:
        res["method"] = "peer_" + res["method"]
        res["confidence"] = "low"
    return res


def detect_anomalies(source: str = "live", snapshot_date: Optional[str] = None, policy: Optional[Dict[str, Any]] = None, persist: bool = True) -> Dict[str, Any]:
    policy = {**DEFAULT_POLICY, **(policy or {})}
    snapshot_date = snapshot_date or date.today().isoformat()
    tracked = list_tracked_campaigns(source)
    events: List[Dict[str, Any]] = []
    evaluated = 0; insufficient = []

    # today's rows for peer comparisons
    today_rows: List[Dict[str, Any]] = []
    histories: Dict[str, List[Dict[str, Any]]] = {}
    for t in tracked:
        h = get_history(t["campaign_id"], source, policy["window_days"] + 1)
        histories[t["campaign_id"]] = h
        todays = [r for r in h if r["snapshot_date"] == snapshot_date]
        if todays:
            today_rows.append(todays[-1])

    for t in tracked:
        cid = t["campaign_id"]
        h = histories[cid]
        todays = [r for r in h if r["snapshot_date"] == snapshot_date]
        if not todays:
            continue
        today = todays[-1]
        prior = [r for r in h if r["snapshot_date"] < snapshot_date][-policy["window_days"]:]
        evaluated += 1
        for metric, (kind, higher_better) in policy["metrics"].items():
            x = today.get(metric)
            if x is None:
                continue
            x = float(x)
            denom = None
            if kind == "rate":
                denom = today.get(_RATE_DENOM.get(metric, ""), None)
                need = policy["min_denominator"].get(metric, 200) if isinstance(policy["min_denominator"], dict) else policy["min_denominator"]
                if denom is not None and float(denom) < need:
                    continue
            hist_vals = [float(r[metric]) for r in prior if r.get(metric) is not None]
            if kind == "rate" and denom:
                base_p = statistics.median(hist_vals) if hist_vals else x
                hw = wilson_halfwidth(x, float(denom))
                if hw > max(policy["max_halfwidth_pp"], 0.5 * base_p):
                    continue   # today's rate is not measurable enough to judge
            res = None
            if len(hist_vals) >= policy["min_history"]:
                full_vals = list(hist_vals)
                # day-of-week restriction when plenty of history (>= 6 same-weekday points), floored by the full window
                if len(hist_vals) >= policy["dow_min_points"]:
                    dow = datetime.fromisoformat(snapshot_date).weekday()
                    same = [float(r[metric]) for r in prior if r.get(metric) is not None and datetime.fromisoformat(r["snapshot_date"]).weekday() == dow]
                    if len(same) >= 6:
                        hist_vals = same
                res = _score_point(x, hist_vals, policy, kind, metric, floor_hist=full_vals)
                if res:
                    res["confidence"] = "high" if len(hist_vals) >= 14 else "medium"
            else:
                if cid not in insufficient:
                    insufficient.append(cid)
                res = _peer_check(today_rows, today, metric, policy, kind)
                # hard rules that need no history
                if not res:
                    if metric == "delivery_rate" and x < 90.0:
                        res = {"severity": "critical" if x < 80 else "warning", "method": "hard_rule_delivery", "score": round(90.0 - x, 2), "baseline": 90.0, "direction": "down", "n_history": len(hist_vals), "confidence": "rule"}
                    elif metric == "ctr" and x == 0 and denom and float(denom) >= 500:
                        res = {"severity": "critical", "method": "hard_rule_zero_ctr", "score": 0.0, "baseline": statistics.median(hist_vals) if hist_vals else 0.0, "direction": "down", "n_history": len(hist_vals), "confidence": "rule"}
            if not res:
                continue
            good_or_bad = None
            if higher_better is not None:
                good_or_bad = "good" if (res["direction"] == "up") == higher_better else "bad"
            msg = (f"{today.get('campaign_name') or cid}: {metric} is {x:g} vs baseline {res['baseline']:g} "
                   f"({res['direction']}, {res['method']} score {res['score']}, n={res.get('n_history', len(hist_vals))})")
            events.append({
                "snapshot_date": snapshot_date, "source": source, "campaign_id": cid,
                "campaign_name": today.get("campaign_name"), "channel": today.get("channel"),
                "metric": metric, "value": x, "baseline": res["baseline"], "score": res["score"],
                "method": res["method"], "severity": res["severity"], "direction": res["direction"], "n_history": res.get("n_history", len(hist_vals)),
                "confidence": res.get("confidence", "medium"), "impact": good_or_bad, "message": msg,
            })
    sev_rank = {"critical": 0, "warning": 1}
    events.sort(key=lambda e: (sev_rank.get(e["severity"], 9), -abs(e["score"])))
    if persist:
        save_events(events)
    from ..metrics import enrich_anomalies
    return enrich_anomalies({
        "snapshot_date": snapshot_date, "source": source, "campaigns_evaluated": evaluated,
        "campaigns_with_insufficient_history": insufficient, "policy": {k: v for k, v in policy.items() if k != "metrics"},
        "anomalies": events, "critical": sum(1 for e in events if e["severity"] == "critical"),
        "warnings": sum(1 for e in events if e["severity"] == "warning"),
    })
