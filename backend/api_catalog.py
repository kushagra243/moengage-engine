"""
Local MoEngage API knowledge pack: every documented public API page and
OpenAPI operation (backend/knowledge/moengage-api, built by
tools/build_api_catalog.py from moengage.com/docs). Gives the agent and the
Claude Code skills a searchable reference — method, path, auth key, rate
limit, parameters and request schema — without any network call.
"""
from __future__ import annotations
import json
import os
import re
from functools import lru_cache
from typing import Any, Dict, List, Optional

PACK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "knowledge", "moengage-api")


@lru_cache(maxsize=1)
def catalog() -> Dict[str, Any]:
    p = os.path.join(PACK, "catalog.json")
    if not os.path.exists(p):
        return {"pages": [], "operations": [], "built_on": None, "missing": True}
    return json.load(open(p))


@lru_cache(maxsize=64)
def spec(name: str) -> Dict[str, Any]:
    p = os.path.join(PACK, "specs", f"{name}.json")
    return json.load(open(p)) if os.path.exists(p) else {}


def _norm(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "{}", path.strip().rstrip("/").lower())


def _matches(template: str, given: str) -> bool:
    """template '/v3/custom-segments/{id}' matches given '/v3/custom-segments/abc' (variables match one segment)."""
    t = template.strip().rstrip("/").lower(); g = given.strip().rstrip("/").lower().split("?")[0]
    rx = "^" + re.sub(r"\\\{[^}]+\\\}", r"[^/]+", re.escape(t)) + "$"
    return re.match(rx, g) is not None


def find_operation(method: str, path: str) -> Optional[Dict[str, Any]]:
    """Match a method+path against documented operations (path variables wildcarded; server prefix optional)."""
    m = method.upper()
    for o in catalog()["operations"]:
        if o["method"] != m:
            continue
        if _matches(o["full_path"], path) or _matches(o["path"], path):
            return o
    return None


# Endpoints that return per-user data (profiles, cards, experiences, preferences, archived messages, GDPR). They are documented
# and may be read-only, but the agent must never pull them into a prompt: real user data would leave the machine via the model.
PII_PATH = re.compile(r"(customers?/export|/customer\b|cards/fetch|experiences/(fetch|events|metadata)|user-preferences|opengdpr|archival/view|personalization/preview)", re.I)


def is_pii_endpoint(path: str) -> bool:
    return bool(PII_PATH.search(path or ""))


def read_safe(method: str, path: str) -> bool:
    o = find_operation(method, path)
    return bool(o and o.get("read_safe") and not is_pii_endpoint(o.get("full_path") or path))


def search(query: str, limit: int = 12) -> List[Dict[str, Any]]:
    words = [w for w in re.findall(r"[a-z0-9]+", (query or "").lower()) if len(w) > 1]
    if not words:
        return []
    out = []
    for o in catalog()["operations"]:
        hay = " ".join(str(o.get(k) or "") for k in ("title", "summary", "doc_summary", "path", "full_path", "operationId", "spec")).lower() + " " + " ".join(o.get("tags") or []).lower()
        score = sum(hay.count(w) * (3 if w in (o.get("path") or "").lower() else 1) for w in words)
        if score:
            out.append((score, o))
    out.sort(key=lambda t: -t[0])
    return [{**{k: o.get(k) for k in ("method", "full_path", "title", "summary", "spec", "key_kind", "rate_limit", "doc_url")}, "read_safe": bool(o.get("read_safe") and not is_pii_endpoint(o.get("full_path", ""))), "pii": is_pii_endpoint(o.get("full_path", ""))} for _, o in out[:limit]]


def _resolve(node: Any, sp: Dict[str, Any], depth: int = 0, seen: Optional[set] = None) -> Any:
    """Shallow $ref resolution + schema flattening (depth-limited) for a readable request/response shape."""
    seen = seen or set()
    if depth > 4:
        return "…"
    if isinstance(node, dict):
        if "$ref" in node:
            ref = node["$ref"]
            if ref in seen:
                return {"$ref": ref.split("/")[-1]}
            seen = seen | {ref}
            tgt: Any = sp
            for part in ref.lstrip("#/").split("/"):
                tgt = (tgt or {}).get(part) if isinstance(tgt, dict) else None
            return _resolve(tgt, sp, depth, seen) if tgt is not None else {"$ref": ref}
        t = node.get("type")
        if t == "object" or "properties" in node:
            props = node.get("properties") or {}
            req = set(node.get("required") or [])
            return {("%s*" % k if k in req else k): _resolve(v, sp, depth + 1, seen) for k, v in list(props.items())[:40]}
        if t == "array":
            return [_resolve(node.get("items") or {}, sp, depth + 1, seen)]
        if "oneOf" in node or "anyOf" in node:
            alts = node.get("oneOf") or node.get("anyOf")
            return {"oneOf": [_resolve(a, sp, depth + 1, seen) for a in alts[:4]]}
        desc = (node.get("description") or "").strip().replace("\n", " ")
        enum = node.get("enum")
        s = t or "any"
        if enum:
            s += " enum[" + ", ".join(str(e) for e in enum[:8]) + ("…" if len(enum) > 8 else "") + "]"
        if node.get("example") is not None:
            s += f" e.g. {json.dumps(node['example'])[:60]}"
        if desc:
            s += " — " + desc[:140]
        return s
    if isinstance(node, list):
        return [_resolve(x, sp, depth + 1, seen) for x in node[:5]]
    return node


