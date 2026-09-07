"""
crew_pipeline.py — CrewAI CLM Pipeline Orchestration

Defines the full multi-agent CLM crew and the task graph. Runs in
SEQUENTIAL process (no external LLM manager required — 100% local).

Pipeline stages:
  Task 1: collect_data       → DataCollectorAgent
  Task 2: audit_campaigns    → CampaignAuditorAgent (depends on Task 1)
  Task 3: strategise_segments → SegmentStrategistAgent (depends on Task 1)
  Task 4: generate_copy      → CopywriterAgent (depends on Task 2 + 3)
  Task 5: synthesise_board   → CLMOrchestratorAgent (depends on all above)
  Task 6: publish_actions    → PublisherAgent (depends on Task 5)
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Optional, Dict, Any

try:
    from crewai import Crew, Task, Process
    CREWAI_AVAILABLE = True
except ImportError:
    CREWAI_AVAILABLE = False

from .crew_agents import build_agents


# ──────────────────────────────────────────────────────────────────────────────
# Task definitions
# ──────────────────────────────────────────────────────────────────────────────

def build_tasks(agents: dict) -> list:
    """Build the ordered task list for the CLM pipeline."""
    if not CREWAI_AVAILABLE:
        raise RuntimeError("CrewAI not installed")

    # Task 1 — Data Collection
    task_collect = Task(
        description=(
            "Collect the current state of all MoEngage campaigns, user segments, and analytics. "
            "Use the fetch_campaigns, fetch_segments, and fetch_analytics tools. "
            "Summarise: total campaigns by status and channel, total segments with user counts, "
            "key metrics (DAU, MAU, push open rate, email CTR, 30-day retention). "
            "Output a structured JSON summary."
        ),
        expected_output=(
            "A structured JSON report with keys: campaigns_summary, segments_summary, analytics_summary."
        ),
        agent=agents["data_collector"],
    )

    # Task 2 — Campaign Audit
    task_audit = Task(
        description=(
            "Using the campaign data collected, audit every active campaign. "
            "Use the audit_campaigns tool. For each campaign identify: "
            "(1) verdict: good / warning / critical, "
            "(2) main performance issue if any, "
            "(3) top recommended action. "
            "Also identify which CLM lifecycle stages have ZERO campaign coverage."
        ),
        expected_output=(
            "A JSON audit report: list of campaigns with verdict + issue + action, "
            "and a list of uncovered lifecycle stages."
        ),
        agent=agents["campaign_auditor"],
        context=[task_collect],
    )

    # Task 3 — Segment Strategy
    task_segments = Task(
        description=(
            "Analyse the current segments and analytics. Use the discover_opportunities and "
            "clm_lifecycle_matrix tools. Identify: "
            "(1) top 3 new segment opportunities with user criteria, "
            "(2) lifecycle stages with weak segmentation coverage, "
            "(3) one specific segment to create right now with full filter criteria as JSON. "
            "Consider: dormant users (30D no open), high-value users (top 10% by engagement), "
            "re-engagement candidates (installed but never converted)."
        ),
        expected_output=(
            "A JSON strategy report: top_opportunities (list), weak_stages (list), "
            "segment_to_create (name, description, criteria as JSON)."
        ),
        agent=agents["segment_strategist"],
        context=[task_collect],
    )

    # Task 4 — Copy Generation
    task_copy = Task(
        description=(
            "Based on the audit findings and segment strategy, generate campaign copy "
            "for the TWO highest-priority lifecycle stages identified as critical or uncovered. "
            "Use the generate_draft_campaign tool for each stage. "
            "For each stage produce: push notification title+body (2 variants), "
            "email subject line (2 variants), and a recommended CTA. "
            "Make copy concise, personalized, and action-oriented."
        ),
        expected_output=(
            "A JSON copy report: list of stage drafts, each with stage_id, "
            "push_variants (list), email_variants (list), recommended_cta."
        ),
        agent=agents["copywriter"],
        context=[task_audit, task_segments],
    )

    # Task 5 — Board Synthesis
    task_orchestrate = Task(
        description=(
            "Synthesise all findings from data collection, campaign audit, segment strategy, "
            "and copy generation into a unified CLM Intelligence Board report. "
            "Use the run_daily_synthesis tool to persist the results. "
            "The report must include: "
            "(1) Executive Summary (3 bullet points), "
            "(2) Critical Alerts (campaigns or stages needing immediate action), "
            "(3) Top 5 Prioritised Actions with owner (agent name) and expected impact, "
            "(4) 30-day CLM roadmap outline, "
            "(5) Overall CLM health score (0-100)."
        ),
        expected_output=(
            "A structured JSON CLM Intelligence Board: executive_summary, critical_alerts, "
            "top_actions, roadmap, clm_health_score."
        ),
        agent=agents["clm_orchestrator"],
        context=[task_audit, task_segments, task_copy],
    )

    # Task 6 — Publish
    task_publish = Task(
        description=(
            "Execute the top publishing action from the CLM board synthesis: "
            "Use the create_segment tool to create the segment recommended by the Segment Strategist. "
            "Use the criteria JSON from the strategy task. "
            "After creating the segment, verify it appears in the segment list using fetch_segments. "
            "Report the new segment ID and confirmation status."
        ),
        expected_output=(
            "A JSON publish report: segment_created (name, id, user_count), "
            "next_steps (list of manual actions to complete in MoEngage)."
        ),
        agent=agents["publisher"],
        context=[task_segments, task_orchestrate],
    )

    return [task_collect, task_audit, task_segments, task_copy, task_orchestrate, task_publish]


# ──────────────────────────────────────────────────────────────────────────────
# CLM Crew
# ──────────────────────────────────────────────────────────────────────────────

class CLMCrew:
    """
    Orchestrates the full 6-agent CLM pipeline using CrewAI Sequential process.
    No external LLM required — all agents use deterministic local tools.
    """

    def __init__(self, llm=None):
        self.llm = llm
        self._agents = None
        self._tasks = None
        self._crew = None

    def _build(self):
        if self._crew:
            return
        self._agents = build_agents(llm=self.llm)
        self._tasks = build_tasks(self._agents)

        crew_kwargs: Dict[str, Any] = {
            "agents": list(self._agents.values()),
            "tasks": self._tasks,
            "process": Process.sequential,
            "verbose": True,
        }
        if self.llm:
            crew_kwargs["manager_llm"] = self.llm

        self._crew = Crew(**crew_kwargs)

    def run(self, dry_run: bool = False) -> Dict[str, Any]:
        """
        Execute the full CLM pipeline.
        Returns a structured result dict with per-task outputs and a final summary.
        """
        if not CREWAI_AVAILABLE:
            return self._fallback_run()

        self._build()

        start = time.time()
        timestamp = datetime.now().isoformat()

        try:
            # CrewAI kickoff — runs all tasks sequentially
            result = self._crew.kickoff()
            elapsed = round(time.time() - start, 1)

            # Parse final output
            final_output = str(result)
            try:
                parsed = json.loads(final_output)
            except Exception:
                parsed = {"raw_output": final_output}

            return {
                "success": True,
                "timestamp": timestamp,
                "elapsed_seconds": elapsed,
                "pipeline_stages": [
                    "data_collection", "campaign_audit", "segment_strategy",
                    "copy_generation", "board_synthesis", "publish"
                ],
                "agents_used": list(self._agents.keys()),
                "final_output": parsed,
                "message": f"CLM pipeline completed in {elapsed}s across 6 agents."
            }

        except Exception as e:
            return {
                "success": False,
                "timestamp": timestamp,
                "error": str(e),
                "message": "Pipeline run failed. Check agent tool connectivity."
            }

    def run_single_stage(self, stage: str) -> Dict[str, Any]:
        """
        Run a single pipeline stage by name without the full crew.
        Useful for targeted re-runs from the UI or CLI.
        stage: one of 'collect', 'audit', 'segments', 'copy', 'synthesise', 'publish'
        """
        stage_map = {
            "collect": ("data_collector", [FetchCampaignsTool, FetchSegmentsTool, FetchAnalyticsTool]),
            "audit": ("campaign_auditor", [AuditCampaignsTool]),
            "segments": ("segment_strategist", [DiscoverOpportunitiesTool, CLMMatrixTool]),
            "copy": ("copywriter", [GenerateDraftCampaignTool]),
            "synthesise": ("clm_orchestrator", [DailySynthesisTool, CLMMatrixTool]),
            "publish": ("publisher", [CreateSegmentTool]),
        }

        if stage not in stage_map:
            return {"success": False, "error": f"Unknown stage: {stage}. Valid: {list(stage_map.keys())}"}

        # Direct tool execution (no LLM overhead)
        from .clm_intelligence import CLMIntelligenceEngine
        from .moengage_client import MoEngageClient
        from .local_brain import LocalIntelligenceBrain

        try:
            if stage == "collect":
                client = MoEngageClient()
                return {
                    "success": True, "stage": stage,
                    "campaigns": client.get_campaigns(),
                    "segments": client.get_segments(),
                    "analytics": client.get_analytics_summary()
                }
            elif stage == "audit":
                engine = CLMIntelligenceEngine()
                return {"success": True, "stage": stage, "audit": engine.audit_active_campaigns()}
            elif stage == "segments":
                engine = CLMIntelligenceEngine()
                return {
                    "success": True, "stage": stage,
                    "matrix": engine.get_clm_lifecycle_matrix(),
                    "opportunities": engine.discover_what_else_can_be_done()
                }
            elif stage == "copy":
                engine = CLMIntelligenceEngine()
                return {"success": True, "stage": stage, "draft": engine.generate_draft_clm_campaign("at_risk")}
            elif stage == "synthesise":
                brain = LocalIntelligenceBrain()
                return {"success": True, "stage": stage, "synthesis": brain.run_daily_synthesis()}
            elif stage == "publish":
                client = MoEngageClient()
                result = client.create_segment(
                    name="CLM Auto-Segment: At-Risk Users",
                    description="Created by CLM PublisherAgent — users with declining engagement",
                    criteria={"days_inactive": {"gte": 14}, "push_enabled": True}
                )
                return {"success": True, "stage": stage, "segment_created": result}
        except Exception as e:
            return {"success": False, "stage": stage, "error": str(e)}

    def _fallback_run(self) -> Dict[str, Any]:
        """Fallback when CrewAI is unavailable — direct tool execution."""
        from .clm_intelligence import CLMIntelligenceEngine
        from .moengage_client import MoEngageClient
        from .local_brain import LocalIntelligenceBrain

        client = MoEngageClient()
        engine = CLMIntelligenceEngine()
        brain = LocalIntelligenceBrain()

        return {
            "success": True,
            "timestamp": datetime.now().isoformat(),
            "mode": "direct_fallback",
            "campaigns": client.get_campaigns(),
            "segments": client.get_segments(),
            "audit": engine.audit_active_campaigns(),
            "matrix": engine.get_clm_lifecycle_matrix(),
            "opportunities": engine.discover_what_else_can_be_done(),
            "synthesis": brain.run_daily_synthesis(),
            "message": "Pipeline ran in direct mode (CrewAI unavailable)."
        }


# Import tools at module level for run_single_stage usage
try:
    from .crew_tools import (
        FetchCampaignsTool, FetchSegmentsTool, FetchAnalyticsTool,
        AuditCampaignsTool, CLMMatrixTool, DiscoverOpportunitiesTool,
        GenerateDraftCampaignTool, CreateSegmentTool, DailySynthesisTool,
    )
except ImportError:
    pass
