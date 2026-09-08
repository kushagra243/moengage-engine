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
from typing import Any, Dict, List, Optional

from ..database import get_setting
from ..security import redact
from . import mock, registry
from .public_api import PublicAPI, PublicAPIError
from .session import DashboardSession, SessionError, cookie_summary, parse_cookies


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
    def get_campaigns(self, status_filter: Optional[str] = None, channel_filter: Optional[str] = None) -> List[Dict[str, Any]]:
        if self.mock_mode:
            rows = mock.campaigns()
        else:
            rows, _ = self._read("campaign_list", lambda: self.api().campaigns_search(page_size=100), "campaigns")
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
