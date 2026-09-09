"""
Competitive intelligence (internal only — never named in user copy).

Public, keyless tickers from the venues CoinDCX competes with for Indian
volume, plus our own ticker, normalised to one shape per pair:
  exchange · symbol (base) · product (spot / perp / option) · quote · vol_24h_usd · oi_usd · funding · chg_24h
Sources: Delta Exchange India (perps + options), WazirX (INR spot), Bybit (global
perps reference), CoinGecko exchange pages (CoinDCX, Delta futures/spot, Mudrex,
ZebPay, Bitbns, Giottus, KoinBX, WazirX — top tickers + 24h BTC volume), and
api.coindcx.com (our own spot markets). Our perps volume comes from the
Hyperliquid-backed universe already in the market snapshot.

Each refresh is persisted (competitor_snapshots) so we can compute:
  * share of volume per pair (us vs each competitor) and pair battles,
  * volume surges at a competitor vs the previous snapshot (≥ surge_pct and ≥ min USD),
  * pairs a competitor lists that we do not (listing gaps) and vice versa (our edges),
  * funding-rate edges on shared perps,
  * ranked actions with an owner (marketing / product / liquidity) and the SOP to run.
"""
from __future__ import annotations
import json
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..database import get_db, get_setting
from ..security import redact
from . import sources

COMPETITORS: Dict[str, Dict[str, Any]] = {
    "delta":     {"name": "Delta Exchange India", "kind": "INR perps + options", "direct": "delta_india", "cmc": "delta-exchange", "cg": "delta_futures"},
    "wazirx":    {"name": "WazirX", "kind": "INR spot", "direct": "wazirx", "cmc": "wazirx", "cg": "wazirx"},
    "mudrex":    {"name": "Mudrex", "kind": "INR spot", "cg": "mudrex"},
    "zebpay":    {"name": "ZebPay", "kind": "INR spot", "cmc": "zebpay", "cg": "zebpay"},
    "bitbns":    {"name": "Bitbns", "kind": "INR spot", "cmc": "bitbns", "cg": "bitbns"},
    "giottus":   {"name": "Giottus", "kind": "INR spot", "cmc": "giottus", "cg": "giottus"},
    "koinbx":    {"name": "KoinBX", "kind": "INR spot", "cmc": "koinbx", "cg": "koinbazar"},
    "unocoin":   {"name": "Unocoin", "kind": "INR spot", "cmc": "unocoin"},
    "bybit":     {"name": "Bybit", "kind": "global perps (reference)", "direct": "bybit", "cmc": "bybit"},
}
OWN_CMC_SLUG = "coindcx"
CMC = "https://api.coinmarketcap.com/data-api/v3/exchange"
CMC_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; moengage-engine intelligence)", "Accept": "application/json"}
STABLES = {"USDT", "USDC", "INR", "USD", "FDUSD", "DAI", "TUSD", "BUSD"}
SYN = {"XAU": "GOLD", "XAUT": "GOLD", "PAXG": "GOLD", "XAG": "SILVER", "XPT": "PLATINUM", "XPD": "PALLADIUM", "USOIL": "CL", "WTI": "CL", "UKOIL": "BRENTOIL", "NGAS": "NATGAS", "SPX500": "SP500", "SPX": "SP500", "US500": "SP500", "NAS100": "NASDAQ100", "NDX": "NASDAQ100", "US30": "DOW", "1000PEPE": "PEPE", "1000SHIB": "SHIB", "1000BONK": "BONK", "1000FLOKI": "FLOKI", "KPEPE": "PEPE", "KSHIB": "SHIB", "KBONK": "BONK", "KFLOKI": "FLOKI", "PUMPFUN": "PUMP", "RAYDIUM": "RAY", "SKHYNIX": "SKHX", "HYPERLIQUID": "HYPE", "1000RATS": "RATS", "1000SATS": "SATS", "1000CAT": "CAT", "1000X": "X"}


def canon(sym: str) -> str:
    u = str(sym or "").upper()
    return SYN.get(u, u)


