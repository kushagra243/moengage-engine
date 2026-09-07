"""
crew_agents.py — CrewAI Agent Definitions for MoEngage CLM Pipeline

Agent Hierarchy:
  1. DataCollectorAgent    — pulls live data from MoEngage (campaigns, segments, analytics)
  2. CampaignAuditorAgent  — audits campaigns for performance gaps
  3. SegmentStrategistAgent — discovers new segment opportunities & proposes creation
  4. CopywriterAgent        — generates A/B campaign copy for targeted CLM stages
  5. CLMOrchestratorAgent   — synthesises the full lifecycle matrix, surfaces gaps
  6. PublisherAgent          — pushes approved segments/drafts to MoEngage

All agents use the local CLMIntelligenceEngine & MoEngageClient as tools —
zero API keys required.
"""
from __future__ import annotations

try:
    from crewai import Agent
    CREWAI_AVAILABLE = True
except ImportError:
    CREWAI_AVAILABLE = False
    Agent = None  # type: ignore

from .crew_tools import (
    FetchCampaignsTool, FetchSegmentsTool, FetchAnalyticsTool,
    AuditCampaignsTool, CLMMatrixTool, DiscoverOpportunitiesTool,
    GenerateDraftCampaignTool, CreateSegmentTool, DailySynthesisTool,
)


def build_agents(llm=None):
    """
    Build and return all 6 CLM agents.
    llm: optional LangChain-compatible LLM. If None, agents use rule-based tools
         with no external model calls (fully local mode).
    """
    if not CREWAI_AVAILABLE:
        raise RuntimeError("CrewAI is not installed. Run: pip install crewai")

    agent_kwargs = {}
    if llm:
        agent_kwargs["llm"] = llm

    # ── 1. Data Collector ──────────────────────────────────────────────────────
    data_collector = Agent(
        role="MoEngage Data Collector",
        goal=(
            "Gather all available data from MoEngage: campaigns, segments, and analytics. "
            "Compile a comprehensive data snapshot that other agents can act on."
        ),
        backstory=(
            "You are a data-fetching specialist with deep knowledge of MoEngage APIs. "
            "You retrieve accurate, real-time campaign and segment data to power the CLM pipeline."
        ),
        tools=[FetchCampaignsTool(), FetchSegmentsTool(), FetchAnalyticsTool()],
        verbose=True,
        allow_delegation=False,
        **agent_kwargs,
    )

    # ── 2. Campaign Auditor ────────────────────────────────────────────────────
    campaign_auditor = Agent(
        role="CLM Campaign Auditor",
        goal=(
            "Audit every active campaign and identify performance gaps, under-performing sends, "
            "low open-rate alerts, and campaigns that need immediate action."
        ),
        backstory=(
            "You are a senior growth analyst specialising in CRM/CLM campaign performance. "
            "You look at open rates, click rates, send volumes, and lifecycle stage coverage "
            "to identify exactly what is working and what needs to be fixed."
        ),
        tools=[AuditCampaignsTool(), FetchCampaignsTool()],
        verbose=True,
        allow_delegation=False,
        **agent_kwargs,
    )

    # ── 3. Segment Strategist ──────────────────────────────────────────────────
    segment_strategist = Agent(
        role="User Segment Strategist",
        goal=(
            "Discover untapped user segments, analyse lifecycle coverage gaps, "
            "and recommend new high-value segments with precise filter criteria."
        ),
        backstory=(
            "You are a customer segmentation expert who builds revenue-generating cohorts. "
            "You cross-reference analytics, existing segments, and lifecycle stages "
            "to surface opportunities that no human analyst would easily find."
        ),
        tools=[FetchSegmentsTool(), FetchAnalyticsTool(), DiscoverOpportunitiesTool(), CLMMatrixTool()],
        verbose=True,
        allow_delegation=False,
        **agent_kwargs,
    )

    # ── 4. Copywriter ─────────────────────────────────────────────────────────
    copywriter = Agent(
        role="CLM Campaign Copywriter",
        goal=(
            "Generate compelling, personalized A/B campaign copy for each lifecycle stage. "
            "Create push notification text, email subject lines, SMS copy, and in-app messages "
            "with strong CTAs tailored to the user's stage."
        ),
        backstory=(
            "You are an expert marketing copywriter with 10 years of experience in "
            "retention and re-engagement campaigns. You write copy that converts — "
            "short, punchy, and highly relevant to the user's current relationship with the app."
        ),
        tools=[GenerateDraftCampaignTool(), CLMMatrixTool()],
        verbose=True,
        allow_delegation=False,
        **agent_kwargs,
    )

    # ── 5. CLM Orchestrator ───────────────────────────────────────────────────
    clm_orchestrator = Agent(
        role="CLM Pipeline Orchestrator",
        goal=(
            "Synthesise all agent findings into a unified CLM intelligence board. "
            "Identify the most critical lifecycle gaps, rank opportunities by impact, "
            "and produce a prioritised action plan for the marketing team."
        ),
        backstory=(
            "You are the Head of CRM Strategy. You take inputs from data, audit, segmentation, "
            "and copy teams, then synthesise them into clear, actionable recommendations "
            "that maximise user lifetime value and minimise churn."
        ),
        tools=[CLMMatrixTool(), DiscoverOpportunitiesTool(), AuditCampaignsTool(), DailySynthesisTool()],
        verbose=True,
        allow_delegation=True,  # Can delegate back to specialist agents
        **agent_kwargs,
    )

    # ── 6. Publisher ──────────────────────────────────────────────────────────
    publisher = Agent(
        role="MoEngage Campaign Publisher",
        goal=(
            "Take approved segment definitions and campaign drafts and push them "
            "directly to MoEngage. Confirm creation and report back segment IDs and campaign status."
        ),
        backstory=(
            "You are a MoEngage platform expert who executes with precision. "
            "You create segments, validate criteria, and push campaign drafts, "
            "ensuring every action is recorded and confirmed."
        ),
        tools=[CreateSegmentTool(), FetchSegmentsTool()],
        verbose=True,
        allow_delegation=False,
        **agent_kwargs,
    )

    return {
        "data_collector": data_collector,
        "campaign_auditor": campaign_auditor,
        "segment_strategist": segment_strategist,
        "copywriter": copywriter,
        "clm_orchestrator": clm_orchestrator,
        "publisher": publisher,
    }
