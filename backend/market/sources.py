"""
Keyless market data with verified fallbacks (verified 2026-09-07), all via the
'market' GuardedSession. Disk+memory cached (default 15 min).

Crypto     CoinGecko /coins/markets (one call, 60–120s) → CoinPaprika → Binance 24h ticker
           (api.binance.com → data-api.binance.vision → api.binance.us); OKX for candles fallback
Indices    CBOE delayed JSON (_SPX,_NDX,_VIX) · NSE allIndices (Nifty/Sensex-class) · Yahoo v8 chart
Commodit.  Yahoo v8 (GC=F, SI=F, CL=F, BZ=F, NG=F, HG=F) → FRED daily CSV for WTI/Brent/HH gas
Macro      Yahoo (DX-Y.NYB, ^TNX) → FRED (DTWEXBGS, DGS10); USD/INR via Frankfurter → er-api
Sentiment  alternative.me crypto F&G; CNN stocks F&G (needs full browser header set)
Calendar   ForexFactory this-week JSON
Yahoo is IP-rate-limited aggressively: on 429 we back off 15 min and use fallbacks.
"""
from __future__ import annotations
import csv
import io
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

from ..database import get_setting
from ..security import guarded_session, redact

log = logging.getLogger("moengage.market")
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data")
CACHE_DIR = os.path.join(DATA_DIR, "cache")
_MEM: Dict[str, Any] = {}
_BACKOFF: Dict[str, float] = {}     # host → unix ts until which we skip it
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36"


class SourceDown(RuntimeError):
    pass


def ttl() -> int:
    return int(get_setting("market_cache_ttl_s", "900") or 900)


def cached(key: str, fn, ttl_s: Optional[int] = None):
    ttl_s = ttl_s or ttl()
    now = time.time()
    if key in _MEM and now - _MEM[key][0] < ttl_s:
        return _MEM[key][1]
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, key.replace("/", "_") + ".json")
    if os.path.exists(path) and now - os.path.getmtime(path) < ttl_s:
        try:
            with open(path) as f:
                v = json.load(f)
            _MEM[key] = (now, v)
            return v
        except Exception:
            pass
    v = fn()
    if v is not None:
        _MEM[key] = (now, v)
        try:
            with open(path, "w") as f:
                json.dump(v, f)
        except OSError:
            pass
    else:
        # serve stale cache if the source is down
        if os.path.exists(path):
            try:
                with open(path) as f:
                    v = json.load(f)
                if isinstance(v, dict):
                    v["_stale"] = True
                return v
            except Exception:
                pass
    return v


def _host(url: str) -> str:
    return url.split("/")[2]


def _get(url: str, params=None, timeout: float = 15.0, headers=None, accept: str = "application/json, text/plain, */*"):
    h = _host(url)
    if _BACKOFF.get(h, 0) > time.time():
        raise SourceDown(f"{h} in backoff")
    s = guarded_session("market")
    hdr = {"User-Agent": UA, "Accept": accept, "Accept-Language": "en-US,en;q=0.9"}
    if headers:
        hdr.update(headers)
    r = s.get(url, params=params, timeout=timeout, headers=hdr)
    if r.status_code == 429 or r.status_code == 418:
        _BACKOFF[h] = time.time() + 900
        raise SourceDown(f"{h} rate limited ({r.status_code})")
    r.raise_for_status()
    return r


def _json(url: str, params=None, timeout: float = 15.0, headers=None):
    return _get(url, params, timeout, headers).json()


# ── crypto ────────────────────────────────────────────────────────────────────
COINGECKO_IDS = {
    "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana", "XRP": "ripple", "BNB": "binancecoin", "DOGE": "dogecoin",
    "ADA": "cardano", "AVAX": "avalanche-2", "LINK": "chainlink", "TON": "the-open-network", "SUI": "sui", "APT": "aptos",
    "ARB": "arbitrum", "OP": "optimism", "NEAR": "near", "INJ": "injective-protocol", "SEI": "sei-network", "PEPE": "pepe",
    "WIF": "dogwifcoin", "TIA": "celestia", "TRX": "tron", "DOT": "polkadot", "POL": "polygon-ecosystem-token", "LTC": "litecoin",
    "SHIB": "shiba-inu", "UNI": "uniswap", "ATOM": "cosmos", "HBAR": "hedera-hashgraph", "AAVE": "aave", "BCH": "bitcoin-cash",
}


