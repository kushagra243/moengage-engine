"""
Explicit demo dataset. Used ONLY when mock_mode is on. Every payload is tagged
source="mock" so the UI, the anomaly baseline and the agent can never mistake it
for live data.
"""
from __future__ import annotations
from datetime import datetime, timedelta
from typing import Any, Dict, List
import random


def _ago(h: int) -> str:
    return (datetime.now() - timedelta(hours=h)).strftime("%Y-%m-%d %H:%M")


FAULTS = {"cmp_002": {"ctr": 4.1, "opened_count": 1650}, "cmp_004": {"delivery_rate": 71.0, "delivered_count": 129504}, "cmp_006": {"revenue_generated": 214000.0}}


def _todays_faults() -> Dict[str, Dict[str, Any]]:
    """Demo scenario: the faults injected by seed_history persist for that calendar day,
    so re-snapshots and the daily process keep showing them instead of overwriting."""
    try:
        from ..database import get_setting
        import json as _json
        raw = get_setting("mock_scenario", "")
        if not raw:
            return {}
        sc = _json.loads(raw)
        return sc.get("faults", {}) if sc.get("date") == datetime.now().date().isoformat() else {}
    except Exception:
        return {}


MOCK_CONTENT = {
    "cmp_001": {"title": "Weekend Flash Sale: 20% off trading fees", "body": "This weekend only — 20% off spot trading fees. Don't miss out, ends Sunday midnight.", "cta": "Trade now"},
    "cmp_002": {"title": "Your order is waiting", "body": "You left a BTC order unfinished. Fees and 1% TDS are shown before you confirm.", "cta": "Review order"},
    "cmp_003": {"subject": "Double points for VIP members this month", "body": "Your fee tier is 12% away from the next level. Points double on every trade this month. Crypto products and NFTs are unregulated and can be highly risky. There may be no regulatory recourse for any loss from such transactions.", "cta": "See your tier"},
    "cmp_004": {"subject": "What changed since you last traded", "body": "Three things moved on your watchlist this month. Here are the facts, plus one tool: price alerts. Crypto products and NFTs are unregulated and can be highly risky. There may be no regulatory recourse for any loss from such transactions.", "cta": "Open watchlist"},
    "cmp_005": {"title": "Price drop on your wishlist", "body": "SOL is 8% lower than when you added it. Set an alert so you don't have to watch the screen.", "cta": "Set alert"},
    "cmp_006": {"title": "Welcome to CoinDCX", "body": "Your account is verified. Add money by UPI in 20 seconds; withdrawals are instant.", "cta": "Add money"},
}


