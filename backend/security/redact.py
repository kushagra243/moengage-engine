"""
Redaction of secrets in free text (logs, tool outputs, error messages).
Applied to *everything* that leaves a trust boundary: log files, LLM prompts,
API error bodies.
"""
from __future__ import annotations
import logging
import re
from typing import Iterable

# Order matters: most specific first.
_PATTERNS = [
    # OpenRouter / OpenAI style keys
    (re.compile(r"sk-or-v1-[A-Za-z0-9]{20,}"), "sk-or-v1-***"),
    (re.compile(r"sk-[A-Za-z0-9_-]{20,}"), "sk-***"),
    # Anthropic keys
    (re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}"), "sk-ant-***"),
    # JWTs
    (re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"), "<jwt>"),
    # Fernet tokens (our own ciphertexts)
    (re.compile(r"gAAAA[A-Za-z0-9_=-]{40,}"), "<enc>"),
    # Authorization / Cookie header values
    (re.compile(r"(?i)(authorization\s*[:=]\s*)(bearer\s+|basic\s+)?[A-Za-z0-9._~+/=-]{8,}"), r"\1\2<redacted>"),
    (re.compile(r"(?i)(cookie\s*[:=]\s*)[^\r\n]{8,}"), r"\1<redacted>"),
    (re.compile(r"(?i)(set-cookie\s*[:=]\s*)[^\r\n]{8,}"), r"\1<redacted>"),
    # Common session cookie names inside cookie strings
    (re.compile(r"(?i)\b(sessionid|session_id|csrftoken|csrf_token|jwt|token|auth_token|access_token|refresh_token|moe_session|_moe[a-z_]*)=([^;&\s]{6,})"), r"\1=<redacted>"),
    # key=... query params
    (re.compile(r"(?i)([?&](api_key|apikey|key|token|secret|password)=)[^&\s]{6,}"), r"\1<redacted>"),
    # Basic auth in URLs
    (re.compile(r"(https?://)[^/\s:@]+:[^/\s@]+@"), r"\1<redacted>@"),
]

_EXTRA: list[str] = []  # exact secret values registered at runtime


def register_secret_value(value: str) -> None:
    """Register a literal secret so it is scrubbed wherever it appears.
    Cookie strings / JSON blobs also register each individual value."""
    if not value or len(value) < 6:
        return
    if value not in _EXTRA:
        _EXTRA.append(value)
    leaves = []
    v = value.strip()
    if v.startswith(("{", "[")):
        try:
            import json
            def walk(o):
                if isinstance(o, dict):
                    for x in o.values():
                        walk(x)
                elif isinstance(o, list):
                    for x in o:
                        walk(x)
                elif isinstance(o, str):
                    leaves.append(o)
            walk(json.loads(v))
        except Exception:
            pass
    elif ";" in v and "=" in v:
        for part in v.split(";"):
            if "=" in part:
                leaves.append(part.split("=", 1)[1].strip())
    for leaf in leaves:
        if len(leaf) >= 8 and leaf not in _EXTRA:
            _EXTRA.append(leaf)


def redact(text) -> str:
    if text is None:
        return ""
    s = text if isinstance(text, str) else str(text)
    for v in _EXTRA:
        if v in s:
            s = s.replace(v, "<redacted>")
    for pat, repl in _PATTERNS:
        s = pat.sub(repl, s)
    return s


class _RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if record.name == "uvicorn.access":
                # uvicorn's formatter unpacks record.args positionally; redact each arg instead
                if isinstance(record.args, tuple):
                    record.args = tuple(redact(a) if isinstance(a, str) else a for a in record.args)
                return True
            record.msg = redact(record.getMessage())
            record.args = ()
        except Exception:
            pass
        return True


def install_log_redaction(loggers: Iterable[str] = ("", "uvicorn", "uvicorn.access", "uvicorn.error", "moengage")) -> None:
    f = _RedactingFilter()
    for name in loggers:
        lg = logging.getLogger(name)
        if not any(isinstance(x, _RedactingFilter) for x in lg.filters):
            lg.addFilter(f)
        for h in lg.handlers:
            if not any(isinstance(x, _RedactingFilter) for x in h.filters):
                h.addFilter(f)
