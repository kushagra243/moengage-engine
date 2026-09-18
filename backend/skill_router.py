"""
Skills that are actually used, not merely installed.

Loading a skill used to be a voluntary tool call. On the free bulk models, which are weak at volunteering tool calls, that
meant campaign drafts, segments and alerts were routinely written without MoEngage's own rules ever entering the context,
and nothing recorded whether a skill had been read. Three things change that:

  pick()     deterministic routing: the task's words, the specialist answering and the purpose decide which skill sections
             belong in the context, MoEngage's own gotchas and verification checklist first whenever something will be
             built in MoEngage.
  block()    the chosen sections, compact (`llm_autoload_chars` per task, default 5 000), placed in front of the user's
             message so the cached system prompt stays cached.
  record()   every load, automatic or by tool call, lands in `skill_loads`; usage() shows what was read in the last days
             and which installed skills were never opened.

`llm_autoload_skills=false` switches the automatic loading off; the `skill` tool keeps working either way.
"""
from __future__ import annotations
import re
from typing import Any, Dict, List, Optional, Tuple

from .database import get_db, get_setting

# (pattern over the task text, [(skill, part, section)…]) — the first matches fill the budget, in order
BUILD = r"\b(campaign|push|email|whatsapp|sms|in-?app|flow|journey|draft|send|trigger|schedule|deliver|template|personalis|business event|inform|cohort|segment|audience|control group|holdout|frequency cap|dnd)\b"
ROUTES: List[Tuple[str, List[Tuple[str, str, str]]]] = [
    (r"\b(flow|journey|automation|multi-?step)\b", [("moengage", "official-skill", "Typical Flow (Automation) Workflow")]),
    (r"\b(campaign|push|email|draft|send|schedule|trigger|template|business event)\b", [("moengage", "official-skill", "Typical Campaign Creation Workflow"), ("moengage", "official-skill", "Campaign Delivery Types")]),
    (r"\b(segment|cohort|audience|upload)\b", [("moengage", "official-skill", "Segmentation: Rule-Based vs File vs Affinity")]),
    (r"\b(api|endpoint|key|401|403|429|rate limit|request_id|payload|v5|created_by)\b", [("moengage-api", "SKILL", "Rules the engine enforces"), ("moengage", "official-skill", "Rate Limits")]),
    (r"\b(alert|market alert|discovery|whale|milestone|inform)\b", [("market-alerts-2", "SKILL", "")]),
    (r"\b(copy|variant|subject line|title|body|hinglish|tone)\b", [("crypto-compliance-copy", "SKILL", ""), ("crypto-copywriting", "SKILL", "")]),
    (r"\b(perp|futures|leverage|funding|liquidat|options|open interest)\b", [("crypto-derivatives-marketing", "SKILL", "")]),
    (r"\b(rival|competitor|share of|counter)\b", [("competitive-intelligence", "SKILL", "")]),
    (r"\b(experiment|holdout|lift|readout|significan|a/b|ab test)\b", [("clm-operator", "SKILL", "")]),
]
ALWAYS_WHEN_BUILDING = [("moengage", "SKILL", "Quick facts"), ("moengage", "official-skill", "Common Gotchas"), ("moengage", "official-skill", "Verification Checklist")]
BY_PERSONA = {"copywriter": [("crypto-compliance-copy", "SKILL", "")], "compliance": [("crypto-compliance-copy", "SKILL", "")], "alerts": [("market-alerts-2", "SKILL", "")],
              "cohorts": [("cohort-studies", "SKILL", "")], "experimenter": [("clm-operator", "SKILL", "")], "intel": [("competitive-intelligence", "SKILL", "")]}
BUILD_TOOLS = {"propose_campaign", "propose_segment", "propose_flow", "propose_signal_rule", "run_sop", "campaign_from_alert", "market_alerts_launch_discovery", "market_alerts_propose_discovery",
               "market_alerts_propose_pilot", "moengage_api_read"}


def init_tables() -> None:
    conn = get_db()
    conn.execute("""CREATE TABLE IF NOT EXISTS skill_loads (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT DEFAULT CURRENT_TIMESTAMP, skill TEXT NOT NULL, part TEXT, section TEXT,
                    how TEXT NOT NULL, persona TEXT, purpose TEXT, chars INTEGER DEFAULT 0)""")
    conn.commit(); conn.close()


