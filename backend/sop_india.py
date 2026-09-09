"""
India-fit review of every SOP — are these procedures the best possible ones
for Indian crypto traders, by the rules in our own skills?

Rules come from `crypto-compliance-copy` (ASCI VDA disclaimer and forbidden
words, 30% tax / 1% TDS framing, derivatives education-only posture, DLT SMS
and WhatsApp windows, DND), `crypto-copywriting` (Hinglish for mass cohorts,
fact + tool, no urgency on price), `crypto-growth-calendar` (salary week 1–7,
ITR July, Budget, Diwali/Muhurat, IST send windows) and `clm-campaign-playbook`
(no bonuses for KYC/deposit, best users hear least). Every SOP gets a score,
flags with the exact fix, and `apply_fixes()` writes a new, framework-checked
version with the deterministic amendments (nothing sends; runs still queue
proposals for approval).
"""
from __future__ import annotations
import re
from typing import Any, Dict, List, Optional

from .security import audit

FORBIDDEN_VDA = re.compile(r"\b(currenc(y|ies)|securit(y|ies)|custodian|depositor(y|ies))\b", re.I)
BANNED = re.compile(r"\b(guaranteed|risk[- ]free|passive income|earn while you sleep|to the moon|moon\b|last chance|don'?t miss|sure ?shot|safe (bet|investment)|100x|up to \d+x|\d{2,3}x leverage|get in early)\b", re.I)
VENUES = re.compile(r"\b(binance|hyperliquid|bybit|okx|coinbase|kraken|kucoin|bitget|mexc|gate\.io|coinswitch|wazirx|zebpay|mudrex|delta exchange|zerodha|groww|upstox)\b", re.I)   # Robinhood Chain is one of our web3 chains, not a venue here
ACQUISITION_DERIV = re.compile(r"(trade (perps?|futures|options) now|open (a |your )?(long|short)\b|start (trading )?(perps|futures|with leverage)|try (perps|futures|leverage)|size up|go long|go short|upgrade to (perps|futures|leverage))", re.I)
DERIV_WORDS = re.compile(r"\b(perps?\b|perpetual|futures|leverage(?! (the|whatsapp|our|its|this|these|existing|email|cards))|margin (buffer|call|mode)|isolated margin|liquidat|funding (rate|cost|payment|apr)|options (education|trading|expiry|desk)|call/put|straddle|hedg(e|ing) (education|with))", re.I)
INCENTIVE = re.compile(r"\b(bonus|cashback|reward|free (crypto|bitcoin|money)|giveaway|lucky draw|prize)\b", re.I)
TAX_WORDS = re.compile(r"\b(tds|tax|30%|1%|itr|statement)\b", re.I)
UPI_WORDS = re.compile(r"\b(upi|imps|neft|bank)\b", re.I)
HINGLISH = re.compile(r"hinglish|regional|app_language|vernacular", re.I)
UTILITY = re.compile(r"utility|transactional|opt-?in|approved template", re.I)
MASS_FAMILIES = ("LVT", "MVT", "FTD", "NEW", "DORMANT", "KYC", "NODEP", "ALL", "ACTIVE", "SPOT", "SIP", "*")
DERIV_FAMILIES = ("PERP", "PERPS", "LEV", "LEVERAGE", "HFT", "FUT", "FUTURES", "OPT", "OPTIONS", "LIQUIDATED", "HEDGER", "HEDGERS", "MARGIN", "LEV_CLIMBER")
FUNNEL_TAX = ("verified_funded", "funded_activated")


def _text(sop: Dict[str, Any]) -> str:
    parts = [sop.get("name", ""), sop.get("objective", ""), sop.get("user", "")] + list(sop.get("ideas") or [])
    for st in sop.get("steps") or []:
        parts += [str(st.get("purpose", "")), str(st.get("copy_brief", ""))]
    return " ".join(parts)


def _hhmm(t: Optional[str]) -> Optional[int]:
    m = re.match(r"^(\d{1,2}):(\d{2})", str(t or ""))
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None


