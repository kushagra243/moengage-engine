"""Brain Lab: money-flow reads and recommendations, ICE scoring, structural audit detection, and the aggregate views the terminal consumes."""
import pytest

from backend import ice


def test_ice_score_and_inference():
    s = ice.score(9, 8, 7)
    assert s["score"] == 504 and s["grade"] == "A" and s["mean"] == 8.0
    assert ice.score(0, 99, "x")["impact"] == 1 and ice.score(0, 99, "x")["confidence"] == 10 and ice.score(0, 99, "x")["ease"] == 5
    assert ice.impact_from_text("CTR 3–5× broadcast; alert adoption +20–30%") >= 8
    assert ice.impact_from_text("informs the next brief") <= 5
    inferred = ice.infer("+8–12% opens vs fixed slot", 0.72, "low", "rules", 60)
    assert inferred["impact"] == 7 and inferred["confidence"] == 7 and inferred["ease"] == 8
    pay = {"goal": {"control_group_pct": 20, "transition": "verified_funded", "target": "+5 pp first deposit"}, "channel": "push"}
    fp = ice.from_payload(pay)
    assert fp["confidence"] == 8 and fp["ease"] == 8
    assert ice.from_payload({"ice": {"impact": 3, "confidence": 3, "ease": 3}})["score"] == 27
    t = ice.tagline("Second trade within 72h", "second_trade_within_7d", "FIRST_TRADE_72H")
    assert t.startswith("For FIRST_TRADE_72H") and "measured by second trade within 7d" in t


def _flow(**over):
    base = {"regime": "chop", "risk_appetite": {"score": 70, "label": "risk-on", "reasons": ["Fear & Greed 66"]},
            "dominance": {"btc": 58.0, "eth": 11.0, "btc_chg_1d_pp": -0.5, "mcap_chg_24h_pct": 1.2, "total_mcap_usd": 2.7e12},
            "stablecoins": {"total_now_usd": 2.7e11, "net_1d_usd": 7e7, "net_7d_usd": 1.1e9, "chains_in": [{"chain": "Tron", "now_usd": 8e10, "chg_7d_usd": 5e8}], "chains_out": [], "top": []},
            "rotation": {"in": [{"tag": "AI", "chg_24h_pct": 6.0, "top": ["TAO"]}, {"tag": "L2", "chg_24h_pct": 3.5, "top": ["ARB"]}], "out": [{"tag": "memecoins", "chg_24h_pct": -6.0, "top": ["DOGE"]}], "count": 20, "source": "test"},
            "venue_share": {"now": {"crypto": 70.0, "us_stocks": 18.0, "indices": 8.0, "commodities": 4.0}, "week_ago": {"crypto": 78.0, "us_stocks": 12.0, "indices": 7.0, "commodities": 3.0}},
            "attention": [{"symbol": "SOL", "asset_class": "crypto", "vol_24h_usd": 5e9, "vs_7d_avg_x": 2.4, "chg_24h": 8.0}],
            "leverage": {"hl_oi_usd": 6e9, "oi_chg_1d_pct": 7.0, "crowded_long": ["VVV"], "crowded_short": [], "funding_bias": 0.3},
            "web3": {"trending_count": 25, "top_chain": "solana", "top": [("solana", "PUMP")]}, "history_days": 3}
    base.update(over)
    return base


def test_behaviour_reads_cover_each_signal():
    from backend.market import moneyflow
    reads = moneyflow.behaviour_reads(_flow())
    text = " ".join(r["read"] for r in reads)
    assert "Risk appetite is up" in text and "rotating out of BTC" in text and "Sector rotation into AI" in text
    assert "Money leaving memecoins" in text and "Dry powder" in text and "Leverage is building" in text
    assert "US-stock perps" in text and "Attention spikes: SOL" in text
    assert all({"read", "evidence", "traders_do", "confidence"} <= set(r) for r in reads)
    quiet = moneyflow.behaviour_reads({"risk_appetite": {"label": "neutral"}, "rotation": {}, "stablecoins": {}, "leverage": {}, "venue_share": {}, "web3": {}, "attention": [], "dominance": {}})
    assert quiet and quiet[0]["read"].startswith("Quiet tape")


