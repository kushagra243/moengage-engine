"""
Endpoint registry with provenance.

Every dashboard call is addressed by *role* (e.g. "campaign_list"), never by a
hard-coded URL. Each role carries:
  status      documented | candidate | learned | verified | failed | unknown
  method/path the resolved call (None until learned or verified)
  candidates  paths to probe read-only during verification (from prior repos,
              docs, or common patterns) — never used for writes
  auth        cookie | basic
Learned entries (from HAR) overlay defaults; verification results overlay both.
Nothing here ever stores header *values* that look like credentials.
"""
from __future__ import annotations
import json
import os
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(HERE)), "data")
DEFAULT_PATH = os.path.join(HERE, "endpoints.default.json")
LEARNED_PATH = os.path.join(DATA_DIR, "endpoints.learned.json")
VERIFIED_PATH = os.path.join(DATA_DIR, "endpoints.verified.json")
_lock = threading.Lock()

READ_ROLES = ["whoami", "app_info", "campaign_list", "campaign_detail", "campaign_stats", "segment_list", "segment_detail",
              "segment_estimate", "flow_list", "flow_detail", "user_attributes", "events", "event_attributes", "analytics_overview"]
WRITE_ROLES = ["segment_create", "segment_update", "campaign_create", "campaign_pause", "campaign_resume", "flow_create"]


def _load(path: str) -> Dict[str, Any]:
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception:
        return {}


def _save(path: str, data: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def get_registry() -> Dict[str, Any]:
    """Merged view: defaults <- learned <- verified."""
    base = _load(DEFAULT_PATH)
    roles: Dict[str, Any] = {k: dict(v) for k, v in (base.get("dashboard") or {}).items()}
    learned = _load(LEARNED_PATH)
    for role, ep in (learned.get("endpoints") or {}).items():
        cur = roles.setdefault(role, {"auth": "cookie", "candidates": []})
        cur.update({k: v for k, v in ep.items() if k != "status"})
        cur["status"] = "learned"
        cur["learned_at"] = learned.get("learned_at")
    verified = _load(VERIFIED_PATH)
    for role, v in (verified.get("roles") or {}).items():
        cur = roles.setdefault(role, {"auth": "cookie", "candidates": []})
        if v.get("ok"):
            cur["status"] = "verified"
            if v.get("method"):
                cur["method"] = v["method"]
            if v.get("path"):
                cur["path"] = v["path"]
        else:
            if cur.get("status") not in ("learned",):
                cur["status"] = "failed"
        cur["verification"] = v
    return {
        "public": base.get("public") or {},
        "dashboard": roles,
        "extra_headers": (learned.get("extra_headers") or {}),
        "notes": base.get("notes") or {},
    }


def resolve(role: str) -> Optional[Dict[str, Any]]:
    ep = get_registry()["dashboard"].get(role)
    if not ep or not ep.get("path") or not ep.get("method"):
        return None
    return ep


def is_usable(role: str) -> bool:
    ep = get_registry()["dashboard"].get(role) or {}
    return bool(ep.get("path")) and ep.get("status") in ("learned", "verified", "documented")


def save_learned(endpoints: Dict[str, Any], extra_headers: Dict[str, str], stats: Dict[str, Any]) -> None:
    with _lock:
        _save(LEARNED_PATH, {"learned_at": datetime.now(timezone.utc).isoformat(), "endpoints": endpoints,
                             "extra_headers": extra_headers, "stats": stats})


def record_verification(role: str, ok: bool, method: Optional[str], path: Optional[str], http_status: Optional[int], detail: str, sample_keys: Optional[List[str]] = None) -> None:
    with _lock:
        data = _load(VERIFIED_PATH)
        roles = data.setdefault("roles", {})
        roles[role] = {"ok": ok, "method": method, "path": path, "http_status": http_status, "detail": detail[:300],
                       "sample_keys": (sample_keys or [])[:40], "checked_at": datetime.now(timezone.utc).isoformat()}
        _save(VERIFIED_PATH, data)


def clear_verification() -> None:
    with _lock:
        _save(VERIFIED_PATH, {"roles": {}})


def registry_status() -> Dict[str, Any]:
    reg = get_registry()
    rows = []
    for role in READ_ROLES + WRITE_ROLES:
        ep = reg["dashboard"].get(role) or {}
        rows.append({
            "role": role, "kind": "write" if role in WRITE_ROLES else "read",
            "status": ep.get("status", "unknown"), "method": ep.get("method"), "path": ep.get("path"),
            "candidates": len(ep.get("candidates") or []),
            "verification": (ep.get("verification") or {}).get("detail"),
            "checked_at": (ep.get("verification") or {}).get("checked_at"),
        })
    usable_reads = sum(1 for r in rows if r["kind"] == "read" and r["status"] in ("learned", "verified"))
    usable_writes = sum(1 for r in rows if r["kind"] == "write" and r["status"] in ("learned", "verified"))
    return {"roles": rows, "usable_reads": usable_reads, "usable_writes": usable_writes,
            "public": {k: {"status": v.get("status"), "doc": v.get("doc")} for k, v in reg["public"].items()},
            "learned_file": os.path.exists(LEARNED_PATH), "verified_file": os.path.exists(VERIFIED_PATH)}
