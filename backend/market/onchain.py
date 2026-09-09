"""
Web3 lane: trending on-chain tokens for the chains our Web3 product supports
(Solana, Base, BNB Chain, Ethereum, Robinhood Chain, …), from GeckoTerminal
trending pools and DexScreener token profiles/boosts. Public, keyless APIs.

Rules baked in:
  * strict quality gate — liquidity, 24h volume and pool age minimums; paid
    "boosts" are listed separately and never treated as organic trend;
  * every row is labelled unverified: on-chain tokens can be scams; nothing
    here is a recommendation and the hooks only allow education / watchlist;
  * universe = tokens tradable on chains in `web3_chains` (setting).
"""
from __future__ import annotations
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ..database import get_setting
from ..security import redact
from . import sources

GT_NETWORKS = {"solana": "solana", "base": "base", "bsc": "bsc", "bnb": "bsc", "eth": "eth", "ethereum": "eth", "arbitrum": "arbitrum", "polygon": "polygon_pos", "robinhood": None}
DS_CHAIN_IDS = {"solana": "solana", "base": "base", "bsc": "bsc", "bnb": "bsc", "eth": "ethereum", "ethereum": "ethereum", "arbitrum": "arbitrum", "polygon": "polygon", "robinhood": "robinhood"}
MIN_LIQ_USD = 150_000
MIN_VOL_USD = 300_000
MIN_AGE_H = 24
MAJORS = {"USDT", "USDC", "USDE", "DAI", "FDUSD", "TUSD", "USD1", "PYUSD", "WETH", "ETH", "WBTC", "CBBTC", "BTC", "SOL", "WSOL", "BNB", "WBNB", "WMATIC", "POL", "STETH", "WSTETH", "USDS", "SUSDE", "BTCB", "ETHB", "WAVAX", "WPOL", "CBETH", "WEETH"}


def chains() -> List[str]:
    raw = get_setting("web3_chains", "solana,base,bsc,eth,robinhood")
    return [c.strip().lower() for c in raw.split(",") if c.strip()]


def enabled() -> bool:
    return get_setting("web3_enabled", "true").lower() == "true"


def _age_hours(iso: Optional[str]) -> Optional[float]:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        return round((datetime.now(timezone.utc) - dt).total_seconds() / 3600, 1)
    except Exception:
        return None


def _gt_trending(network: str) -> List[Dict[str, Any]]:
    d = sources._json(f"https://api.geckoterminal.com/api/v2/networks/{network}/trending_pools", {"page": 1}, 15.0) or {}
    out = []
    for pool in (d.get("data") or [])[:20]:
        a = pool.get("attributes") or {}
        name = str(a.get("name") or "")
        base = name.split(" / ")[0].strip() if " / " in name else name
        if base.upper() in MAJORS:
            continue                      # stable / wrapped bases are plumbing, not trends
        try:
            out.append({"chain": network, "symbol": base[:16], "pair": name[:40], "pool": a.get("address"), "price_usd": float(a.get("base_token_price_usd") or 0),
                        "chg_24h": float(((a.get("price_change_percentage") or {}).get("h24")) or 0), "vol_24h_usd": float(((a.get("volume_usd") or {}).get("h24")) or 0),
                        "liquidity_usd": float(a.get("reserve_in_usd") or 0), "age_h": _age_hours(a.get("pool_created_at")), "source": "geckoterminal:trending",
                        "buys_24h": ((a.get("transactions") or {}).get("h24") or {}).get("buys"), "sells_24h": ((a.get("transactions") or {}).get("h24") or {}).get("sells")})
        except Exception:
            continue
    return out


def _ds_boosts() -> List[Dict[str, Any]]:
    rows = sources._json("https://api.dexscreener.com/token-boosts/top/v1", None, 15.0) or []
    return [{"chain": r.get("chainId"), "token": r.get("tokenAddress"), "description": (r.get("description") or "")[:80], "boost": r.get("totalAmount") or r.get("amount"), "source": "dexscreener:boost (paid promotion)"} for r in rows if isinstance(r, dict)]


def _ds_profiles() -> List[Dict[str, Any]]:
    rows = sources._json("https://api.dexscreener.com/token-profiles/latest/v1", None, 15.0) or []
    return [{"chain": r.get("chainId"), "token": r.get("tokenAddress"), "description": (r.get("description") or "")[:80], "source": "dexscreener:profile"} for r in rows if isinstance(r, dict)]


def _quality(row: Dict[str, Any]) -> List[str]:
    flags = []
    if (row.get("liquidity_usd") or 0) < MIN_LIQ_USD:
        flags.append("low_liquidity")
    if (row.get("vol_24h_usd") or 0) < MIN_VOL_USD:
        flags.append("low_volume")
    if row.get("age_h") is not None and row["age_h"] < MIN_AGE_H:
        flags.append("new_pool")
    b, s = row.get("buys_24h") or 0, row.get("sells_24h") or 0
    if b + s > 200 and s and b / max(s, 1) > 6:
        flags.append("one_sided_flow")
    if abs(row.get("chg_24h") or 0) > 150:
        flags.append("extreme_move")
    return flags


def trending(force: bool = False) -> Dict[str, Any]:
    if not enabled():
        return {"enabled": False, "chains": [], "trending": [], "boosted": [], "note": "web3 lane disabled (web3_enabled)"}
    def fetch():
        out: Dict[str, Any] = {"enabled": True, "chains": chains(), "trending": [], "rejected": [], "boosted": [], "profiles": [], "errors": [], "fetched_at": time.time(),
                               "disclaimer": "On-chain tokens are unverified and can be scams or lose all value; this list is market data for internal analysis, never a recommendation."}
        for ch in chains():
            net = GT_NETWORKS.get(ch)
            if not net:
                continue
            try:
                for r in _gt_trending(net):
                    r["chain"] = ch; r["flags"] = _quality(r)
                    (out["trending"] if not r["flags"] else out["rejected"]).append(r)
            except Exception as e:
                out["errors"].append(f"{ch}: {redact(str(e))[:80]}")
        want = {DS_CHAIN_IDS.get(c, c) for c in chains()}
        try:
            out["boosted"] = [b for b in _ds_boosts() if b.get("chain") in want][:15]
        except Exception as e:
            out["errors"].append("dexscreener boosts: " + redact(str(e))[:80])
        try:
            out["profiles"] = [p for p in _ds_profiles() if p.get("chain") in want][:15]
        except Exception as e:
            out["errors"].append("dexscreener profiles: " + redact(str(e))[:80])
        out["trending"].sort(key=lambda r: -(r.get("vol_24h_usd") or 0))
        out["trending"] = out["trending"][:25]; out["rejected"] = out["rejected"][:25]
        out["by_chain"] = {c: [r for r in out["trending"] if r["chain"] == c][:6] for c in chains()}
        return out
    if force:
        sources._MEM.pop("web3_trending", None)
        try:
            import os
            os.remove(os.path.join(sources.CACHE_DIR, "web3_trending.json"))
        except Exception:
            pass
    return sources.cached("web3_trending", fetch, 600) or {"enabled": True, "trending": [], "boosted": [], "chains": chains()}
