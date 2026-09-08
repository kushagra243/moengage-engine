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


def campaigns(jitter: bool = False) -> List[Dict[str, Any]]:
    base = [
        {"id": "cmp_001", "name": "Weekend Flash Sale 20% Off", "channel": "Push", "status": "Active", "target_segment": "All Active App Users",
         "sent_count": 482100, "delivered_count": 453174, "delivery_rate": 94.0, "opened_count": 28450, "ctr": 6.27, "conversions": 3410, "conversion_rate": 1.2, "revenue_generated": 68200.0, "last_run": _ago(18)},
        {"id": "cmp_002", "name": "Cart Abandonment 1-Hour Reminder", "channel": "Push", "status": "Active", "target_segment": "Cart Abandoners (< 2h)",
         "sent_count": 42100, "delivered_count": 40416, "delivery_rate": 96.0, "opened_count": 5210, "ctr": 12.89, "conversions": 1890, "conversion_rate": 4.68, "revenue_generated": 94500.0, "last_run": _ago(1)},
        {"id": "cmp_003", "name": "VIP Loyalty Club Double Points", "channel": "Email", "status": "Active", "target_segment": "VIP High LTV (> $500)",
         "sent_count": 14200, "delivered_count": 13916, "delivery_rate": 98.0, "opened_count": 1244, "ctr": 8.94, "conversions": 717, "conversion_rate": 5.15, "revenue_generated": 141000.0, "last_run": _ago(30)},
        {"id": "cmp_004", "name": "Reactivation: 30-Day Inactive Winback", "channel": "Email", "status": "Active", "target_segment": "Dormant Users (> 30d)",
         "sent_count": 182400, "delivered_count": 171456, "delivery_rate": 94.0, "opened_count": 2434, "ctr": 1.42, "conversions": 412, "conversion_rate": 0.24, "revenue_generated": 12400.0, "last_run": _ago(50)},
        {"id": "cmp_005", "name": "New User Onboarding Guide", "channel": "In-App", "status": "Active", "target_segment": "New Signups (< 7d)",
         "sent_count": 30500, "delivered_count": 30500, "delivery_rate": 100.0, "opened_count": 9600, "ctr": 31.47, "conversions": 9600, "conversion_rate": 31.47, "revenue_generated": 0.0, "last_run": _ago(3)},
        {"id": "cmp_006", "name": "Price Drop Alert on Wishlist Items", "channel": "Push", "status": "Active", "target_segment": "Wishlist Users",
         "sent_count": 66700, "delivered_count": 64699, "delivery_rate": 97.0, "opened_count": 10900, "ctr": 16.85, "conversions": 2380, "conversion_rate": 3.68, "revenue_generated": 91600.0, "last_run": _ago(7)},
    ]
    if jitter:
        for c in base:
            f = random.uniform(0.93, 1.07)
            c["ctr"] = round(c["ctr"] * f, 2); c["opened_count"] = int(c["opened_count"] * f)
            c["revenue_generated"] = round(c["revenue_generated"] * random.uniform(0.9, 1.1), 2)
    for c in base:
        c["_source"] = "mock"
    return base


def segments() -> List[Dict[str, Any]]:
    rows = [
        {"id": "seg_001", "name": "Cart Abandoners (Last 24h)", "description": "Added to cart within 24h, no checkout", "type": "Behavioral", "estimated_reach": 34800,
         "criteria": {"event_filter": "Added to Cart >= 1 in last 24 hours", "exclusion_filter": "Purchase Completed >= 1 in last 24 hours"}, "created_at": "2026-08-15"},
        {"id": "seg_002", "name": "VIP High LTV Customers (> $500)", "description": "Cumulative purchase value over $500", "type": "Attribute & Behavioral", "estimated_reach": 14200,
         "criteria": {"user_attribute": "lifetime_value > 500", "event_filter": "Purchase Completed >= 3 in last 90 days"}, "created_at": "2026-07-10"},
        {"id": "seg_003", "name": "Dormant Users (Inactive > 45d)", "description": "No app launch in 45 days", "type": "Inactivity", "estimated_reach": 182400,
         "criteria": {"event_filter": "App Opened == 0 in last 45 days", "user_attribute": "has_previous_purchase == true"}, "created_at": "2026-06-20"},
        {"id": "seg_004", "name": "High-Intent Window Shoppers", "description": "4+ product views in 7 days, no add to cart", "type": "Behavioral", "estimated_reach": 62100,
         "criteria": {"event_filter": "Product Viewed >= 4 in last 7 days", "exclusion_filter": "Added to Cart >= 1 in last 7 days"}, "created_at": "2026-08-28"},
        {"id": "seg_005", "name": "Price-Sensitive Discount Seekers", "description": "Buy only with promo codes", "type": "Affinity", "estimated_reach": 51300,
         "criteria": {"user_attribute": "coupon_usage_rate > 0.8", "event_filter": "Promo Applied >= 1 in last 30 days"}, "created_at": "2026-08-01"},
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
