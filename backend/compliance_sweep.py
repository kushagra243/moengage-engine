"""
Compliance sweep — lint the copy of every live MoEngage campaign (not only our
drafts) against the India rules in our skills: venue/competitor names, banned
claims, ASCI-forbidden words, leverage lures, urgency on price, push length,
missing disclaimer on channels that can carry it, incentive language on
onboarding audiences. Findings carry the rule, the snippet and the fix.
Persisted per sweep; the QA layer reads the latest.
"""
from __future__ import annotations
import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .database import get_db
from .security import audit, redact

URGENCY = re.compile(r"\b(hurry|last chance|ends (soon|tonight|today)|don'?t miss|only \d+ (hours|minutes) left|before it'?s too late|act now)\b", re.I)
DISCLAIMER = re.compile(r"unregulated and can be highly risky|no regulatory recourse", re.I)
INCENTIVE = re.compile(r"\b(bonus|cashback|free (crypto|bitcoin|btc|money)|giveaway|lucky draw|prize|reward)\b", re.I)
DIRECTION = re.compile(r"\b(buy (now|the dip)|sell now|go long|go short|will (rise|pump|rally|double)|guaranteed returns?)\b", re.I)


def _rules():
    from .llm.tools import VENUE_WORDS, BANNED_COPY, LEVERAGE_LURE
    from .sop_india import FORBIDDEN_VDA
    return [("venue_named", "high", VENUE_WORDS, "we never name a liquidity venue or competitor in user copy"),
            ("banned_claim", "high", BANNED_COPY, "remove the claim: no forecasts, no safety, no 'don't miss'"),
            ("asci_forbidden_word", "high", FORBIDDEN_VDA, "ASCI forbids 'currency', 'securities', 'custodian', 'depositories' for VDA products"),
            ("leverage_lure", "high", LEVERAGE_LURE, "leverage figures are product documentation, never a marketing lure"),
            ("direction", "high", DIRECTION, "copy never tells a user to buy, sell, long or short"),
            ("price_urgency", "medium", URGENCY, "countdowns only for genuine product deadlines, never price")]


def init_tables() -> None:
    conn = get_db()
    conn.execute("""CREATE TABLE IF NOT EXISTS compliance_sweeps (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, source TEXT, campaigns INTEGER, with_content INTEGER, findings_json TEXT, counts_json TEXT)""")
    conn.commit(); conn.close()


def _texts(c: Dict[str, Any]) -> Dict[str, str]:
    prev = c.get("content_preview") or c.get("campaign_content") or c.get("content") or {}
    out = {k: str(v) for k, v in prev.items() if isinstance(v, str) and v.strip() and "html" not in str(k).lower()} if isinstance(prev, dict) else {}
    if not out:
        for k in ("title", "subject", "body", "message", "text"):
            if c.get(k):
                out[k] = str(c[k])
    return out


def sweep(persist: bool = True) -> Dict[str, Any]:
    from .moengage import MoEngageClient
    c = MoEngageClient(); camps = c.get_campaigns()
    live = [x for x in camps if str(x.get("status") or "").lower() in ("active", "running", "scheduled", "live", "sent", "")]
    findings: List[Dict[str, Any]] = []; with_content = 0
    for camp in live:
        texts = _texts(camp)
        if not texts:
            continue
        with_content += 1
        ch = str(camp.get("channel") or "").lower(); seg = str(camp.get("target_segment") or "").lower(); joined = " ".join(texts.values())
        def add(rule, sev, snippet, fix):
            findings.append({"campaign_id": str(camp.get("id")), "campaign": camp.get("name"), "channel": camp.get("channel"), "rule": rule, "severity": sev, "snippet": snippet[:140], "fix": fix})
        for rule, sev, rx, fix in _rules():
            m = rx.search(joined)
            if m:
                add(rule, sev, m.group(0), fix)
        if ch == "push":
            t = texts.get("title") or ""; b = texts.get("body") or texts.get("message") or ""
            if len(t) > 60 or len(b) > 140:
                add("push_length", "low", f"title {len(t)} / body {len(b)} chars", "push title ≤ 60, body ≤ 140")
            if INCENTIVE.search(joined) or re.search(r"% off|offer|sale", joined, re.I):
                add("promo_push_disclaimer", "medium", (INCENTIVE.search(joined) or re.search(r"% off|offer|sale", joined, re.I)).group(0), "promotional push cannot carry the ASCI disclaimer: the landing screen must, and the push stays factual")
        if ch in ("email", "whatsapp", "in-app", "inapp", "cards") and not DISCLAIMER.search(joined) and re.search(r"crypto|bitcoin|btc|eth|token|perp|futures|trade|trading|deposit|invest", joined, re.I):
            add("missing_disclaimer", "medium", f"{ch} content without the ASCI VDA disclaimer text", "add the verbatim disclaimer in the footer / template body / card")
        if INCENTIVE.search(joined) and re.search(r"kyc|verif|deposit|new user|signup|sign-up|onboard", seg + " " + camp.get("name", ""), re.I):
            add("onboarding_incentive", "high", INCENTIVE.search(joined).group(0), "no bonuses for KYC/deposit: fraud, bonus-seekers and ASCI risk")
    sev_rank = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda f: (sev_rank[f["severity"]], f["campaign"] or ""))
    counts = {k: sum(1 for f in findings if f["severity"] == k) for k in ("high", "medium", "low")}
    by_campaign: Dict[str, int] = {}
    for f in findings:
        by_campaign[f["campaign"]] = by_campaign.get(f["campaign"], 0) + 1
    out = {"generated_at": datetime.now(timezone.utc).isoformat(), "source": c.mode, "campaigns": len(live), "with_content": with_content, "findings": findings, "counts": counts, "worst": sorted(by_campaign.items(), key=lambda kv: -kv[1])[:6],
           "verdict": ("no findings in live copy" if not findings else f"{counts['high']} high · {counts['medium']} medium · {counts['low']} low across {len(by_campaign)} campaign(s)"),
           "note": ("content previews are not exposed for these campaigns; connect the Campaigns API or paste a HAR so the sweep can read copy" if with_content == 0 and live else "rules: crypto-compliance-copy · crypto-copywriting")}
    if persist:
        try:
            init_tables(); conn = get_db()
            conn.execute("INSERT INTO compliance_sweeps (source, campaigns, with_content, findings_json, counts_json) VALUES (?,?,?,?,?)", (c.mode, len(live), with_content, json.dumps(findings, default=str)[:200000], json.dumps(counts)))
            conn.execute("DELETE FROM compliance_sweeps WHERE id NOT IN (SELECT id FROM compliance_sweeps ORDER BY id DESC LIMIT 120)")
            conn.commit(); conn.close()
            audit("compliance.sweep", {"campaigns": len(live), **counts}, actor="qa")
        except Exception:
            pass
    return out


def latest() -> Optional[Dict[str, Any]]:
    try:
        init_tables(); conn = get_db(); r = conn.execute("SELECT * FROM compliance_sweeps ORDER BY id DESC LIMIT 1").fetchone(); conn.close()
        if not r:
            return None
        return {"generated_at": r["created_at"], "source": r["source"], "campaigns": r["campaigns"], "with_content": r["with_content"], "findings": json.loads(r["findings_json"] or "[]"), "counts": json.loads(r["counts_json"] or "{}"), "cached": True}
    except Exception:
        return None
