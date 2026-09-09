"""
ICE scoring (GrowthHackers framework): every idea, recommendation and
experiment carries Impact, Confidence and Ease on 1–10 and a score that ranks
them. Score = I × C × E (1–1000) so a high-impact idea with real evidence and
low effort floats to the top; we also expose the 0–10 mean for people who
prefer the averaged variant. Heuristics turn free-text hints into scores when
the author did not set them; the author's numbers always win.
"""
from __future__ import annotations
import re
from typing import Any, Dict, Optional

EFFORT_EASE = {"low": 8, "medium": 5, "med": 5, "high": 3}


def _clamp(v: Any, default: int) -> int:
    try:
        return int(max(1, min(10, round(float(v)))))
    except Exception:
        return default


def score(impact: Any, confidence: Any, ease: Any) -> Dict[str, Any]:
    i, c, e = _clamp(impact, 5), _clamp(confidence, 5), _clamp(ease, 5)
    s = i * c * e
    return {"impact": i, "confidence": c, "ease": e, "score": s, "mean": round((i + c + e) / 3, 1), "grade": "A" if s >= 343 else "B" if s >= 180 else "C" if s >= 80 else "D"}


def impact_from_text(text: str, default: int = 5) -> int:
    """Read the biggest percentage / multiplier claim in an expected-impact sentence."""
    t = (text or "").lower()
    if not t or t == "—":
        return default
    m = re.findall(r"(\d+(?:\.\d+)?)\s*[–-]?\s*(\d+(?:\.\d+)?)?\s*(%|x|×|pp|pts)", t)
    best = 0.0
    for a, b, unit in m:
        v = float(b or a)
        if unit in ("x", "×"):
            v = v * 25
        best = max(best, v)
    if best >= 40:
        base = 9
    elif best >= 20:
        base = 8
    elif best >= 10:
        base = 7
    elif best >= 5:
        base = 6
    elif best > 0:
        base = 5
    else:
        base = default
    if re.search(r"trust|uninstall|regret|compliance|zero regret|years of", t):
        base = max(base, 8)
    if re.search(r"informs|diagnostic|learn", t):
        base = min(base, 5)
    return base


def infer(expected_impact: str = "", confidence: Optional[float] = None, effort: str = "medium", source: str = "", priority: Optional[int] = None,
          impact: Optional[int] = None, confidence_score: Optional[int] = None, ease: Optional[int] = None) -> Dict[str, Any]:
    """Build ICE from whatever hints exist. Explicit 1–10 numbers win; otherwise expected-impact text, a 0–1 confidence and an effort label."""
    i = impact if impact is not None else impact_from_text(expected_impact, default=(7 if (priority or 0) >= 85 else 6 if (priority or 0) >= 70 else 5))
    if confidence_score is not None:
        c = confidence_score
    elif confidence is not None:
        c = round(float(confidence) * 10)
    else:
        c = {"rules": 7, "library": 8, "skills": 8, "agent": 5, "model": 5}.get((source or "").split(" ")[0], 6)
    e = ease if ease is not None else EFFORT_EASE.get(str(effort or "medium").lower()[:6], 5)
    return score(i, c, e)


def from_payload(payload: Dict[str, Any], source: str = "agent") -> Dict[str, Any]:
    """ICE for a proposal: explicit payload['ice'] if present, else inferred from the goal brief and channel."""
    ice = (payload or {}).get("ice") or {}
    if ice and all(k in ice for k in ("impact", "confidence", "ease")):
        return score(ice["impact"], ice["confidence"], ice["ease"])
    goal = (payload or {}).get("goal") or {}
    text = " ".join(str(goal.get(k, "")) for k in ("target", "hypothesis"))
    conf = 7 if float(goal.get("control_group_pct") or 0) >= 5 else 5
    if str(goal.get("transition", "")).startswith(("acquired", "verified", "funded")):
        conf += 1                                                     # early-funnel transitions have the best-evidenced playbooks
    ch = str((payload or {}).get("channel") or "push").lower()
    ease = 8 if ch in ("push", "in-app", "inapp", "cards") else 6 if ch in ("email", "whatsapp") else 5
    if (payload or {}).get("steps"):
        ease = max(2, ease - 2)
    return score(impact_from_text(text, 6), conf, ease)


def tagline(title: str, kpi: str = "", who: str = "", mechanism: str = "") -> str:
    """A one-line pitch a growth team would put on the card: who → what → measured by."""
    who = (who or "").strip().rstrip("."); kpi = (kpi or "").strip()
    mech = (mechanism or title or "").strip().rstrip(".")
    if len(mech) > 90:
        mech = mech[:87].rsplit(" ", 1)[0] + "…"
    parts = [p for p in [who and f"For {who}", mech, kpi and f"measured by {kpi.replace('_', ' ')}"] if p]
    return " · ".join(parts)[:220]
