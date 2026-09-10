"""
Refresh engine — the local "cron" that keeps every panel current without a
model call. Each job has an interval (minutes; override per job with the
setting `refresh_<job>_min`, or pause everything with `refresh_enabled=false`),
runs inside the scheduler thread when due, and records its last run so the
terminal can show freshness and the team can trigger a job by hand.

Jobs (defaults):
  prices            1   liquidity-venue mark prices patch the cached picture in place (real time)
  market_context   10   full market picture: prices, regime, movers, listings, OI, funding, news, calendar, competitors snapshot, web3, hooks, tier-0
  signals           5   Signal Bridge evaluation (approved rules → business events)
  benchmarks       30   category leaderboards and gap-to-leader targets
  campaign_intel   30   competitor campaigns (announcements, news) + counters
  onchain_cex      30   Hyperliquid vs centralised, per-coin OI/funding
  money_flow       30   stablecoins, sector rotation, venue share, attention, leverage (+ daily history rows)
  app_rankings     60   App Store finance ranks / versions
  structural       60   structural gap audit → P0 ideas in the feed
  qa               60   fact checks + safe improvements
  workspace        60   programme facts (narrative reused until inputs change)
  housekeeping     60   expire stale ideas and proposals
  compliance      180   live-copy compliance sweep
"""
from __future__ import annotations
import json
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from .database import get_db, get_setting
from .security import redact

_lock = threading.Lock()
_running: Dict[str, float] = {}
_state: Dict[str, Dict[str, Any]] = {}


def _j_prices():
    """One venue call per minute. Skips when the full picture was just rebuilt (prices already fresh) and treats a venue rate limit as a soft skip, not a failure."""
    from .market.context import patch_prices
    mc = _state.get("market_context") or {}
    last = _ts(mc.get("last_finished"))
    if last and time.time() - last < 90:
        return {"skipped": "market_context rebuilt < 90 s ago"}
    try:
        return patch_prices()
    except Exception as e:
        if "rate limit" in str(e).lower() or "429" in str(e):
            return {"skipped": "venue rate limited; next minute"}
        raise


def _j_market_context():
    from .market import market_context
    c = market_context(force=True)
    return {"assets": len(c.get("crypto_markets") or []), "regime": ((c.get("crypto") or {}).get("regime") or {}).get("label"), "tier0": bool(c.get("tier0"))}


def _j_signals():
    from . import signals
    r = signals.evaluate()
    return {"fired": r.get("fired"), "regime": r.get("regime")}


def _j_benchmarks():
    from .market.benchmarks import benchmarks
    b = benchmarks(force=True)
    return {"categories": list((b.get("categories") or {}).keys())}


def _j_campaign_intel():
    from .market.campaign_intel import campaigns
    c = campaigns(force=True)
    return {"campaigns_48h": len(c.get("campaigns") or c.get("items") or []) if isinstance(c, dict) else 0}


def _j_onchain_cex():
    from .market.onchain_cex import compare
    o = compare(force=True)
    return {"hl_rank": (o.get("hl_vs_cex") or {}).get("rank_if_listed_with_cex")}


def _j_money_flow():
    from .market import moneyflow
    from .market.context import _latest
    f = moneyflow.flow(_latest(6 * 3600) or {})
    return {"risk": (f.get("risk_appetite") or {}).get("score"), "history_days": f.get("history_days")}


def _j_app_rankings():
    from .market.campaign_intel import app_rankings
    a = app_rankings()
    return {"apps": len(a.get("apps") or a or {}) if isinstance(a, dict) else 0}


def _j_structural():
    from . import structural
    return structural.sync_to_feed()


def _j_qa():
    from . import qa
    r = qa.run()
    return {"score": r.get("score"), **(r.get("counts") or {})}


def _j_workspace():
    from . import workspace_analysis
    r = workspace_analysis.report(force=False, want_model=False)
    return {"campaigns": (r.get("programme") or {}).get("campaigns"), "tier": (r.get("narrative_meta") or {}).get("tier")}


def _j_housekeeping():
    from . import growth, approvals
    a = growth.expire_stale(); b = approvals.expire_stale()
    return {"ideas": a, "proposals": b if isinstance(b, (int, dict)) else str(b)[:80]}


def _j_research():
    from . import research
    r = research.refresh()
    return {"added": r.get("added"), "seen": r.get("seen"), "errors": len(r.get("errors") or [])}


