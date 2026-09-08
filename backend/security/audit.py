"""
Append-only, hash-chained audit log for sensitive actions.
Each line: {"ts","event","detail","prev","hash"}; hash = sha256(prev + line body).
Nothing secret is ever written here (detail is redacted).
"""
from __future__ import annotations
import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from .redact import redact

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data")
AUDIT_FILE = os.path.join(DATA_DIR, "logs", "audit.jsonl")
_lock = threading.Lock()


def _redact_obj(o: Any) -> Any:
    """Redact string leaves individually so the JSON structure stays valid."""
    if isinstance(o, dict):
        return {str(k): _redact_obj(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_redact_obj(v) for v in o]
    if isinstance(o, str):
        return redact(o)
    if isinstance(o, (int, float, bool)) or o is None:
        return o
    return redact(str(o))


def _last_hash() -> str:
    try:
        with open(AUDIT_FILE, "rb") as f:
            last = b""
            for line in f:
                if line.strip():
                    last = line
            if last:
                return json.loads(last.decode()).get("hash", "")
    except FileNotFoundError:
        pass
    return "genesis"


def audit(event: str, detail: Optional[Dict[str, Any]] = None, actor: str = "system") -> Dict[str, Any]:
    os.makedirs(os.path.dirname(AUDIT_FILE), exist_ok=True)
    with _lock:
        prev = _last_hash()
        body = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "actor": actor,
            "event": event,
            "detail": _redact_obj(detail or {}),
            "prev": prev,
        }
        h = hashlib.sha256((prev + json.dumps(body, sort_keys=True, default=str)).encode()).hexdigest()
        body["hash"] = h
        with open(AUDIT_FILE, "a") as f:
            f.write(json.dumps(body, default=str) + "\n")
        try:
            os.chmod(AUDIT_FILE, 0o600)
        except Exception:
            pass
    return body


def verify_chain() -> Dict[str, Any]:
    prev = "genesis"
    n = 0
    try:
        with open(AUDIT_FILE) as f:
            for line in f:
                if not line.strip():
                    continue
                rec = json.loads(line)
                h = rec.pop("hash")
                expect = hashlib.sha256((prev + json.dumps(rec, sort_keys=True, default=str)).encode()).hexdigest()
                if h != expect or rec.get("prev") != prev:
                    return {"ok": False, "broken_at": n + 1, "entries": n}
                prev = h
                n += 1
    except FileNotFoundError:
        return {"ok": True, "entries": 0}
    return {"ok": True, "entries": n}


def tail(limit: int = 50):
    try:
        with open(AUDIT_FILE) as f:
            lines = [l for l in f if l.strip()]
        return [json.loads(l) for l in lines[-limit:]]
    except FileNotFoundError:
        return []
