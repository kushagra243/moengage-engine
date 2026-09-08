"""
Learn the real dashboard API from a HAR capture, then verify it read-only.

Workflow once you have dashboard access:
  1. Log in to MoEngage, open DevTools → Network, tick "Preserve log".
  2. Click through: campaigns list → open one campaign → its analytics; segments
     list → create a segment (estimate reach, save); flows list → open one flow;
     optionally start a campaign and save as draft.
  3. Right-click the request list → "Save all as HAR with content".
  4. Import via UI (Integration tab) or `python cli.py learn ~/Downloads/x.har`.

Only URLs, methods, header NAMES, query keys and JSON *structure* are kept.
Cookie / auth values are dropped; opaque header values are blanked and must be
re-supplied as settings (moengage_header_<name>) — they are then encrypted.
The HAR file itself is never copied into the repo or data dir.
"""
from __future__ import annotations
import json
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qsl, urlsplit

from ..security import audit, redact
from . import registry

SENSITIVE_HEADERS = {"cookie", "set-cookie", "authorization", "x-csrftoken", "x-xsrf-token", "x-csrf-token", "proxy-authorization"}
QUERY_KEYS = ("included_filters", "excluded_filters", "query", "filters", "segment_query", "filter", "conditions", "segmentation")

# role → (method, path regexes, hints found in body/response). First match wins; specific first.
RULES: List[Tuple[str, str, List[str], List[str]]] = [
    ("segment_estimate",  "POST", [r"segment.*(estimat|count|reach|preview|size)", r"(estimat|reach|count).*segment", r"/count/?$"], []),
    ("segment_update",    "POST", [r"segment[s]?/(update|edit)", r"segments?/[0-9a-f]{8,}/?$"], []),
    ("segment_update",    "PUT",  [r"segments?/[0-9a-f]{8,}", r"segment/(update|edit)"], []),
    ("segment_create",    "POST", [r"segment[s]?/(create|save|add|new)", r"/segments?/?$", r"custom[_-]?segments?/?$"], []),
    ("segment_detail",    "GET",  [r"segments?/[0-9a-f]{8,}/?$"], []),
    ("segment_list",      "GET",  [r"segment[s]?/(list|all|search)", r"/segments?/?$", r"segments\?"], []),
    ("segment_list",      "POST", [r"segment[s]?/(list|search|filter)"], []),
    ("campaign_stats",    "GET",  [r"campaign[s]?/[^/]+/(stats|analytics|report|performance|metrics)", r"(stats|analytics|report).*campaign"], []),
    ("campaign_stats",    "POST", [r"campaign[s]?/(stats|analytics|report|performance|metrics)", r"analytics.*campaign"], []),
    ("campaign_pause",    "POST", [r"campaign[s]?/[^/]+/(pause|stop|deactivate)", r"campaign.*pause"], []),
    ("campaign_resume",   "POST", [r"campaign[s]?/[^/]+/(resume|start|activate)"], []),
    ("campaign_create",   "POST", [r"campaign[s]?/(create|save|draft|add|new)", r"/campaigns?/?$"], []),
    ("campaign_create",   "PUT",  [r"campaign[s]?/(create|save|draft)"], []),
    ("campaign_detail",   "GET",  [r"campaigns?/[0-9a-f]{8,}/?$", r"campaign/(get|detail|info)"], []),
    ("campaign_list",     "GET",  [r"campaign[s]?/(list|all|search)", r"/campaigns?/?$", r"campaigns\?"], []),
    ("campaign_list",     "POST", [r"campaign[s]?/(list|search|filter)"], []),
    ("flow_create",       "POST", [r"(flow|journey)[s]?/(create|save|draft|add|new)", r"/(flows?|journeys?)/?$"], []),
    ("flow_detail",       "GET",  [r"(flows?|journeys?)/[0-9a-f]{8,}"], []),
    ("flow_list",         "GET",  [r"(flow|journey)[s]?/(list|all|search)", r"/(flows?|journeys?)/?$"], []),
    ("flow_list",         "POST", [r"(flow|journey)[s]?/(list|search|filter)"], []),
    ("event_attributes",  "GET",  [r"(action|event).*attribut"], []),
    ("user_attributes",   "GET",  [r"(user|profile|customer).*attribut", r"attribut.*(user|profile)", r"segment/attribut"], []),
    ("events",            "GET",  [r"(actions|events)(/list)?/?$", r"segment/(actions|events)", r"/events\?"], []),
    ("analytics_overview","GET",  [r"(analytics|dashboard|overview|kpi|summary)"], []),
    ("app_info",          "GET",  [r"(app|workspace)/(info|detail|settings|config)", r"/apps?/[0-9a-f]{8,}"], []),
    ("whoami",            "GET",  [r"(user/profile|/me/?$|whoami|session|account/info|user/info)"], []),
]

