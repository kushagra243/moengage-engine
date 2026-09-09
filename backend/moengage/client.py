"""
Facade used by the app, the agent tools and the scheduler.

Modes (mock_mode setting):
  mock → backend.moengage.mock, tagged _source="mock"
  live → two transports, tried in order per operation:
         1. public API  (documented; needs Workspace ID + feature key)   _source="live:api"
         2. cookie session over learned/verified dashboard endpoints      _source="live:cookie"
Live calls NEVER fall back to demo data: DataUnavailable carries the reason.
"""
from __future__ import annotations
import json
import logging
import threading
import time
from typing import Any, Dict, List, Optional

from ..database import get_setting
from ..security import redact
from . import mock, registry
from .public_api import PublicAPI, PublicAPIError
from .session import DashboardSession, SessionError, cookie_summary, parse_cookies

log = logging.getLogger("moengage.client")

# campaign-stats costs one API call per 10 campaigns (~12s for 60), and every tab that
# lists campaigns pays it. Stats move hourly at most, so serve them from a short cache.
_STATS_TTL = 600.0
_stats_cache: Dict[str, Any] = {"key": "", "at": 0.0, "map": {}}

# A full live campaign read is ~18 sequential API calls (18 pages + 6 stats batches, ~18s).
# Cache the assembled list and hold a lock across the fetch, so a dashboard load and a
# background poll arriving together share one fetch instead of racing two.
_LIST_TTL = 300.0
_list_cache: Dict[str, Any] = {"at": 0.0, "rows": []}
_list_lock = threading.Lock()


class DataUnavailable(RuntimeError):
    def __init__(self, role: str, reason: str):
        super().__init__(f"{role}: {reason}")
        self.role = role
        self.reason = reason


LIST_KEYS = ("campaigns", "segments", "flows", "data", "results", "items", "list", "records", "custom_segments", "response")


def _extract_list(data: Any, keys=LIST_KEYS) -> List[Dict[str, Any]]:
    if isinstance(data, list):
        return [d for d in data if isinstance(d, dict)]
    if isinstance(data, dict):
        for k in keys:
            v = data.get(k)
            if isinstance(v, list):
                return [d for d in v if isinstance(d, dict)]
            if isinstance(v, dict):
                inner = _extract_list(v, keys)
                if inner:
                    return inner
    return []


def _tag(rows: List[Dict[str, Any]], src: str) -> List[Dict[str, Any]]:
    for r in rows:
        r["_source"] = src
    return rows


def normalize_campaign(r: Dict[str, Any]) -> Dict[str, Any]:
    """
    core-services campaign rows nest their identity under basic_details and carry no
    metrics; flatten to the keys the rest of the app reads. Metrics stay absent rather
    than zero so audits can tell "no data" from "performed badly".
    """
    if "basic_details" not in r:
        return r
    bd = r.get("basic_details") or {}
    seg = r.get("segmentation_details") or {}
    names = [f.get("name") for f in ((seg.get("included_filters") or {}).get("filters") or []) if isinstance(f, dict) and f.get("name")]
    # Whitelist, don't copy: campaign_content carries full email HTML (~1.1MB across 150 rows),
    # which alone overflows an agent tool response before any metrics are reached.
    out: Dict[str, Any] = {"_source": r.get("_source", "")}
    out.update({
        "id": r.get("campaign_id") or r.get("id", ""),
        "name": bd.get("name") or r.get("campaign_id", ""),
        "channel": str(r.get("channel", "")).title(),
        "status": r.get("status", ""),
        "target_segment": names[0] if names else ("All Users" if seg.get("is_all_user_campaign") else ""),
        "last_run": r.get("sent_time") or r.get("created_at", ""),
        "delivery_type": r.get("campaign_delivery_type", ""),
        "tags": bd.get("tags") or [],
        "created_by": r.get("created_by", ""),
        "content_type": bd.get("content_type", ""),
        "conversion_goals": [g.get("name") for g in (r.get("conversion_goal_details") or {}).get("goals", []) if isinstance(g, dict) and g.get("name")],
        "is_all_user_campaign": bool(seg.get("is_all_user_campaign")),
        "segment_count": len(names),
        "segment_filters": [{k: f.get(k) for k in ("name", "filter_type", "operator", "value", "count", "date_filter") if k in f} for f in ((seg.get("included_filters") or {}).get("filters") or [])[:6] if isinstance(f, dict)],
        "content_preview": _content_preview(r.get("campaign_content") or r.get("content") or {}),
        "schedule": {k: r.get(k) for k in ("campaign_delivery_type", "scheduled_time", "sent_time", "periodic_details", "trigger_details", "time_to_live", "frequency_capping", "dnd") if r.get(k) is not None},
    })
    return out


