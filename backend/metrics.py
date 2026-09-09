"""
Metric dictionary and plain-language layer.

One place that decides how every number is named, formatted and explained, so
the UI, the API and the agent use the same words. Rules:
  * rates move in percentage points (pp); counts and money move in percent
  * every comparison is against the campaign's own last ~30 days
  * severity is urgency (act_today / watch / normal) and good surprises are
    tagged separately; the statistic behind a call is never the headline
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional

METRICS: Dict[str, Dict[str, Any]] = {
    "delivery_rate":     {"label": "Delivery rate", "short": "Delivered %", "kind": "rate", "unit": "%", "denominator": "delivered ÷ sent", "higher_better": True,
                          "what": "Share of sent messages that reached a device or inbox.", "why_it_moves": "Invalid or expired push tokens, opt-outs, bounces, sender reputation, a bad app release."},
    "ctr":               {"label": "Click rate", "short": "Click %", "kind": "rate", "unit": "%", "denominator": "clicked ÷ delivered", "higher_better": True,
                          "what": "Share of delivered messages that were clicked (push, in-app) or clicked after opening (email).", "why_it_moves": "Creative fatigue, relevance of the audience, send time, competing sends, market context."},
    "conversion_rate":   {"label": "Conversion rate", "short": "Converted %", "kind": "rate", "unit": "%", "denominator": "converted ÷ delivered", "higher_better": True,
                          "what": "Share of delivered messages followed by the campaign's goal event inside its attribution window.", "why_it_moves": "Landing friction, offer or product change, KYC/payment issues, market regime."},
    "sent_count":        {"label": "Sent", "short": "Sent", "kind": "count", "unit": "", "denominator": "messages attempted", "higher_better": None,
                          "what": "Messages the campaign attempted to send.", "why_it_moves": "Audience definition changes, upstream event volume, frequency caps and DND eating sends."},
    "delivered_count":   {"label": "Delivered", "short": "Delivered", "kind": "count", "unit": "", "denominator": "messages delivered", "higher_better": None,
                          "what": "Messages that reached a device or inbox.", "why_it_moves": "Sent volume and delivery rate."},
    "opened_count":      {"label": "Clicks", "short": "Clicks", "kind": "count", "unit": "", "denominator": "clicks", "higher_better": True,
                          "what": "Number of clicks (or opens for email).", "why_it_moves": "Delivered volume and click rate."},
    "conversions":       {"label": "Conversions", "short": "Conv.", "kind": "count", "unit": "", "denominator": "goal events attributed", "higher_better": True,
                          "what": "Goal events attributed to the campaign.", "why_it_moves": "Delivered volume and conversion rate."},
    "revenue_generated": {"label": "Attributed revenue", "short": "Revenue", "kind": "money", "unit": "$", "denominator": "last-touch, campaign window", "higher_better": True,
                          "what": "Revenue MoEngage attributes to the campaign by last touch. Not incremental unless read against a holdout.", "why_it_moves": "Conversions and order value; a single large order can move it alone."},
}

UNUSUAL_LABELS = {1: "slightly outside normal", 2: "clearly outside normal", 3: "far outside normal", 4: "extremely far outside normal"}
URGENCY_LABELS = {"act_today": "Act today", "watch": "Watch", "good_surprise": "Good surprise", "normal": "Normal"}
CHANNEL_ENGAGEMENT = {"push": "Click rate", "email": "Click rate", "in-app": "Click rate", "inapp": "Click rate", "sms": "Click rate", "whatsapp": "Read rate", "cards": "Click rate"}


def kind(metric: str) -> str:
    return METRICS.get(metric, {}).get("kind", "count")


def label(metric: str) -> str:
    return METRICS.get(metric, {}).get("label", metric.replace("_", " "))


def fmt_value(metric: str, v: Optional[float]) -> str:
    if v is None:
        return "—"
    k = kind(metric)
    if k == "rate":
        return f"{v:.1f}%"
    if k == "money":
        return f"${v:,.0f}"
    return f"{v:,.0f}"


def delta(metric: str, today: Optional[float], base: Optional[float]) -> Dict[str, Any]:
    """Rates → percentage points; counts/money → percent. Returns text and sign-aware 'better'."""
    if today is None or base is None:
        return {"text": "—", "value": None, "unit": None, "better": None}
    hb = METRICS.get(metric, {}).get("higher_better")
    if kind(metric) == "rate":
        d = today - base
        text = f"{d:+.1f} pp"
        unit = "pp"
    else:
        if base == 0:
            return {"text": "new", "value": None, "unit": "%", "better": None}
        d = (today - base) / abs(base) * 100
        text = f"{d:+.0f}%"
        unit = "%"
    better = None if hb is None or abs(d) < 1e-9 else ((d > 0) == hb)
    return {"text": text, "value": round(d, 2), "unit": unit, "better": better}


def unusual_level(score: Optional[float], method: str) -> int:
    """Map the detector statistic to a 1–4 'how unusual' scale (the statistic itself is never shown)."""
    if score is None:
        return 1
    s = abs(float(score))
    if method.startswith("modified_z") or method.startswith("peer_modified_z"):
        return 4 if s >= 8 else 3 if s >= 5 else 2 if s >= 3.5 else 1
    if "iqr" in method:
        return 4 if s >= 6 else 3 if s >= 3 else 2 if s >= 1.5 else 1
    if "flat" in method:
        return 4 if s >= 1.0 else 3 if s >= 0.5 else 2 if s >= 0.2 else 1
    if "hard_rule" in method:
        return 3
    return 2


def urgency(event: Dict[str, Any]) -> str:
    """Urgency is about what to do, not how big the statistic is."""
    if event.get("impact") == "good":
        return "good_surprise"
    lvl = unusual_level(event.get("score"), event.get("method", ""))
    sev = event.get("severity")
    metric = event.get("metric")
    peer = str(event.get("method", "")).startswith("peer_")
    if metric == "delivery_rate" and (event.get("value") or 100) < 85:
        return "act_today"
    if peer:
        return "watch"          # a peer comparison is a hint, never an order
    if sev == "critical" and lvl >= 3 and event.get("impact") == "bad":
        return "act_today"
    if sev in ("critical", "warning"):
        return "watch"
    return "normal"


def anomaly_headline(event: Dict[str, Any]) -> Dict[str, str]:
    """Plain-language headline + one-line 'how we know'."""
    m = event.get("metric", ""); v = event.get("value"); b = event.get("baseline")
    name = event.get("campaign_name") or event.get("campaign_id")
    d = delta(m, v, b)
    lvl = unusual_level(event.get("score"), event.get("method", ""))
    direction = "up" if (v or 0) > (b or 0) else "down"
    method = event.get("method", "")
    if method.startswith("peer_"):
        head = f"{name}: {label(m).lower()} {fmt_value(m, v)} vs {fmt_value(m, b)} for similar campaigns today ({d['text']}, {UNUSUAL_LABELS[lvl]}; no own history yet)"
    elif "hard_rule" in method:
        head = f"{name}: {label(m).lower()} {fmt_value(m, v)} (below the {fmt_value(m, b)} floor)"
    else:
        head = f"{name}: {label(m).lower()} {fmt_value(m, v)}, usually about {fmt_value(m, b)} ({d['text']}, {UNUSUAL_LABELS[lvl]})"
    n = event.get("n_history") or event.get("days") or ""
    basis = ("compared with peer campaigns on the same channel today (thin history)" if method.startswith("peer_")
             else "a hard rule that needs no history" if "hard_rule" in method
             else f"compared with this campaign's own last {n} days, weekday-adjusted" if n else "compared with this campaign's own recent history")
    return {"headline": head, "how_we_know": basis.capitalize() + ".", "direction": direction, "delta_text": d["text"], "unusual": lvl, "unusual_label": UNUSUAL_LABELS[lvl]}


def enrich_anomalies(report: Dict[str, Any]) -> Dict[str, Any]:
    for e in report.get("anomalies", []):
        h = anomaly_headline(e)
        e.update({"headline": h["headline"], "how_we_know": h["how_we_know"], "delta_text": h["delta_text"], "unusual": h["unusual"], "unusual_label": h["unusual_label"]})
        e["urgency"] = urgency(e); e["urgency_label"] = URGENCY_LABELS[e["urgency"]]
        e["metric_label"] = label(e.get("metric", ""))
    events = report.get("anomalies", [])
    # a count is secondary when its rate on the same campaign is flagged too (clicks ↔ click rate, conversions ↔ conversion rate, delivered ↔ delivery rate)
    pair = {"opened_count": "ctr", "conversions": "conversion_rate", "delivered_count": "delivery_rate"}
    flagged = {(e.get("campaign_id"), e.get("metric")) for e in events}
    for e in events:
        e["secondary"] = pair.get(e.get("metric")) is not None and (e.get("campaign_id"), pair[e["metric"]]) in flagged
    events.sort(key=lambda e: (e.get("secondary", False), {"act_today": 0, "watch": 1, "good_surprise": 2, "normal": 3}.get(e.get("urgency"), 9)))
    primary = [e for e in events if not e.get("secondary")]
    report["by_urgency"] = {k: sum(1 for e in primary if e.get("urgency") == k) for k in ("act_today", "watch", "good_surprise", "normal")}
    report["how_detection_works"] = ("Each metric is compared with the same campaign's own last 30 days (weekday-adjusted once 3 weeks exist). "
                                     "A day is flagged when it falls well outside that normal range; rates need enough volume to be judged. "
                                     "'Act today' means a bad move that is far outside normal or delivery below 85%; 'Watch' is outside normal but not yet actionable; "
                                     "'Good surprise' is a favourable outlier worth understanding before scaling.")
    return report


def lights_from_diagnosis(diag: Dict[str, Any]) -> Dict[str, Dict[str, str]]:
    """Three explicit lights per campaign instead of a composite health score."""
    st = diag.get("stats") or {}
    def light(metric: str, red_pp: float, amber_pp: float, floor: Optional[float] = None) -> Dict[str, str]:
        s = st.get(metric) or {}
        today, base = s.get("today"), s.get("mean_28d")
        if today is None or base is None:
            return {"state": "grey", "text": "no baseline yet"}
        d = today - base
        if floor is not None and today < floor:
            return {"state": "red", "text": f"{fmt_value(metric, today)} (below {floor:g}% floor)"}
        if d <= -red_pp:
            return {"state": "red", "text": f"{fmt_value(metric, today)}, {d:+.1f} pp vs usual"}
        if d <= -amber_pp:
            return {"state": "amber", "text": f"{fmt_value(metric, today)}, {d:+.1f} pp vs usual"}
        return {"state": "green", "text": f"{fmt_value(metric, today)}, {d:+.1f} pp vs usual"}
    return {"delivery": light("delivery_rate", 5.0, 2.0, floor=85.0), "engagement": light("ctr", 3.0, 1.0), "conversion": light("conversion_rate", 1.0, 0.4)}


def so_what(diag: Dict[str, Any], lights: Dict[str, Dict[str, str]]) -> str:
    reds = [k for k, v in lights.items() if v["state"] == "red"]; ambers = [k for k, v in lights.items() if v["state"] == "amber"]
    cause = (diag.get("likely_causes") or [{}])[0].get("cause", "")
    opt = (diag.get("options") or [{}])[0].get("action", "")
    if reds:
        return f"{', '.join(reds).capitalize()} below usual — {cause.lower() if cause else 'investigate'}. First move: {opt}."
    if ambers:
        return f"{', '.join(ambers).capitalize()} slipping; watch for a third day before changing anything."
    return "Within its usual range."


def channel_kpis(campaigns: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Engagement per channel weighted by delivered volume, with counts — replaces a cross-channel 'avg CTR'."""
    from .anomaly.store import normalise_campaign
    acc: Dict[str, Dict[str, float]] = {}
    missing = 0
    for c in campaigns:
        n = normalise_campaign(c)
        if n["stats_missing"]:
            missing += 1
            continue
        ch = (n["channel"] or "Other").strip() or "Other"
        a = acc.setdefault(ch, {"delivered": 0.0, "clicks": 0.0, "sent": 0.0, "conv": 0.0, "rev": 0.0, "n": 0})
        d = n["delivered_count"] or 0.0
        a["delivered"] += d; a["sent"] += n["sent_count"] or 0.0; a["n"] += 1
        a["clicks"] += (n["opened_count"] if n["opened_count"] is not None else (d * (n["ctr"] or 0) / 100.0))
        a["conv"] += (n["conversions"] if n["conversions"] is not None else (d * (n["conversion_rate"] or 0) / 100.0))
        a["rev"] += n["revenue_generated"] or 0.0
    out = []
    for ch, a in acc.items():
        out.append({"channel": ch, "campaigns": a["n"], "delivered": int(a["delivered"]), "sent": int(a["sent"]),
                    "engagement_label": CHANNEL_ENGAGEMENT.get(ch.lower(), "Click rate"),
                    "engagement_rate": round(a["clicks"] / a["delivered"] * 100, 2) if a["delivered"] else None,
                    "delivery_rate": round(a["delivered"] / a["sent"] * 100, 1) if a["sent"] else None,
                    "conversion_rate": round(a["conv"] / a["delivered"] * 100, 2) if a["delivered"] else None,
                    "revenue": round(a["rev"], 2)})
    out.sort(key=lambda x: -x["delivered"])
    for o in out:
        o["campaigns_without_stats"] = missing
    return out


def funnel_lines(diag: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Absolute 'usual → today' per stage, with the right unit."""
    st = diag.get("stats") or {}
    out = []
    for m in ("sent_count", "delivery_rate", "ctr", "conversion_rate", "revenue_generated"):
        s = st.get(m) or {}
        t, b = s.get("today"), s.get("mean_28d")
        d = delta(m, t, b)
        out.append({"metric": m, "label": label(m), "usual": fmt_value(m, b), "today": fmt_value(m, t), "delta_text": d["text"], "better": d["better"]})
    return out


def dictionary() -> Dict[str, Any]:
    return {"metrics": METRICS, "urgency": URGENCY_LABELS, "unusual": UNUSUAL_LABELS,
            "conventions": ["Rates change in percentage points (pp); counts and revenue change in percent.",
                            "'Usual' means this campaign's own last 30 days, weekday-adjusted.",
                            "Attributed revenue is last-touch and not incremental unless a holdout exists.",
                            "Red is reserved for 'Act today'; amber is 'Watch'; a favourable outlier is a 'Good surprise'."]}
