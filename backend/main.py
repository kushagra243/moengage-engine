"""
moengage-engine local API. Binds to 127.0.0.1 only (see start.py). Every /api
route requires the per-process X-Local-Token (injected into index.html).
"""
from __future__ import annotations
import json
import logging
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .database import (init_db, get_all_settings, set_setting, get_setting, get_latest_daily_run, get_daily_run_history,
                       get_chat_history, clear_chat_history, save_chat_message, migrate_plaintext_secrets)
from .security import install_log_redaction, LocalTokenMiddleware, local_token, secret_store, redact, audit, request_actor
from .security.audit import tail as audit_tail, verify_chain
from .security.secrets import is_secret_key
from .moengage import MoEngageClient, DataUnavailable, registry_status
from .moengage import capture as har_capture
from .moengage.public_api import PublicAPI
from .moengage.executors import register_all as register_executors
from .moengage.session import DashboardSession, SessionError, parse_cookies, cookie_summary
from .scheduler import scheduler_service
from .clm_intelligence import CLMIntelligenceEngine
from . import approvals
from .anomaly import record_snapshot, detect_anomalies, get_history, list_tracked_campaigns, snapshot_count, init_anomaly_tables
from .anomaly.store import recent_events
from .market import market_context, market_news, campaign_hooks
from .llm import llm_settings, list_models, probe as llm_probe, LLMError
from .llm.agent import MarketerAgent
from . import growth

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
install_log_redaction()

init_db()
init_anomaly_tables()
approvals.init_approval_tables()
growth.init_growth_tables()
from .analysis import init_analysis_tables
init_analysis_tables()
from .autopilot import init_autopilot_tables
from .experiments import init_experiment_tables
init_autopilot_tables(); init_experiment_tables()
from .guidance import init_guidance_tables
init_guidance_tables()
from .segments import init_segment_tables
from .sops import init_sop_tables
init_segment_tables(); init_sop_tables()
from .plans import init_plan_tables
init_plan_tables()
from .selfheal import init_selfheal_tables
init_selfheal_tables()
from .llm.usage import init_usage_tables
init_usage_tables()
from .datarequests import init_datarequest_tables
init_datarequest_tables()
_migrated = migrate_plaintext_secrets()
if _migrated:
    logging.getLogger("moengage").info("encrypted %d legacy plaintext secret(s)", _migrated)
register_executors()
from . import devagent as _devagent
_devagent.register()
# demo mode: make sure there is history to look at
try:
    if get_setting("mock_mode", "true").lower() == "true" and snapshot_count("mock") < 7:
        from .moengage import mock as _mock
        _mock.seed_history(30, inject=True)
        logging.getLogger("moengage").info("mock mode: seeded 30 days of demo history")
except Exception as _e:
    logging.getLogger("moengage").info("mock seed skipped: %s", redact(str(_e)))
scheduler_service.start()
audit("app.start", {"secrets_backend": secret_store.status()["backend"]})