STATIC_EXT = re.compile(r"\.(js|css|png|jpg|jpeg|gif|svg|woff2?|ttf|ico|map|webp|mp4)(\?|$)", re.I)


def _path_of(url: str) -> str:
    p = urlsplit(url)
    return p.path or "/"


def _json(text: Optional[str]) -> Any:
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


def _structure(obj: Any, depth: int = 0) -> Any:
    """Shape only: keys and types, no values (except short enums)."""
    if depth > 4:
        return "…"
    if isinstance(obj, dict):
        return {k: _structure(v, depth + 1) for k, v in list(obj.items())[:40]}
    if isinstance(obj, list):
        return [_structure(obj[0], depth + 1)] if obj else []
    if isinstance(obj, bool):
        return "bool"
    if isinstance(obj, (int, float)):
        return "number"
    if isinstance(obj, str):
        return "string" if len(obj) > 24 or re.search(r"[A-Za-z0-9]{20,}", obj) else obj
    return type(obj).__name__


def _templatise(body: Any) -> Tuple[Any, List[str]]:
    if not isinstance(body, dict):
        return body, []
    removed: List[str] = []
    template: Dict[str, Any] = {}
    for k, v in body.items():
        if k in QUERY_KEYS:
            removed.append(k); continue
        if isinstance(v, dict):
            sub, sub_removed = _templatise(v)
            removed.extend(f"{k}.{r}" for r in sub_removed)
            template[k] = sub
        elif isinstance(v, str) and (len(v) > 64 or re.fullmatch(r"[A-Za-z0-9_\-\.=]{24,}", v)):
            template[k] = ""     # blank anything opaque / long (names, tokens, ids)
        else:
            template[k] = v
    return template, removed


def _value_is_safe(value: str, hints: Dict[str, str]) -> bool:
    if not value:
        return True
    if value in hints.values():
        return True
    if len(value) > 24:
        return False
    return not re.fullmatch(r"[A-Za-z0-9_\-\.]{16,}", value)


def _detect_path_vars(path: str, hints: Dict[str, str]) -> str:
    for var, value in hints.items():
        if value and value in path:
            path = path.replace(value, "{" + var + "}")
    # generic 24-hex ids (Mongo-style) that are not app/db → {id}
    path = re.sub(r"/[0-9a-f]{24}(?=/|$)", "/{id}", path)
    return path


def classify(entry: Dict[str, Any]) -> Optional[str]:
    req = entry.get("request", {})
    method, url = req.get("method", "GET").upper(), req.get("url", "")
    if "moengage" not in url or STATIC_EXT.search(url):
        return None
    if method == "OPTIONS":
        return None
    path = _path_of(url).lower()
    for role, want_method, patterns, hints in RULES:
        if method != want_method:
            continue
        if any(re.search(p, path) for p in patterns):
            return role
    return None


def learn(har_text: str, app_id: str = "", db_name: str = "") -> Dict[str, Any]:
    har = json.loads(har_text)
    entries = har.get("log", {}).get("entries", [])
    hints = {"app_id": app_id, "db_name": db_name}
    found: Dict[str, Dict[str, Any]] = {}
    hosts: Dict[str, int] = {}
    extra_headers: Dict[str, str] = {}
    unmatched: List[str] = []
    skipped = 0

    for entry in entries:
        req = entry.get("request", {})
        url = req.get("url", "")
        if "moengage" in url and not STATIC_EXT.search(url):
            hosts[urlsplit(url).hostname or ""] = hosts.get(urlsplit(url).hostname or "", 0) + 1
        role = classify(entry)
        if not role:
            if "moengage" in url and not STATIC_EXT.search(url) and req.get("method") != "OPTIONS":
                unmatched.append(f"{req.get('method')} {_detect_path_vars(_path_of(url), hints)}")
            skipped += 1
            continue
        status = (entry.get("response") or {}).get("status")
        if role in found and (found[role].get("learned_from", {}).get("status") or 0) < 400:
            continue   # keep first successful capture per role
        ep: Dict[str, Any] = {"method": req["method"].upper(), "path": _detect_path_vars(_path_of(url), hints), "auth": "cookie"}
        params = dict(parse_qsl(urlsplit(url).query))
        if params:
            clean = {}
            for k, v in params.items():
                for var, val in hints.items():
                    if val and v == val:
                        v = "{" + var + "}"
                clean[k] = "" if not _value_is_safe(v, hints) else v
            ep["params"] = clean
        body = _json((req.get("postData") or {}).get("text"))
        if body is not None:
            template, removed = _templatise(body)
            if template:
                ep["body_template"] = template
            if removed:
                ep["query_keys"] = removed
            ep["body_shape"] = _structure(body)
        resp = _json(((entry.get("response") or {}).get("content") or {}).get("text"))
        if resp is not None:
            ep["response_shape"] = _structure(resp)
        ep["learned_from"] = {"status": status, "host": urlsplit(url).hostname}
        found[role] = ep
        for h in req.get("headers", []):
            name, value = h.get("name", ""), h.get("value", "")
            ln = name.lower()
            if ln in SENSITIVE_HEADERS or not (ln.startswith("x-") or ln.startswith("moe")):
                continue
            if ln in ("x-requested-with",):
                continue
            keep = value if _value_is_safe(value, hints) else ""
            extra_headers.setdefault(name, keep)

    redacted = sorted(k for k, v in extra_headers.items() if v == "")
    stats = {"entries": len(entries), "matched": len(found), "ignored": skipped, "hosts": hosts,
             "unmatched_sample": sorted(set(unmatched))[:60], "redacted_headers": redacted}
    registry.save_learned(found, extra_headers, stats)
    audit("moengage.har_learned", {"roles": sorted(found), "redacted_headers": redacted, "entries": len(entries)}, actor="user")
    return {"endpoints": found, "extra_headers": extra_headers, "stats": stats,
            "next_steps": ([f"Set header value(s) in Settings → Integration: {', '.join(redacted)}"] if redacted else [])
                          + ["Run 'Verify endpoints' to confirm each learned read endpoint responds with your cookies."]}