def _j_council():
    """Review council over pending campaign drafts that have not been reviewed yet (max 3 per run; bulk tier)."""
    from . import approvals
    from .llm.roster import council_review, council_summary
    from .llm.provider import llm_settings
    cfg = llm_settings()
    if not (cfg["api_key"] or cfg["provider"] == "claude_cli"):
        return {"skipped": "no model"}
    done = []
    for p in approvals.list_proposals(status="pending", limit=100):
        if p["kind"] not in ("create_campaign", "create_flow", "signal_rule", "skill_update") or council_summary(p):
            continue
        r = council_review(p["id"])
        done.append({"id": p["id"], "overall": r.get("overall")})
        if len(done) >= 3:
            break
    return {"reviewed": done}


def _j_compliance():
    from . import compliance_sweep
    return compliance_sweep.sweep().get("counts")


JOBS: List[Dict[str, Any]] = [
    {"name": "prices", "minutes": 1, "fn": _j_prices, "feeds": "Brain Lab prices · movers · OI · funding (real time)"},
    {"name": "market_context", "minutes": 10, "fn": _j_market_context, "feeds": "Brain Lab · Brain · signals (full picture incl. news, listings, competitors)"},
    {"name": "signals", "minutes": 5, "fn": _j_signals, "feeds": "Signal Bridge → MoEngage business events"},
    {"name": "benchmarks", "minutes": 15, "fn": _j_benchmarks, "feeds": "Brain Lab → Comparison"},
    {"name": "campaign_intel", "minutes": 15, "fn": _j_campaign_intel, "feeds": "Brain Lab → Competitors (their campaigns)"},
    {"name": "onchain_cex", "minutes": 15, "fn": _j_onchain_cex, "feeds": "Brain Lab → On-chain vs CEX"},
    {"name": "money_flow", "minutes": 15, "fn": _j_money_flow, "feeds": "Brain Lab → Money flow · Ideas recommendations"},
    {"name": "app_rankings", "minutes": 60, "fn": _j_app_rankings, "feeds": "Brain Lab → App Store"},
    {"name": "structural", "minutes": 30, "fn": _j_structural, "feeds": "Ideas → P0 structural"},
    {"name": "qa", "minutes": 30, "fn": _j_qa, "feeds": "Brain → QA panel"},
    {"name": "workspace", "minutes": 15, "fn": _j_workspace, "feeds": "Analysis module (facts every run; narrative when inputs change)"},
    {"name": "housekeeping", "minutes": 60, "fn": _j_housekeeping, "feeds": "Ideas board (expiry)"},
    {"name": "compliance", "minutes": 180, "fn": _j_compliance, "feeds": "Analysis → Compliance sweep · QA"},
    {"name": "council", "minutes": 30, "fn": _j_council, "feeds": "Ideas → council verdicts on new drafts"},
    {"name": "research", "minutes": 360, "fn": _j_research, "feeds": "Skills → Methodology radar"},
]


