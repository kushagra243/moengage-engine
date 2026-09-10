"""
Exchange-native universe: only what is tradable on Binance (USDT spot) and
Hyperliquid (perps on the main dex + builder dexes such as xyz / flx / km).

Hyperliquid's metaAndAssetCtxs returns mark price, previous-day price, funding,
open interest and 24h notional volume for every listed perp in one call;
Binance's ticker/24hr does the same for every spot pair. So the whole market
picture is two or three requests, refreshed every few minutes, with no
hand-picked lists and no third-party price aggregators in the loop.

Builder dexes also carry equities, indices and commodities as perps
(xyz:TSLA, flx:GOLD, km:US500 …). Those are the ONLY equities/commodities the
engine reports, because they are the only ones a user can act on.
"""
from __future__ import annotations
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from ..database import get_setting
from ..security import redact
from .sources import cached, _json, _get, UA, SourceDown

log = logging.getLogger("moengage.market.exchanges")
HL = "https://api.hyperliquid.xyz/info"
BINANCE_BASES = ("https://api.binance.com", "https://data-api.binance.vision", "https://api.binance.us")
STABLES = {"USDT", "USDC", "FDUSD", "TUSD", "BUSD", "DAI", "USDP", "USDE", "USD1", "EUR", "TRY", "BRL", "JPY", "PYUSD", "RLUSD", "USDD", "FRAX", "AEUR"}
COMMODITY = re.compile(r"^(GOLD|SILVER|XAU|XAG|OIL|USOIL|WTI|BRENT|GAS|NATGAS|COPPER|PALLADIUM|PLATINUM|URANIUM|WHEAT|CORN|COFFEE|COCOA|SUGAR)", re.I)
INDEX = re.compile(r"(^XYZ100$|^SP500$|US500|USA500|USTECH|SMALL2000|NASDAQ|^SPX|^DOW|MAG7|^SEMIS$|NIFTY|NIKKEI|^DAX$|FTSE|EU50|USBOND|USENERGY|^ROBOT$|INFOTECH|NUCLEAR|^DEFENSE$|^ENERGY$|BIOTECH|^DRAM$|^SKHX$)", re.I)
FX = re.compile(r"^(EUR|JPY|GBP|CHF|AUD|CAD|CNH|INR|DXY|USDINDEX)$", re.I)


def _hl(body: Dict[str, Any], timeout: float = 15.0):
    from ..security import guarded_session
    s = guarded_session("market")
    r = s.post(HL, json=body, timeout=timeout, headers={"User-Agent": UA, "Content-Type": "application/json"})
    if r.status_code == 429:
        raise SourceDown("hyperliquid rate limited")
    r.raise_for_status()
    return r.json()


ETF = re.compile(r"^(SPY|QQQ|IWM|DIA|VTI|VOO|TLT|IEF|HYG|LQD|GLD|SLV|USO|UNG|ARKK|ARKW|SOXL|SOXS|TQQQ|SQQQ|UPRO|SPXU|SMH|XLF|XLE|XLK|XLV|XLI|XLU|EEM|EFA|FXI|KWEB|MCHI|EWY|KORU|EWJ|INDA|EPI|INDL|IBIT|ETHA|BITO|GBTC|ETHE|FBTC|ARKB|HODL|BITB|EZBC|BTCO|BRRR|MSTU|MSTX|CONL|NVDL|TSLL|YINN|YANG|VXX|UVXY|SVXY|TMF|TBT)$", re.I)


def _asset_class(name: str) -> str:
    if COMMODITY.search(name):
        return "commodity"
    if INDEX.search(name) or ETF.search(name):
        return "index"          # index perps and ETF perps are one product for us (Index & ETF perps)
    if FX.search(name):
        return "fx"
    return "equity"