# ── read-only verification against the live session ────────────────────────────
def verify(session, roles: Optional[List[str]] = None, try_candidates: bool = True) -> Dict[str, Any]:
    """
    For each READ role: call the resolved endpoint (learned/verified) or probe the
    candidate paths one by one with GET. Records the first path that returns a
    JSON 2xx. Write roles are never called; they are reported as 'requires HAR'
    unless learned. Only key names of responses are stored.
    """
    reg = registry.get_registry()["dashboard"]
    roles = roles or registry.READ_ROLES
    results: Dict[str, Any] = {}
    for role in roles:
        ep = reg.get(role) or {}
        tried: List[str] = []
        ok = False
        # 1) resolved path
        cands: List[Tuple[str, str, Dict[str, Any]]] = []
        if ep.get("path") and ep.get("method", "GET").upper() == "GET":
            cands.append((ep["method"], ep["path"], ep.get("params") or {}))
        if try_candidates:
            for c in ep.get("candidates") or []:
                if isinstance(c, str):
                    cands.append(("GET", c, {}))
                elif isinstance(c, dict) and c.get("method", "GET").upper() == "GET":
                    cands.append(("GET", c["path"], c.get("params") or {}))
        if not cands:
            registry.record_verification(role, False, None, None, None, "no GET path or candidates; import a HAR capture")
            results[role] = {"ok": False, "detail": "no candidates"}
            continue
        for method, path, params in cands:
            p = path
            for k, v in {"app_id": session.app_id, "db_name": session.db_name}.items():
                if v:
                    p = p.replace("{" + k + "}", v)
            if "{" in p:
                tried.append(f"{p} (unresolved var)"); continue
            try:
                resp = session.probe_get(p, params=params)
            except Exception as e:
                tried.append(f"{p} → {redact(str(e))[:80]}")
                if "session rejected" in str(e) or "expired" in str(e):
                    results[role] = {"ok": False, "detail": str(e)}
                    registry.record_verification(role, False, method, p, None, str(e))
                    break
                continue
            ctype = resp.headers.get("content-type", "")
            if 200 <= resp.status_code < 300 and "json" in ctype:
                try:
                    data = resp.json()
                except ValueError:
                    tried.append(f"{p} → {resp.status_code} non-json"); continue
                keys = list(data.keys())[:40] if isinstance(data, dict) else ["<list>"]
                registry.record_verification(role, True, method, path, resp.status_code, "ok", keys)
                results[role] = {"ok": True, "path": path, "status": resp.status_code, "keys": keys}
                ok = True
                break
            tried.append(f"{p} → {resp.status_code} {ctype.split(';')[0]}")
        if not ok and role not in results:
            registry.record_verification(role, False, None, None, None, "; ".join(tried)[:300])
            results[role] = {"ok": False, "tried": tried}
    audit("moengage.verify", {"ok": [r for r, v in results.items() if v.get("ok")], "failed": [r for r, v in results.items() if not v.get("ok")]}, actor="user")
    return {"results": results, "summary": registry.registry_status()}
