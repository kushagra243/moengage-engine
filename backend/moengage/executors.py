"""
Approval-gated write executors (registered at app start).
validate(payload) → raises on bad input; preview(payload) → the exact redacted
request that will be sent; execute(payload) → performs the write.
"""
from __future__ import annotations
import json
from typing import Any, Dict

from ..approvals import register_executor
from ..database import get_db
from .client import MoEngageClient
from .session import SessionError


def _need(p: Dict[str, Any], *keys: str) -> None:
    missing = [k for k in keys if not p.get(k)]
    if missing:
        raise ValueError(f"missing fields: {', '.join(missing)}")


def _preview(c: MoEngageClient, api_name: str, cookie_role: str, body=None, path_vars=None, key_kind: str = "campaigns") -> Dict[str, Any]:
    if c.mock_mode:
        return {"mode": "mock", "note": "mock mode: nothing is sent to MoEngage", "payload": body}
    if c.api().has(key_kind):
        return {"mode": "live", **c.api().preview(api_name, body=body, path_vars=path_vars)}
    try:
        return {"mode": "live", "transport": "cookie", **c.session().preview(cookie_role, body=body, path_vars=path_vars)}
    except SessionError as e:
        return {"mode": "live", "transport": "none", "blocked": str(e), "payload": body,
                "unlock": f"Set the {key_kind} API key (documented path) or learn '{cookie_role}' from a HAR capture."}


# create_segment ──────────────────────────────────────────────────────────────
def _seg_validate(p):
    _need(p, "name", "criteria")
    if not isinstance(p["criteria"], dict):
        raise ValueError("criteria must be an object")
    if len(p["name"]) > 120:
        raise ValueError("name too long")


def _seg_preview(p):
    return _preview(MoEngageClient(), "segment_create", "segment_create", body={"name": p["name"], "description": p.get("description", ""), **p["criteria"]}, key_kind="segmentation")


def _seg_execute(p):
    res = MoEngageClient().create_segment(p["name"], p.get("description", ""), p["criteria"])
    conn = get_db()
    conn.execute("INSERT INTO segments (name, description, criteria_json, estimated_reach, moengage_segment_id, status) VALUES (?,?,?,?,?,?)",
                 (p["name"], p.get("description", ""), json.dumps(p["criteria"]), int(p.get("estimated_reach") or 0), res.get("segment_id"), "created"))
    conn.commit(); conn.close()
    return res


# create_campaign (draft only) ────────────────────────────────────────────────
CHANNELS = {"push": "PUSH", "email": "EMAIL", "in-app": "INAPP", "in_app": "INAPP", "inapp": "INAPP", "sms": "SMS", "whatsapp": "WHATSAPP", "cards": "CARDS"}


def _cmp_validate(p):
    _need(p, "name", "channel", "target_segment")
    if str(p["channel"]).lower() not in CHANNELS:
        raise ValueError(f"channel must be one of {sorted(CHANNELS)}")
    if not p.get("variants") and not p.get("content"):
        raise ValueError("provide variants[] or content{}")
    for v in p.get("variants") or []:
        if len(str(v.get("title", ""))) > 120 or len(str(v.get("body", ""))) > 1000:
            raise ValueError("variant title/body too long")
    goal = p.get("goal") or {}
    required = ["transition", "primary_kpi", "guardrail_metric", "control_group_pct", "measurement_window_days", "kill_criteria"]
    missing = [k for k in required if goal.get(k) in (None, "", [])]
    if missing:
        raise ValueError("campaign brief incomplete: goal." + ", goal.".join(missing) + " (every campaign needs a transition, KPI, guardrail, holdout, window and kill criteria)")
    try:
        if float(goal.get("control_group_pct")) < 5:
            raise ValueError("control_group_pct must be >= 5")
    except (TypeError, ValueError):
        raise ValueError("control_group_pct must be a number >= 5")


