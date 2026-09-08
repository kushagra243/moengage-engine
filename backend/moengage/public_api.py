"""
Documented MoEngage public APIs (server-to-server), per the official OpenAPI
specs in github.com/moengage/moengage-documentation.

Auth: HTTP Basic — username = Workspace ID (App ID), password = the feature
key (Data / Segmentation / Campaigns-Reports / Inform). MOE-APPKEY header =
Workspace ID where the spec requires it. Host: api-{dc}.moengage.com.

Everything routes through the 'moengage' GuardedSession. Writes are only
reachable from approval executors (write=True is audited).
"""
from __future__ import annotations
import base64
import json
import re
import time
from typing import Any, Dict, List, Optional

from ..database import get_setting
from ..security import guarded_session, redact, audit
from . import registry


class PublicAPIError(RuntimeError):
    pass


KEY_SETTINGS = {"data": "moengage_data_api_key", "segmentation": "moengage_segmentation_key", "campaigns": "moengage_campaign_key", "inform": "moengage_inform_key"}


def detect_dc() -> str:
    explicit = get_setting("moengage_dc", "").strip()
    if explicit:
        return explicit.zfill(2) if explicit.isdigit() and len(explicit) < 2 else explicit
    region = get_setting("moengage_region", "dashboard-01.moengage.com")
    m = re.search(r"-(\d{2,3})\.", region)
    return m.group(1) if m else "01"


