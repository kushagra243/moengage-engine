"""
Market Alerts 2.0 — every BRD rule, the privacy guarantees and the pilot gate.

Governance and signal helpers are pure, so the BRD is checked rule by rule without network or database.
"""
import json
import os
from datetime import datetime, timedelta, timezone

import pytest

IST = timezone(timedelta(hours=5, minutes=30))


def _cfg(**over):
    from backend.alerts2 import rules
    import copy
    c = copy.deepcopy(rules.DEFAULTS)
    c.update(over)
    return c


def _sig(**over):
    base = {"prices": {"BTC": 78100.0, "ETH": 3120.0, "SOL": 160.0, "DOGE": 0.2}, "chg_24h": {"BTC": 1.0, "ETH": 6.2, "SOL": -7.5, "XRP": 9.0},
            "moves": {}, "ath_atl": {}, "ath_week_blocked": [], "milestones": {}, "milestone_day_first": {}, "most_traded": {},
            "whales": [], "futures_listed": ["BTC", "ETH", "SOL", "DOGE"], "regime": "chop",
            "listed": {"futures": ["BTC", "ETH", "SOL", "DOGE"], "us_futures": ["TSLA"], "options": ["BTC", "ETH"], "spot": ["BTC", "ETH", "SOL", "DOGE", "XRP"]}}
    base.update(over)
    return base


def _user(**over):
    u = {"user_id": "u1", "uid_hash": "abc", "products": ["futures"], "positions": [], "spot_holdings": [], "watchlist": [], "traded": [],
         "futures_screen_views_7d": 0, "futures_ever": True, "profitable_trades": [], "country": "IN",
         "flags": {"push_disabled": False, "dnd": False, "liquidated_14d": False}, "holdout": False}
    u.update(over)
    return u


def _decide(user, sig, ledger=None, gates=None, cfg=None):
    from backend.alerts2 import governance as g
    cfg = cfg or _cfg(); ledger = ledger or g.empty_ledger()
    cands = g.build_candidates(user, sig, ledger, cfg)
    return g.decide(user, cands, ledger, gates or {"quiet": False, "stress": False, "exposure_age_min": 5}, cfg)


def _sends(decisions):
    return [d for d in decisions if d["decision"] in ("send", "holdout")]


# ── Relevance · PnL (BRD 3.1) ─────────────────────────────────────────────────
def test_pnl_fires_at_5pct_and_ignores_smaller_moves():
    pos = lambda pnl: {"token": "BTC", "product": "futures", "side": "long", "entry_price": 70000, "leverage": 5, "pnl_pct": pnl, "key": "BTC|futures|long"}
    assert not _sends(_decide(_user(positions=[pos(4.9)]), _sig()))
    s = _sends(_decide(_user(positions=[pos(-5.0)]), _sig()))
    assert len(s) == 1 and s[0]["theme"] == "pnl" and s[0]["slot"] == "baseline" and s[0]["direction"] == "down"


def test_pnl_computed_from_entry_side_and_leverage_when_the_app_does_not_send_it():
    from backend.alerts2.governance import position_pnl
    assert position_pnl({"entry_price": 100, "side": "long", "leverage": 5}, 102) == 10.0
    assert position_pnl({"entry_price": 100, "side": "short", "leverage": 2}, 103) == -6.0
    assert position_pnl({"pnl_pct": 7.5, "entry_price": 1}, 999) == 7.5


def test_pnl_extra_needs_a_further_10_points_from_the_last_alert_on_that_position():
    from backend.alerts2 import governance as g
    p = lambda pnl: [{"token": "BTC", "product": "futures", "side": "long", "pnl_pct": pnl, "key": "K"}]
    lg = g.empty_ledger(); lg["today"]["relevance"]["baseline"] = 1; lg["pnl_last"]["K"] = 6.0
    d = _decide(_user(positions=p(15.9)), _sig(), lg)
    assert not _sends(d), "9.9 points is not a breach"
    d = _decide(_user(positions=p(16.0)), _sig(), lg)
    assert [x["slot"] for x in _sends(d)] == ["extra"]


def test_same_position_does_not_realert_next_day_without_a_10_point_move():
    from backend.alerts2 import governance as g
    lg = g.empty_ledger(); lg["pnl_last"]["K"] = 6.0                       # alerted yesterday at +6
    d = _decide(_user(positions=[{"token": "BTC", "product": "futures", "pnl_pct": 8.0, "key": "K"}]), _sig(), lg)
    assert not _sends(d)


# ── Relevance · price movement (BRD 3.2) ──────────────────────────────────────
def test_price_movement_priority_active_before_traded_and_futures_before_spot():
    sig = _sig(moves={"SOL|spot": {"z": 5.0, "ret_pct": 3.0}, "DOGE|futures": {"z": 2.1, "ret_pct": 1.0}, "ETH|futures": {"z": 2.2, "ret_pct": -1.2}})
    u = _user(products=["futures", "spot"], spot_holdings=[{"token": "SOL", "auc_inr": 10}], traded=["DOGE", "ETH"])
    s = _sends(_decide(u, sig))
    assert s[0]["token"] == "SOL", "active holding outranks traded tokens even with a bigger Z elsewhere"
    u2 = _user(products=["futures", "spot"], traded=["SOL", "ETH"])
    sig2 = _sig(moves={"SOL|spot": {"z": 6.0, "ret_pct": 3.0}, "ETH|futures": {"z": 2.1, "ret_pct": 1.0}})
    assert _sends(_decide(u2, sig2))[0]["product"] == "futures", "same relation: Futures beats Spot"


def test_price_movement_breach_at_twice_the_threshold():
    from backend.alerts2 import governance as g
    lg = g.empty_ledger(); lg["today"]["relevance"]["baseline"] = 1
    u = _user(traded=["ETH"])
    assert not _sends(_decide(u, _sig(moves={"ETH|futures": {"z": 3.9, "ret_pct": 2}}), lg))
    d = _decide(u, _sig(moves={"ETH|futures": {"z": 4.0, "ret_pct": 2}}), lg)
    assert [x["slot"] for x in _sends(d)] == ["extra"]
    assert any(x["reason"] == "no_breach" for x in _decide(u, _sig(moves={"ETH|futures": {"z": 3.0, "ret_pct": 2}}), lg))


def test_pnl_outranks_price_movement_inside_relevance():
    u = _user(positions=[{"token": "BTC", "product": "futures", "pnl_pct": 6, "key": "K"}], traded=["ETH"])
    s = _sends(_decide(u, _sig(moves={"ETH|futures": {"z": 9, "ret_pct": 4}})))
    assert [x["theme"] for x in s] == ["pnl"]


# ── caps and anchor (BRD 6) ───────────────────────────────────────────────────
def test_category_cap_is_one_baseline_plus_one_breach_extra():
    from backend.alerts2 import governance as g
    lg = g.empty_ledger(); lg["today"]["relevance"] = {"baseline": 1, "extra": 1}
    d = _decide(_user(traded=["ETH"]), _sig(moves={"ETH|futures": {"z": 9, "ret_pct": 5}}), lg)
    assert not _sends(d) and d[0]["reason"] == "category_cap"


def test_four_alerts_a_day_only_when_both_categories_breach():
    from backend.alerts2 import governance as g
    cfg = _cfg(); lg = g.empty_ledger(); total = 0
    u = _user(products=["futures"], traded=["ETH"])
    runs = [
        _sig(moves={"ETH|futures": {"z": 2.5, "ret_pct": 1}}, milestones={"BTC": {"direction": "up", "level": 78000, "price": 78050}}),
        _sig(moves={"ETH|futures": {"z": 4.5, "ret_pct": 3}}, milestones={"BTC": {"direction": "up", "level": 80000, "price": 80020}}, milestone_day_first={"BTC": 78000}),
        _sig(moves={"ETH|futures": {"z": 6.0, "ret_pct": 5}}, milestones={"BTC": {"direction": "up", "level": 83000, "price": 83010}}, milestone_day_first={"BTC": 78000}),
    ]
    slots = []
    for sig in runs:
        cands = g.build_candidates(u, sig, lg, cfg)
        d = g.decide(u, cands, lg, {"quiet": False, "stress": False, "exposure_age_min": 1}, cfg)
        s = _sends(d); total += len(s); slots += [(x["category"], x["slot"]) for x in s]
        assert len({x["category"] for x in s}) == len(s), "never two alerts of one category in the same run"
        lg = g.apply_to_ledger(lg, d)
    assert total == 4 and sorted(slots) == sorted([("relevance", "baseline"), ("discovery", "baseline"), ("relevance", "extra"), ("discovery", "extra")])


def test_relevance_is_the_anchor_when_nothing_else_triggers():
    s = _sends(_decide(_user(traded=["ETH"]), _sig(moves={"ETH|futures": {"z": 2.4, "ret_pct": 1}})))
    assert len(s) == 1 and s[0]["category"] == "relevance"


# ── Discovery (BRD 4) ─────────────────────────────────────────────────────────
def test_milestone_extra_needs_a_further_2000_btc_and_caps_at_two():
    from backend.alerts2 import governance as g
    lg = g.empty_ledger(); lg["today"]["discovery"]["baseline"] = 1; lg["milestone_today"] = 1
    near = _sig(milestones={"BTC": {"direction": "up", "level": 79000, "price": 79010}}, milestone_day_first={"BTC": 78000})
    assert not _sends(_decide(_user(), near, lg))
    far = _sig(milestones={"BTC": {"direction": "up", "level": 80000, "price": 80010}}, milestone_day_first={"BTC": 78000})
    assert [x["slot"] for x in _sends(_decide(_user(), far, lg))] == ["extra"]
    lg["milestone_today"] = 2; lg["today"]["discovery"]["extra"] = 0
    assert not _sends(_decide(_user(), far, lg)), "max 2 milestone alerts a day"


