"""
Token-level signals for Market Alerts 2.0, from public market data only (allowlisted 'market' session).

Pure helpers (tested): zscore · ath_atl · milestone_cross · most_traded
compute(): fetches what the cohort actually needs and returns (signals, state_updates). State updates — the last
price seen per milestone token, the first milestone of the day, ATH/ATL days per week — are applied only by a live run,
so a dry run never changes what the next live run sees.
"""
from __future__ import annotations
import json
import logging
import math
import time
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

log = logging.getLogger("moengage.alerts2")
HL = "https://api.hyperliquid.xyz/info"
OPTION_BASES = ("BTC", "ETH", "SOL", "XRP", "DOGE", "BNB")
INTERVAL_SECONDS = {"5m": 300, "15m": 900, "1h": 3600, "1d": 86400}


# ── pure ──────────────────────────────────────────────────────────────────────
def zscore(closes: List[float], window: int) -> Optional[Dict[str, float]]:
    """Latest candle return against the mean and deviation of the `window` returns before it."""
    cl = [float(c) for c in closes if c is not None and float(c) > 0]
    if len(cl) < 22:
        return None
    rets = [cl[i] / cl[i - 1] - 1.0 for i in range(1, len(cl))]
    last, base = rets[-1], rets[-(window + 1):-1]
    if len(base) < 20:
        return None
    mu = sum(base) / len(base)
    sd = math.sqrt(sum((r - mu) ** 2 for r in base) / (len(base) - 1))
    if sd <= 0:
        return None
    return {"z": round((last - mu) / sd, 2), "ret_pct": round(last * 100.0, 2), "price": cl[-1], "n": len(base)}


def ath_atl(daily: List[Dict[str, float]], price: Optional[float], window_days: int = 365) -> Optional[str]:
    """'ath' when the live price reaches the highest high of the prior year (today's candle excluded), 'atl' for the lowest low."""
    if not daily or price is None:
        return None
    prior = daily[:-1][-window_days:]
    if len(prior) < 30:
        return None                                            # a token with weeks of history has no meaningful 1-year extreme
    hi = max(float(r["h"]) for r in prior); lo = min(float(r["l"]) for r in prior)
    if price >= hi:
        return "ath"
    if price <= lo:
        return "atl"
    return None


def milestone_cross(prev: Optional[float], now: Optional[float], band: float) -> Optional[Dict[str, Any]]:
    """The round level crossed between two observations (BTC $1,000 bands, ETH $200). The furthest level wins on a big jump."""
    if prev is None or now is None or band <= 0 or prev <= 0 or now <= 0:
        return None
    pb, nb = math.floor(prev / band), math.floor(now / band)
    if nb > pb:
        return {"direction": "up", "level": int(nb * band), "price": now}
    if nb < pb:
        return {"direction": "down", "level": int((nb + 1) * band), "price": now}
    return None


def most_traded(rows: Iterable[Dict[str, Any]], exclude: Iterable[str]) -> Optional[str]:
    ex = {e.upper() for e in exclude}
    best = max((r for r in rows if str(r.get("symbol") or "").upper() not in ex and (r.get("vol_24h_usd") or 0) > 0),
               key=lambda r: float(r.get("vol_24h_usd") or 0), default=None)
    return str(best["symbol"]).upper() if best else None


def iso_week(d: datetime) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


# ── fetchers (network, cached) ────────────────────────────────────────────────
CANDLES_5M = 340          # ~28 hours: one series shared by the live run and the coverage replay, so both hit the same cache entry
CANDLES_1H_EXTRA = 30


def _post(body: Dict[str, Any]) -> Any:
    from ..security import guarded_session
    from ..market.sources import UA, SourceDown
    s = guarded_session("market")
    for attempt in range(3):
        r = s.post(HL, json=body, timeout=15, headers={"User-Agent": UA, "Content-Type": "application/json"})
        if r.status_code == 429 and attempt < 2:
            time.sleep(1.5 * (attempt + 1))                       # the venue meters requests per minute; a short pause is enough
            continue
        if r.status_code == 429:
            raise SourceDown("liquidity venue rate limited")
        r.raise_for_status()
        return r.json()
    raise SourceDown("liquidity venue rate limited")


def mids() -> Dict[str, float]:
    from ..market.sources import cached

    def fetch():
        raw = _post({"type": "allMids"})
        return {k: float(v) for k, v in (raw or {}).items() if not str(k).startswith("@")} if isinstance(raw, dict) else None
    return cached("ma2_mids", fetch, ttl_s=30) or {}


