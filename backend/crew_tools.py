"""
crew_tools.py — CrewAI Tool Definitions for MoEngage CLM Agents
Each function here is wrapped as a CrewAI BaseTool so individual agents
can call them during the pipeline run.
"""
from __future__ import annotations
import json
from typing import Optional, Type, Any

try:
    from crewai.tools import BaseTool
    from pydantic import BaseModel, Field
    CREWAI_AVAILABLE = True
except ImportError:
    CREWAI_AVAILABLE = False
    BaseTool = object
    class BaseModel:
        pass
    Field = lambda *a, **kw: None

# ──────────────────────────────────────────────────────────────
# Input Schemas (pydantic v2 compatible)
# ──────────────────────────────────────────────────────────────

class EmptyInput(BaseModel):
    pass

class StageInput(BaseModel):
    stage_id: str = Field(default="at_risk", description="CLM lifecycle stage id, e.g. 'at_risk', 'new_users', 'champions'")

class SegmentInput(BaseModel):
    name: str = Field(..., description="Segment name")
    description: str = Field(default="", description="Segment description")
    criteria: str = Field(default="{}", description="JSON string of segment criteria")


# ──────────────────────────────────────────────────────────────
# Tool: Fetch Campaigns
# ──────────────────────────────────────────────────────────────

class FetchCampaignsTool(BaseTool):
    name: str = "fetch_campaigns"
    description: str = (
        "Fetches all active and paused campaigns from MoEngage. "
        "Returns a JSON list of campaigns with status, channel, send count, and open rate."
    )
    args_schema: Type[BaseModel] = EmptyInput

    def _run(self, **kwargs) -> str:
        from .moengage_client import MoEngageClient
        client = MoEngageClient()
        data = client.get_campaigns()
        return json.dumps(data, indent=2)


# ──────────────────────────────────────────────────────────────
# Tool: Fetch Segments
# ──────────────────────────────────────────────────────────────

class FetchSegmentsTool(BaseTool):
    name: str = "fetch_segments"
    description: str = (
        "Fetches all user segments from MoEngage. "
        "Returns a JSON list of segments with user counts and creation dates."
    )
    args_schema: Type[BaseModel] = EmptyInput

    def _run(self, **kwargs) -> str:
        from .moengage_client import MoEngageClient
        client = MoEngageClient()
        data = client.get_segments()
        return json.dumps(data, indent=2)


# ──────────────────────────────────────────────────────────────
# Tool: Fetch Analytics
# ──────────────────────────────────────────────────────────────

class FetchAnalyticsTool(BaseTool):
    name: str = "fetch_analytics"
    description: str = (
        "Fetches a 30-day analytics summary from MoEngage: DAU, MAU, retention, "
        "channel performance (push, email, SMS, in-app), and revenue metrics."
    )
    args_schema: Type[BaseModel] = EmptyInput

    def _run(self, **kwargs) -> str:
        from .moengage_client import MoEngageClient
        client = MoEngageClient()
        data = client.get_analytics_summary()
        return json.dumps(data, indent=2)


# ──────────────────────────────────────────────────────────────
# Tool: Audit Campaigns
# ──────────────────────────────────────────────────────────────

class AuditCampaignsTool(BaseTool):
    name: str = "audit_campaigns"
    description: str = (
        "Audits all active campaigns using CLM intelligence. "
        "Returns verdict (good/warning/critical), performance notes, and recommended actions for each campaign."
    )
    args_schema: Type[BaseModel] = EmptyInput

    def _run(self, **kwargs) -> str:
        from .clm_intelligence import CLMIntelligenceEngine
        engine = CLMIntelligenceEngine()
        data = engine.audit_active_campaigns()
        return json.dumps(data, indent=2)


# ──────────────────────────────────────────────────────────────
# Tool: CLM Lifecycle Matrix
# ──────────────────────────────────────────────────────────────

