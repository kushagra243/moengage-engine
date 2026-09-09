"""
Category benchmarks — who is best in each product category, in India and
internationally, and how far CoinDCX is from matching their numbers.

Categories: spot · perps · options · commodities_tokenised.
Free sources only: CoinMarketCap exchange listings (24h spot/derivatives volume,
open interest, markets, fees, traffic) for every venue; direct public tickers
for pair-level numbers (Binance futures, OKX swaps, Bitget futures, Bybit
linear/options, Deribit options, Binance options, Delta Exchange India).
Our own figures: CoinDCX spot from CMC (team rule); perps and tokenised markets
from the liquidity venue as a *reference* (labelled); options unknown until the
team shares a source. Every output row says where the number came from.
"""
from __future__ import annotations
import json
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..database import get_db, get_setting
from ..security import redact
from . import sources
from .competitors import fetch_cmc_listing, fetch_delta_india, fetch_bybit, canon, STABLES, _f

# scope: in = operates for Indian users (INR rails or India entity); intl = global reference
VENUES: Dict[str, Dict[str, Any]] = {
    "binance":  {"name": "Binance", "scope": "intl", "cmc": "binance", "cats": ["spot", "perps", "options"]},
    "okx":      {"name": "OKX", "scope": "intl", "cmc": "okx", "cats": ["spot", "perps", "options", "commodities_tokenised"]},
    "bybit":    {"name": "Bybit", "scope": "intl", "cmc": "bybit", "cats": ["spot", "perps", "options", "commodities_tokenised"]},
    "bitget":   {"name": "Bitget", "scope": "intl", "cmc": "bitget", "cats": ["spot", "perps", "commodities_tokenised"]},
    "coinbase": {"name": "Coinbase", "scope": "intl", "cmc": "coinbase-exchange", "cats": ["spot"]},
    "kraken":   {"name": "Kraken", "scope": "intl", "cmc": "kraken", "cats": ["spot", "perps"]},
    "kucoin":   {"name": "KuCoin", "scope": "intl", "cmc": "kucoin", "cats": ["spot", "perps"]},
    "gate":     {"name": "Gate", "scope": "intl", "cmc": "gate", "cats": ["spot", "perps", "commodities_tokenised"]},
    "mexc":     {"name": "MEXC", "scope": "intl", "cmc": "mexc", "cats": ["spot", "perps"]},
    "htx":      {"name": "HTX", "scope": "intl", "cmc": "htx", "cats": ["spot", "perps"]},
    "hyperliquid": {"name": "Hyperliquid (our liquidity venue)", "scope": "intl", "cmc": "hyperliquid", "cats": ["perps", "commodities_tokenised"], "reference": True},
    "deribit":  {"name": "Deribit", "scope": "intl", "cmc": "deribit", "cats": ["options"]},
    "delta":    {"name": "Delta Exchange India", "scope": "in", "cmc": None, "cats": ["perps", "options"]},
    "coindcx":  {"name": "CoinDCX (us)", "scope": "in", "cmc": "coindcx", "cats": ["spot", "perps", "options", "commodities_tokenised"], "us": True},
    "wazirx":   {"name": "WazirX", "scope": "in", "cmc": "wazirx", "cats": ["spot"]},
    "zebpay":   {"name": "ZebPay", "scope": "in", "cmc": "zebpay", "cats": ["spot"]},
    "giottus":  {"name": "Giottus", "scope": "in", "cmc": "giottus", "cats": ["spot"]},
    "koinbx":   {"name": "KoinBX", "scope": "in", "cmc": "koinbx", "cats": ["spot"]},
    "unocoin":  {"name": "Unocoin", "scope": "in", "cmc": "unocoin", "cats": ["spot"]},
}
# Curated: commodities, indices and large-cap US names as they appear on venues' TradFi perps. Kept explicit because short
# tokenised-stock tickers collide with crypto tokens of the same symbol on some venues (a fuzzy match would inflate the category).
RWA_WORDS = {"XAU", "XAUT", "PAXG", "GOLD", "XAG", "SILVER", "PLATINUM", "PALLADIUM", "BRENTOIL", "USOIL", "UKOIL", "WTI", "NATGAS", "COPPER", "SP500", "SPX", "NASDAQ100", "NDX", "DOW", "US30", "NAS100", "US500", "DAX", "NIFTY",
             "TSLA", "NVDA", "AAPL", "MSFT", "AMZN", "GOOGL", "GOOG", "META", "COIN", "MSTR", "HOOD", "CRCL", "NFLX", "AMD", "INTC", "PLTR", "AVGO", "BRK", "JPM", "V", "MA", "SPY", "QQQ", "GLD", "SLV", "TLT", "IWM", "DIA", "USO", "UNG"}


