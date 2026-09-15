"""
The SOP library seen by product and by team — so every team knows what CLM
does for their product without reading 57 documents.

by_product()  each product (spot, SIP, crypto perps, US-stock / index & ETF /
              commodity perps, options, earn, web3): the SOPs that touch it,
              which lifecycle transitions are covered, which are not, the
              cadence and never-list that bind it, and a plain-English brief.
by_team()     each role: the SOPs it owns, the ones it reviews, and the process
              segments it is on the hook for.
gaps()        the uncovered product × transition cells, each ready to become a
              "please write this SOP" request (backend/sop_requests.py).
"""
from __future__ import annotations
import re
from typing import Any, Dict, List, Optional

PRODUCT_WORDS: Dict[str, List[str]] = {
    "spot": ["spot", "watchlist", "asset spotlight", "price alert", "new listing"],
    "sip": ["sip", "recurring buy", "recurring", "dca", "auto-invest", "autoinvest"],
    "perps_crypto": ["perp", "perps", "futures", "leverage", "margin", "funding", "open interest", "liquidat", "hedger", "hft"],
    "perps_us_stocks": ["us stock", "us-stock", "tokenised", "tokenized", "equity", "after-hours", "after hours", "earnings", "xstock"],
    "perps_indices": ["index", "indices", "etf", "s&p", "nasdaq", "macro print", "sp500", "ustech"],
    "perps_commodities": ["commodit", "gold", "silver", "oil", "brent", "natgas", "xau"],
    "options": ["option", "expiry", "greeks", "straddle", "call/put"],
    "earn": ["earn", "staking", "stake", "yield", "apr", "idle balance"],
    "web3": ["web3", "wallet", "dex", "on-chain", "onchain", "trending token", "solana", "base chain"],
}
# Words that only hint. One of these alone does not make a SOP "about" the product — a perps SOP that
# happens to say "watchlist" is not a spot procedure. A hint needs a second hint, or a naming word.
WEAK: Dict[str, List[str]] = {
    "spot": ["watchlist", "asset spotlight", "price alert", "new listing"],
    "sip": ["recurring", "dca", "auto-invest", "autoinvest"],
    "perps_crypto": ["leverage", "margin", "hedger", "hft", "funding"],
    "perps_us_stocks": ["equity", "after-hours", "after hours", "earnings"],
    "perps_indices": ["index", "indices", "macro print"],
    "perps_commodities": ["gold", "silver", "oil"],
    "options": ["expiry"],
    "earn": ["stake", "yield", "apr", "idle balance"],
    "web3": ["wallet", "trending token", "solana", "base chain"],
}
GENERIC = {"onboarding", "compliance", "cohort_upload", "newsletter"}          # types that serve every product


def _text(sop: Dict[str, Any]) -> str:
    parts = [sop.get("name", ""), sop.get("objective", ""), sop.get("user", ""), (sop.get("audience") or {}).get("segment_family", "")]
    for st in sop.get("steps") or []:
        parts += [str(st.get("purpose", "")), str(st.get("copy_brief", ""))]
    return " ".join(parts).lower()


def products_for(sop: Dict[str, Any]) -> List[str]:
    """Which products an SOP actually serves: explicit map first, then family codes and wording."""
    from .products import PRODUCTS, affinity_from_tokens
    from .segments import decode
    hits: List[str] = []
    fam = str((sop.get("audience") or {}).get("segment_family") or "")
    if fam and fam != "*":
        try:
            d = decode(fam)
            fl = fam.lower()
            # the decoder falls back to spot for families that name no product (DEP_FAILED, REKYC_PENDING…);
            # only count a product the family actually names, or the whole library reads as a spot library
            hits += [p for p in (d.get("products") or []) if p in PRODUCTS and any(w in fl for w in PRODUCT_WORDS.get(p, []))]
        except Exception:
            pass
    text = _text(sop)
    for pid, words in PRODUCT_WORDS.items():
        if pid in hits:
            continue
        weak = set(WEAK.get(pid) or [])
        found = [w for w in words if w in text]
        named = [w for w in found if w not in weak]
        if named or len(found) >= 2:          # a naming word, or two hints agreeing
            hits.append(pid)
    if len(hits) >= 4 or (not hits and str(sop.get("campaign_type")) in GENERIC):
        return ["all"]           # touches everything → it is a shared journey, not a product one
    return hits or ["all"]