def review_one(sop: Dict[str, Any]) -> Dict[str, Any]:
    flags: List[Dict[str, Any]] = []; strengths: List[str] = []
    def flag(rule, sev, detail, fix, auto=False):
        flags.append({"rule": rule, "severity": sev, "detail": detail[:200], "fix": fix[:200], "auto_fixable": auto})
    txt = _text(sop); fam = str((sop.get("audience") or {}).get("segment_family") or "").upper(); steps = sop.get("steps") or []
    comp = sop.get("compliance") or {}; disc = {str(c).lower() for c in (comp.get("disclaimer_channels") or [])}; banned = {str(b).lower() for b in (comp.get("banned_angles") or [])}
    excl = " ".join(str(x).lower() for x in ((sop.get("audience") or {}).get("exclusions") or []))
    fam_tokens = set(re.split(r"[^A-Z0-9]+", fam))
    is_deriv = sop.get("campaign_type") in ("risk",) or bool(DERIV_WORDS.search(sop.get("name", "") + " " + sop.get("objective", ""))) or bool(fam_tokens & set(DERIV_FAMILIES))
    is_onboarding = sop.get("transition") in ("acquired_verified", "verified_funded", "funded_activated") or sop.get("campaign_type") == "onboarding"
    is_mass = any(k in fam for k in MASS_FAMILIES) or sop.get("campaign_type") in ("newsletter", "cohort_upload", "compliance")
    channels = [str(st.get("channel", "")).lower() for st in steps if not str(st.get("purpose", "")).startswith("(internal)")]
    # 1. ASCI disclaimer carriage
    need = {c for c in channels if c in ("email", "whatsapp", "in-app", "inapp", "cards", "sms")}
    missing = sorted(c for c in need if c not in disc and c.replace("inapp", "in-app") not in disc)
    if missing:
        flag("asci_disclaimer", "high", f"steps on {', '.join(missing)} but the SOP does not carry the ASCI VDA disclaimer there", "add these channels to compliance.disclaimer_channels (verbatim ASCI wording; English on the landing screen)", auto=True)
    else:
        strengths.append("ASCI disclaimer carried on every channel that can hold it")
    if "push" in channels and sop.get("campaign_type") in ("competition", "market") and "push" in channels:
        flag("push_promo", "medium", "promotional push cannot carry the disclaimer", "keep the push factual and non-promotional; landing screen carries the ASCI text", auto=False)
    # 2. forbidden words
    for rx, rule, fix in ((FORBIDDEN_VDA, "asci_forbidden_words", "ASCI forbids 'currency', 'securities', 'custodian', 'depositories' for VDA products; rephrase"), (BANNED, "banned_claims", "remove the claim; copy never forecasts, lures or implies safety"), (VENUES, "venue_named", "we never name a venue or competitor in user-facing procedure text")):
        m = rx.search(txt)
        if m:
            flag(rule, "high", f"'{m.group(0)}' appears in the SOP text", fix)
    # 3. derivatives posture
    if is_deriv:
        if ACQUISITION_DERIV.search(txt):
            flag("derivatives_acquisition", "high", f"derivatives SOP contains acquisition language: '{ACQUISITION_DERIV.search(txt).group(0)}'", "India posture is education-only until counsel clears: teach margin/funding/liquidation and risk tools, never 'trade now'")
        want = {"leverage_upsell", "size_up", "first_futures_trade"}
        if not (want & banned):
            flag("derivatives_banned_angles", "medium", "derivatives SOP does not ban leverage_upsell / size_up / first_futures_trade", "add them to compliance.banned_angles", auto=True)
        if not ({"email", "in-app"} & disc):
            flag("derivatives_risk_block", "high", "derivatives education must append the risk block (email/in-app)", "add email and in-app to disclaimer channels; push points to a landing screen with the block", auto=True)
        if "liquidat" not in excl:
            flag("exclude_liquidated", "high", "derivatives SOP does not exclude users liquidated in the last 14 days", "add 'liquidated in last 14d' to audience.exclusions", auto=True)
        else:
            strengths.append("liquidated-14d users excluded")
    # 4. incentives in onboarding
    if is_onboarding and INCENTIVE.search(txt) and "bonus" not in banned:
        flag("onboarding_incentive", "high", f"onboarding SOP mentions '{INCENTIVE.search(txt).group(0)}'", "KYC/deposit bonuses attract fraud, train bonus-seekers and raise ASCI risk; ban the angle and remove the mention", auto=True)
    elif is_onboarding and "bonus" not in banned:
        flag("onboarding_ban_bonus", "low", "onboarding SOP does not explicitly ban bonus angles", "add 'bonus' to compliance.banned_angles", auto=True)
    # 5. tax / TDS transparency where money moves
    money_moves = sop.get("transition") in FUNNEL_TAX or sop.get("campaign_type") == "newsletter" or bool(re.search(r"\b(first trade|second trade|fee tier|fees|statement|recap|sell)\b", sop.get("name", "") + " " + sop.get("objective", ""), re.I))
    if money_moves and not TAX_WORDS.search(txt):
        flag("tds_transparency", "medium", "money moves in this journey but no step mentions fees/1% TDS/30% tax framing", "add a fees-and-TDS transparency line (inform, never imply avoidance) to the education step", auto=True)
    elif TAX_WORDS.search(txt):
        strengths.append("fees/TDS framing present")
    # 6. UPI / IMPS specifics for deposits
    if sop.get("transition") == "verified_funded" and not UPI_WORDS.search(txt):
        flag("upi_specifics", "medium", "deposit journey without UPI/IMPS specifics", "name the rails (UPI in ~20s, IMPS fallback, bank-timeout explanation) and the salary-week timing", auto=True)
    # 7. send windows (IST)
    for st in steps:
        ch = str(st.get("channel", "")).lower(); t = _hhmm(st.get("send_time_ist"))
        if t is None:
            continue
        if ch == "push" and not (8 * 60 <= t <= 22 * 60):
            flag("quiet_hours", "medium", f"push step at {st.get('send_time_ist')} IST outside 08:00–22:00", "move inside quiet-hour rules", auto=True)
        if ch in ("whatsapp", "sms") and not (10 * 60 <= t <= 21 * 60):
            flag("dlt_window", "high", f"{ch} step at {st.get('send_time_ist')} IST outside 10:00–21:00 (TRAI/DLT window)", "move to 10:00–21:00", auto=True)
    # 8. WhatsApp utility, SMS DLT
    for st in steps:
        ch = str(st.get("channel", "")).lower(); brief = str(st.get("copy_brief", ""))
        if ch == "whatsapp" and not UTILITY.search(brief):
            flag("whatsapp_utility", "medium", f"WhatsApp step '{str(st.get('purpose', ''))[:50]}' is not marked as an approved utility/opt-in template", "WhatsApp only to opted-in users with approved templates; mark the step as a utility template", auto=True)
        if ch == "sms" and "dlt" not in brief.lower():
            flag("sms_dlt", "high", "SMS step without a DLT-registered template note", "SMS via DLT-registered templates only, 10:00–21:00", auto=True)
    # 9. Hinglish for mass cohorts
    if is_mass and any(c in ("push", "whatsapp") for c in channels) and not HINGLISH.search(txt):
        flag("hinglish_variant", "low", "mass cohort on push/WhatsApp without a Hinglish/regional variant plan", "add a Hinglish variant for app_language ≠ en (tickers and numbers in English; disclaimer stays English)", auto=True)
    elif HINGLISH.search(txt):
        strengths.append("Hinglish/regional variant planned")
    # 10. DND / unsubscribed exclusion and frequency
    if "dnd" not in excl and "unsub" not in excl:
        flag("dnd_exclusion", "high", "no DND / unsubscribed exclusion", "add 'unsubscribed / DND' to audience.exclusions", auto=True)
    pw = (sop.get("frequency") or {}).get("max_messages_per_user_per_week")
    if pw is not None and float(pw) > 4:
        flag("frequency_cap", "medium", f"{pw} messages/user/week exceeds the India push cap of 4", "reduce to ≤ 4 or move steps to Cards/in-app", auto=False)
    # 11. salary-week timing for deposit / SIP journeys
    if sop.get("transition") in ("verified_funded",) or "SIP" in fam:
        if not re.search(r"salary|1[–-]7|2[–-]6 of (the )?month", txt, re.I):
            flag("salary_week", "low", "deposit/SIP journey without salary-week timing", "schedule waves for days 2–6 of the month when deposit propensity peaks", auto=True)
        else:
            strengths.append("salary-week timing considered")
    # 12. urgency on price for market SOPs
    if sop.get("campaign_type") == "market" and re.search(r"countdown|hurry|now or never|ends soon", txt, re.I):
        flag("price_urgency", "high", "urgency attached to a market move", "countdowns only for genuine product deadlines, never price")
    weight = {"high": 18, "medium": 9, "low": 4}
    score = max(0, 100 - sum(weight[f["severity"]] for f in flags))
    status = "india-ready" if score >= 85 else "needs edits" if score >= 60 else "rework"
    return {"id": sop["id"], "name": sop["name"], "campaign_type": sop.get("campaign_type"), "transition": sop.get("transition"), "family": fam, "version": sop.get("version"), "score": score, "status": status,
            "flags": flags, "strengths": strengths[:5], "auto_fixable": sum(1 for f in flags if f["auto_fixable"]), "derivatives": is_deriv, "mass_cohort": is_mass}


