"""Refresh engine: due logic, manual runs, persisted state and status shape."""


def test_due_run_and_status(monkeypatch):
    from backend import refresher
    from backend.database import set_setting
    set_setting("mock_mode", "true"); set_setting("refresh_enabled", "true")
    calls = {"n": 0}
    monkeypatch.setattr(refresher, "JOBS", [{"name": "unit_fast", "minutes": 1, "fn": lambda: calls.__setitem__("n", calls["n"] + 1) or {"ok": 1}, "feeds": "test"}, {"name": "unit_boom", "minutes": 1, "fn": lambda: (_ for _ in ()).throw(RuntimeError("source down sk-secret-1234567890abcdef")), "feeds": "test"}])
    refresher._state.clear()
    assert set(refresher.due_jobs()) == {"unit_fast", "unit_boom"}          # never run → due
    runs = refresher.run_due()
    assert {r["job"]: r["ok"] for r in runs} == {"unit_fast": True, "unit_boom": False} and calls["n"] == 1
    boom = next(r for r in runs if r["job"] == "unit_boom")
    assert "sk-secret" not in (boom["error"] or "")                             # redacted
    assert refresher.due_jobs() == []                                          # just ran → not due
    set_setting("refresh_unit_fast_min", "0")
    assert "unit_fast" in refresher.due_jobs()                                 # per-job override
    st = refresher.status()
    row = next(j for j in st["jobs"] if j["job"] == "unit_boom")
    assert row["ok"] is False and row["error"] and st["failing"] >= 1 and {"interval_min", "age_min", "next_due_min", "feeds"} <= set(row)
    set_setting("refresh_enabled", "false")
    assert refresher.due_jobs() == []
    set_setting("refresh_enabled", "true"); set_setting("refresh_unit_fast_min", "")
    r = refresher.run_job("nope")
    assert not r["ok"] and "unknown job" in r["error"]
    refresher._state.clear()


def test_state_has_freshness():
    from backend import brain
    from backend.database import set_setting
    set_setting("mock_mode", "true")
    st = brain.state()
    assert "freshness" in st and {"market_age_min", "overdue", "failing", "enabled"} <= set(st["freshness"])


def test_patch_prices_updates_snapshot_in_place(monkeypatch):
    import json
    from backend.market import context, exchanges
    from backend.database import get_db
    context._init(); conn = get_db()
    ctx = {"hooks": {"regime": "chop"}, "crypto": {"assets": {"BTC": {"price": 1.0, "chg_24h": 0.0}}}, "crypto_markets": [{"symbol": "BTC", "price": 1.0, "chg_24h": 0.0, "vol_24h_usd": 1.0, "oi_usd": 1.0, "funding_apr_pct": 0.0}, {"symbol": "XYZ", "price": 5.0, "chg_24h": 1.0}]}
    conn.execute("INSERT INTO market_snapshots (regime, snapshot_json) VALUES (?, ?)", ("chop", json.dumps(ctx))); conn.commit(); conn.close()
    monkeypatch.setattr(context, "_hl", lambda body: ({"universe": [{"name": "BTC"}]}, [{"markPx": "78000", "prevDayPx": "76000", "dayNtlVlm": "3.7e9", "openInterest": "37000", "funding": "0.0000125"}]), raising=False)
    import backend.market.context as cm
    monkeypatch.setattr(exchanges, "_hl", lambda body, timeout=15.0: ({"universe": [{"name": "BTC"}]}, [{"markPx": "78000", "prevDayPx": "76000", "dayNtlVlm": "3.7e9", "openInterest": "37000", "funding": "0.0000125"}]))
    r = cm.patch_prices()
    assert r["ok"] and r["patched"] >= 1
    latest = cm._latest(3600)
    btc = next(m for m in latest["crypto_markets"] if m["symbol"] == "BTC")
    assert btc["price"] == 78000 and round(btc["chg_24h"], 2) == 2.63 and btc["vol_24h_usd"] == 3.7e9 and btc["oi_usd"] == 37000 * 78000
    assert latest["crypto"]["assets"]["BTC"]["price"] == 78000 and latest.get("prices_at")
    assert next(m for m in latest["crypto_markets"] if m["symbol"] == "XYZ")["price"] == 5.0        # untouched when the venue has no such market
    from backend import refresher
    ch = refresher.changes()
    assert "prices" in ch["jobs"] and "lab" in ch["jobs"]["prices"]["views"]
