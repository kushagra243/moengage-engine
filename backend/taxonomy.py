"""
Campaign taxonomy: turn naming conventions, tags and segment names into
structured facets so campaigns can be grouped and compared by programme,
cohort, propensity, value tier, trader type, product and channel.

The dictionary below is seeded from the conventions seen in the workspace
(GMC_Sep26_CS_Res_HighProp_HVS_8thSept, Re-KYC_LowRisk_Update_Reminder_8Sep26,
GMC_Sep'26_HFT_Futures_Ret_INJ blog_4Sep26 …) and is editable through the
`taxonomy_codes` setting (JSON) without a code change. Unknown tokens are kept
as `other` facets so nothing is silently dropped.
"""
from __future__ import annotations
import json
import re
from collections import defaultdict
from typing import Any, Dict, List, Optional

from .database import get_setting

DEFAULT_CODES: Dict[str, Dict[str, str]] = {
    # programme
    "GMC": "programme:Growth marketing calendar", "RE-KYC": "programme:Re-KYC compliance", "REKYC": "programme:Re-KYC compliance",
    "NEWSLETTER": "programme:Newsletter", "DIGEST": "programme:Daily digest", "CRYPTOPULSE": "programme:Newsletter",
    "ONBOARD": "programme:Onboarding", "ONBOARDING": "programme:Onboarding", "KYC": "programme:KYC",
    # cohort / lifecycle
    "CS": "cohort:Cross-sell", "RES": "cohort:Resurrection (dormant)", "RET": "cohort:Retention", "ACT": "cohort:Activation", "ACTIVATION": "cohort:Activation",
    "ACQ": "cohort:Acquisition", "WINBACK": "cohort:Resurrection (dormant)", "CHURN": "cohort:Churn risk", "NULL": "cohort:No prior product",
    # propensity / risk
    "HIGHPROP": "propensity:High propensity", "LOWPROP": "propensity:Low propensity", "MIDPROP": "propensity:Mid propensity",
    "LOWRISK": "risk:Low risk", "HIGHRISK": "risk:High risk", "MIDRISK": "risk:Mid risk",
    # value tier
    "HVS": "value:High value", "LVS": "value:Low value", "LIS": "value:Low intent", "HIS": "value:High intent", "MVS": "value:Mid value",
    # trader type
    "HFT": "trader:High-frequency trader", "BC": "trader:Buy-and-hold / basic", "LIF": "trader:Low-frequency / infrequent", "LF": "trader:Low-frequency / infrequent",
    "PRO": "trader:Pro", "WHALE": "trader:Whale", "VIP": "trader:VIP",
    # product
    "FUTURES": "product:Futures", "SPOT": "product:Spot", "MARGIN": "product:Margin", "EARN": "product:Earn", "STAKING": "product:Earn",
    "OPTIONS": "product:Options", "INJ": "product:INJ (asset)", "TG": "product:Trading game / tournament", "BLOG": "content:Blog",
    "PERP": "product:Perpetuals", "PERPS": "product:Perpetuals", "LEV": "product:Leverage", "LEVERAGE": "product:Leverage", "HL": "product:Hyperliquid perps",
    "TOKENISED": "product:Tokenised equities (HL)", "TOKENIZED": "product:Tokenised equities (HL)", "XSTOCK": "product:Tokenised equities (HL)", "STOCKS": "product:Tokenised equities (HL)",
    "GOLD": "product:Commodities (HL)", "XAU": "product:Commodities (HL)", "OIL": "product:Commodities (HL)", "SPX": "product:Indices (HL)", "NDX": "product:Indices (HL)",
    "LIQ": "cohort:Liquidation recovery", "LIQUIDATED": "cohort:Liquidation recovery", "HEDGE": "message:Hedging education", "FUNDING": "message:Funding", "OI": "message:Open interest",
    "LISTING": "message:New listing", "COMP": "message:Competition", "TOURNAMENT": "message:Competition", "AIRDROP": "message:Rewards", "POINTS": "message:Rewards", "REFERRAL": "message:Referral",
    # message type
    "REMINDER": "message:Reminder", "UPDATE": "message:Update", "EXPERTPICKS": "message:Expert picks", "EXPERTPICKSGTM": "message:Expert picks",
    "ALERT": "message:Alert", "PRICE": "message:Price", "DROP": "message:Price drop",
}
MONTH = re.compile(r"^(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|SEPT|OCT|NOV|DEC)'?\s?(\d{2,4})$", re.I)
DATE_TOKEN = re.compile(r"^(\d{1,2})(ST|ND|RD|TH)?(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|SEPT|OCT|NOV|DEC)'?(\d{2,4})?$", re.I)


def codes() -> Dict[str, str]:
    d = dict(DEFAULT_CODES)
    try:
        extra = json.loads(get_setting("taxonomy_codes", "") or "{}")
        if isinstance(extra, dict):
            d.update({str(k).upper(): str(v) for k, v in extra.items()})
    except Exception:
        pass
    return d


HYPHENATED = {"re-kyc": "REKYC", "cross-sell": "CS", "cross-sell.": "CS", "win-back": "WINBACK", "up-sell": "UPSELL", "re-engage": "RES", "re-engagement": "RES", "in-app": "INAPP"}


def tokens(name: str) -> List[str]:
    name = (name or "").replace("’", "'")
    low = name.lower()
    for k, v in HYPHENATED.items():
        if k in low:
            idx = low.index(k)
            name = name[:idx] + v + name[idx + len(k):]
            low = name.lower()
    parts = re.split(r"[\s_\-–—/|,]+", name)
    return [p.strip("()[]") for p in parts if p.strip("()[]")]


