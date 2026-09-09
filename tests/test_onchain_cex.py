def test_hl_vs_cex_compare_from_fixtures(monkeypatch):
    from backend.market import onchain_cex as oc, sources
    import os
    monkeypatch.setattr(oc, "hl_global", lambda: {"daily_volume_usd": 8.6e9, "oi_usd": 1.5e10, "users": 2500000, "total_volume_usd": 5e12, "source": "t"})
    monkeypatch.setattr(oc, "fetch_cmc_listing", lambda: {"binance": {"derivativesVol24h": 6.2e10, "derivativesOpenInterests": 3.2e10, "derivativesMarketPairs": 796, "takerFee": 0.04}, "okx": {"derivativesVol24h": 2.3e10, "derivativesOpenInterests": 7e9}, "bybit": {"derivativesVol24h": 1.4e10, "derivativesOpenInterests": 6e9}, "bitget": {"derivativesVol24h": 7.7e9, "derivativesOpenInterests": 4.4e9}, "gate": {"derivativesVol24h": 1.2e10, "derivativesOpenInterests": 1.1e10}})
    monkeypatch.setattr(oc, "defillama_oi", lambda: {"total_onchain_oi_usd": 2.6e10, "protocols": [{"name": "Hyperliquid Perps", "oi_usd": 1.46e10, "share_of_onchain_pct": 56.0, "hl_ecosystem": True}, {"name": "tradeXYZ", "oi_usd": 3.8e9, "share_of_onchain_pct": 14.6, "hl_ecosystem": True}, {"name": "Aster Perps", "oi_usd": 2.5e9, "share_of_onchain_pct": 9.6, "hl_ecosystem": False}], "source": "t"})
    import backend.market.exchanges as ex
    monkeypatch.setattr(ex, "hyperliquid_perps", lambda: {"crypto": {"BTC": {"oi_usd": 6e9, "vol_24h_usd": 3e9, "funding_1h_pct": 0.0009, "price": 78000}, "ETH": {"oi_usd": 3e9, "vol_24h_usd": 2e9, "funding_1h_pct": 0.0012, "price": 2500}}, "rwa": {}})
    monkeypatch.setattr(oc, "binance_funding_map", lambda: {"BTC": {"funding_1h_pct": 0.00125, "mark": 78000}, "ETH": {"funding_1h_pct": 0.0010, "mark": 2500}})
    monkeypatch.setattr(oc, "binance_oi_usd", lambda coin, mark: {"BTC": 8.2e9, "ETH": 4e9}[coin])
    monkeypatch.setattr(oc, "bybit_map", lambda: {"BTC": {"oi_usd": 4.4e9, "funding_1h_pct": 0.00106, "vol_24h_usd": 4.1e9}, "ETH": {"oi_usd": 2e9, "funding_1h_pct": 0.0011, "vol_24h_usd": 3e9}})
    monkeypatch.setattr(oc, "okx_map", lambda coins: {"BTC": {"oi_usd": 4.7e8, "funding_1h_pct": 0.00096}, "ETH": {"oi_usd": 1.6e8}})
    monkeypatch.setattr(oc, "coinglass", lambda path, params=None: {"configured": False, "note": "no key"})
    sources._MEM.pop("onchain_cex", None)
    try: os.remove(os.path.join(sources.CACHE_DIR, "onchain_cex.json"))
    except FileNotFoundError: pass
    r = oc.compare(force=True)
    v = r["hl_vs_cex"]
    assert v["rank_if_listed_with_cex"] == 5 and v["vs_binance_volume_x"] == 7.2 and 0 < v["share_of_cex_top5_volume_pct"] < 20 and v["hl_oi_vs_binance_oi_x"] == 2.13
    btc = next(c for c in r["per_coin"] if c["coin"] == "BTC")
    assert btc["hl_oi_share_pct"] == round(6e9 / (6e9 + 8.2e9 + 4.4e9 + 4.7e8) * 100, 1) and btc["cheapest_for_longs"] == "hyperliquid" and btc["hl_funding_edge_vs_binance_1h_pct"] > 0
    eth = next(c for c in r["per_coin"] if c["coin"] == "ETH"); assert eth["cheapest_for_longs"] == "binance"
    assert r["onchain"]["protocols"][0]["hl_ecosystem"] and r["coinglass"]["configured"] is False and r["errors"] == []
    from backend.llm.tools import TOOLS
    assert "onchain_vs_cex" in TOOLS
    from backend.security.secrets import is_secret_key
    assert is_secret_key("market_coinglass_api_key")
