import json
from datetime import datetime, timezone, timedelta


def _ctx():
    now = datetime.now(timezone.utc)
    return {"tier0": None, "crypto": {"regime": {"label": "high_volatility_down", "reasons": ["BTC −9%"]}},
            "crypto_markets": [{"symbol": "BTC", "price": 78000, "chg_24h": -9.1, "vol_24h_usd": 4e9, "oi_usd": 3e9, "funding_apr_pct": 11}, {"symbol": "ETH", "price": 2500, "chg_24h": -6.0, "vol_24h_usd": 2e9, "oi_usd": 1.5e9, "funding_apr_pct": 5}],
            "crypto_movers_detail": {"up": [], "down": [{"symbol": "BTC", "chg_24h": -9.1, "vol_24h_usd": 4e9}], "crowded_long": [{"symbol": "ONDO", "funding_apr_pct": 27, "oi_usd": 2.8e7}], "crowded_short": []},
            "equity_movers": [{"name": "NVDA", "symbol": "NVDA", "chg_24h": 4.1, "price": 180}], "index_movers": [], "commodity_movers": [{"name": "GOLD", "chg_24h": -2.3, "price": 2600}],
            "equities": [{"name": "NVDA", "symbol": "NVDA", "oi_usd": 5e8, "chg_24h": 4.1, "vol_24h_usd": 3e8, "asset_class": "equity"}], "indices": [], "commodities": [{"name": "GOLD", "oi_usd": 2e8, "chg_24h": -2.3, "vol_24h_usd": 1e8, "asset_class": "commodity"}], "macro": [],
            "oi_movers": {"surge": [{"symbol": "ETH", "oi_chg_pct": 40, "price_chg_pct": 1.0, "reading": "crowding"}], "drop": []}, "listings": {"new": [{"symbol": "PENDLE", "product": "spot", "venue": "binance"}]},
            "news": {"risk_flags": [{"title": "Exchange hack drains $40M", "published": (now - timedelta(hours=2)).strftime("%a, %d %b %Y %H:%M:%S GMT"), "source": "CoinDesk", "link": "u"}],
                     "top": {"crypto": [{"title": "Exchange hack drains $40M", "published": (now - timedelta(hours=2)).strftime("%a, %d %b %Y %H:%M:%S GMT"), "sentiment": "risk", "source": "CoinDesk", "link": "u"}, {"title": "ETF inflows continue", "published": (now - timedelta(hours=1)).strftime("%a, %d %b %Y %H:%M:%S GMT"), "sentiment": "positive", "source": "Block"}],
                             "macro": [{"title": "Fed holds rates", "published": (now - timedelta(hours=5)).strftime("%a, %d %b %Y %H:%M:%S GMT"), "sentiment": "neutral", "source": "CNBC"}]}},
            "competitors": {"actions": [{"type": "counter_surge", "symbol": "SUI", "priority": 90, "what": "surge elsewhere"}]},
            "calendar": [{"title": "CPI", "country": "USD", "impact": "High", "date": (now + timedelta(hours=5)).isoformat()}],
            "web3": {"trending": [{"symbol": "PEPE", "chain": "solana", "vol_24h_usd": 5e6, "chg_24h": 12}]},
            "hooks": {"regime": "high_volatility_down", "hooks": [{"id": "funding_long_ONDO", "trigger": "ONDO crowded", "angle": "risk_education", "asset_class": "crypto"}, {"id": "mover_equities_NVDA", "trigger": "NVDA up 4.1%", "angle": "watchlist_adoption", "asset_class": "equities (HL perps)"}]}}


def test_flash_news_top_oi_category_and_products():
    from backend.market import feed
    ctx = _ctx()
    fl = feed.flash(ctx)
    cats = [f["category"] for f in fl]
    assert fl[0]["urgency"] == "alert" and "regime" in cats and "risk headline" in cats and "crypto move" in cats and "open interest" in cats and "listing" in cats and "funding" in cats and "macro" in cats and "competitor" in cats and "US stock perp" in cats
    assert all(f["urgency"] in ("alert", "warn", "good", "info") and f["products"] for f in fl)
    news = feed.biggest_news(ctx)
    assert news[0]["sentiment"] == "risk" and news[0]["products"] and len({n["title"] for n in news}) == len(news)
    to = feed.top_oi(ctx)
    assert to["crypto"][0]["symbol"] == "BTC" and to["tokenised"][0]["symbol"] == "NVDA"
    bc = feed.top_by_category(ctx)
    assert set(bc) == {"crypto", "us_stocks", "indices", "commodities", "fx", "web3"} and bc["crypto"]["losers"][0]["symbol"] == "BTC" and bc["web3"]["trending"][0]["chain"] == "solana"
    bp = feed.by_product(ctx)
    perps = next(p for p in bp if p["product"] == "perps_crypto"); stocks = next(p for p in bp if p["product"] == "perps_us_stocks")
    assert perps["status"] == "act" and any(h["id"] == "funding_long_ONDO" for h in perps["hooks"]) and perps["sops"]
    assert any(h["id"] == "mover_equities_NVDA" for h in stocks["hooks"]) and any("NVDA" in f["headline"] for f in stocks["facts"])