def campaigns(jitter: bool = False) -> List[Dict[str, Any]]:
    base = [
        {"id": "cmp_001", "name": "Weekend Flash Sale 20% Off", "channel": "Push", "status": "Active", "target_segment": "All Active App Users",
         "sent_count": 482100, "delivered_count": 453174, "delivery_rate": 94.0, "opened_count": 28450, "ctr": 6.27, "conversions": 3410, "conversion_rate": 1.2, "revenue_generated": 68200.0, "last_run": _ago(18), "campaign_content": MOCK_CONTENT["cmp_001"]},
        {"id": "cmp_002", "name": "Cart Abandonment 1-Hour Reminder", "channel": "Push", "status": "Active", "target_segment": "Cart Abandoners (< 2h)",
         "sent_count": 42100, "delivered_count": 40416, "delivery_rate": 96.0, "opened_count": 5210, "ctr": 12.89, "conversions": 1890, "conversion_rate": 4.68, "revenue_generated": 94500.0, "last_run": _ago(1), "campaign_content": MOCK_CONTENT["cmp_002"]},
        {"id": "cmp_003", "name": "VIP Loyalty Club Double Points", "channel": "Email", "status": "Active", "target_segment": "HVT_Sep26",
         "sent_count": 14200, "delivered_count": 13916, "delivery_rate": 98.0, "opened_count": 1244, "ctr": 8.94, "conversions": 717, "conversion_rate": 5.15, "revenue_generated": 141000.0, "last_run": _ago(30), "campaign_content": MOCK_CONTENT["cmp_003"]},
        {"id": "cmp_004", "name": "Reactivation: 30-Day Inactive Winback", "channel": "Email", "status": "Active", "target_segment": "Dormant_D60_LowProp_Sep26",
         "sent_count": 182400, "delivered_count": 171456, "delivery_rate": 94.0, "opened_count": 2434, "ctr": 1.42, "conversions": 412, "conversion_rate": 0.24, "revenue_generated": 12400.0, "last_run": _ago(50), "campaign_content": MOCK_CONTENT["cmp_004"]},
        {"id": "cmp_005", "name": "New User Onboarding Guide", "channel": "In-App", "status": "Active", "target_segment": "FTD_NoTrade_Sep26",
         "sent_count": 30500, "delivered_count": 30500, "delivery_rate": 100.0, "opened_count": 9600, "ctr": 31.47, "conversions": 9600, "conversion_rate": 31.47, "revenue_generated": 0.0, "last_run": _ago(3), "campaign_content": MOCK_CONTENT["cmp_005"]},
        {"id": "cmp_006", "name": "Price Drop Alert on Wishlist Items", "channel": "Push", "status": "Active", "target_segment": "Wishlist Users",
         "sent_count": 66700, "delivered_count": 64699, "delivery_rate": 97.0, "opened_count": 10900, "ctr": 16.85, "conversions": 2380, "conversion_rate": 3.68, "revenue_generated": 91600.0, "last_run": _ago(7), "campaign_content": MOCK_CONTENT["cmp_006"]},
    ]
    if jitter:
        for c in base:
            f = random.uniform(0.93, 1.07)
            c["ctr"] = round(c["ctr"] * f, 2); c["opened_count"] = int(c["opened_count"] * f)
            c["revenue_generated"] = round(c["revenue_generated"] * random.uniform(0.9, 1.1), 2)
    faults = _todays_faults()
    for c in base:
        c.update(faults.get(c["id"], {}))
        c["_source"] = "mock"
    return base


def segments() -> List[Dict[str, Any]]:
    """Monthly cohort uploads named by the team's nomenclature (family_version)."""
    rows = [
        {"id": "seg_hvt_aug", "name": "HVT_Aug26", "description": "High-value traders, August upload", "type": "File", "estimated_reach": 4180, "created_at": "2026-08-02"},
        {"id": "seg_hvt_sep", "name": "HVT_Sep26", "description": "High-value traders, September upload", "type": "File", "estimated_reach": 4420, "created_at": "2026-09-02"},
        {"id": "seg_mvt_sep", "name": "MVT_Sep26", "description": "Mid-value traders", "type": "File", "estimated_reach": 18900, "created_at": "2026-09-02"},
        {"id": "seg_lvt_sep", "name": "LVT_Sep26", "description": "Low-value traders", "type": "File", "estimated_reach": 61200, "created_at": "2026-09-02"},
        {"id": "seg_hft_fut", "name": "HFT_Futures_Sep26", "description": "High-frequency futures traders", "type": "File", "estimated_reach": 2650, "created_at": "2026-09-02"},
        {"id": "seg_dorm", "name": "Dormant_D60_LowProp_Sep26", "description": "No trade 60d, low propensity", "type": "Filter", "estimated_reach": 142000, "created_at": "2026-09-03"},
        {"id": "seg_ftd", "name": "FTD_NoTrade_Sep26", "description": "First deposit, no trade yet", "type": "Filter", "estimated_reach": 7300, "created_at": "2026-09-03"},
        {"id": "seg_rekyc", "name": "Re-KYC_Pending_Sep26", "description": "Re-KYC due within 30 days", "type": "Filter", "estimated_reach": 9800, "created_at": "2026-09-01"},
        {"id": "seg_xq", "name": "XQ_TG_Sep26", "description": "(undefined code XQ) trading game cohort", "type": "File", "estimated_reach": 1200, "created_at": "2026-09-05"},
    ]
    for r in rows:
        r["_source"] = "mock"
    return rows


