"""Which settings may be written from outside (the API and the CLI share this list), and light normalisation."""
from __future__ import annotations
import re
from typing import Any, Dict, List, Tuple

ALLOWED_SETTING_PREFIXES = ("moengage_", "llm_", "market_", "schedule_", "refresh_", "analysis_", "taxonomy_", "autopilot_", "devagent_", "web3_", "competitor", "mock_mode",
                            "ma2_", "sop_", "sopqa_", "usd_inr", "telegram_", "slack_")


def allowed(key: str) -> bool:
    return str(key or "").startswith(ALLOWED_SETTING_PREFIXES)


def normalise(key: str, value: str) -> str:
    """The same checks the settings API applies: data centre digits, a *.moengage.com region host, an https model base url."""
    v = str(value)
    if key == "moengage_dc" and v:
        m = re.fullmatch(r"(?:api|dashboard)?-?0*(\d{1,3})(?:\.moengage\.com)?", v.strip(), re.I)
        if not m:
            raise ValueError("data centre must be digits, e.g. 03 (api-03 and dashboard-03 also accepted)")
        return m.group(1).zfill(2)
    if key == "moengage_region" and v and not (v.endswith(".moengage.com") and "/" not in v and " " not in v):
        raise ValueError("region must be a *.moengage.com host")
    if key == "llm_base_url" and not (v.startswith("https://") or v.startswith("http://127.0.0.1") or v.startswith("http://localhost")):
        raise ValueError("LLM base URL must be https:// (or a loopback http:// server)")
    return v


def save_plain(values: Dict[str, Any]) -> Dict[str, List[str]]:
    """Write settings without the web layer (CLI use): allowlist, normalise, keep an empty secret untouched, encrypt via set_setting."""
    from .database import set_setting
    from .security import is_secret_key
    from .roles import refuse_setting
    saved, rejected = [], []
    for k, v in values.items():
        if not allowed(k) or refuse_setting(k):
            rejected.append(k); continue
        if v is None or (is_secret_key(k) and str(v) == ""):
            continue
        set_setting(k, normalise(k, str(v)))
        saved.append(k)
    if any(k in ("llm_provider", "llm_model", "llm_api_key", "llm_base_url") for k in saved):
        try:
            from .llm.provider import reconcile_llm_settings
            for k, v in reconcile_llm_settings(set(saved)).items():
                saved.append(f"{k} → {v}")
        except Exception:
            pass
    return {"saved": saved, "rejected": rejected}