def _cg_headers() -> Dict[str, str]:
    k = get_setting("market_coingecko_key", "")
    return {"x-cg-demo-api-key": k} if k else {}


def coingecko_markets(symbols: List[str]) -> List[Dict[str, Any]]:
    ids = [COINGECKO_IDS[s] for s in symbols if s in COINGECKO_IDS]
    key = "cg_markets_" + "_".join(sorted(symbols))[:80]

    def fetch():
        try:
            rows = _json("https://api.coingecko.com/api/v3/coins/markets",
                         {"vs_currency": "usd", "ids": ",".join(ids), "order": "market_cap_desc", "per_page": 100, "page": 1,
                          "sparkline": "false", "price_change_percentage": "1h,24h,7d,30d"}, headers=_cg_headers())
            return [{"symbol": r["symbol"].upper(), "name": r["name"], "price": r["current_price"], "mcap": r.get("market_cap"), "rank": r.get("market_cap_rank"),
                     "vol_24h": r.get("total_volume"), "chg_1h": r.get("price_change_percentage_1h_in_currency"),
                     "chg_24h": r.get("price_change_percentage_24h_in_currency"), "chg_7d": r.get("price_change_percentage_7d_in_currency"),
                     "chg_30d": r.get("price_change_percentage_30d_in_currency"), "ath_chg": r.get("ath_change_percentage"), "source": "coingecko"} for r in rows]
        except Exception as e:
            log.info("coingecko failed: %s", redact(str(e)))
        # fallback 1: CoinPaprika (rank, 24h/7d/30d %, mcap)
        try:
            rows = _json("https://api.coinpaprika.com/v1/tickers", {"limit": 300})
            want = set(symbols)
            out = []
            for r in rows:
                if r.get("symbol") in want and r.get("rank", 9999) < 500:
                    q = (r.get("quotes") or {}).get("USD") or {}
                    out.append({"symbol": r["symbol"], "name": r.get("name"), "price": q.get("price"), "mcap": q.get("market_cap"), "rank": r.get("rank"),
                                "vol_24h": q.get("volume_24h"), "chg_1h": q.get("percent_change_1h"), "chg_24h": q.get("percent_change_24h"),
                                "chg_7d": q.get("percent_change_7d"), "chg_30d": q.get("percent_change_30d"), "ath_chg": q.get("percent_from_price_ath"), "source": "coinpaprika"})
            if out:
                return out
        except Exception as e:
            log.info("coinpaprika failed: %s", redact(str(e)))
        # fallback 2: Binance 24h tickers (24h only)
        for base in ("https://api.binance.com", "https://data-api.binance.vision", "https://api.binance.us"):
            try:
                rows = _json(base + "/api/v3/ticker/24hr", {"symbols": json.dumps([f"{s}USDT" for s in symbols])})
                return [{"symbol": r["symbol"][:-4], "name": r["symbol"][:-4], "price": float(r["lastPrice"]), "chg_24h": float(r["priceChangePercent"]),
                         "vol_24h": float(r["quoteVolume"]), "chg_7d": None, "chg_30d": None, "source": "binance"} for r in rows]
            except Exception as e:
                log.info("binance ticker failed (%s): %s", base, redact(str(e)))
        return None
    return cached(key, fetch) or []


def coingecko_global() -> Dict[str, Any]:
    def fetch():
        try:
            d = _json("https://api.coingecko.com/api/v3/global", headers=_cg_headers())["data"]
            return {"total_mcap_usd": d["total_market_cap"].get("usd"), "mcap_chg_24h": d.get("market_cap_change_percentage_24h_usd"),
                    "btc_dominance": d.get("market_cap_percentage", {}).get("btc"), "eth_dominance": d.get("market_cap_percentage", {}).get("eth"), "source": "coingecko"}
        except Exception as e:
            log.info("coingecko global failed: %s", redact(str(e))); return None
    return cached("cg_global", fetch, 1800) or {}