def test_ath_atl_weekly_token_cap_blocks_with_a_reason():
    d = _decide(_user(), _sig(ath_atl={"ETH": "ath"}, ath_week_blocked=["ETH"]))
    assert not _sends(d) and d[0]["reason"] == "ath_weekly_token_cap"


def test_whale_beats_most_traded_and_price_trending_beats_volume():
    sig = _sig(whales=[{"token": "DOGE", "product": "futures", "side": "buy", "size_usd": 3e6}], most_traded={"futures": "DOGE"})
    assert _sends(_decide(_user(), sig))[0]["alert_type"] == "whale"
    sig2 = _sig(whales=[{"token": "DOGE", "product": "futures", "side": "buy", "size_usd": 3e6}], ath_atl={"ETH": "ath"})
    assert _sends(_decide(_user(), sig2))[0]["alert_type"] == "ath"


def test_volume_trending_skips_spot_only_users_and_excluded_majors():
    from backend.alerts2.signals import most_traded
    rows = [{"symbol": "BTC", "vol_24h_usd": 9e9}, {"symbol": "ETH", "vol_24h_usd": 5e9}, {"symbol": "SOL", "vol_24h_usd": 3e9}, {"symbol": "HYPE", "vol_24h_usd": 1e9}]
    assert most_traded(rows, ["BTC", "ETH", "SOL"]) == "HYPE"
    sig = _sig(most_traded={"futures": "HYPE", "spot": "HYPE"}, whales=[{"token": "ETH", "product": "futures", "side": "sell", "size_usd": 1e6}])
    spot_user = _user(products=["spot"], futures_ever=False)
    assert not [d for d in _sends(_decide(spot_user, sig)) if d["theme"] == "volume_trending"]
    assert not [d for d in _sends(_decide(_user(), _sig(most_traded={"futures": "BTC"}))) if d["alert_type"] == "most_traded"]


# ── Moments of Truth (BRD 5) ──────────────────────────────────────────────────
def test_cross_sell_pure_spot_futures_viewer_highest_auc_and_outside_the_cap():
    from backend.alerts2 import governance as g
    u = _user(products=["spot"], futures_ever=False, futures_screen_views_7d=1,
              spot_holdings=[{"token": "ETH", "auc_inr": 5000}, {"token": "SOL", "auc_inr": 9000}, {"token": "XRP", "auc_inr": 99999}])
    lg = g.empty_ledger(); lg["today"] = {"relevance": {"baseline": 1, "extra": 1}, "discovery": {"baseline": 1, "extra": 1}}
    s = [d for d in _sends(_decide(u, _sig(), lg)) if d["theme"] == "cross_sell"]
    assert len(s) == 1 and s[0]["token"] == "SOL" and s[0]["direction"] == "down", "XRP is not futures-listed; SOL has the higher AUC"
    assert not [d for d in _sends(_decide({**u, "futures_screen_views_7d": 0}, _sig())) if d["theme"] == "cross_sell"]
    assert not [d for d in _sends(_decide({**u, "products": ["spot", "futures"]}, _sig())) if d["theme"] == "cross_sell"]
    lg2 = g.empty_ledger(); lg2["cross_sell_recent"] = True
    assert [d["reason"] for d in _decide(u, _sig(), lg2) if d["theme"] == "cross_sell"] == ["cross_sell_monthly_cap"]


def test_referral_realised_futures_first_then_spot_once_in_a_lifetime():
    from backend.alerts2 import governance as g
    trades = [{"kind": "unrealized_spot", "token": "SOL", "profit_pct": 30, "volume_inr": 5000}, {"kind": "realized_futures", "token": "BTC", "profit_pct": 10, "volume_inr": 1000}]
    s = [d for d in _sends(_decide(_user(profitable_trades=trades), _sig())) if d["theme"] == "referral"]
    assert len(s) == 1 and s[0]["trigger"] == "realized_futures" and s[0]["token"] == "BTC"
    small = [{"kind": "realized_futures", "token": "BTC", "profit_pct": 9.9, "volume_inr": 50000}, {"kind": "unrealized_spot", "token": "SOL", "profit_pct": 12, "volume_inr": 999}]
    assert not [d for d in _sends(_decide(_user(profitable_trades=small), _sig())) if d["theme"] == "referral"]
    lg = g.empty_ledger(); lg["referral_ever"] = True
    assert [d["reason"] for d in _decide(_user(profitable_trades=trades), _sig(), lg) if d["theme"] == "referral"] == ["referral_lifetime_cap"]


# ── gates ─────────────────────────────────────────────────────────────────────
def test_holdout_is_decided_and_capped_but_never_sent():
    from backend.alerts2 import governance as g
    u = _user(traded=["ETH"], holdout=True)
    d = _decide(u, _sig(moves={"ETH|futures": {"z": 3, "ret_pct": 2}}))
    assert [x["decision"] for x in d] == ["holdout"]
    lg = g.apply_to_ledger(g.empty_ledger(), d)
    assert lg["today"]["relevance"]["baseline"] == 1, "holdout consumes the cap like treatment, so the comparison is fair"


def test_quiet_hours_stress_liquidation_push_off_and_stale_file():
    base = _user(positions=[{"token": "BTC", "product": "futures", "pnl_pct": 7, "key": "K"}], traded=["ETH"])
    sig = _sig(moves={"ETH|futures": {"z": 3, "ret_pct": 2}}, ath_atl={"ETH": "ath"})
    assert {d["reason"] for d in _decide(base, sig, gates={"quiet": True, "stress": False, "exposure_age_min": 1})} == {"quiet_hours"}
    stress = _decide(base, sig, gates={"quiet": False, "stress": True, "exposure_age_min": 1})
    assert [d["theme"] for d in _sends(stress)] == ["pnl"] and any(d["reason"] == "stress_regime" for d in stress)
    liq = _decide({**base, "flags": {"liquidated_14d": True}}, sig)
    assert any(d["reason"] == "liquidated_14d" and d["category"] == "discovery" for d in liq) and _sends(liq)[0]["theme"] == "pnl"
    assert {d["reason"] for d in _decide({**base, "flags": {"push_disabled": True}}, sig)} == {"push_disabled"}
    stale = _decide(base, sig, gates={"quiet": False, "stress": False, "exposure_age_min": 90})
    assert any(d["theme"] == "pnl" and d["reason"] == "stale_exposure" for d in stale)
    assert [d["theme"] for d in _sends(stale) if d["category"] == "relevance"] == ["price_movement"], "stale positions pause PnL only"


# ── signal helpers ────────────────────────────────────────────────────────────
def test_zscore_ath_atl_and_milestones():
    from backend.alerts2.signals import zscore, ath_atl, milestone_cross
    flat = [100 * (1 + 0.001 * ((i % 5) - 2)) for i in range(60)]
    spike = flat + [flat[-1] * 1.08]
    assert zscore(flat[:10], 168) is None
    z = zscore(spike, 168)
    assert z and z["z"] > 10 and z["ret_pct"] == pytest.approx(8.0, abs=0.01)
    daily = [{"h": 100 + i, "l": 50 + i, "c": 75} for i in range(40)]
    assert ath_atl(daily, 140) == "ath" and ath_atl(daily, 49) == "atl" and ath_atl(daily, 100) is None and ath_atl(daily[:10], 999) is None
    assert milestone_cross(77950, 78010, 1000) == {"direction": "up", "level": 78000, "price": 78010}
    assert milestone_cross(80500, 77100, 1000)["level"] == 78000, "a big drop reports the furthest level crossed"
    assert milestone_cross(3210, 3390, 200) is None and milestone_cross(3390, 3401, 200)["level"] == 3400
    assert milestone_cross(None, 78010, 1000) is None


# ── copy ──────────────────────────────────────────────────────────────────────
def test_brd_cross_sell_copy_is_blocked_and_default_copy_passes():
    from backend.alerts2 import rules
    lint = rules.lint_all()
    assert lint["blocking"] == []
    brd = {b["key"]: {f["rule"] for f in b["findings"]} for b in lint["brd_reference"]}
    assert {"hypothetical_returns", "leverage_multiple"} <= brd["cross_sell:up"]
    assert "implied_safety" in brd["cross_sell:down"]
    assert rules.render("pnl:up", {"token": "BTC", "product": "futures", "pnl": 6.25})["body"].startswith("Your BTC Futures position is at +6.2%")


def test_shared_live_copy_sweep_now_catches_single_digit_leverage():
    from backend.llm.tools import LEVERAGE_LURE
    assert LEVERAGE_LURE.search("profit at 5x leverage") and not LEVERAGE_LURE.search("learn how leverage works")


# ── cohort privacy ────────────────────────────────────────────────────────────
def test_cohort_rejects_personal_data_in_columns_or_values():
    from backend.alerts2 import cohort
    ok = json.dumps({"users": [{"user_id": "moe_1", "products": ["spot"]}]}).encode()
    assert cohort.parse("c.json", ok)["users"]
    for bad in ({"users": [{"user_id": "moe_1", "email": "x@y.com"}]},
                {"users": [{"user_id": "moe_1", "notes": "call +91 98765 43210"}]},
                {"users": [{"user_id": "9876543210"}]},
                {"users": [{"user_id": "moe_1", "full_name": "A B"}]},
                {"users": [{"user_id": "moe_1", "kyc": "ABCDE1234F"}]}):
        with pytest.raises(cohort.CohortError):
            cohort.parse("c.json", json.dumps(bad).encode())


def test_cohort_csv_compact_format_and_hashing():
    from backend.alerts2 import cohort
    csv_text = ("user_id,products,watchlist,traded_tokens,positions,spot_holdings,futures_screen_views_7d,profitable_trades,liquidated_14d\n"
                "moe_a,futures|spot,SOL|DOGE,BTC,BTC:futures:long:70000:5:12.5,ETH:40000:3000,3,realized_futures:BTC:14:25000,false\n")
    users, whales, notes = cohort.normalise(cohort.parse("c.csv", csv_text.encode()))
    u = users[0]
    assert u["positions"][0] == {"token": "BTC", "product": "futures", "side": "long", "entry_price": 70000.0, "leverage": 5.0, "pnl_pct": 12.5, "key": "BTC|futures|long"}
    assert u["spot_holdings"][0]["auc_inr"] == 40000.0 and u["watchlist"] == ["DOGE", "SOL"] and u["profitable_trades"][0]["kind"] == "realized_futures"
    h = cohort.hash_id("moe_a")
    assert h == cohort.hash_id("moe_a") and "moe_a" not in h and len(h) == 24
    assert 0 <= cohort.holdout_bucket(h) < 100


