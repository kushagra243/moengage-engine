"""
Regime classification from explicit, inspectable numbers (ported from the
orchestrator and extended with cross-asset context). Every snapshot carries
the evidence so a reviewer can disagree with the label.
"""
from __future__ import annotations
import math
import statistics
from typing import Any, Dict, List

from .sources import klines, funding_rates


def _ema(values: List[float], span: int):
    if len(values) < span:
        return None
    k = 2 / (span + 1)
    ema = statistics.fmean(values[:span])
    for v in values[span:]:
        ema = v * k + ema * (1 - k)
    return ema


def _pct(a: float, b: float) -> float:
    return 0.0 if not b else (a - b) / b * 100


def _returns(closes: List[float]) -> List[float]:
    return [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes)) if closes[i - 1] > 0 and closes[i] > 0]


def _realised_vol(closes: List[float], window: int = 30) -> float:
    r = _returns(closes[-(window + 1):])
    return statistics.pstdev(r) * math.sqrt(365) * 100 if len(r) >= 5 else 0.0


def _vol_percentile(closes: List[float], window: int = 30) -> float:
    if len(closes) < window * 3:
        return 50.0
    series = []
    for i in range(window, len(closes)):
        r = _returns(closes[i - window:i + 1])
        if len(r) >= 5:
            series.append(statistics.pstdev(r) * math.sqrt(365) * 100)
    if not series:
        return 50.0
    today = series[-1]
    return sum(1 for v in series if v <= today) / len(series) * 100


def asset_stats(symbol: str, fast: int = 20, slow: int = 50) -> Dict[str, Any]:
    rows = klines(symbol, "1d", 365)
    if len(rows) < 60:
        return {"symbol": symbol, "ok": False}
    closes = [r["c"] for r in rows]
    last = closes[-1]
    ef, es = _ema(closes, fast), _ema(closes, slow)
    high_30 = max(r["h"] for r in rows[-30:])
    return {
        "symbol": symbol, "ok": True, "price": round(last, 4),
        "chg_24h": round(_pct(last, closes[-2]), 2), "chg_7d": round(_pct(last, closes[-8]), 2), "chg_30d": round(_pct(last, closes[-31]), 2),
        "ema_fast": round(ef, 2) if ef else None, "ema_slow": round(es, 2) if es else None,
        "stacked_up": bool(ef and es and last > ef > es), "stacked_down": bool(ef and es and last < ef < es),
        "vol_30d": round(_realised_vol(closes), 1), "vol_percentile": round(_vol_percentile(closes), 1),
        "drawdown_from_30d_high": round(_pct(last, high_30), 2),
        "range_7d_pct": round(_pct(max(r["h"] for r in rows[-7:]), min(r["l"] for r in rows[-7:])), 2),
    }


def classify(btc: Dict[str, Any], breadth_up_pct: float, funding_bias: float) -> Dict[str, Any]:
    vol_p = btc.get("vol_percentile", 50)
    dd = btc.get("drawdown_from_30d_high", 0)
    if btc.get("chg_24h", 0) <= -6 and breadth_up_pct <= 20 and vol_p >= 80:
        label, reasons = "capitulation", [f"BTC {btc['chg_24h']}% in 24h", f"only {breadth_up_pct:.0f}% of universe up", f"vol at {vol_p:.0f}th pct"]
    elif vol_p >= 75 and (btc.get("chg_7d", 0) <= -8 or dd <= -12):
        label, reasons = "high_volatility_down", [f"vol at {vol_p:.0f}th pct", f"BTC {btc.get('chg_7d')}% 7d", f"{dd}% off 30d high"]
    elif vol_p >= 75 and btc.get("chg_7d", 0) >= 8:
        label, reasons = "high_volatility_up", [f"vol at {vol_p:.0f}th pct", f"BTC +{btc.get('chg_7d')}% 7d"]
    elif btc.get("stacked_up") and btc.get("chg_30d", 0) >= 5:
        label, reasons = "trending_up", ["price above both EMAs", f"BTC +{btc['chg_30d']}% 30d", f"{breadth_up_pct:.0f}% of universe up on week"]
    elif btc.get("stacked_down") and btc.get("chg_30d", 0) <= -5:
        label, reasons = "trending_down", ["price below both EMAs", f"BTC {btc['chg_30d']}% 30d"]
    else:
        label, reasons = "chop", ["no EMA trend structure", f"BTC {btc.get('chg_30d', 0)}% 30d", f"vol at {vol_p:.0f}th pct"]
    if funding_bias > 0.0004:
        reasons.append("funding crowded long")
    elif funding_bias < -0.0002:
        reasons.append("funding crowded short")
    return {"label": label, "reasons": reasons, "vol_percentile": vol_p, "breadth_up_pct": round(breadth_up_pct, 1), "funding_bias": round(funding_bias, 6)}


def crypto_regime(universe: List[str]) -> Dict[str, Any]:
    assets = {}
    for b in dict.fromkeys(["BTC", "ETH", "SOL"] + universe):
        st = asset_stats(b)
        if st.get("ok"):
            assets[b] = st
    if "BTC" not in assets:
        return {"ok": False, "regime": {"label": "unknown", "reasons": ["no BTC candles from any source"]}, "assets": {}}
    week = [a["chg_7d"] for a in assets.values()]
    breadth = sum(1 for c in week if c > 0) / len(week) * 100 if week else 50.0
    funding = funding_rates()
    tracked = [funding.get(b) for b in assets if funding.get(b) is not None]
    bias = statistics.fmean(tracked) if tracked else 0.0
    ranked = sorted(assets.values(), key=lambda a: a["chg_7d"], reverse=True)
    return {"ok": True, "regime": classify(assets["BTC"], breadth, bias), "assets": assets, "breadth_up_pct": round(breadth, 1), "funding_bias": round(bias, 6),
            "leaders": [{"asset": a["symbol"], "chg_7d": a["chg_7d"], "chg_24h": a["chg_24h"]} for a in ranked[:5]],
            "laggards": [{"asset": a["symbol"], "chg_7d": a["chg_7d"], "chg_24h": a["chg_24h"]} for a in ranked[-5:]]}