def coingecko_trending() -> List[Dict[str, Any]]:
    def fetch():
        try:
            d = _json("https://api.coingecko.com/api/v3/search/trending", headers=_cg_headers())
            return [{"symbol": c["item"]["symbol"].upper(), "name": c["item"]["name"], "rank": c["item"].get("market_cap_rank"),
                     "chg_24h": ((c["item"].get("data") or {}).get("price_change_percentage_24h") or {}).get("usd")} for c in d.get("coins", [])[:10]]
        except Exception as e:
            log.info("coingecko trending failed: %s", redact(str(e))); return None
    return cached("cg_trending", fetch, 1800) or []


def klines(symbol: str, interval: str = "1d", limit: int = 365) -> List[Dict[str, float]]:
    def fetch():
        for base in ("https://api.binance.com", "https://data-api.binance.vision", "https://api.binance.us"):
            try:
                raw = _json(base + "/api/v3/klines", {"symbol": f"{symbol}USDT", "interval": interval, "limit": limit})
                return [{"t": r[0] / 1000, "o": float(r[1]), "h": float(r[2]), "l": float(r[3]), "c": float(r[4]), "v": float(r[5])} for r in raw]
            except Exception as e:
                log.info("klines failed %s %s: %s", base, symbol, redact(str(e)))
        try:
            bar = {"1d": "1D", "4h": "4H", "1h": "1H"}.get(interval, "1D")
            raw = _json("https://www.okx.com/api/v5/market/candles", {"instId": f"{symbol}-USDT", "bar": bar, "limit": min(limit, 300)})
            rows = raw.get("data", [])[::-1]
            return [{"t": int(r[0]) / 1000, "o": float(r[1]), "h": float(r[2]), "l": float(r[3]), "c": float(r[4]), "v": float(r[5])} for r in rows]
        except Exception as e:
            log.info("okx klines failed %s: %s", symbol, redact(str(e)))
        return None
    return cached(f"kl_{symbol}_{interval}_{limit}", fetch) or []


def funding_rates() -> Dict[str, float]:
    def fetch():
        try:
            raw = _json("https://fapi.binance.com/fapi/v1/premiumIndex")
            return {r["symbol"][:-4]: float(r.get("lastFundingRate", 0) or 0) for r in raw if str(r.get("symbol", "")).endswith("USDT")}
        except Exception:
            pass
        out = {}
        for b in ("BTC", "ETH", "SOL"):
            try:
                d = _json("https://www.okx.com/api/v5/public/funding-rate", {"instId": f"{b}-USDT-SWAP"}).get("data", [])
                if d:
                    out[b] = float(d[0].get("fundingRate", 0) or 0)
            except Exception:
                continue
        return out or None
    return cached("funding", fetch) or {}


def fear_greed() -> Dict[str, Any]:
    def fetch():
        try:
            d = _json("https://api.alternative.me/fng/", {"limit": 8, "format": "json"})["data"]
            cur = d[0]
            return {"value": int(cur["value"]), "label": cur["value_classification"], "history": [int(x["value"]) for x in d], "source": "alternative.me"}
        except Exception as e:
            log.info("fear&greed failed: %s", redact(str(e))); return None
    return cached("fng", fetch, 3600) or {}


def cnn_fear_greed() -> Dict[str, Any]:
    def fetch():
        try:
            d = _json("https://production.dataviz.cnn.io/index/fearandgreed/graphdata", timeout=15,
                      headers={"Referer": "https://www.cnn.com/markets/fear-and-greed", "Origin": "https://www.cnn.com"})
            fg = d.get("fear_and_greed") or {}
            return {"value": round(float(fg.get("score", 0)), 1), "label": fg.get("rating"), "prev_week": fg.get("previous_1_week"), "prev_month": fg.get("previous_1_month"), "source": "cnn"}
        except Exception as e:
            log.info("cnn f&g failed: %s", redact(str(e))); return None
    return cached("cnn_fng", fetch, 6 * 3600) or {}