def _ladder() -> List[Dict[str, str]]:
    from .llm.tools import TRANSITIONS
    return [{"id": tid, "label": label} for tid, label, _ in TRANSITIONS if tid not in ("promotional", "intent_dropoff")]


def by_product(product: Optional[str] = None) -> Dict[str, Any]:
    from .products import PRODUCTS
    from .sops import list_sops, get_sop
    from . import sop_ownership as own
    ladder = _ladder()
    rows: Dict[str, Dict[str, Any]] = {}
    for pid, p in PRODUCTS.items():
        rows[pid] = {"product": pid, "name": p["name"], "lens": p["lens"], "pillars": p["pillars"], "cadence": p["cadence"], "never": p["never"],
                     "cross_sell": p["cross_sell"], "sops": [], "transitions": {t["id"]: [] for t in ladder}, "own_transitions": {t["id"]: [] for t in ladder}}
    shared: List[Dict[str, Any]] = []
    for row in list_sops():
        sop = get_sop(row["id"])
        if not sop:
            continue
        o = own.owners_for(sop)
        item = {"id": sop["id"], "name": sop["name"], "type": sop.get("campaign_type"), "transition": sop.get("transition"),
                "family": (sop.get("audience") or {}).get("segment_family"), "owner": o["primary"], "steps": len(sop.get("steps") or []), "version": sop.get("version")}
        pids = products_for(sop)
        if pids == ["all"]:
            shared.append(item)
            for pid in rows:
                rows[pid]["sops"].append({**item, "shared": True})
                rows[pid]["transitions"].setdefault(sop.get("transition"), []).append(sop["id"])
        else:
            for pid in pids:
                if pid in rows:
                    rows[pid]["sops"].append(item)
                    rows[pid]["transitions"].setdefault(sop.get("transition"), []).append(sop["id"])
                    rows[pid]["own_transitions"].setdefault(sop.get("transition"), []).append(sop["id"])
    out = []
    for pid, r in rows.items():
        covered = [t for t in ladder if r["transitions"].get(t["id"])]
        own_covered = [t for t in ladder if r["own_transitions"].get(t["id"])]
        nothing = [t for t in ladder if not r["transitions"].get(t["id"])]
        no_lens = [t for t in ladder if not r["own_transitions"].get(t["id"])]
        own_sops = [s for s in r["sops"] if not s.get("shared")]
        r["coverage_pct"] = round(100 * len(covered) / max(1, len(ladder)))              # handled at all (shared journeys count)
        r["own_coverage_pct"] = round(100 * len(own_covered) / max(1, len(ladder)))      # handled as this product
        r["covered"] = [t["label"] for t in covered]
        r["own_covered"] = [t["label"] for t in own_covered]
        r["uncovered"] = [t["label"] for t in nothing]
        r["missing"] = [{"transition": t["id"], "label": t["label"], "shared_cover": bool(r["transitions"].get(t["id"])),
                         "suggested": f"{t['label'].split('→')[-1].strip()} for {r['name']}"} for t in no_lens]
        r["own_sops"] = len(own_sops); r["shared_sops"] = len(r["sops"]) - len(own_sops)
        r["brief"] = brief(r, own_sops, own_covered, r["missing"], nothing)
        r.pop("own_transitions", None)
        out.append(r)
    out.sort(key=lambda r: (-r["own_sops"], r["product"]))
    if product:
        one = next((r for r in out if r["product"] == product), None)
        return one or {"error": f"unknown product {product}", "available": list(rows)}
    return {"products": out, "shared": shared, "ladder": ladder,
            "note": "coverage_pct counts shared journeys (onboarding, KYC, compliance, service) that serve every product; own_coverage_pct counts only procedures written in this product's lens"}


