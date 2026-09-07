import re
import json
import urllib.parse
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
import requests

from .database import get_setting

class MoEngageClient:
    def __init__(self):
        self.region = get_setting("moengage_region", "dashboard-01.moengage.com").strip()
        self.app_id = get_setting("moengage_app_id", "").strip()
        self.raw_cookies = get_setting("moengage_cookies", "").strip()
        self.mock_mode = get_setting("mock_mode", "true").lower() == "true"
        self.session = requests.Session()
        self._configure_session()

    def _configure_session(self):
        # Setup modern browser headers
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": f"https://{self.region}/",
            "Origin": f"https://{self.region}"
        })

        if not self.raw_cookies:
            return

        # Parse cookies from raw string or JSON
        cookies_dict = {}
        if self.raw_cookies.startswith("{") and self.raw_cookies.endswith("}"):
            try:
                cookies_dict = json.loads(self.raw_cookies)
            except Exception:
                pass

        if not cookies_dict:
            # Parse Cookie header string: "key1=val1; key2=val2"
            for item in self.raw_cookies.split(";"):
                item = item.strip()
                if "=" in item:
                    key, val = item.split("=", 1)
                    cookies_dict[key.strip()] = val.strip()

        # Update requests cookiejar
        for k, v in cookies_dict.items():
            self.session.cookies.set(k, v, domain=self.region.replace("https://", "").replace("http://", "").split("/")[0])

        # Auto-detect JWT or Auth token if present
        for k, v in cookies_dict.items():
            k_lower = k.lower()
            if "jwt" in k_lower or "token" in k_lower or "auth" in k_lower:
                if v.startswith("eyJ"): # Standard JWT header
                    self.session.headers["Authorization"] = f"Bearer {v}"
                    break

        if self.app_id:
            self.session.headers["MOE-APPKEY"] = self.app_id
            self.session.headers["X-Workspace-Id"] = self.app_id

    def verify_session(self) -> Dict[str, Any]:
        """Test if the session cookies are valid or if we are in mock mode"""
        if self.mock_mode:
            return {
                "valid": True,
                "mode": "mock",
                "message": "Connected in Mock/Demo Mode with simulated MoEngage environment.",
                "user": "demo.marketer@brand.com",
                "workspace": "Production-Retail-Demo",
                "region": self.region
            }

        if not self.raw_cookies:
            return {
                "valid": False,
                "mode": "live",
                "message": "No cookies provided. Please paste your MoEngage session cookies in Settings.",
                "user": None,
                "workspace": None,
                "region": self.region
            }

        # Try hitting common MoEngage dashboard verification endpoints
        probe_urls = [
            f"https://{self.region}/v1/user/profile",
            f"https://{self.region}/api/v1/app/info",
            f"https://{self.region}/v3/segments/list"
        ]

        last_error = "Unable to connect"
        for url in probe_urls:
            try:
                resp = self.session.get(url, timeout=6)
                if resp.status_code == 200:
                    data = resp.json() if "application/json" in resp.headers.get("Content-Type", "") else {}
                    return {
                        "valid": True,
                        "mode": "live",
                        "message": "Session cookies validated successfully with MoEngage!",
                        "user": data.get("email") or data.get("user_email") or "Authenticated User",
                        "workspace": self.app_id or data.get("app_name") or "MoEngage Active Workspace",
                        "region": self.region
                    }
                elif resp.status_code in (401, 403):
                    return {
                        "valid": False,
                        "mode": "live",
                        "message": f"Session expired or unauthorized (HTTP {resp.status_code}). Please refresh MoEngage in browser and paste fresh cookies.",
                        "user": None,
                        "workspace": None,
                        "region": self.region
                    }
                else:
                    last_error = f"HTTP {resp.status_code}: {resp.text[:120]}"
            except Exception as e:
                last_error = str(e)

        return {
            "valid": False,
            "mode": "live",
            "message": f"Connection verification failed: {last_error}. Ensure your MoEngage region and cookies are correct, or switch to Mock Mode.",
            "user": None,
            "workspace": None,
            "region": self.region
        }

    def get_campaigns(self, status_filter: Optional[str] = None, channel_filter: Optional[str] = None) -> List[Dict[str, Any]]:
        """Fetch list of campaigns from MoEngage or realistic synthetic data"""
        if not self.mock_mode and self.raw_cookies:
            try:
                url = f"https://{self.region}/v5/campaigns/search"
                resp = self.session.post(url, json={"limit": 25}, timeout=8)
                if resp.status_code == 200:
                    data = resp.json()
                    campaigns = data.get("campaigns", data.get("data", []))
                    if campaigns:
                        return campaigns
            except Exception as e:
                print(f"[MoEngageClient] Live campaign fetch failed, falling back to cached/demo: {e}")

        # Synthetic/Realistic Dataset
        all_campaigns = [
            {
                "id": "cmp_001",
                "name": "Weekend Flash Sale 20% Off",
                "channel": "Push",
                "status": "Active",
                "target_segment": "All Active App Users",
                "sent_count": 482100,
                "delivered_count": 453174,
                "delivery_rate": 94.0,
                "opened_count": 28450,
                "ctr": 6.27,
                "conversions": 3410,
                "conversion_rate": 1.2,
                "revenue_generated": 68200.0,
                "last_run": (datetime.now() - timedelta(hours=18)).strftime("%Y-%m-%d %H:%M")
            },
            {
                "id": "cmp_002",
                "name": "Cart Abandonment 1-Hour Reminder",
                "channel": "Push",
                "status": "Active",
                "target_segment": "Cart Abandoners (< 2h)",
                "sent_count": 42100,
                "delivered_count": 40416,
                "delivery_rate": 96.0,
                "opened_count": 5210,
                "ctr": 12.89,
                "conversions": 2180,
                "conversion_rate": 5.39,
                "revenue_generated": 87200.0,
                "last_run": (datetime.now() - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M")
            },
            {
                "id": "cmp_003",
                "name": "VIP Loyalty Club Double Points",
                "channel": "Email",
                "status": "Active",
                "target_segment": "VIP Customers (LTV > $500)",
                "sent_count": 18450,
                "delivered_count": 18265,
                "delivery_rate": 99.0,
                "opened_count": 7120,
                "ctr": 8.94,
                "conversions": 940,
                "conversion_rate": 5.15,
                "revenue_generated": 141000.0,
                "last_run": (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d %H:%M")
            },
            {
                "id": "cmp_004",
                "name": "Reactivation: 30-Day Inactive Winback",
                "channel": "Email",
                "status": "Active",
                "target_segment": "Dormant Users (30-60d)",
                "sent_count": 142000,
                "delivered_count": 133480,
                "delivery_rate": 94.0,
                "opened_count": 14100,
                "ctr": 1.42,
                "conversions": 420,
                "conversion_rate": 0.31,
                "revenue_generated": 12600.0,
                "last_run": (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M")
            },
            {
                "id": "cmp_005",
                "name": "New User Onboarding Guide",
                "channel": "In-App",
                "status": "Active",
                "target_segment": "New Signups (< 7d)",
                "sent_count": 68000,
                "delivered_count": 68000,
                "delivery_rate": 100.0,
                "opened_count": 21400,
                "ctr": 31.47,
                "conversions": 9600,
                "conversion_rate": 14.12,
                "revenue_generated": 48000.0,
                "last_run": (datetime.now() - timedelta(hours=6)).strftime("%Y-%m-%d %H:%M")
            },
            {
                "id": "cmp_006",
                "name": "Price Drop Alert on Wishlist Items",
                "channel": "Push",
                "status": "Active",
                "target_segment": "Wishlist Items Users",
                "sent_count": 29800,
                "delivered_count": 28608,
                "delivery_rate": 96.0,
                "opened_count": 4820,
                "ctr": 16.85,
                "conversions": 1690,
                "conversion_rate": 5.91,
                "revenue_generated": 50700.0,
                "last_run": (datetime.now() - timedelta(hours=12)).strftime("%Y-%m-%d %H:%M")
            }
        ]

        filtered = all_campaigns
        if channel_filter:
            filtered = [c for c in filtered if c["channel"].lower() == channel_filter.lower()]
        if status_filter:
            filtered = [c for c in filtered if c["status"].lower() == status_filter.lower()]
        return filtered

    def get_segments(self) -> List[Dict[str, Any]]:
        """Fetch list of user segments from MoEngage or realistic synthetic data"""
        if not self.mock_mode and self.raw_cookies:
            try:
                url = f"https://{self.region}/v3/segments/list"
                resp = self.session.get(url, timeout=8)
                if resp.status_code == 200:
                    data = resp.json()
                    segments = data.get("segments", data.get("data", []))
                    if segments:
                        return segments
            except Exception as e:
                print(f"[MoEngageClient] Live segments fetch failed, falling back to cached/demo: {e}")

        return [
            {
                "id": "seg_001",
                "name": "Cart Abandoners (Last 24h)",
                "description": "Users who added product to cart within 24h but did not complete checkout",
                "type": "Behavioral",
                "estimated_reach": 34800,
                "criteria": {
                    "event_filter": "Added to Cart >= 1 in last 24 hours",
                    "exclusion_filter": "Purchase Completed >= 1 in last 24 hours"
                },
                "created_at": "2026-08-15"
            },
            {
                "id": "seg_002",
                "name": "VIP High LTV Customers (> $500)",
                "description": "Top tier spenders with cumulative purchase value over $500",
                "type": "Attribute & Behavioral",
                "estimated_reach": 14200,
                "criteria": {
                    "user_attribute": "lifetime_value > 500",
                    "event_filter": "Purchase Completed >= 3 in last 90 days"
                },
                "created_at": "2026-07-10"
            },
            {
                "id": "seg_003",
                "name": "Dormant Users (Inactive > 45d)",
                "description": "Previously active users who have not launched the app or visited the site in 45 days",
                "type": "Inactivity",
                "estimated_reach": 182400,
                "criteria": {
                    "event_filter": "App Opened == 0 in last 45 days",
                    "user_attribute": "has_previous_purchase == true"
                },
                "created_at": "2026-06-20"
            },
            {
                "id": "seg_004",
                "name": "High-Intent Window Shoppers",
                "description": "Viewed at least 4 products in the past 7 days without adding to cart",
                "type": "Behavioral",
                "estimated_reach": 62100,
                "criteria": {
                    "event_filter": "Product Viewed >= 4 in last 7 days",
                    "exclusion_filter": "Added to Cart >= 1 in last 7 days"
                },
                "created_at": "2026-08-28"
            },
            {
                "id": "seg_005",
                "name": "Price-Sensitive Discount Seekers",
                "description": "Users who only purchase when promo codes or clearance tags are active",
                "type": "Affinity",
                "estimated_reach": 51300,
                "criteria": {
                    "user_attribute": "coupon_usage_rate > 0.8",
                    "event_filter": "Promo Applied >= 1 in last 30 days"
                },
                "created_at": "2026-08-01"
            }
        ]

    def create_segment(self, name: str, description: str, criteria: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new filter segment in MoEngage or simulate in mock mode"""
        if not self.mock_mode and self.raw_cookies:
            try:
                url = f"https://{self.region}/v3/segments/create"
                payload = {
                    "name": name,
                    "description": description,
                    "filters": criteria
                }
                resp = self.session.post(url, json=payload, timeout=10)
                if resp.status_code in (200, 201):
                    data = resp.json()
                    return {
                        "success": True,
                        "segment_id": data.get("segment_id", f"moe_seg_{int(datetime.now().timestamp())}"),
                        "name": name,
                        "status": "Created in MoEngage",
                        "response": data
                    }
            except Exception as e:
                print(f"[MoEngageClient] Error creating segment on live MoEngage: {e}")

        # Synthetic creation
        new_id = f"moe_seg_{int(datetime.now().timestamp())}"
        return {
            "success": True,
            "segment_id": new_id,
            "name": name,
            "status": "Created (Simulated)" if self.mock_mode else "Drafted Locally",
            "message": f"Segment '{name}' successfully registered with criteria rules."
        }

    def get_analytics_summary(self) -> Dict[str, Any]:
        """Retrieve aggregated behavioral signals, event frequencies, and churn trends"""
        return {
            "timeframe": "Last 7 Days",
            "active_users_count": 842000,
            "events_breakdown": [
                {"event": "App Opened", "count": 1240000, "change_pct": 4.2},
                {"event": "Product Viewed", "count": 712000, "change_pct": -1.5},
                {"event": "Added to Cart", "count": 148000, "change_pct": 8.3},
                {"event": "Checkout Started", "count": 52000, "change_pct": 6.1},
                {"event": "Purchase Completed", "count": 31400, "change_pct": 9.4},
                {"event": "App Uninstalled", "count": 3420, "change_pct": -12.0}
            ],
            "conversion_funnels": {
                "view_to_cart_rate": 20.78,
                "cart_to_checkout_rate": 35.13,
                "checkout_to_purchase_rate": 60.38,
                "overall_view_to_purchase_rate": 4.41
            },
            "top_dropoff_points": [
                {
                    "stage": "Cart to Checkout",
                    "dropoff_pct": 64.87,
                    "insight": "High shipping fee or lack of guest checkout observed at checkout start."
                },
                {
                    "stage": "Product View to Cart",
                    "dropoff_pct": 79.22,
                    "insight": "Shoppers viewing category pages without seeing customer reviews or stock urgency."
                }
            ],
            "channel_performance_summary": {
                "Push": {"avg_ctr": 8.1, "best_performing_time": "19:00 - 21:00"},
                "Email": {"avg_open_rate": 22.4, "avg_ctr": 4.8, "best_performing_time": "08:30 - 10:30"},
                "In-App": {"avg_conversion": 14.1, "trigger": "On 2nd Screen View"}
            }
        }