def coindcx_ticker() -> Dict[str, Dict[str, float]]:
    def fetch():
        try:
            rows = _json("https://api.coindcx.com/exchange/ticker")
            out = {}
            for r in rows:
                m = str(r.get("market", ""))
                if m.endswith("INR"):
                    try:
                        out[m[:-3]] = {"price_inr": float(r.get("last_price") or 0), "chg_24h": float(r.get("change_24_hour") or 0)}
                    except (TypeError, ValueError):
                        continue
            return out
        except Exception as e:
            log.info("coindcx failed: %s", redact(str(e))); return None
    return cached("coindcx", fetch) or {}


# ── equities / commodities / macro ────────────────────────────────────────────
NAMES = {
    "^GSPC": "S&P 500", "^IXIC": "Nasdaq Composite", "^NDX": "Nasdaq 100", "^DJI": "Dow Jones", "^NSEI": "Nifty 50", "^BSESN": "Sensex", "^NSEBANK": "Bank Nifty",
    "^VIX": "VIX", "^TNX": "US 10Y yield", "DX-Y.NYB": "Dollar index", "INR=X": "USD/INR", "GC=F": "Gold", "SI=F": "Silver", "CL=F": "WTI crude",
    "BZ=F": "Brent crude", "NG=F": "Natural gas", "HG=F": "Copper", "COIN": "Coinbase", "MSTR": "MicroStrategy", "HOOD": "Robinhood",
    "AAPL": "Apple", "NVDA": "Nvidia", "TSLA": "Tesla", "MSFT": "Microsoft",
}
CBOE = {"^GSPC": "_SPX", "^NDX": "_NDX", "^VIX": "_VIX"}
NSE = {"^NSEI": "NIFTY 50", "^NSEBANK": "NIFTY BANK", "^BSESN": None}
FRED = {"^TNX": "DGS10", "CL=F": "DCOILWTICO", "BZ=F": "DCOILBRENTEU", "NG=F": "DHHNGSP", "DX-Y.NYB": "DTWEXBGS", "INR=X": "DEXINUS", "^VIX": "VIXCLS"}


# Finnhub (free key: 60 calls/min). Indices are premium on Finnhub, so use liquid ETF proxies and label them.
FINNHUB_MAP = {
    "^GSPC": ("SPY", "S&P 500 (SPY proxy)"), "^NDX": ("QQQ", "Nasdaq 100 (QQQ proxy)"), "^IXIC": ("QQQ", "Nasdaq (QQQ proxy)"), "^DJI": ("DIA", "Dow (DIA proxy)"),
    "^NSEI": ("INDA", "India (INDA proxy)"), "^VIX": ("VIXY", "VIX (VIXY proxy)"), "GC=F": ("OANDA:XAU_USD", "Gold"), "SI=F": ("OANDA:XAG_USD", "Silver"),
    "CL=F": ("OANDA:WTICO_USD", "WTI crude"), "BZ=F": ("OANDA:BCO_USD", "Brent crude"), "NG=F": ("OANDA:NATGAS_USD", "Natural gas"), "HG=F": ("OANDA:XCU_USD", "Copper"),
    "INR=X": ("OANDA:USD_INR", "USD/INR"), "^TNX": ("OANDA:US10YB_USD", "US 10Y"), "DX-Y.NYB": ("OANDA:USD_INDEX", "Dollar index"),
}


def finnhub_key() -> str:
    return get_setting("market_finnhub_key", "").strip()


def _finnhub(symbol: str) -> Optional[Dict[str, Any]]:
    key = finnhub_key()
    if not key:
        return None
    fsym, label = FINNHUB_MAP.get(symbol, (symbol, NAMES.get(symbol, symbol)))
    d = _json("https://finnhub.io/api/v1/quote", {"symbol": fsym, "token": key}, 10.0)
    if not d or not d.get("c"):
        return None
    return {"symbol": symbol, "name": label, "price": d.get("c"), "chg_24h": round(float(d.get("dp") or 0), 2), "chg_7d": None, "chg_30d": None,
            "currency": "INR" if symbol == "INR=X" else "USD", "source": f"finnhub:{fsym}", "prev_close": d.get("pc"), "as_of": d.get("t")}


