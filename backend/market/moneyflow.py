"""
Money flow and trader behaviour — where capital and attention are going
today, what that means traders are doing, and what we should do about it.

Free sources only: CoinGecko categories (sector rotation) and global (dominance,
market cap), DefiLlama stablecoins (net minting = dry powder; chain breakdown =
where stables move), our own market context (regime, breadth, funding, OI,
per-asset volume across crypto / US-stock / index / commodity perps, web3
trending, Fear & Greed), and a small daily history table this module writes so
7-day deltas exist after a week. Everything is a fact with evidence; the
recommendations are rules, each with the segment, SOP, KPI and what to avoid.
"""
from __future__ import annotations
import json
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from ..database import get_db, get_setting
from ..security import redact
from . import sources

H = {"User-Agent": "Mozilla/5.0 (compatible; moengage-engine intelligence)", "Accept": "application/json"}
NARRATIVES = {"Meme": "memecoins", "Artificial Intelligence (AI)": "AI", "Layer 2 (L2)": "L2", "Decentralized Finance (DeFi)": "DeFi", "Real World Assets (RWA)": "RWA", "Privacy Coins": "privacy", "Gaming (GameFi)": "gaming", "Solana Ecosystem": "Solana eco", "Base Ecosystem": "Base eco", "Ethereum Ecosystem": "Ethereum eco", "Layer 1 (L1)": "L1", "Perpetuals": "perp DEX tokens", "Stablecoins": "stablecoins", "Smart Contract Platform": "L1 platforms", "Exchange-based Tokens": "exchange tokens", "Liquid Staking": "liquid staking", "Zero Knowledge (ZK)": "ZK", "Bitcoin Ecosystem": "Bitcoin eco"}


def init_tables() -> None:
    conn = get_db()
    conn.execute("""CREATE TABLE IF NOT EXISTS asset_daily (day TEXT, symbol TEXT, asset_class TEXT, vol_24h_usd REAL, oi_usd REAL, price REAL, PRIMARY KEY (day, symbol, asset_class))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS macro_daily (day TEXT PRIMARY KEY, btc_dominance REAL, eth_dominance REAL, total_mcap_usd REAL, mcap_chg_24h REAL, fng INTEGER, regime TEXT, hl_oi_usd REAL, hl_vol_usd REAL, usdt_supply REAL, usdc_supply REAL,
                    crypto_vol_usd REAL, tokenised_vol_usd REAL, commodities_vol_usd REAL, indices_vol_usd REAL, breadth_up_pct REAL, funding_bias REAL, web3_trending INTEGER)""")
    conn.commit(); conn.close()


