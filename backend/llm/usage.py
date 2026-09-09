"""
Token ledger: every model call is recorded (purpose, tier, model, prompt /
completion / cached tokens, estimated cost) so spend is visible per day and
per purpose, and the budgets below can be tuned with evidence.
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional

from ..database import get_db, get_setting

# rough $/1M tokens for cost estimates when the provider does not return cost; free-tier models → 0
PRICE_HINTS = [("free", 0.0, 0.0), ("claude-opus", 15.0, 75.0), ("claude-sonnet", 3.0, 15.0), ("claude-haiku", 0.8, 4.0), ("gpt-4o-mini", 0.15, 0.6), ("gpt-4o", 2.5, 10.0),
               ("gpt-4.1", 2.0, 8.0), ("deepseek", 0.3, 1.2), ("llama", 0.2, 0.6), ("qwen", 0.2, 0.6), ("gemini-2.5-flash", 0.3, 2.5), ("gemini", 1.25, 10.0), ("sim/", 0.0, 0.0)]


def init_usage_tables() -> None:
    conn = get_db()
    conn.execute("""CREATE TABLE IF NOT EXISTS llm_usage (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, purpose TEXT, tier TEXT, model TEXT,
                    prompt_tokens INTEGER, completion_tokens INTEGER, cached_tokens INTEGER, cost_usd REAL, rounds INTEGER DEFAULT 1)""")
    conn.commit(); conn.close()


def estimate_cost(model: str, prompt: int, completion: int, cached: int = 0) -> float:
    m = (model or "").lower()
    for key, pin, pout in PRICE_HINTS:
        if key in m:
            return round(((prompt - cached) * pin + cached * pin * 0.1 + completion * pout) / 1_000_000, 6)
    return round((prompt * 1.0 + completion * 4.0) / 1_000_000, 6)


def record(purpose: str, tier: str, model: str, usage: Dict[str, Any], cost_usd: Optional[float] = None) -> None:
    try:
        init_usage_tables()
        p = int(usage.get("prompt_tokens") or 0); c = int(usage.get("completion_tokens") or 0)
        cached = int(((usage.get("prompt_tokens_details") or {}).get("cached_tokens")) or usage.get("cache_read_input_tokens") or 0)
        cost = float(cost_usd) if cost_usd is not None else estimate_cost(model, p, c, cached)
        conn = get_db()
        conn.execute("INSERT INTO llm_usage (purpose, tier, model, prompt_tokens, completion_tokens, cached_tokens, cost_usd) VALUES (?,?,?,?,?,?,?)", (purpose, tier, model, p, c, cached, cost))
        conn.commit(); conn.close()
    except Exception:
        pass


def summary(days: int = 7) -> Dict[str, Any]:
    init_usage_tables()
    conn = get_db()
    rows = [dict(r) for r in conn.execute("""SELECT date(created_at) d, purpose, tier, model, COUNT(*) calls, SUM(prompt_tokens) p, SUM(completion_tokens) c, SUM(cached_tokens) cached, SUM(cost_usd) cost
                                             FROM llm_usage WHERE created_at >= datetime('now', ?) GROUP BY d, purpose, tier, model ORDER BY d DESC""", (f"-{int(days)} days",)).fetchall()]
    today = [r for r in rows if r["d"] == conn.execute("SELECT date('now')").fetchone()[0]]
    conn.close()
    def agg(rs):
        return {"calls": sum(r["calls"] for r in rs), "prompt_tokens": sum(r["p"] or 0 for r in rs), "completion_tokens": sum(r["c"] or 0 for r in rs), "cached_tokens": sum(r["cached"] or 0 for r in rs), "cost_usd": round(sum(r["cost"] or 0 for r in rs), 4)}
    by_purpose: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        by_purpose.setdefault(r["purpose"], []).append(r)
    return {"days": days, "today": agg(today), "period": agg(rows), "by_purpose": {k: agg(v) for k, v in by_purpose.items()},
            "by_tier": {t: agg([r for r in rows if r["tier"] == t]) for t in sorted({r["tier"] for r in rows})},
            "budgets": {"tool_output_chars": int(get_setting("llm_tool_output_chars", "7000") or 7000), "history_messages": int(get_setting("llm_history_messages", "8") or 8),
                        "max_rounds_chat": int(get_setting("llm_max_rounds", "8") or 8), "max_rounds_autopilot": int(get_setting("llm_max_rounds_autopilot", "6") or 6),
                        "bulk_model": get_setting("llm_model_bulk", "auto-free"), "brief_tier": get_setting("llm_brief_tier", "bulk")},
            "note": "cost is the provider's figure when returned, else an estimate from public list prices; free-tier models count as 0"}