def finnhub_news(category: str = "general", limit: int = 15) -> List[Dict[str, Any]]:
    key = finnhub_key()
    if not key:
        return []
    def fetch():
        try:
            rows = _json("https://finnhub.io/api/v1/news", {"category": category, "token": key}, 12.0)
            out = []
            for r in rows[:limit]:
                from datetime import datetime as _dt, timezone as _tz
                out.append({"title": (r.get("headline") or "")[:200], "link": r.get("url"), "published": _dt.fromtimestamp(int(r.get("datetime") or 0), tz=_tz.utc).isoformat(),
                            "source": f"{r.get('source') or 'Finnhub'} (API)", "sentiment": "neutral"})
            return out
        except Exception as e:
            log.info("finnhub news failed: %s", redact(str(e))); return None
    return cached(f"fh_news_{category}", fetch, 900) or []


def finnhub_calendar() -> List[Dict[str, Any]]:
    key = finnhub_key()
    if not key:
        return []
    def fetch():
        try:
            d = _json("https://finnhub.io/api/v1/calendar/economic", {"token": key}, 12.0)
            rows = d.get("economicCalendar") or []
            return [{"title": r.get("event"), "country": r.get("country"), "date": r.get("time"), "impact": {"high": "High", "medium": "Medium", "low": "Low"}.get(str(r.get("impact")).lower(), r.get("impact")),
                     "forecast": r.get("estimate"), "previous": r.get("prev")} for r in rows if str(r.get("impact")).lower() in ("high", "medium")][:80]
        except Exception as e:
            log.info("finnhub calendar failed: %s", redact(str(e))); return None
    return cached("fh_calendar", fetch, 3600) or []


def _yahoo(symbol: str) -> Optional[Dict[str, Any]]:
    d = _json(f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}", {"range": "1mo", "interval": "1d"}, 15.0)
    res = d["chart"]["result"][0]
    meta = res["meta"]
    closes = [c for c in (res["indicators"]["quote"][0].get("close") or []) if c is not None]
    price = meta.get("regularMarketPrice") or (closes[-1] if closes else None)
    prev = meta.get("chartPreviousClose") or meta.get("previousClose") or (closes[-2] if len(closes) > 1 else None)
    chg = ((price - prev) / prev * 100) if (price and prev) else meta.get("regularMarketChangePercent")
    chg7 = ((price - closes[-6]) / closes[-6] * 100) if (price and len(closes) >= 6 and closes[-6]) else None
    chg30 = ((price - closes[0]) / closes[0] * 100) if (price and closes and closes[0]) else None
    return {"symbol": symbol, "name": NAMES.get(symbol, meta.get("shortName") or symbol), "price": price, "chg_24h": round(chg, 2) if chg is not None else None,
            "chg_7d": round(chg7, 2) if chg7 is not None else None, "chg_30d": round(chg30, 2) if chg30 is not None else None,
            "currency": meta.get("currency"), "source": "yahoo"}


def _cboe(symbol: str) -> Optional[Dict[str, Any]]:
    code = CBOE.get(symbol)
    if not code:
        return None
    d = _json(f"https://cdn.cboe.com/api/global/delayed_quotes/quotes/{code}.json")["data"]
    return {"symbol": symbol, "name": NAMES.get(symbol, symbol), "price": d.get("current_price"), "chg_24h": round(float(d.get("price_change_percent") or 0), 2),
            "chg_7d": None, "chg_30d": None, "currency": "USD", "source": "cboe"}


def _nse_all() -> Dict[str, Dict[str, Any]]:
    def fetch():
        try:
            d = _json("https://www.nseindia.com/api/allIndices", headers={"Referer": "https://www.nseindia.com/", "Accept": "application/json"})
            return {r["index"]: r for r in d.get("data", [])}
        except Exception as e:
            log.info("nse failed: %s", redact(str(e))); return None
    return cached("nse_all", fetch, 300) or {}


