def test_category_benchmarks_from_fixtures(monkeypatch):
    from backend.market import benchmarks as b, sources
    import os
    monkeypatch.setattr(b, "fetch_cmc_listing", lambda: {"binance": {"spotVol24h": 9.9e9, "derivativesVol24h": 6.2e10, "derivativesOpenInterests": 3.2e10, "numMarkets": 2171, "derivativesMarketPairs": 796, "makerFee": 0.02, "takerFee": 0.04, "visits": 7e6},
                                                        "bybit": {"spotVol24h": 1.7e9, "derivativesVol24h": 1.4e10, "derivativesOpenInterests": 6e9, "numMarkets": 1282, "derivativesMarketPairs": 768, "takerFee": 0.055},
                                                        "coindcx": {"spotVol24h": 6.3e6, "numMarkets": 704, "takerFee": 0.0, "visits": 3.4e5}, "wazirx": {"spotVol24h": 6e5, "numMarkets": 296}, "hyperliquid": {"derivativesVol24h": 8.4e9, "derivativesOpenInterests": 7.5e9}})
    monkeypatch.setattr(b, "fetch_delta_india", lambda: [{"symbol": "BTC", "product": "perp", "vol_24h_usd": 2.0e9, "oi_usd": 1e8}, {"symbol": "ETH", "product": "perp", "vol_24h_usd": 1.0e9, "oi_usd": 5e7}, {"symbol": "BTC", "product": "option", "vol_24h_usd": 3e7, "oi_usd": 1e7}])
    monkeypatch.setattr(b, "deribit_options", lambda cur="BTC": {"vol_24h_usd": 2.5e8, "oi_usd": 1.1e9, "instruments": 800})
    monkeypatch.setattr(b, "binance_options", lambda: {"vol_24h_usd": 1.2e8, "instruments": 900})
    monkeypatch.setattr(b, "bybit_options", lambda base="BTC": {"vol_24h_usd": 4e7, "oi_usd": 2e8, "instruments": 500})
    monkeypatch.setattr(b, "binance_futures", lambda: [{"symbol": "BTC", "vol_24h_usd": 1.5e10}, {"symbol": "ETH", "vol_24h_usd": 9e9}, {"symbol": "XAUT", "vol_24h_usd": 3e7}])
    monkeypatch.setattr(b, "okx_swaps", lambda: [{"symbol": "BTC", "vol_24h_usd": 4e9}, {"symbol": "XAUT", "vol_24h_usd": 8e7}])
    monkeypatch.setattr(b, "bitget_futures", lambda: [{"symbol": "BTC", "vol_24h_usd": 2e9, "oi_usd": 1e9}, {"symbol": "TSLA", "vol_24h_usd": 5e7, "oi_usd": 1e7}])
    monkeypatch.setattr(b, "fetch_bybit", lambda: [{"symbol": "BTC", "product": "perp", "vol_24h_usd": 3e9}, {"symbol": "XAUT", "product": "perp", "vol_24h_usd": 9e7, "oi_usd": 2e7}])
    import backend.market.exchanges as ex
    monkeypatch.setattr(ex, "hyperliquid_perps", lambda: {"crypto": {"BTC": {"name": "BTC", "vol_24h_usd": 4e8, "oi_usd": 1e8}}, "rwa": {"xyz:GOLD": {"name": "GOLD", "vol_24h_usd": 5e6, "oi_usd": 1e6}, "xyz:TSLA": {"name": "TSLA", "vol_24h_usd": 2e6, "oi_usd": 1e6}}})
    sources._MEM.pop("benchmarks", None)
    try: os.remove(os.path.join(sources.CACHE_DIR, "benchmarks.json"))
    except FileNotFoundError: pass
    r = b.benchmarks(force=True)
    cats = r["categories"]
    assert set(cats) == {"spot", "perps", "options", "commodities_tokenised"}
    spot = cats["spot"]; assert spot["leader"]["venue"] == "binance" and spot["india_leader"]["venue"] == "wazirx" or spot["india_leader"]["venue"] == "coindcx"
    assert spot["ours"]["venue"] == "coindcx" and spot["gap_to_leader_x"] > 1000
    perps = cats["perps"]; assert perps["leader"]["venue"] == "binance" and perps["india_leader"]["venue"] == "delta" and perps["ours"]["reference"] is True
    pair_targets = [t for t in perps["targets"] if t.get("pair")]
    assert pair_targets and pair_targets[0]["pair"] == "BTC" and pair_targets[0]["multiple"] > 10
    assert any(t.get("to_match", "").startswith("India leader") for t in perps["targets"])
    opts = cats["options"]; assert opts["leader"]["venue"] == "deribit" and opts["india_leader"]["venue"] == "delta" and opts["ours"]["reference"] is True and opts["ours"]["vol_24h_usd"] == next(v for v in opts["venues"] if v["venue"] == "bybit")["vol_24h_usd"] and any(t.get("to_match", "").startswith("India leader") for t in opts["targets"])
    rwa = cats["commodities_tokenised"]; assert {v["venue"] for v in rwa["venues"]} >= {"okx", "bybit", "bitget", "binance", "coindcx"} and rwa["ours"]["reference"]
    from backend.llm.tools import TOOLS
    assert "competitor_benchmarks" in TOOLS