def hl_candles(coin: str, interval: str, n: int) -> List[Dict[str, float]]:
    from ..market.sources import cached
    secs = INTERVAL_SECONDS.get(interval, 3600)

    def fetch():
        start = int((time.time() - secs * (n + 2)) * 1000)
        raw = _post({"type": "candleSnapshot", "req": {"coin": coin, "interval": interval, "startTime": start}})
        if not isinstance(raw, list):
            return None
        return [{"t": int(c["t"]) / 1000, "o": float(c["o"]), "h": float(c["h"]), "l": float(c["l"]), "c": float(c["c"]), "v": float(c["v"]), "n": int(c.get("n") or 0)}
                for c in raw if isinstance(c, dict)]
    return cached(f"ma2_c_{coin.replace(':', '_')}_{interval}_{n}", fetch, ttl_s=min(secs, 120)) or []


def options_turnover() -> Dict[str, float]:
    """24h option turnover per underlying from the options liquidity venue's public tickers (reference data)."""
    from ..market.sources import cached, _json

    def fetch():
        out: Dict[str, float] = {}
        for base in OPTION_BASES:
            try:
                d = _json("https://api.bybit.com/v5/market/tickers", {"category": "option", "baseCoin": base})
                rows = ((d or {}).get("result") or {}).get("list") or []
                tv = sum(float(r.get("turnover24h") or 0) for r in rows)
                if rows:
                    out[base] = tv
            except Exception as e:
                log.info("options turnover failed %s: %s", base, e)
        return out or None
    return cached("ma2_options_turnover", fetch, ttl_s=600) or {}