class CLMMatrixTool(BaseTool):
    name: str = "clm_lifecycle_matrix"
    description: str = (
        "Returns the 5-stage CLM lifecycle matrix (New Users, Active, Champions, At Risk, Churned). "
        "Each stage shows coverage status, active campaigns, and recommended actions."
    )
    args_schema: Type[BaseModel] = EmptyInput

    def _run(self, **kwargs) -> str:
        from .clm_intelligence import CLMIntelligenceEngine
        engine = CLMIntelligenceEngine()
        data = engine.get_clm_lifecycle_matrix()
        return json.dumps(data, indent=2)


# ──────────────────────────────────────────────────────────────
# Tool: Discover Opportunities
# ──────────────────────────────────────────────────────────────

class DiscoverOpportunitiesTool(BaseTool):
    name: str = "discover_opportunities"
    description: str = (
        "Discovers high-impact CLM opportunities not yet being actioned — "
        "missing lifecycle coverage, untapped channels, and new segment ideas. "
        "Returns a prioritised list with effort and impact ratings."
    )
    args_schema: Type[BaseModel] = EmptyInput

    def _run(self, **kwargs) -> str:
        from .clm_intelligence import CLMIntelligenceEngine
        engine = CLMIntelligenceEngine()
        data = engine.discover_what_else_can_be_done()
        return json.dumps(data, indent=2)


# ──────────────────────────────────────────────────────────────
# Tool: Generate Draft Campaign
# ──────────────────────────────────────────────────────────────

class GenerateDraftCampaignTool(BaseTool):
    name: str = "generate_draft_campaign"
    description: str = (
        "Generates a ready-to-push draft campaign for a given CLM lifecycle stage. "
        "Input: stage_id (e.g. 'at_risk', 'new_users', 'churned', 'champions', 'active'). "
        "Returns campaign name, channel, A/B variants with copy, CTAs, and targeting criteria."
    )
    args_schema: Type[BaseModel] = StageInput

    def _run(self, stage_id: str = "at_risk", **kwargs) -> str:
        from .clm_intelligence import CLMIntelligenceEngine
        engine = CLMIntelligenceEngine()
        data = engine.generate_draft_clm_campaign(stage_id)
        return json.dumps(data, indent=2)


# ──────────────────────────────────────────────────────────────
# Tool: Create Segment in MoEngage
# ──────────────────────────────────────────────────────────────

class CreateSegmentTool(BaseTool):
    name: str = "create_segment"
    description: str = (
        "Creates a new user segment in MoEngage. "
        "Input: name (string), description (string), criteria (JSON string of filter rules). "
        "Returns the created segment ID and confirmation."
    )
    args_schema: Type[BaseModel] = SegmentInput

    def _run(self, name: str, description: str = "", criteria: str = "{}", **kwargs) -> str:
        from .moengage_client import MoEngageClient
        client = MoEngageClient()
        try:
            criteria_dict = json.loads(criteria)
        except Exception:
            criteria_dict = {}
        result = client.create_segment(name=name, description=description, criteria=criteria_dict)
        return json.dumps(result, indent=2)


# ──────────────────────────────────────────────────────────────
# Tool: Run Daily Synthesis
# ──────────────────────────────────────────────────────────────

class DailySynthesisTool(BaseTool):
    name: str = "run_daily_synthesis"
    description: str = (
        "Runs the full daily CLM intelligence synthesis: collects data, identifies trends, "
        "generates segment recommendations, and stores results for the dashboard."
    )
    args_schema: Type[BaseModel] = EmptyInput

    def _run(self, **kwargs) -> str:
        from .local_brain import LocalIntelligenceBrain
        brain = LocalIntelligenceBrain()
        result = brain.run_daily_synthesis()
        return json.dumps(result, indent=2)


# ──────────────────────────────────────────────────────────────
# Registry — all tools exported for easy import
# ──────────────────────────────────────────────────────────────

def get_all_tools():
    """Return all CrewAI tool instances."""
    if not CREWAI_AVAILABLE:
        return []
    return [
        FetchCampaignsTool(),
        FetchSegmentsTool(),
        FetchAnalyticsTool(),
        AuditCampaignsTool(),
        CLMMatrixTool(),
        DiscoverOpportunitiesTool(),
        GenerateDraftCampaignTool(),
        CreateSegmentTool(),
        DailySynthesisTool(),
    ]
