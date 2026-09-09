"""
Daily automation: snapshot campaign metrics → detect anomalies → refresh market
context → run the agent's daily brief (if an LLM is configured) → persist.
Runs in a daemon thread; also triggerable from the UI/CLI.
"""
from __future__ import annotations
import logging
import threading
import time
from datetime import datetime
from typing import Any, Dict, Optional

from .database import get_setting, save_daily_run
from .security import redact

log = logging.getLogger("moengage.scheduler")


class DailyAutomationScheduler:
    def __init__(self):
        self.is_running = False
        self.thread: Optional[threading.Thread] = None
        self.last_run_time: Optional[str] = None
        self.last_run_status: str = "Idle"
        self._lock = threading.Lock()
        self._run_lock = threading.Lock()

    def start(self):
        with self._lock:
            if self.is_running:
                return
            self.is_running = True
            self.thread = threading.Thread(target=self._loop, daemon=True, name="daily-scheduler")
            self.thread.start()
            log.info("scheduler started")

    def stop(self):
        with self._lock:
            self.is_running = False

    def trigger_run(self, trigger_type: str = "manual", use_llm: Optional[bool] = None) -> Dict[str, Any]:
        if not self._run_lock.acquire(blocking=False):
            return {"success": False, "error": "a run is already in progress"}
        self.last_run_status = "Running"
        try:
            from .moengage import MoEngageClient, DataUnavailable
            from .anomaly import record_snapshot, detect_anomalies, snapshot_count
            from .market import market_context
            from .llm.agent import MarketerAgent
            from .llm.provider import llm_settings

            client = MoEngageClient()
            report: Dict[str, Any] = {"trigger": trigger_type, "mode": client.mode, "started_at": datetime.now().isoformat(), "steps": []}

            # 1) snapshot + anomalies
            try:
                campaigns = client.get_campaigns()
                snap = record_snapshot(campaigns, source=client.mode)
                report["snapshot"] = snap
                report["anomalies"] = detect_anomalies(source=client.mode)
                report["steps"].append("snapshot+anomalies")
            except DataUnavailable as e:
                report["snapshot_error"] = str(e)
            except Exception as e:
                report["snapshot_error"] = redact(str(e))

            # 2) market context (never fatal)
            try:
                report["market"] = market_context(force=False)
                report["steps"].append("market")
            except Exception as e:
                report["market_error"] = redact(str(e))

            # 3) growth feed (rules; no model needed)
            try:
                from .growth import generate_rule_ideas
                report["growth"] = generate_rule_ideas()
                report["steps"].append("growth_rules")
            except Exception as e:
                report["growth_error"] = redact(str(e))

            # 3b) deep dives for campaigns needing attention + largest (bulk model tier; deterministic without a model)
            try:
                from .analysis import analyse_priority
                report["deep_dives"] = analyse_priority(limit=int(get_setting("analysis_batch", "8") or 8))
                report["steps"].append("deep_dives")
            except Exception as e:
                report["deep_dives_error"] = redact(str(e))

            # 4) agent brief + agent ideas (only when a model is configured)
            cfg = llm_settings()
            want_llm = (cfg["api_key"] or cfg["provider"] == "claude_cli") if use_llm is None else use_llm
            if want_llm:
                try:
                    agent = MarketerAgent()
                    brief = agent.daily_brief(report)
                    report["brief"] = brief
                    report["executive_summary"] = brief.get("executive_summary") or brief.get("text", "")[:600]
                    report["steps"].append("agent_brief")
                    try:
                        from .growth import generate_agent_ideas
                        report["growth_agent"] = generate_agent_ideas(agent, n=5)
                        report["steps"].append("growth_agent")
                    except Exception as e:
                        report["growth_agent_error"] = redact(str(e))
                except Exception as e:
                    report["brief_error"] = redact(str(e))
            if not report.get("executive_summary"):
                a = report.get("anomalies") or {}
                report["executive_summary"] = (
                    f"Snapshot recorded for {len(campaigns) if 'campaigns' in dir() else 0} campaigns ({client.mode}). "
                    f"{a.get('critical', 0)} critical / {a.get('warnings', 0)} warning anomalies. "
                    + ("Configure an LLM key in Settings to get the agent's written brief." if not want_llm else "")
                )
            report["finished_at"] = datetime.now().isoformat()
            run_id = save_daily_run(trigger_type=trigger_type, summary=report["executive_summary"], report_data=report)
            self.last_run_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.last_run_status = "Completed"
            return {"success": True, "run_id": run_id, "summary": report["executive_summary"], "report": report}
        except Exception as e:
            self.last_run_status = f"Error: {redact(str(e))}"
            log.exception("daily run failed")
            return {"success": False, "error": redact(str(e))}
        finally:
            self._run_lock.release()

    def refresh_intraday(self) -> Dict[str, Any]:
        """Lightweight, model-free refresh: re-read campaigns, snapshot, detect, market, rules ideas."""
        return self.trigger_run(trigger_type="intraday", use_llm=False)

    def _loop(self):
        last_minute = None
        last_intraday = time.time()
        while self.is_running:
            try:
                enabled = get_setting("schedule_enabled", "true").lower() == "true"
                sched = get_setting("schedule_time", "09:00").strip()
                every_h = float(get_setting("refresh_interval_hours", "6") or 6)
                now = datetime.now().strftime("%H:%M")
                if enabled and now == sched and last_minute != now:
                    last_minute = now
                    self.trigger_run(trigger_type="scheduled")
                    last_intraday = time.time()
                elif enabled and every_h > 0 and time.time() - last_intraday >= every_h * 3600:
                    last_intraday = time.time()
                    self.refresh_intraday()
                time.sleep(30)
            except Exception as e:
                log.warning("scheduler loop error: %s", redact(str(e)))
                time.sleep(60)


scheduler_service = DailyAutomationScheduler()