# ── end to end: dry run, pilot gate, live in mock mode ────────────────────────
def _upload(ids=("moe_live_1", "moe_live_2", "moe_live_3", "moe_live_4", "moe_live_5")):
    from backend.alerts2 import cohort
    users = [{"user_id": i, "products": ["futures"], "futures_ever": True, "traded_tokens": ["ETH"],
              "positions": [{"token": "BTC", "product": "futures", "side": "long", "pnl_pct": 8.0}]} for i in ids]
    return cohort.save("pilot.json", json.dumps({"cohort_name": "MA2_test", "users": users}).encode(), actor="test")


def test_dry_run_live_gate_pilot_approval_and_mock_delivery():
    from backend.alerts2 import service, rules
    from backend import approvals
    from backend.database import get_db, set_setting
    set_setting("mock_mode", "true"); set_setting("ma2_config_json", json.dumps({"holdout_pct": 20}))
    service.register()
    _upload()
    now = datetime(2026, 9, 15, 11, 0, tzinfo=IST)
    sig = _sig(moves={"ETH|futures": {"z": 2.6, "ret_pct": 1.5}})

    dry = service.run("dry_run", now=now, signals_override=sig)
    assert dry["ok"] and dry["would_send"] + dry["holdout"] == 5, "PnL at +8% for each of five users"
    conn = get_db(); assert conn.execute("SELECT COUNT(*) n FROM ma2_ledger").fetchone()["n"] == 0; conn.close()

    assert not service.run("live", now=now, signals_override=sig)["ok"], "no approved pilot, no live run"

    p = service.propose_pilot("MA2_test", holdout_pct=20, days=7, created_by="test")
    approvals.approve_and_execute(p["proposal_id"], decided_by="lead")
    assert service.pilot_live(now)["live"]

    live = service.run("live", now=now, signals_override=sig)
    assert live["ok"] and live["delivery"] == "mock" and live["sent"] + live["holdout"] == 5
    conn = get_db()
    rows = [dict(r) for r in conn.execute("SELECT uid_hash, theme, slot, status FROM ma2_ledger").fetchall()]
    dump = json.dumps([dict(r) for r in conn.execute("SELECT * FROM ma2_ledger").fetchall()] + [dict(r) for r in conn.execute("SELECT summary_json FROM ma2_runs").fetchall()])
    conn.close()
    assert all(r["theme"] == "pnl" and r["slot"] == "baseline" and r["status"] in ("recorded_mock", "holdout") for r in rows)
    assert "moe_live_" not in dump, "raw customer ids never reach the ledger or run records"

    again = service.run("live", now=now + timedelta(minutes=15), signals_override=sig)
    assert again["sent"] + again["holdout"] == 0, "the same PnL and a sub-breach move do not stack a second alert"
    assert service.today_distribution(now)["max_per_user"] <= 4

    service.set_kill(True, actor="test")
    assert not service.run("live", now=now, signals_override=sig)["ok"]
    service.set_kill(False, actor="test")

    _upload(("moe_live_1", "moe_new_9"))
    r = service.run("live", now=now, signals_override=sig)
    assert not r["ok"] and "membership changed" in r["error"]


def test_pilot_approval_blocked_by_non_compliant_copy_and_tools_hide_user_rows():
    from backend.alerts2 import service
    from backend.llm import tools
    from backend import approvals
    from backend.database import set_setting
    service.register()
    _upload(("moe_c_1", "moe_c_2"))
    set_setting("ma2_templates_json", json.dumps({"cross_sell:up": {"title": "Up 5%", "body": "You could have made 25% profit at 5x leverage"}}))
    try:
        with pytest.raises(Exception):
            service.propose_pilot("blocked", themes=["cross_sell"], created_by="test")
    finally:
        set_setting("ma2_templates_json", "{}")
    st = tools.market_alerts_status()
    assert "samples" not in json.dumps(st.get("last_run") or {}) and "moe_c_" not in json.dumps(st)
    rules = tools.market_alerts_rules()
    assert rules["assumptions"] and any("Z-score" in a["topic"] for a in rules["assumptions"])
    assert approvals._executors.get("ma2_pilot") and "ma2_pilot" in approvals.KINDS


# ── discovery experiment: no per-user data, fires business events ─────────────
def _disc_ctx():
    return {"crypto_markets": [{"symbol": "BTC", "price": 78100.0, "vol_24h_usd": 9e9}, {"symbol": "ETH", "price": 3120.0, "vol_24h_usd": 4e9},
                               {"symbol": "SOL", "price": 160.0, "vol_24h_usd": 2e9}, {"symbol": "HYPE", "price": 40.0, "vol_24h_usd": 1e9}],
            "crypto": {"regime": {"label": "chop"}}, "hooks": {"regime": "chop"}}


def test_large_trade_burst_needs_both_volume_and_trade_size():
    from backend.alerts2.discovery import large_trade_burst
    base = [{"o": 100, "c": 100, "v": 1000, "n": 100} for _ in range(50)]
    assert large_trade_burst(base, 1000, 3.0, 2.0) is None
    quiet_spike = base + [{"o": 100, "c": 101, "v": 5000, "n": 500}]          # 5x volume but the same average trade size
    assert large_trade_burst(quiet_spike, 1000, 3.0, 2.0) is None
    whale = base + [{"o": 100, "c": 103, "v": 5000, "n": 50}]                 # 5x volume on a tenth of the trades
    b = large_trade_burst(whale, 1000, 3.0, 2.0)
    assert b and b["vol_x"] >= 5.0 and b["size_x"] >= 10.0 and b["direction"] == "up"
    assert large_trade_burst(whale, 10 ** 9, 3.0, 2.0) is None, "below the minimum notional"
    assert large_trade_burst(base[:5], 1000, 3.0, 2.0) is None


def test_whale_feed_keeps_only_market_facts_and_ages_out(monkeypatch):
    import time
    from backend.alerts2 import discovery
    r = discovery.ingest_whales([{"token": "eth", "side": "B", "size_usd": 4_000_000, "ts": time.time() * 1000, "users": ["0xabc"], "hash": "0xdead"},
                                 {"token": "SOL", "side": "sell", "size_usd": 10, "ts": time.time()},
                                 {"token": "BTC", "side": "sell", "size_usd": 900_000, "ts": time.time() - 7200}], actor="test")
    assert r["accepted"] == 2 and r["rejected"] == 1
    feed = discovery._whale_feed()
    assert [w["token"] for w in feed] == ["ETH"], "the two-hour-old trade is outside the TTL"
    assert set(feed[0]) == {"token", "product", "side", "size_usd", "price", "ts"} and feed[0]["side"] == "buy"
    assert "0xabc" not in json.dumps(discovery._whale_feed(all_rows=True)), "wallet addresses never enter the engine"


def test_discovery_dry_run_caps_and_live_gate(monkeypatch):
    from backend.alerts2 import discovery, service
    from backend.database import set_setting
    from backend import approvals
    set_setting("mock_mode", "true")
    discovery.register()
    monkeypatch.setattr(discovery.sig_mod, "hl_candles", lambda *a, **k: [])
    monkeypatch.setattr(discovery.sig_mod, "zscore", lambda *a, **k: None)
    from backend.market import sources
    monkeypatch.setattr(sources, "klines", lambda *a, **k: [])
    now = datetime(2026, 9, 16, 12, 0, tzinfo=IST)

    dry = discovery.run("dry_run", now=now, ctx=_disc_ctx())
    assert dry["ok"] and dry["would_send"] >= 1
    assert any(d["signal"] == "most_traded" and d["token"] == "HYPE" for d in dry["summary"]["decisions"]), "majors are excluded from most traded"
    assert not discovery.run("live", now=now, ctx=_disc_ctx())["ok"], "no approved experiment, no fire"

    p = discovery.propose("disc test", days=7, signals=["most_traded", "milestone", "large_trades"], created_by="test")
    approvals.approve_and_execute(p["proposal_id"], decided_by="lead")
    assert discovery.is_live(now)["live"]

    live = discovery.run("live", now=now, ctx=_disc_ctx())
    assert live["ok"] and live["sent"] >= 1
    fires = discovery.fires(10)
    assert fires and all(f["status"] == "recorded_mock" for f in fires)
    again = discovery.run("live", now=now, ctx=_disc_ctx())
    assert again["sent"] == 0 and again["summary"]["already_recorded"] >= 1, "a detection already judged is never judged twice"

    quiet = discovery.run("live", now=now.replace(hour=23), ctx=_disc_ctx())
    assert quiet["sent"] == 0 and quiet["summary"]["quiet_hours"]
    stress_ctx = {**_disc_ctx(), "crypto": {"regime": {"label": "capitulation"}}}
    assert discovery.run("live", now=now.replace(hour=14), ctx=stress_ctx)["sent"] == 0

    service.set_kill(True, actor="test")
    assert not discovery.run("live", now=now, ctx=_disc_ctx())["ok"]
    service.set_kill(False, actor="test")


