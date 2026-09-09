import json


def test_product_affinity_from_names():
    from backend.segments import decode
    assert decode("WEB3_SOL_Degens_Sep26")["products"] == ["web3"]
    assert decode("USS_Watchers_Sep26")["products"] == ["perps_us_stocks"]
    assert decode("HVT_Sep26")["products"] == ["spot"]                       # default when no product code
    assert decode("GMC_Sep26_HFT_Futures_Ret")["products"] == ["perps_crypto"]
    assert decode("SIP_Active_Aug26")["products"] == ["sip"]
    assert decode("Options_Intent_Sep26")["products"] == ["options"]


def test_onchain_quality_gate_and_majors_filter(monkeypatch):
    from backend.market import onchain, sources
    gt = {"data": [
        {"attributes": {"name": "PEPE / WETH", "address": "p1", "base_token_price_usd": "0.00001", "price_change_percentage": {"h24": "12.5"}, "volume_usd": {"h24": "5000000"}, "reserve_in_usd": "900000", "pool_created_at": "2026-01-01T00:00:00Z", "transactions": {"h24": {"buys": 500, "sells": 400}}}},
        {"attributes": {"name": "USDT / WETH", "address": "p2", "base_token_price_usd": "1", "price_change_percentage": {"h24": "0.1"}, "volume_usd": {"h24": "90000000"}, "reserve_in_usd": "50000000", "pool_created_at": "2025-01-01T00:00:00Z", "transactions": {"h24": {"buys": 1, "sells": 1}}}},
        {"attributes": {"name": "RUG / SOL", "address": "p3", "base_token_price_usd": "0.5", "price_change_percentage": {"h24": "900"}, "volume_usd": {"h24": "400000"}, "reserve_in_usd": "20000", "pool_created_at": "2099-01-01T00:00:00Z", "transactions": {"h24": {"buys": 900, "sells": 20}}}},
    ]}
    def fake_json(url, params=None, timeout=15.0, headers=None):
        if "geckoterminal" in url:
            return gt
        if "token-boosts" in url:
            return [{"chainId": "solana", "tokenAddress": "x", "description": "PAID", "totalAmount": 500}, {"chainId": "tron", "tokenAddress": "y"}]
        return []
    monkeypatch.setattr(sources, "_json", fake_json)
    monkeypatch.setattr(onchain, "chains", lambda: ["solana"])
    t = onchain.trending(force=True)
    syms = [r["symbol"] for r in t["trending"]]
    assert syms == ["PEPE"] and all(r["symbol"] != "USDT" for r in t["trending"] + t["rejected"])
    rug = next(r for r in t["rejected"] if r["symbol"] == "RUG")
    assert {"low_liquidity", "extreme_move", "one_sided_flow"} <= set(rug["flags"])
    assert len(t["boosted"]) == 1 and "paid" in t["boosted"][0]["source"]


def test_web3_hooks_blocked_in_stress_and_tier0_trigger():
    from backend.market.hooks import build_hooks
    from backend.autopilot import tier0_trigger
    ctx = {"crypto": {"regime": {"label": "trending_up"}}, "web3": {"by_chain": {"solana": [{"symbol": "PEPE", "vol_24h_usd": 5e6, "chg_24h": 12.5, "flags": []}]}}}
    h = build_hooks(ctx)
    assert any(x["id"] == "web3_trend_solana" for x in h["hooks"])
    ctx["crypto"]["regime"]["label"] = "trending_down"
    assert "web3_trend_solana" in build_hooks(ctx)["blocked_hook_ids"]
    assert tier0_trigger({"crypto": {"regime": {"label": "chop"}}, "crypto_markets": [{"symbol": "BTC", "chg_24h": -9.2}]})["kind"] == "major_move"
    assert tier0_trigger({"crypto": {"regime": {"label": "capitulation", "reasons": ["x"]}}, "crypto_markets": []})["kind"] == "stress_regime"
    assert tier0_trigger({"crypto": {"regime": {"label": "chop"}}, "news": {"risk_flags": [{"title": "War escalates"}, {"title": "New tariff on chips"}, {"title": "SEBI regulation draft"}]}})["kind"] == "geopolitical_or_regulatory"
    assert tier0_trigger({"crypto": {"regime": {"label": "chop"}}, "crypto_markets": [{"symbol": "BTC", "chg_24h": 2.0}]}) is None


def test_announcement_lenses_product_matrix_and_sops():
    from backend.products import announcement_lenses, PRODUCTS
    from backend import sops
    al = announcement_lenses("BTC -9% in 24h")
    assert set(al["lenses"]) == set(PRODUCTS) and al["regime_rule"]
    pm = sops.product_cohort_matrix(); ids = {x["id"] for x in sops.LIBRARY}
    assert len(pm["cohorts"]) == len(PRODUCTS) and all(s in ids for c in pm["cohorts"] for s in c["sops"])
    for sid in ("sop_global_announcement_lenses", "sop_geopolitical_event_brief", "sop_web3_trending_watch", "sop_web3_onboarding_safety", "sop_product_cohort_monthly", "sop_options_education", "sop_sip_nurture"):
        assert sid in ids
    assert all(sops.sop_check(x)["ok"] for x in sops.LIBRARY)


def test_data_requests_and_experiments_from_proposals():
    from backend import datarequests, approvals, experiments
    from backend.moengage.executors import register_all
    from backend.database import set_setting
    register_all(); set_setting("mock_mode", "true")
    r = datarequests.request("segment", "Upload WEB3_SOL_Active_Sep26 (swap event 30d on Solana)", "Web3 trending watch cannot target Solana users without it", spec="customer_id list from wallet service", unblocks=["sop_web3_trending_watch"])
    assert r["id"] and datarequests.request("segment", "upload web3_sol_active_sep26 (swap event 30d on solana)", "dup")["duplicate"]
    assert datarequests.set_status(r["id"], "fulfilled", "uploaded")["status"] == "fulfilled"
    p = approvals.propose("create_campaign", "Exp from proposal", {"name": "EXP_test", "channel": "push", "target_segment": "HVT_Sep26", "variants": [{"title": "t", "body": "b", "cta": "c"}],
                                                                  "goal": {"transition": "habitual_core", "hypothesis": "h", "primary_kpi": "ctr", "target": "+1", "guardrail_metric": "g", "control_group_pct": 20, "measurement_window_days": 7, "kill_criteria": "k"}}, created_by="test")
    assert p.get("experiment_id")
    ex = next(e for e in experiments.list_experiments(200) if e["proposal_id"] == p["id"])
    assert ex["status"] == "proposed"
    approvals.reject(p["id"], note="x", decided_by="test")
    ex2 = next(e for e in experiments.list_experiments(200) if e["proposal_id"] == p["id"])
    assert ex2["status"] == "abandoned"
    from backend.llm.tools import TOOLS
    for t in ("web3_trending", "product_cohorts", "announcement_lenses", "request_data", "data_requests"):
        assert t in TOOLS
