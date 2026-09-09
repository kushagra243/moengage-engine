"""
Security core for moengage-engine.

Design goals (all enforced in code, not by convention):
  * Secrets (MoEngage cookies, API keys, LLM keys) are encrypted at rest with a
    key held in the macOS Keychain (file fallback, mode 0600). They are never
    written to logs, never returned by the settings API, and never placed in an
    LLM prompt.
  * Every outbound HTTP call goes through a scoped GuardedSession with a host
    allowlist. The MoEngage scope carries cookies; the LLM and market scopes
    can never reach MoEngage hosts, so credentials cannot be exfiltrated by a
    prompt-injected tool call.
  * The local API binds to 127.0.0.1 and requires a per-process token that is
    only delivered to the page served from the same origin (CSRF / drive-by
    protection for a localhost service).
  * Sensitive actions are appended to a tamper-evident audit log.
"""
from .redact import redact, install_log_redaction
from .secrets import SecretStore, secret_store, is_secret_key, mask_secret
from .netguard import guarded_session, GuardedSession, NetworkPolicyError
from .audit import audit
from .localauth import local_token, LocalTokenMiddleware, request_actor

__all__ = [
    "redact", "install_log_redaction",
    "SecretStore", "secret_store", "is_secret_key", "mask_secret",
    "guarded_session", "GuardedSession", "NetworkPolicyError",
    "audit", "local_token", "LocalTokenMiddleware", "request_actor",
]
