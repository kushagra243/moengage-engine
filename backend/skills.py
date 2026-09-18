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


def _sections(body: str) -> List[str]:
    return [m.group(2).strip() for m in re.finditer(r"^(#{2,4})\s+(.+)$", body, re.M)]


def _cut_section(body: str, title: str) -> str:
    """The text under one heading (any level 2-4), up to the next heading of the same or a higher level."""
    want = re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()
    heads = list(re.finditer(r"^(#{2,4})\s+(.+)$", body, re.M))
    for i, m in enumerate(heads):
        if want and want in re.sub(r"[^a-z0-9]+", " ", m.group(2).lower()):
            level = len(m.group(1))
            end = next((h.start() for h in heads[i + 1:] if len(h.group(1)) <= level), len(body))
            return body[m.start():end].strip()
    return ""


def _parts(safe: str) -> Dict[str, str]:
    """Every markdown file that ships with a skill: 'SKILL' plus its reference files by stem (e.g. 'official-skill')."""
    out = {}
    for x in sorted(glob.glob(os.path.join(SKILLS_DIR, safe, "*.md"))):
        stem = os.path.splitext(os.path.basename(x))[0]
        out["SKILL" if stem == "SKILL" else stem] = x
    return out


def read_skill(name: str, max_chars: int = 9000, part: str = "", section: str = "") -> Dict[str, Any]:
    """A skill's body, one of its reference files (`part`), or one heading of either (`section`). The reference files used to be
    listed as bare paths the agent had no tool to open, so MoEngage's own vendored skill could never actually be read."""
    safe = re.sub(r"[^a-z0-9_-]", "", (name or "").lower())
    parts = _parts(safe) if safe else {}
    if not parts.get("SKILL"):
        return {"error": f"unknown skill '{name}'", "available": [s["name"] for s in list_skills()]}
    want = re.sub(r"[^a-z0-9_-]", "", (part or "").lower().replace(".md", ""))
    key = next((k for k in parts if k.lower() == want), "SKILL") if want else "SKILL"
    if want and key == "SKILL" and want != "skill":
        return {"error": f"skill '{safe}' has no part '{part}'", "parts": [k for k in parts if k != "SKILL"]}
    text = open(parts[key], encoding="utf-8").read()
    body = re.sub(r"^---\n.*?\n---\n", "", text, count=1, flags=re.S)
    if section:
        cut = _cut_section(body, section)
        if not cut:
            return {"error": f"no section like '{section}' in {safe}/{key}", "sections": _sections(body)}
        body = cut
    others = {k: _sections(re.sub(r"^---\n.*?\n---\n", "", open(v, encoding="utf-8").read(), count=1, flags=re.S))[:24] for k, v in parts.items() if k != key}
    out = {"name": safe, "part": key, "content": body[:max_chars] + ("\n…[truncated: ask for one `section`]" if len(body) > max_chars else ""), "sections": _sections(body)[:30]}
    if others:
        out["more_parts"] = {k: v for k, v in others.items()}
        out["how_to_read_more"] = "call skill(name, part='<part>', section='<heading>') for any heading listed in more_parts"
    return out


def prompt_lines() -> str:
    sk = list_skills()
    if not sk:
        return ""
    return "\n".join(f"- {s['name']}: {s['description'][:150]}" for s in sk)