def review(sop_id: Optional[str] = None) -> Dict[str, Any]:
    from .sops import list_sops, get_sop
    ids = [sop_id] if sop_id else [s["id"] for s in list_sops()]
    rows = [review_one(get_sop(i)) for i in ids if get_sop(i)]
    rows.sort(key=lambda r: r["score"])
    counts = {k: sum(1 for r in rows if r["status"] == k) for k in ("india-ready", "needs edits", "rework")}
    rule_counts: Dict[str, int] = {}
    for r in rows:
        for f in r["flags"]:
            rule_counts[f["rule"]] = rule_counts.get(f["rule"], 0) + 1
    missing = _missing_india_sops([s["id"] for s in list_sops()])
    return {"sops": rows, "counts": counts, "avg_score": round(sum(r["score"] for r in rows) / max(1, len(rows)), 1), "top_rules": sorted(rule_counts.items(), key=lambda kv: -kv[1])[:8], "missing_india_sops": missing,
            "auto_fixable_sops": sum(1 for r in rows if r["auto_fixable"]), "rules_source": "crypto-compliance-copy · crypto-copywriting · crypto-growth-calendar · clm-campaign-playbook"}


INDIA_SOPS = [
    ("sop_tax_season_explainer", "ITR / TDS season explainer (1–15 Jul) with TDS statements"),
    ("sop_salary_week_deposit_wave", "Salary-week (2–6 of month) deposit and recurring-buy wave for KYC-approved non-depositors"),
    ("sop_muhurat_markets_never_close", "Diwali / Muhurat: 'markets that never close' tokenised education moment; greetings without offers to loss-dormant users"),
    ("sop_budget_day_brief", "Union Budget day (1 Feb) macro brief: what changed for VDA tax, education only"),
    ("sop_hinglish_variant_programme", "Hinglish / regional variants for every mass push and WhatsApp step (app_language ≠ en)"),
    ("sop_tax_loss_season_education", "March tax-loss season: education on VDA loss rules (no set-off), no advice"),
]


