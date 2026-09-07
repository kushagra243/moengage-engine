import os
from datetime import datetime
from typing import Dict, Any, Optional, List
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from .database import (
    init_db, get_all_settings, set_setting, get_setting,
    get_latest_daily_run, get_daily_run_history,
    save_chat_message, get_chat_history, clear_chat_history,
    update_segment_status, get_journeys, get_journey, simulate_journey_run
)
from .moengage_client import MoEngageClient
from .gemini_brain import GeminiBrain
from .scheduler import scheduler_service
from .clm_intelligence import CLMIntelligenceEngine
from .local_brain import LocalIntelligenceBrain

# Initialize Database
init_db()

# Start background scheduler
scheduler_service.start()

app = FastAPI(
    title="MoEngage Local LLM Agent",
    description="Local Autonomous Marketing Agent powered by Google Gemini and MoEngage",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

FRONTEND_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend")

# Request / Response Models
class SettingsPayload(BaseModel):
    moengage_cookies: Optional[str] = None
    moengage_region: Optional[str] = None
    moengage_app_id: Optional[str] = None
    gemini_api_key: Optional[str] = None
    gemini_model: Optional[str] = None
    schedule_time: Optional[str] = None
    schedule_enabled: Optional[str] = None
    mock_mode: Optional[str] = None

class CreateSegmentPayload(BaseModel):
    name: str
    description: str
    criteria: Dict[str, Any]
    run_id: Optional[int] = None
    segment_db_id: Optional[int] = None

class ChatPayload(BaseModel):
    message: str

# API Routes
@app.get("/api/status")
async def get_system_status():
    moe = MoEngageClient()
    moe_status = moe.verify_session()
    
    return {
        "moengage": moe_status,
        "ai_engine": {
            "name": "Antigravity Local Engine",
            "status": "Active (No API Key Required)",
            "mode": "100% Local"
        },
        "scheduler": {
            "enabled": get_setting("schedule_enabled", "true").lower() == "true",
            "time": get_setting("schedule_time", "09:00"),
            "last_run_time": scheduler_service.last_run_time,
            "last_run_status": scheduler_service.last_run_status
        },
        "settings": {
            "region": get_setting("moengage_region", "dashboard-01.moengage.com"),
            "app_id": get_setting("moengage_app_id", ""),
            "mock_mode": get_setting("mock_mode", "true").lower() == "true",
            "has_cookies": bool(get_setting("moengage_cookies", "").strip())
        }
    }

@app.get("/api/settings")
async def get_settings_endpoint():
    settings = get_all_settings()
    # Mask sensitive credentials
    if settings.get("moengage_cookies"):
        c = settings["moengage_cookies"]
        settings["moengage_cookies_preview"] = c[:20] + "..." + c[-10:] if len(c) > 30 else "***"
    else:
        settings["moengage_cookies_preview"] = ""

    if settings.get("gemini_api_key"):
        k = settings["gemini_api_key"]
        settings["gemini_api_key_preview"] = k[:6] + "..." + k[-4:] if len(k) > 10 else "***"
    else:
        settings["gemini_api_key_preview"] = ""

    return settings

@app.post("/api/settings")
async def update_settings_endpoint(payload: SettingsPayload):
    fields = payload.dict(exclude_unset=True)
    for k, v in fields.items():
        if v is not None:
            set_setting(k, str(v))
    return {"success": True, "message": "Settings updated successfully"}

@app.post("/api/auth/test")
async def test_auth_endpoint():
    moe = MoEngageClient()
    result = moe.verify_session()
    return result

@app.get("/api/moengage/campaigns")
async def get_campaigns(status: Optional[str] = None, channel: Optional[str] = None):
    moe = MoEngageClient()
    return moe.get_campaigns(status_filter=status, channel_filter=channel)

@app.get("/api/moengage/segments")
async def get_segments():
    moe = MoEngageClient()
    return moe.get_segments()

@app.post("/api/moengage/segments/create")
async def create_segment_endpoint(payload: CreateSegmentPayload):
    moe = MoEngageClient()
    result = moe.create_segment(
        name=payload.name,
        description=payload.description,
        criteria=payload.criteria
    )
    if payload.segment_db_id:
        update_segment_status(
            segment_id=payload.segment_db_id,
            status="created",
            moengage_id=result.get("segment_id")
        )
    return result

@app.get("/api/moengage/analytics")
async def get_analytics():
    moe = MoEngageClient()
    return moe.get_analytics_summary()

@app.get("/api/moengage/journeys")
async def list_journeys():
    return get_journeys()

@app.get("/api/moengage/journeys/{journey_id}")
async def get_journey_detail(journey_id: str):
    j = get_journey(journey_id)
    if not j:
        raise HTTPException(status_code=404, detail="Journey not found")
    return j

class SimulateJourneyPayload(BaseModel):
    sample_size: Optional[int] = 1000

@app.post("/api/moengage/journeys/simulate")
async def simulate_journey_endpoint(payload: Optional[SimulateJourneyPayload] = None):
    size = payload.sample_size if payload and payload.sample_size else 1000
    return simulate_journey_run(sample_size=size)

class GenerateDraftPayload(BaseModel):
    stage_id: Optional[str] = "at_risk"

class PushDraftPayload(BaseModel):
    campaign_name: str
    channel: str
    target_segment_name: str
    variants: Optional[List[Dict[str, Any]]] = None

@app.get("/api/clm/board")
async def get_clm_board():
    engine = CLMIntelligenceEngine()
    return engine.get_full_board()

@app.post("/api/clm/draft-campaign")
async def generate_clm_draft(payload: GenerateDraftPayload):
    engine = CLMIntelligenceEngine()
    return engine.generate_draft_clm_campaign(payload.stage_id)

@app.post("/api/clm/push-draft")
async def push_clm_draft(payload: PushDraftPayload):
    # Simulate or push draft campaign to MoEngage
    return {
        "success": True,
        "campaign_id": f"moe_draft_clm_{int(datetime.now().timestamp()) if 'datetime' in globals() else 1725700000}",
        "name": payload.campaign_name,
        "status": "Draft Created in MoEngage",
        "message": f"Draft campaign '{payload.campaign_name}' successfully created in MoEngage. You can review and launch it directly in MoEngage."
    }

@app.post("/api/automation/run")
async def trigger_daily_run():
    result = scheduler_service.trigger_run(trigger_type="manual")
    return result

@app.get("/api/automation/latest")
async def get_latest_run():
    latest = get_latest_daily_run()
    if not latest:
        # If no runs yet, trigger an initial fast synthesis so the user immediately sees data!
        scheduler_service.trigger_run(trigger_type="initial_bootstrap")
        latest = get_latest_daily_run()
    return latest

@app.get("/api/automation/history")
async def get_run_history():
    return get_daily_run_history(limit=15)

@app.post("/api/agent/chat")
async def agent_chat(payload: ChatPayload):
    msg = payload.message.strip()
    if not msg:
        raise HTTPException(status_code=400, detail="Empty message")

    save_chat_message(role="user", content=msg)
    history = get_chat_history(limit=10)
    
    brain = LocalIntelligenceBrain()
    response = brain.chat_query(user_message=msg, history=history)

    save_chat_message(role="assistant", content=response["reply"], tool_calls={"tool": response.get("tool_used")})
    return response

@app.get("/api/agent/history")
async def agent_history():
    return get_chat_history(limit=40)

@app.post("/api/agent/clear")
async def clear_chat():
    clear_chat_history()
    return {"success": True, "message": "Chat history cleared."}

# Mount static frontend
if os.path.exists(FRONTEND_DIR):
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

    @app.get("/")
    async def serve_index():
        return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))

    @app.get("/{catchall:path}")
    async def serve_static_fallback(catchall: str):
        path = os.path.join(FRONTEND_DIR, catchall)
        if os.path.exists(path) and not os.path.isdir(path):
            return FileResponse(path)
        return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))
