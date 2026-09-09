"""
Assembles the market context the agent and the UI consume, cached in SQLite.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ..database import get_db, get_setting
from ..security import redact
from . import sources, regime as regime_mod, news as news_mod, exchanges
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
    ctx: Dict[str, Any] = {"generated_at": datetime.now(timezone.utc).isoformat(), "errors": []}
    # exchange-native universe first: everything below is restricted to what Binance / Hyperliquid list
    try:
        uni = exchanges.universe()
    except Exception as e:
        uni = {"ok": False, "crypto": [], "crypto_all_symbols": [], "equities": [], "indices": [], "commodities": [], "fx": [], "counts": {}}
        ctx["errors"].append("exchanges: " + redact(str(e)))
    ctx["universe"] = {k: uni.get(k) for k in ("mode", "top_n", "counts", "fetched_at", "ok")}
    listed = set(uni.get("crypto_all_symbols") or [])
    regime_syms = [r["symbol"] for r in uni.get("crypto", [])[:20]]
    universe = list(dict.fromkeys(["BTC", "ETH", "SOL"] + regime_syms))

    def step(name, fn):
        try:
            return name, fn(), None
        except Exception as e:
            return name, None, redact(str(e))

    steps = {
        "crypto": lambda: regime_mod.crypto_regime(universe),
        "crypto_markets": lambda: [dict(r, name=r["symbol"]) for r in uni.get("crypto", [])],
        "crypto_global": sources.coingecko_global,
        "crypto_trending": sources.coingecko_trending,
        "fear_greed": sources.fear_greed,
        "stocks_fear_greed": sources.cnn_fear_greed,
        "inr_marks": lambda: sources.coindcx_ticker(),
        "equities": lambda: uni.get("equities", []),
        "indices": lambda: uni.get("indices", []),
        "commodities": lambda: uni.get("commodities", []),
        "macro": lambda: uni.get("fx", []),
        "calendar": sources.econ_calendar,
        "web3": lambda: __import__("backend.market.onchain", fromlist=["trending"]).trending(),
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
    mv = exchanges.movers(uni.get("crypto", []))
    ctx["crypto_movers"] = (mv["up"][:4] + mv["down"][:4])
    ctx["crypto_movers_detail"] = mv
    ctx["equity_movers"] = _movers(ctx.get("equities") or [])
    ctx["index_movers"] = _movers(ctx.get("indices") or [])
    ctx["commodity_movers"] = _movers(ctx.get("commodities") or [])
    # competitive intelligence (internal): Indian + global venues vs our own markets
    try:
        from . import competitors
        ctx["competitors"] = competitors.intel({"crypto_markets": ctx.get("crypto_markets") or []})
    except Exception as e:
        ctx["errors"].append("competitors: " + redact(str(e))[:100]); ctx["competitors"] = {"actions": [], "surges": []}
    # derivatives-grade signals: new listings (persisted) and open-interest movers vs ~24h ago
    try:
        from . import derivs
        ctx["listings"] = derivs.track_listings(uni)
        ctx["oi_movers"] = derivs.oi_changes(ctx, derivs.previous_snapshot())
    except Exception as e:
        ctx["errors"].append("derivs: " + redact(str(e)))
        ctx["listings"] = {"new": []}; ctx["oi_movers"] = {"surge": [], "drop": []}
    # trending only if tradable on our venues
    ctx["crypto_trending"] = [t for t in (ctx.get("crypto_trending") or []) if t.get("symbol") in listed]
    ctx["inr_marks"] = {k: v for k, v in (ctx.get("inr_marks") or {}).items() if k in listed}
    try:
        ctx["sources"] = sources.source_status()
    except Exception:
        ctx["sources"] = {}
    ctx["hooks"] = build_hooks(ctx)
    try:
        from ..autopilot import tier0_trigger
        ctx["tier0"] = tier0_trigger(ctx)
    except Exception:
        ctx["tier0"] = None
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
    u = (ctx.get("universe") or {}).get("counts") or {}
    lines = [f"Universe: {u.get('binance_usdt_pairs', 0)} Binance USDT pairs, {u.get('hyperliquid_perps', 0)} Hyperliquid perps ({u.get('on_both', 0)} on both); {u.get('hl_builder_assets', 0)} equity/index/commodity perps on HL builder dexes. All figures below are restricted to these.",
             f"Crypto regime: {reg.get('label','unknown')} ({', '.join(reg.get('reasons', []))})."]
    btc = (cr.get("assets") or {}).get("BTC")
    if btc:
        lines.append(f"BTC {btc['price']} ({btc['chg_24h']:+.1f}% 24h, {btc['chg_7d']:+.1f}% 7d, {btc['chg_30d']:+.1f}% 30d), {btc['drawdown_from_30d_high']}% off 30d high, vol pct {btc['vol_percentile']}.")
    fg = ctx.get("fear_greed") or {}
    if fg:
        lines.append(f"Fear & Greed {fg.get('value')} ({fg.get('label')}).")
    md = ctx.get("crypto_movers_detail") or {}
    if md.get("up"):
        lines.append("Top gainers 24h (≥$1M vol): " + ", ".join(f"{m['symbol']} {m['chg_24h']:+.1f}%" for m in md["up"][:5]) + ".")
    if md.get("down"):
        lines.append("Top losers 24h: " + ", ".join(f"{m['symbol']} {m['chg_24h']:+.1f}%" for m in md["down"][:5]) + ".")
    if md.get("crowded_long"):
        lines.append("Crowded longs (funding APR): " + ", ".join(f"{m['symbol']} {m['funding_apr_pct']:+.0f}%" for m in md["crowded_long"][:4]) + ".")
    if md.get("crowded_short"):
        lines.append("Crowded shorts: " + ", ".join(f"{m['symbol']} {m['funding_apr_pct']:+.0f}%" for m in md["crowded_short"][:4]) + ".")
    if ctx.get("equity_movers"):
        lines.append("HL equity perps: " + ", ".join(f"{m['name']} {m['chg_24h']:+.1f}%" for m in ctx["equity_movers"][:5]) + ".")
    if ctx.get("index_movers"):
        lines.append("HL index perps: " + ", ".join(f"{m['name']} {m['chg_24h']:+.1f}%" for m in ctx["index_movers"][:4]) + ".")
    if ctx.get("commodity_movers"):
        lines.append("HL commodity perps: " + ", ".join(f"{m['name']} {m['chg_24h']:+.1f}%" for m in ctx["commodity_movers"][:5]) + ".")
    risk = ((ctx.get("news") or {}).get("risk_flags")) or []
    if risk:
        lines.append(f"Risk headlines ({len(risk)}): " + "; ".join(r["title"][:70] for r in risk[:3]) + ".")
    pol = (ctx.get("hooks") or {}).get("angle_policy") or {}
    if pol.get("prefer"):
        lines.append("Angles that fit: " + ", ".join(pol["prefer"]) + ".")
    if pol.get("block"):
        lines.append("Angles REFUSED: " + ", ".join(pol["block"]) + ".")
    ci = ctx.get("competitors") or {}
    if ci.get("surges") or ci.get("actions"):
        lines.append("Competitive: " + "; ".join(f"{a['type']} {a['symbol']}" for a in (ci.get("actions") or [])[:4]) + (f"; {len(ci.get('surges') or [])} surge(s) at tracked venues" if ci.get("surges") else "") + " (internal).")
    w3 = ctx.get("web3") or {}
    if w3.get("trending"):
        lines.append("Web3 trending (unverified, quality-gated): " + "; ".join(f"{c}: " + ", ".join(r["symbol"] for r in rows[:3]) for c, rows in (w3.get("by_chain") or {}).items() if rows) + ".")
    nl = (ctx.get("listings") or {}).get("new") or []
    if nl:
        lines.append("New listings: " + ", ".join(f"{n['symbol']} ({n['venue']} {n['product']})" for n in nl[:6]) + ".")
    oi = ctx.get("oi_movers") or {}
    if oi.get("surge") or oi.get("drop"):
        lines.append("Open interest: " + "; ".join(f"{o['symbol']} OI {o['oi_chg_pct']:+.0f}% / px {o['price_chg_pct']:+.1f}%" for o in (oi.get("surge") or [])[:3] + (oi.get("drop") or [])[:2]) + ".")
    if ctx.get("errors"):
        lines.append("Data gaps: " + "; ".join(ctx["errors"][:4]) + ".")
    return "\n".join(lines)


def market_news(category: Optional[str] = None, query: Optional[str] = None, limit: int = 15) -> Dict[str, Any]:
    if query:
        return {"query": query, "items": news_mod.search(query, limit)}
    return news_mod.headlines(category=category, limit_per_feed=limit)


def campaign_hooks(force: bool = False) -> Dict[str, Any]:
    return market_context(force=force)["hooks"]