def init_tables() -> None:
    conn = get_db()
    conn.execute("""CREATE TABLE IF NOT EXISTS competitor_snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, exchange TEXT, pairs_json TEXT, total_vol_usd REAL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS competitor_pairs (exchange TEXT, symbol TEXT, product TEXT, first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP, last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (exchange, symbol, product))""")
    conn.commit(); conn.close()


def enabled_ids() -> List[str]:
    raw = get_setting("competitors", "delta,wazirx,mudrex,zebpay,bitbns,giottus,koinbx,bybit")
    return [c.strip().lower() for c in raw.split(",") if c.strip() and c.strip().lower() in COMPETITORS]


def _inr_usd() -> float:
    try:
        d = sources._json("https://open.er-api.com/v6/latest/USD", None, 10.0) or {}
        v = float((d.get("rates") or {}).get("INR") or 0)
        return v or 88.0
    except Exception:
        return 88.0


def _f(x, default=0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


# ── fetchers → list of pair rows ──────────────────────────────────────────────
def fetch_delta_india() -> List[Dict[str, Any]]:
    d = sources._json("https://api.india.delta.exchange/v2/tickers", None, 25.0) or {}
    out = []
    for r in d.get("result") or []:
        ct = r.get("contract_type") or ""
        if ct not in ("perpetual_futures", "spot", "call_options", "put_options"):
            continue
        product = "perp" if ct == "perpetual_futures" else ("option" if "options" in ct else "spot")
        out.append({"symbol": str(r.get("underlying_asset_symbol") or r.get("symbol") or "").upper(), "product": product, "quote": str(r.get("turnover_symbol") or "USD"),
                    "vol_24h_usd": _f(r.get("turnover_usd")), "oi_usd": _f(r.get("oi_value_usd")), "funding_1h_pct": _f(r.get("funding_rate")) if product == "perp" else None,
                    "chg_24h": _f(r.get("mark_change_24h")), "price": _f(r.get("close"))})
    return out


def fetch_wazirx(inr: float) -> List[Dict[str, Any]]:
    rows = sources._json("https://api.wazirx.com/sapi/v1/tickers/24hr", None, 20.0) or []
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        base, quote = str(r.get("baseAsset") or "").upper(), str(r.get("quoteAsset") or "").upper()
        vol_base = _f(r.get("volume")); last = _f(r.get("lastPrice"))
        vol_quote = vol_base * last
        usd = vol_quote / inr if quote == "INR" else vol_quote
        out.append({"symbol": base, "product": "spot", "quote": quote, "vol_24h_usd": usd, "oi_usd": None, "funding_1h_pct": None, "chg_24h": round((last / _f(r.get("openPrice"), last) - 1) * 100, 2) if _f(r.get("openPrice")) else 0.0, "price": last})
    return out


def fetch_bybit() -> List[Dict[str, Any]]:
    d = sources._json("https://api.bybit.com/v5/market/tickers", {"category": "linear"}, 20.0) or {}
    out = []
    for r in ((d.get("result") or {}).get("list") or []):
        sym = str(r.get("symbol") or "")
        if not sym.endswith("USDT"):
            continue
        out.append({"symbol": sym[:-4].upper(), "product": "perp", "quote": "USDT", "vol_24h_usd": _f(r.get("turnover24h")), "oi_usd": _f(r.get("openInterestValue")),
                    "funding_1h_pct": _f(r.get("fundingRate")) * 100 / 8, "chg_24h": _f(r.get("price24hPcnt")) * 100, "price": _f(r.get("lastPrice"))})
    return out


def fetch_coindcx(inr: float) -> List[Dict[str, Any]]:
    rows = sources._json("https://api.coindcx.com/exchange/ticker", None, 20.0) or []
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        m = str(r.get("market") or "")
        quote = next((q for q in ("USDT", "INR", "USDC", "BTC", "ETH") if m.endswith(q)), None)
        if not quote:
            continue
        base = m[: -len(quote)].upper()
        vq = _f(r.get("volume")); last = _f(r.get("last_price"))      # CoinDCX reports 24h volume in the quote currency
        usd = vq / inr if quote == "INR" else vq
        out.append({"symbol": base, "product": "spot", "quote": quote, "vol_24h_usd": usd, "oi_usd": None, "funding_1h_pct": None, "chg_24h": _f(r.get("change_24_hour")), "price": last, "inr_market": quote == "INR"})
    return out


def fetch_cmc_pairs(slug: str, category: str = "spot", limit: int = 300) -> List[Dict[str, Any]]:
    """CoinMarketCap public data-api: market pairs of one exchange with USD volume (spot | perpetual | futures)."""
    d = sources._json(f"{CMC}/market-pairs/latest", {"slug": slug, "category": category, "start": 1, "limit": limit}, 20.0, headers=CMC_HEADERS) or {}
    out = []
    for mp in ((d.get("data") or {}).get("marketPairs") or []):
        base = str(mp.get("baseSymbol") or "").upper(); quote = str(mp.get("quoteSymbol") or "").upper()
        if not base:
            continue
        out.append({"symbol": base, "product": "perp" if category in ("perpetual", "futures") else "spot", "quote": quote, "vol_24h_usd": _f(mp.get("volumeUsd")), "oi_usd": None, "funding_1h_pct": None,
                    "chg_24h": None, "price": _f(mp.get("price")), "inr_market": quote == "INR", "excluded": bool(mp.get("volumeExcluded") or mp.get("outlierDetected")), "market_score": _f(mp.get("marketScore"))})
    return out


def fetch_cmc_listing() -> Dict[str, Dict[str, Any]]:
    """Exchange-level facts from CMC: 24h spot / derivatives / total volume, market share, 24h change, fees, traffic — keyed by slug."""
    out: Dict[str, Dict[str, Any]] = {}
    for cat in ("spot", "derivatives"):
        d = sources._json(f"{CMC}/listing", {"start": 1, "limit": 500, "sort": "volume_24h", "category": cat}, 25.0, headers=CMC_HEADERS) or {}
        for e in ((d.get("data") or {}).get("exchanges") or []):
            slug = e.get("slug")
            if not slug:
                continue
            row = out.setdefault(slug, {"name": e.get("name")})
            row.update({k: e.get(k) for k in ("spotVol24h", "derivativesVol24h", "totalVol24h", "totalVolAdjusted24h", "totalVolChgPct24h", "totalVolChgPct7d", "marketSharePct", "makerFee", "takerFee", "visits", "trafficScore", "numMarkets", "numCoins", "countries", "score") if e.get(k) is not None})
    return out


def fetch_own(inr: float) -> List[Dict[str, Any]]:
    """Our own spot markets. Default source is CoinMarketCap (the team's rule: CoinDCX volume is taken from CMC, not shared directly); api.coindcx.com only if competitor_own_source=coindcx_api."""
    if get_setting("competitor_own_source", "cmc") == "coindcx_api":
        return fetch_coindcx(inr)
    rows = fetch_cmc_pairs(OWN_CMC_SLUG, "spot", 400)
    try:
        rows += fetch_cmc_pairs(OWN_CMC_SLUG, "perpetual", 200)
    except Exception:
        pass
    return rows


def fetch_coingecko_exchange(cg_id: str) -> Dict[str, Any]:
    d = sources._json(f"https://api.coingecko.com/api/v3/exchanges/{cg_id}", None, 20.0, headers=sources._cg_headers()) or {}
    rows = []
    for t in d.get("tickers") or []:
        base = str(t.get("base") or "").upper(); target = str(t.get("target") or "").upper()
        if base in STABLES and target in STABLES:
            continue
        rows.append({"symbol": base, "product": "perp" if "futures" in cg_id else "spot", "quote": target, "vol_24h_usd": _f((t.get("converted_volume") or {}).get("usd")), "oi_usd": None, "funding_1h_pct": None, "chg_24h": None, "price": _f(t.get("last"))})
    btc_vol = _f(d.get("trade_volume_24h_btc"))
    return {"pairs": rows, "total_vol_btc": btc_vol, "trust_score": d.get("trust_score"), "name": d.get("name")}


def _btc_usd(ctx_rows: List[Dict[str, Any]]) -> float:
    for r in ctx_rows or []:
        if r.get("symbol") == "BTC" and r.get("price"):
            return float(r["price"])
    try:
        d = sources._json("https://api.coingecko.com/api/v3/simple/price", {"ids": "bitcoin", "vs_currencies": "usd"}, 10.0, headers=sources._cg_headers()) or {}
        return float((d.get("bitcoin") or {}).get("usd") or 0) or 80000.0
    except Exception:
        return 80000.0


# ── snapshot ──────────────────────────────────────────────────────────────────
def snapshot(universe_ctx: Optional[Dict[str, Any]] = None, force: bool = False) -> Dict[str, Any]:
    """Fetch every enabled competitor + our own markets; persist; return the normalised picture."""
    def fetch():
        init_tables()
        inr = _inr_usd(); btc = _btc_usd((universe_ctx or {}).get("crypto_markets") or [])
        ex: Dict[str, Dict[str, Any]] = {}
        errors: List[str] = []
        # us
        try:
            ours = fetch_own(inr)
        except Exception as e:
            ours = []; errors.append("coindcx (cmc): " + redact(str(e))[:80])
        try:
            listing = fetch_cmc_listing()
        except Exception as e:
            listing = {}; errors.append("cmc listing: " + redact(str(e))[:80])
        our_perps: List[Dict[str, Any]] = []
        try:
            from .exchanges import hyperliquid_perps
            hl = hyperliquid_perps()
            for sym, r in {**(hl.get("crypto") or {}), **(hl.get("rwa") or {})}.items():
                our_perps.append({"symbol": canon(r.get("name") or sym), "product": "perp", "quote": "USDC", "vol_24h_usd": _f(r.get("vol_24h_usd")), "oi_usd": _f(r.get("oi_usd")), "funding_1h_pct": r.get("funding_1h_pct"),
                                  "chg_24h": r.get("chg_24h"), "price": r.get("price"), "reference": True})
        except Exception as e:
            errors.append("our perps (liquidity venue): " + redact(str(e))[:80])
        for p in ours:
            p["symbol"] = canon(p["symbol"])
        inr_spot = sum(p["vol_24h_usd"] or 0 for p in ours if p.get("inr_market"))
        usdt_spot = sum(p["vol_24h_usd"] or 0 for p in ours if not p.get("inr_market"))
        own_src = "coinmarketcap" if get_setting("competitor_own_source", "cmc") != "coindcx_api" else "api.coindcx.com"
        ex["coindcx"] = {"name": "CoinDCX (us)", "kind": "INR spot (own) · USDT spot (reported) · perps (liquidity-venue reference)", "pairs": ours + our_perps, "total_vol_usd": inr_spot,
                         "inr_spot_vol_usd": inr_spot, "usdt_spot_vol_usd_reported": usdt_spot, "perps_reference_vol_usd": sum(p["vol_24h_usd"] or 0 for p in our_perps), "source": f"{own_src} + liquidity venue", "cmc": listing.get(OWN_CMC_SLUG) or {}}
        for cid in enabled_ids():
            meta = COMPETITORS[cid]
            rows: List[Dict[str, Any]] = []; src = ""
            try:
                if meta.get("direct") == "delta_india":
                    rows = fetch_delta_india(); src = "api.india.delta.exchange"
                elif meta.get("direct") == "wazirx":
                    rows = fetch_wazirx(inr); src = "api.wazirx.com"
                elif meta.get("direct") == "bybit":
                    rows = fetch_bybit(); src = "api.bybit.com"
            except Exception as e:
                errors.append(f"{cid}: {redact(str(e))[:80]}")
            if (not rows) and meta.get("cmc"):
                try:
                    rows = fetch_cmc_pairs(meta["cmc"], "spot", 300)
                    if "perps" in meta["kind"] or cid == "bybit":
                        rows += fetch_cmc_pairs(meta["cmc"], "perpetual", 200)
                    src = f"coinmarketcap:{meta['cmc']}"
                except Exception as e:
                    errors.append(f"{cid} (cmc): {redact(str(e))[:80]}")
            for p in rows:
                p["symbol"] = canon(p["symbol"])
            total = sum(p["vol_24h_usd"] or 0 for p in rows)
            if (not rows or total <= 0) and meta.get("cg"):
                try:
                    cg = fetch_coingecko_exchange(meta["cg"]); rows = cg["pairs"]; total = (cg["total_vol_btc"] or 0) * btc or sum(p["vol_24h_usd"] or 0 for p in rows); src = f"coingecko:{meta['cg']}"
                except Exception as e:
                    errors.append(f"{cid} (coingecko): {redact(str(e))[:80]}")
            ex[cid] = {"name": meta["name"], "kind": meta["kind"], "pairs": rows, "total_vol_usd": total, "source": src, "cmc": listing.get(meta.get("cmc") or "") or {}}
        conn = get_db()
        ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        for cid, e in ex.items():
            slim = [{k: p.get(k) for k in ("symbol", "product", "quote", "vol_24h_usd", "oi_usd", "funding_1h_pct", "chg_24h")} for p in sorted(e["pairs"], key=lambda p: -(p.get("vol_24h_usd") or 0))[:400]]
            conn.execute("INSERT INTO competitor_snapshots (exchange, pairs_json, total_vol_usd) VALUES (?,?,?)", (cid, json.dumps(slim), e["total_vol_usd"]))
            for p in slim:
                conn.execute("INSERT INTO competitor_pairs (exchange, symbol, product, first_seen, last_seen) VALUES (?,?,?,?,?) ON CONFLICT(exchange, symbol, product) DO UPDATE SET last_seen=excluded.last_seen", (cid, p["symbol"], p["product"], ts, ts))
        conn.execute("DELETE FROM competitor_snapshots WHERE created_at < datetime('now', '-30 days')")
        conn.commit(); conn.close()
        return {"fetched_at": time.time(), "inr_per_usd": inr, "exchanges": {k: {**v, "pairs": sorted(v["pairs"], key=lambda p: -(p.get("vol_24h_usd") or 0))[:300], "pair_count": len(v["pairs"])} for k, v in ex.items()}, "errors": errors}
    if force:
        sources._MEM.pop("competitors", None)
        try:
            import os; os.remove(os.path.join(sources.CACHE_DIR, "competitors.json"))
        except Exception:
            pass
    return sources.cached("competitors", fetch, int(get_setting("competitor_cache_ttl_s", "900") or 900)) or {"exchanges": {}, "errors": ["no data"]}


def _previous(exchange: str, min_age_min: int = 45) -> Optional[Dict[str, Dict[str, Any]]]:
    conn = get_db()
    try:
        r = conn.execute("SELECT pairs_json FROM competitor_snapshots WHERE exchange=? AND created_at <= datetime('now', ?) ORDER BY id DESC LIMIT 1", (exchange, f"-{min_age_min} minutes")).fetchone()
    except Exception:
        r = None
    conn.close()
    if not r:
        return None
    try:
        return {(p["symbol"], p["product"]): p for p in json.loads(r["pairs_json"])}
    except Exception:
        return None


def _first_seen(exchange: str) -> Dict[tuple, str]:
    conn = get_db()
    rows = conn.execute("SELECT symbol, product, first_seen FROM competitor_pairs WHERE exchange=?", (exchange,)).fetchall()
    conn.close()
    return {(r["symbol"], r["product"]): r["first_seen"] for r in rows}


# ── intelligence ──────────────────────────────────────────────────────────────
def intel(universe_ctx: Optional[Dict[str, Any]] = None, force: bool = False) -> Dict[str, Any]:
    snap = snapshot(universe_ctx, force=force)
    ex = snap.get("exchanges") or {}
    surge_pct = float(get_setting("competitor_surge_pct", "50") or 50)
    min_usd = float(get_setting("competitor_surge_min_usd", "250000") or 250000)
    us = ex.get("coindcx") or {"pairs": [], "total_vol_usd": 0}
    our = {}
    for p in us["pairs"]:
        k = (p["symbol"], p["product"]); our[k] = our.get(k, 0.0) + (p.get("vol_24h_usd") or 0)
    our_syms = {k[0] for k in our}
    # exchange table + Indian share
    inr_spot_ids = [cid for cid in ex if cid in ("coindcx", "wazirx", "mudrex", "zebpay", "bitbns", "giottus", "koinbx", "unocoin")]
    total_in = sum((ex[c].get("total_vol_usd") or 0) for c in inr_spot_ids) or 1.0
    def _cmc_facts(e):
        c = e.get("cmc") or {}
        return {"cmc_spot_vol_24h_usd": c.get("spotVol24h"), "cmc_derivatives_vol_24h_usd": c.get("derivativesVol24h"), "cmc_total_vol_24h_usd": c.get("totalVol24h"), "cmc_vol_chg_24h_pct": c.get("totalVolChgPct24h"),
                "cmc_vol_chg_7d_pct": c.get("totalVolChgPct7d"), "cmc_market_share_pct": c.get("marketSharePct"), "maker_fee_pct": c.get("makerFee"), "taker_fee_pct": c.get("takerFee"), "weekly_visits": c.get("visits"), "cmc_score": c.get("score")}
    inr_spot_ids = [cid for cid in inr_spot_ids]
    table = [{"exchange": cid, "name": e["name"], "kind": e["kind"], "vol_24h_usd": round(e.get("total_vol_usd") or 0), "pairs": e.get("pair_count", len(e.get("pairs", []))),
              "share_of_tracked_inr_spot_pct": round((e.get("total_vol_usd") or 0) / total_in * 100, 1) if cid in inr_spot_ids else None, "source": e.get("source"), **_cmc_facts(e),
              **({k: round(e.get(k) or 0) for k in ("inr_spot_vol_usd", "usdt_spot_vol_usd_reported", "perps_reference_vol_usd")} if cid == "coindcx" else {})} for cid, e in ex.items()]
    table.sort(key=lambda r: -r["vol_24h_usd"])
    battles, surges, gaps, edges, funding_edges = [], [], [], [], []
    now_first = {}
    for cid, e in ex.items():
        if cid == "coindcx":
            continue
        prev = _previous(cid); fs = _first_seen(cid)
        for p in e.get("pairs", [])[:300]:
            k = (p["symbol"], p["product"]); v = p.get("vol_24h_usd") or 0
            if p["symbol"] in STABLES:
                continue
            ours_v = our.get(k, 0.0)
            if v >= min_usd or ours_v >= min_usd:
                battles.append({"symbol": p["symbol"], "product": p["product"], "competitor": cid, "their_vol_usd": round(v), "our_vol_usd": round(ours_v), "our_share_pct": round(ours_v / (ours_v + v) * 100, 1) if (ours_v + v) else None})
            if prev is not None:
                pv = (prev.get(k) or {}).get("vol_24h_usd") or 0
                if pv >= 50_000 and v >= min_usd and (v / pv - 1) * 100 >= surge_pct:
                    surges.append({"symbol": p["symbol"], "product": p["product"], "competitor": cid, "vol_now_usd": round(v), "vol_prev_usd": round(pv), "surge_pct": round((v / pv - 1) * 100), "chg_24h": p.get("chg_24h"), "we_list_it": p["symbol"] in our_syms, "our_vol_usd": round(ours_v)})
            if p["symbol"] not in our_syms and v >= min_usd and p["product"] in ("spot", "perp"):
                gaps.append({"symbol": p["symbol"], "product": p["product"], "competitor": cid, "their_vol_usd": round(v), "first_seen_there": fs.get(k)})
            if p["product"] == "perp" and p.get("funding_1h_pct") is not None:
                mine = next((q for q in us["pairs"] if q["symbol"] == p["symbol"] and q["product"] == "perp" and q.get("funding_1h_pct") is not None), None)
                if mine and abs((p["funding_1h_pct"] or 0) - (mine["funding_1h_pct"] or 0)) >= 0.01:
                    funding_edges.append({"symbol": p["symbol"], "competitor": cid, "their_funding_1h_pct": round(p["funding_1h_pct"], 4), "our_funding_1h_pct": round(mine["funding_1h_pct"], 4), "cheaper_for_longs": "us" if mine["funding_1h_pct"] < p["funding_1h_pct"] else "them"})
    # our edges: pairs where we lead every tracked competitor with meaningful volume
    comp_vol: Dict[tuple, float] = {}
    for cid, e in ex.items():
        if cid == "coindcx":
            continue
        for p in e.get("pairs", []):
            k = (p["symbol"], p["product"]); comp_vol[k] = max(comp_vol.get(k, 0.0), p.get("vol_24h_usd") or 0)
    for k, v in our.items():
        if v >= min_usd and v > comp_vol.get(k, 0.0) * 1.5:
            edges.append({"symbol": k[0], "product": k[1], "our_vol_usd": round(v), "best_competitor_vol_usd": round(comp_vol.get(k, 0.0))})
    battles.sort(key=lambda b: -(b["their_vol_usd"] + b["our_vol_usd"])); surges.sort(key=lambda s: -s["surge_pct"]); gaps.sort(key=lambda g: -g["their_vol_usd"]); edges.sort(key=lambda e: -e["our_vol_usd"])
    # de-duplicate gaps per symbol (largest competitor volume)
    seen = set(); gaps2 = []
    for g in gaps:
        if g["symbol"] in seen:
            continue
        seen.add(g["symbol"]); gaps2.append(g)
    actions = _actions(surges[:10], gaps2[:10], battles[:40], funding_edges[:10], edges[:10])
    try:
        mine = next((t for t in table if t["exchange"] == "coindcx"), {})
        if mine.get("taker_fee_pct") is not None:
            cheaper = [t for t in table if t["exchange"] not in ("coindcx", "bybit") and t.get("taker_fee_pct") is not None and float(t["taker_fee_pct"]) < float(mine["taker_fee_pct"])]
            if cheaper:
                actions.append({"priority": 45, "owner": "product+marketing", "type": "fee_position", "symbol": "—", "product": "spot", "what": f"{len(cheaper)} tracked Indian venue(s) list a lower taker fee than ours ({mine['taker_fee_pct']}%): lead with total cost (fee + TDS + spread) transparency and fee tiers rather than headline fee; review tier thresholds.", "sop": "sop_fee_tier_nudge"})
        drops = [t for t in table if t["exchange"] != "coindcx" and t.get("cmc_vol_chg_24h_pct") is not None and float(t["cmc_vol_chg_24h_pct"]) >= 40]
        for t in drops[:2]:
            actions.append({"priority": 55, "owner": "marketing", "type": "venue_volume_jump", "symbol": "—", "product": "all", "what": f"A tracked venue's total volume is up {round(float(t['cmc_vol_chg_24h_pct']))}% in 24h (CMC). Check its surging pairs in pair battles and counter on the ones we list.", "sop": "sop_asset_spotlight"})
        actions.sort(key=lambda a: -a["priority"])
    except Exception:
        pass
    return {"generated_at": snap.get("fetched_at"), "exchanges": table, "pair_battles": battles[:40], "surges": surges[:15], "listing_gaps": gaps2[:15], "our_edges": edges[:15], "funding_edges": funding_edges[:10],
            "actions": actions, "errors": snap.get("errors"), "thresholds": {"surge_pct": surge_pct, "min_usd": min_usd},
            "note": "internal intelligence from free public sources: CoinMarketCap data-api (CoinDCX and venue market pairs, exchange volumes, market share, fees, traffic), direct venue tickers for realtime (Delta India, WazirX, Bybit), CoinGecko fallback. CoinDCX INR markets are our own volume; USDT markets and perps are routed/liquidity-venue volume (reference, not share). CMC excludes outlier volumes. Never name a competitor in user-facing copy."}


def _actions(surges, gaps, battles, funding_edges, edges) -> List[Dict[str, Any]]:
    out = []
    for s in surges[:6]:
        if s["we_list_it"]:
            out.append({"priority": 90, "owner": "marketing", "type": "counter_surge", "symbol": s["symbol"], "product": s["product"],
                        "what": f"{s['symbol']} {s['product']} volume is surging elsewhere (+{s['surge_pct']}% vs earlier today). Run the asset spotlight to our {s['symbol']} watchers/holders now (fact + tool, TTL 4h) and check spreads/liquidity on our book.",
                        "sop": "sop_asset_spotlight", "guardrails": ["regime policy applies", "no venue names", "no direction"]})
        else:
            out.append({"priority": 80, "owner": "product", "type": "listing_request", "symbol": s["symbol"], "product": s["product"], "what": f"{s['symbol']} {s['product']} is surging elsewhere and we do not list it; file a listing/liquidity request with the volume evidence.", "sop": None, "data_request": {"kind": "other", "title": f"List {s['symbol']} ({s['product']})", "why": f"competitor volume ${s['vol_now_usd']:,} in 24h, +{s['surge_pct']}% surge"}})
    for g in gaps[:5]:
        out.append({"priority": 60, "owner": "product", "type": "listing_gap", "symbol": g["symbol"], "product": g["product"], "what": f"{g['symbol']} {g['product']} trades ${g['their_vol_usd']:,}/24h elsewhere and is not on CoinDCX.", "sop": None,
                    "data_request": {"kind": "other", "title": f"List {g['symbol']} ({g['product']})", "why": f"competitor volume ${g['their_vol_usd']:,}/24h; first seen there {g.get('first_seen_there') or 'unknown'}"}})
    weak = [b for b in battles if b["competitor"] != "bybit" and b.get("our_share_pct") is not None and b["our_share_pct"] < 25 and b["their_vol_usd"] >= 1_000_000][:5]   # share is contested with Indian venues; global venues are reference
    for b in weak:
        out.append({"priority": 70, "owner": "liquidity+marketing", "type": "share_defence", "symbol": b["symbol"], "product": b["product"], "what": f"Our share of {b['symbol']} {b['product']} volume vs a tracked competitor is {b['our_share_pct']}% (${b['our_vol_usd']:,} vs ${b['their_vol_usd']:,}). Check spread, maker incentives and run the product-cohort programme for {b['symbol']} traders.", "sop": "sop_product_cohort_monthly"})
    for f in funding_edges[:3]:
        if f["cheaper_for_longs"] == "us":
            out.append({"priority": 50, "owner": "marketing", "type": "funding_edge", "symbol": f["symbol"], "product": "perp", "what": f"Funding on {f['symbol']} perps is cheaper for longs here than at a tracked competitor ({f['our_funding_1h_pct']}%/h vs {f['their_funding_1h_pct']}%/h). Education content on funding costs to habitual perp traders (no venue names).", "sop": "sop_funding_crowding_nudge"})
    for e in edges[:3]:
        out.append({"priority": 40, "owner": "marketing", "type": "press_advantage", "symbol": e["symbol"], "product": e["product"], "what": f"We lead tracked competitors on {e['symbol']} {e['product']} (${e['our_vol_usd']:,} vs ${e['best_competitor_vol_usd']:,}). Keep the depth story: spotlight to sector watchers; fee-tier nudge to near-threshold traders.", "sop": "sop_asset_spotlight"})
    out.sort(key=lambda a: -a["priority"])
    return out[:12]


def pair_battle(symbol: str, universe_ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    snap = snapshot(universe_ctx)
    sym = symbol.upper()
    rows = []
    for cid, e in (snap.get("exchanges") or {}).items():
        for p in e.get("pairs", []):
            if p["symbol"] == sym:
                rows.append({"exchange": cid, "name": e["name"], "product": p["product"], "quote": p.get("quote"), "vol_24h_usd": round(p.get("vol_24h_usd") or 0), "oi_usd": p.get("oi_usd"), "funding_1h_pct": p.get("funding_1h_pct"), "chg_24h": p.get("chg_24h")})
    rows.sort(key=lambda r: -r["vol_24h_usd"])
    total = sum(r["vol_24h_usd"] for r in rows) or 1
    for r in rows:
        r["share_pct"] = round(r["vol_24h_usd"] / total * 100, 1)
    return {"symbol": sym, "venues": rows, "we_list_it": any(r["exchange"] == "coindcx" for r in rows)}