def test_discovery_launches_a_real_moengage_campaign_and_experiment():
    from backend.alerts2 import discovery
    from backend import approvals
    from backend.llm import tools
    from backend.moengage.executors import register_all
    register_all()                                        # the MoEngage executors main.py registers at boot
    b = discovery.campaign_brief(control_pct=20)
    check = tools.campaign_brief_check(b["goal"], b["variants"], "push", market_linked=True, ttl_hours=b["ttl_hours"])
    assert check["ok"], check["problems"]
    assert b["schedule"]["business_event"] == "MA2_Discovery_INTERNAL" and b["goal"]["control_group_pct"] == 5
    assert any("liquidat" in e.lower() for e in b["exclusions"])

    r = discovery.launch(days=7, signals=["most_traded"], control_pct=20, created_by="test", reviewed=True)
    camp = approvals.get_proposal(r["campaign_proposal_id"])
    assert camp["kind"] == "create_campaign" and camp["status"] == "pending"
    assert "{{BusinessEvent.title}}" in json.dumps(camp["payload"]), "copy comes from the event the engine fires"
    st = discovery.status()
    assert st["campaign"]["state"] == "pending" and [l["step"] for l in st["launch"]][0].startswith("MoEngage")

    approvals.approve_and_execute(r["campaign_proposal_id"], decided_by="lead")
    from backend.database import get_db
    conn = get_db(); row = conn.execute("SELECT id, control_group_pct, primary_kpi FROM experiments WHERE proposal_id=?", (r["campaign_proposal_id"],)).fetchone(); conn.close()
    assert row and row["control_group_pct"] == 5, "an approved internal-stage campaign becomes a live experiment; employees all see it, 5% control"


def test_experiment_registration_survives_list_kill_criteria():
    """A campaign proposal executed but no experiment appeared: SQLite cannot bind a list, and the error was swallowed."""
    from backend import experiments
    from backend.database import get_db
    p = {"id": 987654, "kind": "create_campaign", "payload": {"name": "KillCriteriaList", "goal": {
        "primary_kpi": "sessions_per_week", "target": "+0.3", "guardrail_metric": "notification_disable_rate",
        "control_group_pct": 20, "measurement_window_days": 14, "kill_criteria": ["disable rate > 0.3%", "uninstalls up"]}}}
    eid = experiments.register_from_proposal(p, {"success": True, "campaign_id": "cmp_1"}, "mock")
    conn = get_db(); row = conn.execute("SELECT kill_criteria FROM experiments WHERE id=?", (eid,)).fetchone(); conn.close()
    assert eid and "disable rate" in row["kill_criteria"]


# ── continuous programme and the best-time loop ───────────────────────────────
def test_send_windows_learn_from_click_rate_and_keep_exploring():
    from backend.alerts2 import timing, rules
    from backend.database import get_db
    cfg = rules.config()
    timing.init_tables()
    conn = get_db()
    conn.execute("DELETE FROM ma2_window_days")
    for i, (day, win, ctr) in enumerate([("2026-09-01", "morning", 1.0), ("2026-09-02", "morning", 1.2), ("2026-09-03", "morning", 1.1),
                                         ("2026-09-04", "evening", 3.0), ("2026-09-05", "evening", 3.4), ("2026-09-06", "evening", 3.2)]):
        conn.execute("INSERT INTO ma2_window_days (day_ist, window_id, chosen_by, fires, delivered, clicks, ctr) VALUES (?,?,?,?,?,?,?)", (day, win, "explore", 2, 1000, int(ctr * 10), ctr))
    conn.commit(); conn.close()
    b = timing.best(cfg)
    assert b["learned"] and b["window"]["id"] == "evening" and "3.2" in b["why"]
    picks = {timing.window_for_day(f"2026-10-{d:02d}", cfg)["chosen_by"] for d in range(1, 26)}
    assert "explore" in picks and "best" in picks, "the engine keeps trying other windows instead of locking in"
    assert timing.window_for_day("2026-10-05", cfg)["window"]["id"] == timing.window_for_day("2026-10-05", cfg)["window"]["id"], "a day keeps its window"


def test_best_time_is_not_claimed_before_there_is_evidence():
    from backend.alerts2 import timing, rules
    from backend.database import get_db
    conn = get_db(); conn.execute("DELETE FROM ma2_window_days"); conn.commit(); conn.close()
    b = timing.best(rules.config())
    assert not b["learned"] and b["window"]["id"] == "evening" and "no window has" in b["why"]


def test_evergreen_signals_wait_for_the_window_and_perishable_ones_do_not(monkeypatch):
    from backend.alerts2 import discovery, timing, service
    from backend import approvals
    from backend.database import get_db, set_setting
    set_setting("mock_mode", "true")
    discovery.register()
    from backend.alerts2 import detect
    detect.init_tables()
    conn = get_db()
    for t in ("ma2_queue", "ma2_discovery_fires", "ma2_window_days", "ma2_detections", "ma2_watermarks"):
        conn.execute(f"DELETE FROM {t}")
    conn.commit(); conn.close()
    monkeypatch.setattr(discovery.sig_mod, "hl_candles", lambda *a, **k: [])
    monkeypatch.setattr(discovery.sig_mod, "zscore", lambda *a, **k: None)
    from backend.market import sources
    monkeypatch.setattr(sources, "klines", lambda *a, **k: [])
    discovery.ingest_whales([{"token": "ETH", "side": "buy", "size_usd": 5_000_000, "ts": __import__("time").time()}], actor="test")
    p = discovery.propose("continuous", days=0, signals=["most_traded", "large_trades"], audience="ALL_PUSH_ENABLED", created_by="test",
                          override_reason="test of the whole-base window lane", cohort_ids=["futures_active", "all"])
    approvals.approve_and_execute(p["proposal_id"], decided_by="lead")
    st = discovery.is_live(datetime(2026, 9, 16, 11, 0, tzinfo=IST))
    assert st["live"] and st.get("standing") and st["experiment"]["ends_at"] is None, "continuous: no end date"

    morning = datetime(2026, 9, 16, 11, 0, tzinfo=IST)                        # before the evening window
    r = discovery.run("live", now=morning, ctx=_disc_ctx())
    assert r["sent"] >= 1 and r["queued"] == 1, "the whale fires now, the most-traded token waits"
    q = [x for x in timing.queue_view() if x["status"] == "queued"]
    assert q and q[0]["signal"] == "most_traded" and q[0]["due_at"] > morning.isoformat()
    assert discovery.release(morning, "test")["fired"] == 0, "nothing is due before the window opens"

    evening = datetime(2026, 9, 16, 19, 45, tzinfo=IST)
    out = discovery.release(evening, "test")
    assert out["fired"] == 1
    assert [f["signal"] for f in discovery.fires(5)][0] == "most_traded"
    assert [x["status"] for x in timing.queue_view() if x["id"] == q[0]["id"]] == ["sent"]


def test_queued_alerts_are_dropped_when_they_go_stale_or_the_market_turns():
    from backend.alerts2 import discovery, timing
    from backend.database import get_db
    conn = get_db(); conn.execute("DELETE FROM ma2_queue"); conn.commit(); conn.close()
    now = datetime(2026, 9, 16, 11, 0, tzinfo=IST)
    timing.enqueue({"signal": "ath_atl", "token": "LIT", "direction": "up", "value": 4.0, "title": "t", "body": "b"}, now, __import__("backend.alerts2.rules", fromlist=["rules"]).config(), actor="test")
    late = now + timedelta(days=2)
    assert discovery.release(late, "test")["fired"] == 0 and [x["status"] for x in timing.queue_view()][0] == "expired", "a two-day-old 1-year high is not news"

    timing.enqueue({"signal": "ath_atl", "token": "LIT", "direction": "up", "value": 4.0, "title": "t", "body": "b"}, now, __import__("backend.alerts2.rules", fromlist=["rules"]).config(), actor="test")
    out = discovery.release(datetime(2026, 9, 16, 20, 0, tzinfo=IST), "test", regime="capitulation")
    assert out["fired"] == 0 and out.get("held") == "stress_regime"


def test_continuous_campaign_targets_the_whole_base_with_a_permanent_control():
    from backend.alerts2 import discovery
    from backend.llm import tools
    b = discovery.campaign_brief(audience="ALL_PUSH_ENABLED", continuous=True)
    assert b["target_segment"] == "ALL_PUSH_ENABLED" and b["goal"]["control_group_pct"] == 10 and b["goal"]["continuous"] is True
    assert tools.campaign_brief_check(b["goal"], b["variants"], "push", market_linked=True, ttl_hours=b["ttl_hours"])["ok"]
    assert any("liquidat" in e.lower() for e in b["exclusions"]) and any("dnd" in e.lower() or "unsub" in e.lower() for e in b["exclusions"])


# ── the draft MoEngage actually accepts ───────────────────────────────────────
def test_campaign_payload_matches_the_documented_v5_schema():
    """The engine used to post its own brief shape to /v5/campaigns, so no draft ever appeared in MoEngage."""
    from backend.alerts2 import discovery
    from backend.moengage.executors import v5_campaign_payload
    b = discovery.campaign_brief(audience="ALL_PUSH_ENABLED", continuous=True)
    p = {k: b[k] for k in ("name", "channel", "target_segment", "variants", "schedule", "ttl_hours", "goal", "frequency_cap")}
    body = v5_campaign_payload(p)
    assert body["channel"] == "PUSH" and body["campaign_delivery_type"] == "BUSINESS_EVENT_TRIGGERED"
    assert body["basic_details"]["business_event"] == discovery.EVENT, "the event name belongs in basic_details"
    push = body["campaign_content"]["content"]["push"]["android"]
    assert push["template_type"] == "BASIC" and push["basic_details"]["title"] == "{{BusinessEvent.title}}"
    assert body["segmentation_details"]["is_all_user_campaign"] is True and body["segmentation_details"]["send_campaign_to_opt_out_users"] is False
    assert body["scheduling_details"]["delivery_type"] == "AT_FIXED_TIME" and body["scheduling_details"]["expiry_time"] > body["scheduling_details"]["start_time"]
    assert body["control_group_details"] == {"is_campaign_control_group_enabled": True, "campaign_control_group_percentage": 10}
    assert set(body) <= {"request_id", "channel", "campaign_delivery_type", "created_by", "basic_details", "trigger_condition", "campaign_content",
                         "segmentation_details", "scheduling_details", "delivery_controls", "advanced", "conversion_goal_details",
                         "control_group_details", "utm_params", "campaign_audience_limit"}, "no field outside the documented schema"