def init_tables() -> None:
    conn = get_db()
    conn.execute("""CREATE TABLE IF NOT EXISTS refresh_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, job TEXT, started_at TIMESTAMP, finished_at TIMESTAMP, ok INTEGER, ms INTEGER, summary TEXT, error TEXT)""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_refresh_job ON refresh_runs(job, id)")
    conn.commit(); conn.close()


def interval_min(job: Dict[str, Any]) -> float:
    try:
        return float(get_setting(f"refresh_{job['name']}_min", str(job["minutes"])) or job["minutes"])
    except Exception:
        return float(job["minutes"])


def _load_state() -> None:
    if _state:
        return
    try:
        init_tables(); conn = get_db()
        for j in JOBS:
            r = conn.execute("SELECT * FROM refresh_runs WHERE job=? ORDER BY id DESC LIMIT 1", (j["name"],)).fetchone()
            if r:
                _state[j["name"]] = {"last_started": r["started_at"], "last_finished": r["finished_at"], "ok": bool(r["ok"]), "ms": r["ms"], "summary": json.loads(r["summary"] or "null"), "error": r["error"]}
        conn.close()
    except Exception:
        pass


def _ts(s: Optional[str]) -> Optional[float]:
    if not s:
        return None
    try:
        d = datetime.fromisoformat(str(s).replace(" ", "T").replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.timestamp()
    except Exception:
        return None


def run_job(name: str) -> Dict[str, Any]:
    job = next((j for j in JOBS if j["name"] == name), None)
    if not job:
        return {"ok": False, "error": f"unknown job {name}", "jobs": [j["name"] for j in JOBS]}
    if name in _running:
        return {"ok": False, "error": "already running", "since_s": round(time.time() - _running[name])}
    _running[name] = time.time(); started = datetime.now(timezone.utc); t0 = time.time(); ok, summary, error = True, None, None
    try:
        summary = job["fn"]()
    except Exception as e:
        ok, error = False, redact(str(e))[:300]
    finally:
        _running.pop(name, None)
    ms = int((time.time() - t0) * 1000); finished = datetime.now(timezone.utc)
    _state[name] = {"last_started": started.isoformat(), "last_finished": finished.isoformat(), "ok": ok, "ms": ms, "summary": summary, "error": error}
    try:
        init_tables(); conn = get_db()
        conn.execute("INSERT INTO refresh_runs (job, started_at, finished_at, ok, ms, summary, error) VALUES (?,?,?,?,?,?,?)", (name, started.isoformat(), finished.isoformat(), 1 if ok else 0, ms, json.dumps(summary, default=str)[:2000], error))
        conn.execute("DELETE FROM refresh_runs WHERE id NOT IN (SELECT id FROM refresh_runs ORDER BY id DESC LIMIT 2000)")
        conn.commit(); conn.close()
    except Exception:
        pass
    return {"ok": ok, "job": name, "ms": ms, "summary": summary, "error": error}


def due_jobs(now: Optional[float] = None) -> List[str]:
    _load_state(); now = now or time.time()
    if get_setting("refresh_enabled", "true").lower() != "true":
        return []
    out = []
    for j in JOBS:
        st = _state.get(j["name"]) or {}
        last = _ts(st.get("last_started"))
        if j["name"] in _running:
            continue
        if last is None or now - last >= interval_min(j) * 60:
            out.append(j["name"])
    return out


def run_due(max_jobs: int = 3) -> List[Dict[str, Any]]:
    """Called by the scheduler loop every tick; runs at most `max_jobs` due jobs (market_context first) so one slow source never blocks the rest for long."""
    if not _lock.acquire(blocking=False):
        return []
    try:
        due = due_jobs()
        order = {j["name"]: i for i, j in enumerate(JOBS)}
        due.sort(key=lambda n: order.get(n, 99))
        return [run_job(n) for n in due[:max_jobs]]
    finally:
        _lock.release()


VIEWS_OF = {"prices": ["lab", "brain"], "market_context": ["lab", "brain", "ideas"], "signals": ["engine"], "benchmarks": ["lab"], "campaign_intel": ["lab"], "onchain_cex": ["lab"], "money_flow": ["lab", "ideas"], "app_rankings": ["lab"],
            "structural": ["ideas"], "qa": ["brain"], "workspace": ["analysis"], "housekeeping": ["ideas"], "compliance": ["analysis", "brain"], "council": ["ideas"], "research": ["skills"]}


def changes() -> Dict[str, Any]:
    """Tiny payload the terminal polls every few seconds: when each job last finished and which modules it feeds."""
    _load_state()
    return {"now": datetime.now(timezone.utc).isoformat(), "jobs": {j["name"]: {"finished": (_state.get(j["name"]) or {}).get("last_finished"), "ok": (_state.get(j["name"]) or {}).get("ok"), "views": VIEWS_OF.get(j["name"], [])} for j in JOBS}, "running": sorted(_running.keys())}


def status() -> Dict[str, Any]:
    _load_state(); now = time.time()
    rows = []
    for j in JOBS:
        st = _state.get(j["name"]) or {}
        last = _ts(st.get("last_started")); iv = interval_min(j)
        age = round((now - last) / 60, 1) if last else None
        rows.append({"job": j["name"], "feeds": j["feeds"], "interval_min": iv, "last_started": st.get("last_started"), "age_min": age, "next_due_min": (round(max(0, iv - age), 1) if age is not None else 0), "ok": st.get("ok"), "ms": st.get("ms"), "summary": st.get("summary"), "error": st.get("error"),
                     "running": j["name"] in _running, "overdue": age is not None and age > iv * 2, "never": last is None})
    enabled = get_setting("refresh_enabled", "true").lower() == "true"
    ctx_age = next((r["age_min"] for r in rows if r["job"] == "prices"), None)
    if ctx_age is None:
        ctx_age = next((r["age_min"] for r in rows if r["job"] == "market_context"), None)
    return {"enabled": enabled, "jobs": rows, "overdue": sum(1 for r in rows if r["overdue"]), "failing": sum(1 for r in rows if r["ok"] is False), "market_age_min": ctx_age, "now": datetime.now(timezone.utc).isoformat(),
            "note": "intervals are minutes; override with settings refresh_<job>_min; refresh_enabled=false pauses all"}