def _nse(symbol: str) -> Optional[Dict[str, Any]]:
    name = NSE.get(symbol)
    if not name:
        return None
    r = _nse_all().get(name)
    if not r:
        return None
    return {"symbol": symbol, "name": NAMES.get(symbol, name), "price": r.get("last"), "chg_24h": r.get("percentChange"), "chg_7d": None, "chg_30d": None,
            "currency": "INR", "source": "nse", "advances": r.get("advances"), "declines": r.get("declines"), "pe": r.get("pe")}


def _fred(symbol: str) -> Optional[Dict[str, Any]]:
    sid = FRED.get(symbol)
    if not sid:
        return None
    r = _get("https://fred.stlouisfed.org/graph/fredgraph.csv", {"id": sid}, 8.0, accept="text/csv,*/*")
    rows = [x for x in csv.reader(io.StringIO(r.text)) if len(x) == 2 and x[1] not in (".", "")]
    if len(rows) < 3:
        return None
    last, prev = float(rows[-1][1]), float(rows[-2][1])
    return {"symbol": symbol, "name": NAMES.get(symbol, sid), "price": last, "chg_24h": round((last - prev) / prev * 100, 2) if prev else None, "chg_7d": None, "chg_30d": None,
            "currency": "USD", "source": f"fred:{sid}", "as_of": rows[-1][0]}


METALS = {"GC=F": "xau", "SI=F": "xag"}


def _metal(symbol: str) -> Optional[Dict[str, Any]]:
    unit = METALS.get(symbol)
    if not unit:
        return None
    def fetch():
        try:
            return _json("https://api.coingecko.com/api/v3/simple/price", {"ids": "bitcoin", "vs_currencies": f"usd,{unit}", "include_24hr_change": "true"}, headers=_cg_headers())
        except Exception as e:
            log.info("metal via coingecko failed: %s", redact(str(e))); return None
    d = cached("cg_btc_metals_" + unit, fetch, 900)
    if not d:
        return None
    b = d.get("bitcoin") or {}
    usd, per = b.get("usd"), b.get(unit)
    if not usd or not per:
        return None
    price = usd / per
    # 24h change of metal ≈ change(BTC/USD) − change(BTC/metal)
    cu, cm = b.get("usd_24h_change"), b.get(f"{unit}_24h_change")
    chg = round(((1 + cu / 100) / (1 + cm / 100) - 1) * 100, 2) if (cu is not None and cm is not None) else None
    return {"symbol": symbol, "name": NAMES.get(symbol, symbol), "price": round(price, 2), "chg_24h": chg, "chg_7d": None, "chg_30d": None,
            "currency": "USD", "source": "coingecko:btc/" + unit, "note": "spot derived from BTC priced in USD and in troy ounces"}


def _fx_inr() -> Optional[Dict[str, Any]]:
    for url, pick in (("https://api.frankfurter.app/latest?from=USD&to=INR", lambda d: d["rates"]["INR"]),
                      ("https://open.er-api.com/v6/latest/USD", lambda d: d["rates"]["INR"])):
        try:
            v = pick(_json(url))
            return {"symbol": "INR=X", "name": "USD/INR", "price": v, "chg_24h": None, "chg_7d": None, "chg_30d": None, "currency": "INR", "source": url.split("/")[2]}
        except Exception as e:
            log.info("fx failed %s: %s", url, redact(str(e)))
    return None


def quote(symbol: str) -> Optional[Dict[str, Any]]:
    def fetch():
        chain = []
        if finnhub_key():
            chain.append(_finnhub)          # keyed API first when available
        if symbol in CBOE:
            chain.append(_cboe)
        if symbol in NSE and NSE[symbol]:
            chain.append(_nse)
        chain.append(_yahoo)
        if symbol in FRED:
            chain.append(_fred)
        if symbol in METALS:
            chain.append(_metal)
        if symbol == "INR=X":
            chain.append(lambda s: _fx_inr())
        for fn in chain:
            try:
                q = fn(symbol)
                if q and q.get("price") is not None:
                    return q
            except SourceDown as e:
                log.info("%s: %s", symbol, e)
            except Exception as e:
                log.info("quote %s via %s failed: %s", symbol, getattr(fn, "__name__", "fn"), redact(str(e)))
        return None
    return cached("q_" + symbol.replace("^", "idx_").replace("=", "_"), fetch, 600)


