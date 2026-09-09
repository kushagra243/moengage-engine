"""
Derivatives-grade signals on top of the exchange-native universe:
  * new listings — first time a symbol/venue pair appears on Binance spot or a
    Hyperliquid dex (main or builder). Persisted in market_listings so the first
    run seeds silently and later runs report genuinely new pairs.
  * open-interest movers — OI change vs the last snapshot at least ~20h old,
    quoted together with the price change so crowding is read honestly.
Both feed campaign hooks (new_listing, oi_surge/oi_drop) and the narrative.
"""
from __future__ import annotations
import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..database import get_db

MIN_OI_USD = 2_000_000
SURGE_PCT = 30.0
DROP_PCT = -25.0


def init_tables() -> None:
    conn = get_db()
    conn.execute("""CREATE TABLE IF NOT EXISTS market_listings (
        symbol TEXT, venue TEXT, asset_class TEXT, product TEXT, first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP, last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (symbol, venue))""")
    conn.commit(); conn.close()


def _rows(uni: Dict[str, Any]) -> List[Dict[str, str]]:
    out = []
    for r in uni.get("crypto") or []:
        if r.get("on_binance"):
            out.append({"symbol": r["symbol"], "venue": "binance", "asset_class": "crypto", "product": "spot"})
        if r.get("on_hyperliquid"):
            out.append({"symbol": r["symbol"], "venue": "hyperliquid", "asset_class": "crypto", "product": "perp"})
        if not r.get("on_binance") and not r.get("on_hyperliquid"):
            out.append({"symbol": r["symbol"], "venue": r.get("venue") or "unknown", "asset_class": "crypto", "product": r.get("product") or "spot"})
    for cls in ("equities", "indices", "commodities", "fx"):
        for r in uni.get(cls) or []:
            out.append({"symbol": r.get("symbol") or r.get("name"), "venue": r.get("venue") or "hyperliquid", "asset_class": r.get("asset_class") or cls.rstrip("s"), "product": "perp"})
    seen = set(); dedup = []
    for r in out:
        k = (r["symbol"], r["venue"])
        if r["symbol"] and k not in seen:
            seen.add(k); dedup.append(r)
    return dedup


def track_listings(uni: Dict[str, Any], now: Optional[datetime] = None) -> Dict[str, Any]:
    """Upsert every listed pair; return the ones never seen before (empty on the seeding run)."""
    init_tables()
    rows = _rows(uni)
    if not rows:
        return {"new": [], "tracked": 0, "seeded": False}
    conn = get_db()
    existing = {(r["symbol"], r["venue"]) for r in conn.execute("SELECT symbol, venue FROM market_listings").fetchall()}
    seeding = len(existing) == 0
    ts = (now or datetime.utcnow()).strftime("%Y-%m-%d %H:%M:%S")
    new = []
    for r in rows:
        k = (r["symbol"], r["venue"])
        if k in existing:
            conn.execute("UPDATE market_listings SET last_seen=? WHERE symbol=? AND venue=?", (ts, r["symbol"], r["venue"]))
        else:
            conn.execute("INSERT OR IGNORE INTO market_listings (symbol, venue, asset_class, product, first_seen, last_seen) VALUES (?,?,?,?,?,?)",
                         (r["symbol"], r["venue"], r["asset_class"], r["product"], ts, ts))
            if not seeding:
                new.append(r)
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM market_listings").fetchone()[0]
    conn.close()
    return {"new": new[:20], "tracked": total, "seeded": seeding}


def recent_listings(days: int = 7) -> List[Dict[str, Any]]:
    init_tables()
    conn = get_db()
    rows = [dict(r) for r in conn.execute("SELECT * FROM market_listings WHERE first_seen >= datetime('now', ?) AND first_seen <> (SELECT MIN(first_seen) FROM market_listings) ORDER BY first_seen DESC LIMIT 50", (f"-{int(days)} days",)).fetchall()]
    conn.close()
    return rows


def _oi_map(ctx: Dict[str, Any]) -> Dict[str, Dict[str, float]]:
    m: Dict[str, Dict[str, float]] = {}
    for key in ("crypto_markets", "equities", "indices", "commodities"):
        for r in ctx.get(key) or []:
            if r.get("oi_usd") and r.get("price"):
                m[str(r.get("symbol") or r.get("name"))] = {"oi": float(r["oi_usd"]), "price": float(r["price"])}
    return m


def previous_snapshot(min_age_hours: float = 20.0) -> Optional[Dict[str, Any]]:
    conn = get_db()
    try:
        r = conn.execute("SELECT snapshot_json FROM market_snapshots WHERE created_at <= datetime('now', ?) ORDER BY id DESC LIMIT 1", (f"-{int(min_age_hours * 60)} minutes",)).fetchone()
    except Exception:
        r = None
    conn.close()
    if not r:
        return None
    try:
        return json.loads(r["snapshot_json"])
    except Exception:
        return None


def oi_changes(current: Dict[str, Any], previous: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """OI % change vs the previous snapshot for liquid perps, with the price change alongside."""
    if not previous:
        return {"surge": [], "drop": [], "compared_to": None}
    cur, prev = _oi_map(current), _oi_map(previous)
    out = []
    for sym, c in cur.items():
        p = prev.get(sym)
        if not p or p["oi"] < MIN_OI_USD or c["oi"] < MIN_OI_USD:
            continue
        oi_pct = (c["oi"] / p["oi"] - 1) * 100
        px_pct = (c["price"] / p["price"] - 1) * 100 if p["price"] else 0.0
        out.append({"symbol": sym, "oi_usd": round(c["oi"]), "oi_chg_pct": round(oi_pct, 1), "price_chg_pct": round(px_pct, 2),
                    "reading": ("crowding: leverage building while price is flat" if oi_pct >= SURGE_PCT and abs(px_pct) < 3 else
                                "trend with leverage: OI and price moving together" if oi_pct >= SURGE_PCT else
                                "flush: leverage leaving after the move" if oi_pct <= DROP_PCT else "normal")})
    surge = sorted([o for o in out if o["oi_chg_pct"] >= SURGE_PCT], key=lambda o: -o["oi_chg_pct"])[:6]
    drop = sorted([o for o in out if o["oi_chg_pct"] <= DROP_PCT], key=lambda o: o["oi_chg_pct"])[:6]
    return {"surge": surge, "drop": drop, "compared_to": previous.get("generated_at"), "pairs_compared": len(out)}
