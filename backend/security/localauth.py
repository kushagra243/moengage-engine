"""
Local API protection for a loopback-only service.

Threat: any web page open in the user's browser can fire requests at
http://127.0.0.1:8080 (CSRF / drive-by). CORS does not stop simple POSTs from
*reaching* the server. Mitigation: a random per-process token is injected into
index.html (same-origin pages can read it, cross-origin pages cannot) and
required as the X-Local-Token header on every /api/* request. We also reject
requests whose Host header is not loopback (DNS-rebinding defence).
"""
from __future__ import annotations
import secrets
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

import os

local_token = secrets.token_urlsafe(32)

# Loopback always. Extra hostnames (e.g. the Tailscale MagicDNS name or a Cloudflare
# Access hostname) come from MOE_ALLOWED_HOSTS="engine.tailnet.ts.net,clm.internal".
ALLOWED_HOST_PREFIXES = ("127.0.0.1", "localhost", "[::1]") + tuple(
    h.strip().lower() for h in os.environ.get("MOE_ALLOWED_HOSTS", "").split(",") if h.strip())
PUBLIC_PATHS = {"/api/health"}
# Identity headers set by identity-aware proxies. Only trusted when the TCP peer is
# loopback (i.e. the proxy runs on this host), never from an arbitrary client.
IDENTITY_HEADERS = ("tailscale-user-login", "cf-access-authenticated-user-email", "x-forwarded-user")


def request_actor(request: Request) -> str:
    """Who is operating: proxy-asserted identity when proxied locally, else 'user'."""
    try:
        peer = request.client.host if request.client else ""
    except Exception:
        peer = ""
    if peer in ("127.0.0.1", "::1", "localhost"):
        for h in IDENTITY_HEADERS:
            v = request.headers.get(h)
            if v:
                return v.strip()[:120]
    return "user"


class LocalTokenMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable):
        host = (request.headers.get("host") or "").lower()
        if not host.startswith(ALLOWED_HOST_PREFIXES):
            return JSONResponse({"detail": "forbidden host"}, status_code=403)

        path = request.url.path
        if path.startswith("/api/") and path not in PUBLIC_PATHS:
            tok = request.headers.get("x-local-token", "")
            if not tok or not secrets.compare_digest(tok, local_token):
                return JSONResponse({"detail": "missing or invalid local token"}, status_code=401)
            origin = request.headers.get("origin")
            if origin and not any(origin.startswith(f"http://{p}") or origin.startswith(f"https://{p}") for p in ALLOWED_HOST_PREFIXES):
                return JSONResponse({"detail": "cross-origin request blocked"}, status_code=403)
            request.state.actor = request_actor(request)

        resp = await call_next(request)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        resp.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        resp.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Cache-Control"] = "no-store"
        return resp
