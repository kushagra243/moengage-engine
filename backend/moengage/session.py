"""
Cookie-driven MoEngage dashboard session.

Accepts cookies in any of: raw Cookie header string, JSON object, EditThisCookie /
Cookie-Editor JSON array, Playwright storageState, Netscape cookie jar text.
Mirrors CSRF cookies into their conventional headers. All traffic goes through
the 'moengage' GuardedSession (only *.moengage.com, https only, no env proxies).

Write gate: any non-GET call must pass write=True. The only code path that
passes write=True is an approval executor after a human clicked Approve.
"""
from __future__ import annotations
import http.cookiejar
import io
import json
import logging
import re
import threading
import time
import uuid
from collections import deque
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlencode

from ..database import get_setting, set_setting
from ..security import guarded_session, redact, audit
from . import registry

log = logging.getLogger("moengage.session")

CSRF_COOKIE_TO_HEADER = {
    "csrftoken": "X-CSRFToken", "csrf_token": "X-CSRFToken", "XSRF-TOKEN": "X-XSRF-TOKEN", "_csrf": "X-CSRF-Token",
}
LOGIN_MARKERS = re.compile(r"(/login|signin|sign-in|session_expired|unauthorized|auth/)", re.I)
REGION_HOSTS = {
    "dashboard-01.moengage.com", "dashboard-02.moengage.com", "dashboard-03.moengage.com",
    "dashboard-04.moengage.com", "dashboard-05.moengage.com", "app.moengage.com",
}


class SessionError(RuntimeError):
    pass


class WriteBlocked(RuntimeError):
    pass


# ── cookie parsing ─────────────────────────────────────────────────────────────
def parse_cookies(raw: str) -> Dict[str, str]:
    raw = (raw or "").strip()
    if not raw:
        return {}
    if raw.startswith(("{", "[")):
        data = json.loads(raw)
        if isinstance(data, dict) and "cookies" in data:       # playwright storageState
            data = data["cookies"]
        if isinstance(data, list):                              # cookie-editor export
            return {str(c["name"]): str(c["value"]) for c in data if isinstance(c, dict) and c.get("name")}
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items()}
        raise SessionError("unrecognised cookie JSON")
    first = raw.splitlines()[0]
    if raw.startswith("# Netscape") or ("\t" in first and first.count("\t") >= 5):
        jar = http.cookiejar.MozillaCookieJar()
        buf = io.StringIO(raw if raw.startswith("#") else "# Netscape HTTP Cookie File\n" + raw)
        # MozillaCookieJar needs a filename; emulate via _really_load
        jar._really_load(buf, "<memory>", ignore_discard=True, ignore_expires=True)  # type: ignore[attr-defined]
        return {c.name: c.value for c in jar}
    # "Cookie: a=b; c=d" or "a=b; c=d"
    if raw.lower().startswith("cookie:"):
        raw = raw.split(":", 1)[1]
    out: Dict[str, str] = {}
    for part in raw.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, _, v = part.partition("=")
        out[k.strip()] = v.strip()
    return out


def parse_credentials(raw: str) -> Dict[str, Any]:
    """
    Accepts anything copied from DevTools: a Cookie header, a whole 'Request Headers'
    block, a JSON with bearer/refresh_token (the dashboard's localStorage userData),
    or a cookie-editor export. Returns {cookies, access_token, refresh_token, app_key}.
    """
    raw = (raw or "").strip()
    out: Dict[str, Any] = {"cookies": {}, "access_token": "", "refresh_token": "", "app_key": ""}
    if not raw:
        return out
    if raw.startswith(("{", "[")):
        try:
            data = json.loads(raw)
        except Exception:
            data = None
        if isinstance(data, dict) and any(k in data for k in ("bearer", "refresh_token", "accessToken", "access_token")):
            out["access_token"] = str(data.get("bearer") or data.get("accessToken") or data.get("access_token") or "")
            out["refresh_token"] = str(data.get("refresh_token") or data.get("refreshToken") or "")
            out["app_key"] = str(data.get("app_key") or data.get("appKey") or "")
            if isinstance(data.get("cookies"), (dict, list)):
                out["cookies"] = parse_cookies(json.dumps(data["cookies"]))
            return out
        out["cookies"] = parse_cookies(raw)
        return out
    # header block?
    lines = [l for l in raw.splitlines() if l.strip()]
    hdr_like = sum(1 for l in lines if re.match(r"^\s*:?[A-Za-z0-9-]+:\s*\S", l)) >= 2
    if hdr_like:
        for l in lines:
            m = re.match(r"^\s*:?([A-Za-z0-9-]+):\s*(.*)$", l)
            if not m:
                continue
            k, v = m.group(1).lower(), m.group(2).strip()
            if k == "cookie":
                out["cookies"].update(parse_cookies(v))
            elif k == "authorization" and v.lower().startswith("bearer "):
                out["access_token"] = v[7:].strip()
            elif k == "refreshtoken":
                out["refresh_token"] = v
            elif k == "moe-appkey":
                out["app_key"] = v
        return out
    out["cookies"] = parse_cookies(raw)
    for k, v in list(out["cookies"].items()):
        if k.lower() in ("accesstoken", "bearer", "access_token") and v.startswith("eyJ"):
            out["access_token"] = out["access_token"] or v
        if k.lower() in ("refresh_token", "refreshtoken"):
            out["refresh_token"] = out["refresh_token"] or v
    return out


