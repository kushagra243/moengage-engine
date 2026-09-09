"""
Hyperliquid (our liquidity venue) versus centralised exchanges, and the
on-chain perps landscape.

Free sources: Hyperliquid info API (global daily volume, OI, users; per-coin
contexts), DefiLlama open-interest overview (on-chain perps protocols — the
derivatives *volume* overview is paid, so volumes come from the venues
themselves), CoinMarketCap derivatives listing (CEX volume + OI), Binance
futures (funding for every symbol in one call, OI per symbol), Bybit linear
tickers (OI value + funding), OKX swaps (OI + funding). Coinglass is wired as
an optional keyed source (setting market_coinglass_api_key) for cross-exchange
OI, funding and liquidations; without a key its section says so.
"""
from __future__ import annotations
import time
from typing import Any, Dict, List, Optional

from ..database import get_setting
from ..security import redact
from . import sources
from .competitors import fetch_cmc_listing, canon, _f

H = {"User-Agent": "Mozilla/5.0 (compatible; moengage-engine intelligence)", "Accept": "application/json"}
COINS = ["BTC", "ETH", "SOL", "XRP", "DOGE", "HYPE", "SUI", "BNB"]
CEX = {"binance": "Binance", "okx": "OKX", "bybit": "Bybit", "bitget": "Bitget", "gate": "Gate", "mexc": "MEXC", "kucoin": "KuCoin", "kraken": "Kraken"}


def hl_global() -> Dict[str, Any]:
    s = sources.guarded_session("market")
    r = s.post("https://api.hyperliquid.xyz/info", json={"type": "globalStats"}, headers=H, timeout=15)
    r.raise_for_status(); d = r.json() or {}
    return {"daily_volume_usd": _f(d.get("dailyVolume")), "oi_usd": _f(d.get("oi")), "users": int(_f(d.get("nUsers"))), "total_volume_usd": _f(d.get("totalVolume")), "source": "api.hyperliquid.xyz globalStats"}


def defillama_oi() -> Dict[str, Any]:
    d = sources._json("https://api.llama.fi/overview/open-interest", {"excludeTotalDataChart": "true", "excludeTotalDataChartBreakdown": "true"}, 30.0, headers=H) or {}
    ps = []
    for p in d.get("protocols") or []:
        ps.append({"name": p.get("displayName") or p.get("name"), "slug": p.get("slug"), "oi_usd": _f(p.get("total24h")), "chg_1d_pct": p.get("change_1d"), "chg_7d_pct": p.get("change_7d"), "chains": (p.get("chains") or [])[:3], "hl_ecosystem": any("Hyperliquid" in str(c) for c in (p.get("chains") or []))})
    ps.sort(key=lambda p: -p["oi_usd"])
    total = sum(p["oi_usd"] for p in ps) or 1.0
    for p in ps:
        p["share_of_onchain_pct"] = round(p["oi_usd"] / total * 100, 1)
    return {"total_onchain_oi_usd": total, "protocols": ps[:20], "source": "defillama open-interest (free); on-chain derivatives volume overview is a paid endpoint"}


def binance_funding_map() -> Dict[str, Dict[str, float]]:
    rows = sources._json("https://fapi.binance.com/fapi/v1/premiumIndex", None, 20.0) or []
    return {canon(r["symbol"][:-4]): {"funding_1h_pct": _f(r.get("lastFundingRate")) * 100 / 8, "mark": _f(r.get("markPrice"))} for r in rows if str(r.get("symbol", "")).endswith("USDT")}


def binance_oi_usd(coin: str, mark: float) -> Optional[float]:
    try:
        d = sources._json("https://fapi.binance.com/fapi/v1/openInterest", {"symbol": f"{coin}USDT"}, 10.0) or {}
        return _f(d.get("openInterest")) * mark
    except Exception:
        return None


def bybit_map() -> Dict[str, Dict[str, float]]:
    d = sources._json("https://api.bybit.com/v5/market/tickers", {"category": "linear"}, 20.0) or {}
    out = {}
    for r in ((d.get("result") or {}).get("list") or []):
        sym = str(r.get("symbol") or "")
        if sym.endswith("USDT"):
            out[canon(sym[:-4])] = {"oi_usd": _f(r.get("openInterestValue")), "funding_1h_pct": _f(r.get("fundingRate")) * 100 / 8, "vol_24h_usd": _f(r.get("turnover24h"))}
    return out


def okx_map(coins: List[str]) -> Dict[str, Dict[str, float]]:
    out: Dict[str, Dict[str, float]] = {}
    try:
        d = sources._json("https://www.okx.com/api/v5/public/open-interest", {"instType": "SWAP"}, 20.0) or {}
        for r in d.get("data") or []:
            inst = str(r.get("instId") or "")
            if inst.endswith("-USDT-SWAP"):
                out[canon(inst.split("-")[0])] = {"oi_usd": _f(r.get("oiUsd"))}
    except Exception:
        pass
    for c in coins[:6]:
        try:
            d = sources._json("https://www.okx.com/api/v5/public/funding-rate", {"instId": f"{c}-USDT-SWAP"}, 10.0) or {}
            fr = (d.get("data") or [{}])[0].get("fundingRate")
            if fr is not None:
                out.setdefault(c, {})["funding_1h_pct"] = _f(fr) * 100 / 8
        except Exception:
            continue
    return out


