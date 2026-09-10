"""
Product affinity: the dimension that decides which programme a user gets.
CoinDCX products → codes seen in segment/campaign names → treatment rules,
announcement lenses and cross-sell paths (only on intent). Used by the segment
study (families → products), the SOP engine (product cohort matrix) and the
Tier-0 global announcement SOP (one fact, one lens per product cohort).
"""
from __future__ import annotations
from typing import Any, Dict, List

PRODUCTS: Dict[str, Dict[str, Any]] = {
    "spot":        dict(name="Spot", codes=["SPOT", "SPOTTRADER", "SPOTTRADERS", "SPT"], pillars=["watchlist", "alerts", "fees/TDS", "recurring buy"], lens="what moved on your watchlist and one tool (alert / SIP)", never=["leverage", "asset recommendation"], cadence="up to 4 push / 3 email per week", cross_sell=["sip (habit)", "perps (only on intent)", "earn (idle balance)"]),
    "sip":         dict(name="SIP / recurring buy", codes=["SIP", "RECURRING", "DCA", "AUTOINVEST"], pillars=["plan continuity", "DCA education", "statements"], lens="your plan continues; what volatility means for a recurring buyer (education, no advice)", never=["market timing prompts", "leverage", "pause suggestions"], cadence="≤ 2 touches / week; monthly statement", cross_sell=["spot alerts", "earn"]),
    "perps_crypto": dict(name="Crypto perps", codes=["PERP", "PERPS", "FUTURES", "FUT", "LEV", "LEVERAGE", "MARGIN", "HFT"], pillars=["risk tools", "funding/OI notes", "fee tiers", "macro briefs"], lens="margin buffer, funding, liquidation distance — tools, no direction", never=["size-up", "leverage lure", "P&L leaderboards"], cadence="≤ 4 push / week; 1 market push / day", cross_sell=["tokenised markets (US hours / equity watchlist)", "commodities (volatile regimes, education)"]),
    "perps_us_stocks": dict(name="US stock perps (tokenised)", codes=["USS", "USSTOCK", "USSTOCKS", "STOCK", "STOCKS", "TOKENISED", "TOKENIZED", "XSTOCK", "EQUITY", "NVDA", "TSLA"], pillars=["24/7 access", "after-hours moves", "earnings-week risk notes"], lens="US names moved after close / pre-market — 24/7 on CoinDCX; risk note for earnings weeks", never=["'trade earnings'", "forecasts", "venue names"], cadence="≤ 3 push / week around US hours", cross_sell=["indices", "commodities"]),
    "perps_indices": dict(name="Index & ETF perps", codes=["IDX", "INDEX", "INDICES", "ETF", "ETFS", "SPX", "NDX", "SP500", "NASDAQ", "SPY", "QQQ", "IWM", "TLT", "GLD", "SOXL", "IBIT"], pillars=["macro calendar", "24/7 hedging education", "ETF basics (what the fund holds, leverage/decay on 3× products)"], lens="index / ETF move + macro context; hedge/risk tools; for leveraged ETFs the decay note, never a lure", never=["direction", "'3× returns' framing"], cadence="≤ 2 push / week", cross_sell=["US stocks", "commodities"]),
    "perps_commodities": dict(name="Commodity perps", codes=["COMM", "COMMODITY", "COMMODITIES", "GOLD", "XAU", "SILVER", "OIL", "BRENT", "NATGAS"], pillars=["macro/geopolitics context", "24/7 education"], lens="gold/oil move with the macro fact; education, no safe-haven claims", never=["safe-haven claims", "direction"], cadence="≤ 2 push / week", cross_sell=["indices"]),
    "options":     dict(name="Crypto options", codes=["OPT", "OPTIONS", "OPTION", "CALL", "PUT", "STRADDLE"], pillars=["defined-risk education", "expiry calendar", "greeks basics"], lens="expiry / IV context as education; max-loss framing; no strategy recommendation", never=["strategy tips", "'cheap premium' lures"], cadence="≤ 2 touches / week; expiry-day notes", cross_sell=["perps risk tools"]),
    "earn":        dict(name="Earn", codes=["EARN", "STAKE", "STAKING", "YIELD", "LEND", "FIXED"], pillars=["variable APR transparency", "risks", "statements"], lens="what changes for your earn position (rates are variable); no yield-as-income", never=["guaranteed", "passive income", "APR as headline"], cadence="≤ 1 push / week; monthly statement", cross_sell=["SIP"]),
    "web3":        dict(name="Web3 wallet / DEX", codes=["WEB3", "DEX", "ONCHAIN", "WALLET", "SOL", "SOLANA", "BASE", "BNB", "BSC", "ETH", "ETHEREUM", "ROBINHOOD", "RH", "MEME", "MEMECOIN", "DEGEN", "SWAP"], pillars=["chain trends as data (unverified)", "safety: scams, slippage, gas", "watchlist"], lens="what is trending on your chains (unverified; volume/liquidity facts), plus a safety note; never a token pick", never=["token recommendations", "'100x'", "boosted/paid tokens as trends", "airdrop hype"], cadence="≤ 3 push / week; 1 trend push / day", cross_sell=["spot for majors", "earn for stablecoins"]),
}
DEFAULT_PRODUCT = "spot"


