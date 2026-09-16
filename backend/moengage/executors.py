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
    sched = p.get("schedule") or {}
    delivery = DELIVERY_TYPES.get(str(sched.get("type") or sched.get("delivery_type") or "one_time").lower(), "ONE_TIME")
    if delivery == "BUSINESS_EVENT_TRIGGERED" and not (sched.get("business_event") or sched.get("event")):
        raise ValueError("a business-event-triggered campaign needs schedule.business_event (the event the engine fires)")
    if not MoEngageClient().mock_mode and not _created_by():
        raise ValueError("MoEngage needs the dashboard email of the creator: set moengage_created_by in Engine → Settings")


DELIVERY_TYPES = {"business_event_triggered": "BUSINESS_EVENT_TRIGGERED", "business_event": "BUSINESS_EVENT_TRIGGERED", "event_triggered": "EVENT_TRIGGERED",
                  "event": "EVENT_TRIGGERED", "periodic": "PERIODIC", "recurring": "PERIODIC", "one_time": "ONE_TIME", "onetime": "ONE_TIME"}


def _created_by() -> str:
    from ..database import get_setting
    return (get_setting("moengage_created_by", "") or get_setting("moengage_user_email", "")).strip()


def _platforms() -> list:
    from ..database import get_setting
    raw = get_setting("moengage_push_platforms", "ANDROID,IOS") or "ANDROID,IOS"
    return [x.strip().upper() for x in raw.split(",") if x.strip()]


def _push_content(v: dict) -> dict:
    """One BASIC push template per platform. Title and message may be MoEngage placeholders (event attributes)."""
    basic = {"title": str(v.get("title", ""))[:120], "message": str(v.get("body", ""))[:1000]}
    link = v.get("deeplink") or v.get("cta_url")
    if link:
        basic["default_click_action"] = "DEEPLINKING"; basic["default_click_action_value"] = str(link)
    out = {}
    for plat in _platforms():
        if plat in ("ANDROID", "IOS"):
            out[plat.lower()] = {"template_type": "BASIC", "basic_details": dict(basic)}
        elif plat == "WEB":
            out["web"] = {"template_type": "BASIC", "basic_details": dict(basic)}
    return {"content": {"push": out or {"android": {"template_type": "BASIC", "basic_details": basic}}}}


def _segmentation(p: dict) -> dict:
    """The whole base, or a named custom segment. Exclusions stay in the brief: they are set on the segment in MoEngage."""
    seg = str(p.get("target_segment") or "").strip()
    if not seg or seg.upper().startswith("ALL"):
        return {"is_all_user_campaign": True, "send_campaign_to_opt_out_users": False}
    return {"included_filters": {"filter_operator": "and", "filters": [{"filter_type": "custom_segments", "name": seg}]},
            "send_campaign_to_opt_out_users": False}


def v5_campaign_payload(p: dict) -> dict:
    """The documented MoEngage v5 draft body (POST /v5/campaigns).

    The engine used to post its own brief shape here, which MoEngage cannot read: the draft never appeared. This builds
    the schema the API documents, including BUSINESS_EVENT_TRIGGERED campaigns, whose event name goes in basic_details.
    """
    from datetime import datetime, timedelta, timezone as _tz
    sched = p.get("schedule") or {}
    goal = p.get("goal") or {}
    delivery = DELIVERY_TYPES.get(str(sched.get("type") or sched.get("delivery_type") or "one_time").lower(), "ONE_TIME")
    channel = CHANNELS[str(p["channel"]).lower()]
    variants = p.get("variants") or []
    v = variants[0] if variants else (p.get("content") or {})
    basic = {"name": p["name"], "platforms": _platforms()} if channel == "PUSH" else {"name": p["name"]}
    if delivery == "BUSINESS_EVENT_TRIGGERED":
        ev = sched.get("business_event") or sched.get("event")
        if ev:
            basic["business_event"] = ev
    now = datetime.now(_tz.utc)
    days = 365 if goal.get("continuous") else int(goal.get("measurement_window_days") or 30)
    body = {"channel": channel, "campaign_delivery_type": delivery, "created_by": _created_by(),
            "basic_details": basic,
            "campaign_content": _push_content(v) if channel == "PUSH" else {"content": {"email": {"subject": v.get("title", ""), "html_content": v.get("body", "")}}},
            "segmentation_details": _segmentation(p),
            "scheduling_details": ({"delivery_type": "AT_FIXED_TIME", "start_time": now.isoformat(timespec="seconds"),
                                    "expiry_time": (now + timedelta(days=days)).isoformat(timespec="seconds")}
                                   if delivery in ("BUSINESS_EVENT_TRIGGERED", "EVENT_TRIGGERED") else {"delivery_type": "ASAP"})}
    cg = goal.get("control_group_pct")
    if cg:
        body["control_group_details"] = {"is_campaign_control_group_enabled": True, "campaign_control_group_percentage": int(float(cg))}
    dc: dict = {}
    if "engine cap" in str(p.get("frequency_cap") or "").lower() or str(sched.get("frequency_capping") or "").lower().startswith("leave"):
        dc["count_for_frequency_capping"] = True                     # MoEngage holds the per-user cap for this one
    if str(sched.get("frequency_capping") or "").lower().startswith("off") or str(p.get("frequency_cap") or "").lower().startswith("engine only"):
        dc["ignore_frequency_capping"] = True                        # the engine caps per user (the per-user pilot)
    if p.get("ttl_hours"):
        dc["max_time_to_show_message_of_same_camapign"] = str(p["ttl_hours"])
    if dc:
        body["delivery_controls"] = dc
    return body


def _cmp_payload(p):
    return v5_campaign_payload(p)


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