def test_recommendations_map_signals_to_sops_and_freeze_in_stress():
    from backend.market import moneyflow
    f = _flow()
    recs = moneyflow.recommendations(f, moneyflow.behaviour_reads(f), {})
    sops = {r["sop"] for r in recs}
    assert {"sop_funding_crowding_nudge", "sop_asset_spotlight", "sop_slipping_checkin", "sop_verified_to_funded", "sop_cross_sell_crypto_to_tokenised", "sop_web3_trending_watch", "sop_market_dormant_return"} <= sops
    assert recs == sorted(recs, key=lambda r: -r["priority"])
    assert all({"title", "why", "who", "what", "kpi", "urgency", "avoid"} <= set(r) for r in recs)
    stress = moneyflow.recommendations(_flow(regime="capitulation"), [], {})
    assert stress[0]["sop"] == "sop_global_announcement_lenses" and stress[0]["urgency"] == "now"
    assert not any(r["sop"] in ("sop_asset_spotlight", "sop_web3_trending_watch", "sop_market_dormant_return") for r in stress)


def test_structural_audit_detects_missing_journeys(monkeypatch):
    from backend import structural
    from backend.llm import tools
    camps = [{"id": "c1", "name": "Weekend Flash Sale 20% Off", "channel": "Push", "target_segment": "All Active App Users", "sent_count": 1000, "delivered_count": 900},
             {"id": "c2", "name": "Deposit failure recovery (UPI)", "channel": "WhatsApp", "target_segment": "DEPOSIT_FAILED_24H", "sent_count": 100, "delivered_count": 95},
             {"id": "c3", "name": "Price alert · watchlist movers", "channel": "Push", "target_segment": "WATCHLIST_HOLDERS", "sent_count": 500, "delivered_count": 470}]

    class FakeClient:
        mode = "mock"

        def get_campaigns(self):
            return camps
    monkeypatch.setattr(tools, "_client", lambda: FakeClient())
    a = structural.audit(camps)
    ids = {g["id"] for g in a["gaps"]}
    assert "deposit_failure_recovery" in a["covered"] and "price_alert_adoption" in a["covered"] and "whatsapp_utility_journey" in a["covered"]
    assert {"funded_first_trade", "second_trade_72h", "liquidation_recovery", "regime_stress_mode"} <= ids
    assert a["gaps"] == sorted(a["gaps"], key=lambda g: -g["ice"]["score"])
    assert all({"tagline", "who", "what", "sop", "kpi", "benchmark", "ice", "transition"} <= set(g) for g in a["gaps"])
    recs = structural.as_recommendations(3, camps)
    assert len(recs) == 3 and all(r["structural"] and r["priority"] >= 95 and r["ice"]["score"] >= 300 for r in recs)
    res = structural.sync_to_feed(camps)
    assert res["gaps"] == len(a["gaps"]) and res["added"] + res["refreshed"] == res["gaps"]
    from backend import growth
    feed = growth.list_ideas(status="new", limit=200, kind="structural_gap")
    assert feed and all((i.get("data") or {}).get("structural") for i in feed)


def test_lab_and_atlas_views_have_the_terminal_shape(monkeypatch):
    from backend import brain
    from backend.market import moneyflow
    from backend.database import set_setting
    set_setting("mock_mode", "true")
    monkeypatch.setattr(moneyflow, "categories", lambda *a, **k: {"in": [], "out": [], "count": 0, "source": "test"})
    monkeypatch.setattr(moneyflow, "stablecoins", lambda *a, **k: {"stables": [], "chains_in": [], "chains_out": [], "total_now_usd": 0, "net_1d_usd": 0, "net_7d_usd": 0})
    L = brain.lab_view()
    assert {"situation", "flow", "reads", "recommendations", "events", "rivals", "benchmarks", "hl_vs_cex", "market"} <= set(L)
    assert L["recommendations"] and all("ice" in r and "tagline" in r for r in L["recommendations"])
    structural_first = [r.get("structural") for r in L["recommendations"]]
    assert structural_first[0] is True and structural_first == sorted(structural_first, key=lambda x: not x)
    assert {"major", "minor", "moves", "campaigns", "apps", "actions", "sov"} <= set(L["rivals"])
    assert {"tiles", "hooks", "top_oi", "by_category", "by_product"} <= set(L["market"])
    A = brain.atlas_view()
    assert {"families", "products", "peace", "north_star", "limits", "studies", "requests"} <= set(A)
    assert len(A["products"]) == 9 and all({"lens", "never", "cadence", "cross_sell"} <= set(p) for p in A["products"])
    ideas = brain.ideas()
    assert ideas and all("ice" in i and "tagline" in i for i in ideas)
    assert [i["structural"] for i in ideas] == sorted([i["structural"] for i in ideas], key=lambda x: not x)
    st = brain.state()
    assert {"lab", "atlas"} <= set(st["stats"]) and "lab" in st["badges"]


