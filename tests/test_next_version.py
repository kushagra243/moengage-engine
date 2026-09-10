"""Next version: Signal Bridge (detect → rule → policy → fire → ledger), compliance sweep over live copy, own-learnings skill."""
import json


def _ctx(regime="trending_up"):
    return {"crypto": {"regime": {"label": regime, "reasons": ["test"]}}, "hooks": {"regime": regime},
            "crypto_movers": [{"symbol": "SOL", "chg_24h": 9.4, "price": 150.0, "vol_24h_usd": 8e8}, {"symbol": "DOGE", "chg_24h": 2.0, "price": 0.1, "vol_24h_usd": 3e8}],
            "listings": {"new": [{"symbol": "ZEN", "venue": "binance", "asset_class": "crypto", "product": "spot"}]},
            "crypto_movers_detail": {"crowded_long": [{"symbol": "VVV", "funding_apr_pct": 58.7, "oi_usd": 5.8e7}], "crowded_short": []},
            "competitors": {"surges": [{"symbol": "GOLD", "product": "perp", "competitor": "delta", "surge_pct": 361, "we_list_it": True}]},
            "calendar": [], "tier0": None}


def test_detectors_and_catalog():
    from backend import signals
    ctx = _ctx()
    assert [d["attributes"]["symbol"] for d in signals._d_asset_move(ctx)] == ["SOL"]
    assert signals._d_new_listing(ctx)[0]["attributes"]["symbol"] == "ZEN"
    assert signals._d_funding_crowding(ctx)[0]["attributes"]["side"] == "long"
    surge = signals._d_competitor_surge(ctx)[0]["attributes"]
    assert surge["symbol"] == "GOLD" and "competitor" not in surge and "delta" not in json.dumps(surge)     # the venue never leaks into an event
    assert signals._d_tier0({"tier0": {"kind": "geo", "detail": "x"}})[0]["attributes"]["kind"] == "geo"
    cat = signals.catalog()
    assert {s["id"] for s in cat["signals"]} == set(signals.SIGNALS) and all({"event", "attributes", "sop", "who"} <= set(s) for s in cat["signals"])


def test_rule_lifecycle_policy_caps_and_ledger(monkeypatch):
    from backend import signals, approvals
    from backend.database import set_setting
    set_setting("mock_mode", "true")
    signals.register()
    # no rule → detected but nothing fires
    r0 = signals.evaluate(_ctx(), dry_run=False)
    assert next(x for x in r0["rows"] if x["signal"] == "asset_move")["status"] == "detected_no_rule" and r0["fired"] == 0
    # propose → approve → rule active
    p = signals.propose_rule("asset_move", max_per_day=1, rationale="test", created_by="test")
    assert p["proposal_id"] and (p["preview"] or {}).get("event_name") == "moe_asset_move"
    done = approvals.approve_and_execute(p["proposal_id"], decided_by="test")
    assert done["status"] == "executed" and any(r["signal_id"] == "asset_move" and r["enabled"] for r in signals.rules())
    monkeypatch.setattr(signals, "_in_quiet", lambda rule: False)
    r1 = signals.evaluate(_ctx(), dry_run=False)
    row = next(x for x in r1["rows"] if x["signal"] == "asset_move")
    assert row["status"] == "fired" and row["fired"] == 1
    f = signals.fires(5, "asset_move")[0]
    assert f["mode"] == "mock" and f["attributes"]["symbol"] == "SOL" and f["attributes"]["regime"] == "trending_up"
    # same day: de-duplicated / capped
    r2 = signals.evaluate(_ctx(), dry_run=False)
    row2 = next(x for x in r2["rows"] if x["signal"] == "asset_move")
    assert row2["fired"] == 0 and row2["skipped"]
    # stress regime suppresses non-service signals
    r3 = signals.evaluate(_ctx("capitulation"), dry_run=False)
    assert next(x for x in r3["rows"] if x["signal"] == "asset_move")["status"] == "suppressed_stress"
    # toggle off
    signals.set_enabled("asset_move", False, actor="test")
    r4 = signals.evaluate(_ctx(), dry_run=True)
    assert next(x for x in r4["rows"] if x["signal"] == "asset_move")["status"].endswith("no_rule")


def test_compliance_sweep_finds_issues_in_live_copy(monkeypatch):
    from backend import compliance_sweep as cs
    from backend.moengage import client as mclient

    class Fake:
        mode = "mock"; mock_mode = True

        def get_campaigns(self):
            return [{"id": "1", "name": "Flash sale", "channel": "Push", "status": "Active", "target_segment": "All", "content_preview": {"title": "20% off fees, don't miss out", "body": "Buy the dip on Binance-listed tokens now"}},
                    {"id": "2", "name": "KYC bonus", "channel": "Email", "status": "Active", "target_segment": "KYC pending users", "content_preview": {"subject": "Finish KYC, get a ₹100 bonus", "body": "Complete verification today and trade crypto."}},
                    {"id": "3", "name": "Clean recap", "channel": "Email", "status": "Active", "target_segment": "HVT", "content_preview": {"subject": "Your week", "body": "Fees, trades and alerts hit. Crypto products and NFTs are unregulated and can be highly risky. There may be no regulatory recourse for any loss from such transactions."}},
                    {"id": "4", "name": "Paused thing", "channel": "Push", "status": "Paused", "content_preview": {"title": "guaranteed 100x"}}]
    monkeypatch.setattr(mclient, "MoEngageClient", Fake)
    import backend.moengage
    monkeypatch.setattr(backend.moengage, "MoEngageClient", Fake)
    sw = cs.sweep(persist=False)
    rules = {(f["campaign"], f["rule"]) for f in sw["findings"]}
    assert ("Flash sale", "venue_named") in rules and ("Flash sale", "banned_claim") in rules and ("Flash sale", "direction") in rules
    assert ("KYC bonus", "onboarding_incentive") in rules and ("KYC bonus", "missing_disclaimer") in rules
    assert not any(f["campaign"] == "Clean recap" for f in sw["findings"]) and not any(f["campaign"] == "Paused thing" for f in sw["findings"])
    assert sw["counts"]["high"] >= 4 and sw["findings"][0]["severity"] == "high"


def test_learnings_skill_is_written_and_discoverable(tmp_path, monkeypatch):
    from backend import learnings
    from backend.database import set_setting
    set_setting("mock_mode", "true")
    target = tmp_path / "our-learnings" / "SKILL.md"
    monkeypatch.setattr(learnings, "PATH", str(target))
    r = learnings.refresh()
    assert r["ok"] and target.exists()
    text = target.read_text(encoding="utf-8")
    assert text.startswith("---\nname: our-learnings") and "## What worked here" in text and "## India fit of the SOP library" in text