def init_tables() -> None:
    conn = get_db()
    conn.execute("""CREATE TABLE IF NOT EXISTS benchmark_snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, day TEXT, category TEXT, venue TEXT, vol_24h_usd REAL, oi_usd REAL, markets INTEGER)""")
    conn.commit(); conn.close()


def _rwa_symbols() -> set:
    """Symbols counted as commodities / tokenised on other venues: the curated list only (see RWA_WORDS)."""
    return set(RWA_WORDS)


# ── pair-level fetchers (direct, keyless) ─────────────────────────────────────
def binance_futures() -> List[Dict[str, Any]]:
    rows = sources._json("https://fapi.binance.com/fapi/v1/ticker/24hr", None, 20.0) or []
    return [{"symbol": canon(r["symbol"][:-4]), "vol_24h_usd": _f(r.get("quoteVolume")), "chg_24h": _f(r.get("priceChangePercent"))} for r in rows if str(r.get("symbol", "")).endswith("USDT")]


def okx_swaps() -> List[Dict[str, Any]]:
    d = sources._json("https://www.okx.com/api/v5/market/tickers", {"instType": "SWAP"}, 20.0) or {}
    out = []
    for r in d.get("data") or []:
        inst = str(r.get("instId") or "")
        if not inst.endswith("-USDT-SWAP"):
            continue
        last = _f(r.get("last")); out.append({"symbol": canon(inst.split("-")[0]), "vol_24h_usd": _f(r.get("volCcy24h")) * last, "chg_24h": round((last / _f(r.get("open24h"), last) - 1) * 100, 2) if _f(r.get("open24h")) else None})
    return out


def bitget_futures() -> List[Dict[str, Any]]:
    d = sources._json("https://api.bitget.com/api/v2/mix/market/tickers", {"productType": "USDT-FUTURES"}, 20.0) or {}
    return [{"symbol": canon(str(r.get("symbol", ""))[:-4]), "vol_24h_usd": _f(r.get("quoteVolume")), "chg_24h": _f(r.get("change24h")) * 100, "oi_usd": _f(r.get("holdingAmount")) * _f(r.get("lastPr"))} for r in d.get("data") or [] if str(r.get("symbol", "")).endswith("USDT")]


def deribit_options(currency: str = "BTC") -> Dict[str, float]:
    d = sources._json("https://www.deribit.com/api/v2/public/get_book_summary_by_currency", {"currency": currency, "kind": "option"}, 25.0) or {}
    vol = oi = 0.0
    for r in d.get("result") or []:
        vol += _f(r.get("volume_usd")); oi += _f(r.get("open_interest")) * _f(r.get("estimated_delivery_price"))
    return {"vol_24h_usd": vol, "oi_usd": oi, "instruments": len(d.get("result") or [])}


def binance_options() -> Dict[str, float]:
    rows = sources._json("https://eapi.binance.com/eapi/v1/ticker", None, 25.0) or []
    return {"vol_24h_usd": sum(_f(r.get("amount")) for r in rows if isinstance(r, dict)), "instruments": len(rows)}


def bybit_options(base: str = "BTC") -> Dict[str, float]:
    d = sources._json("https://api.bybit.com/v5/market/tickers", {"category": "option", "baseCoin": base}, 20.0) or {}
    rows = ((d.get("result") or {}).get("list") or [])
    return {"vol_24h_usd": sum(_f(r.get("turnover24h")) for r in rows), "oi_usd": sum(_f(r.get("openInterest")) * _f(r.get("indexPrice")) for r in rows), "instruments": len(rows)}


