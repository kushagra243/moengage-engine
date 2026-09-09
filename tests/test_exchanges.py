from backend.market import exchanges as ex


def test_asset_class_heuristics():
    assert ex._asset_class("GOLD") == "commodity" and ex._asset_class("OIL") == "commodity" and ex._asset_class("SILVER") == "commodity"
    assert ex._asset_class("XYZ100") == "index" and ex._asset_class("US500") == "index" and ex._asset_class("USTECH") == "index" and ex._asset_class("MAG7") == "index"
    assert ex._asset_class("TSLA") == "equity" and ex._asset_class("NVDA") == "equity"
    assert ex._asset_class("EUR") == "fx"


def test_universe_merge_modes(monkeypatch):
    hl = {"crypto": {"BTC": {"symbol": "BTC", "price": 80000, "chg_24h": 1.0, "funding_apr_pct": 30.0, "oi_usd": 5e9, "vol_24h_usd": 2e9, "max_leverage": 40},
                     "HYPE": {"symbol": "HYPE", "price": 40, "chg_24h": 3.0, "funding_apr_pct": -15.0, "oi_usd": 3e8, "vol_24h_usd": 4e8, "max_leverage": 10}},
          "rwa": {"TSLA": {"symbol": "TSLA", "hl_symbol": "xyz:TSLA", "dex": "xyz", "name": "TSLA", "price": 300, "chg_24h": -2.0, "vol_24h_usd": 1e7, "asset_class": "equity", "venue": "hyperliquid:xyz", "oi_usd": 1e6, "funding_1h_pct": 0},
                  "GOLD": {"symbol": "GOLD", "hl_symbol": "flx:GOLD", "dex": "flx", "name": "GOLD", "price": 4400, "chg_24h": 0.5, "vol_24h_usd": 2e6, "asset_class": "commodity", "venue": "hyperliquid:flx", "oi_usd": 1e6, "funding_1h_pct": 0}},
          "dexes": ["xyz", "flx"], "fetched_at": 1.0}
    bn = {"BTC": {"symbol": "BTC", "price": 80010, "chg_24h": 1.1, "vol_24h_usd": 3e9, "venue": "binance"},
          "ADA": {"symbol": "ADA", "price": 0.2, "chg_24h": -4.0, "vol_24h_usd": 5e7, "venue": "binance"}}
    monkeypatch.setattr(ex, "hyperliquid_perps", lambda: hl)
    monkeypatch.setattr(ex, "binance_spot", lambda: bn)
    from backend.database import set_setting
    set_setting("market_universe_mode", "either")
    u = ex.universe()
    assert {r["symbol"] for r in u["crypto"]} == {"BTC", "HYPE", "ADA"} and u["counts"]["on_both"] == 1
    btc = next(r for r in u["crypto"] if r["symbol"] == "BTC")
    assert btc["on_binance"] and btc["on_hyperliquid"] and btc["vol_24h_usd"] == 5e9 and btc["funding_apr_pct"] == 30.0
    assert [r["symbol"] for r in u["equities"]] == ["TSLA"] and [r["symbol"] for r in u["commodities"]] == ["GOLD"] and u["indices"] == []
    set_setting("market_universe_mode", "both")
    assert [r["symbol"] for r in ex.universe()["crypto"]] == ["BTC"]
    set_setting("market_universe_mode", "hyperliquid")
    assert {r["symbol"] for r in ex.universe()["crypto"]} == {"BTC", "HYPE"}
    set_setting("market_universe_mode", "either")
    mv = ex.movers(u["crypto"], n=3)
    assert mv["up"][0]["symbol"] == "HYPE" and mv["down"][0]["symbol"] == "ADA"
    assert [m["symbol"] for m in mv["crowded_long"]] == ["BTC"] and [m["symbol"] for m in mv["crowded_short"]] == ["HYPE"]
