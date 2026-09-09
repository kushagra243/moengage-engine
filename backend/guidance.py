"""
Operator guidance and engine knobs — how the team teaches the agent and how
the agent changes the engine on the go.

* agent_guidance: standing instructions ("always exclude KYC-pending users",
  "our brand voice is …", "never propose email on weekends"). Written by the
  operator in the Agent tab or saved by the agent with remember_guidance when
  the operator says "from now on…". Active entries are injected into the
  system prompt under OPERATOR GUIDANCE. Both kinds are visible and can be
  switched off or deleted in the UI.
* engine settings: an allowlist of non-secret knobs the agent may set
  directly (autopilot, thresholds, taxonomy codes, market universe…). Secrets,
  provider, hosts and security settings are never in the list.
"""
from __future__ import annotations
import json
from typing import Any, Dict, List, Optional

from .database import get_db, get_setting, set_setting
from .security import audit, redact

MAX_ACTIVE_CHARS = 6000


def init_guidance_tables() -> None:
    conn = get_db()
    conn.execute("""CREATE TABLE IF NOT EXISTS agent_guidance (
        id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL, scope TEXT DEFAULT 'general', author TEXT DEFAULT 'user',
        active INTEGER DEFAULT 1, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
    conn.commit(); conn.close()


SCOPES = ("general", "copy", "audience", "channel", "measurement", "market", "process", "ui")


def add(text: str, author: str = "user", scope: str = "general") -> Dict[str, Any]:
    init_guidance_tables()
    text = redact((text or "").strip())[:2000]
    if len(text) < 4:
        raise ValueError("guidance too short")
    scope = scope if scope in SCOPES else "general"
    conn = get_db()
    dup = conn.execute("SELECT id FROM agent_guidance WHERE lower(text)=lower(?)", (text,)).fetchone()
    if dup:
        conn.execute("UPDATE agent_guidance SET active=1, updated_at=CURRENT_TIMESTAMP WHERE id=?", (dup["id"],)); conn.commit(); conn.close()
        return get(dup["id"])  # type: ignore[return-value]
    cur = conn.execute("INSERT INTO agent_guidance (text, scope, author) VALUES (?,?,?)", (text, scope, author))
    gid = cur.lastrowid; conn.commit(); conn.close()
    audit("guidance.added", {"id": gid, "author": author, "scope": scope, "chars": len(text)}, actor=author)
    return get(gid)  # type: ignore[return-value]


def get(gid: int) -> Optional[Dict[str, Any]]:
    conn = get_db()
    r = conn.execute("SELECT * FROM agent_guidance WHERE id=?", (gid,)).fetchone()
    conn.close()
    return dict(r) if r else None


def list_guidance(active_only: bool = False) -> List[Dict[str, Any]]:
    init_guidance_tables()
    conn = get_db()
    q = "SELECT * FROM agent_guidance" + (" WHERE active=1" if active_only else "") + " ORDER BY active DESC, id DESC"
    rows = [dict(r) for r in conn.execute(q).fetchall()]
    conn.close()
    return rows


def set_active(gid: int, active: bool, actor: str = "user") -> Optional[Dict[str, Any]]:
    conn = get_db()
    conn.execute("UPDATE agent_guidance SET active=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (1 if active else 0, gid))
    conn.commit(); conn.close()
    audit("guidance.toggled", {"id": gid, "active": active}, actor=actor)
    return get(gid)


def delete(gid: int, actor: str = "user") -> bool:
    conn = get_db()
    n = conn.execute("DELETE FROM agent_guidance WHERE id=?", (gid,)).rowcount
    conn.commit(); conn.close()
    audit("guidance.deleted", {"id": gid}, actor=actor)
    return n > 0


def prompt_block() -> str:
    rows = list_guidance(active_only=True)
    if not rows:
        return ""
    lines, used = [], 0
    for r in rows:
        line = f"- [{r['scope']}, {r['author']} #{r['id']}] {r['text']}"
        if used + len(line) > MAX_ACTIVE_CHARS:
            lines.append("- … (older guidance omitted for length; see the Agent tab)"); break
        lines.append(line); used += len(line)
    return "OPERATOR GUIDANCE (standing instructions from your team; they refine the doctrine and override defaults, never the safety rules 5, 7 and 9):\n" + "\n".join(lines)


# ── engine knobs the agent may change directly ─────────────────────────────────
ENGINE_SETTINGS: Dict[str, Dict[str, Any]] = {
    "autopilot_enabled":      {"type": "bool", "help": "run missions after each refresh"},
    "autopilot_max_actions":  {"type": "int", "min": 0, "max": 10, "help": "proposals budget per autopilot run"},
    "schedule_enabled":       {"type": "bool", "help": "daily run on/off"},
    "schedule_time":          {"type": "time", "help": "HH:MM local for the daily run"},
    "refresh_interval_hours": {"type": "float", "min": 0.5, "max": 24, "help": "intraday model-free refresh cadence"},
    "analysis_batch":         {"type": "int", "min": 1, "max": 50, "help": "campaigns deep-analysed per run"},
    "market_universe_mode":   {"type": "enum", "values": ["either", "both", "hyperliquid", "binance", "manual"], "help": "which exchange listing defines the market universe"},
    "market_top_n":           {"type": "int", "min": 5, "max": 200, "help": "movers shown"},
    "market_cache_ttl_s":     {"type": "int", "min": 60, "max": 86400, "help": "market cache seconds"},
    "taxonomy_codes":         {"type": "json_object", "help": "extra naming-convention codes, e.g. {\"VIP\": \"trader:VIP\"} (merged into existing)"},
    "mock_scenario":          {"type": "str", "max_len": 200, "help": "demo fault scenario"},
    "llm_temperature":        {"type": "float", "min": 0, "max": 1.5, "help": "agent sampling temperature"},
    "llm_max_tokens":         {"type": "int", "min": 256, "max": 32000, "help": "agent max output tokens"},
    "llm_model_bulk":         {"type": "str", "max_len": 120, "help": "bulk-tier model id or auto-free"},
    "devagent_enabled":       {"type": "bool", "help": "allow code-change proposals to be drafted/merged"},
    "llm_tool_output_chars":  {"type": "int", "min": 1500, "max": 16000, "help": "default per-tool output budget (chars) sent back to the model"},
    "llm_history_messages":   {"type": "int", "min": 0, "max": 20, "help": "chat turns carried into each request"},
    "llm_max_rounds":         {"type": "int", "min": 1, "max": 12, "help": "max tool rounds per chat"},
    "llm_max_rounds_autopilot": {"type": "int", "min": 1, "max": 10, "help": "max tool rounds per autopilot mission"},
    "web3_enabled":           {"type": "bool", "help": "web3 trending lane on/off"},
    "web3_chains":            {"type": "str", "max_len": 120, "help": "comma list: solana,base,bsc,eth,robinhood"},
    "llm_routes":             {"type": "json_object", "help": "purpose → model list (merged), purposes: chat, autopilot, analysis, brief, copy, classification, code, review, test"},
    "llm_brief_tier":         {"type": "enum", "values": ["bulk", "main"], "help": "which tier writes the daily brief"},
}


def set_engine_setting(key: str, value: Any, actor: str = "agent") -> Dict[str, Any]:
    spec = ENGINE_SETTINGS.get(key)
    if not spec:
        return {"error": f"'{key}' is not an agent-changeable setting", "allowed": {k: v["help"] for k, v in ENGINE_SETTINGS.items()}}
    t = spec["type"]; sval: str
    try:
        if t == "bool":
            sval = "true" if str(value).strip().lower() in ("1", "true", "yes", "on") else "false"
        elif t in ("int", "float"):
            num = int(float(value)) if t == "int" else float(value)
            if not (spec["min"] <= num <= spec["max"]):
                return {"error": f"{key} must be between {spec['min']} and {spec['max']}"}
            sval = str(num)
        elif t == "time":
            import re as _re
            if not _re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", str(value).strip()):
                return {"error": "time must be HH:MM"}
            sval = str(value).strip()
        elif t == "enum":
            if str(value) not in spec["values"]:
                return {"error": f"{key} must be one of {spec['values']}"}
            sval = str(value)
        elif t == "json_object":
            obj = value if isinstance(value, dict) else json.loads(str(value))
            if not isinstance(obj, dict):
                return {"error": f"{key} must be a JSON object"}
            cur = {}
            try:
                cur = json.loads(get_setting(key, "") or "{}")
            except Exception:
                cur = {}
            cur.update({str(k)[:40]: (v if isinstance(v, list) else str(v)[:120]) for k, v in obj.items()})
            sval = json.dumps(cur)
        else:
            sval = str(value)[: spec.get("max_len", 200)]
    except (ValueError, TypeError) as e:
        return {"error": f"bad value for {key}: {e}"}
    before = get_setting(key, "")
    set_setting(key, sval)
    audit("engine.setting_changed", {"key": key, "before": before[:200], "after": sval[:200]}, actor=actor)
    return {"ok": True, "key": key, "before": before, "after": sval, "effective": "immediately (settings are read on use)"}
