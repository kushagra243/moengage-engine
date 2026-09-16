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
    assert again["sent"] == 0 and again["summary"]["suppression_reasons"].get("already_sent_today"), "the same signal and token does not repeat in a day"

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
    assert b["schedule"]["business_event"] == discovery.EVENT and b["goal"]["control_group_pct"] == 20
    assert any("liquidat" in e.lower() for e in b["exclusions"])

    r = discovery.launch(days=7, signals=["most_traded"], control_pct=20, created_by="test")
    camp = approvals.get_proposal(r["campaign_proposal_id"])
    assert camp["kind"] == "create_campaign" and camp["status"] == "pending"
    assert "{{BusinessEvent.title}}" in json.dumps(camp["payload"]), "copy comes from the event the engine fires"
    st = discovery.status()
    assert st["campaign"]["state"] == "pending" and [l["step"] for l in st["launch"]][0].startswith("MoEngage")

    approvals.approve_and_execute(r["campaign_proposal_id"], decided_by="lead")
    from backend.database import get_db
    conn = get_db(); row = conn.execute("SELECT id, control_group_pct, primary_kpi FROM experiments WHERE proposal_id=?", (r["campaign_proposal_id"],)).fetchone(); conn.close()
    assert row and row["control_group_pct"] == 20, "an approved campaign becomes a live experiment with a control group"


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
    conn = get_db(); conn.execute("DELETE FROM ma2_queue"); conn.execute("DELETE FROM ma2_discovery_fires"); conn.execute("DELETE FROM ma2_window_days"); conn.commit(); conn.close()
    monkeypatch.setattr(discovery.sig_mod, "hl_candles", lambda *a, **k: [])
    monkeypatch.setattr(discovery.sig_mod, "zscore", lambda *a, **k: None)
    from backend.market import sources
    monkeypatch.setattr(sources, "klines", lambda *a, **k: [])
    discovery.ingest_whales([{"token": "ETH", "side": "buy", "size_usd": 5_000_000, "ts": __import__("time").time()}], actor="test")
    p = discovery.propose("continuous", days=0, signals=["most_traded", "large_trades"], created_by="test")
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
    b = discovery.campaign_brief(continuous=True)
    assert b["target_segment"] == "ALL_PUSH_ENABLED" and b["goal"]["control_group_pct"] == 10 and b["goal"]["continuous"] is True
    assert tools.campaign_brief_check(b["goal"], b["variants"], "push", market_linked=True, ttl_hours=b["ttl_hours"])["ok"]
    assert any("liquidat" in e.lower() for e in b["exclusions"]) and any("dnd" in e.lower() or "unsub" in e.lower() for e in b["exclusions"])


# ── the draft MoEngage actually accepts ───────────────────────────────────────
def test_campaign_payload_matches_the_documented_v5_schema():
    """The engine used to post its own brief shape to /v5/campaigns, so no draft ever appeared in MoEngage."""
    from backend.alerts2 import discovery
    from backend.moengage.executors import v5_campaign_payload
    b = discovery.campaign_brief(continuous=True)
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
    assert set(checks) == {"Mode", "Campaigns API key", "Creator email", "Business event", "Campaign draft", "Engine firing"}
    assert checks["Mode"]["ok"] is False and "nothing reaches MoEngage" in checks["Mode"]["detail"]
    assert all(c["ok"] or c["fix"] for c in checks.values()), "anything blocked says how to unblock it"