def test_a_named_segment_becomes_a_custom_segment_filter():
    from backend.alerts2 import discovery
    from backend.moengage.executors import v5_campaign_payload
    b = discovery.campaign_brief(audience="ACTIVE_30D")
    p = {k: b[k] for k in ("name", "channel", "target_segment", "variants", "schedule", "ttl_hours", "goal", "frequency_cap")}
    seg = v5_campaign_payload(p)["segmentation_details"]
    assert seg["included_filters"]["filters"] == [{"filter_type": "custom_segments", "name": "ACTIVE_30D"}]


def test_live_draft_refuses_without_a_creator_email_and_the_event_name():
    from backend.moengage import executors
    from backend.database import set_setting
    from backend.alerts2 import discovery
    b = discovery.campaign_brief()
    p = {k: b[k] for k in ("name", "channel", "target_segment", "variants", "schedule", "ttl_hours", "goal", "frequency_cap")}
    set_setting("mock_mode", "true")
    executors._cmp_validate(p)                                     # mock: allowed, nothing leaves the machine
    set_setting("mock_mode", "false")
    try:
        with pytest.raises(ValueError, match="created_by"):
            executors._cmp_validate(p)
        set_setting("moengage_created_by", "ops@example.com")
        executors._cmp_validate(p)
        bad = {**p, "schedule": {"type": "business_event_triggered"}}
        with pytest.raises(ValueError, match="business_event"):
            executors._cmp_validate(bad)
    finally:
        set_setting("mock_mode", "true"); set_setting("moengage_created_by", "")


def test_preflight_names_every_reason_nothing_reached_moengage():
    from backend.alerts2 import discovery
    from backend.database import set_setting
    set_setting("mock_mode", "true")
    checks = {c["check"]: c for c in discovery.preflight()}
    assert set(checks) == {"Mode", "Campaigns API key", "Creator email", "Business event", "Campaign draft", "Engine firing", "Engine as a service", "Heartbeat"}
    assert checks["Mode"]["ok"] is False and "nothing reaches MoEngage" in checks["Mode"]["detail"]
    assert all(c["ok"] or c["fix"] for c in checks.values()), "anything blocked says how to unblock it"


# ── determinism: every closed candle judged exactly once, nothing missed ──────
def _c5(start_ts, n, base_v=1000.0, base_n=100, px=100.0):
    return [{"t": start_ts + i * 300, "o": px, "h": px + 0.2, "l": px - 0.2, "c": px, "v": base_v, "n": base_n} for i in range(n)]


def test_only_closed_candles_are_judged_and_the_forming_one_waits():
    from backend.alerts2 import detect
    start = 1_789_000_000
    rows = _c5(start, 60)
    rows[-1] = {**rows[-1], "v": 20000.0, "n": 50, "c": 103.0, "o": 100.0}          # a burst in the newest candle
    now_ts = start + 59 * 300 + 120                                                  # that candle is still forming
    assert detect.closed(rows, "5m", now_ts)[-1]["t"] == rows[-2]["t"]
    assert detect.burst_at(detect.closed(rows, "5m", now_ts), 58, 1000, 3.0, 2.0) is None
    now_ts = start + 60 * 300                                                        # it has closed
    closed = detect.closed(rows, "5m", now_ts)
    assert closed[-1]["t"] == rows[-1]["t"] and detect.burst_at(closed, 59, 1000, 3.0, 2.0)


def test_watermark_makes_a_late_run_catch_up_and_never_double_judge(monkeypatch):
    from backend.alerts2 import detect, rules
    from backend.database import get_db
    conn = get_db(); conn.execute("DELETE FROM ma2_watermarks"); conn.execute("DELETE FROM ma2_detections"); conn.commit(); conn.close()
    cfg = rules.config(); cfg.update({"large_trade_min_usd": 1000, "large_trade_vol_multiple": 3.0, "large_trade_size_multiple": 2.0})
    start = 1_789_100_000
    rows = _c5(start, 90)
    for i in (60, 70, 80):                                                           # three bursts, 50 minutes apart
        rows[i] = {**rows[i], "v": 20000.0, "n": 50, "c": 103.0}
    now = datetime(2026, 9, 16, 12, 0, tzinfo=IST)
    # first live run judges only what is closed at t=61 candles; then the job is late by 45 minutes
    dets, _ = detect.scan_bursts("BTC", rows[:61], cfg, start + 61 * 300, True, now)
    assert [int(d["candle_t"]) for d in dets] == [start + 60 * 300]
    fresh = detect.record(dets, now); assert len(fresh) == 1
    late = detect.scan_bursts("BTC", rows, cfg, start + 90 * 300, True, now)[0]      # 29 candles closed since: both later bursts, nothing repeated
    assert [int(d["candle_t"]) for d in late] == [start + 70 * 300, start + 80 * 300]
    assert len(detect.record(late, now)) == 2
    again = detect.scan_bursts("BTC", rows, cfg, start + 90 * 300, True, now)[0]
    assert again == [] and detect.record(dets + late, now) == [], "the same minute run twice produces nothing new"


def test_milestone_is_read_off_the_price_path_not_a_point_sample():
    from backend.alerts2.detect import path_crossings
    # BTC spikes through 78,000 to 78,120 and closes back at 77,950 inside one 5-minute candle
    spike = [{"t": 1, "o": 77900, "h": 78120, "l": 77880, "c": 77950}]
    x = path_crossings(spike, 77900, 1000)
    assert [(c["direction"], c["level"]) for c in x] == [], "up through 78k and straight back down nets to no crossing"
    held = [{"t": 1, "o": 77900, "h": 78120, "l": 77880, "c": 78050}]
    assert [(c["direction"], c["level"]) for c in path_crossings(held, 77900, 1000)] == [("up", 78000)]
    # a fast fall through two bands in one candle reports both, in order
    crash = [{"t": 2, "o": 78050, "h": 78060, "l": 75900, "c": 76010}]
    assert [(c["direction"], c["level"]) for c in path_crossings(crash, 78050, 1000)] == [("down", 78000), ("down", 77000)]
    # the reference is the previous close, so a gap between runs is not lost
    gap = [{"t": 3, "o": 80010, "h": 80020, "l": 79990, "c": 80005}]
    assert [(c["direction"], c["level"]) for c in path_crossings(gap, 78990, 1000)] == [("up", 79000), ("up", 80000)]


def test_quiet_hours_hold_perishable_facts_instead_of_dropping_them(monkeypatch):
    from backend.alerts2 import discovery, timing, detect, service
    from backend import approvals
    from backend.database import get_db, set_setting
    set_setting("mock_mode", "true"); discovery.register(); detect.init_tables()
    conn = get_db()
    for t in ("ma2_queue", "ma2_discovery_fires", "ma2_window_days", "ma2_detections", "ma2_watermarks"):
        conn.execute(f"DELETE FROM {t}")
    conn.execute("DELETE FROM ma2_state WHERE key='discovery'"); conn.commit(); conn.close()
    monkeypatch.setattr(discovery.sig_mod, "hl_candles", lambda *a, **k: [])
    monkeypatch.setattr(discovery.sig_mod, "mids", lambda: {})
    from backend.market import sources
    monkeypatch.setattr(sources, "klines", lambda *a, **k: [])
    import time as _t, os
    if os.path.exists(discovery._whale_path()):
        os.remove(discovery._whale_path())                                       # trades left by earlier tests
    discovery.ingest_whales([{"token": "SOL", "side": "sell", "size_usd": 6_000_000, "ts": _t.time()}], actor="test")
    p = discovery.propose("night", days=0, signals=["large_trades"], created_by="test")
    approvals.approve_and_execute(p["proposal_id"], decided_by="lead")
    night = datetime(2026, 9, 16, 23, 30, tzinfo=IST)
    r = discovery.run("live", now=night, ctx=_disc_ctx())
    assert r["sent"] == 0 and r["held_quiet_hours"] == 1, "held, not lost"
    q = [x for x in timing.queue_view() if x["status"] == "queued"]
    assert q and q[0]["due_at"].startswith("2026-09-17T07:00"), "the internal profile's quiet hours end at 07:00"
    assert discovery.release(datetime(2026, 9, 17, 6, 59, tzinfo=IST), "test")["fired"] == 0
    assert discovery.release(datetime(2026, 9, 17, 7, 1, tzinfo=IST), "test")["fired"] == 1, "delivered the moment quiet hours end"


def test_held_milestone_is_dropped_if_the_price_fell_back(monkeypatch):
    from backend.alerts2 import discovery, timing, rules
    from backend.database import get_db
    conn = get_db(); conn.execute("DELETE FROM ma2_queue"); conn.commit(); conn.close()
    now = datetime(2026, 9, 16, 23, 0, tzinfo=IST)
    timing.enqueue({"signal": "milestone", "token": "BTC", "direction": "up", "value": 78000, "title": "t", "body": "b"}, now, rules.config(), actor="test",
                   due_at=datetime(2026, 9, 17, 8, 0, tzinfo=IST))
    monkeypatch.setattr(discovery.sig_mod, "mids", lambda: {"BTC": 77800.0})
    out = discovery.release(datetime(2026, 9, 17, 8, 5, tzinfo=IST), "test", regime="chop")
    assert out["fired"] == 0 and [x["status"] for x in timing.queue_view()][0] == "stale", "a crossing that reversed overnight is not announced"


def test_fires_are_written_the_moment_they_happen(monkeypatch):
    """A crash after delivery must not double-send on the next tick: the ledger row exists before the loop moves on."""
    from backend.alerts2 import discovery
    from backend.database import get_db
    from datetime import datetime as _dt
    conn = get_db(); conn.execute("DELETE FROM ma2_discovery_fires"); conn.commit(); conn.close()
    now = _dt(2026, 9, 16, 12, 0, tzinfo=IST)
    discovery._write_fire(None, now, {"signal": "btc_move", "token": "BTC", "product": "futures", "direction": "up", "value": 3.1}, {"title": "t", "body": "b"}, "recorded_mock")
    assert discovery.fired_today(now)["btc_move|BTC|up"] == 1


