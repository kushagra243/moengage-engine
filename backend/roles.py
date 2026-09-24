"""
Two roles for one codebase.

  operator  — the enterprise machine: holds the MoEngage keys, sees the workspace, runs the brain on the org's Claude
              credits, fires alerts, asks for approvals in Slack. It shares nothing outward except development
              knowledge: challenges (what it could not do) and telemetry (counts, latencies, spend — never a user,
              a campaign name or a piece of copy).
  builder   — a Claude Code machine: reads challenges and telemetry, changes the engine, ships. It has no business with
              workspace data, so by role it refuses to store any MoEngage credential or session, refuses test users
              and employee ids, and stays in practice mode. `./cli.py role builder` sets it; `doctor` shows it.
"""
from __future__ import annotations
from typing import Any, Dict, List

from .database import get_setting, set_setting

ROLES = ("operator", "builder")
BUILDER_REFUSES = ("moengage_data_api_key", "moengage_campaign_key", "moengage_segmentation_key", "moengage_inform_key", "moengage_api_key", "moengage_custom_segment_key",
                   "moengage_cookies", "moengage_access_token", "moengage_refresh_token", "moengage_test_users_secret", "mock_mode")


def role() -> str:
    r = (get_setting("engine_role", "operator") or "operator").strip().lower()
    return r if r in ROLES else "operator"


def is_builder() -> bool:
    return role() == "builder"


def set_role(r: str, actor: str = "user") -> Dict[str, Any]:
    from .security import audit
    if r not in ROLES:
        raise ValueError("role must be operator or builder")
    set_setting("engine_role", r)
    cleared: List[str] = []
    if r == "builder":                                   # a builder keeps no workspace credentials, whatever was there before
        for k in BUILDER_REFUSES:
            if k == "mock_mode":
                set_setting("mock_mode", "true"); continue
            if get_setting(k, ""):
                set_setting(k, ""); cleared.append(k)
        try:
            from .alerts2 import cohort
            import os
            for f in ("internal_users.json", "cohort.json"):
                p = os.path.join(cohort.data_dir(), f)
                if os.path.exists(p):
                    os.remove(p); cleared.append(f)
        except Exception:
            pass
    audit("engine.role", {"role": r, "cleared": cleared}, actor=actor)
    return {"role": r, "cleared": cleared}


def refuse_setting(key: str) -> str:
    """Why a builder may not store this setting; empty when allowed."""
    if is_builder() and key in BUILDER_REFUSES:
        return f"this machine is a builder: it never holds {key.replace('_', ' ')}; only the operator machine does"
    return ""