def flows() -> List[Dict[str, Any]]:
    return [{
        "id": "flow_demo_001", "name": "Reactivation & Warm-up Flow (Dormant 30D)", "status": "Active",
        "entry_trigger": "Push sent in last 30D & App not opened in last 30D",
        "steps": ["Create cohort", "Wait for app activity (max 14d)", "Split: opened? → 2 PN/day × 7d | else exit & suppress"],
        "stats": {"enrolled": 42500, "reactivated_pct": 15.0, "warmup_avg_ctr": 14.8}, "_source": "mock",
    }]


def analytics() -> Dict[str, Any]:
    return {
        "timeframe": "Last 7 Days", "active_users_count": 842000,
        "events_breakdown": [
            {"event": "App Opened", "count": 1240000, "change_pct": 4.2}, {"event": "Product Viewed", "count": 712000, "change_pct": -1.5},
            {"event": "Added to Cart", "count": 148000, "change_pct": 8.3}, {"event": "Checkout Started", "count": 52000, "change_pct": 6.1},
            {"event": "Purchase Completed", "count": 31400, "change_pct": 9.4}, {"event": "App Uninstalled", "count": 3420, "change_pct": -12.0}],
        "conversion_funnels": {"view_to_cart_rate": 20.78, "cart_to_checkout_rate": 35.13, "checkout_to_purchase_rate": 60.38, "overall_view_to_purchase_rate": 4.41},
        "channel_performance_summary": {"Push": {"avg_ctr": 8.1}, "Email": {"avg_open_rate": 22.4, "avg_ctr": 4.8}, "In-App": {"avg_conversion": 14.1}},
        "_source": "mock",
    }


def whoami() -> Dict[str, Any]:
    return {"valid": True, "mode": "mock", "user": "demo.marketer@brand.com", "workspace": "Production-Retail-Demo", "_source": "mock"}


def seed_history(days: int = 30, inject: bool = True, source: str = "mock") -> Dict[str, Any]:
    """Write `days` of realistic daily snapshots (weekly seasonality + noise) so anomaly
    detection, sparklines and the feed have something to work with in demo mode.
    With inject=True the latest day carries three visible faults."""
    from ..anomaly import record_snapshot, detect_anomalies
    from ..anomaly.store import get_db
    rnd = random.Random(20260907)
    conn = get_db(); conn.execute("DELETE FROM campaign_snapshots WHERE source=?", (source,)); conn.execute("DELETE FROM anomaly_events WHERE source=?", (source,)); conn.commit(); conn.close()
    base = campaigns()
    today = datetime.now().date()
    for i in range(days, 0, -1):
        d = today - timedelta(days=i)
        dow = 1.08 if d.weekday() >= 5 else 1.0
        rows = []
        for c in base:
            f = rnd.gauss(1, 0.05); r = dict(c)
            r["ctr"] = round(c["ctr"] * f * dow, 2); r["opened_count"] = int(c["opened_count"] * f * dow)
            r["delivery_rate"] = round(c["delivery_rate"] + rnd.gauss(0, 0.4), 1)
            r["conversion_rate"] = round(c["conversion_rate"] * rnd.gauss(1, 0.06), 2)
            r["revenue_generated"] = round(c["revenue_generated"] * rnd.gauss(1, 0.08), 2)
            rows.append(r)
        record_snapshot(rows, source=source, snapshot_date=d.isoformat())
    from ..database import set_setting
    import json as _json
    set_setting("mock_scenario", _json.dumps({"date": today.isoformat(), "faults": FAULTS if inject else {}}))
    tod = campaigns()   # applies today's faults
    record_snapshot(tod, source=source)
    rep = detect_anomalies(source=source, persist=True)
    return {"days": days, "injected": inject, "critical": rep["critical"], "warnings": rep["warnings"]}