def codes_index() -> Dict[str, str]:
    idx = {}
    for pid, p in PRODUCTS.items():
        for c in p["codes"]:
            idx[c.upper()] = pid
    return idx


def affinity_from_tokens(tokens: List[str], facets: Dict[str, List[str]]) -> List[str]:
    """Products implied by a segment/campaign name (codes + product facets). Order = specificity (web3/derivatives before spot)."""
    idx = codes_index(); found: List[str] = []
    for t in tokens:
        pid = idx.get(str(t).upper().replace("'", ""))
        if pid and pid not in found:
            found.append(pid)
    for v in facets.get("product", []) or []:
        vl = v.lower()
        for pid, p in PRODUCTS.items():
            if pid not in found and any(k in vl for k in (p["name"].lower().split(" ")[0], pid.split("_")[-1])):
                found.append(pid)
    if not found:
        return []
    order = ["web3", "options", "perps_us_stocks", "perps_indices", "perps_commodities", "perps_crypto", "earn", "sip", "spot"]
    return sorted(found, key=lambda x: order.index(x) if x in order else 99)


def treatment(pid: str) -> Dict[str, Any]:
    p = PRODUCTS.get(pid) or PRODUCTS[DEFAULT_PRODUCT]
    return {"product": pid, "name": p["name"], "pillars": p["pillars"], "cadence": p["cadence"], "never": p["never"], "cross_sell_on_intent_only": p["cross_sell"], "announcement_lens": p["lens"]}


def announcement_lenses(event: str) -> Dict[str, Any]:
    """Tier-0 plan: one verified fact for everyone, one lens per product cohort, plus the suppression rules that always apply."""
    return {"event": event, "core_fact_rules": ["one sentence, verifiable in-app, time-stamped", "no forecast, no direction, no urgency on price", "service tone; support link where relevant"],
            "lenses": {pid: {"cohort": p["name"], "lens": p["lens"], "never": p["never"]} for pid, p in PRODUCTS.items()},
            "always_suppress": ["liquidated 14d (service card only)", "loss-dormant (email only, service)", "unsubscribed / DND", "open ticket"],
            "channels": {"push": "only cohorts with a tool to act on (perps: margin; spot: alerts); TTL 4h", "in-app": "everyone active; bottom sheet with the fact + lens", "email": "same-day summary for email opt-ins", "cards": "persistent recap"},
            "regime_rule": "in capitulation / high-volatility-down the lens is service-only for every cohort; promotional angles are blocked by policy"}
