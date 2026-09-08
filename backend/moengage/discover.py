"""
Endpoint discovery from the dashboard's own public JavaScript bundle.

The MoEngage dashboard is a React SPA whose chunks are served from
app-cdn.moengage.com without authentication. Those chunks contain the API
paths the dashboard calls. We fetch the shell page for the configured
region, read the webpack chunk map from the loader, fetch the feature chunks
(bounded), extract path literals, classify them into our roles with the same
rules as the HAR learner, and store them as *candidates*. Nothing is trusted
until `capture.verify` has probed it read-only with the user's cookies.

Only string literals leave the bundle; no code is executed. Everything goes
through the 'moengage' GuardedSession (*.moengage.com only).
"""
from __future__ import annotations
import json
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from ..security import guarded_session, redact, audit
from . import registry
from .capture import RULES

DISCOVERED_PATH = os.path.join(registry.DATA_DIR, "endpoints.discovered.json")
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36"
PATH_RE = re.compile(r'["`\'](/(?:v\d+(?:\.\d+)?|api|core-services|bapi|segment\w*|campaign\w*|flow\w*|journey\w*|user\w*|analytics\w*|auth\w*|dashboard\w*|stats\w*|app\w*|team\w*|account\w*|login\w*|session\w*)[A-Za-z0-9_/${}:.\-]{2,140})["`\']')
PREFIX_RE = re.compile(r'["`\'](/(?:v\d+(?:\.\d+)?|api|api/v\d+|core-services))/?["`\']')
RELEVANT = re.compile(r'api|service|campaign|segment|flow|journey|analytic|auth|login|core|common|util|http|request|shared|store|dashboard|home|stats|user|team|settings', re.I)
DEFAULT_PREFIXES = ["", "/v3", "/v4", "/api", "/api/v1", "/v1"]
_lock = threading.Lock()


def _load() -> Dict[str, Any]:
    try:
        with open(DISCOVERED_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def _save(d: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(DISCOVERED_PATH), exist_ok=True)
    tmp = DISCOVERED_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f, indent=2)
    os.replace(tmp, DISCOVERED_PATH)


def discovered() -> Dict[str, Any]:
    return _load()


OVERRIDES = [
    (r"getloggedinuserdata|/user/info/?$", "whoami"),
    (r"session/2fa|session/refresh|/logout", None),
    (r"segmentation/all-segments/custom-segments/?$", "segment_list"),
    (r"segmentation/create", "segment_create"),
    (r"/campaigns/all$", "campaign_list"),
    (r"/flows/all$", "flow_list"),
    (r"/campaigns?/info/", "campaign_detail"),
    (r"stats/performance_stats|stats-client/performance", "campaign_stats"),
    (r"custom-templates|custom-domain-campaigns", None),
]


def _classify_path(path: str) -> Optional[str]:
    """Method-agnostic role classification: explicit overrides, then the HAR learner's rules."""
    p = path.lower()
    for pat, role in OVERRIDES:
        if re.search(pat, p):
            return role
    for role, _method, patterns, _hints in RULES:
        if any(re.search(pat, p) for pat in patterns):
            return role
    return None


def _shell_scripts(session, region: str) -> Tuple[str, List[str]]:
    r = session.get(f"https://{region}/", headers={"User-Agent": UA, "Accept": "text/html,*/*"}, timeout=20)
    r.raise_for_status()
    html = r.text
    srcs = re.findall(r'<script[^>]+src="([^"]+)"', html)
    return html, [s for s in srcs if "moengage.com" in s and "app_react" in s]


def _chunk_map(main_js: str) -> Tuple[str, Dict[str, str]]:
    """Return (public_path, {chunk_name: hash}) from the webpack loader."""
    pub = re.search(r'\.p="(https://[a-z0-9.-]+\.moengage\.com/[^"]*)"', main_js)
    public_path = pub.group(1) if pub else "https://app-cdn.moengage.com/prod/app_react/"
    m = re.search(r'\.u=\w=>"static/js/"\+\w\+"\."\+(\{.*?\})\[\w\]\+"\.chunk\.js"', main_js, re.S)
    if not m:
        # older shape: ({names}[e]||e)+"."+{hashes}[e]
        m2 = re.search(r'\.u=\w=>"static/js/"\+\((\{.*?\})\[\w\]\|\|\w\)\+"\."\+(\{.*?\})\[\w\]\+"\.chunk\.js"', main_js, re.S)
        if not m2:
            return public_path, {}
        names = dict(re.findall(r'(\w+):"([^"]+)"', m2.group(1)))
        hashes = dict(re.findall(r'(\w+):"([a-f0-9]{16,})"', m2.group(2)))
        return public_path, {names.get(k, k): v for k, v in hashes.items()}
    pairs = re.findall(r'"?([A-Za-z0-9_.\-]+)"?:"([a-f0-9]{16,})"', m.group(1))
    return public_path, dict(pairs)