def cookie_summary(cookies: Dict[str, str]) -> Dict[str, Any]:
    """Names only. Never values."""
    names = sorted(cookies.keys())
    return {"count": len(names), "names": names[:40],
            "has_csrf": any(n in cookies for n in CSRF_COOKIE_TO_HEADER),
            "has_session_like": any(re.search(r"sess|sid|auth|token|jwt", n, re.I) for n in names)}


# ── session ────────────────────────────────────────────────────────────────────
class DashboardSession:
    _rate_lock = threading.Lock()
    _calls: deque = deque()

    def __init__(self, cookies: Optional[Dict[str, str]] = None, region: Optional[str] = None, app_id: Optional[str] = None, db_name: Optional[str] = None):
        self.region = (region or get_setting("moengage_region", "dashboard-01.moengage.com")).strip().replace("https://", "").rstrip("/")
        if self.region not in REGION_HOSTS and not self.region.endswith(".moengage.com"):
            raise SessionError(f"region host not allowed: {self.region}")
        self.base_url = f"https://{self.region}"
        self.app_id = (app_id if app_id is not None else get_setting("moengage_app_id", "")).strip()
        self.db_name = (db_name if db_name is not None else get_setting("moengage_db_name", "")).strip()
        self.cookies = cookies if cookies is not None else parse_cookies(get_setting("moengage_cookies", ""))
        self.access_token = get_setting("moengage_access_token", "").strip()
        self.refresh_token = get_setting("moengage_refresh_token", "").strip()
        self.http = guarded_session("moengage")
        for k, v in self.cookies.items():
            self.http.cookies.set(k, v, domain=".moengage.com")   # dashboard + any api host under moengage.com
        self.http.headers.update(self._headers())
        self.rpm = int(get_setting("moengage_max_rpm", "30") or 30)
        self.min_gap = float(get_setting("moengage_min_gap_s", "0.6") or 0.6)
        self._last = 0.0

    def _headers(self) -> Dict[str, str]:
        h = {
            "User-Agent": get_setting("moengage_user_agent", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Origin": self.base_url,
            "Referer": self.base_url + "/",
            "X-Requested-With": "XMLHttpRequest",
            "Sec-Fetch-Dest": "empty", "Sec-Fetch-Mode": "cors", "Sec-Fetch-Site": "same-origin",
        }
        for cname, hname in CSRF_COOKIE_TO_HEADER.items():
            if cname in self.cookies:
                h[hname] = self.cookies[cname]
        # The dashboard SPA authenticates with a bearer JWT + refresh token (see discover.py notes).
        tok = self.access_token
        if not tok:
            for k, v in self.cookies.items():
                if re.search(r"jwt|token|auth|bearer", k, re.I) and v.startswith("eyJ"):
                    tok = v; break
        if tok:
            h["Authorization"] = f"Bearer {tok}"
        if self.refresh_token:
            h["RefreshToken"] = self.refresh_token
        h["MoeTraceId"] = str(uuid.uuid4())
        h["page"] = "campaigns"
        # Headers learned from HAR: names always; values from settings if they were redacted
        reg = registry.get_registry()
        for name, value in (reg.get("extra_headers") or {}).items():
            if not value:
                value = get_setting("moengage_header_" + name.lower().replace("-", "_"), "")
            if value:
                h[name] = value
        if self.app_id:
            h.setdefault("MOE-APPKEY", self.app_id)
        return h

    @property
    def authenticated(self) -> bool:
        return bool(self.cookies or self.access_token)

    def refresh_session(self) -> bool:
        """GET /session/refresh?api=1 with the RefreshToken header (as the SPA does); stores new tokens encrypted."""
        if not self.refresh_token:
            return False
        try:
            r = self.http.get(self.base_url + "/session/refresh", params={"api": "1"}, timeout=15, allow_redirects=False)
            if r.status_code != 200:
                return False
            data = r.json().get("data") if "json" in r.headers.get("content-type", "") else None
            if not isinstance(data, dict):
                return False
            new_tok = data.get("bearer") or data.get("accessToken") or data.get("access_token")
            new_ref = data.get("refresh_token") or data.get("refreshToken")
            if new_tok:
                self.access_token = new_tok; set_setting("moengage_access_token", new_tok)
                self.http.headers["Authorization"] = f"Bearer {new_tok}"
            if new_ref:
                self.refresh_token = new_ref; set_setting("moengage_refresh_token", new_ref)
                self.http.headers["RefreshToken"] = new_ref
            audit("moengage.session_refreshed", {"rotated_refresh": bool(new_ref)}, actor="system")
            return bool(new_tok)
        except Exception as e:
            log.info("refresh failed: %s", redact(str(e)))
            return False

    # ── request building (used by previews) ─────────────────────────────
    def build(self, role: str, params: Optional[Dict[str, Any]] = None, body: Optional[Dict[str, Any]] = None, path_vars: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        ep = registry.resolve(role)
        if not ep:
            raise SessionError(f"endpoint for role '{role}' is not known yet — import a HAR capture or run verification")
        path = ep["path"]
        vars_ = {"app_id": self.app_id, "db_name": self.db_name, **(path_vars or {})}
        for k, v in vars_.items():
            if v:
                path = path.replace("{" + k + "}", str(v))
        if "{" in path:
            raise SessionError(f"unresolved path variable in {path}; set moengage_app_id / moengage_db_name in Settings")
        merged_params = {**(ep.get("params") or {}), **(params or {})}
        for k, v in list(merged_params.items()):
            if isinstance(v, str) and "{" in v:
                for var, val in vars_.items():
                    if val:
                        v = v.replace("{" + var + "}", str(val))
                merged_params[k] = v
        merged_body = None
        if ep["method"].upper() != "GET":
            merged_body = {**(ep.get("body_template") or {}), **(body or {})}
        return {"role": role, "method": ep["method"].upper(), "url": self.base_url + path, "params": merged_params, "json": merged_body,
                "headers_names": sorted(self.http.headers.keys()), "endpoint_status": ep.get("status")}

    def preview(self, role: str, **kw) -> Dict[str, Any]:
        """Redacted description of the exact request that would be sent."""
        r = self.build(role, **kw)
        return {"method": r["method"], "url": r["url"] + (("?" + urlencode(r["params"])) if r["params"] else ""),
                "json": r["json"], "headers": r["headers_names"], "endpoint_status": r["endpoint_status"],
                "cookies": cookie_summary(self.cookies)["count"]}

    # ── execution ───────────────────────────────────────────────────────
    def _pace(self) -> None:
        with DashboardSession._rate_lock:
            now = time.time()
            while DashboardSession._calls and now - DashboardSession._calls[0] > 60:
                DashboardSession._calls.popleft()
            if len(DashboardSession._calls) >= self.rpm:
                time.sleep(max(0.0, 60 - (now - DashboardSession._calls[0])))
            gap = now - self._last
            if gap < self.min_gap:
                time.sleep(self.min_gap - gap)
            DashboardSession._calls.append(time.time())
            self._last = time.time()

    def request_raw(self, method: str, url: str, params=None, json_body=None, write: bool = False, timeout: float = 30.0):
        if method.upper() != "GET" and not write:
            raise WriteBlocked(f"{method} {url} blocked: writes require an approved proposal")
        if not (self.cookies or self.access_token):
            raise SessionError("no MoEngage session credentials configured (cookies and/or bearer token)")
        params = dict(params or {})
        params.setdefault("api", "1")          # the dashboard marks XHR calls this way
        self._pace()
        self.http.headers["MoeTraceId"] = str(uuid.uuid4())
        resp = self.http.request(method.upper(), url, params=params, json=json_body, timeout=timeout, allow_redirects=False)
        if resp.status_code == 401 and self.refresh_token and not getattr(self, "_refreshing", False):
            self._refreshing = True
            try:
                if self.refresh_session():
                    resp = self.http.request(method.upper(), url, params=params, json=json_body, timeout=timeout, allow_redirects=False)
            finally:
                self._refreshing = False
        if resp.status_code in (401, 403) or (resp.is_redirect and LOGIN_MARKERS.search(resp.headers.get("location", ""))):
            raise SessionError(f"session rejected (HTTP {resp.status_code}); paste fresh dashboard credentials (Authorization/RefreshToken headers or cookies)")
        ctype = resp.headers.get("content-type", "")
        if "text/html" in ctype and LOGIN_MARKERS.search(resp.text[:4000] or ""):
            raise SessionError("session expired (login page returned); refresh cookies")
        return resp

    def call(self, role: str, params=None, body=None, path_vars=None, write: bool = False) -> Tuple[int, Any]:
        r = self.build(role, params=params, body=body, path_vars=path_vars)
        if write:
            audit("moengage.write", {"role": role, "url": redact(r["url"])}, actor="executor")
        resp = self.request_raw(r["method"], r["url"], params=r["params"], json_body=r["json"], write=write)
        try:
            data = resp.json()
        except ValueError:
            data = {"_raw": redact(resp.text[:2000]), "_content_type": resp.headers.get("content-type", "")}
        return resp.status_code, data

    def probe_get(self, path: str, params=None, timeout: float = 15.0):
        """Read-only probe of a candidate path (verification only)."""
        return self.request_raw("GET", self.base_url + path, params=params, write=False, timeout=timeout)