# ── the run's signal picture ──────────────────────────────────────────────────
def compute(tokens: Dict[str, List[str]], cfg: Dict[str, Any], state: Dict[str, Any], whales: List[Dict[str, Any]],
            now: datetime, ctx: Optional[Dict[str, Any]] = None) -> Tuple[Dict[str, Any], Dict[str, Any], List[str]]:
    """tokens: product → tokens the cohort is exposed to. Returns (signals, state_updates, errors)."""
    from ..market import context as mctx, sources
    errors: List[str] = []
    if ctx is None:
        try:
            ctx = mctx.market_context()
        except Exception as e:
            ctx = {}
            errors.append(f"market context: {e}")
    ctx = ctx or {}
    crypto_rows = [r for r in ctx.get("crypto_markets") or [] if isinstance(r, dict)]
    us_rows = [r for k in ("equities", "indices", "commodities") for r in (ctx.get(k) or []) if isinstance(r, dict)]
    us_coin = {str(r.get("symbol")).upper(): r.get("hl_symbol") for r in us_rows if r.get("hl_symbol")}

    try:
        live = mids()
    except Exception as e:
        live = {}
        errors.append(f"mark prices: {e}")
    prices: Dict[str, float] = {}
    for r in crypto_rows + us_rows:
        if r.get("price"):
            prices[str(r["symbol"]).upper()] = float(r["price"])
    for k, v in live.items():
        prices[k.split(":", 1)[-1].upper()] = v
    chg = {str(r["symbol"]).upper(): r.get("chg_24h") for r in crypto_rows + us_rows if r.get("symbol") and r.get("chg_24h") is not None}

    opt = {}
    if tokens.get("options") or cfg.get("theme_order"):
        try:
            opt = options_turnover()
        except Exception as e:
            errors.append(f"options turnover: {e}")
    futures_listed = sorted({k for k in live if ":" not in k} or {str(r["symbol"]).upper() for r in crypto_rows})
    listed = {
        "futures": futures_listed,
        "us_futures": sorted(us_coin),
        "options": sorted(opt),                              # empty when the tickers are unreachable: never assume a listing
        "spot": sorted({str(r["symbol"]).upper() for r in crypto_rows if r.get("on_binance")} | set(tokens.get("spot") or [])),
    }

    # Z-score moves on the tokens each product's users are exposed to
    moves: Dict[str, Dict[str, Any]] = {}
    budget = int(cfg.get("max_tokens_per_run") or 80)
    for product in ("futures", "us_futures", "options", "spot"):
        interval = (cfg.get("candle") or {}).get(product, "1h")
        window = int((cfg.get("z_window") or {}).get(interval, 168))
        for token in (tokens.get(product) or [])[:budget]:
            try:
                if product == "spot":
                    rows = sources.klines(token, "1d", window + 2)
                elif product == "us_futures":
                    coin = us_coin.get(token)
                    rows = hl_candles(coin, interval, window + 2) if coin else []
                else:
                    rows = hl_candles(token, interval, window + 2)      # options move with their underlying
                z = zscore([r["c"] for r in rows], window)
                if z:
                    if product != "spot" and prices.get(token):
                        z["price"] = prices[token]
                    moves[f"{token}|{product}"] = {**z, "interval": interval}
            except Exception as e:
                errors.append(f"candles {token}/{product}: {str(e)[:80]}")

    # 1-year ATH/ATL on exposed tokens plus the most traded majors
    day = now.strftime("%Y-%m-%d"); week = iso_week(now)
    universe = list(dict.fromkeys([t for p in ("futures", "spot", "options") for t in (tokens.get(p) or [])] +
                                  [str(r["symbol"]).upper() for r in sorted(crypto_rows, key=lambda r: -(r.get("vol_24h_usd") or 0))[:10]]))[:budget]
    ath: Dict[str, str] = {}
    for token in universe:
        try:
            daily = sources.klines(token, "1d", int(cfg.get("ath_window_days") or 365) + 1)
            kind = ath_atl(daily, prices.get(token), int(cfg.get("ath_window_days") or 365))
            if kind:
                ath[token] = kind
        except Exception as e:
            errors.append(f"ath/atl {token}: {str(e)[:80]}")
    cap = int(cfg.get("ath_cap_per_token_per_week") or 2)
    blocked = sorted(t for t in ath if day not in (state.get(f"ath_days:{t}:{week}") or []) and len(state.get(f"ath_days:{t}:{week}") or []) >= cap)

    # round-number milestones against the last price the engine saw
    updates: Dict[str, Any] = {}
    milestones: Dict[str, Dict[str, Any]] = {}
    first_today: Dict[str, float] = {}
    for token, band in (cfg.get("milestone_bands") or {}).items():
        now_px = prices.get(token)
        ms = milestone_cross(state.get(f"ms_last:{token}"), now_px, float(band))
        if now_px:
            updates[f"ms_last:{token}"] = now_px
        f = state.get(f"ms_first:{token}:{day}")
        if ms:
            milestones[token] = ms
            if f is None:
                updates[f"ms_first:{token}:{day}"] = ms["level"]
        if f is not None:
            first_today[token] = f

    most = {
        "futures": most_traded(crypto_rows, (cfg.get("volume_exclude") or {}).get("futures", [])),
        "us_futures": most_traded(us_rows, (cfg.get("volume_exclude") or {}).get("us_futures", [])),
        "options": (max(opt.items(), key=lambda kv: kv[1])[0] if opt else None),
        "spot": most_traded([r for r in crypto_rows if r.get("on_binance")], (cfg.get("volume_exclude") or {}).get("spot", [])),
    }
    regime = str(((ctx.get("crypto") or {}).get("regime") or {}).get("label") or (ctx.get("hooks") or {}).get("regime") or "unknown")
    sig = {"prices": prices, "chg_24h": chg, "moves": moves, "ath_atl": ath, "ath_week_blocked": blocked, "milestones": milestones,
           "milestone_day_first": first_today, "most_traded": most, "whales": [w for w in whales if isinstance(w, dict) and w.get("token")],
           "futures_listed": futures_listed, "listed": listed, "regime": regime, "computed_at": now.isoformat(),
           "sources": {"moves": "liquidity-venue candles (F&O/US hourly or 5-minute), spot daily candles", "ath_atl": "daily candles, 1-year window",
                       "most_traded": "24h volume on the liquidity venue; options from the options liquidity venue's public tickers",
                       "whales": "CoinDCX whale feed (uploaded)" if whales else "no whale feed connected"}}
    return sig, updates, errors


def ath_state_updates(sent_tokens: Iterable[str], state: Dict[str, Any], now: datetime) -> Dict[str, Any]:
    day, week = now.strftime("%Y-%m-%d"), iso_week(now)
    out = {}
    for t in set(sent_tokens):
        days = list(state.get(f"ath_days:{t}:{week}") or [])
        if day not in days:
            out[f"ath_days:{t}:{week}"] = days + [day]
    return out