def brief(r: Dict[str, Any], own_sops: List[Dict[str, Any]], own_covered: List[Dict[str, str]], missing: List[Dict[str, Any]], nothing: List[Dict[str, str]]) -> str:
    """What CLM does for this product, in one paragraph a non-CRM team can read."""
    names = ", ".join(s["name"] for s in own_sops[:4]) or "nothing product-specific yet"
    shared_only = [m["label"] for m in missing if m["shared_cover"]]
    return (f"For {r['name']}, lifecycle marketing runs {len(own_sops)} procedure(s) written for this product, on top of the {r['shared_sops']} shared journeys "
            f"(onboarding, KYC, deposit failure, compliance, service) that every product gets. "
            f"The message is always {r['lens']}. We never {('; '.join(r['never'])).lower()}. Cadence: {r['cadence']}. "
            f"Running today: {names}. Stages with a {r['name']}-specific procedure: {', '.join(t['label'] for t in own_covered) or 'none'}. "
            + (f"Stages handled only by a shared journey, with no {r['name']} lens: {', '.join(shared_only)}. " if shared_only else "")
            + (f"Stages with nothing at all: {', '.join(t['label'] for t in nothing)}." if nothing else "Every stage is handled by something."))


def by_team() -> Dict[str, Any]:
    from .sops import list_sops, get_sop
    from . import sop_ownership as own
    roles = own.roles()
    teams: Dict[str, Dict[str, Any]] = {k: {"key": k, "role": v["role"], "person": v.get("person"), "does": v["does"], "owns": [], "reviews": [], "segments": []} for k, v in roles.items()}
    for s in list_sops():
        sop = get_sop(s["id"])
        if not sop:
            continue
        o = own.owners_for(sop)
        item = {"id": sop["id"], "name": sop["name"], "products": [p for p in products_for(sop)]}
        teams.setdefault(o["primary_key"], {"key": o["primary_key"], "role": o["primary"], "does": "", "owns": [], "reviews": [], "segments": []})["owns"].append(item)
        for rk in o["reviewer_keys"]:
            if rk in teams:
                teams[rk]["reviews"].append(item)
    for seg in own.SEGMENTS:
        if seg["owner"] in teams:
            teams[seg["owner"]]["segments"].append(f"{seg['n']}. {seg['name']}")
    out = sorted(teams.values(), key=lambda t: -len(t["owns"]))
    for t in out:
        t["owns_count"] = len(t["owns"]); t["reviews_count"] = len(t["reviews"])
        t["must_know"] = [x["name"] for x in t["owns"][:5]]
        t["owns"] = t["owns"][:20]; t["reviews"] = t["reviews"][:20]
    return {"teams": out, "note": "ownership is derived from campaign type, channels and compliance needs; set real people in Engine → Settings → sop_owners_json"}


def gaps() -> Dict[str, Any]:
    """Uncovered product × stage cells, ready to become a request for a new SOP."""
    data = by_product()
    out = []
    for p in data["products"]:
        for m in p["missing"]:
            out.append({"product": p["product"], "product_name": p["name"], "transition": m["transition"], "label": m["label"], "shared_cover": m["shared_cover"],
                        "title": f"{m['label']} — {p['name']}",
                        "why": (f"{p['name']} has nothing at all for the {m['label']} stage — that transition is unmanaged for this product."
                                if not m["shared_cover"] else
                                f"{m['label']} is handled for {p['name']} only by a shared journey; there is no procedure written in the {p['name']} lens."),
                        "lens": p["lens"], "never": p["never"], "cadence": p["cadence"]})
    out.sort(key=lambda r: (r["shared_cover"], r["product"], r["transition"]))     # nothing-at-all first
    return {"gaps": out, "count": len(out), "products": len(data["products"]),
            "next": "request the SOP (SOP Library → request) — the brain drafts it and peers sign it off before it enters the library"}
