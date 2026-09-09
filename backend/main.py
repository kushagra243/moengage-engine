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
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
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
ALLOWED_SETTING_PREFIXES = ("moengage_", "llm_", "market_", "schedule_", "refresh_", "analysis_", "taxonomy_", "autopilot_", "devagent_", "mock_mode")


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
            "last_run": (latest or {}).get("created_at"), "last_summary": ((latest or {}).get("summary") or "")[:220], "days_of_history": snapshot_count(moe.mode)}


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
def _index_html() -> str:
    with open(os.path.join(FRONTEND_DIR, "index.html"), encoding="utf-8") as f:
        html = f.read()
    tag = f'<meta name="local-token" content="{local_token}">'
    return html.replace("<head>", "<head>" + tag, 1) if "<head>" in html else tag + html


if os.path.exists(FRONTEND_DIR):
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

    @app.get("/", response_class=HTMLResponse)
    def index():
        return HTMLResponse(_index_html(), headers={"Content-Security-Policy": CSP})

    @app.get("/{path:path}")
    def static_fallback(path: str):
        if path.startswith("api/"):
            raise HTTPException(404)
        full = os.path.realpath(os.path.join(FRONTEND_DIR, path))
        if os.path.commonpath([full, os.path.realpath(FRONTEND_DIR)]) == os.path.realpath(FRONTEND_DIR) and os.path.isfile(full):
            return FileResponse(full)
        return HTMLResponse(_index_html(), headers={"Content-Security-Policy": CSP})