def test_competitor_surges_ignore_option_contracts():
    from backend.market import competitors
    src = open(competitors.__file__, encoding="utf-8").read()
    assert 'p.get("product") == "option"' in src


def test_market_moving_news_filters_and_tags():
    from backend.market import feed
    ctx = {"crypto_markets": [{"symbol": "BTC", "oi_usd": 3e9, "vol_24h_usd": 4e9, "chg_24h": 0.3}, {"symbol": "SOL", "oi_usd": 5e8, "vol_24h_usd": 8e8, "chg_24h": -13.0}, {"symbol": "TINY", "oi_usd": 1e6, "vol_24h_usd": 6e7, "chg_24h": 40.0}, {"symbol": "XYZ", "oi_usd": 1e6, "vol_24h_usd": 5e6, "chg_24h": 1.0}],
           "commodities": [{"symbol": "GOLD", "chg_24h": 1.0}], "equities": [{"symbol": "NVDA", "chg_24h": 2.0}], "oi_movers": [{"symbol": "SOL", "oi_chg_pct": -9.0}],
           "news": {"top": {"crypto": [{"title": "Over $600 million in longs liquidated as Bitcoin slips below support", "link": "u1", "published": None, "sentiment": "risk", "source": "S"},
                                       {"title": "Local bakery opens second branch", "link": "u2", "published": None, "sentiment": "neutral", "source": "S"}],
                            "macro": [{"title": "Fed holds rates, signals cut in December", "link": "u3", "published": None, "sentiment": "neutral", "source": "S"}],
                            "stocks": [{"title": "Nvidia earnings beat estimates, guidance raised", "link": "u4", "published": None, "sentiment": "positive", "source": "S"}],
                            "commodities": [{"title": "Gold hits record as dollar weakens", "link": "u5", "published": None, "sentiment": "positive", "source": "S"}]}}}
    r = feed.market_moving_news(ctx, 20)
    titles = {i["title"]: i for i in r["items"]}
    assert not any("bakery" in t for t in titles)
    liq = next(v for t, v in titles.items() if "liquidated" in t)
    assert "liquidations" in liq["drivers"] and liq["assets"] == ["BTC"] and liq["impact"] == "high" and "margin buffer" in liq["so_what"]
    fed = next(v for t, v in titles.items() if t.startswith("Fed")); assert "macro" in fed["drivers"] and fed["assets"] == ["broad market"]
    nv = next(v for t, v in titles.items() if t.startswith("Nvidia")); assert "earnings" in nv["drivers"] and nv["assets"] == ["NVDA"]
    gold = next(v for t, v in titles.items() if t.startswith("Gold")); assert gold["assets"] == ["GOLD"]
    synth = [i for i in r["items"] if i.get("synthetic")]
    assert len(synth) == 1 and synth[0]["assets"] == ["SOL"] and "long liquidations likely" in synth[0]["title"] and "(OI -9%)" in synth[0]["title"]
    assert dict(r["by_driver"])["liquidations"] >= 2 and "SOL" in r["assets_in_focus"] and "GOLD" in r["assets_in_focus"]


def test_etfs_classify_as_index_product():
    from backend.market.exchanges import _asset_class
    from backend.products import PRODUCTS
    assert all(_asset_class(x) == "index" for x in ("SOXL", "EWY", "KORU", "QQQ", "IBIT", "SP500", "USTECH"))
    assert _asset_class("NVDA") == "equity" and _asset_class("GOLD") == "commodity"
    assert PRODUCTS["perps_indices"]["name"] == "Index & ETF perps" and "ETF" in PRODUCTS["perps_indices"]["codes"]