def _missing_india_sops(existing: List[str]) -> List[Dict[str, str]]:
    have = set(existing)
    return [{"id": i, "what": w} for i, w in INDIA_SOPS if i not in have]


# ── deterministic fixes → new SOP version ─────────────────────────────────────
def apply_fixes(sop_id: str, actor: str = "user") -> Dict[str, Any]:
    import copy
    from .sops import get_sop, define_sop
    sop = get_sop(sop_id)
    if not sop:
        return {"ok": False, "error": f"unknown SOP {sop_id}"}
    before = review_one(sop); spec = copy.deepcopy(sop)
    for k in ("framework_ok", "framework_problems", "steps_count", "active", "updated_at"):
        spec.pop(k, None)
    comp = spec.setdefault("compliance", {}); disc = list(comp.get("disclaimer_channels") or []); banned = list(comp.get("banned_angles") or [])
    aud = spec.setdefault("audience", {}); excl = list(aud.get("exclusions") or [])
    rules = {f["rule"] for f in before["flags"] if f["auto_fixable"]}
    changes: List[str] = []
    steps = spec.get("steps") or []
    chans = {str(st.get("channel", "")).lower() for st in steps}
    if "asci_disclaimer" in rules:
        for c in ("email", "whatsapp", "in-app", "cards", "sms"):
            if c in chans and c not in disc:
                disc.append(c)
        changes.append("ASCI disclaimer on every carrying channel")
    if "derivatives_banned_angles" in rules:
        for b in ("leverage_upsell", "size_up", "first_futures_trade"):
            if b not in banned:
                banned.append(b)
        changes.append("banned leverage_upsell / size_up / first_futures_trade")
    if "derivatives_risk_block" in rules:
        for c in ("email", "in-app"):
            if c not in disc:
                disc.append(c)
        spec["objective"] = (spec.get("objective") or "").rstrip(". ") + ". Every education asset appends the derivatives risk block; push routes to a landing screen carrying it."
        changes.append("derivatives risk block")
    if "exclude_liquidated" in rules and not any("liquidat" in x.lower() for x in excl):
        excl.append("liquidated in last 14d"); changes.append("exclude liquidated 14d")
    if "onboarding_incentive" in rules or "onboarding_ban_bonus" in rules:
        if "bonus" not in banned:
            banned.append("bonus")
        changes.append("bonus angle banned")
    if "dnd_exclusion" in rules and not any(("dnd" in x.lower() or "unsub" in x.lower()) for x in excl):
        excl.insert(0, "unsubscribed / DND"); changes.append("DND / unsubscribed excluded")
    for st in steps:
        ch = str(st.get("channel", "")).lower()
        def brief():
            return str(st.get("copy_brief", ""))
        if "tds_transparency" in rules and ch in ("email", "in-app", "cards") and "TDS" not in brief() and not st.get("_tds_done"):
            st["copy_brief"] = brief().rstrip(". ") + ". India: state fees and the 1% TDS on sells plainly (inform, never imply avoidance)."; st["_tds_done"] = True; rules.discard("tds_transparency"); changes.append("fees + TDS line")
        if "upi_specifics" in rules and ch in ("in-app", "push", "whatsapp") and not UPI_WORDS.search(brief()):
            st["copy_brief"] = brief().rstrip(". ") + ". Rails: UPI in ~20 seconds, IMPS fallback, bank-timeout explanation."; rules.discard("upi_specifics"); changes.append("UPI/IMPS specifics")
        if "quiet_hours" in rules and ch == "push":
            t = _hhmm(st.get("send_time_ist"))
            if t is not None and not (8 * 60 <= t <= 22 * 60):
                st["send_time_ist"] = "19:00"; changes.append("push moved inside 08:00–22:00")
        if "dlt_window" in rules and ch in ("whatsapp", "sms"):
            t = _hhmm(st.get("send_time_ist"))
            if t is not None and not (10 * 60 <= t <= 21 * 60):
                st["send_time_ist"] = "11:00"; changes.append(f"{ch} moved inside 10:00–21:00")
        if "whatsapp_utility" in rules and ch == "whatsapp" and not UTILITY.search(brief()):
            st["copy_brief"] = "Utility template (opted-in users, approved template): " + brief(); changes.append("WhatsApp marked utility/opt-in")
        if "sms_dlt" in rules and ch == "sms" and "dlt" not in brief().lower():
            st["copy_brief"] = "DLT-registered template, 10:00–21:00: " + brief(); changes.append("SMS DLT note")
        if "hinglish_variant" in rules and ch in ("push", "whatsapp") and not HINGLISH.search(brief()):
            st["copy_brief"] = brief().rstrip(". ") + ". Hinglish variant for app_language ≠ en (tickers/numbers in English)."; rules.discard("hinglish_variant"); changes.append("Hinglish variant")
        st.pop("_tds_done", None)
    if "salary_week" in rules:
        spec["ideas"] = list(spec.get("ideas") or []) + ["Run waves on days 2–6 of the month (salary week): deposit propensity peaks."]
        spec["objective"] = (spec.get("objective") or "").rstrip(". ") + ". Timed to salary week (2–6 of the month)."
        changes.append("salary-week timing")
    comp["disclaimer_channels"] = disc; comp["banned_angles"] = banned; aud["exclusions"] = excl
    if not changes:
        return {"ok": True, "id": sop_id, "changed": [], "note": "nothing auto-fixable; remaining flags need a human edit", "before": before["score"], "after": before["score"]}
    res = define_sop(spec, author=actor)
    if not res.get("ok"):
        return {"ok": False, "id": sop_id, "problems": res.get("problems"), "changed": changes}
    after = review_one(get_sop(sop_id))
    audit("sop.india_fix", {"id": sop_id, "version": res.get("version"), "changes": changes, "score": [before["score"], after["score"]]}, actor=actor)
    return {"ok": True, "id": sop_id, "version": res.get("version"), "changed": sorted(set(changes)), "before": before["score"], "after": after["score"], "remaining": [f for f in after["flags"]]}


def apply_all(actor: str = "user", min_gain: int = 1) -> Dict[str, Any]:
    from .sops import list_sops
    out = []
    for s in list_sops():
        r = apply_fixes(s["id"], actor=actor)
        if r.get("changed"):
            out.append({"id": s["id"], "version": r.get("version"), "before": r.get("before"), "after": r.get("after"), "changed": r["changed"]})
    return {"fixed": out, "count": len(out), "review": {k: v for k, v in review().items() if k in ("counts", "avg_score", "top_rules")}}