# ── Hyperliquid ───────────────────────────────────────────────────────────────
def hyperliquid_perps() -> Dict[str, Any]:
    """Main-dex perps (crypto) + builder-dex perps (equities/indices/commodities), with contexts."""
    def fetch():
        try:
            meta, ctxs = _hl({"type": "metaAndAssetCtxs"})
        except Exception as e:
            log.info("hyperliquid meta failed: %s", redact(str(e))); return None
        crypto: Dict[str, Dict[str, Any]] = {}
        for u, c in zip(meta.get("universe", []), ctxs):
            if u.get("isDelisted"):
                continue
            try:
                px = float(c.get("markPx") or 0); prev = float(c.get("prevDayPx") or 0)
                crypto[u["name"]] = {"symbol": u["name"], "price": px, "chg_24h": round((px / prev - 1) * 100, 2) if prev else None,
                                     "funding_1h_pct": round(float(c.get("funding") or 0) * 100, 5), "funding_apr_pct": round(float(c.get("funding") or 0) * 24 * 365 * 100, 1),
                                     "oi_usd": round(float(c.get("openInterest") or 0) * px, 0), "vol_24h_usd": round(float(c.get("dayNtlVlm") or 0), 0),
                                     "max_leverage": u.get("maxLeverage"), "venue": "hyperliquid"}
            except (TypeError, ValueError):
                continue
        # builder dexes
        rwa: Dict[str, Dict[str, Any]] = {}
        try:
            dexes = [d for d in _hl({"type": "perpDexs"}) if d and d.get("name")]
        except Exception as e:
            log.info("perpDexs failed: %s", redact(str(e))); dexes = []
        for d in dexes[:12]:
            name = d["name"]
            try:
                m2, c2 = _hl({"type": "metaAndAssetCtxs", "dex": name})
            except Exception:
                continue
            for u, c in zip(m2.get("universe", []), c2):
                if u.get("isDelisted"):
                    continue
                full = u["name"]; short = full.split(":", 1)[-1]
                try:
                    px = float(c.get("markPx") or 0); prev = float(c.get("prevDayPx") or 0)
                except (TypeError, ValueError):
                    continue
                if px <= 0:
                    continue
                key = short.upper()
                row = {"symbol": key, "hl_symbol": full, "dex": name, "name": short, "price": px, "chg_24h": round((px / prev - 1) * 100, 2) if prev else None,
                       "funding_1h_pct": round(float(c.get("funding") or 0) * 100, 5), "oi_usd": round(float(c.get("openInterest") or 0) * px, 0),
                       "vol_24h_usd": round(float(c.get("dayNtlVlm") or 0), 0), "asset_class": _asset_class(short), "venue": f"hyperliquid:{name}", "currency": "USD"}
                # keep the most liquid listing per underlying
                if key not in rwa or row["vol_24h_usd"] > rwa[key]["vol_24h_usd"]:
                    rwa[key] = row
        return {"crypto": crypto, "rwa": rwa, "dexes": [d["name"] for d in dexes], "fetched_at": time.time()}
    return cached("hl_perps", fetch, 300) or {"crypto": {}, "rwa": {}, "dexes": []}


# ── Binance ───────────────────────────────────────────────────────────────────
def binance_spot() -> Dict[str, Dict[str, Any]]:
    """All TRADING USDT spot pairs with 24h ticker."""
    def fetch():
        for base in BINANCE_BASES:
            try:
                info = _json(base + "/api/v3/exchangeInfo", timeout=20)
                trading = {s["baseAsset"] for s in info.get("symbols", []) if s.get("quoteAsset") == "USDT" and s.get("status") == "TRADING"}
                tick = _json(base + "/api/v3/ticker/24hr", timeout=20)
                out = {}
                for t in tick:
                    sym = t.get("symbol", "")
                    if not sym.endswith("USDT"):
                        continue
                    b = sym[:-4]
                    if b not in trading or b in STABLES:
                        continue
                    try:
                        out[b] = {"symbol": b, "price": float(t["lastPrice"]), "chg_24h": round(float(t["priceChangePercent"]), 2), "vol_24h_usd": round(float(t["quoteVolume"]), 0), "venue": "binance"}
                    except (TypeError, ValueError):
                        continue
                return out
            except Exception as e:
                log.info("binance spot failed (%s): %s", base, redact(str(e)))
        return None
    return cached("binance_spot", fetch, 300) or {}


