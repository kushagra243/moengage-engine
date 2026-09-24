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


def test_patch_prices_handles_the_real_snapshot_shape_and_bad_replies(monkeypatch):
    """oi_movers is {surge: [...], drop: [...], compared_to, pairs_compared}, not a list. Iterating it yielded its keys
    as strings and the price job failed every minute with "'str' object has no attribute 'get'"."""
    import json
    from backend.market import context as cm, exchanges
    from backend.database import get_db
    cm._init(); conn = get_db()
    ctx = {"hooks": {"regime": "chop"}, "crypto": {"assets": {"BTC": {"price": 1.0, "chg_24h": 0.0}}},
           "crypto_markets": [{"symbol": "BTC", "price": 1.0, "chg_24h": 0.0}, "stray string row"],
           "oi_movers": {"surge": [{"symbol": "BTC", "oi_usd": 5.0, "oi_chg_pct": 82.2, "price_chg_pct": -1.0}], "drop": [],
                         "compared_to": "2026-09-14T09:00:00", "pairs_compared": 89}}
    conn.execute("INSERT INTO market_snapshots (regime, snapshot_json) VALUES (?, ?)", ("chop", json.dumps(ctx))); conn.commit(); conn.close()
    reply = [{"universe": [{"name": "BTC"}]}, [{"markPx": "78000", "prevDayPx": "76000", "dayNtlVlm": "3.7e9", "openInterest": "37000", "funding": "0.0000125"}]]
    monkeypatch.setattr(exchanges, "_hl", lambda body, timeout=15.0: reply)            # a list, the way the venue really answers
    r = cm.patch_prices()
    assert r["ok"] and r["patched"] >= 2
    latest = cm._latest(3600)
    mover = latest["oi_movers"]["surge"][0]
    assert mover["oi_usd"] == 37000 * 78000 and "price" not in mover and "chg_24h" not in mover, "OI mover rows keep their own fields"
    assert latest["oi_movers"]["pairs_compared"] == 89

    for bad in ("not json", {"meta": {}}, None, [{"universe": []}]):
        monkeypatch.setattr(exchanges, "_hl", lambda body, timeout=15.0, b=bad: b)
        out = cm.patch_prices()
        assert out["ok"] is False and "unexpected venue reply" in out["error"], f"a bad reply must fail clearly, not crash: {bad!r}"


def test_run_due_is_fair_to_the_tail_and_flaky_minutes_are_not_challenges(monkeypatch):
    """Fixed list order starved workspace/council/housekeeping for hours whenever the 15-minute jobs were due together."""
    import time
    from backend import refresher as rf, challenges
    now = time.time()
    names = [j["name"] for j in rf.JOBS]
    ran = []
    real_run_job = rf.run_job
    monkeypatch.setattr(rf, "_load_state", lambda: None)
    monkeypatch.setattr(rf, "run_job", lambda n: (ran.append(n), {"job": n, "ok": True})[1])
    monkeypatch.setattr(rf, "get_setting", lambda k, d="": "true" if k == "refresh_enabled" else d)
    iso = lambda secs_ago: time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(now - secs_ago))          # noqa: E731
    state = {}
    for j in rf.JOBS:                                                     # every job just ran, except: head jobs due again by a hair, the tail badly overdue
        state[j["name"]] = {"last_started": iso(rf.interval_min(j) * 60 + 5) if j["name"] in ("benchmarks", "campaign_intel", "onchain_cex", "money_flow", "app_rankings") else iso(10)}
    state["workspace"] = {"last_started": iso(140 * 60)}; state["council"] = {"last_started": iso(140 * 60)}; state["housekeeping"] = {"last_started": iso(140 * 60)}
    state["slack_approvals"] = {"last_started": iso(120)}                  # a priority job, due
    monkeypatch.setattr(rf, "_state", state); monkeypatch.setattr(rf, "_running", {})
    rf.run_due(max_jobs=3)
    non_prio = [n for n in ran if not next(j for j in rf.JOBS if j["name"] == n).get("priority")]
    assert "slack_approvals" in ran, "priority jobs always run"
    assert non_prio[0] == "workspace" and set(non_prio[:2]) <= {"workspace", "council", "housekeeping"}, non_prio
    assert len(non_prio) == 3
    # streaks: one failure logs nothing, the third in a row logs one challenge
    rf._fail_streak.clear()
    before = len(challenges.list_open("all", 500))
    def boom(): raise ConnectionError("Read timed out")
    monkeypatch.setattr(rf, "_load_state", lambda: None)
    job = next(j for j in rf.JOBS if j["name"] == "prices"); real_fn = job["fn"]; job["fn"] = boom
    try:
        real_run_job("prices"); real_run_job("prices")
        assert len(challenges.list_open("all", 500)) == before
        real_run_job("prices")
        after = challenges.list_open("all", 500)
        assert len(after) == before + 1 and any("keeps failing" in c["task"] and "3 times in a row" in c["blocked_by"] for c in after)
    finally:
        job["fn"] = real_fn; rf._fail_streak.clear()
