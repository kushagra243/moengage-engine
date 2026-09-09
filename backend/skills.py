"""
Skills shared by Claude Code and the in-app agent: one source of truth in
.claude/skills/<name>/SKILL.md (Agent Skills format: YAML front matter with
name + description, then the body). Claude Code discovers them natively; the
agent lists them in its system prompt and loads one on demand with skill().
"""
from __future__ import annotations
import glob
import os
import re
from typing import Any, Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILLS_DIR = os.path.join(ROOT, ".claude", "skills")


def _front(text: str) -> Dict[str, str]:
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    meta: Dict[str, str] = {}
    if m:
        for line in m.group(1).splitlines():
            if ":" in line and not line.startswith(" "):
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip().strip('"')
    return meta


def list_skills() -> List[Dict[str, Any]]:
    out = []
    for p in sorted(glob.glob(os.path.join(SKILLS_DIR, "*", "SKILL.md"))):
        text = open(p, encoding="utf-8").read()
        meta = _front(text)
        out.append({"name": meta.get("name") or os.path.basename(os.path.dirname(p)), "description": meta.get("description", "")[:300],
                    "path": os.path.relpath(p, ROOT), "chars": len(text)})
    return out


def read_skill(name: str, max_chars: int = 14000) -> Dict[str, Any]:
    safe = re.sub(r"[^a-z0-9_-]", "", (name or "").lower())
    p = os.path.join(SKILLS_DIR, safe, "SKILL.md")
    if not safe or not os.path.exists(p):
        return {"error": f"unknown skill '{name}'", "available": [s["name"] for s in list_skills()]}
    text = open(p, encoding="utf-8").read()
    body = re.sub(r"^---\n.*?\n---\n", "", text, count=1, flags=re.S)
    refs = sorted(os.path.relpath(x, ROOT) for x in glob.glob(os.path.join(SKILLS_DIR, safe, "*")) if not x.endswith("SKILL.md"))
    return {"name": safe, "content": body[:max_chars] + ("\n…[truncated]" if len(body) > max_chars else ""), "references": refs}


def prompt_lines() -> str:
    sk = list_skills()
    if not sk:
        return ""
    return "\n".join(f"- {s['name']}: {s['description'][:150]}" for s in sk)