# ── assemble ──────────────────────────────────────────────────────────────────
def compute(force: bool = False) -> Dict[str, Any]:
    def fetch():
        init_tables()
        errors: List[str] = []
        try:
            listing = fetch_cmc_listing()
        except Exception as e:
            listing = {}; errors.append("cmc listing: " + redact(str(e))[:80])
        cats: Dict[str, Dict[str, Dict[str, Any]]] = {"spot": {}, "perps": {}, "options": {}, "commodities_tokenised": {}}
        # 1. exchange-level from CMC
        for vid, v in VENUES.items():
            c = listing.get(v.get("cmc") or "") or {}
            if "spot" in v["cats"] and c:
                cats["spot"][vid] = {"vol_24h_usd": _f(c.get("spotVol24h")), "markets": c.get("numMarkets"), "maker_fee_pct": c.get("makerFee"), "taker_fee_pct": c.get("takerFee"), "visits": c.get("visits"), "source": "coinmarketcap"}
            if "perps" in v["cats"] and c and _f(c.get("derivativesVol24h")) > 0:
                cats["perps"][vid] = {"vol_24h_usd": _f(c.get("derivativesVol24h")), "oi_usd": _f(c.get("derivativesOpenInterests")), "markets": c.get("derivativesMarketPairs"), "maker_fee_pct": c.get("makerFee"), "taker_fee_pct": c.get("takerFee"), "source": "coinmarketcap"}
        # 2. Delta India direct (perps + options)
        try:
            rows = fetch_delta_india()
            perps = [r for r in rows if r["product"] == "perp"]; opts = [r for r in rows if r["product"] == "option"]
            cats["perps"]["delta"] = {"vol_24h_usd": sum(r["vol_24h_usd"] for r in perps), "oi_usd": sum(r["oi_usd"] or 0 for r in perps), "markets": len(perps), "source": "api.india.delta.exchange", "pairs": sorted(perps, key=lambda r: -r["vol_24h_usd"])[:15]}
            cats["options"]["delta"] = {"vol_24h_usd": sum(r["vol_24h_usd"] for r in opts), "oi_usd": sum(r["oi_usd"] or 0 for r in opts), "markets": len(opts), "source": "api.india.delta.exchange"}
        except Exception as e:
            errors.append("delta: " + redact(str(e))[:80])
        # 3. options internationals
        for vid, fn in (("deribit", lambda: {k: deribit_options("BTC")[k] + deribit_options("ETH")[k] for k in ("vol_24h_usd", "oi_usd", "instruments")}), ("binance", binance_options), ("bybit", lambda: {k: bybit_options("BTC").get(k, 0) + bybit_options("ETH").get(k, 0) for k in ("vol_24h_usd", "oi_usd", "instruments")})):
            try:
                o = fn(); cats["options"][vid] = {"vol_24h_usd": o.get("vol_24h_usd", 0), "oi_usd": o.get("oi_usd"), "markets": o.get("instruments"), "source": {"deribit": "deribit public api", "binance": "eapi.binance.com", "bybit": "api.bybit.com"}[vid]}
            except Exception as e:
                errors.append(f"{vid} options: {redact(str(e))[:80]}")
        # 4. pair-level perps + tokenised/commodities from direct tickers
        rwa = _rwa_symbols()
        pair_sources = {"binance": binance_futures, "okx": okx_swaps, "bitget": bitget_futures, "bybit": fetch_bybit}
        pairs: Dict[str, List[Dict[str, Any]]] = {}
        for vid, fn in pair_sources.items():
            try:
                rows = [dict(r, symbol=canon(r["symbol"])) for r in fn() if r.get("symbol") and canon(r["symbol"]) not in STABLES]
                pairs[vid] = sorted(rows, key=lambda r: -(r.get("vol_24h_usd") or 0))
                tok = [r for r in rows if r["symbol"] in rwa]
                if tok:
                    cats["commodities_tokenised"][vid] = {"vol_24h_usd": sum(r.get("vol_24h_usd") or 0 for r in tok), "oi_usd": sum(r.get("oi_usd") or 0 for r in tok) or None, "markets": len(tok), "source": f"{vid} public tickers (RWA symbols)", "pairs": sorted(tok, key=lambda r: -(r.get("vol_24h_usd") or 0))[:12]}
                if vid in cats["perps"]:
                    cats["perps"][vid]["pairs"] = pairs[vid][:15]
            except Exception as e:
                errors.append(f"{vid} pairs: {redact(str(e))[:80]}")
        # 5. ours + our liquidity venue
        try:
            from .exchanges import hyperliquid_perps
            hl = hyperliquid_perps()
            cr = [{"symbol": canon(k), "vol_24h_usd": _f(v.get("vol_24h_usd")), "oi_usd": _f(v.get("oi_usd"))} for k, v in (hl.get("crypto") or {}).items()]
            rw = [{"symbol": canon(v.get("name") or k.split(":")[-1]), "vol_24h_usd": _f(v.get("vol_24h_usd")), "oi_usd": _f(v.get("oi_usd"))} for k, v in (hl.get("rwa") or {}).items()]
            cats["perps"]["coindcx"] = {"vol_24h_usd": sum(r["vol_24h_usd"] for r in cr), "oi_usd": sum(r["oi_usd"] for r in cr), "markets": len(cr), "source": "liquidity venue (reference — not our own volume)", "reference": True, "pairs": sorted(cr, key=lambda r: -r["vol_24h_usd"])[:15], "all_pairs": {r["symbol"]: r["vol_24h_usd"] for r in cr}}
            cats["commodities_tokenised"]["coindcx"] = {"vol_24h_usd": sum(r["vol_24h_usd"] for r in rw), "oi_usd": sum(r["oi_usd"] for r in rw), "markets": len(rw), "source": "liquidity venue builder dexes (reference)", "reference": True, "pairs": sorted(rw, key=lambda r: -r["vol_24h_usd"])[:12], "all_pairs": {r["symbol"]: r["vol_24h_usd"] for r in rw}}
            cats["commodities_tokenised"]["hyperliquid"] = dict(cats["commodities_tokenised"]["coindcx"], source="hyperliquid builder dexes")
        except Exception as e:
            errors.append("liquidity venue: " + redact(str(e))[:80])
        cats["options"].setdefault("coindcx", {"vol_24h_usd": None, "oi_usd": None, "markets": None, "source": "no free source for our options volume yet — team to provide", "unknown": True})
        # persist a daily row per venue/category for trends
        try:
            conn = get_db(); day = datetime.utcnow().strftime("%Y-%m-%d")
            conn.execute("DELETE FROM benchmark_snapshots WHERE day=?", (day,))
            for cat, vs in cats.items():
                for vid, m in vs.items():
                    conn.execute("INSERT INTO benchmark_snapshots (day, category, venue, vol_24h_usd, oi_usd, markets) VALUES (?,?,?,?,?,?)", (day, cat, vid, m.get("vol_24h_usd"), m.get("oi_usd"), m.get("markets")))
            conn.execute("DELETE FROM benchmark_snapshots WHERE created_at < datetime('now', '-120 days')")
            conn.commit(); conn.close()
        except Exception:
            pass
        return {"fetched_at": time.time(), "categories": cats, "errors": errors}
    if force:
        sources._MEM.pop("benchmarks", None)
        try:
            import os; os.remove(os.path.join(sources.CACHE_DIR, "benchmarks.json"))
        except Exception:
            pass
    return sources.cached("benchmarks", fetch, int(get_setting("competitor_cache_ttl_s", "900") or 900)) or {"categories": {}, "errors": ["no data"]}