def test_coverage_replay_finds_what_a_live_path_recorded_and_flags_what_it_did_not():
    from backend.alerts2 import detect, rules
    from backend.database import get_db
    conn = get_db(); conn.execute("DELETE FROM ma2_detections"); conn.commit(); conn.close()
    cfg = rules.config(); cfg.update({"large_trade_min_usd": 1000, "large_trade_vol_multiple": 3.0, "large_trade_size_multiple": 2.0, "milestone_bands": {}, "discovery_move_tokens": []})
    day = "2026-09-16"
    d0 = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=IST).timestamp()
    rows = _c5(int(d0) - 48 * 300, 48 + 288)                                       # a full day plus the baseline before it
    for i in (100, 200):
        rows[i] = {**rows[i], "v": 20000.0, "n": 50, "c": 103.0}
    now = datetime(2026, 9, 17, 1, 0, tzinfo=IST)
    detect.record([{"det_key": f"large_trades|ETH|{int(rows[100]['t'])}", "signal": "large_trades", "token": "ETH", "candle_t": rows[100]["t"], "value": 1}], now)
    c = detect.coverage(day, cfg, ["ETH"], lambda t: rows, lambda t: [])
    assert c["expected"] == 2 and c["recorded"] == 1 and c["missed_count"] == 1
    assert c["missed"][0]["det_key"] == f"large_trades|ETH|{int(rows[200]['t'])}"


def test_alerts_job_runs_every_five_minutes_and_is_never_starved():
    from backend import refresher
    j = next(x for x in refresher.JOBS if x["name"] == "market_alerts")
    assert j["minutes"] == 5 and j.get("priority") is True


def test_catch_up_records_old_facts_but_only_announces_fresh_ones(monkeypatch):
    """After an outage the watermark catches up on everything; a burst from three hours ago is recorded as stale, not pushed."""
    from backend.alerts2 import discovery, detect, rules
    from backend import approvals
    from backend.database import get_db, set_setting
    set_setting("mock_mode", "true"); discovery.register(); detect.init_tables()
    conn = get_db()
    for t in ("ma2_queue", "ma2_discovery_fires", "ma2_window_days", "ma2_detections", "ma2_watermarks"):
        conn.execute(f"DELETE FROM {t}")
    conn.execute("DELETE FROM ma2_state WHERE key='discovery'"); conn.commit(); conn.close()
    import os
    if os.path.exists(discovery._whale_path()):
        os.remove(discovery._whale_path())
    now = datetime(2026, 9, 16, 12, 0, tzinfo=IST)
    start = int(now.timestamp()) - 340 * 300
    rows = _c5(start, 340)
    old_i, fresh_i = 340 - 36, 340 - 3                                              # 3 hours ago, and 15 minutes ago
    for i in (old_i, fresh_i):
        rows[i] = {**rows[i], "v": 60000.0, "n": 40, "c": 103.0}
    monkeypatch.setattr(discovery.sig_mod, "hl_candles", lambda tok, interval, n: rows if (tok == "BTC" and interval == "5m") else [])
    monkeypatch.setattr(discovery.sig_mod, "mids", lambda: {})
    monkeypatch.setattr(discovery.sig_mod, "zscore", lambda *a, **k: None)
    from backend.market import sources
    monkeypatch.setattr(sources, "klines", lambda *a, **k: [])
    monkeypatch.setattr(rules, "DEFAULTS", {**rules.DEFAULTS, "large_trade_min_usd": 1000, "large_trade_vol_multiple": 3.0, "large_trade_size_multiple": 2.0, "milestone_bands": {}})
    p = discovery.propose("catchup", days=0, signals=["large_trades"], created_by="test")
    approvals.approve_and_execute(p["proposal_id"], decided_by="lead")
    ctx = {"crypto_markets": [{"symbol": "BTC", "price": 103.0, "vol_24h_usd": 9e9}], "crypto": {"regime": {"label": "chop"}}}
    r = discovery.run("live", now=now, ctx=ctx)
    dec = {int(d["candle_t"]): d for d in r["summary"]["decisions"] if d["signal"] == "large_trades"}
    assert dec[rows[fresh_i]["t"]]["decision"] == "recorded_mock"
    assert dec[rows[old_i]["t"]]["reason"] == "stale_at_detection"
    outcomes = detect.outcomes_since(now - timedelta(hours=1))
    assert outcomes.get("sent") == 1 and outcomes.get("suppressed:stale_at_detection") == 1, "both were judged; only the fresh one went out"
    assert discovery.run("live", now=now, ctx=ctx)["summary"]["already_recorded"] == 0 and detect.watermark("large_trades|BTC|5m") == rows[-1]["t"]


def test_the_same_round_level_is_not_announced_twice_in_a_chop():
    from backend.alerts2 import discovery, rules
    from backend.database import get_db
    conn = get_db(); conn.execute("DELETE FROM ma2_discovery_fires"); conn.execute("DELETE FROM ma2_queue"); conn.commit(); conn.close()
    now = datetime(2026, 9, 16, 12, 0, tzinfo=IST)
    discovery._write_fire(None, now - timedelta(hours=2), {"signal": "milestone", "token": "BTC", "direction": "up", "value": 76000}, {"title": "t", "body": "b"}, "recorded_mock")
    assert discovery._level_recent("BTC", 76000, now, rules.config()) is True
    assert discovery._level_recent("BTC", 77000, now, rules.config()) is False
    assert discovery._level_recent("BTC", 76000, now + timedelta(hours=7), rules.config()) is False, "after the cooldown it can be news again"


# ── the launch brief and the staged rollout ───────────────────────────────────
def test_launch_brief_contains_every_push_word_for_word_and_the_exact_draft():
    from backend.alerts2 import brief, discovery, rules
    b = brief.build(days=0, with_dry_run=False)
    keys = {c["template"] for c in b["copy"]}
    assert keys >= {"whale:buy", "whale:sell", "burst:up", "burst:down", "most_traded:any", "milestone:up", "milestone:down", "market_move:up", "market_move:down", "ath:up", "atl:down"}
    for c in b["copy"]:
        assert c["title"] and c["body"] and c["sample_title"] and "{" not in c["sample_title"], "samples are rendered, not raw placeholders"
        assert c["lint"] == "passes" and not c["blocks"]
    assert {s["signal"] for s in b["signals"]} == set(discovery.SIGNALS), "the internal brief lists the agent's own picks as a signal"
    assert all(s["when"] and any(ch.isdigit() for ch in s["when"]) for s in b["signals"]), "every trigger is described with its actual numbers"
    assert b["moengage_draft"]["campaign_delivery_type"] == "BUSINESS_EVENT_TRIGGERED" and b["moengage_draft"]["basic_details"]["business_event"] == "MA2_Discovery_INTERNAL"
    assert [c["event"] for c in b["cohorts"]] == ["MA2_Discovery_INTERNAL"], "the default brief is the employee cohort alone"
    assert b["experiment"]["control_group_pct"] == 5 and b["audience"]["stage"] == "internal", "the first brief is the internal-employee stage"
    assert b["audience"]["segment"].startswith(rules.config()["internal_segment"])
    md = brief.markdown(b)
    for needle in ("## Every push, word for word", "## Audience", "## Experiment design", "## The exact draft", "## Assumptions", "STAGE 1", "BUSINESS_EVENT_TRIGGERED"):
        assert needle in md
    for c in b["copy"]:
        assert c["body"] in md, "the markdown carries the templates themselves"


def test_launch_shows_the_brief_first_and_creates_nothing_until_reviewed(monkeypatch):
    from backend.alerts2 import discovery
    from backend import approvals
    from backend.database import set_setting
    set_setting("mock_mode", "true"); discovery.register()
    monkeypatch.setattr(discovery, "run", lambda *a, **k: {"ok": True, "detected": 0, "would_send": 0, "queued": 0, "held_quiet_hours": 0, "suppressed": 0, "summary": {}})
    before = len(approvals.list_proposals(limit=500))
    r = discovery.launch(days=0, signals=["most_traded"], created_by="test")
    assert r.get("needs_review") and r["brief"]["copy"] and "word for word" in r["brief_markdown"]
    assert len(approvals.list_proposals(limit=500)) == before, "no proposal exists until the brief is confirmed"
    r2 = discovery.launch(days=0, signals=["most_traded"], created_by="test", reviewed=True)
    assert r2["campaign_proposal_id"] and r2["experiment_proposal_id"] and r2["stage"] == "internal"
    for pid in (r2["campaign_proposal_id"], r2["experiment_proposal_id"]):
        p = approvals.get_proposal(pid)
        assert "## Every push, word for word" in (p["payload"].get("launch_brief_md") or ""), "the approver reads what the launcher read"
    camp = approvals.get_proposal(r2["campaign_proposal_id"])["payload"]
    assert camp["target_segment"] == "INTERNAL_EMPLOYEES" and camp["goal"]["control_group_pct"] == 5 and "INTERNAL" in camp["name"]


