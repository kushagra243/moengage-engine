"""
Assembles the market context the agent and the UI consume, cached in SQLite.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ..database import get_db, get_setting
from ..security import redact
from . import sources, regime as regime_mod, news as news_mod
from .hooks import build_hooks


def _init():
    conn = get_db()
    conn.execute("""CREATE TABLE IF NOT EXISTS market_snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    regime TEXT, snapshot_json TEXT)""")
    conn.commit(); conn.close()


def _latest(max_age_s: int) -> Optional[Dict[str, Any]]:
    _init()
    conn = get_db()
    r = conn.execute("SELECT created_at, snapshot_json FROM market_snapshots ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    if not r:
        return None
    try:
        created = datetime.fromisoformat(r["created_at"]).replace(tzinfo=timezone.utc)
    except Exception:
        return None
    if (datetime.now(timezone.utc) - created).total_seconds() > max_age_s:
        return None
    try:
        return json.loads(r["snapshot_json"])
    except Exception:
        return None


def _lst(key: str, default: str) -> List[str]:
    return [x.strip() for x in (get_setting(key, default) or default).split(",") if x.strip()]


def _movers(rows: List[Dict[str, Any]], n: int = 6) -> List[Dict[str, Any]]:
    rows = [r for r in rows if r.get("chg_24h") is not None]
    return sorted(rows, key=lambda r: abs(r["chg_24h"]), reverse=True)[:n]


def market_context(force: bool = False, include_news: bool = True) -> Dict[str, Any]:
    ttl = sources.ttl()
    if not force:
        c = _latest(ttl)
        if c:
            c["cached"] = True
            return c
    from concurrent.futures import ThreadPoolExecutor
    universe = _lst("market_universe", "BTC,ETH,SOL,XRP,BNB,DOGE")
    ctx: Dict[str, Any] = {"generated_at": datetime.now(timezone.utc).isoformat(), "errors": []}

    def step(name, fn):
        try:
            return name, fn(), None
        except Exception as e:
            return name, None, redact(str(e))

    steps = {
        "crypto": lambda: regime_mod.crypto_regime(universe),
        "crypto_markets": lambda: sources.coingecko_markets(universe),
        "crypto_global": sources.coingecko_global,
        "crypto_trending": sources.coingecko_trending,
        "fear_greed": sources.fear_greed,
        "stocks_fear_greed": sources.cnn_fear_greed,
        "inr_marks": lambda: {k: v for k, v in sources.coindcx_ticker().items() if k in universe},
        "equities": lambda: sources.quotes(_lst("market_equities", "^GSPC,^NDX,^NSEI,^BSESN")),
        "commodities": lambda: sources.quotes(_lst("market_commodities", "GC=F,SI=F,CL=F,BZ=F,NG=F")),
        "macro": lambda: sources.quotes(_lst("market_macro", "DX-Y.NYB,^TNX,^VIX,INR=X")),
        "calendar": sources.econ_calendar,
    }
    if include_news:
        steps["news_raw"] = lambda: news_mod.headlines(limit_per_feed=8)
    with ThreadPoolExecutor(max_workers=8) as ex:
        for name, val, err in ex.map(lambda kv: step(kv[0], kv[1]), steps.items()):
            if err:
                ctx["errors"].append(f"{name}: {err}")
                if name == "crypto":
                    ctx["crypto"] = {"ok": False, "regime": {"label": "unknown", "reasons": [err]}}
            elif name == "news_raw":
                ctx["news"] = {"risk_flags": val["risk_flags"], "top": {k: v[:6] for k, v in val["categories"].items()}, "errors": val["errors"]}
            else:
                ctx[name] = val
    ctx["crypto_movers"] = _movers(ctx.get("crypto_markets") or [])
    ctx["equity_movers"] = _movers(ctx.get("equities") or [])
    ctx["commodity_movers"] = _movers(ctx.get("commodities") or [])
    ctx["hooks"] = build_hooks(ctx)
    ctx["narrative"] = narrative(ctx)
    _init()
    conn = get_db()
    conn.execute("INSERT INTO market_snapshots (regime, snapshot_json) VALUES (?, ?)", (ctx["hooks"]["regime"], json.dumps(ctx, default=str)))
    conn.commit(); conn.close()
    ctx["cached"] = False
    return ctx


def narrative(ctx: Dict[str, Any]) -> str:
    cr = ctx.get("crypto") or {}
    reg = cr.get("regime") or {}
    lines = [f"Crypto regime: {reg.get('label','unknown')} ({', '.join(reg.get('reasons', []))})."]
    btc = (cr.get("assets") or {}).get("BTC")
    if btc:
        lines.append(f"BTC {btc['price']} ({btc['chg_24h']:+.1f}% 24h, {btc['chg_7d']:+.1f}% 7d, {btc['chg_30d']:+.1f}% 30d), {btc['drawdown_from_30d_high']}% off 30d high, vol pct {btc['vol_percentile']}.")
    fg = ctx.get("fear_greed") or {}
    if fg:
        lines.append(f"Fear & Greed {fg.get('value')} ({fg.get('label')}).")
    if ctx.get("crypto_movers"):
        lines.append("Crypto movers 24h: " + ", ".join(f"{m['symbol']} {m['chg_24h']:+.1f}%" for m in ctx["crypto_movers"][:5]) + ".")
    if ctx.get("equity_movers"):
        lines.append("Equities: " + ", ".join(f"{m['name']} {m['chg_24h']:+.1f}%" for m in ctx["equity_movers"][:5]) + ".")
    if ctx.get("commodity_movers"):
        lines.append("Commodities: " + ", ".join(f"{m['name']} {m['chg_24h']:+.1f}%" for m in ctx["commodity_movers"][:5]) + ".")
    mac = ctx.get("macro") or []
    if mac:
        lines.append("Macro: " + ", ".join(f"{m['name']} {m['price']}" + (f" ({m['chg_24h']:+.1f}%)" if m.get("chg_24h") is not None else "") for m in mac) + ".")
    risk = ((ctx.get("news") or {}).get("risk_flags")) or []
    if risk:
        lines.append(f"Risk headlines ({len(risk)}): " + "; ".join(r["title"][:70] for r in risk[:3]) + ".")
    pol = (ctx.get("hooks") or {}).get("angle_policy") or {}
    if pol.get("prefer"):
        lines.append("Angles that fit: " + ", ".join(pol["prefer"]) + ".")
    if pol.get("block"):
        lines.append("Angles REFUSED: " + ", ".join(pol["block"]) + ".")
    if ctx.get("errors"):
        lines.append("Data gaps: " + "; ".join(ctx["errors"][:4]) + ".")
    return "\n".join(lines)


def market_news(category: Optional[str] = None, query: Optional[str] = None, limit: int = 15) -> Dict[str, Any]:
    if query:
        return {"query": query, "items": news_mod.search(query, limit)}
    return news_mod.headlines(category=category, limit_per_feed=limit)


def campaign_hooks(force: bool = False) -> Dict[str, Any]:
    return market_context(force=force)["hooks"]
