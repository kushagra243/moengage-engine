import json


def test_classify_campaign_types():
    from backend.market.campaign_intel import classify
    assert classify("Stock Buzz: Trade Apple to earn a share of $100,000!")["type"] == "trading_competition"
    assert classify("Trade TradFi Perpetual Contracts with discounted fees")["type"] == "fee_promo"
    assert classify("OKX to list Unified Tokenized Stocks xSHEIN, xKORU")["type"] == "stock_perps"
    assert classify("Binance Will Add XYZ on Earn, Buy Crypto, Convert")["type"] == "listing"
    assert classify("Diwali Dhamaka: trade and win")["type"] == "festival_offer"
    assert classify("Bitget announcement on suspending IOST deposits")["type"] == "delisting"
    assert classify("Scheduled system maintenance")["type"] == "maintenance"
    assert classify("Quarterly letter to shareholders")["type"] == "other"


def test_campaigns_dedupe_rank_counter_and_actions(monkeypatch):
    from backend.market import campaign_intel as ci, sources
    from backend.database import get_db
    import os
    conn = get_db(); conn.execute("DROP TABLE IF EXISTS competitor_campaigns"); conn.execute("DROP TABLE IF EXISTS app_rank_snapshots"); conn.commit(); conn.close()
    now = __import__("datetime").datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    monkeypatch.setattr(ci, "binance_announcements", lambda: [{"venue": "binance", "title": "Binance Futures Trading Competition: share $500,000 prize pool", "summary": "", "url": "u1", "source": "binance", "published_at": now}])
    monkeypatch.setattr(ci, "bybit_announcements", lambda: [{"venue": "bybit", "title": "Trade TradFi perps with discounted fees", "summary": "", "url": "u2", "source": "bybit", "published_at": now}])
    monkeypatch.setattr(ci, "okx_announcements", lambda: [{"venue": "okx", "title": "Scheduled maintenance for spot", "summary": "", "url": "u3", "source": "okx", "published_at": now}])
    monkeypatch.setattr(ci, "bitget_announcements", lambda: [])
    monkeypatch.setattr(ci, "mudrex_blog", lambda: [])
    monkeypatch.setattr(ci, "news_for", lambda venue, q: ([{"venue": "delta", "title": "Delta Exchange launches zero-fee options for 30 days - Economic Times", "summary": "", "url": "u4", "source": "news", "published_at": now}] if venue == "delta" else []))
    monkeypatch.setattr(ci, "app_rankings", lambda: {"apps": {"coinswitch": {"name": "CoinSwitch", "rating": 4.5, "ratings_count": 61000, "version": "9.5.6", "updated": now[:10], "release_notes": "Introducing Zing, smart trading signals", "rank_top_free": 88}, "coindcx": {"name": "CoinDCX", "rating": 4.3, "ratings_count": 44000, "version": "7.62", "updated": now[:10], "release_notes": "", "rank_top_free": 92}}, "day": now[:10]})
    sources._MEM.pop("competitor_campaigns", None)
    try: os.remove(os.path.join(sources.CACHE_DIR, "competitor_campaigns.json"))
    except FileNotFoundError: pass
    r = ci.campaigns(hours=48, force=True)
    types = {c["venue"]: c["type"] for c in r["campaigns"]}
    assert types["binance"] == "trading_competition" and types["bybit"] == "fee_promo" and types["delta"] == "fee_promo" and types["coinswitch"] == "product_launch"
    assert "okx" not in types                                       # maintenance filtered out
    assert r["campaigns"][0]["impact"] == "MATERIAL" and r["campaigns"][0]["counter_sop"]
    assert any(a["type"] == "counter_campaign" for a in r["actions"])
    assert r["apps"]["coindcx"]["rank_top_free"] == 92
    # second collection does not duplicate
    sources._MEM.pop("competitor_campaigns", None)
    r2 = ci.campaigns(hours=48, force=True)
    assert r2["new_this_fetch"] == 0 and len(r2["campaigns"]) == len(r["campaigns"])
    from backend.llm.tools import TOOLS
    assert "competitor_campaigns" in TOOLS