def quotes(symbols: List[str]) -> List[Dict[str, Any]]:
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=min(6, max(1, len(symbols)))) as ex:
        results = list(ex.map(quote, symbols))
    return [q for q in results if q]


def econ_calendar() -> List[Dict[str, Any]]:
    fh = finnhub_calendar()
    if fh:
        return fh
    def fetch():
        try:
            rows = _json("https://nfs.faireconomy.media/ff_calendar_thisweek.json")
            return [{"title": r.get("title"), "country": r.get("country"), "date": r.get("date"), "impact": r.get("impact"),
                     "forecast": r.get("forecast"), "previous": r.get("previous")} for r in rows if r.get("impact") in ("High", "Medium")][:80]
        except Exception as e:
            log.info("calendar failed: %s", redact(str(e))); return None
    return cached("ff_calendar", fetch, 3600) or []


def source_status() -> Dict[str, Any]:
    """Which data sources are active, keyed, in backoff or blocked — for the Market tab's data panel."""
    now = time.time()
    def st(host):
        return "backoff" if _BACKOFF.get(host, 0) > now else "ok"
    keyed = {"finnhub": bool(finnhub_key()), "coingecko_demo": bool(get_setting("market_coingecko_key", ""))}
    rows = [
        {"name": "Finnhub", "kind": "API (keyed)", "covers": "equities, ETF index proxies, commodities/FX via OANDA, news, economic calendar", "active": keyed["finnhub"], "state": st("finnhub.io") if keyed["finnhub"] else "add market_finnhub_key (free, 60 calls/min)"},
        {"name": "CoinGecko", "kind": "API" + (" (demo key)" if keyed["coingecko_demo"] else " (keyless, ~5/min)"), "covers": "crypto markets, global, trending, gold/silver via BTC/XAU", "active": True, "state": st("api.coingecko.com")},
        {"name": "Binance / OKX", "kind": "API (keyless)", "covers": "daily candles for regime maths, funding", "active": True, "state": st("api.binance.com")},
        {"name": "CoinPaprika", "kind": "API (keyless)", "covers": "crypto fallback", "active": True, "state": st("api.coinpaprika.com")},
        {"name": "CBOE", "kind": "API (keyless)", "covers": "S&P 500, Nasdaq 100, VIX", "active": True, "state": st("cdn.cboe.com")},
        {"name": "NSE India", "kind": "API (keyless)", "covers": "Nifty 50, Bank Nifty", "active": True, "state": st("www.nseindia.com")},
        {"name": "Yahoo Finance", "kind": "API (keyless, IP-limited)", "covers": "any symbol; 7d/30d change", "active": True, "state": st("query1.finance.yahoo.com")},
        {"name": "FRED", "kind": "API (keyless)", "covers": "yields, oil, gas, dollar index (daily)", "active": True, "state": st("fred.stlouisfed.org")},
        {"name": "CoinDCX", "kind": "API (keyless)", "covers": "INR marks", "active": True, "state": st("api.coindcx.com")},
        {"name": "alternative.me / CNN", "kind": "API (keyless)", "covers": "crypto and stocks Fear & Greed", "active": True, "state": st("api.alternative.me")},
        {"name": "ForexFactory", "kind": "JSON (keyless)", "covers": "this week's macro calendar", "active": True, "state": st("nfs.faireconomy.media")},
        {"name": "RSS", "kind": "feeds", "covers": "CoinDesk, Cointelegraph, The Block, Decrypt, Bloomberg, CNBC, WSJ, ET, LiveMint, BS, SEC, SEBI, RBI, Google News", "active": True, "state": "ok"},
    ]
    return {"keyed": keyed, "sources": rows}