def coinglass(path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    key = get_setting("market_coinglass_api_key", "").strip()
    if not key:
        return {"configured": False, "note": "set market_coinglass_api_key in Settings → Market to add cross-exchange OI, funding and liquidations by venue (Coinglass free tier)"}
    d = sources._json(f"https://open-api-v4.coinglass.com{path}", params, 20.0, headers={**H, "CG-API-KEY": key}) or {}
    return {"configured": True, "data": d.get("data"), "code": d.get("code")}


def compare(force: bool = False) -> Dict[str, Any]:
    def fetch():
        errors: List[str] = []
        out: Dict[str, Any] = {"fetched_at": time.time()}
        try:
            out["hyperliquid"] = hl_global()
        except Exception as e:
            out["hyperliquid"] = {}; errors.append("hyperliquid: " + redact(str(e))[:80])
        try:
            listing = fetch_cmc_listing()
        except Exception as e:
            listing = {}; errors.append("cmc: " + redact(str(e))[:80])
        cex = []
        for slug, name in CEX.items():
            c = listing.get(slug) or {}
            if _f(c.get("derivativesVol24h")):
                cex.append({"venue": slug, "name": name, "vol_24h_usd": _f(c.get("derivativesVol24h")), "oi_usd": _f(c.get("derivativesOpenInterests")), "markets": c.get("derivativesMarketPairs"), "taker_fee_pct": c.get("takerFee")})
        cex.sort(key=lambda r: -r["vol_24h_usd"]); out["cex"] = cex
        hl = out.get("hyperliquid") or {}
        if hl.get("daily_volume_usd") and cex:
            top5 = sum(r["vol_24h_usd"] for r in cex[:5]); rank = 1 + sum(1 for r in cex if r["vol_24h_usd"] > hl["daily_volume_usd"])
            bn = next((r for r in cex if r["venue"] == "binance"), None); lead = cex[0]
            out["hl_vs_cex"] = {"hl_daily_volume_usd": hl["daily_volume_usd"], "hl_oi_usd": hl["oi_usd"], "rank_if_listed_with_cex": rank, "share_of_cex_top5_volume_pct": round(hl["daily_volume_usd"] / (top5 + hl["daily_volume_usd"]) * 100, 1),
                              "leader": lead["name"], "vs_leader_volume_x": round(lead["vol_24h_usd"] / hl["daily_volume_usd"], 1),
                              "vs_binance_volume_x": round(bn["vol_24h_usd"] / hl["daily_volume_usd"], 1) if bn else None, "hl_oi_vs_binance_oi_x": round((bn["oi_usd"] or 0) / hl["oi_usd"], 2) if bn and bn.get("oi_usd") and hl.get("oi_usd") else None}
        try:
            out["onchain"] = defillama_oi()
        except Exception as e:
            out["onchain"] = {"protocols": []}; errors.append("defillama: " + redact(str(e))[:80])
        # per-coin: HL vs Binance / Bybit / OKX
        per_coin = []
        try:
            from .exchanges import hyperliquid_perps
            hlp = (hyperliquid_perps().get("crypto") or {})
            coins = [c for c in COINS if c in hlp] + [k for k, v in sorted(hlp.items(), key=lambda kv: -(kv[1].get("oi_usd") or 0)) if k not in COINS][:4]
            bmap = binance_funding_map(); ymap = bybit_map(); omap = okx_map(coins)
            for c in coins[:10]:
                h = hlp.get(c) or {}
                b = bmap.get(c) or {}; b_oi = binance_oi_usd(c, b.get("mark") or h.get("price") or 0) if b else None
                y = ymap.get(c) or {}; o = omap.get(c) or {}
                ois = {"hyperliquid": h.get("oi_usd"), "binance": b_oi, "bybit": y.get("oi_usd"), "okx": o.get("oi_usd")}
                tot = sum(v for v in ois.values() if v) or 1.0
                fund = {"hyperliquid": h.get("funding_1h_pct"), "binance": b.get("funding_1h_pct"), "bybit": y.get("funding_1h_pct"), "okx": o.get("funding_1h_pct")}
                cheapest = min((k for k, v in fund.items() if v is not None), key=lambda k: fund[k], default=None)
                per_coin.append({"coin": c, "oi_usd": ois, "hl_oi_share_pct": round((h.get("oi_usd") or 0) / tot * 100, 1), "vol_24h_usd": {"hyperliquid": h.get("vol_24h_usd"), "bybit": y.get("vol_24h_usd")}, "funding_1h_pct": {k: (round(v, 5) if v is not None else None) for k, v in fund.items()},
                                 "cheapest_for_longs": cheapest, "hl_funding_edge_vs_binance_1h_pct": round((b.get("funding_1h_pct") or 0) - (h.get("funding_1h_pct") or 0), 5) if b and h.get("funding_1h_pct") is not None else None})
        except Exception as e:
            errors.append("per-coin: " + redact(str(e))[:80])
        out["per_coin"] = per_coin
        try:
            out["coinglass"] = coinglass("/api/futures/open-interest/exchange-list", {"symbol": "BTC"})
        except Exception as e:
            out["coinglass"] = {"configured": True, "error": redact(str(e))[:80]}
        out["errors"] = errors
        out["note"] = ("Hyperliquid is the venue behind our perps: these are its figures, not CoinDCX volume. CEX figures are CoinMarketCap-adjusted 24h derivatives volume and OI. On-chain OI from DefiLlama (free); "
                       "funding shown per hour (CEX 8h rates ÷ 8). CoinMarketMan has no public API; Coinglass needs a free key.")
        return out
    if force:
        sources._MEM.pop("onchain_cex", None)
        try:
            import os; os.remove(os.path.join(sources.CACHE_DIR, "onchain_cex.json"))
        except Exception:
            pass
    return sources.cached("onchain_cex", fetch, int(get_setting("competitor_cache_ttl_s", "900") or 900)) or {"errors": ["no data"]}