def discover(region: str, max_chunks: int = 90, max_bytes: int = 40 * 1024 * 1024, workers: int = 6) -> Dict[str, Any]:
    session = guarded_session("moengage")
    started = datetime.now(timezone.utc).isoformat()
    html, scripts = _shell_scripts(session, region)
    if not scripts:
        raise RuntimeError("dashboard shell did not reference an app_react bundle; the SPA layout may have changed")
    main_url = next((s for s in scripts if "/main." in s), scripts[0])
    main_js = session.get(main_url, headers={"User-Agent": UA}, timeout=30).text
    public_path, chunks = _chunk_map(main_js)
    names = [n for n in chunks if RELEVANT.search(n)] or list(chunks)
    names = names[:max_chunks]

    paths: Dict[str, int] = {}
    prefixes: Dict[str, int] = {}
    hosts: Dict[str, int] = {}
    per_chunk: Dict[str, List[str]] = {}
    total = {"bytes": 0, "fetched": 0}
    lock = threading.Lock()

    def scan(text: str, name: str) -> None:
        found = set(PATH_RE.findall(text))
        pre = set(PREFIX_RE.findall(text))
        hs = re.findall(r'[a-z0-9-]+\.moengage\.com', text)
        with lock:
            for p in found:
                paths[p] = paths.get(p, 0) + 1
            for p in pre:
                prefixes[p] = prefixes.get(p, 0) + 1
            for h in hs:
                hosts[h] = hosts.get(h, 0) + 1
            if found:
                per_chunk[name] = sorted(found)[:12]

    scan(main_js, "main")

    def fetch(name: str) -> None:
        if total["bytes"] >= max_bytes:
            return
        url = f"{public_path}static/js/{name}.{chunks[name]}.chunk.js"
        try:
            r = session.get(url, headers={"User-Agent": UA}, timeout=30)
        except Exception:
            return
        if r.status_code != 200:
            return
        with lock:
            total["bytes"] += len(r.content); total["fetched"] += 1
        scan(r.text, name)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(fetch, names))

    # classify → roles
    roles: Dict[str, List[Dict[str, Any]]] = {}
    unclassified: List[str] = []
    for p, c in sorted(paths.items(), key=lambda kv: (-kv[1], kv[0])):
        if "${" in p or "{" in p and "}" not in p:
            pass
        role = _classify_path(p)
        if role:
            roles.setdefault(role, []).append({"path": p, "seen": c})
        else:
            unclassified.append(p)
    ranked_prefixes = [p for p, _ in sorted(prefixes.items(), key=lambda kv: -kv[1])]
    out = {
        "discovered_at": started, "region": region, "public_path": public_path,
        "chunks_total": len(chunks), "chunks_fetched": total["fetched"], "bytes": total["bytes"],
        "paths_total": len(paths), "prefixes": ranked_prefixes[:8] or DEFAULT_PREFIXES,
        "hosts": dict(sorted(hosts.items(), key=lambda kv: -kv[1])[:15]),
        "roles": {r: v[:12] for r, v in roles.items()},
        "unclassified_sample": unclassified[:80],
        "per_chunk_sample": dict(list(per_chunk.items())[:12]),
    }
    with _lock:
        _save(out)
    audit("moengage.discover", {"region": region, "paths": len(paths), "roles": sorted(roles), "chunks": total["fetched"]}, actor="user")
    return out


def candidates_for(role: str, region: str) -> List[Dict[str, Any]]:
    """Discovered candidate requests for a role: every discovered path × plausible prefixes, same-host."""
    d = _load()
    if not d or d.get("region") not in (None, region):
        return []
    out: List[Dict[str, Any]] = []
    prefixes = [""] + [p for p in (d.get("prefixes") or []) if p] + [p for p in DEFAULT_PREFIXES if p]
    seen = set()
    for ent in d.get("roles", {}).get(role, []):
        p = ent["path"]
        if "${" in p:
            continue
        for pre in prefixes:
            if p.startswith(pre) and pre:
                full = p
            else:
                full = pre + p
            if full in seen:
                continue
            seen.add(full)
            out.append({"method": "GET", "path": full, "source": "bundle", "seen": ent.get("seen", 1)})
    return out[:40]
