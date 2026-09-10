"""
Market Feed intelligence layers built from the market context (no extra
network): a breaking-now flash strip, biggest news, top open-interest assets,
top assets per category, and intel per CoinDCX product. Everything is a fact
with a product lens; nothing here is a recommendation to users.
"""
from __future__ import annotations
import json
import re
import os
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional

from ..products import PRODUCTS
from . import sources

URG_ORDER = {"alert": 0, "warn": 1, "good": 2, "info": 3}


def _pub_dt(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        d = parsedate_to_datetime(s)
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:
        try:
            d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except Exception:
            return None


def _age_h(s: Optional[str]) -> Optional[float]:
    d = _pub_dt(s)
    return round((datetime.now(timezone.utc) - d).total_seconds() / 3600, 1) if d else None


def flash(ctx: Dict[str, Any], hours: int = 6) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    t0 = ctx.get("tier0")
    if t0:
        out.append({"t": "now", "category": "tier-0", "headline": f"Tier-0: {t0['kind'].replace('_', ' ')}", "detail": t0["detail"], "urgency": "alert", "products": list(PRODUCTS)})
    reg = (ctx.get("crypto") or {}).get("regime") or {}
    if reg.get("label"):
        out.append({"t": "now", "category": "regime", "headline": f"Regime {reg['label'].replace('_', ' ')}", "detail": "; ".join((reg.get("reasons") or [])[:2]), "urgency": "alert" if reg["label"] in ("capitulation", "high_volatility_down") else "info", "products": ["perps_crypto", "spot", "sip"]})
    md = ctx.get("crypto_movers_detail") or {}
    for m in (md.get("up") or [])[:3] + (md.get("down") or [])[:3]:
        if abs(m.get("chg_24h") or 0) >= 5:
            out.append({"t": "24h", "category": "crypto move", "headline": f"{m['symbol']} {m['chg_24h']:+.1f}%", "detail": f"${(m.get('vol_24h_usd') or 0) / 1e6:.0f}M volume · watchlist/alert angle only", "urgency": "warn" if m["chg_24h"] < 0 else "good", "products": ["spot", "perps_crypto", "web3"]})
    for key, label, thr, prods in (("equity_movers", "US stock perp", 3, ["perps_us_stocks"]), ("index_movers", "index perp", 1.5, ["perps_indices"]), ("commodity_movers", "commodity perp", 2, ["perps_commodities"])):
        for m in (ctx.get(key) or [])[:3]:
            if abs(m.get("chg_24h") or 0) >= thr:
                out.append({"t": "24h", "category": label, "headline": f"{m.get('name') or m.get('symbol')} {m['chg_24h']:+.1f}%", "detail": "24/7 on CoinDCX · education / after-hours angle", "urgency": "warn" if m["chg_24h"] < 0 else "good", "products": prods})
    oi = ctx.get("oi_movers") or {}
    for o in (oi.get("surge") or [])[:3]:
        out.append({"t": "~24h", "category": "open interest", "headline": f"{o['symbol']} OI {o['oi_chg_pct']:+.0f}%", "detail": o.get("reading", ""), "urgency": "warn", "products": ["perps_crypto"]})
    for o in (oi.get("drop") or [])[:2]:
        out.append({"t": "~24h", "category": "open interest", "headline": f"{o['symbol']} OI {o['oi_chg_pct']:+.0f}%", "detail": o.get("reading", ""), "urgency": "info", "products": ["perps_crypto"]})
    for n in ((ctx.get("listings") or {}).get("new") or [])[:4]:
        out.append({"t": "today", "category": "listing", "headline": f"{n['symbol']} newly tradable ({n['product']})", "detail": "spotlight to watchers; no launch-pump framing", "urgency": "good", "products": ["spot" if n["product"] == "spot" else "perps_crypto"]})
    for m in (md.get("crowded_long") or [])[:2]:
        out.append({"t": "now", "category": "funding", "headline": f"{m['symbol']} funding {m.get('funding_apr_pct', 0):+.0f}% APR (crowded long)", "detail": f"OI ${(m.get('oi_usd') or 0) / 1e6:.0f}M · risk-education angle", "urgency": "warn", "products": ["perps_crypto"]})
    for m in (md.get("crowded_short") or [])[:2]:
        out.append({"t": "now", "category": "funding", "headline": f"{m['symbol']} funding {m.get('funding_apr_pct', 0):+.0f}% APR (crowded short)", "detail": "shorts pay · risk-education angle", "urgency": "warn", "products": ["perps_crypto"]})
    news = ctx.get("news") or {}
    for f in (news.get("risk_flags") or [])[:8]:
        age = _age_h(f.get("published"))
        if age is None or age <= hours:
            out.append({"t": f"{age:.0f}h" if age is not None else "—", "category": "risk headline", "headline": (f.get("title") or "")[:110], "detail": f.get("source") or "", "urgency": "alert", "products": list(PRODUCTS), "url": f.get("link")})
    ci = ctx.get("competitors") or {}
    for a in [x for x in (ci.get("actions") or []) if x.get("priority", 0) >= 80][:3]:
        out.append({"t": "24h", "category": "competitor", "headline": f"{a['type'].replace('_', ' ')} · {a['symbol']}", "detail": a["what"][:120], "urgency": "warn", "products": ["spot", "perps_crypto"]})
    now = datetime.now(timezone.utc)
    for e in (ctx.get("calendar") or []):
        if e.get("impact") != "High":
            continue
        d = _pub_dt(e.get("date"))
        if d and 0 <= (d - now).total_seconds() <= 24 * 3600:
            out.append({"t": f"in {int((d - now).total_seconds() // 3600)}h", "category": "macro", "headline": f"{e.get('country')} {e.get('title')}", "detail": "T−24h risk brief · freeze market-linked promos T−2h→T+2h", "urgency": "warn", "products": ["perps_crypto", "perps_indices", "perps_commodities", "options"]})
    w3 = ctx.get("web3") or {}
    for r in (w3.get("trending") or [])[:2]:
        out.append({"t": "24h", "category": "web3", "headline": f"{r['symbol']} trending on {r['chain']}", "detail": f"${(r.get('vol_24h_usd') or 0) / 1e6:.1f}M vol · unverified token · education only", "urgency": "info", "products": ["web3"]})
    out.sort(key=lambda x: URG_ORDER.get(x["urgency"], 9))
    return out[:24]


def biggest_news(ctx: Dict[str, Any], limit: int = 10) -> List[Dict[str, Any]]:
    news = ctx.get("news") or {}
    rows = []
    seen = set()
    for cat, items in (news.get("top") or {}).items():
        for it in items[:6]:
            t = (it.get("title") or "").strip()
            key = t.lower()[:80]
            if not t or key in seen:
                continue
            seen.add(key)
            rows.append({"title": t[:140], "category": cat, "source": it.get("source"), "sentiment": it.get("sentiment"), "age_h": _age_h(it.get("published")), "url": it.get("link"),
                         "products": {"crypto": ["spot", "perps_crypto"], "stocks": ["perps_us_stocks", "perps_indices"], "commodities": ["perps_commodities"], "macro": ["perps_indices", "perps_crypto"], "regulatory": list(PRODUCTS)}.get(cat, [])})
    rows.sort(key=lambda r: ({"risk": 0, "positive": 1}.get(r["sentiment"], 2), r["age_h"] if r["age_h"] is not None else 99))
    return rows[:limit]


def _cached_onchain() -> Dict[str, Any]:
    try:
        p = os.path.join(sources.CACHE_DIR, "onchain_cex.json")
        return json.load(open(p)) if os.path.exists(p) else {}
    except Exception:
        return {}


def top_oi(ctx: Dict[str, Any], n: int = 10) -> Dict[str, Any]:
    crypto = sorted([m for m in (ctx.get("crypto_markets") or []) if m.get("oi_usd")], key=lambda m: -m["oi_usd"])[:n]
    oc = _cached_onchain(); per = {c["coin"]: c for c in (oc.get("per_coin") or [])}
    rows = []
    for m in crypto:
        pc = per.get(m["symbol"]) or {}
        rows.append({"symbol": m["symbol"], "oi_usd": m["oi_usd"], "chg_24h": m.get("chg_24h"), "funding_apr_pct": m.get("funding_apr_pct"), "vol_24h_usd": m.get("vol_24h_usd"), "hl_oi_share_pct": pc.get("hl_oi_share_pct"), "cheapest_for_longs": pc.get("cheapest_for_longs")})
    rwa = sorted([m for k in ("equities", "indices", "commodities") for m in (ctx.get(k) or []) if m.get("oi_usd")], key=lambda m: -m["oi_usd"])[:8]
    return {"crypto": rows, "tokenised": [{"symbol": m.get("name") or m.get("symbol"), "asset_class": m.get("asset_class"), "oi_usd": m["oi_usd"], "chg_24h": m.get("chg_24h"), "funding_1h_pct": m.get("funding_1h_pct")} for m in rwa], "source": "liquidity venue OI (reference); CEX share from the HL-vs-CEX comparison when cached"}


def top_by_category(ctx: Dict[str, Any]) -> Dict[str, Any]:
    md = ctx.get("crypto_movers_detail") or {}
    def slim(rows, n=8):
        return [{"symbol": r.get("name") or r.get("symbol"), "price": r.get("price"), "chg_24h": r.get("chg_24h"), "vol_24h_usd": r.get("vol_24h_usd"), "oi_usd": r.get("oi_usd")} for r in rows[:n]]
    cm = ctx.get("crypto_markets") or []
    return {"crypto": {"by_volume": slim(sorted(cm, key=lambda m: -(m.get("vol_24h_usd") or 0))), "gainers": slim(md.get("up") or [], 6), "losers": slim(md.get("down") or [], 6)},
            "us_stocks": {"by_volume": slim(sorted(ctx.get("equities") or [], key=lambda m: -(m.get("vol_24h_usd") or 0))), "movers": slim(ctx.get("equity_movers") or [], 6)},
            "indices": {"by_volume": slim(ctx.get("indices") or []), "movers": slim(ctx.get("index_movers") or [], 6)},
            "commodities": {"by_volume": slim(ctx.get("commodities") or []), "movers": slim(ctx.get("commodity_movers") or [], 6)},
            "fx": {"by_volume": slim(ctx.get("macro") or [], 6)},
            "web3": {"trending": [{"symbol": r.get("symbol"), "chain": r.get("chain"), "chg_24h": r.get("chg_24h"), "vol_24h_usd": r.get("vol_24h_usd"), "liquidity_usd": r.get("liquidity_usd")} for r in ((ctx.get("web3") or {}).get("trending") or [])[:8]]}}


# default SOP per product for an on-the-go alert campaign, by the alert category
ALERT_SOP = {
    "spot": {"crypto move": "sop_asset_spotlight", "listing": "sop_new_listing_watchers", "default": "sop_asset_spotlight"},
    "sip": {"regime": "sop_sip_nurture", "default": "sop_sip_nurture"},
    "perps_crypto": {"funding": "sop_funding_crowding_nudge", "open interest": "sop_oi_crowding_note", "macro": "sop_macro_print_brief", "crypto move": "sop_asset_spotlight", "listing": "sop_new_listing_watchers", "default": "sop_asset_spotlight"},
    "perps_us_stocks": {"US stock perp": "sop_tokenised_after_hours", "default": "sop_tokenised_after_hours"},
    "perps_indices": {"index perp": "sop_asset_spotlight", "macro": "sop_macro_print_brief", "default": "sop_macro_print_brief"},
    "perps_commodities": {"commodity perp": "sop_cross_sell_to_commodities", "macro": "sop_geopolitical_event_brief", "default": "sop_asset_spotlight"},
    "options": {"macro": "sop_options_education", "default": "sop_options_education"},
    "earn": {"default": "sop_cross_sell_earn"},
    "web3": {"web3": "sop_web3_trending_watch", "default": "sop_web3_trending_watch"},
}
CTA = {"spot": "View on CoinDCX", "sip": "Review your SIP", "perps_crypto": "Check your position", "perps_us_stocks": "View 24/7 markets", "perps_indices": "View index perps", "perps_commodities": "View commodities", "options": "Learn defined risk", "earn": "See earn options", "web3": "Open watchlist"}


def campaign_hint(product: str, fact: Dict[str, Any]) -> Dict[str, Any]:
    m = ALERT_SOP.get(product) or {"default": "sop_asset_spotlight"}
    return {"sop": m.get(fact.get("category"), m["default"]), "cta": CTA.get(product, "Open"), "product": product}


def by_product(ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    fl = flash(ctx, hours=24)
    n_all = len(PRODUCTS)
    global_alerts = [f for f in fl if len(f.get("products") or []) >= n_all]
    hooks = ((ctx.get("hooks") or {}).get("hooks") or [])
    try:
        from ..sops import product_cohort_matrix
        sop_map = {c["product"]: c["sops"] for c in product_cohort_matrix()["cohorts"]}
    except Exception:
        sop_map = {}
    hook_products = {"crypto": ["spot", "perps_crypto"], "equities (HL perps)": ["perps_us_stocks"], "indices (HL perps)": ["perps_indices"], "commodities (HL perps)": ["perps_commodities"], "web3": ["web3"], "competitive": ["spot", "perps_crypto"]}
    out = []
    for pid, p in PRODUCTS.items():
        facts = [dict(f, campaign=campaign_hint(pid, f)) for f in fl if pid in (f.get("products") or []) and len(f.get("products") or []) < n_all][:5]
        hk = [h for h in hooks if pid in hook_products.get(h.get("asset_class"), []) or (pid in ("spot", "sip") and h.get("angle") in ("reactivation", "graduation", "feature_discovery"))][:3]
        out.append({"product": pid, "name": p["name"], "lens": p["lens"], "facts": facts, "hooks": [{"id": h.get("id"), "trigger": h.get("trigger"), "angle": h.get("angle"), "sop": h.get("sop")} for h in hk],
                    "sops": sop_map.get(pid, [])[:3], "never": p["never"][:3], "status": "act" if any(f["urgency"] == "alert" for f in facts) else "watch" if any(f["urgency"] == "warn" for f in facts) else "quiet",
                    "global_alerts": [{"category": g["category"], "headline": g["headline"], "urgency": g["urgency"]} for g in global_alerts[:3]]})
    return out


# ── market-moving news filter ─────────────────────────────────────────────────
ASSET_ALIASES = {"BTC": ["bitcoin", "btc"], "ETH": ["ethereum", "ether", "eth"], "SOL": ["solana", "sol"], "XRP": ["xrp", "ripple"], "BNB": ["bnb", "binance coin"], "DOGE": ["dogecoin", "doge"], "ADA": ["cardano"], "AVAX": ["avalanche"], "TRX": ["tron"], "LINK": ["chainlink"], "DOT": ["polkadot"], "MATIC": ["polygon"], "POL": ["polygon"], "LTC": ["litecoin"], "TON": ["toncoin", " ton "], "SUI": ["sui"], "HYPE": ["hyperliquid token", "hype token"],
                 "GOLD": ["gold", "xau"], "SILVER": ["silver", "xag"], "BRENTOIL": ["brent", "crude", "oil price", "opec"], "NATGAS": ["natural gas", "natgas"], "COPPER": ["copper"], "PLATINUM": ["platinum"],
                 "NVDA": ["nvidia"], "TSLA": ["tesla"], "AAPL": ["apple"], "MSFT": ["microsoft"], "AMZN": ["amazon"], "GOOGL": ["alphabet", "google"], "META": ["meta platforms"], "COIN": ["coinbase stock", "coinbase shares"], "MSTR": ["microstrategy", "strategy inc"], "HOOD": ["robinhood"], "SPX": ["s&p 500", "s&p"], "NDX": ["nasdaq"], "DJI": ["dow jones"],
                 "JPY": ["yen", "boj"], "EUR": ["euro", "ecb"], "GBP": ["sterling", "pound", "boe"]}
DRIVERS = [
    ("liquidations", "high", re.compile(r"\b(liquidat(ed|ion|ions)|cascade|long squeeze|short squeeze|margin call|forced selling|wiped out|\$\d+(\.\d+)?\s?(m|million|b|billion) (in )?(longs|shorts|positions))\b", re.I)),
    ("macro", "high", re.compile(r"\b(fed|fomc|rate (cut|hike|decision)|cpi|inflation|payrolls|jobs report|treasury yields?|tariff|trade war|recession|gdp|rbi|repo rate|boj|ecb)\b", re.I)),
    ("regulatory", "high", re.compile(r"\b(sec|cftc|sebi|rbi|fiu|ban|banned|lawsuit|charges|approval|approved|etf|license|licence|regulation|regulator|tax|tds|crackdown|sanction)\b", re.I)),
    ("security", "high", re.compile(r"\b(hack|hacked|exploit|breach|drain|stolen|phishing|outage|halt|halted|withdrawals? (paused|suspended)|insolven|bankrupt)\b", re.I)),
    ("listings", "medium", re.compile(r"\b(list(s|ed|ing)|delist(s|ed|ing)|launch(es|ed)? (perpetual|futures|spot)|airdrop|token unlock|unlock)\b", re.I)),
    ("flows", "medium", re.compile(r"\b(inflow|outflow|whale|accumulat|treasury (buys|adds|purchase)|reserve|etf flows?|stablecoin (supply|mint|minted|burn))\b", re.I)),
    ("protocol", "medium", re.compile(r"\b(upgrade|hard ?fork|halving|mainnet|testnet|governance vote|network (congestion|down))\b", re.I)),
    ("earnings", "medium", re.compile(r"\b(earnings|guidance|quarterly results|revenue beat|misses estimates)\b", re.I)),
]


def assets_in_focus(ctx: Dict[str, Any], n_crypto: int = 40) -> List[Dict[str, Any]]:
    """The assets our users actually have exposure to: top crypto by OI/volume on our liquidity venue, tokenised equities/indices/commodities we list, FX macro."""
    out: List[Dict[str, Any]] = []
    cr = sorted([m for m in (ctx.get("crypto_markets") or []) if m.get("symbol")], key=lambda m: -((m.get("oi_usd") or 0) + (m.get("vol_24h_usd") or 0) * 0.2))[:n_crypto]
    for m in cr:
        out.append({"symbol": m["symbol"], "asset_class": "crypto", "chg_24h": m.get("chg_24h"), "oi_usd": m.get("oi_usd")})
    for key, cls in (("equities", "us_stock"), ("indices", "index"), ("commodities", "commodity"), ("macro", "fx")):
        for m in (ctx.get(key) or []):
            sym = str(m.get("symbol") or m.get("name") or "").upper()
            if sym:
                out.append({"symbol": sym, "asset_class": cls, "chg_24h": m.get("chg_24h"), "oi_usd": m.get("oi_usd")})
    return out


def _match_assets(text: str, focus: List[Dict[str, Any]]) -> List[str]:
    t = " " + text.lower() + " "
    hits = []
    for a in focus:
        sym = a["symbol"]
        aliases = ASSET_ALIASES.get(sym, []) + ([sym.lower()] if len(sym) >= 3 and sym.isalpha() else [])
        for al in aliases:
            if re.search(r"(?<![a-z0-9])" + re.escape(al.strip()) + r"(?![a-z0-9])", t):
                hits.append(sym); break
    return list(dict.fromkeys(hits))


def market_moving_news(ctx: Dict[str, Any], limit: int = 20) -> Dict[str, Any]:
    """Headlines that can move the assets in focus, tagged by driver (liquidations, macro, regulatory, security, listings, flows, protocol, earnings) with the assets they touch; plus synthetic liquidation-cascade items from OI and price data."""
    focus = assets_in_focus(ctx)
    focus_syms = {a["symbol"] for a in focus}
    rows: List[Dict[str, Any]] = []
    seen = set()
    top = (ctx.get("news") or {}).get("top") or {}
    for cat, items in (top.items() if isinstance(top, dict) else []):
        for it in (items or [])[:12]:
            if not isinstance(it, dict):
                continue
            t = (it.get("title") or "").strip()
            key = t.lower()[:80]
            if not t or key in seen:
                continue
            seen.add(key)
            drivers = [(name, sev) for name, sev, rx in DRIVERS if rx.search(t)]
            assets = _match_assets(t, focus)
            if not drivers and not assets:
                continue
            if not drivers and cat not in ("crypto", "commodities", "stocks", "macro", "regulatory"):
                continue
            sev = "high" if any(s == "high" for _, s in drivers) and (assets or cat in ("macro", "regulatory")) else "medium" if drivers or assets else "low"
            score = (3 if sev == "high" else 2 if sev == "medium" else 1) + min(3, len(assets)) + (1 if it.get("sentiment") == "risk" else 0)
            rows.append({"title": t[:150], "url": it.get("link"), "source": it.get("source"), "category": cat, "sentiment": it.get("sentiment"), "age_h": _age_h(it.get("published")), "drivers": [d for d, _ in drivers] or ["asset mention"], "assets": assets or (["broad market"] if cat in ("macro", "regulatory") else []), "impact": sev, "score": score,
                         "so_what": _so_what(drivers, assets, cat)})
    # synthetic: liquidation cascades visible in our own data (big move + OI drop, or funding whiplash)
    for m in (ctx.get("crypto_markets") or []):
        ch = m.get("chg_24h"); sym = m.get("symbol")
        if ch is None or abs(ch) < 8 or (m.get("vol_24h_usd") or 0) < 5e7 or (m.get("oi_usd") or 0) < 2e7 or sym not in focus_syms:
            continue
        oi_drop = None
        for om in (ctx.get("oi_movers") or []):
            if not isinstance(om, dict):
                continue
            if om.get("symbol") == sym and (om.get("oi_chg_pct") or om.get("chg_pct") or 0) < -5:
                oi_drop = om.get("oi_chg_pct") or om.get("chg_pct")
        if oi_drop is not None or abs(ch) >= 12:
            kind = "long liquidations likely" if ch < 0 else "short squeeze likely"
            rows.append({"title": f"{sym} {ch:+.1f}% in 24h on ${(m.get('oi_usd') or 0) / 1e6:.0f}M open interest" + (f" (OI {oi_drop:+.0f}%)" if oi_drop is not None else "") + f" — {kind}", "url": None, "source": "own venue data", "category": "crypto", "sentiment": "risk", "age_h": 0,
                         "drivers": ["liquidations"], "assets": [sym], "impact": "high" if (oi_drop is not None or abs(ch) >= 12) else "medium", "score": 6 if oi_drop is not None else 4, "so_what": "holders of this asset: risk-education and margin-buffer message today; suppress promos to anyone liquidated; service tone", "synthetic": True})
    rows.sort(key=lambda r: (-r["score"], r["age_h"] if r["age_h"] is not None else 99))
    by_driver: Dict[str, int] = {}
    for r in rows:
        for d in r["drivers"]:
            by_driver[d] = by_driver.get(d, 0) + 1
    return {"items": rows[:limit], "assets_in_focus": sorted(focus_syms)[:60], "by_driver": sorted(by_driver.items(), key=lambda kv: -kv[1]), "total_headlines": len(seen), "kept": len(rows),
            "note": "kept = headlines mentioning an asset we list or carrying a market-moving driver; synthetic items come from our own price/OI data"}


def _so_what(drivers, assets, cat) -> str:
    names = [d for d, _ in drivers]
    who = f"holders/watchers of {', '.join(assets[:3])}" if assets else "all actives"
    if "liquidations" in names:
        return f"{who}: risk tools + margin buffer now; suppress promos to liquidated users; no direction"
    if "security" in names:
        return f"{who}: service-tone note only if we are affected; otherwise silence; never amplify a rival's incident"
    if "regulatory" in names:
        return "compliance check before any send today; Tier-0 lenses if it is India-facing; education, not reassurance"
    if "macro" in names:
        return f"perp traders ({who}): T-24h risk brief per SOP macro_print_brief; no forecast"
    if "listings" in names:
        return f"{who}: listing fact + watchlist CTA if we list it; product ask if we do not"
    if "flows" in names:
        return f"{who}: fact + alert tool; watch for attention spike in the next 6h"
    if "earnings" in names:
        return f"{who} on tokenised markets: after-hours education, never 'trade earnings'"
    return f"{who}: asset spotlight only if it appears in attention spikes; otherwise monitor"