def test_whole_base_is_locked_until_the_internal_stage_has_run(monkeypatch):
    from backend.alerts2 import discovery
    from backend.database import get_db
    from datetime import datetime as _dt
    conn = get_db(); conn.execute("DELETE FROM ma2_discovery_fires"); conn.execute("DELETE FROM ma2_state WHERE key='discovery'"); conn.commit(); conn.close()
    monkeypatch.setattr(discovery, "run", lambda *a, **k: {"ok": True, "detected": 0, "would_send": 0, "queued": 0, "held_quiet_hours": 0, "suppressed": 0, "summary": {}})
    r = discovery.launch(audience="ALL_PUSH_ENABLED", days=0, signals=["most_traded"], created_by="test", reviewed=True)
    assert r.get("error") and "internal" in r["error"] and "stage" in r["error"] and not r.get("campaign_proposal_id")
    assert discovery.promotion()["ready"] is False
    # simulate the internal stage: five alerts over four days
    for i in range(5):
        discovery._write_fire(None, _dt(2026, 9, 10 + i, 12, 0, tzinfo=IST), {"signal": "most_traded", "token": "HYPE", "direction": "any", "value": 1}, {"title": "t", "body": "b"}, "recorded_mock")
    pr = discovery.promotion(now=_dt(2026, 9, 16, 12, 0, tzinfo=IST))
    assert pr["ready"] and pr["fires"] == 5 and pr["days"] >= 3
    conn = get_db(); conn.execute("DELETE FROM ma2_discovery_fires"); conn.commit(); conn.close()


def test_agent_launch_tool_cannot_skip_the_brief():
    from backend.llm import tools
    import backend.alerts2.discovery as d
    r = tools.market_alerts_launch_discovery(signals=["most_traded"])
    assert r.get("needs_review") and r["brief"]["copy"] and "brief_markdown" not in r
    b = tools.market_alerts_brief(signals=["milestone"])
    assert {c["template"] for c in b["copy"]} == {"milestone:up", "milestone:down"} and "moengage_draft" not in b


# ── the agent off the leash, on the internal cohort only ──────────────────────
def test_internal_profile_relaxes_caps_but_not_the_law():
    from backend.alerts2 import rules
    base, internal = rules.config(), rules.config(stage="internal")
    assert internal["discovery_daily_cap"] > base["discovery_daily_cap"] and internal["agent_autonomy"] is True and internal["evergreen_signals"] == []
    assert internal["large_trade_min_usd"] == base["large_trade_min_usd"] and internal["perishable_max_age_min"] == base["perishable_max_age_min"], "freshness and thresholds are not loosened"
    assert rules.config(stage="all").get("agent_autonomy") in (None, False)


def _fake_llm(payload):
    class _Cli:
        def __init__(self, *a, **k): pass
        def chat(self, messages, **k):
            return {"content": json.dumps(payload), "model": "fake/bulk"}
    return _Cli


def test_agent_pass_keeps_only_copy_that_passes_the_linter_and_picks_from_the_snapshot(monkeypatch):
    from backend.alerts2 import agent
    import backend.llm.provider as prov
    now = datetime(2026, 9, 16, 12, 0, tzinfo=IST)
    ctx = {"crypto_markets": [{"symbol": "BTC", "price": 78000, "chg_24h": 2.0, "vol_24h_usd": 9e9, "oi_usd": 3e9, "funding_apr_pct": 41.0}, {"symbol": "HYPE", "price": 40, "chg_24h": 9.0, "vol_24h_usd": 1e9}],
           "crypto": {"regime": {"label": "chop"}}, "crypto_movers_detail": {"crowded_long": [{"symbol": "BTC", "funding_apr_pct": 41.0}]}}
    cands = [{"det_key": "most_traded|HYPE|2026-09-16", "signal": "most_traded", "token": "HYPE", "direction": "any", "fields": {"token": "HYPE"}, "title": "Most traded today: HYPE", "body": "HYPE leads."}]
    payload = {"rewrites": [{"det_key": "most_traded|HYPE|2026-09-16", "title": "HYPE leads volume today, up 9%", "body": "Most traded futures token of the day. See the market or set an alert.", "why": "volume leader"},
                            {"det_key": "most_traded|HYPE|2026-09-16", "title": "HYPE will moon, buy now", "body": "guaranteed", "why": "x"}],
               "picks": [{"token": "BTC", "topic": "funding_crowding", "direction": "up", "title": "BTC funding is crowded long", "body": "Funding near 41% annualised: longs are paying up. Review open positions.", "why": "crowd", "evidence": "funding_apr 41.0"},
                         {"token": "PEPE", "topic": "listing", "direction": "any", "title": "PEPE listed", "body": "See the market.", "why": "x", "evidence": "1"},
                         {"token": "BTC", "topic": "level_watch", "direction": "up", "title": "BTC at 78,000: 5x leverage pays", "body": "Go long now.", "why": "x", "evidence": "78000"},
                         {"token": "HYPE", "topic": "volume_leader", "direction": "up", "title": "HYPE volume", "body": "No number here.", "why": "x", "evidence": "big"}]}
    monkeypatch.setattr(prov, "LLMClient", _fake_llm(payload))
    out = agent.pass_once(cands, ctx, {"agent_max_picks": 4}, now)
    assert list(out["rewrites"]) == ["most_traded|HYPE|2026-09-16"] and out["rewrites"]["most_traded|HYPE|2026-09-16"]["title"].startswith("HYPE leads")
    assert [p["token"] for p in out["picks"]] == ["BTC"] and out["picks"][0]["det_key"] == "agent_pick|BTC|funding_crowding|2026-09-16"
    reasons = {str(r.get("pick") or r.get("det_key")): r["why"] for r in out["rejected"]}
    assert reasons["PEPE"] == "token not in snapshot" and "leverage" in reasons["BTC"] and reasons["HYPE"] == "no number in evidence"


def test_agent_pass_is_silent_when_the_model_is_down(monkeypatch):
    from backend.alerts2 import agent
    import backend.llm.provider as prov
    class _Down:
        def __init__(self, *a, **k): pass
        def chat(self, *a, **k): raise RuntimeError("provider offline")
    monkeypatch.setattr(prov, "LLMClient", _Down)
    out = agent.pass_once([], {"crypto_markets": []}, {}, datetime(2026, 9, 16, 12, 0, tzinfo=IST))
    assert out["rewrites"] == {} and out["picks"] == [] and "skipped" in out["note"]


def test_internal_live_run_lets_the_agent_send_picks_without_a_click(monkeypatch):
    from backend.alerts2 import discovery, detect, rules
    from backend import approvals
    from backend.database import get_db, set_setting
    import backend.llm.provider as prov
    set_setting("mock_mode", "true"); discovery.register(); detect.init_tables()
    conn = get_db()
    for t in ("ma2_queue", "ma2_discovery_fires", "ma2_window_days", "ma2_detections", "ma2_watermarks"):
        conn.execute(f"DELETE FROM {t}")
    conn.execute("DELETE FROM ma2_state WHERE key IN ('discovery','agent_last_run')"); conn.commit(); conn.close()
    import os
    if os.path.exists(discovery._whale_path()):
        os.remove(discovery._whale_path())
    monkeypatch.setattr(discovery.sig_mod, "hl_candles", lambda *a, **k: [])
    monkeypatch.setattr(discovery.sig_mod, "mids", lambda: {})
    from backend.market import sources
    monkeypatch.setattr(sources, "klines", lambda *a, **k: [])
    payload = {"rewrites": [], "picks": [{"token": "BTC", "topic": "funding_crowding", "direction": "up", "title": "BTC funding is crowded long", "body": "Funding near 41% annualised: longs are paying up. Review open positions.", "why": "crowd", "evidence": "41.0"}]}
    monkeypatch.setattr(prov, "LLMClient", _fake_llm(payload))
    p = discovery.propose("internal unhinged", days=0, signals=["most_traded"], created_by="test")     # audience defaults to the employee segment
    approvals.approve_and_execute(p["proposal_id"], decided_by="lead")
    assert discovery.experiment()["stage"] == "internal"
    ctx = {**_disc_ctx(), "crypto_markets": _disc_ctx()["crypto_markets"] + [], "crypto_movers_detail": {"crowded_long": [{"symbol": "BTC", "funding_apr_pct": 41.0}]}}
    r = discovery.run("live", now=datetime(2026, 9, 16, 12, 0, tzinfo=IST), ctx=ctx)
    dec = {d["signal"]: d for d in r["summary"]["decisions"]}
    assert dec["agent_pick"]["decision"] == "recorded_mock" and dec["agent_pick"]["by_agent"] and "41%" in dec["agent_pick"]["body"]
    assert dec["most_traded"]["decision"] == "recorded_mock", "on the internal profile the most-traded token goes out at once instead of waiting for a window"
    assert r["summary"]["profile"] == "internal" and r["summary"]["agent"]["model"] == "fake/bulk"
    r2 = discovery.run("live", now=datetime(2026, 9, 16, 12, 5, tzinfo=IST), ctx=ctx)
    assert r2["summary"]["agent"] is None, "one model call per agent_every_min, not per tick"
    assert discovery.fired_today(datetime(2026, 9, 16, 12, 0, tzinfo=IST))["agent_pick"] == 1


# ── one campaign per cohort; MoEngage delivers, the engine routes the event ───
def test_cohort_plan_routes_each_signal_to_the_cohorts_that_hear_it():
    from backend.alerts2 import discovery
    plan = discovery.cohorts()
    ids = [c["id"] for c in plan]
    assert ids[0] == "internal" and {"futures_active", "spot_active", "all"} <= set(ids)
    assert plan[0]["segment"] == "INTERNAL_EMPLOYEES" and plan[0]["event"] == "MA2_Discovery_INTERNAL" and plan[0]["stage"] == "internal"
    whale = {"signal": "large_trades"}; ath = {"signal": "ath_atl"}; pick = {"signal": "agent_pick"}
    assert {c["id"] for c in discovery.route(whale, plan)} == {"internal", "futures_active"}, "a whale story never reaches spot-only or the broad base"
    assert {c["id"] for c in discovery.route(ath, plan)} == {"internal", "futures_active", "spot_active", "all"}
    assert [c["id"] for c in discovery.route(pick, plan)] == ["internal"], "the agent's own picks stay on the employee cohort"
    assert len({c["event"] for c in plan}) == len(plan), "one business event per cohort, so MoEngage needs no attribute filters"