def _cmp_payload(p):
    return {"name": p["name"], "channel": CHANNELS[str(p["channel"]).lower()], "delivery_type": (p.get("schedule") or {}).get("delivery_type", "ONE_TIME"),
            "audience": {"segment_name": p["target_segment"]}, "content": p.get("content") or {"variants": p.get("variants")},
            "state": "DRAFT", "ttl_hours": p.get("ttl_hours"), "market_hook_id": p.get("market_hook_id"),
            "goal": p.get("goal"), "exclusions": p.get("exclusions") or [], "frequency_cap": p.get("frequency_cap"),
            "control_group_pct": (p.get("goal") or {}).get("control_group_pct"),
            "engine_note": "Created as a draft by moengage-engine after human approval; review in dashboard before publishing."}


def _cmp_preview(p):
    return _preview(MoEngageClient(), "campaign_create_v5", "campaign_create", body=_cmp_payload(p), key_kind="campaigns")


def _cmp_execute(p):
    return MoEngageClient().create_campaign_draft(_cmp_payload(p))


# create_flow (draft; dashboard-only in MoEngage) ─────────────────────────────
def _flow_validate(p):
    _need(p, "name", "entry_trigger", "steps")
    if not isinstance(p["steps"], list) or not p["steps"]:
        raise ValueError("steps must be a non-empty list")


def _flow_preview(p):
    c = MoEngageClient()
    if c.mock_mode:
        return {"mode": "mock", "payload": p}
    try:
        return {"mode": "live", "transport": "cookie", **c.session().preview("flow_create", body=p)}
    except SessionError as e:
        return {"mode": "live", "transport": "none", "blocked": str(e), "payload": p, "unlock": "Capture a flow 'save' in a HAR; MoEngage has no public create-flow API."}


def _flow_execute(p):
    return MoEngageClient().create_flow_draft(p)


# pause / resume campaign ─────────────────────────────────────────────────────
def _state_validate(p):
    _need(p, "campaign_id")


def _pause_preview(p):
    return _preview(MoEngageClient(), "campaign_status_v5", "campaign_pause", body={"status": "PAUSED"}, path_vars={"id": p["campaign_id"], "campaign_id": p["campaign_id"]})


def _resume_preview(p):
    return _preview(MoEngageClient(), "campaign_status_v5", "campaign_resume", body={"status": "ACTIVE"}, path_vars={"id": p["campaign_id"], "campaign_id": p["campaign_id"]})


def _pause_execute(p):
    return MoEngageClient().set_campaign_state(p["campaign_id"], "pause")


def _resume_execute(p):
    return MoEngageClient().set_campaign_state(p["campaign_id"], "resume")


# cohort sync (custom segment membership via public API) ──────────────────────
def _csu_validate(p):
    _need(p, "segment_name", "customer_ids")
    if not isinstance(p["customer_ids"], list) or not p["customer_ids"]:
        raise ValueError("customer_ids must be a non-empty list")
    if len(p["customer_ids"]) > 100000:
        raise ValueError("too many ids for one sync")


def _csu_preview(p):
    c = MoEngageClient()
    return _preview(c, "cohort_sync", "segment_create", body={"segment_name": p["segment_name"], "add": f"<{len(p['customer_ids'])} ids>", "remove": f"<{len(p.get('remove_ids') or [])} ids>"}, key_kind="segmentation")


def _csu_execute(p):
    return MoEngageClient().cohort_sync(p["segment_name"], p["customer_ids"], p.get("remove_ids"))


def register_all() -> None:
    register_executor("create_segment", _seg_execute, _seg_preview, _seg_validate)
    register_executor("create_campaign", _cmp_execute, _cmp_preview, _cmp_validate)
    register_executor("create_flow", _flow_execute, _flow_preview, _flow_validate)
    register_executor("pause_campaign", _pause_execute, _pause_preview, _state_validate)
    register_executor("resume_campaign", _resume_execute, _resume_preview, _state_validate)
    register_executor("custom_segment_upload", _csu_execute, _csu_preview, _csu_validate)