class PublicAPI:
    def __init__(self):
        self.app_id = get_setting("moengage_app_id", "").strip()
        self.db_name = get_setting("moengage_db_name", "").strip()
        self.dc = detect_dc()
        self.keys = {k: get_setting(s, "").strip() for k, s in KEY_SETTINGS.items()}
        self.reg = registry.get_registry()["public"]
        self.http = guarded_session("moengage")
        self.host = f"https://api-{self.dc}.moengage.com"

    # ── configuration ────────────────────────────────────────────────────
    def configured(self) -> Dict[str, Any]:
        return {"app_id": bool(self.app_id), "db_name": bool(self.db_name), "dc": self.dc,
                "keys": {k: bool(v) for k, v in self.keys.items()}}

    def has(self, key_kind: str) -> bool:
        return bool(self.app_id and self.keys.get(key_kind))

    def _headers(self, ep: Dict[str, Any]) -> Dict[str, str]:
        secret = self.keys.get(ep.get("key", "data"), "")
        if not self.app_id or not secret:
            raise PublicAPIError(f"Workspace ID and the {ep.get('key')} API key must be set in Settings → Integration")
        tok = base64.b64encode(f"{self.app_id}:{secret}".encode()).decode()
        h = {"Authorization": f"Basic {tok}", "Content-Type": "application/json", "Accept": "application/json"}
        if ep.get("appkey_header"):
            h["MOE-APPKEY"] = self.app_id
        if ep.get("dbname_header") and self.db_name:
            h["MOE-DBNAME"] = self.db_name
        return h

    def preview(self, name: str, body: Optional[Dict[str, Any]] = None, path_vars: Optional[Dict[str, str]] = None, params=None) -> Dict[str, Any]:
        ep = self.reg.get(name)
        if not ep:
            return {"error": f"unknown public endpoint {name}"}
        path = ep["path"].replace("{app_id}", self.app_id or "{app_id}")
        for k, v in (path_vars or {}).items():
            path = path.replace("{" + k + "}", str(v))
        return {"transport": "public_api", "method": ep["method"], "url": self.host + path, "params": params or {},
                "headers": ["Authorization: Basic <WorkspaceID:%s key>" % ep.get("key")] + (["MOE-APPKEY"] if ep.get("appkey_header") else []),
                "json": body, "doc": ep.get("doc"), "key_configured": self.has(ep.get("key", "data"))}

    def call(self, name: str, body: Optional[Dict[str, Any]] = None, path_vars: Optional[Dict[str, str]] = None, params=None, write: bool = False, timeout: float = 30.0) -> Dict[str, Any]:
        ep = self.reg.get(name)
        if not ep:
            raise PublicAPIError(f"public endpoint '{name}' not in registry")
        if ep["method"].upper() != "GET" and write is False and name not in ("campaigns_search", "campaign_stats", "flows_search", "campaigns_search_v5", "data_get_user", "test_connection"):
            raise PublicAPIError(f"{name} is a write; it must go through an approved proposal")
        path = ep["path"].replace("{app_id}", self.app_id)
        for k, v in (path_vars or {}).items():
            path = path.replace("{" + k + "}", str(v))
        url = self.host + path
        headers = self._headers(ep)
        if write:
            audit("moengage.public_write", {"endpoint": name, "url": url}, actor="executor")
        for attempt in range(3):
            r = self.http.request(ep["method"], url, headers=headers, json=body, params=params, timeout=timeout)
            if r.status_code == 429 and attempt < 2:
                wait = float(r.headers.get("x-ratelimit-reset") or r.headers.get("retry-after") or 2)
                time.sleep(min(max(wait, 1.0), 20.0)); continue
            break
        try:
            data = r.json()
        except ValueError:
            data = {"_raw": redact(r.text[:1000])}
        if r.status_code in (401, 403):
            raise PublicAPIError(f"{name}: auth rejected (HTTP {r.status_code}). Check Workspace ID, the {ep.get('key')} key and the data centre (api-{self.dc}).")
        if r.status_code >= 400:
            raise PublicAPIError(f"{name}: HTTP {r.status_code}: {redact(json.dumps(data)[:300])}")
        return {"status": r.status_code, "data": data, "endpoint": name, "doc": ep.get("doc"),
                "ratelimit_remaining": r.headers.get("x-ratelimit-remaining")}

    # ── reads ────────────────────────────────────────────────────────────
    def test_connection(self) -> Dict[str, Any]:
        return self.call("test_connection", body={}, write=False)

    def campaigns_search(self, page: int = 1, page_size: int = 50, filters: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        body = {"page": page, "page_size": page_size}
        if filters:
            body.update(filters)
        return self.call("campaigns_search", body=body)

    def campaign_stats(self, campaign_ids: List[str], start: Optional[str] = None, end: Optional[str] = None) -> Dict[str, Any]:
        body: Dict[str, Any] = {"campaign_ids": campaign_ids[:10]}
        if start:
            body["start_date"] = start
        if end:
            body["end_date"] = end
        return self.call("campaign_stats", body=body)

    def segments_list(self, name: Optional[str] = None) -> Dict[str, Any]:
        return self.call("segments_list", params={"name": name} if name else None)

    def segment_get(self, segment_id: str) -> Dict[str, Any]:
        return self.call("segment_get", path_vars={"id": segment_id})

    def flows_search(self, cursor: Optional[str] = None, limit: int = 50) -> Dict[str, Any]:
        body: Dict[str, Any] = {"limit": limit}
        if cursor:
            body["cursor"] = cursor
        return self.call("flows_search", body=body)

    def flow_get(self, flow_id: str) -> Dict[str, Any]:
        return self.call("flow_get", path_vars={"id": flow_id})

    def business_events_list(self) -> Dict[str, Any]:
        return self.call("business_events_list")

    def analytics_dashboards(self) -> Dict[str, Any]:
        return self.call("analytics_dashboards")

    # ── writes (executors only) ──────────────────────────────────────────
    def segment_create(self, name: str, description: str, filters: Dict[str, Any]) -> Dict[str, Any]:
        return self.call("segment_create", body={"name": name, "description": description, **filters}, write=True)

    def cohort_sync(self, segment_name: str, add_ids: List[str], remove_ids: Optional[List[str]] = None, id_type: str = "customer_id") -> Dict[str, Any]:
        body = {"segment_name": segment_name, "id_type": id_type, "add": add_ids, "remove": remove_ids or []}
        return self.call("cohort_sync", body=body, write=True)

    def campaign_create_draft(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self.call("campaign_create_v5", body=payload, write=True)

    def campaign_update(self, campaign_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
        return self.call("campaign_update_v5", body=patch, path_vars={"id": campaign_id}, write=True)

    def campaign_validate(self, campaign_id: str) -> Dict[str, Any]:
        return self.call("campaign_validate_v5", body={}, path_vars={"id": campaign_id}, write=True)

    def campaign_status(self, campaign_id: str, status: str) -> Dict[str, Any]:
        return self.call("campaign_status_v5", body={"status": status}, path_vars={"id": campaign_id}, write=True)

    def flow_status(self, flow_id: str, status: str) -> Dict[str, Any]:
        return self.call("flow_status", body={"status": status}, path_vars={"id": flow_id}, write=True)

    def business_event_trigger(self, event_name: str, attributes: Dict[str, Any]) -> Dict[str, Any]:
        return self.call("business_event_trigger", body={"event_name": event_name, "attributes": attributes}, write=True)

    def track_event(self, customer_id: str, action: str, attributes: Optional[Dict[str, Any]] = None, platform: str = "web") -> Dict[str, Any]:
        return self.call("data_track_event", body={"type": "event", "customer_id": customer_id, "actions": [{"action": action, "attributes": attributes or {}, "platform": platform}]}, write=True)

    def update_user(self, customer_id: str, attributes: Dict[str, Any]) -> Dict[str, Any]:
        return self.call("data_update_user", body={"type": "customer", "customer_id": customer_id, "attributes": attributes}, write=True)

    def inform_send(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self.call("inform_send", body=payload, write=True)

    # ── probe: which keys work ────────────────────────────────────────────
    def probe(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"dc": self.dc, "host": self.host, "configured": self.configured(), "checks": {}}
        if not self.app_id:
            out["ok"] = False; out["detail"] = "Workspace ID missing"; return out
        checks = [("data", "test_connection", lambda: self.test_connection()),
                  ("segmentation", "segments_list", lambda: self.segments_list()),
                  ("campaigns", "campaigns_search", lambda: self.campaigns_search(page_size=1)),
                  ("campaigns", "flows_search", lambda: self.flows_search(limit=1))]
        any_ok = False
        for kind, name, fn in checks:
            if not self.keys.get(kind):
                out["checks"][name] = {"ok": None, "detail": f"{kind} key not set"}; continue
            try:
                r = fn()
                out["checks"][name] = {"ok": True, "http": r["status"], "keys": list(r["data"].keys())[:12] if isinstance(r["data"], dict) else "list"}
                any_ok = True
            except Exception as e:
                out["checks"][name] = {"ok": False, "detail": redact(str(e))[:200]}
        out["ok"] = any_ok
        return out
