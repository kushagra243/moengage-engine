import threading
import time
from datetime import datetime
from typing import Dict, Any, Optional

from .database import get_setting, save_daily_run, get_latest_daily_run
from .local_brain import LocalIntelligenceBrain

class DailyAutomationScheduler:
    def __init__(self):
        self.is_running = False
        self.thread: Optional[threading.Thread] = None
        self.last_run_time: Optional[str] = None
        self.last_run_status: str = "Idle"
        self._lock = threading.Lock()

    def start(self):
        with self._lock:
            if self.is_running:
                return
            self.is_running = True
            self.thread = threading.Thread(target=self._scheduler_loop, daemon=True)
            self.thread.start()
            print("[Scheduler] Daily automation scheduler started.")

    def stop(self):
        with self._lock:
            self.is_running = False

    def trigger_run(self, trigger_type: str = "manual") -> Dict[str, Any]:
        """Execute the daily intelligence process immediately"""
        print(f"[Scheduler] Triggering intelligence run (type: {trigger_type})...")
        self.last_run_status = "Running"
        try:
            brain = LocalIntelligenceBrain()
            report = brain.run_daily_synthesis()
            summary = report.get("executive_summary", "Daily synthesis complete.")
            run_id = save_daily_run(trigger_type=trigger_type, summary=summary, report_data=report)
            self.last_run_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.last_run_status = "Completed"
            return {
                "success": True,
                "run_id": run_id,
                "summary": summary,
                "report": report
            }
        except Exception as e:
            self.last_run_status = f"Error: {e}"
            print(f"[Scheduler] Error during daily run: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    def _scheduler_loop(self):
        last_checked_minute = None
        while self.is_running:
            try:
                enabled = get_setting("schedule_enabled", "true").lower() == "true"
                sched_time = get_setting("schedule_time", "09:00").strip()
                now = datetime.now()
                current_time_str = now.strftime("%H:%M")

                if enabled and current_time_str == sched_time and last_checked_minute != current_time_str:
                    last_checked_minute = current_time_str
                    print(f"[Scheduler] Scheduled time {sched_time} reached. Running daily process...")
                    self.trigger_run(trigger_type="scheduled")

                # Sleep 30 seconds before re-checking
                time.sleep(30)
            except Exception as e:
                print(f"[Scheduler] Exception in loop: {e}")
                time.sleep(60)

# Global singleton
scheduler_service = DailyAutomationScheduler()