# ── merged universe ───────────────────────────────────────────────────────────
def universe() -> Dict[str, Any]:
    mode = (get_setting("market_universe_mode", "either") or "either").lower()     # either | both | hyperliquid | binance | manual
    top_n = int(get_setting("market_top_n", "60") or 60)
    hl = hyperliquid_perps(); bn = binance_spot()
    hl_c = hl.get("crypto", {}); rwa = hl.get("rwa", {})
    names = set(hl_c) | set(bn)
    if mode == "both":
        names = set(hl_c) & set(bn)
    elif mode == "hyperliquid":
        names = set(hl_c)
    elif mode == "binance":
        names = set(bn)
    elif mode == "manual":
        manual = [x.strip().upper() for x in (get_setting("market_universe", "") or "").split(",") if x.strip()]
        names = {n for n in names if n in manual}
    rows: List[Dict[str, Any]] = []
    for n in names:
        h, b = hl_c.get(n), bn.get(n)
        src = h or b
        if not src:
            continue
        rows.append({"symbol": n, "price": (h or b)["price"], "chg_24h": (h or b).get("chg_24h"),
                     "vol_24h_usd": round((h or {}).get("vol_24h_usd", 0) + (b or {}).get("vol_24h_usd", 0), 0),
                     "funding_apr_pct": (h or {}).get("funding_apr_pct"), "oi_usd": (h or {}).get("oi_usd"), "max_leverage": (h or {}).get("max_leverage"),
                     "on_binance": bool(b), "on_hyperliquid": bool(h), "binance_chg_24h": (b or {}).get("chg_24h"), "hl_chg_24h": (h or {}).get("chg_24h")})
    rows.sort(key=lambda r: -(r["vol_24h_usd"] or 0))
    top = rows[:top_n]
    by_class: Dict[str, List[Dict[str, Any]]] = {"equity": [], "index": [], "commodity": [], "fx": []}
    for r in sorted(rwa.values(), key=lambda x: -(x["vol_24h_usd"] or 0)):
        by_class.setdefault(r["asset_class"], []).append(r)
    return {
        "mode": mode, "top_n": top_n,
        "counts": {"binance_usdt_pairs": len(bn), "hyperliquid_perps": len(hl_c), "on_both": len(set(hl_c) & set(bn)), "universe": len(rows),
                   "hl_builder_assets": len(rwa), "hl_dexes": hl.get("dexes", [])},
        "crypto": top, "crypto_all_symbols": [r["symbol"] for r in rows],
        "equities": by_class["equity"][:40], "indices": by_class["index"][:20], "commodities": by_class["commodity"][:20], "fx": by_class["fx"][:10],
        "fetched_at": hl.get("fetched_at"),
        "ok": bool(rows),
    }


def movers(rows: List[Dict[str, Any]], n: int = 8, min_vol_usd: float = 1_000_000) -> Dict[str, List[Dict[str, Any]]]:
    liquid = [r for r in rows if r.get("chg_24h") is not None and (r.get("vol_24h_usd") or 0) >= min_vol_usd]
    up = sorted(liquid, key=lambda r: -r["chg_24h"])[:n]
    down = sorted(liquid, key=lambda r: r["chg_24h"])[:n]
    fund = [r for r in rows if r.get("funding_apr_pct") is not None and (r.get("oi_usd") or 0) >= 2_000_000]
    crowded_long = sorted(fund, key=lambda r: -r["funding_apr_pct"])[:5]
    crowded_short = sorted(fund, key=lambda r: r["funding_apr_pct"])[:5]
    return {"up": up, "down": down, "crowded_long": [r for r in crowded_long if r["funding_apr_pct"] > 20], "crowded_short": [r for r in crowded_short if r["funding_apr_pct"] < -10]}