def record(skill: str, how: str, part: str = "SKILL", section: str = "", persona: str = "", purpose: str = "", chars: int = 0) -> None:
    try:
        init_tables()
        conn = get_db()
        conn.execute("INSERT INTO skill_loads (skill, part, section, how, persona, purpose, chars) VALUES (?,?,?,?,?,?,?)", (skill, part, section[:80], how, persona or "", purpose or "", int(chars)))
        conn.commit(); conn.close()
    except Exception:
        pass                                           # a ledger failure must never break an answer


def enabled() -> bool:
    return str(get_setting("llm_autoload_skills", "true")).lower() != "false"


def pick(message: str, persona: Optional[str] = None, purpose: Optional[str] = None) -> List[Tuple[str, str, str]]:
    text = (message or "").lower()
    out: List[Tuple[str, str, str]] = []
    if re.search(BUILD, text) or (purpose == "autopilot"):
        out += ALWAYS_WHEN_BUILDING                   # anything that ends up in MoEngage is written with MoEngage's own rules in view
    for rx, items in ROUTES:
        if re.search(rx, text):
            out += items
    out += BY_PERSONA.get((persona or "").lower(), [])
    return list(dict.fromkeys(out))


def block(message: str, persona: Optional[str] = None, purpose: Optional[str] = None) -> Tuple[str, List[str]]:
    """The text to put in front of the task, and the names of what was loaded. Empty when nothing applies or it is switched off."""
    if not enabled():
        return "", []
    from .skills import read_skill
    budget = int(get_setting("llm_autoload_chars", "5000") or 5000)
    chunks, names, used = [], [], 0
    for skill, part, section in pick(message, persona, purpose):
        room = budget - used
        if room < 400:
            break
        r = read_skill(skill, max_chars=min(room, 2600 if section else 1800), part="" if part == "SKILL" else part, section=section)
        if r.get("error") or not r.get("content"):
            continue
        label = skill + (f"/{part}" if part != "SKILL" else "") + (f" § {section}" if section else "")
        chunks.append(f"<skill name=\"{label}\">\n{r['content']}\n</skill>")
        names.append(label); used += len(r["content"])
        record(skill, "auto", part=part, section=section, persona=persona or "", purpose=purpose or "", chars=len(r["content"]))
    if not chunks:
        return "", []
    head = ("Skills loaded for this task by the engine (our own rules and MoEngage's; follow them, and call skill(name, part, section) for any other heading you need):\n")
    return head + "\n".join(chunks), names


def checklist_for_tool(tool: str, loaded: List[str]) -> str:
    """When something is about to be built in MoEngage and its checklist was never in context, attach it to the tool result."""
    if tool not in BUILD_TOOLS or any(n.startswith("moengage") for n in loaded) or not enabled():
        return ""
    from .skills import read_skill
    r = read_skill("moengage", max_chars=1800, part="official-skill", section="Verification Checklist")
    if r.get("error"):
        return ""
    record("moengage", "tool_gate", part="official-skill", section="Verification Checklist", chars=len(r["content"]))
    return r["content"]


def usage(days: int = 7) -> Dict[str, Any]:
    from .skills import list_skills
    init_tables()
    conn = get_db()
    rows = [dict(r) for r in conn.execute("""SELECT skill, how, COUNT(*) n, MAX(at) last, SUM(chars) chars FROM skill_loads WHERE at >= datetime('now', ?) GROUP BY skill, how""", (f"-{int(days)} days",)).fetchall()]
    conn.close()
    by: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        b = by.setdefault(r["skill"], {"skill": r["skill"], "auto": 0, "by_the_agent": 0, "last": ""})
        b["auto" if r["how"] in ("auto", "tool_gate") else "by_the_agent"] += r["n"]
        b["last"] = max(b["last"], str(r["last"] or ""))
    installed = [s["name"] for s in list_skills()]
    used = sorted(by.values(), key=lambda b: -(b["auto"] + b["by_the_agent"]))
    return {"days": days, "enabled": enabled(), "used": used, "never_opened": [n for n in installed if n not in by], "installed": len(installed),
            "note": "auto = placed in context by the engine because the task called for it; by the agent = the model asked for it with the skill tool"}