def _content_preview(content: Any, limit: int = 320) -> Dict[str, Any]:
    """Small, safe summary of campaign content: subject/title/body text, CTA, personalisation tokens. Never the HTML."""
    import re as _re
    found: Dict[str, str] = {}
    def walk(o, depth=0):
        if depth > 6 or len(found) > 8:
            return
        if isinstance(o, dict):
            for k, v in o.items():
                kl = str(k).lower()
                if isinstance(v, str) and v.strip() and any(t in kl for t in ("subject", "title", "header", "body", "message", "text", "cta", "button", "preheader", "summary")) and "html" not in kl:
                    txt = _re.sub(r"<[^>]+>", " ", v); txt = _re.sub(r"\s+", " ", txt).strip()
                    if txt and kl not in found:
                        found[kl] = txt[:limit]
                else:
                    walk(v, depth + 1)
        elif isinstance(o, list):
            for v in o[:6]:
                walk(v, depth + 1)
    walk(content)
    blob = json.dumps(content, default=str)[:20000] if content else ""
    tokens = sorted(set(_re.findall(r"\{\{[^}]{2,60}\}\}", blob)))[:8]
    return {"fields": found, "personalisation_tokens": tokens, "has_html": "<html" in blob.lower() or "<table" in blob.lower()}


class MoEngageClient:
    def __init__(self):
        self.mock_mode = get_setting("mock_mode", "true").lower() == "true"
        self.region = get_setting("moengage_region", "dashboard-01.moengage.com")
        self._session: Optional[DashboardSession] = None
        self._api: Optional[PublicAPI] = None

    @property
    def mode(self) -> str:
        return "mock" if self.mock_mode else "live"

    def api(self) -> PublicAPI:
        if self._api is None:
            self._api = PublicAPI()
        return self._api

    def session(self) -> DashboardSession:
        if self._session is None:
            self._session = DashboardSession()
        return self._session

    def has_cookies(self) -> bool:
        """True when any dashboard credential is present (cookies and/or bearer token)."""
        return bool(parse_cookies(get_setting("moengage_cookies", "")) or get_setting("moengage_access_token", "").strip())

    # ── status ──────────────────────────────────────────────────────────
    def verify_session(self) -> Dict[str, Any]:
        if self.mock_mode:
            d = mock.whoami(); d["region"] = self.region
            d["message"] = "Mock/Demo mode: simulated workspace. Turn off mock mode in Settings to use your workspace."
            return d
        api = self.api()
        out: Dict[str, Any] = {"mode": "live", "region": self.region, "dc": api.dc, "transports": {}}
        # public API
        conf = api.configured()
        out["transports"]["public_api"] = {"configured": any(conf["keys"].values()) and conf["app_id"], "keys": conf["keys"], "app_id": conf["app_id"]}
        # cookies
        cookies = parse_cookies(get_setting("moengage_cookies", ""))
        st = registry.registry_status()
        out["transports"]["cookie"] = {"configured": bool(cookies), "cookies": cookie_summary(cookies) if cookies else None,
                                       "usable_reads": st["usable_reads"], "usable_writes": st["usable_writes"]}
        out["valid"] = bool(out["transports"]["public_api"]["configured"] or (cookies and st["usable_reads"] > 0))
        if out["valid"]:
            out["message"] = "Live mode. " + ("Public API keys configured. " if out["transports"]["public_api"]["configured"] else "") + \
                             (f"Cookie session with {st['usable_reads']} verified read endpoints." if cookies and st["usable_reads"] else ("Cookies loaded; no dashboard endpoint verified yet." if cookies else ""))
        elif cookies:
            out["valid"] = None
            out["message"] = f"Cookies loaded ({len(cookies)}). No verified dashboard endpoint yet: run Verify or import a HAR. Adding Workspace ID + API keys enables the documented APIs immediately."
        else:
            out["message"] = "Live mode but nothing configured. Add Workspace ID + API keys (stable) and/or session cookies (dashboard-only actions) in Settings → Integration."
        return out

    # ── generic read with transport preference ───────────────────────────
    def _read(self, role: str, api_fn=None, api_key_kind: Optional[str] = None, cookie_role: Optional[str] = None, cookie_params=None, cookie_path_vars=None, want_list: bool = True):
        errors: List[str] = []
        if api_fn and api_key_kind and self.api().has(api_key_kind):
            try:
                r = api_fn()
                data = r["data"]
                if want_list:
                    return _tag(_extract_list(data), "live:api"), "live:api"
                if isinstance(data, dict):
                    data["_source"] = "live:api"
                return data, "live:api"
            except PublicAPIError as e:
                errors.append(f"api: {e}")
            except Exception as e:
                errors.append(f"api: {redact(str(e))}")
        elif api_fn and api_key_kind:
            errors.append(f"api: {api_key_kind} key not configured")
        cookie_role = cookie_role or role
        if self.has_cookies() and registry.is_usable(cookie_role):
            try:
                code, data = self.session().call(cookie_role, params=cookie_params, path_vars=cookie_path_vars)
                if code >= 400:
                    errors.append(f"cookie: HTTP {code}")
                else:
                    if want_list:
                        return _tag(_extract_list(data), "live:cookie"), "live:cookie"
                    if isinstance(data, dict):
                        data["_source"] = "live:cookie"
                    return data, "live:cookie"
            except Exception as e:
                errors.append(f"cookie: {redact(str(e))}")
        elif self.has_cookies():
            errors.append(f"cookie: '{cookie_role}' not learned/verified (Integration → Verify / Import HAR)")
        else:
            errors.append("cookie: no session cookies")
        raise DataUnavailable(role, " | ".join(errors))

    # ── reads ───────────────────────────────────────────────────────────
    STATS_LIMIT = 250         # 10 ids per stats call, cached 10 min; active/recent campaigns first

    def _merge_stats(self, rows: List[Dict[str, Any]]) -> None:
        """Campaign search carries no performance data; fold in campaign-stats where available."""
        def _recent(r):
            st = str(r.get("status", "")).lower()
            return 0 if st in ("active", "running", "scheduled", "sent", "completed") else 1
        ordered = sorted([r for r in rows if r.get("id")], key=lambda r: (_recent(r), str(r.get("last_run") or "")), reverse=False)
        ordered.sort(key=lambda r: (_recent(r), -(len(str(r.get("last_run") or "")))))
        ids = [r["id"] for r in ordered[:self.STATS_LIMIT]]
        if not ids:
            return
        key = "|".join(sorted(ids))
        now = time.time()
        if _stats_cache["key"] == key and now - _stats_cache["at"] < _STATS_TTL:
            stats = _stats_cache["map"]
        else:
            try:
                stats = self.api().campaign_stats_map(ids)
            except Exception as e:                  # stats are additive; never fail the list read
                log.warning("campaign stats unavailable: %s", redact(str(e)))
                return
            _stats_cache.update({"key": key, "at": now, "map": stats})
        for r in rows:
            p = stats.get(r.get("id"))
            if not p:
                r["stats_missing"] = True
                continue
            r["stats_source"] = "live:api"
            r["stats_missing"] = False
            r["_stats_raw"] = {k: v for k, v in p.items() if not isinstance(v, (dict, list))}[:0] if False else {k: v for k, v in list(p.items())[:40] if not isinstance(v, (dict, list))}
            def first(*keys):
                for k in keys:
                    if p.get(k) is not None:
                        return p[k]
                return None
            mapping = {
                "sent_count": first("sent", "sent_count", "total_sent"),
                "attempted_count": first("attempted", "attempted_count"),
                "delivered_count": first("delivered", "delivered_count", "impressions", "impression", "received"),
                "opened_count": first("open", "opens", "opened", "unique_open"),
                "clicks": first("click", "clicks", "clicked", "unique_click"),
                "ctr": first("ctr", "click_rate", "click_through_rate"),
                "delivery_rate": first("delivery_rate", "delivered_rate"),
                "open_rate": first("open_rate"),
                "ctor": first("ctor", "click_to_open_rate"),
                "bounce_rate": first("bounce_rate"),
                "conversions": first("conversion", "conversions", "converted", "goal_conversions", "primary_conversion"),
                "conversion_rate": first("conversion_rate", "goal_conversion_rate", "primary_conversion_rate"),
                "revenue_generated": first("revenue", "total_revenue", "revenue_generated"),
                "unsubscribes": first("unsubscribe", "unsubscribes"),
                "uninstalls": first("uninstall", "uninstalls"),
            }
            for dst, val in mapping.items():
                if val is not None:
                    r[dst] = val
            # derive counts from rates when the API gives one but not the other
            try:
                sent = float(r.get("sent_count") or 0)
                if r.get("delivered_count") is None and sent and r.get("delivery_rate") is not None:
                    r["delivered_count"] = round(sent * float(r["delivery_rate"]) / 100.0)
                deliv = float(r.get("delivered_count") or 0)
                if r.get("clicks") is None and deliv and r.get("ctr") is not None:
                    r["clicks"] = round(deliv * float(r["ctr"]) / 100.0)
            except (TypeError, ValueError):
                pass
            r["_provenance"] = {"list": "core-services/v1/campaigns/search", "stats": "core-services/v1/campaign-stats (30d window)", "fetched_at": now}

    def get_campaigns(self, status_filter: Optional[str] = None, channel_filter: Optional[str] = None) -> List[Dict[str, Any]]:
        if self.mock_mode:
            rows = mock.campaigns()
        else:
            with _list_lock:                # collapses concurrent callers onto one fetch
                if time.time() - _list_cache["at"] < _LIST_TTL and _list_cache["rows"]:
                    rows = _list_cache["rows"]
                else:
                    rows, _ = self._read("campaign_list", lambda: self.api().campaigns_search_paged(), "campaigns")
                    rows = [normalize_campaign(r) for r in rows]
                    self._merge_stats(rows)
                    _list_cache.update({"at": time.time(), "rows": rows})
        rows = [dict(r) for r in rows]      # callers filter/mutate; never hand out the cached objects
        if status_filter:
            rows = [r for r in rows if str(r.get("status", r.get("state", ""))).lower() == status_filter.lower()]
        if channel_filter:
            rows = [r for r in rows if str(r.get("channel", r.get("channel_type", ""))).lower() == channel_filter.lower()]
        return rows

    def get_campaign_stats(self, campaign_id: str) -> Dict[str, Any]:
        if self.mock_mode:
            for c in mock.campaigns():
                if c["id"] == campaign_id:
                    return c
            raise DataUnavailable("campaign_stats", f"unknown mock campaign {campaign_id}")
        data, _ = self._read("campaign_stats", lambda: self.api().campaign_stats([campaign_id]), "campaigns",
                             cookie_path_vars={"id": campaign_id, "campaign_id": campaign_id}, want_list=False)
        return data

    def get_segments(self) -> List[Dict[str, Any]]:
        if self.mock_mode:
            return mock.segments()
        rows, _ = self._read("segment_list", lambda: self.api().segments_list(), "segmentation")
        return rows

    def get_flows(self) -> List[Dict[str, Any]]:
        if self.mock_mode:
            return mock.flows()
        rows, _ = self._read("flow_list", lambda: self.api().flows_search(), "campaigns")
        return rows

    def get_analytics_summary(self) -> Dict[str, Any]:
        if self.mock_mode:
            return mock.analytics()
        data, _ = self._read("analytics_overview", lambda: self.api().analytics_dashboards(), "campaigns", want_list=False)
        return data

    def estimate_segment(self, criteria: Dict[str, Any]) -> Dict[str, Any]:
        if self.mock_mode:
            return {"estimated_reach": 12000 + (abs(hash(json.dumps(criteria, sort_keys=True))) % 90000), "_source": "mock"}
        if self.has_cookies() and registry.is_usable("segment_estimate"):
            ep = registry.resolve("segment_estimate") or {}
            qkeys = ep.get("query_keys") or ["filters"]
            body = {k.split(".")[-1]: criteria for k in qkeys}
            code, data = self.session().call("segment_estimate", body=body, write=True)   # POST but read-only in effect
            if code >= 400:
                raise DataUnavailable("segment_estimate", f"HTTP {code}")
            return {"raw": data, "_source": "live:cookie"}
        raise DataUnavailable("segment_estimate", "no public API for reach estimate; capture the dashboard estimate call in a HAR")

    # ── writes (approval executors only) ─────────────────────────────────
    def create_segment(self, name: str, description: str, criteria: Dict[str, Any]) -> Dict[str, Any]:
        if self.mock_mode:
            return {"success": True, "segment_id": f"mock_seg_{abs(hash(name)) % 10**8}", "name": name, "status": "Created (Simulated)", "_source": "mock"}
        if self.api().has("segmentation"):
            r = self.api().segment_create(name, description, criteria)
            d = r["data"] if isinstance(r["data"], dict) else {}
            return {"success": True, "segment_id": d.get("id") or d.get("segment_id") or d.get("_id"), "name": name, "status": "Created in MoEngage (public API)", "response_keys": list(d.keys())[:20], "_source": "live:api"}
        if self.has_cookies() and registry.is_usable("segment_create"):
            ep = registry.resolve("segment_create") or {}
            qkeys = ep.get("query_keys") or []
            body: Dict[str, Any] = {"name": name, "description": description}
            if qkeys:
                for k in qkeys:
                    body[k.split(".")[-1]] = criteria
            else:
                body["filters"] = criteria
            code, data = self.session().call("segment_create", body=body, write=True)
            if code >= 400:
                raise DataUnavailable("segment_create", f"HTTP {code}: {redact(json.dumps(data)[:200])}")
            sid = (data.get("segment_id") or data.get("id") or data.get("_id")) if isinstance(data, dict) else None
            return {"success": True, "segment_id": sid, "name": name, "status": "Created in MoEngage (dashboard)", "_source": "live:cookie"}
        raise DataUnavailable("segment_create", "needs Segmentation API key (documented) or a learned dashboard segment_create endpoint")

    def create_campaign_draft(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if self.mock_mode:
            return {"success": True, "campaign_id": f"mock_cmp_{abs(hash(json.dumps(payload, sort_keys=True))) % 10**8}", "status": "Draft (Simulated)", "_source": "mock"}
        if self.api().has("campaigns"):
            r = self.api().campaign_create_draft(payload)
            d = r["data"] if isinstance(r["data"], dict) else {}
            return {"success": True, "campaign_id": d.get("campaign_id") or d.get("id"), "status": "Draft created (public API v5). Review and publish in the dashboard.", "response_keys": list(d.keys())[:20], "_source": "live:api"}
        if self.has_cookies() and registry.is_usable("campaign_create"):
            code, data = self.session().call("campaign_create", body=payload, write=True)
            if code >= 400:
                raise DataUnavailable("campaign_create", f"HTTP {code}: {redact(json.dumps(data)[:200])}")
            cid = (data.get("campaign_id") or data.get("id") or data.get("_id")) if isinstance(data, dict) else None
            return {"success": True, "campaign_id": cid, "status": "Draft created (dashboard)", "_source": "live:cookie"}
        raise DataUnavailable("campaign_create", "needs Campaigns API key (documented v5 draft) or a learned dashboard campaign_create endpoint")

    def create_flow_draft(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if self.mock_mode:
            return {"success": True, "flow_id": f"mock_flow_{abs(hash(json.dumps(payload, sort_keys=True))) % 10**8}", "status": "Draft (Simulated)", "_source": "mock"}
        if self.has_cookies() and registry.is_usable("flow_create"):
            code, data = self.session().call("flow_create", body=payload, write=True)
            if code >= 400:
                raise DataUnavailable("flow_create", f"HTTP {code}")
            return {"success": True, "flow_id": (data.get("flow_id") or data.get("id")) if isinstance(data, dict) else None, "status": "Flow draft created (dashboard)", "_source": "live:cookie"}
        raise DataUnavailable("flow_create", "MoEngage has no public API to create flows; capture a flow save in a HAR to enable the cookie path")

    def set_campaign_state(self, campaign_id: str, action: str) -> Dict[str, Any]:
        if self.mock_mode:
            return {"success": True, "campaign_id": campaign_id, "status": f"{action}d (Simulated)", "_source": "mock"}
        if self.api().has("campaigns"):
            r = self.api().campaign_status(campaign_id, {"pause": "PAUSED", "resume": "ACTIVE", "stop": "STOPPED"}.get(action, action.upper()))
            return {"success": True, "campaign_id": campaign_id, "status": f"{action} requested (public API)", "http": r["status"], "_source": "live:api"}
        role = "campaign_pause" if action == "pause" else "campaign_resume"
        if self.has_cookies() and registry.is_usable(role):
            code, data = self.session().call(role, path_vars={"id": campaign_id, "campaign_id": campaign_id}, write=True)
            if code >= 400:
                raise DataUnavailable(role, f"HTTP {code}")
            return {"success": True, "campaign_id": campaign_id, "status": f"{action}d (dashboard)", "_source": "live:cookie"}
        raise DataUnavailable(role, "needs Campaigns API key or a learned dashboard endpoint")

    def set_flow_state(self, flow_id: str, status: str) -> Dict[str, Any]:
        if self.mock_mode:
            return {"success": True, "flow_id": flow_id, "status": f"{status} (Simulated)", "_source": "mock"}
        if self.api().has("campaigns"):
            r = self.api().flow_status(flow_id, status)
            return {"success": True, "flow_id": flow_id, "status": status, "http": r["status"], "_source": "live:api"}
        raise DataUnavailable("flow_status", "needs Campaigns API key")

    def cohort_sync(self, segment_name: str, add_ids: List[str], remove_ids: Optional[List[str]] = None) -> Dict[str, Any]:
        if self.mock_mode:
            return {"success": True, "segment_name": segment_name, "added": len(add_ids), "removed": len(remove_ids or []), "status": "Synced (Simulated)", "_source": "mock"}
        if self.api().has("segmentation"):
            r = self.api().cohort_sync(segment_name, add_ids, remove_ids)
            return {"success": True, "segment_name": segment_name, "added": len(add_ids), "removed": len(remove_ids or []), "http": r["status"], "_source": "live:api"}
        raise DataUnavailable("cohort_sync", "needs Segmentation API key")