def test_dossier_from_fixtures():
    from backend.market import dossiers, campaign_intel
    from backend.database import get_db
    campaign_intel.init_tables()
    conn = get_db(); conn.execute("DELETE FROM competitor_campaigns WHERE venue='bitget'")
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    for i, (t, ty, w) in enumerate((("Bitget Futures Trading Competition $500k", "trading_competition", 90), ("Welcome bonus for new users: deposit rewards", "cashback_bonus", 80), ("Bitget lists TSLAUSDT stock perpetual", "stock_perps", 78))):
        conn.execute("INSERT OR REPLACE INTO competitor_campaigns (hash, venue, type, title, summary, url, source, published_at, first_seen, weight) VALUES (?,?,?,?,?,?,?,?,?,?)", (f"t{i}", "bitget", ty, t, "", None, "bitget announcements", now, now, w))
    conn.execute("INSERT OR REPLACE INTO app_rank_snapshots (day, app, chart, rank, rating, ratings_count, version) VALUES (date('now'), 'bitget', 'top_free', 120, 4.6, 5000, '3.1')")
    conn.commit(); conn.close()
    intel = {"exchanges": [{"exchange": "bitget", "name": "Bitget", "cmc_total_vol_24h_usd": 8e9, "cmc_vol_chg_7d_pct": 12.0, "taker_fee_pct": 0.06, "weekly_visits": 2e6}], "rivals": [{"id": "bitget", "pressureIndex": 55, "threat": "ELEVATED", "color": "#ffb84d"}], "surges": [{"competitor": "bitget", "symbol": "SUI", "surge_pct": 120}], "listing_gaps": []}
    bench = {"categories": {"perps": {"leader": {"name": "Binance"}, "venues": [{"venue": "binance", "vol_24h_usd": 6e10}, {"venue": "bitget", "vol_24h_usd": 7.7e9, "oi_usd": 4.4e9, "markets": 719, "top_pairs": [{"symbol": "BTC", "vol_24h_usd": 2e9}], "vol_7d_ago_usd": 7e9}]}}}
    d = dossiers.dossier("bitget", intel, bench)
    assert d["playbook"].startswith("competition-led activation") and d["campaigns_7d"]["count"] == 3 and d["campaigns_7d"]["audience_focus"]
    assert d["market"]["by_category"]["perps"]["rank"] == 2 and d["pressure"]["index"] == 55 and d["app"]["rank_top_free"] == 120
    assert any("volume +12% w/w" in x for x in d["what_changed_7d"]) and any("perps volume +10%" in x for x in d["what_changed_7d"])
    assert d["counters"][0]["against"] == "trading_competition" and d["counters"][0]["sop"] == "sop_trading_competition" and d["how_to_beat"]
    from backend.llm.tools import TOOLS
    assert "market_flash" in TOOLS and "competitor_dossier" in TOOLS


def test_campaign_from_alert_queues_compliant_proposals():
    from backend import sops, approvals, segments
    from backend.moengage import mock
    from backend.moengage.executors import register_all
    from backend.database import set_setting
    register_all(); set_setting("mock_mode", "true"); sops.init_sop_tables(); segments.sync(mock.segments())
    dry = sops.run_from_alert("perps_us_stocks", "NVDA +4.1% after US close on Binance", "moved while Wall Street was closed", dry_run=True)
    v = dry["variants"][0]
    assert "binance" not in v["title"].lower() and len(v["title"]) <= 60 and len(v["body"]) <= 140 and "24/7 on CoinDCX" in v["body"]
    assert dry["source_alert"]["product"] == "perps_us_stocks" and dry["sop"] == "sop_tokenised_after_hours"
    real = sops.run_from_alert("spot", "SOL moved 6.2% today", "$2.1B volume", created_by="test")
    if real["ok"]:
        assert real["run_id"] and len(real["proposal_ids"]) >= 1
        p = approvals.get_proposal(real["proposal_ids"][0]); assert p["status"] == "pending" and p["payload"]["variants"][0]["cta"] == "View on CoinDCX"
    else:
        assert real["preflight"]["checks"]                       # blocked by pre-flight in this mock state, but never silently
    from backend.market import feed
    bp = feed.by_product(_ctx())
    stocks = next(p for p in bp if p["product"] == "perps_us_stocks")
    assert stocks["facts"] and stocks["facts"][0]["campaign"]["sop"] == "sop_tokenised_after_hours" and stocks["global_alerts"]
    from backend.llm.tools import TOOLS
    assert "campaign_from_alert" in TOOLS