def record_daily(ctx: Dict[str, Any], stables: Optional[Dict[str, Any]] = None) -> None:
    """Persist today's per-asset and macro figures (overwrite within the day) so 7-day comparisons exist after a week."""
    try:
        init_tables(); conn = get_db(); day = date.today().isoformat()
        for cls, key in (("crypto", "crypto_markets"), ("equity", "equities"), ("index", "indices"), ("commodity", "commodities"), ("fx", "macro")):
            for r in (ctx.get(key) or []):
                sym = r.get("symbol") or r.get("name")
                if sym:
                    conn.execute("INSERT OR REPLACE INTO asset_daily (day, symbol, asset_class, vol_24h_usd, oi_usd, price) VALUES (?,?,?,?,?,?)", (day, str(sym), cls, r.get("vol_24h_usd"), r.get("oi_usd"), r.get("price")))
        cg = ctx.get("crypto_global") or {}; reg = (ctx.get("crypto") or {}).get("regime") or {}; fg = ctx.get("fear_greed") or {}
        vol = lambda key: sum((r.get("vol_24h_usd") or 0) for r in (ctx.get(key) or []))
        conn.execute("""INSERT OR REPLACE INTO macro_daily (day, btc_dominance, eth_dominance, total_mcap_usd, mcap_chg_24h, fng, regime, hl_oi_usd, hl_vol_usd, usdt_supply, usdc_supply, crypto_vol_usd, tokenised_vol_usd, commodities_vol_usd, indices_vol_usd, breadth_up_pct, funding_bias, web3_trending)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                     (day, cg.get("btc_dominance"), cg.get("eth_dominance"), cg.get("total_mcap_usd"), cg.get("mcap_chg_24h"), fg.get("value"), reg.get("label"),
                      sum((r.get("oi_usd") or 0) for r in (ctx.get("crypto_markets") or []) if r.get("on_hyperliquid")), vol("crypto_markets"), (stables or {}).get("usdt_now"), (stables or {}).get("usdc_now"),
                      vol("crypto_markets"), vol("equities"), vol("commodities"), vol("indices"), reg.get("breadth_up_pct"), reg.get("funding_bias"), len(((ctx.get("web3") or {}).get("trending") or []))))
        conn.execute("DELETE FROM asset_daily WHERE day < date('now', '-60 days')"); conn.execute("DELETE FROM macro_daily WHERE day < date('now', '-180 days')")
        conn.commit(); conn.close()
    except Exception:
        pass


def _macro_ago(days: int) -> Dict[str, Any]:
    try:
        conn = get_db(); r = conn.execute("SELECT * FROM macro_daily WHERE day <= date('now', ?) ORDER BY day DESC LIMIT 1", (f"-{days} days",)).fetchone(); conn.close()
        return dict(r) if r else {}
    except Exception:
        return {}


def _asset_avg(days: int = 7) -> Dict[str, float]:
    try:
        conn = get_db()
        rows = conn.execute("SELECT symbol, AVG(vol_24h_usd) v, COUNT(DISTINCT day) n FROM asset_daily WHERE day < date('now') AND day >= date('now', ?) GROUP BY symbol", (f"-{days} days",)).fetchall(); conn.close()
        return {r["symbol"]: float(r["v"]) for r in rows if r["n"] >= 2 and r["v"]}
    except Exception:
        return {}


# ── external flow sources ─────────────────────────────────────────────────────
def categories(min_mcap_usd: float = 3e9) -> Dict[str, Any]:
    def fetch():
        rows = sources._json("https://api.coingecko.com/api/v3/coins/categories", {"order": "market_cap_desc"}, 25.0, headers=sources._cg_headers()) or []
        out = []
        for r in rows:
            if (r.get("market_cap") or 0) < min_mcap_usd:
                continue
            out.append({"name": r.get("name"), "tag": NARRATIVES.get(r.get("name"), r.get("name")), "mcap_usd": r.get("market_cap"), "chg_24h_pct": r.get("market_cap_change_24h"), "vol_24h_usd": r.get("volume_24h"), "top": [c.split("/")[-1][:16] for c in (r.get("top_3_coins") or [])][:3]})
        out.sort(key=lambda r: -(r["chg_24h_pct"] or 0))
        return {"in": out[:8], "out": sorted(out, key=lambda r: (r["chg_24h_pct"] or 0))[:8], "count": len(out), "source": "coingecko categories (24h market-cap change)"}
    return sources.cached("cg_categories", fetch, 1800) or {"in": [], "out": []}


def stablecoins() -> Dict[str, Any]:
    def fetch():
        d = sources._json("https://stablecoins.llama.fi/stablecoins", {"includePrices": "true"}, 30.0, headers=H) or {}
        pa = d.get("peggedAssets") or []
        def circ(p, k="circulating"):
            return float(((p.get(k) or {}).get("peggedUSD")) or 0)
        top = sorted(pa, key=lambda p: -circ(p))[:6]
        rows = [{"symbol": p.get("symbol"), "now_usd": circ(p), "chg_1d_usd": circ(p) - circ(p, "circulatingPrevDay"), "chg_7d_usd": circ(p) - circ(p, "circulatingPrevWeek"), "chg_30d_usd": circ(p) - circ(p, "circulatingPrevMonth")} for p in top]
        chains: Dict[str, Dict[str, float]] = {}
        for p in top[:3]:
            for ch, v in (p.get("chainCirculating") or {}).items():
                cur = float(((v.get("current") or {}).get("peggedUSD")) or 0); prev = float(((v.get("circulatingPrevWeek") or {}).get("peggedUSD")) or 0)
                c = chains.setdefault(ch, {"now_usd": 0.0, "chg_7d_usd": 0.0}); c["now_usd"] += cur; c["chg_7d_usd"] += cur - prev
        chain_rows = sorted([{"chain": k, **v} for k, v in chains.items() if v["now_usd"] > 1e9], key=lambda r: -r["chg_7d_usd"])
        total_now = sum(r["now_usd"] for r in rows); total_7d = sum(r["chg_7d_usd"] for r in rows)
        return {"stables": rows, "total_now_usd": total_now, "net_7d_usd": total_7d, "net_1d_usd": sum(r["chg_1d_usd"] for r in rows), "chains_in": chain_rows[:5], "chains_out": chain_rows[-4:][::-1], "usdt_now": next((r["now_usd"] for r in rows if r["symbol"] == "USDT"), None), "usdc_now": next((r["now_usd"] for r in rows if r["symbol"] == "USDC"), None), "source": "defillama stablecoins (free)"}
    return sources.cached("llama_stables", fetch, 3600) or {"stables": [], "chains_in": [], "chains_out": []}


# ── assemble ──────────────────────────────────────────────────────────────────
def flow(ctx: Dict[str, Any]) -> Dict[str, Any]:
    cg = ctx.get("crypto_global") or {}; reg = (ctx.get("crypto") or {}).get("regime") or {}; fg = ctx.get("fear_greed") or {}
    cats = categories(); st = stablecoins()
    record_daily(ctx, st)
    y = _macro_ago(1); w = _macro_ago(7)
    def delta(key, now):
        try:
            return round(float(now) - float(y[key]), 2) if y.get(key) is not None and now is not None else None
        except Exception:
            return None
    vol = lambda key: sum((r.get("vol_24h_usd") or 0) for r in (ctx.get(key) or []))
    v_crypto, v_eq, v_idx, v_com = vol("crypto_markets"), vol("equities"), vol("indices"), vol("commodities")
    v_tot = (v_crypto + v_eq + v_idx + v_com) or 1.0
    share_now = {"crypto": round(v_crypto / v_tot * 100, 1), "us_stocks": round(v_eq / v_tot * 100, 1), "indices": round(v_idx / v_tot * 100, 1), "commodities": round(v_com / v_tot * 100, 1)}
    share_7d = None
    if w.get("crypto_vol_usd"):
        wt = (w.get("crypto_vol_usd") or 0) + (w.get("tokenised_vol_usd") or 0) + (w.get("indices_vol_usd") or 0) + (w.get("commodities_vol_usd") or 0) or 1.0
        share_7d = {"crypto": round((w.get("crypto_vol_usd") or 0) / wt * 100, 1), "us_stocks": round((w.get("tokenised_vol_usd") or 0) / wt * 100, 1), "indices": round((w.get("indices_vol_usd") or 0) / wt * 100, 1), "commodities": round((w.get("commodities_vol_usd") or 0) / wt * 100, 1)}
    avg = _asset_avg(7)
    attention = []
    for r in (ctx.get("crypto_markets") or []) + (ctx.get("equities") or []) + (ctx.get("commodities") or []) + (ctx.get("indices") or []):
        sym = r.get("symbol") or r.get("name"); v = r.get("vol_24h_usd") or 0; a = avg.get(str(sym))
        if a and v >= 5e6:
            attention.append({"symbol": sym, "asset_class": r.get("asset_class", "crypto"), "vol_24h_usd": v, "vs_7d_avg_x": round(v / a, 2), "chg_24h": r.get("chg_24h")})
    attention.sort(key=lambda r: -r["vs_7d_avg_x"])
    hl_oi = sum((r.get("oi_usd") or 0) for r in (ctx.get("crypto_markets") or []) if r.get("on_hyperliquid"))
    oi_chg = round((hl_oi / y["hl_oi_usd"] - 1) * 100, 1) if y.get("hl_oi_usd") else None
    md = ctx.get("crypto_movers_detail") or {}
    w3 = ctx.get("web3") or {}
    # risk appetite composite 0..100
    score = 50.0; reasons = []
    if fg.get("value") is not None:
        score += (float(fg["value"]) - 50) * 0.4; reasons.append(f"Fear & Greed {fg['value']}")
    if reg.get("breadth_up_pct") is not None:
        score += (float(reg["breadth_up_pct"]) - 50) * 0.3; reasons.append(f"breadth {reg['breadth_up_pct']}% up on week")
    if cg.get("mcap_chg_24h") is not None:
        score += max(-10, min(10, float(cg["mcap_chg_24h"]) * 2)); reasons.append(f"market cap {cg['mcap_chg_24h']:+.1f}% 24h")
    if reg.get("label") in ("capitulation", "high_volatility_down"):
        score -= 20; reasons.append(f"regime {reg['label']}")
    if len(md.get("crowded_long") or []) > len(md.get("crowded_short") or []):
        score += 5; reasons.append("funding skewed long")
    elif md.get("crowded_short"):
        score -= 5; reasons.append("funding skewed short")
    if st.get("net_7d_usd") and st["net_7d_usd"] > 0:
        score += 5; reasons.append(f"stablecoins +${st['net_7d_usd'] / 1e9:.1f}B 7d")
    score = int(max(0, min(100, round(score))))
    return {"generated_at": ctx.get("generated_at"), "regime": reg.get("label"), "risk_appetite": {"score": score, "label": "risk-on" if score >= 62 else "risk-off" if score <= 38 else "neutral", "reasons": reasons[:6]},
            "dominance": {"btc": cg.get("btc_dominance"), "eth": cg.get("eth_dominance"), "btc_chg_1d_pp": delta("btc_dominance", cg.get("btc_dominance")), "mcap_chg_24h_pct": cg.get("mcap_chg_24h"), "total_mcap_usd": cg.get("total_mcap_usd")},
            "stablecoins": {k: st.get(k) for k in ("total_now_usd", "net_1d_usd", "net_7d_usd", "chains_in", "chains_out", "source")} | {"top": (st.get("stables") or [])[:4]},
            "rotation": cats, "venue_share": {"now": share_now, "week_ago": share_7d, "note": "share of 24h volume across our liquidity venue's crypto perps, US-stock, index and commodity perps"},
            "attention": attention[:10], "leverage": {"hl_oi_usd": hl_oi, "oi_chg_1d_pct": oi_chg, "crowded_long": [m["symbol"] for m in (md.get("crowded_long") or [])][:5], "crowded_short": [m["symbol"] for m in (md.get("crowded_short") or [])][:5], "funding_bias": reg.get("funding_bias")},
            "web3": {"trending_count": len(w3.get("trending") or []), "top_chain": max(((w3.get("by_chain") or {}).items()), key=lambda kv: len(kv[1]), default=(None, []))[0], "top": [(r.get("chain"), r.get("symbol")) for r in (w3.get("trending") or [])[:5]]},
            "history_days": _history_days(), "sources": ["coingecko global + categories", "defillama stablecoins", "liquidity venue contexts", "own daily history"]}


def _history_days() -> int:
    try:
        conn = get_db(); n = conn.execute("SELECT COUNT(*) FROM macro_daily").fetchone()[0]; conn.close(); return int(n)
    except Exception:
        return 0


def behaviour_reads(f: Dict[str, Any]) -> List[Dict[str, Any]]:
    reads = []
    ra = f.get("risk_appetite") or {}; dom = f.get("dominance") or {}; st = f.get("stablecoins") or {}; rot = f.get("rotation") or {}; lev = f.get("leverage") or {}; vs = f.get("venue_share") or {}; w3 = f.get("web3") or {}; att = f.get("attention") or []
    if ra.get("label") == "risk-on":
        reads.append({"read": "Risk appetite is up: traders are adding exposure and rotating down the risk curve.", "evidence": "; ".join(ra.get("reasons", [])[:4]), "traders_do": "more alt trading, more leverage, more sessions after big moves", "confidence": "medium"})
    elif ra.get("label") == "risk-off":
        reads.append({"read": "Risk appetite is down: traders de-risk into majors and stablecoins and trade less.", "evidence": "; ".join(ra.get("reasons", [])[:4]), "traders_do": "fewer trades, withdrawals up, liquidations, support load up", "confidence": "medium"})
    if dom.get("btc_chg_1d_pp") is not None:
        if dom["btc_chg_1d_pp"] >= 0.3:
            reads.append({"read": "Capital is consolidating into BTC (dominance rising).", "evidence": f"BTC dominance {dom['btc']}% ({dom['btc_chg_1d_pp']:+.2f} pp/day)", "traders_do": "alt positions cut; BTC spot/perp volume share up", "confidence": "medium"})
        elif dom["btc_chg_1d_pp"] <= -0.3:
            reads.append({"read": "Capital is rotating out of BTC into alts (dominance falling).", "evidence": f"BTC dominance {dom['btc']}% ({dom['btc_chg_1d_pp']:+.2f} pp/day)", "traders_do": "alt and sector bets, more small-cap volume", "confidence": "medium"})
    ins = [c for c in (rot.get("in") or []) if (c.get("chg_24h_pct") or 0) >= 3]
    if ins:
        reads.append({"read": f"Sector rotation into {', '.join(c['tag'] for c in ins[:3])}.", "evidence": "; ".join(f"{c['tag']} {c['chg_24h_pct']:+.1f}% cap 24h" for c in ins[:3]), "traders_do": "chasing the narrative: watchlist adds and first buys in those names", "confidence": "medium"})
    outs = [c for c in (rot.get("out") or []) if (c.get("chg_24h_pct") or 0) <= -3]
    if outs:
        reads.append({"read": f"Money leaving {', '.join(c['tag'] for c in outs[:3])}.", "evidence": "; ".join(f"{c['tag']} {c['chg_24h_pct']:+.1f}%" for c in outs[:3]), "traders_do": "realised losses in those holders → loss-dormancy risk", "confidence": "medium"})
    if st.get("net_7d_usd") is not None:
        if st["net_7d_usd"] > 5e8:
            reads.append({"read": "Dry powder is growing: stablecoin supply expanded this week.", "evidence": f"net +${st['net_7d_usd'] / 1e9:.2f}B in 7d; inflows led by {', '.join(c['chain'] for c in (st.get('chains_in') or [])[:2])}", "traders_do": "deposits and buy-side readiness up; SIP and spot demand follows with a lag", "confidence": "medium"})
        elif st["net_7d_usd"] < -5e8:
            reads.append({"read": "Liquidity is leaving crypto: stablecoin supply shrank this week.", "evidence": f"net ${st['net_7d_usd'] / 1e9:.2f}B in 7d", "traders_do": "withdrawals to fiat; lower deposit conversion", "confidence": "medium"})
    if lev.get("oi_chg_1d_pct") is not None:
        if lev["oi_chg_1d_pct"] >= 5:
            reads.append({"read": "Leverage is building.", "evidence": f"perp OI {lev['oi_chg_1d_pct']:+.1f}% in a day; crowded longs: {', '.join(lev.get('crowded_long') or []) or 'none'}", "traders_do": "more perps sessions, higher liquidation risk on the next move", "confidence": "high"})
        elif lev["oi_chg_1d_pct"] <= -5:
            reads.append({"read": "Leverage was flushed.", "evidence": f"perp OI {lev['oi_chg_1d_pct']:+.1f}% in a day", "traders_do": "liquidated users going quiet; risk of loss-dormancy", "confidence": "high"})
    now = (vs.get("now") or {}); wk = vs.get("week_ago")
    if wk and now:
        for k, label in (("us_stocks", "US-stock perps"), ("commodities", "commodity perps"), ("indices", "index perps")):
            if now.get(k) is not None and wk.get(k) is not None and now[k] - wk[k] >= 2:
                reads.append({"read": f"Attention is shifting to {label} on our venue.", "evidence": f"{label} {wk[k]}% → {now[k]}% of venue volume vs a week ago", "traders_do": "crypto traders trying tokenised markets, especially in US hours", "confidence": "medium"})
    elif now.get("us_stocks", 0) + now.get("commodities", 0) + now.get("indices", 0) >= 25:
        reads.append({"read": "Tokenised markets already carry a large share of venue volume.", "evidence": f"US stocks {now.get('us_stocks')}% · commodities {now.get('commodities')}% · indices {now.get('indices')}% of venue volume today", "traders_do": "24/7 TradFi trading is mainstream for this base", "confidence": "medium"})
    if att:
        reads.append({"read": f"Attention spikes: {', '.join(a['symbol'] for a in att[:4])}.", "evidence": "; ".join(f"{a['symbol']} {a['vs_7d_avg_x']}× 7d volume" for a in att[:3]), "traders_do": "watchlist adds and first trades concentrate in these names today", "confidence": "high"})
    if (w3.get("trending_count") or 0) >= 10:
        reads.append({"read": f"On-chain speculation is active ({w3.get('trending_count')} quality-gated trending tokens, mostly {w3.get('top_chain')}).", "evidence": ", ".join(f"{c}:{s}" for c, s in (w3.get("top") or [])[:4]), "traders_do": "web3 users rotate fast; scam risk highest here", "confidence": "medium"})
    if not reads:
        reads.append({"read": "Quiet tape: no strong flow signal today.", "evidence": "risk appetite neutral, no rotation above 3%, OI stable", "traders_do": "habit and education content outperform promos", "confidence": "low"})
    return reads[:8]


def recommendations(f: Dict[str, Any], reads: List[Dict[str, Any]], ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    recs: List[Dict[str, Any]] = []
    ra = (f.get("risk_appetite") or {}).get("label"); regime = f.get("regime"); stress = regime in ("capitulation", "high_volatility_down")
    lev = f.get("leverage") or {}; rot = f.get("rotation") or {}; att = f.get("attention") or []; st = f.get("stablecoins") or {}; vs = (f.get("venue_share") or {}).get("now") or {}; w3 = f.get("web3") or {}; dom = f.get("dominance") or {}
    def add(priority, title, why, who, what, sop, kpi, urgency="today", avoid=None, product=None):
        recs.append({"priority": priority, "title": title, "why": why, "who": who, "what": what, "sop": sop, "kpi": kpi, "urgency": urgency, "avoid": avoid, "product": product})
    if stress:
        add(100, "Switch to service mode; freeze promotions", f"regime {regime}", "everyone; liquidated-14d and loss-dormant first", "Tier-0 global announcement with product lenses; pause acquisition/upsell; calm risk-tools message", "sop_global_announcement_lenses", "support_contacts_avoided; notification_disable_rate ≤ baseline", "now", "any promo, competition, leverage or web3 trend content", "all")
    if (lev.get("oi_chg_1d_pct") or 0) >= 5 or lev.get("crowded_long"):
        add(90, "Risk-education to leverage climbers and crowded longs now", (f"OI {lev.get('oi_chg_1d_pct'):+.1f}% 1d; " if lev.get('oi_chg_1d_pct') is not None else "OI change needs a day of history; ") + f"crowded longs {', '.join(lev.get('crowded_long') or []) or 'none'}", "LEV_CLIMBER + holders of crowded perps", "funding-cost nudge + margin-buffer review; no direction", "sop_funding_crowding_nudge", "risk_tool_open_rate; liquidation_rate_30d", "today", "size-up or leverage lures", "perps_crypto")
    if (lev.get("oi_chg_1d_pct") or 0) <= -5:
        add(85, "Run liquidation-recovery for yesterday's flush", f"OI {lev.get('oi_chg_1d_pct')}% in a day", "LIQUIDATED_14D", "T+0 silence, T+24h explainer email, T+14 spot-first path; suppress promos 14d", "sop_liquidation_recovery", "return_to_trade_rate_30d", "today", "market pushes to liquidated users", "perps_crypto")
    ins = [c for c in (rot.get("in") or []) if (c.get("chg_24h_pct") or 0) >= 3][:2]
    if ins and not stress:
        add(80, f"Spotlight the rotation: {', '.join(c['tag'] for c in ins)}", "; ".join(f"{c['tag']} {c['chg_24h_pct']:+.1f}% cap" for c in ins), f"watchers/holders of {', '.join(c['top'][0] for c in ins if c.get('top'))} and sector watchlists", "asset spotlight with the pairs we list; watchlist/alert CTA; TTL 4h", "sop_asset_spotlight", "watchlist_add_rate; first trades on the pair vs holdout", "today", "'buy the narrative' framing", "spot")
    outs = [c for c in (rot.get("out") or []) if (c.get("chg_24h_pct") or 0) <= -5][:2]
    if outs:
        add(70, f"Protect holders of {', '.join(c['tag'] for c in outs)} from loss-dormancy", "; ".join(f"{c['tag']} {c['chg_24h_pct']:+.1f}%" for c in outs), f"holders of {', '.join(c['top'][0] for c in outs if c.get('top'))} with realised losses", "portfolio review with own numbers; SIP continuity for plan holders; no promos", "sop_slipping_checkin", "trade_frequency_recovery_14d", "this week", "offers to lossy users", "spot")
    if att and not stress:
        add(75, f"Attention campaigns on {', '.join(a['symbol'] for a in att[:3])}", "; ".join(f"{a['symbol']} {a['vs_7d_avg_x']}× 7d volume" for a in att[:3]), "watchers/holders of those assets; active traders 14d", "fact + tool push (alert / watchlist), one per user per day", "sop_asset_spotlight", "alert_adoption_rate", "today", "urgency on price", "spot")
    if (st.get("net_7d_usd") or 0) > 5e8 and not stress:
        add(65, "Deposit-readiness: convert dry powder", f"stablecoins +${st['net_7d_usd'] / 1e9:.2f}B in 7d; salary week if 1–7", "KYC_APPROVED_NODEP and FTD_NOTRADE", "deposit path + SIP setup; failure recovery within minutes", "sop_verified_to_funded", "first_deposit_rate_7d", "this week", "deposit bonuses", "sip")
    if (st.get("net_7d_usd") or 0) < -5e8:
        add(60, "Retention over acquisition this week", f"stablecoins ${st['net_7d_usd'] / 1e9:.2f}B in 7d (liquidity leaving)", "Habitual and Core cohorts", "service-grade content, statements, fee tiers; no acquisition pushes", "sop_hvt_retention", "weekly_active_weeks_4w", "this week", "acquisition spend and promos", "spot")
    if (vs.get("us_stocks") or 0) + (vs.get("commodities") or 0) + (vs.get("indices") or 0) >= 20 and not stress:
        add(62, "Cross-sell tokenised markets to US-hours perp traders", f"tokenised markets {vs.get('us_stocks')}+{vs.get('commodities')}+{vs.get('indices')}% of venue volume", "PERP_HABITUAL_US_HOURS", "24/7 education + after-hours movers; never 'trade earnings'", "sop_cross_sell_crypto_to_tokenised", "tokenised_first_fill_rate_14d", "this week", "venue names, forecasts", "perps_us_stocks")
    if (dom.get("btc_chg_1d_pp") or 0) >= 0.3 and not stress:
        add(55, "Lead with BTC and SIP continuity while alts bleed", f"BTC dominance {dom.get('btc')}% ({dom.get('btc_chg_1d_pp'):+.2f} pp)", "SIP_ACTIVE and spot-only", "DCA education; BTC watchlist/alert; no alt picks", "sop_sip_nurture", "sip_continuation_rate_90d", "this week", "alt spotlights", "sip")
    if (w3.get("trending_count") or 0) >= 10 and not stress:
        add(50, "Web3 safety-first trend watch", f"{w3.get('trending_count')} trending tokens, mostly {w3.get('top_chain')}", "WEB3_ACTIVE (opted-in), WEB3_NEW gets onboarding first", "trend facts with 'unverified' wording and a safety line; watchlist CTA", "sop_web3_trending_watch", "watchlist_add_rate; complaint_rate", "today", "token picks, paid boosts, '100x'", "web3")
    if ra == "risk-on" and not stress:
        add(58, "Reactivate market-dormant users while the tape is friendly", "risk appetite " + ra, "MARKET_DORMANT (no loss event)", "what changed since you left (facts), habit tool re-entry; 20% holdout", "sop_market_dormant_return", "reactivation_rate_14d", "this week", "FOMO framing", "spot")
    add(40, "Keep the peace index in band", "every recommendation above adds touches", "Core cohorts first", "check peace_index before queuing; cap 1 market push per user per day", None, "touches/user/week vs cap", "always", "stacking three market pushes on the same user", "all")
    recs.sort(key=lambda r: -r["priority"])
    return recs[:10]


def lab(ctx: Dict[str, Any]) -> Dict[str, Any]:
    try:
        f = flow(ctx)
    except Exception as e:
        f = {"error": redact(str(e))[:200], "risk_appetite": {}, "rotation": {}, "stablecoins": {}, "leverage": {}, "venue_share": {}, "attention": [], "web3": {}, "dominance": {}}
    reads = behaviour_reads(f)
    return {"flow": f, "reads": reads, "recommendations": recommendations(f, reads, ctx)}
