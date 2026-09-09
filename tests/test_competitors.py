import json


def _snap(monkeypatch, ours, theirs, prev_theirs=None):
    from backend.market import competitors as c, sources
    from backend.database import get_db
    c.init_tables()
    conn = get_db(); conn.execute("DELETE FROM competitor_snapshots"); conn.execute("DELETE FROM competitor_pairs"); conn.commit()
    if prev_theirs is not None:
        conn.execute("INSERT INTO competitor_snapshots (created_at, exchange, pairs_json, total_vol_usd) VALUES (datetime('now','-2 hours'), 'delta', ?, ?)", (json.dumps(prev_theirs), sum(p['vol_24h_usd'] for p in prev_theirs)))
        conn.commit()
    conn.close()
    monkeypatch.setattr(c, "_inr_usd", lambda: 88.0)
    monkeypatch.setattr(c, "_btc_usd", lambda rows: 80000.0)
    monkeypatch.setattr(c, "fetch_own", lambda inr: ours)
    monkeypatch.setattr(c, "fetch_cmc_listing", lambda: {"coindcx": {"takerFee": 0.5, "totalVolChgPct24h": 3.0}, "delta-exchange": {"takerFee": 0.05, "totalVolChgPct24h": 55.0}})
    monkeypatch.setattr(c, "fetch_delta_india", lambda: theirs)
    monkeypatch.setattr(c, "enabled_ids", lambda: ["delta"])
    import backend.market.exchanges as ex
    monkeypatch.setattr(ex, "hyperliquid_perps", lambda: {"crypto": {"BTC": {"name": "BTC", "vol_24h_usd": 5e8, "oi_usd": 1e8, "funding_1h_pct": 0.001, "price": 80000}}, "rwa": {"xyz:GOLD": {"name": "GOLD", "vol_24h_usd": 3e6, "oi_usd": 1e6, "funding_1h_pct": 0.0, "price": 2500}}})
    sources._MEM.pop("competitors", None)
    import os
    try: os.remove(os.path.join(sources.CACHE_DIR, "competitors.json"))
    except FileNotFoundError: pass
    return c


def test_intel_share_surge_gap_edge_and_actions(monkeypatch):
    ours = [{"symbol": "BTC", "product": "spot", "quote": "INR", "vol_24h_usd": 2_000_000, "inr_market": True, "price": 80000, "chg_24h": 1.0},
            {"symbol": "ETH", "product": "spot", "quote": "USDT", "vol_24h_usd": 50_000_000, "inr_market": False, "price": 2500, "chg_24h": 0.5}]
    theirs = [{"symbol": "BTC", "product": "perp", "quote": "USD", "vol_24h_usd": 9_000_000, "oi_usd": 2e6, "funding_1h_pct": 0.02, "chg_24h": 1.0, "price": 80000},
              {"symbol": "XAU", "product": "perp", "quote": "USD", "vol_24h_usd": 1_500_000, "oi_usd": 1e5, "funding_1h_pct": 0.0, "chg_24h": 0.2, "price": 2500},
              {"symbol": "SUI", "product": "perp", "quote": "USD", "vol_24h_usd": 10_000_000, "oi_usd": 1e5, "funding_1h_pct": 0.0, "chg_24h": 9.0, "price": 1.2}]
    prev = [{"symbol": "BTC", "product": "perp", "vol_24h_usd": 8_500_000}, {"symbol": "SUI", "product": "perp", "vol_24h_usd": 2_000_000}]
    c = _snap(monkeypatch, ours, theirs, prev)
    i = c.intel({"crypto_markets": []}, force=True)
    dcx = next(t for t in i["exchanges"] if t["exchange"] == "coindcx")
    assert dcx["inr_spot_vol_usd"] == 2_000_000 and dcx["usdt_spot_vol_usd_reported"] == 50_000_000 and dcx["vol_24h_usd"] == 2_000_000   # share uses INR own volume only
    assert next(t for t in i["exchanges"] if t["exchange"] == "delta")["share_of_tracked_inr_spot_pct"] is None
    surge = next(s for s in i["surges"] if s["symbol"] == "SUI"); assert surge["surge_pct"] == 400 and surge["we_list_it"] is False
    assert any(g["symbol"] == "SUI" for g in i["listing_gaps"]) and not any(g["symbol"] in ("GOLD", "XAU") for g in i["listing_gaps"])   # XAU→GOLD synonym: we list it
    btc = next(b for b in i["pair_battles"] if b["symbol"] == "BTC" and b["product"] == "perp"); assert btc["our_vol_usd"] == 500_000_000 and btc["our_share_pct"] > 90
    fe = next(f for f in i["funding_edges"] if f["symbol"] == "BTC"); assert fe["cheaper_for_longs"] == "us"
    types = [a["type"] for a in i["actions"]]
    assert "listing_request" in types and "funding_edge" in types and "press_advantage" in types and "fee_position" in types and "venue_volume_jump" in types
    assert next(t for t in i["exchanges"] if t["exchange"] == "coindcx")["taker_fee_pct"] == 0.5
    pb = c.pair_battle("btc"); assert pb["we_list_it"] and pb["venues"][0]["exchange"] in ("coindcx", "delta")


def test_competitor_hooks_and_tools():
    from backend.market.hooks import build_hooks
    ctx = {"crypto": {"regime": {"label": "chop"}}, "competitors": {"actions": [{"type": "counter_surge", "symbol": "SUI", "product": "perp", "owner": "marketing", "what": "surge", "sop": "sop_asset_spotlight"}, {"type": "listing_gap", "symbol": "XYZ", "product": "spot", "owner": "product", "what": "gap"}]}}
    ids = [h["id"] for h in build_hooks(ctx)["hooks"]]
    assert "compete_counter_surge_SUI" in ids and not any("XYZ" in x for x in ids)
    from backend.llm.tools import TOOLS, campaign_brief_check
    assert "competitor_intel" in TOOLS and "pair_battle" in TOOLS
    r = campaign_brief_check({"transition": "activated_habitual", "hypothesis": "h", "primary_kpi": "k", "target": "t", "guardrail_metric": "g", "control_group_pct": 20, "measurement_window_days": 7, "kill_criteria": "k", "suppressions": ["liquidated 14d", "loss-dormant"]},
                             [{"title": "SUI volume record on CoinDCX", "body": "More SUI traded here today than on Delta Exchange.", "cta": "View"}], channel="push")
    assert any("competitor" in p for p in r["problems"])