def classify(campaign: Dict[str, Any]) -> Dict[str, Any]:
    name = campaign.get("name") or campaign.get("campaign_name") or ""
    code_map = codes()
    facets: Dict[str, List[str]] = defaultdict(list)
    unknown: List[str] = []
    for t in tokens(name):
        u = t.upper().replace("'", "")
        if MONTH.match(u) or DATE_TOKEN.match(u):
            m = MONTH.match(u) or DATE_TOKEN.match(u)
            facets["period"].append(t)
            continue
        if u in code_map:
            k, v = code_map[u].split(":", 1)
            if v not in facets[k]:
                facets[k].append(v)
            continue
        # compound tokens like HighProp / LowRisk / ExpertPicksGTM
        hit = False
        for code, kv in code_map.items():
            if len(code) >= 4 and code in u and u != code:
                k, v = kv.split(":", 1)
                if v not in facets[k]:
                    facets[k].append(v); hit = True
        if not hit and len(u) > 1 and not u.isdigit():
            unknown.append(t)
    for tag in campaign.get("tags") or []:
        facets["tag"].append(str(tag))
    seg = campaign.get("target_segment") or ""
    if seg:
        facets["segment"].append(seg)
    if campaign.get("channel"):
        facets["channel"].append(str(campaign["channel"]))
    if campaign.get("delivery_type"):
        facets["delivery"].append(str(campaign["delivery_type"]))
    programme = (facets.get("programme") or ["Other"])[0]
    cohort = (facets.get("cohort") or ["Unclassified"])[0]
    key_parts = [programme, cohort] + facets.get("propensity", []) + facets.get("value", []) + facets.get("trader", []) + facets.get("product", [])
    return {"campaign_id": campaign.get("id"), "name": name, "programme": programme, "cohort": cohort,
            "facets": dict(facets), "group_key": " · ".join(key_parts), "unknown_tokens": unknown[:6]}


def group(campaigns: List[Dict[str, Any]], by: str = "group_key") -> List[Dict[str, Any]]:
    """Aggregate metrics per facet group with volume-weighted rates; returns groups sorted by delivered volume."""
    from .anomaly.store import normalise_campaign
    groups: Dict[str, Dict[str, Any]] = {}
    for c in campaigns:
        t = classify(c); n = normalise_campaign(c)
        if by == "group_key":
            keys = [t["group_key"]]
        elif by in ("programme", "cohort"):
            keys = [t[by]]
        else:
            keys = t["facets"].get(by) or ["(none)"]
        for k in keys:
            g = groups.setdefault(k, {"key": k, "campaigns": 0, "with_stats": 0, "sent": 0.0, "delivered": 0.0, "clicks": 0.0, "conv": 0.0, "revenue": 0.0, "names": []})
            g["campaigns"] += 1; g["names"].append(t["name"])
            if n["stats_missing"]:
                continue
            g["with_stats"] += 1
            d = n["delivered_count"] or 0.0
            g["sent"] += n["sent_count"] or 0.0; g["delivered"] += d
            g["clicks"] += (n["opened_count"] if n["opened_count"] is not None else d * (n["ctr"] or 0) / 100.0)
            g["conv"] += (n["conversions"] if n["conversions"] is not None else d * (n["conversion_rate"] or 0) / 100.0)
            g["revenue"] += n["revenue_generated"] or 0.0
    out = []
    for g in groups.values():
        out.append({**g, "names": g["names"][:8], "delivery_rate": round(g["delivered"] / g["sent"] * 100, 1) if g["sent"] else None,
                    "click_rate": round(g["clicks"] / g["delivered"] * 100, 2) if g["delivered"] else None,
                    "conversion_rate": round(g["conv"] / g["delivered"] * 100, 2) if g["delivered"] and g["conv"] else None})
    out.sort(key=lambda x: -x["delivered"])
    return out


def comparisons(campaigns: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Within-facet contrasts that carry insight: HighProp vs LowProp, HVS vs LVS, HFT vs LIF, channel vs channel …"""
    out = []
    for facet in ("propensity", "value", "trader", "cohort", "channel", "product", "programme"):
        rows = [g for g in group(campaigns, by=facet) if g["with_stats"] > 0 and g["delivered"] >= 1000]
        if len(rows) < 2:
            continue
        best = max(rows, key=lambda g: g["click_rate"] or 0); worst = min(rows, key=lambda g: g["click_rate"] or 0)
        if best is worst or best["click_rate"] is None or worst["click_rate"] is None:
            continue
        out.append({"facet": facet, "best": {"key": best["key"], "click_rate": best["click_rate"], "delivered": int(best["delivered"])},
                    "worst": {"key": worst["key"], "click_rate": worst["click_rate"], "delivered": int(worst["delivered"])},
                    "gap_pp": round(best["click_rate"] - worst["click_rate"], 2),
                    "reading": f"{facet}: '{best['key']}' clicks at {best['click_rate']}% vs '{worst['key']}' at {worst['click_rate']}% ({round(best['click_rate'] - worst['click_rate'], 1)} pp gap on {int(best['delivered'] + worst['delivered']):,} delivered)."})
    out.sort(key=lambda x: -abs(x["gap_pp"]))
    return out


def catalog(campaigns: List[Dict[str, Any]]) -> Dict[str, Any]:
    cls = [classify(c) for c in campaigns]
    unknown: Dict[str, int] = defaultdict(int)
    for t in cls:
        for u in t["unknown_tokens"]:
            unknown[u] += 1
    return {"campaigns": len(cls), "programmes": group(campaigns, "programme"), "cohorts": group(campaigns, "cohort"),
            "facets_seen": sorted({k for t in cls for k in t["facets"].keys()}),
            "unknown_tokens": sorted(unknown.items(), key=lambda kv: -kv[1])[:30],
            "comparisons": comparisons(campaigns)}