app = FastAPI(title="moengage-engine (local)", version="2.0.0", docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(LocalTokenMiddleware)
app.add_middleware(CORSMiddleware, allow_origins=["http://127.0.0.1:8080", "http://localhost:8080"], allow_credentials=False,
                   allow_methods=["GET", "POST"], allow_headers=["Content-Type", "X-Local-Token"])

FRONTEND_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend")
CSP = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; font-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"


def _ok(data: Any, source: Optional[str] = None) -> Dict[str, Any]:
    return {"ok": True, "source": source, "data": data}


def _unavailable(e: DataUnavailable) -> Dict[str, Any]:
    return {"ok": False, "source": "unavailable", "role": e.role, "reason": e.reason, "data": None}


# ── models ─────────────────────────────────────────────────────────────────────
class SettingsPayload(BaseModel):
    values: Dict[str, Any]

class ClearSecretPayload(BaseModel):
    key: str

class ChatPayload(BaseModel):
    message: str

class ProposePayload(BaseModel):
    kind: str
    title: str
    payload: Dict[str, Any]
    rationale: str = ""
    risk: str = "medium"

class DecisionPayload(BaseModel):
    note: str = ""

class VerifyPayload(BaseModel):
    roles: Optional[List[str]] = None
    try_candidates: bool = True

class HarPayload(BaseModel):
    har_text: str


# ── health / status ────────────────────────────────────────────────────────────
@app.get("/api/health")
def health():
    return {"ok": True, "time": datetime.now().isoformat()}


@app.get("/api/status")
def status():
    moe = MoEngageClient()
    cfg = llm_settings()
    return {
        "moengage": moe.verify_session(),
        "llm": {"provider": cfg["provider"], "model": cfg["model"], "base_url": cfg["base_url"], "configured": bool(cfg["api_key"]) or cfg["provider"] == "claude_cli"},
        "scheduler": {"enabled": get_setting("schedule_enabled", "true").lower() == "true", "time": get_setting("schedule_time", "09:00"),
                      "last_run_time": scheduler_service.last_run_time, "last_run_status": scheduler_service.last_run_status},
        "settings": {"region": get_setting("moengage_region", ""), "app_id": get_setting("moengage_app_id", ""), "mock_mode": moe.mock_mode,
                     "has_cookies": moe.has_cookies()},
        "security": {"secrets_backend": secret_store.status()["backend"], "audit": verify_chain(), "bind": "127.0.0.1",
                     "allowed_hosts": list(__import__("backend.security.localauth", fromlist=["ALLOWED_HOST_PREFIXES"]).ALLOWED_HOST_PREFIXES)},
        "integration": {k: v for k, v in registry_status().items() if k in ("usable_reads", "usable_writes", "learned_file", "verified_file")},
        "anomaly": {"days_of_history": snapshot_count(moe.mode)},
        "growth": growth.counts(),
    }


# ── settings ───────────────────────────────────────────────────────────────────
ALLOWED_SETTING_PREFIXES = ("moengage_", "llm_", "market_", "schedule_", "refresh_", "analysis_", "taxonomy_", "autopilot_", "devagent_", "web3_", "competitor", "mock_mode")


@app.get("/api/settings")
def get_settings():
    return get_all_settings()


@app.post("/api/settings")
def update_settings(payload: SettingsPayload):
    saved, rejected = [], []
    for k, v in payload.values.items():
        if not k.startswith(ALLOWED_SETTING_PREFIXES):
            rejected.append(k); continue
        if v is None:
            continue
        v = str(v)
        if is_secret_key(k) and v == "":
            continue                    # empty secret field = leave unchanged
        if k == "moengage_dc" and v:
            m = re.fullmatch(r"(?:api|dashboard)?-?0*(\d{1,3})(?:\.moengage\.com)?", v.strip(), re.I)
            if not m:
                raise HTTPException(400, "data centre must be digits, e.g. 03 (api-03 and dashboard-03 also accepted)")
            v = m.group(1).zfill(2)
        if k == "moengage_region" and v and not (v.endswith(".moengage.com") and "/" not in v and " " not in v):
            raise HTTPException(400, "region must be a *.moengage.com host")
        if k == "llm_base_url" and not (v.startswith("https://") or v.startswith("http://127.0.0.1") or v.startswith("http://localhost")):
            raise HTTPException(400, "LLM base URL must be https:// (or a loopback http:// server)")
        if k == "moengage_cookies":
            from .moengage.session import parse_credentials
            try:
                cred = parse_credentials(v)
            except Exception as e:
                raise HTTPException(400, f"credential format not recognised: {redact(str(e))}")
            if cred["access_token"]:
                set_setting("moengage_access_token", cred["access_token"]); saved.append("moengage_access_token")
            if cred["refresh_token"]:
                set_setting("moengage_refresh_token", cred["refresh_token"]); saved.append("moengage_refresh_token")
            if cred["app_key"] and not get_setting("moengage_app_id", ""):
                set_setting("moengage_app_id", cred["app_key"]); saved.append("moengage_app_id")
            if not cred["cookies"]:
                if cred["access_token"]:
                    continue            # tokens only: nothing to store under cookies
                raise HTTPException(400, "no cookies or bearer token found in the pasted text")
            v = json.dumps(cred["cookies"])      # normalise to JSON object
        set_setting(k, v)
        saved.append(k)
    if any(k in ("llm_provider", "llm_model", "llm_api_key", "llm_base_url") for k in saved):
        from .llm.provider import reconcile_llm_settings
        for k, v in reconcile_llm_settings(set(saved)).items():
            saved.append(f"{k} → {v}")
    if any(k in ("moengage_cookies", "moengage_access_token", "moengage_refresh_token") for k in saved) and get_setting("mock_mode", "true").lower() != "true":
        _background_verify()
    return {"success": True, "saved": saved, "rejected": rejected, "auto_verify": any(k in ("moengage_cookies", "moengage_access_token", "moengage_refresh_token") for k in saved)}


@app.post("/api/settings/clear-secret")
def clear_secret(payload: ClearSecretPayload):
    if not is_secret_key(payload.key):
        raise HTTPException(400, "not a secret key")
    set_setting(payload.key, "")
    return {"success": True}


# ── connection tests ───────────────────────────────────────────────────────────
@app.post("/api/auth/test")
def auth_test():
    moe = MoEngageClient()
    out: Dict[str, Any] = {"session": moe.verify_session()}
    if not moe.mock_mode:
        api = PublicAPI()
        out["public_api"] = api.probe() if api.app_id else {"ok": None, "detail": "Workspace ID not set"}
        if moe.has_cookies() or get_setting("moengage_access_token", ""):
            try:
                out["cookie_verify"] = har_capture.verify(DashboardSession(), roles=["whoami", "campaign_list", "segment_list", "flow_list"])
            except Exception as e:
                out["cookie_verify"] = {"error": redact(str(e))}
    return out


# ── integration (registry / HAR / verify) ──────────────────────────────────────
@app.get("/api/integration/status")
def integration_status():
    api = PublicAPI()
    return {"registry": registry_status(), "public_api": api.configured(), "cookies": cookie_summary(parse_cookies(get_setting("moengage_cookies", ""))) if get_setting("moengage_cookies", "") else None}


@app.post("/api/integration/har")
async def integration_har(file: Optional[UploadFile] = File(None), har_text: Optional[str] = Form(None)):
    text = har_text
    if file is not None:
        raw = await file.read()
        if len(raw) > 100 * 1024 * 1024:
            raise HTTPException(413, "HAR too large")
        text = raw.decode("utf-8", errors="replace")
    if not text:
        raise HTTPException(400, "provide a HAR file")
    try:
        return har_capture.learn(text, app_id=get_setting("moengage_app_id", ""), db_name=get_setting("moengage_db_name", ""))
    except json.JSONDecodeError:
        raise HTTPException(400, "not valid HAR JSON")


@app.post("/api/integration/har-json")
def integration_har_json(payload: HarPayload):
    try:
        return har_capture.learn(payload.har_text, app_id=get_setting("moengage_app_id", ""), db_name=get_setting("moengage_db_name", ""))
    except json.JSONDecodeError:
        raise HTTPException(400, "not valid HAR JSON")


@app.post("/api/integration/verify")
def integration_verify(payload: VerifyPayload):
    if not (get_setting("moengage_cookies", "") or get_setting("moengage_access_token", "")):
        raise HTTPException(400, "no session credentials configured (cookies or bearer token)")
    try:
        return har_capture.verify(DashboardSession(), roles=payload.roles, try_candidates=payload.try_candidates)
    except SessionError as e:
        raise HTTPException(400, str(e))


class DiscoverPayload(BaseModel):
    verify: bool = True


@app.post("/api/integration/discover")
def integration_discover(payload: DiscoverPayload):
    from .moengage import discover as disc
    region = get_setting("moengage_region", "dashboard-01.moengage.com")
    try:
        out = disc.discover(region)
    except Exception as e:
        raise HTTPException(502, f"discovery failed: {redact(str(e))}")
    result: Dict[str, Any] = {"discovery": {k: v for k, v in out.items() if k != "per_chunk_sample"}}
    if payload.verify and (get_setting("moengage_cookies", "") or get_setting("moengage_access_token", "")):
        try:
            result["verify"] = har_capture.verify(DashboardSession(), try_candidates=True)
        except Exception as e:
            result["verify_error"] = redact(str(e))
    result["registry"] = registry_status()
    return result


def _background_verify():
    """After cookies/tokens are saved: discover once (if never done) and probe read roles. Never raises."""
    import threading
    def run():
        try:
            from .moengage import discover as disc
            if not disc.discovered():
                disc.discover(get_setting("moengage_region", "dashboard-01.moengage.com"))
            har_capture.verify(DashboardSession(), try_candidates=True)
        except Exception as e:
            logging.getLogger("moengage").info("background verify: %s", redact(str(e)))
    threading.Thread(target=run, daemon=True, name="auto-verify").start()


@app.post("/api/integration/probe-keys")
def integration_probe_keys():
    return PublicAPI().probe()


@app.get("/api/integration/audit")
def integration_audit(limit: int = 50):
    return {"chain": verify_chain(), "entries": audit_tail(limit)}


# ── MoEngage reads ─────────────────────────────────────────────────────────────
@app.get("/api/moengage/campaigns")
def campaigns(status: Optional[str] = None, channel: Optional[str] = None):
    try:
        rows = MoEngageClient().get_campaigns(status, channel)
        return _ok(rows, rows[0].get("_source") if rows else MoEngageClient().mode)
    except DataUnavailable as e:
        return _unavailable(e)


@app.get("/api/moengage/campaigns/{campaign_id}/stats")
def campaign_stats(campaign_id: str):
    try:
        d = MoEngageClient().get_campaign_stats(campaign_id)
        return _ok(d, d.get("_source"))
    except DataUnavailable as e:
        return _unavailable(e)


@app.get("/api/moengage/segments")
def segments():
    try:
        rows = MoEngageClient().get_segments()
        return _ok(rows, rows[0].get("_source") if rows else MoEngageClient().mode)
    except DataUnavailable as e:
        return _unavailable(e)


@app.get("/api/moengage/flows")
def flows():
    try:
        rows = MoEngageClient().get_flows()
        return _ok(rows, rows[0].get("_source") if rows else MoEngageClient().mode)
    except DataUnavailable as e:
        return _unavailable(e)


@app.get("/api/moengage/analytics")
def analytics():
    try:
        d = MoEngageClient().get_analytics_summary()
        return _ok(d, d.get("_source"))
    except DataUnavailable as e:
        return _unavailable(e)


@app.get("/api/metrics/dictionary")
def metrics_dictionary():
    from .metrics import dictionary
    return dictionary()


@app.get("/api/clm/board")
def clm_board():
    return CLMIntelligenceEngine().get_full_board()


# ── anomalies ──────────────────────────────────────────────────────────────────
@app.get("/api/anomaly/report")
def anomaly_report(source: Optional[str] = None):
    src = source or MoEngageClient().mode
    rep = detect_anomalies(source=src, persist=False)
    rep["days_of_history"] = snapshot_count(src)
    rep["tracked"] = list_tracked_campaigns(src)
    return rep


@app.post("/api/anomaly/snapshot")
def anomaly_snapshot():
    moe = MoEngageClient()
    try:
        camps = moe.get_campaigns()
    except DataUnavailable as e:
        return _unavailable(e)
    snap = record_snapshot(camps, source=moe.mode)
    rep = detect_anomalies(source=moe.mode, persist=True)
    return {"snapshot": snap, "report": rep}


@app.get("/api/anomaly/history/{campaign_id}")
def anomaly_history(campaign_id: str, days: int = 60):
    return {"campaign_id": campaign_id, "history": get_history(campaign_id, MoEngageClient().mode, days)}


@app.get("/api/anomaly/diagnose")
def anomaly_diagnose_all(limit: int = 20):
    from .anomaly.diagnose import diagnose_all
    return {"source": MoEngageClient().mode, "campaigns": diagnose_all(MoEngageClient().mode, limit)}


@app.get("/api/anomaly/diagnose/{campaign_id}")
def anomaly_diagnose_one(campaign_id: str):
    from .anomaly.diagnose import diagnose_campaign
    return diagnose_campaign(campaign_id, MoEngageClient().mode)


@app.get("/api/anomaly/events")
def anomaly_events(limit: int = 50):
    return recent_events(limit, MoEngageClient().mode)


# ── market ─────────────────────────────────────────────────────────────────────
@app.get("/api/market/context")
def market_ctx(force: int = 0):
    return market_context(force=bool(force))


@app.get("/api/market/news")
def market_news_route(category: Optional[str] = None, query: Optional[str] = None, limit: int = 12):
    return market_news(category=category, query=query, limit=limit)


@app.get("/api/market/hooks")
def market_hooks_route(force: int = 0):
    return campaign_hooks(force=bool(force))


# ── approvals ──────────────────────────────────────────────────────────────────
@app.get("/api/approvals")
def approvals_list(status: Optional[str] = None):
    return {"proposals": approvals.list_proposals(status=status), "kinds": approvals.registered_kinds()}


@app.get("/api/approvals/{pid}")
def approvals_get(pid: int):
    p = approvals.get_proposal(pid)
    if not p:
        raise HTTPException(404, "not found")
    return p


@app.post("/api/approvals/propose")
def approvals_propose(payload: ProposePayload, request: Request):
    try:
        return approvals.propose(payload.kind, payload.title, payload.payload, payload.rationale, payload.risk, created_by=request_actor(request))
    except (approvals.ApprovalError, ValueError) as e:
        raise HTTPException(400, str(e))


@app.post("/api/approvals/{pid}/approve")
def approvals_approve(pid: int, payload: DecisionPayload, request: Request):
    try:
        return approvals.approve_and_execute(pid, decided_by=request_actor(request), note=payload.note)
    except approvals.ApprovalError as e:
        raise HTTPException(400, str(e))


class EditPayload(BaseModel):
    changes: Dict[str, Any]
    note: str = ""
    replace: bool = False

class CommentPayload(BaseModel):
    text: str


@app.post("/api/approvals/{pid}/edit")
def approvals_edit(pid: int, payload: EditPayload, request: Request):
    try:
        return approvals.update_payload(pid, payload.changes, actor=request_actor(request), note=payload.note, replace=payload.replace)
    except (approvals.ApprovalError, ValueError) as e:
        raise HTTPException(400, str(e))


@app.post("/api/approvals/{pid}/comment")
def approvals_comment(pid: int, payload: CommentPayload, request: Request):
    try:
        return approvals.add_comment(pid, payload.text, actor=request_actor(request))
    except approvals.ApprovalError as e:
        raise HTTPException(400, str(e))


@app.post("/api/approvals/{pid}/reject")
def approvals_reject(pid: int, payload: DecisionPayload, request: Request):
    try:
        return approvals.reject(pid, note=payload.note, decided_by=request_actor(request))
    except approvals.ApprovalError as e:
        raise HTTPException(400, str(e))


# ── mock / demo walkthrough ────────────────────────────────────────────────────
class MockSeed(BaseModel):
    days: int = 30
    inject: bool = True


@app.post("/api/mock/seed")
def mock_seed(payload: MockSeed):
    if get_setting("mock_mode", "true").lower() != "true":
        raise HTTPException(400, "seeding is only allowed in mock mode")
    from .moengage import mock as _mock
    return _mock.seed_history(max(7, min(90, payload.days)), inject=payload.inject)


@app.get("/api/mock/walkthrough")
def mock_walkthrough():
    """Progress of the demo checklist, computed from state (no separate bookkeeping)."""
    mode_mock = get_setting("mock_mode", "true").lower() == "true"
    cfg = llm_settings()
    latest = get_latest_daily_run()
    props = approvals.list_proposals(limit=200)
    steps = [
        {"id": "history", "title": "Seed 30 days of campaign history", "done": snapshot_count("mock") >= 7, "action": "seed", "detail": f"{snapshot_count('mock')} days recorded"},
        {"id": "anomalies", "title": "Detect outliers against each campaign's own baseline", "done": bool(recent_events(1, "mock")), "action": "snapshot", "detail": "critical / warning events appear in Anomalies"},
        {"id": "market", "title": "Pull live market context and hooks", "done": bool(market_context(force=False).get("generated_at")), "action": "market", "detail": "regime, movers, headlines → hooks"},
        {"id": "feed", "title": "Derive growth hacks from data (no model)", "done": (growth.counts().get("new", 0) + growth.counts().get("saved", 0) + growth.counts().get("proposed", 0)) > 0, "action": "feed", "detail": "Overview → Markets & growth hacks"},
        {"id": "daily", "title": "Run the daily process", "done": latest is not None, "action": "daily", "detail": "snapshot → anomalies → market → feed → brief"},
        {"id": "llm", "title": "Connect a model (Claude CLI or OpenRouter)", "done": bool(cfg["api_key"]) or cfg["provider"] == "claude_cli", "action": "settings", "detail": f"provider {cfg['provider']}"},
        {"id": "agent", "title": "Ask the agent for an audit and a proposal", "done": any(p["created_by"] == "agent" for p in props), "action": "agent", "detail": "Agent tab; every idea is captured in the feed"},
        {"id": "approve", "title": "Approve or reject a proposal", "done": any(p["status"] in ("executed", "rejected") for p in props), "action": "approvals", "detail": "nothing reaches MoEngage without this"},
        {"id": "live", "title": "Go live: paste dashboard headers or API keys", "done": not mode_mock, "action": "settings", "detail": "Settings → Integration"},
    ]
    return {"mock_mode": mode_mock, "steps": steps, "done": sum(1 for s in steps if s["done"]), "total": len(steps)}


# ── taxonomy & deep analysis ───────────────────────────────────────────────────
@app.get("/api/taxonomy/catalog")
def taxonomy_catalog():
    from .taxonomy import catalog
    try:
        return catalog(MoEngageClient().get_campaigns())
    except DataUnavailable as e:
        return _unavailable(e)


@app.get("/api/taxonomy/groups")
def taxonomy_groups(by: str = "group_key"):
    from .taxonomy import group
    try:
        return {"by": by, "groups": group(MoEngageClient().get_campaigns(), by)}
    except DataUnavailable as e:
        return _unavailable(e)


@app.get("/api/analysis")
def analysis_list(limit: int = 50):
    from .analysis import list_analyses
    return {"analyses": list_analyses(limit)}


@app.get("/api/analysis/{campaign_id}")
def analysis_get(campaign_id: str):
    from .analysis import latest
    r = latest(campaign_id)
    if not r:
        raise HTTPException(404, "no analysis yet; POST /api/analysis/run")
    return r


class AnalysisRun(BaseModel):
    campaign_ids: Optional[List[str]] = None
    force: bool = False
    limit: int = 8


@app.post("/api/analysis/run")
def analysis_run(payload: AnalysisRun):
    from .analysis import analyse, analyse_priority
    if payload.campaign_ids:
        out = []
        for cid in payload.campaign_ids[:20]:
            try:
                r = analyse(cid, force=payload.force)
                out.append({"campaign_id": cid, "cached": r["cached"], "tier": r["tier"], "model": r["model"], "verdict": r["analysis"].get("verdict")})
            except Exception as e:
                out.append({"campaign_id": cid, "error": redact(str(e))[:200]})
        return {"analysed": out}
    return analyse_priority(limit=payload.limit, force=payload.force)


# ── autopilot & experiments ────────────────────────────────────────────────────
class AutopilotRun(BaseModel):
    max_actions: int = 3
    force: bool = True


@app.get("/api/autopilot/queue")
def autopilot_queue():
    from .autopilot import action_queue
    q = action_queue()
    q["enabled"] = get_setting("autopilot_enabled", "true").lower() == "true"
    return q


@app.post("/api/autopilot/run")
def autopilot_run(payload: AutopilotRun):
    from .autopilot import run as ap_run
    return ap_run(max_actions=max(1, min(6, payload.max_actions)), force=payload.force)


@app.get("/api/autopilot/runs")
def autopilot_runs(limit: int = 30):
    from .autopilot import recent
    return {"runs": recent(limit)}


@app.get("/api/experiments")
def experiments_list(limit: int = 50):
    from .experiments import list_experiments
    return {"experiments": list_experiments(limit)}


@app.post("/api/experiments/refresh")
def experiments_refresh():
    from .experiments import refresh_all
    return refresh_all()


# ── growth hacks (curated + model-suggested, ranked for this workspace) ────────
class HackStatus(BaseModel):
    status: str
    verified: Optional[bool] = None


@app.get("/api/hacks")
def hacks_list(status: Optional[str] = None):
    from .hacks import ranked
    return ranked(status)


@app.post("/api/hacks/suggest")
def hacks_suggest(n: int = 5):
    from .hacks import suggest_with_model, ranked
    r = suggest_with_model(n)
    r["hacks"] = ranked()["hacks"][:5]
    return r


@app.post("/api/hacks/{hack_id}/status")
def hacks_status(hack_id: str, payload: HackStatus):
    from .hacks import set_status
    if payload.status not in ("new", "saved", "dismissed", "proposed"):
        raise HTTPException(400, "bad status")
    set_status(hack_id, payload.status, payload.verified)
    return {"ok": True}


# ── growth feed ────────────────────────────────────────────────────────────────
class IdeaStatus(BaseModel):
    status: str

class GrowthRefresh(BaseModel):
    use_llm: Optional[bool] = None
    n: int = 5


@app.get("/api/growth/feed")
def growth_feed(status: Optional[str] = "new", kind: Optional[str] = None, limit: int = 40):
    return {"ideas": growth.list_ideas(status=status or None, kind=kind, limit=limit), "counts": growth.counts()}


@app.post("/api/growth/refresh")
def growth_refresh(payload: GrowthRefresh):
    out: Dict[str, Any] = {"rules": growth.generate_rule_ideas()}
    cfg = llm_settings()
    want = (bool(cfg["api_key"]) or cfg["provider"] == "claude_cli") if payload.use_llm is None else payload.use_llm
    if want:
        try:
            out["agent"] = growth.generate_agent_ideas(MarketerAgent(), n=payload.n)
        except Exception as e:
            out["agent_error"] = redact(str(e))
    out["counts"] = growth.counts()
    return out


@app.post("/api/growth/{idea_id}/status")
def growth_status(idea_id: int, payload: IdeaStatus):
    try:
        r = growth.set_status(idea_id, payload.status)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not r:
        raise HTTPException(404, "idea not found")
    return r


# ── agent / LLM ────────────────────────────────────────────────────────────────
@app.post("/api/agent/chat")
def agent_chat(payload: ChatPayload):
    msg = payload.message.strip()
    if not msg:
        raise HTTPException(400, "Empty message")
    cfg = llm_settings()
    if not cfg["api_key"] and cfg["provider"] != "claude_cli":
        save_chat_message("user", msg)
        reply = ("No LLM configured. Add an OpenRouter (or any OpenAI-compatible) API key in Settings → LLM to enable the live agent. "
                 "Meanwhile the deterministic audit and anomaly report are available in the CLM board.")
        save_chat_message("assistant", reply)
        return {"reply": reply, "model": None, "tool_used": [], "trace": [], "needs_llm": True}
    try:
        return MarketerAgent().chat(msg)
    except LLMError as e:
        raise HTTPException(502, str(e))


@app.get("/api/agent/context")
def agent_context():
    """Compact situational strip for the Agent tab: mode, model, what needs attention, pending approvals, ideas."""
    moe = MoEngageClient(); cfg = llm_settings()
    from .llm.provider import cli_model
    rep = detect_anomalies(source=moe.mode, persist=False)
    latest = get_latest_daily_run()
    top = [e for e in rep.get("anomalies", []) if not e.get("secondary")][:3]
    return {"mode": moe.mode, "provider": cfg["provider"], "model": cli_model(cfg["model"]) if cfg["provider"] == "claude_cli" else cfg["model"],
            "llm_configured": bool(cfg["api_key"]) or cfg["provider"] == "claude_cli",
            "by_urgency": rep.get("by_urgency", {}), "top_issues": [{"headline": e.get("headline"), "urgency": e.get("urgency")} for e in top],
            "pending_approvals": len(approvals.list_proposals(status="pending")), "new_ideas": growth.counts().get("new", 0),
            "last_run": (latest or {}).get("created_at"), "last_summary": ((latest or {}).get("summary") or "")[:220], "days_of_history": snapshot_count(moe.mode),
            "tokens_today": (lambda u: {"prompt": u["today"]["prompt_tokens"], "completion": u["today"]["completion_tokens"], "cost_usd": u["today"]["cost_usd"], "calls": u["today"]["calls"]})(__import__("backend.llm.usage", fromlist=["summary"]).summary(1))}


@app.get("/api/agent/history")
def agent_history():
    return get_chat_history(limit=40)


@app.post("/api/agent/clear")
def agent_clear():
    clear_chat_history()
    return {"success": True}


# ── teach the agent / skills / API catalog / dev agent ─────────────────────────
class GuidancePayload(BaseModel):
    text: str
    scope: str = "general"

class GuidanceToggle(BaseModel):
    active: bool

class EngineSettingPayload(BaseModel):
    key: str
    value: Any


@app.get("/api/guidance")
def guidance_list():
    from . import guidance
    return {"guidance": guidance.list_guidance(), "scopes": list(guidance.SCOPES), "engine_settings": {k: {**v, "current": get_setting(k, "")} for k, v in guidance.ENGINE_SETTINGS.items()}}


@app.post("/api/guidance")
def guidance_add(payload: GuidancePayload, request: Request):
    from . import guidance
    try:
        return guidance.add(payload.text, author=request_actor(request), scope=payload.scope)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/guidance/{gid}/toggle")
def guidance_toggle(gid: int, payload: GuidanceToggle, request: Request):
    from . import guidance
    r = guidance.set_active(gid, payload.active, actor=request_actor(request))
    if not r:
        raise HTTPException(404, "not found")
    return r


@app.post("/api/guidance/{gid}/delete")
def guidance_delete(gid: int, request: Request):
    from . import guidance
    return {"deleted": guidance.delete(gid, actor=request_actor(request))}


@app.post("/api/engine/setting")
def engine_setting(payload: EngineSettingPayload, request: Request):
    from . import guidance
    r = guidance.set_engine_setting(payload.key, payload.value, actor=request_actor(request))
    if r.get("error"):
        raise HTTPException(400, r["error"])
    return r


@app.get("/api/skills")
def skills_list():
    from .skills import list_skills
    return {"skills": list_skills()}


@app.get("/api/agent/tools")
def agent_tools():
    from .llm.tools import TOOL_SCHEMAS
    from .llm.agent import TOOL_BUDGETS
    out = []
    for t in TOOL_SCHEMAS:
        fn = t.get("function") or t
        name = fn.get("name"); params = list(((fn.get("parameters") or {}).get("properties") or {}).keys())
        out.append({"name": name, "description": fn.get("description", ""), "params": params, "required": (fn.get("parameters") or {}).get("required") or [], "writes": bool(name and (name.startswith("propose_") or name in ("run_sop", "define_sop", "campaign_from_alert", "record_ideas", "request_data", "set_engine_setting", "set_comms_limits", "remember_guidance", "define_nomenclature", "write_flight_plan", "sop_india_fix"))), "budget_chars": TOOL_BUDGETS.get(name)})
    return {"tools": out, "count": len(out)}


@app.get("/api/skills/{name}")
def skills_get(name: str):
    from .skills import read_skill
    r = read_skill(name)
    if r.get("error"):
        raise HTTPException(404, r["error"])
    return r


@app.get("/api/api-catalog")
def api_catalog_view(q: Optional[str] = None, method: Optional[str] = None, path: Optional[str] = None):
    from . import api_catalog
    if method and path:
        return api_catalog.operation_detail(method, path)
    if q:
        return {"matches": api_catalog.search(q, limit=25)}
    return api_catalog.overview()


# ── cohorts (segment nomenclature) and SOPs ───────────────────────────────────
class DefineCode(BaseModel):
    code: str
    meaning: str

class SopSpec(BaseModel):
    spec: Dict[str, Any]

class SopRun(BaseModel):
    segment_name: Optional[str] = None
    start_date: Optional[str] = None
    dry_run: bool = False

class SopToggle(BaseModel):
    active: bool


@app.get("/api/segments/study")
def segments_study(refresh: bool = False):
    from . import segments
    c = MoEngageClient()
    if refresh or not segments.registry(limit=1):
        try:
            segments.sync(c.get_segments())
        except DataUnavailable as e:
            return {"ok": False, "role": e.role, "reason": str(e)}
    try:
        return {"ok": True, **segments.study(c.get_campaigns())}
    except DataUnavailable as e:
        return {"ok": False, "role": e.role, "reason": str(e)}


@app.post("/api/taxonomy/define")
def taxonomy_define(payload: DefineCode, request: Request):
    from . import guidance
    meaning = payload.meaning if ":" in payload.meaning else "cohort:" + payload.meaning
    r = guidance.set_engine_setting("taxonomy_codes", {payload.code.upper(): meaning}, actor=request_actor(request))
    if r.get("error"):
        raise HTTPException(400, r["error"])
    return r


@app.get("/api/sops")
def sops_list():
    from . import sops
    return {"sops": sops.list_sops(include_inactive=True), "runs": sops.list_runs(20), "types": list(sops.CAMPAIGN_TYPES)}


class DataRequestPayload(BaseModel):
    kind: str
    title: str
    why: str
    spec: str = ""
    unblocks: List[str] = []
    priority: int = 50

class DataRequestStatus(BaseModel):
    status: str
    note: str = ""


@app.get("/api/data-requests")
def data_requests_list(status: Optional[str] = None):
    from . import datarequests
    return {"requests": datarequests.list_requests(status), "kinds": list(datarequests.KINDS)}


@app.post("/api/data-requests")
def data_requests_create(payload: DataRequestPayload, request: Request):
    from . import datarequests
    r = datarequests.request(payload.kind, payload.title, payload.why, spec=payload.spec, unblocks=payload.unblocks, priority=payload.priority, requested_by=request_actor(request))
    if r.get("error"):
        raise HTTPException(400, r["error"])
    return r


@app.post("/api/data-requests/{rid}/status")
def data_requests_status(rid: int, payload: DataRequestStatus, request: Request):
    from . import datarequests
    r = datarequests.set_status(rid, payload.status, payload.note, actor=request_actor(request))
    if not r:
        raise HTTPException(400, "bad status or id")
    return r


@app.get("/api/market/web3")
def market_web3(force: bool = False, chain: Optional[str] = None):
    from .market.onchain import trending
    t = trending(force=force)
    if chain:
        t = {**t, "trending": [r for r in t.get("trending", []) if r.get("chain") == chain]}
    return t


@app.get("/api/market/competitors")
def market_competitors(force: bool = False):
    from .market import competitors
    from .market.context import _latest
    ctx = _latest(6 * 3600) or {}
    return competitors.intel({"crypto_markets": ctx.get("crypto_markets") or []}, force=force)


@app.get("/api/market/competitor-campaigns")
def market_competitor_campaigns(hours: int = 48, venue: Optional[str] = None, force: bool = False):
    from .market import campaign_intel
    return campaign_intel.campaigns(hours=hours, venue=venue, force=force)


class AlertCampaign(BaseModel):
    product: str
    headline: str
    detail: str = ""
    sop_id: Optional[str] = None
    segment_name: Optional[str] = None
    dry_run: bool = False


@app.post("/api/market/alert-campaign")
def market_alert_campaign(payload: AlertCampaign, request: Request):
    from . import sops
    return sops.run_from_alert(payload.product, payload.headline, payload.detail, sop_id=payload.sop_id, segment_name=payload.segment_name, created_by=request_actor(request), dry_run=payload.dry_run)


@app.get("/api/market/feed")
def market_feed():
    from .market import feed
    from .market.context import _latest
    ctx = _latest(6 * 3600) or {}
    return {"flash": feed.flash(ctx), "news": feed.biggest_news(ctx), "top_oi": feed.top_oi(ctx), "by_category": feed.top_by_category(ctx), "by_product": feed.by_product(ctx), "generated_at": ctx.get("generated_at")}


@app.get("/api/market/competitors/dossiers")
def market_dossiers():
    from .market import dossiers
    return dossiers.all_dossiers()


@app.get("/api/market/competitor/{venue}")
def market_dossier(venue: str):
    from .market import dossiers, competitors, benchmarks
    from . import brain
    try:
        it = brain.intel()
    except Exception:
        it = {}
    ci = competitors.intel({"crypto_markets": []}); bench = benchmarks.benchmarks()
    return dossiers.dossier(venue, {**ci, "rivals": it.get("rivals") or []}, bench)


@app.get("/api/market/onchain-vs-cex")
def market_onchain_vs_cex(force: bool = False):
    from .market import onchain_cex
    return onchain_cex.compare(force=force)


@app.get("/api/market/benchmarks")
def market_benchmarks(category: Optional[str] = None, force: bool = False):
    from .market import benchmarks
    return benchmarks.benchmarks(category, force=force)


@app.get("/api/market/pair-battle")
def market_pair_battle(symbol: str):
    from .market import competitors
    return competitors.pair_battle(symbol)


@app.get("/api/sops/product-matrix")
def sops_product_matrix():
    from . import sops
    return sops.product_cohort_matrix()


def _download(data: bytes, filename: str, media: str) -> Response:
    return Response(content=data, media_type=media, headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"})


@app.get("/api/sops/export")
def sops_export(format: str = "md", id: Optional[str] = None):
    """One SOP (id) as md / docx / json, or the whole library as zip / csv / json / md."""
    from . import docs_io, sops, sop_india
    if id:
        sop = sops.get_sop(id)
        if not sop:
            raise HTTPException(404, "unknown SOP")
        md = docs_io.sop_markdown(sop)
        if format == "docx":
            return _download(docs_io.markdown_to_docx(md, sop["name"]), f"{id}.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        if format == "json":
            return _download(json.dumps({k: v for k, v in sop.items() if k not in ("framework_ok", "framework_problems", "steps_count", "active", "updated_at")}, indent=1, default=str).encode(), f"{id}.json", "application/json")
        return _download(md.encode("utf-8"), f"{id}.md", "text/markdown; charset=utf-8")
    all_sops = [sops.get_sop(x["id"]) for x in sops.list_sops()]
    try:
        india = sop_india.review()
    except Exception:
        india = None
    stamp = datetime.now().strftime("%Y%m%d")
    if format == "zip":
        return _download(docs_io.sop_bundle_zip(all_sops, india), f"sop-library-{stamp}.zip", "application/zip")
    if format == "csv":
        return _download(docs_io.sop_index_csv(all_sops, india).encode("utf-8"), f"sop-index-{stamp}.csv", "text/csv; charset=utf-8")
    if format == "json":
        return _download(json.dumps(all_sops, indent=1, default=str).encode(), f"sop-library-{stamp}.json", "application/json")
    return _download("\n\n---\n\n".join(docs_io.sop_markdown(x, machine_block=False) for x in all_sops).encode("utf-8"), f"sop-library-{stamp}.md", "text/markdown; charset=utf-8")


@app.post("/api/sops/import")
async def sops_import(request: Request, file: UploadFile = File(...), apply: bool = False):
    """Upload an edited SOP (.md / .docx / .json): preview the diff and checks; apply=true saves a new version."""
    from . import docs_io, sops, sop_india
    data = await file.read()
    if len(data) > 5 * 1024 * 1024:
        raise HTTPException(413, "file too large")
    try:
        spec, fmt = docs_io.parse_upload(file.filename or "upload.md", data, kind="sop")
    except Exception as e:
        raise HTTPException(400, f"could not parse the document: {redact(str(e))[:160]}")
    if not spec.get("id") and not spec.get("name"):
        raise HTTPException(400, "no SOP found in the document (expected '# SOP: <name>' and an id line, or a JSON spec)")
    current = sops.get_sop(spec.get("id") or "") if spec.get("id") else None
    base = {k: v for k, v in (current or {}).items() if k not in ("framework_ok", "framework_problems", "steps_count", "active", "updated_at")}
    merged = {**base, **{k: v for k, v in spec.items() if v is not None}}
    for k in ("checks", "source"):
        merged.setdefault(k, base.get(k))
    changes = docs_io.diff(base, merged) if current else [{"path": "(new SOP)", "from": None, "to": merged.get("name")}]
    chk = sops.sop_check(merged)
    fit_before = sop_india.review_one(current)["score"] if current else None
    fit_after = sop_india.review_one(merged)["score"] if chk["ok"] else None
    out = {"format": fmt, "id": merged.get("id"), "name": merged.get("name"), "current_version": (current or {}).get("version"), "changes": changes[:80], "change_count": len(changes), "framework": chk, "india_fit": {"before": fit_before, "after": fit_after}, "applied": False}
    if apply:
        if not chk["ok"]:
            raise HTTPException(400, "framework check failed: " + "; ".join(chk["problems"]))
        if not changes:
            return {**out, "note": "no changes detected; nothing saved"}
        res = sops.define_sop(merged, author=f"upload:{request_actor(request)}")
        if not res.get("ok"):
            raise HTTPException(400, "; ".join(res.get("problems") or ["could not save"]))
        audit("sop.import", {"id": merged.get("id"), "version": res.get("version"), "changes": len(changes), "format": fmt}, actor=request_actor(request))
        out.update({"applied": True, "version": res.get("version"), "warnings": res.get("warnings")})
    return out


@app.get("/api/experiments/{pid}/export")
def experiment_export(pid: int, format: str = "md"):
    from . import docs_io, approvals
    p = approvals.get_proposal(pid)
    if not p:
        raise HTTPException(404, "proposal not found")
    md = docs_io.experiment_markdown(p)
    if format == "docx":
        return _download(docs_io.markdown_to_docx(md, p.get("title", f"experiment-{pid}")), f"experiment-{pid}.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    if format == "json":
        return _download(json.dumps(p, indent=1, default=str).encode(), f"experiment-{pid}.json", "application/json")
    return _download(md.encode("utf-8"), f"experiment-{pid}.md", "text/markdown; charset=utf-8")


@app.post("/api/experiments/{pid}/import")
async def experiment_import(pid: int, request: Request, file: UploadFile = File(...), apply: bool = False):
    """Upload an edited experiment document: preview the payload diff; apply=true records the revision on the pending proposal (still needs approval)."""
    from . import docs_io, approvals
    p = approvals.get_proposal(pid)
    if not p:
        raise HTTPException(404, "proposal not found")
    data = await file.read()
    try:
        parsed, fmt = docs_io.parse_upload(file.filename or "upload.md", data, kind="experiment")
    except Exception as e:
        raise HTTPException(400, f"could not parse the document: {redact(str(e))[:160]}")
    if fmt == "json":
        parsed = {"id": parsed.get("id"), "changes": {"payload": parsed.get("payload") or parsed, "rationale": parsed.get("rationale")}}
    if parsed.get("id") and int(parsed["id"]) != pid:
        raise HTTPException(400, f"the document is experiment #{parsed['id']}, not #{pid}")
    changes = parsed.get("changes") or {}
    new_payload = changes.get("payload") or {}
    d = docs_io.diff(p.get("payload") or {}, {**(p.get("payload") or {}), **new_payload}) if new_payload else []
    if changes.get("rationale") and changes["rationale"] != (p.get("rationale") or ""):
        d.append({"path": "rationale", "from": (p.get("rationale") or "")[:120], "to": changes["rationale"][:120]})
    out = {"format": fmt, "id": pid, "status": p.get("status"), "changes": d[:80], "change_count": len(d), "applied": False}
    if apply:
        if not d:
            return {**out, "note": "no changes detected"}
        try:
            r = approvals.update_payload(pid, new_payload, actor=f"upload:{request_actor(request)}", note=f"revision uploaded as {fmt}", replace=False)
        except (approvals.ApprovalError, ValueError) as e:
            raise HTTPException(400, str(e))
        if changes.get("rationale"):
            approvals.add_comment(pid, "Rationale from the uploaded document: " + changes["rationale"][:1500], actor=f"upload:{request_actor(request)}")
        out.update({"applied": True, "proposal": {k: r.get(k) for k in ("id", "status", "preview") if isinstance(r, dict)}})
    return out


@app.get("/api/sops/india-fit")
def sops_india_fit(sop_id: Optional[str] = None):
    from . import sop_india
    return sop_india.review(sop_id)


@app.post("/api/sops/india-fix-all")
def sops_india_fix_all(request: Request):
    from . import sop_india
    return sop_india.apply_all(actor=request_actor(request))


@app.post("/api/sops/{sop_id}/india-fix")
def sops_india_fix(sop_id: str, request: Request):
    from . import sop_india
    r = sop_india.apply_fixes(sop_id, actor=request_actor(request))
    if not r.get("ok") and r.get("error"):
        raise HTTPException(404, r["error"])
    return r


@app.get("/api/sops/matrix")
def sops_matrix():
    from . import sops
    return sops.channel_matrix()


@app.post("/api/sops/define")
def sops_define(payload: SopSpec, request: Request):
    from . import sops
    r = sops.define_sop(payload.spec, author=request_actor(request))
    if not r.get("ok"):
        raise HTTPException(400, "; ".join(r.get("problems") or ["invalid SOP"]))
    return r


@app.post("/api/sops/{sop_id}/run")
def sops_run(sop_id: str, payload: SopRun, request: Request):
    from . import sops
    return sops.run_sop(sop_id, segment_name=payload.segment_name, start_date=payload.start_date, created_by=request_actor(request), dry_run=payload.dry_run)


@app.post("/api/sops/{sop_id}/toggle")
def sops_toggle(sop_id: str, payload: SopToggle):
    from . import sops
    sops.set_active(sop_id, payload.active)
    return {"ok": True}


@app.post("/api/sops/checks")
def sops_checks():
    from . import sops
    return sops.midflight_checks()


# ── guardrails: north star, limits, peace index, monitor; flight plans ────────
class NorthStar(BaseModel):
    text: str

class LimitsPatch(BaseModel):
    patch: Dict[str, Any]

class PlanSpec(BaseModel):
    spec: Dict[str, Any]


def _regime_now() -> Optional[str]:
    try:
        from .market.context import _latest
        return (((_latest(6 * 3600) or {}).get("hooks") or {}).get("regime"))
    except Exception:
        return None


@app.get("/api/guardrails")
def guardrails_view():
    from . import guardrails, plans
    return {"north_star": guardrails.north_star(), "limits": guardrails.limits(), "effective": guardrails.effective_limits(_regime_now()), "month": plans.month_summary()}


@app.post("/api/guardrails/north-star")
def guardrails_north_star(payload: NorthStar, request: Request):
    from . import guardrails
    r = guardrails.set_north_star(payload.text, actor=request_actor(request))
    if r.get("error"):
        raise HTTPException(400, r["error"])
    return r


@app.post("/api/guardrails/limits")
def guardrails_limits(payload: LimitsPatch, request: Request):
    from . import guardrails
    r = guardrails.set_limits(payload.patch, actor=request_actor(request))
    if r.get("error"):
        raise HTTPException(400, r["error"])
    return r


@app.get("/api/guardrails/peace")
def guardrails_peace():
    from . import guardrails
    try:
        return {"ok": True, **guardrails.peace_index(MoEngageClient().get_campaigns(), _regime_now())}
    except DataUnavailable as e:
        return {"ok": False, "role": e.role, "reason": str(e)}


@app.post("/api/guardrails/monitor")
def guardrails_monitor():
    from . import guardrails
    return guardrails.sop_monitor(_regime_now())


@app.get("/api/plans")
def plans_list(month: Optional[str] = None):
    from . import plans
    return {"plans": plans.list_plans(month), "summary": plans.month_summary(month)}


@app.get("/api/plans/{pid}")
def plans_get(pid: int):
    from . import plans
    p = plans.get_plan(pid)
    if not p:
        raise HTTPException(404, "not found")
    return p


@app.post("/api/plans")
def plans_create(payload: PlanSpec, request: Request):
    from . import plans
    r = plans.create_plan(payload.spec, author=request_actor(request))
    if not r.get("ok"):
        raise HTTPException(400, r.get("error", "invalid plan"))
    return r


@app.get("/api/llm/usage")
def llm_usage(days: int = 7):
    from .llm.usage import summary
    return summary(days)


@app.get("/api/selfheal")
def selfheal_view(run_tests: bool = False):
    from .selfheal import health_report
    return health_report(run_tests=run_tests)


@app.post("/api/selfheal/rollback")
def selfheal_rollback(request: Request):
    from .selfheal import rollback_last_merge
    r = rollback_last_merge(f"requested by {request_actor(request)}")
    if not r.get("ok"):
        raise HTTPException(400, r.get("error", "cannot roll back"))
    r["restart_scheduled"] = _devagent.schedule_restart()
    return r


# ── brain API (terminal UI) ─────────────────────────────────────────────────────
class DirectiveAct(BaseModel):
    note: str = ""

class AskPayload(BaseModel):
    message: str


@app.get("/api/brain/state")
def brain_state():
    from . import brain
    return brain.state()


@app.get("/api/brain/directives")
def brain_directives():
    from . import brain
    return {"directives": brain.directives()}


@app.post("/api/brain/directives/{did}/{action}")
def brain_directive_act(did: str, action: str, payload: DirectiveAct, request: Request):
    from . import brain
    if action not in ("approve", "hold", "simulate"):
        raise HTTPException(400, "action must be approve | hold | simulate")
    try:
        return brain.act_on_directive(did, action, actor=request_actor(request), note=payload.note)
    except (approvals.ApprovalError, ValueError) as e:
        raise HTTPException(400, str(e))


@app.get("/api/brain/ideas")
def brain_ideas(filter: str = "all"):
    from . import brain
    return {"ideas": brain.ideas(filter)}


@app.post("/api/brain/ideas/{raw}/promote")
def brain_idea_promote(raw: str, request: Request):
    from . import brain
    return brain.promote_idea(raw, actor=request_actor(request))


@app.get("/api/brain/experiments")
def brain_experiments(filter: str = "all"):
    from . import brain
    return brain.experiments_board(filter)


@app.get("/api/brain/intel")
def brain_intel():
    from . import brain
    return brain.intel()


@app.get("/api/brain/anomalies")
def brain_anomalies():
    from . import brain
    return {"anomalies": brain.anomalies_view()}


@app.get("/api/brain/market")
def brain_market():
    from . import brain
    return brain.market_view()


@app.get("/api/brain/lab")
def brain_lab():
    from . import brain
    return brain.lab_view()


@app.get("/api/brain/atlas")
def brain_atlas():
    from . import brain
    return brain.atlas_view()


class RecQueuePayload(BaseModel):
    id: str


@app.post("/api/brain/recommendations/queue")
def brain_recommendation_queue(payload: RecQueuePayload, request: Request):
    from . import brain
    return brain.queue_recommendation(payload.id, actor=request_actor(request))


@app.get("/api/brain/analysis")
def brain_analysis(force: bool = False):
    from . import workspace_analysis
    return workspace_analysis.report(force=force)


@app.post("/api/brain/analysis/run")
def brain_analysis_run(request: Request):
    from . import workspace_analysis
    audit("workspace_analysis.manual", {}, actor=request_actor(request))
    return workspace_analysis.report(force=True)


class SignalProposePayload(BaseModel):
    signal_id: str
    max_per_day: Optional[int] = None
    quiet_start: str = "22:00"
    quiet_end: str = "08:00"
    regimes: List[str] = []
    rationale: str = ""


class SignalEvalPayload(BaseModel):
    dry_run: bool = True


class SignalTogglePayload(BaseModel):
    enabled: bool


@app.get("/api/refresh/status")
def refresh_status():
    from . import refresher
    return refresher.status()


@app.get("/api/refresh/changes")
def refresh_changes():
    from . import refresher
    return refresher.changes()


@app.post("/api/refresh/run/{job}")
def refresh_run(job: str, request: Request):
    from . import refresher
    audit("refresh.manual", {"job": job}, actor=request_actor(request))
    r = refresher.run_job(job)
    if not r.get("ok") and r.get("error", "").startswith("unknown job"):
        raise HTTPException(404, r["error"])
    return r


@app.post("/api/refresh/run-all")
def refresh_run_all(request: Request):
    from . import refresher
    audit("refresh.manual", {"job": "all"}, actor=request_actor(request))
    return {"runs": [refresher.run_job(j["name"]) for j in refresher.JOBS]}


@app.get("/api/signals")
def signals_catalog():
    from . import signals
    return signals.catalog()


@app.post("/api/signals/evaluate")
def signals_evaluate(payload: SignalEvalPayload, request: Request):
    from . import signals
    audit("signals.evaluate", {"dry_run": payload.dry_run}, actor=request_actor(request))
    return signals.evaluate(dry_run=payload.dry_run)


@app.post("/api/signals/propose")
def signals_propose(payload: SignalProposePayload, request: Request):
    from . import signals
    r = signals.propose_rule(payload.signal_id, payload.max_per_day, payload.quiet_start, payload.quiet_end, payload.regimes, payload.rationale, created_by=request_actor(request))
    if r.get("error"):
        raise HTTPException(400, r["error"])
    return r


@app.post("/api/signals/rules/{signal_id}/toggle")
def signals_toggle(signal_id: str, payload: SignalTogglePayload, request: Request):
    from . import signals
    return signals.set_enabled(signal_id, payload.enabled, actor=request_actor(request))


@app.get("/api/brain/compliance")
def brain_compliance(force: bool = False):
    from . import compliance_sweep
    return compliance_sweep.sweep() if force else (compliance_sweep.latest() or compliance_sweep.sweep())


@app.post("/api/brain/learnings/refresh")
def brain_learnings_refresh(request: Request):
    from . import learnings
    return learnings.refresh()


@app.get("/api/brain/qa")
def brain_qa(force: bool = False):
    from . import qa
    return qa.report(force=force)


@app.post("/api/brain/qa/run")
def brain_qa_run(request: Request):
    from . import qa
    audit("qa.manual", {}, actor=request_actor(request))
    return qa.run()


@app.get("/api/brain/structural")
def brain_structural():
    from . import structural
    return structural.audit()


@app.get("/api/market/moneyflow")
def market_moneyflow():
    from .market import moneyflow
    from .market.context import _latest
    return moneyflow.lab(_latest(6 * 3600) or {})


@app.get("/api/brain/trace")
def brain_trace(limit: int = 40):
    from . import brain
    return {"lines": brain.trace(limit)}


@app.post("/api/brain/cycle/run")
def brain_cycle_run(request: Request):
    audit("cycle.start", {"trigger": "terminal", "mode": "guarded"}, actor=request_actor(request))
    return scheduler_service.trigger_run(trigger_type="manual")


@app.post("/api/brain/ask")
def brain_ask(payload: AskPayload):
    return agent_chat(ChatPayload(message=payload.message))


@app.get("/api/devagent/status")
def devagent_status():
    return _devagent.status()


@app.post("/api/devagent/{pid}/draft")
def devagent_draft(pid: int, request: Request):
    try:
        _devagent.draft_async(pid, actor=request_actor(request))
        return {"started": True, "id": pid}
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/llm/models")
def llm_models():
    return list_models()


@app.post("/api/llm/probe")
def llm_probe_route():
    return llm_probe()


# ── automation ─────────────────────────────────────────────────────────────────
@app.post("/api/automation/run")
def automation_run():
    return scheduler_service.trigger_run(trigger_type="manual")


@app.get("/api/automation/latest")
def automation_latest():
    return get_latest_daily_run()


@app.get("/api/automation/history")
def automation_history():
    return get_daily_run_history(limit=15)


# ── static frontend with token injection ───────────────────────────────────────
def _index_html(name: str = os.path.join("terminal", "index.html")) -> str:
    with open(os.path.join(FRONTEND_DIR, name), encoding="utf-8") as f:
        html = f.read()
    tag = f'<meta name="local-token" content="{local_token}">'
    return html.replace("<head>", "<head>" + tag, 1) if "<head>" in html else tag + html


@app.middleware("http")
async def _no_cache_static(request: Request, call_next):
    """Static assets and the shell are revalidated on every load, so a restart never leaves a browser on an old terminal.js."""
    resp = await call_next(request)
    if request.url.path.startswith("/static/") or request.url.path in ("/", "/terminal"):
        resp.headers["Cache-Control"] = "no-cache, must-revalidate"
    return resp


if os.path.exists(FRONTEND_DIR):
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

    @app.get("/", response_class=HTMLResponse)
    def index():
        return HTMLResponse(_index_html(os.path.join("terminal", "index.html")), headers={"Content-Security-Policy": CSP})

    @app.get("/terminal", response_class=HTMLResponse)
    def terminal_page():
        return HTMLResponse(_index_html(os.path.join("terminal", "index.html")), headers={"Content-Security-Policy": CSP})

    @app.get("/classic", response_class=HTMLResponse)
    def classic_page():
        return RedirectResponse("/", status_code=307)

    @app.get("/{path:path}")
    def static_fallback(path: str):
        if path.startswith("api/"):
            raise HTTPException(404)
        full = os.path.realpath(os.path.join(FRONTEND_DIR, path))
        if os.path.commonpath([full, os.path.realpath(FRONTEND_DIR)]) == os.path.realpath(FRONTEND_DIR) and os.path.isfile(full):
            return FileResponse(full)
        return HTMLResponse(_index_html(), headers={"Content-Security-Policy": CSP})
