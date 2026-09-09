import json
from backend.database import get_db


def _uni(extra=None):
    crypto = [{"symbol": "BTC", "on_binance": True, "on_hyperliquid": True}, {"symbol": "ETH", "on_binance": True, "on_hyperliquid": True}]
    if extra:
        crypto.append(extra)
    return {"crypto": crypto, "equities": [{"symbol": "NVDA", "venue": "hyperliquid:xyz", "asset_class": "equity"}], "indices": [], "commodities": [], "fx": []}


def test_listing_detection_seeds_then_reports_new():
    from backend.market import derivs
    conn = get_db(); conn.execute("DROP TABLE IF EXISTS market_listings"); conn.commit(); conn.close()
    first = derivs.track_listings(_uni())
    assert first["seeded"] and first["new"] == [] and first["tracked"] == 5
    second = derivs.track_listings(_uni({"symbol": "PENDLE", "on_binance": True, "on_hyperliquid": False}))
    assert [n["symbol"] for n in second["new"]] == ["PENDLE"] and second["new"][0]["venue"] == "binance"
    third = derivs.track_listings(_uni({"symbol": "PENDLE", "on_binance": True, "on_hyperliquid": False}))
    assert third["new"] == []


def test_oi_changes_reads_crowding_and_flush():
    from backend.market import derivs
    prev = {"generated_at": "t0", "crypto_markets": [{"symbol": "ETH", "oi_usd": 10_000_000, "price": 100}, {"symbol": "SOL", "oi_usd": 8_000_000, "price": 50}, {"symbol": "TINY", "oi_usd": 100_000, "price": 1}]}
    cur = {"crypto_markets": [{"symbol": "ETH", "oi_usd": 14_000_000, "price": 101}, {"symbol": "SOL", "oi_usd": 5_000_000, "price": 40}, {"symbol": "TINY", "oi_usd": 900_000, "price": 3}]}
    r = derivs.oi_changes(cur, prev)
    assert r["surge"][0]["symbol"] == "ETH" and "crowding" in r["surge"][0]["reading"]
    assert r["drop"][0]["symbol"] == "SOL" and "flush" in r["drop"][0]["reading"]
    assert all(o["symbol"] != "TINY" for o in r["surge"] + r["drop"])
    assert derivs.oi_changes(cur, None)["surge"] == []


def test_hooks_include_listing_and_oi_and_respect_policy():
    from backend.market.hooks import build_hooks
    ctx = {"crypto": {"regime": {"label": "trending_up"}}, "listings": {"new": [{"symbol": "PENDLE", "venue": "binance", "product": "spot", "asset_class": "crypto"}]},
           "oi_movers": {"surge": [{"symbol": "ETH", "oi_chg_pct": 40.0, "price_chg_pct": 1.0, "reading": "crowding"}], "drop": []}}
    h = build_hooks(ctx)
    ids = [x["id"] for x in h["hooks"]]
    assert "listing_binance_PENDLE" in ids and "oi_surge_ETH" in ids
    ctx["crypto"]["regime"]["label"] = "capitulation"
    h2 = build_hooks(ctx)
    assert "listing_binance_PENDLE" in h2["blocked_hook_ids"] and "oi_surge_ETH" in [x["id"] for x in h2["hooks"]]


def test_marketing_skills_installed_and_searchable():
    from backend.skills import list_skills
    names = {s["name"] for s in list_skills()}
    for n in ("crypto-derivatives-marketing", "crypto-compliance-copy", "trading-event-taxonomy", "crypto-copywriting", "trader-analytics-playbook", "crypto-growth-calendar"):
        assert n in names
    from backend.llm.tools import moengage_guidance
    g = moengage_guidance("liquidation recovery funding")
    assert any(m["doc"].startswith("skill:") for m in g["matches"])
    from backend.taxonomy import classify
    t = classify({"name": "GMC_Sep26_HFT_PERP_Liquidated_Recovery_9Sep26"})
    assert "Perpetuals" in t["facets"].get("product", []) and t["cohort"] == "Liquidation recovery"
    from backend import hacks
    assert any(h["id"] == "liquidation_recovery_flow" for h in hacks.LIBRARY)