def test_launch_creates_one_moengage_draft_per_cohort_and_locks_the_rest(monkeypatch):
    from backend.alerts2 import discovery
    from backend import approvals
    from backend.database import get_db, set_setting
    set_setting("mock_mode", "true"); discovery.register()
    conn = get_db(); conn.execute("DELETE FROM ma2_discovery_fires"); conn.execute("DELETE FROM ma2_state WHERE key='discovery'"); conn.commit(); conn.close()
    monkeypatch.setattr(discovery, "run", lambda *a, **k: {"ok": True, "detected": 0, "would_send": 0, "queued": 0, "held_quiet_hours": 0, "suppressed": 0, "summary": {}})
    locked = discovery.launch(days=0, signals=["most_traded"], created_by="test", reviewed=True, cohort_ids=["internal", "futures_active"])
    assert locked.get("error") and locked["locked"] == ["futures_active"], "only the employees until the internal stage has run"
    r = discovery.launch(days=0, signals=["most_traded"], created_by="test", reviewed=True, cohort_ids=["internal"])
    assert [c["cohort"] for c in r["campaign_proposals"]] == ["internal"] and r["campaign_proposals"][0]["event"] == "MA2_Discovery_INTERNAL"
    camp = approvals.get_proposal(r["campaign_proposals"][0]["proposal_id"])["payload"]
    assert camp["schedule"]["business_event"] == "MA2_Discovery_INTERNAL" and camp["target_segment"] == "INTERNAL_EMPLOYEES" and "INTERNAL" in camp["name"]
    exp = approvals.get_proposal(r["experiment_proposal_id"])["payload"]
    assert exp["cohorts"] == ["internal"] and "## One MoEngage campaign per cohort" in exp["launch_brief_md"]
    # after the internal stage has earned it, three cohorts → three drafts on three events
    from datetime import datetime as _dt
    for i in range(5):
        discovery._write_fire(None, _dt(2026, 9, 10 + i, 12, 0, tzinfo=IST), {"signal": "most_traded", "token": "HYPE", "direction": "any", "value": 1}, {"title": "t", "body": "b"}, "recorded_mock")
    r2 = discovery.launch(days=0, signals=["most_traded"], created_by="test", reviewed=True, cohort_ids=["futures_active", "spot_active", "all"])
    assert [c["event"] for c in r2["campaign_proposals"]] == ["MA2_Discovery_FUTURES", "MA2_Discovery_SPOT", "MA2_Discovery"]
    conn = get_db(); conn.execute("DELETE FROM ma2_discovery_fires"); conn.commit(); conn.close()


def test_live_run_fires_each_alert_on_every_active_cohort_event(monkeypatch):
    from backend.alerts2 import discovery, detect, service
    from backend import approvals
    from backend.database import get_db, set_setting
    set_setting("mock_mode", "true"); discovery.register(); detect.init_tables()
    conn = get_db()
    for t in ("ma2_queue", "ma2_discovery_fires", "ma2_window_days", "ma2_detections", "ma2_watermarks"):
        conn.execute(f"DELETE FROM {t}")
    conn.execute("DELETE FROM ma2_state WHERE key IN ('discovery','agent_last_run','heartbeat_last')"); conn.commit(); conn.close()
    import os
    if os.path.exists(discovery._whale_path()):
        os.remove(discovery._whale_path())
    monkeypatch.setattr(discovery.sig_mod, "hl_candles", lambda *a, **k: [])
    monkeypatch.setattr(discovery.sig_mod, "mids", lambda: {})
    from backend.market import sources
    monkeypatch.setattr(sources, "klines", lambda *a, **k: [])
    set_setting("ma2_agent_autonomy", "false")
    sent = []
    monkeypatch.setattr(service, "_deliver_business_event", lambda ev, attrs: (sent.append((ev, attrs.get("cohort"), attrs.get("signal"))) or {"status": "recorded_mock"}))
    p = discovery.propose("cohorts", days=0, signals=["most_traded"], audience="ALL_PUSH_ENABLED", created_by="test", override_reason="cohort routing test", cohort_ids=["futures_active", "spot_active", "all"])
    approvals.approve_and_execute(p["proposal_id"], decided_by="lead")
    r = discovery.run("live", now=datetime(2026, 9, 16, 12, 0, tzinfo=IST), ctx=_disc_ctx())
    # the whole-base profile queues most_traded for the window; release it and check where it went
    out = discovery.release(datetime(2026, 9, 16, 19, 45, tzinfo=IST), "test", regime="chop")
    assert out["fired"] == 1
    assert {(e, c) for e, c, s in sent if s == "most_traded"} == {("MA2_Discovery_FUTURES", "futures_active"), ("MA2_Discovery_SPOT", "spot_active"), ("MA2_Discovery", "all")}
    set_setting("ma2_agent_autonomy", "true")


def test_heartbeat_and_keepalive_are_moengage_native():
    from backend.alerts2 import discovery, service
    from backend.database import set_setting
    set_setting("mock_mode", "true")
    r = discovery.heartbeat(datetime(2026, 9, 16, 12, 0, tzinfo=IST))
    assert r["status"] == "recorded_mock"
    k = discovery.keepalive()
    assert k["heartbeat_event"] == "MA2_Engine_Heartbeat" and k["heartbeat_last"].startswith("2026-09-16T12:00")
    assert any("no market-data ingestion" in x for x in k["engine_still_needed_for"]), "honest about what MoEngage cannot do"
    checks = {c["check"]: c for c in discovery.preflight()}
    assert "Heartbeat" in checks and "flow" in checks["Heartbeat"]["fix"] and "Engine as a service" in checks


# ── MoEngage Inform for the employee cohort: direct sends, no campaign ─────────
def test_inform_payload_is_idempotent_and_carries_the_facts():
    from backend.alerts2 import service, cohort
    attrs = {"title": "BTC crossed $78,000", "body": "BTC is trading at $78,050 after crossing $78,000. See the chart.", "token": "BTC", "product": "futures", "signal": "milestone",
             "direction": "up", "landing": "token_page", "level": 78000, "run_id": 7, "event_key": "x"}
    p = service.inform_payload("636b77e6e2cf83277195fb60", "emp_001", "milestone|BTC|up78000@1789", attrs, "MA2_Discovery_Internal")
    assert p["alert_id"] == "636b77e6e2cf83277195fb60" and p["user_id"] == "emp_001" and p["alert_reference_name"] == "MA2_Discovery_Internal"
    assert p["transaction_id"] == service.inform_payload("636b77e6e2cf83277195fb60", "emp_001", "milestone|BTC|up78000@1789", attrs)["transaction_id"], "same alert, same user → same transaction id, so MoEngage refuses a duplicate"
    assert "emp_001" not in p["transaction_id"] and cohort.hash_id("emp_001")[:12] in p["transaction_id"]
    pa = p["payloads"]["PUSH"]["personalized_attributes"]
    assert pa["title"].startswith("BTC crossed") and pa["level"] == "78000" and "run_id" not in pa and "event_key" not in pa


def test_employee_id_list_refuses_personal_details():
    from backend.alerts2 import cohort
    with pytest.raises(cohort.CohortError):
        cohort.save_internal_users(["emp_1", "someone@coindcx.com"], actor="test")
    with pytest.raises(cohort.CohortError):
        cohort.save_internal_users(["9876543210"], actor="test")
    m = cohort.save_internal_users(["emp_1", "emp_2", "emp_1"], actor="test")
    assert m["count"] == 2 and cohort.internal_users() == ["emp_1", "emp_2"]


def test_internal_cohort_goes_through_inform_when_configured(monkeypatch):
    from backend.alerts2 import discovery, detect, service, cohort
    from backend import approvals
    from backend.database import get_db, set_setting
    set_setting("mock_mode", "true"); discovery.register(); detect.init_tables()
    conn = get_db()
    for t in ("ma2_queue", "ma2_discovery_fires", "ma2_window_days", "ma2_detections", "ma2_watermarks"):
        conn.execute(f"DELETE FROM {t}")
    conn.execute("DELETE FROM ma2_state WHERE key IN ('discovery','agent_last_run')"); conn.commit(); conn.close()
    import os
    if os.path.exists(discovery._whale_path()):
        os.remove(discovery._whale_path())
    cohort.save_internal_users(["emp_1", "emp_2", "emp_3"], actor="test")
    set_setting("ma2_internal_delivery", "inform"); set_setting("ma2_inform_alert_id", "alert123"); set_setting("ma2_agent_autonomy", "false")
    monkeypatch.setattr(discovery.sig_mod, "hl_candles", lambda *a, **k: [])
    monkeypatch.setattr(discovery.sig_mod, "mids", lambda: {})
    from backend.market import sources
    monkeypatch.setattr(sources, "klines", lambda *a, **k: [])
    calls = []
    monkeypatch.setattr(service, "_deliver_inform", lambda det_key, attrs, users: (calls.append((det_key, tuple(users), attrs["cohort"])) or {"status": "recorded_mock", "sent": len(users), "failed": 0, "channel": "inform"}))
    monkeypatch.setattr(service, "_deliver_business_event", lambda ev, attrs: (_ for _ in ()).throw(AssertionError("the internal cohort must not use the event path when Inform is configured")))
    try:
        p = discovery.propose("inform internal", days=0, signals=["most_traded"], created_by="test")
        approvals.approve_and_execute(p["proposal_id"], decided_by="lead")
        r = discovery.run("live", now=datetime(2026, 9, 16, 12, 0, tzinfo=IST), ctx=_disc_ctx())
        assert r["sent"] >= 1 and calls and calls[0][1] == ("emp_1", "emp_2", "emp_3") and calls[0][2] == "internal"
        assert discovery.internal_delivery()["mode"] == "inform" and discovery.keepalive()["internal_delivery"].startswith("MoEngage Inform")
        checks = {c["check"]: c for c in discovery.preflight()}
        assert checks["Inform (internal cohort)"]["ok"] and "3 employee id" in checks["Inform (internal cohort)"]["detail"]
    finally:
        set_setting("ma2_internal_delivery", "event"); set_setting("ma2_inform_alert_id", ""); set_setting("ma2_agent_autonomy", "true")