def _trend(cat: str, venue: str, days: int = 7) -> Optional[float]:
    try:
        conn = get_db()
        r = conn.execute("SELECT vol_24h_usd FROM benchmark_snapshots WHERE category=? AND venue=? AND day <= date('now', ?) ORDER BY day DESC LIMIT 1", (cat, venue, f"-{days} days")).fetchone()
        conn.close()
        return float(r["vol_24h_usd"]) if r and r["vol_24h_usd"] else None
    except Exception:
        return None


def benchmarks(category: Optional[str] = None, force: bool = False) -> Dict[str, Any]:
    raw = compute(force=force)
    out: Dict[str, Any] = {"generated_at": raw.get("fetched_at"), "categories": {}, "errors": raw.get("errors"),
                           "note": "Free public sources only. Ours: spot from CoinMarketCap (team rule); perps and tokenised markets are the liquidity venue's figures, a reference not our own volume; options unknown. CMC excludes outlier volumes; self-reported numbers vary."}
    for cat, vs in (raw.get("categories") or {}).items():
        if category and cat != category:
            continue
        rows = []
        for vid, m in vs.items():
            v = VENUES.get(vid, {"name": vid, "scope": "intl"})
            rows.append({"venue": vid, "name": v["name"], "scope": v["scope"], "us": bool(v.get("us")), "reference": bool(m.get("reference") or v.get("reference")), "unknown": bool(m.get("unknown")), "_all_pairs": m.get("all_pairs") or {},
                         "vol_24h_usd": m.get("vol_24h_usd"), "oi_usd": m.get("oi_usd"), "markets": m.get("markets"), "maker_fee_pct": m.get("maker_fee_pct"), "taker_fee_pct": m.get("taker_fee_pct"), "visits": m.get("visits"), "source": m.get("source"),
                         "top_pairs": [{"symbol": p["symbol"], "vol_24h_usd": round(p.get("vol_24h_usd") or 0)} for p in (m.get("pairs") or [])[:8]], "vol_7d_ago_usd": _trend(cat, vid)})
        rows.sort(key=lambda r: -(r["vol_24h_usd"] or 0))
        ours = next((r for r in rows if r["us"]), None)
        comp = [r for r in rows if not r["us"] and not r["reference"] and (r["vol_24h_usd"] or 0) > 0]
        leader = comp[0] if comp else None
        in_leader = next((r for r in comp if r["scope"] == "in"), None)
        def gap(a, b):
            if not a or not b or not (b.get("vol_24h_usd") or 0) or not (a.get("vol_24h_usd") or 0):
                return None
            return round(b["vol_24h_usd"] / a["vol_24h_usd"], 1)
        top5 = sum((r["vol_24h_usd"] or 0) for r in comp[:5]) or 1
        targets = []
        if ours and leader and not ours.get("unknown"):
            our_v = ours["vol_24h_usd"] or 0
            for name, ref in (("India leader", in_leader), ("Global leader", leader)):
                if ref and ref is not ours:
                    targets.append({"to_match": f"{name} · {ref['name']}", "their_vol_24h_usd": round(ref["vol_24h_usd"]), "our_vol_24h_usd": round(our_v), "multiple": gap(ours, ref), "daily_volume_needed_usd": round(max(0, ref["vol_24h_usd"] - our_v)),
                                    "step_25pct_usd": round(max(0, ref["vol_24h_usd"] * 0.25 - our_v)) if ref["vol_24h_usd"] * 0.25 > our_v else 0})
            # pair gaps vs the leader's top pairs
            our_pairs = dict(ours.get("_all_pairs") or {}) or {p["symbol"]: p["vol_24h_usd"] for p in ours.get("top_pairs") or []}
            for p in (leader.get("top_pairs") or [])[:6]:
                ov = our_pairs.get(p["symbol"], 0)
                targets.append({"pair": p["symbol"], "leader": leader["name"], "their_vol_24h_usd": p["vol_24h_usd"], "our_vol_24h_usd": round(ov), "multiple": round(p["vol_24h_usd"] / ov, 1) if ov else None, "sop": "sop_asset_spotlight" if ov else None, "note": "not listed here" if not ov else None})
        for r in rows:
            r.pop("_all_pairs", None)
        out["categories"][cat] = {"venues": rows, "leader": leader, "india_leader": in_leader, "ours": ours, "gap_to_leader_x": gap(ours, leader) if ours else None, "gap_to_india_leader_x": gap(ours, in_leader) if ours else None,
                                  "our_share_of_top5_pct": round((ours["vol_24h_usd"] or 0) / top5 * 100, 2) if ours and ours.get("vol_24h_usd") else None, "targets": targets[:10]}
    return out