def _deref(node: Any, sp: Dict[str, Any]) -> Any:
    """Raw $ref lookup (no flattening) for requestBody / parameter / response objects."""
    if isinstance(node, dict) and "$ref" in node:
        tgt: Any = sp
        for part in node["$ref"].lstrip("#/").split("/"):
            tgt = (tgt or {}).get(part) if isinstance(tgt, dict) else None
        return tgt if isinstance(tgt, dict) else {}
    return node


def operation_detail(method: str, path: str) -> Dict[str, Any]:
    o = find_operation(method, path)
    if not o:
        return {"error": f"no documented operation {method.upper()} {path}", "hint": "search with moengage_api_reference first"}
    sp = spec(o["spec"])
    item = ((sp.get("paths") or {}).get(o["path"]) or {})
    op = item.get(o["method"].lower()) or {}
    params = []
    for prm in (item.get("parameters") or []) + (op.get("parameters") or []):
        prm = _deref(prm, sp)
        if isinstance(prm, dict):
            params.append({"name": prm.get("name"), "in": prm.get("in"), "required": bool(prm.get("required")), "schema": _resolve(prm.get("schema") or {}, sp),
                           "description": (prm.get("description") or "")[:160]})
    body = None
    rb = op.get("requestBody")
    if rb:
        rb = _deref(rb, sp)
        content = (rb or {}).get("content") or {}
        for ctype, c in content.items():
            body = {"content_type": ctype, "schema": _resolve(c.get("schema") or {}, sp)}
            ex = c.get("example") or ((c.get("examples") or {}) and next(iter((c.get("examples") or {}).values()), {}).get("value"))
            if ex is not None:
                body["example"] = json.loads(json.dumps(ex, default=str)[:3000]) if isinstance(ex, (dict, list)) else str(ex)[:1000]
            break
    responses = {}
    for code, r in (op.get("responses") or {}).items():
        r = _deref(r, sp)
        responses[str(code)] = (r or {}).get("description", "")[:120] if isinstance(r, dict) else str(r)[:120]
    server = o.get("server") or "https://api-{dc}.moengage.com"
    return {"method": o["method"], "url": server.rstrip("/") + o["path"], "title": o.get("title") or op.get("summary"), "summary": (op.get("description") or o.get("doc_summary") or "")[:700],
            "auth": f"HTTP Basic (Workspace ID : {o['key_kind']} API key)" + (" + MOE-APPKEY header" if any(k.lower().startswith("appkey") or k.lower() == "moeappkey" for k in ((sp.get("components") or {}).get("securitySchemes") or {})) else ""),
            "key_kind": o["key_kind"], "read_safe": bool(o["read_safe"] and not is_pii_endpoint(o["full_path"])), "pii": is_pii_endpoint(o["full_path"]), "rate_limit": o.get("rate_limit"), "doc_url": o.get("doc_url"),
            "parameters": params[:30], "request_body": body, "responses": responses,
            "engine_note": ("per-user data: never fetched by the agent (would send user data to the model)" if is_pii_endpoint(o["full_path"]) else "read-safe: the agent may call it with moengage_api_read" if o["read_safe"] else "write: only through an approved proposal")}


def overview() -> Dict[str, Any]:
    c = catalog()
    by_spec: Dict[str, List[str]] = {}
    for o in c["operations"]:
        by_spec.setdefault(o["spec"], []).append(f"{o['method']} {o['full_path']}")
    return {"built_on": c.get("built_on"), "pages": len(c.get("pages", [])), "operations": len(c.get("operations", [])),
            "read_safe_operations": sum(1 for o in c.get("operations", []) if o.get("read_safe")), "specs": {k: len(v) for k, v in sorted(by_spec.items())},
            "auth": c.get("auth"), "mcp": c.get("mcp")}
