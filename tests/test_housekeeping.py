import json
from backend.database import get_db


def test_ideas_expire_by_ttl_hook_and_anomaly_and_revive_on_resurface():
    from backend import growth
    growth.init_growth_tables()
    conn = get_db(); conn.execute("DELETE FROM growth_ideas WHERE title LIKE 'HK test%'"); conn.commit(); conn.close()
    r = growth.upsert_ideas([
        {"kind": "market_play", "title": "HK test: BTC +6% push to watchers", "why": "move", "how": "push", "market_hook_id": "mover_crypto_BTC"},
        {"kind": "fix", "title": "HK test: fix delivery on Winback", "why": "delivery fell", "how": "clean", "data": {"campaign_name": "Winback"}},
        {"kind": "growth_hack", "title": "HK test: evergreen tactic", "why": "works", "how": "do"},
    ], "rules")
    assert r["added"] == 3
    conn = get_db()
    rows = {x["title"]: x for x in conn.execute("SELECT title, expires_at FROM growth_ideas WHERE title LIKE 'HK test%'").fetchall()}
    assert all(v["expires_at"] for v in rows.values())
    # simulate time passing for the market play only
    conn.execute("UPDATE growth_ideas SET expires_at=datetime('now','-1 hour') WHERE title='HK test: BTC +6% push to watchers'"); conn.commit(); conn.close()
    out = growth.expire_stale(current_hook_ids=["mover_crypto_BTC"], active_anomaly_campaigns=["Other campaign"])
    assert out["expired_ttl"] >= 1 and out["expired_anomaly_cleared"] >= 1
    statuses = {x["title"]: x["status"] for x in get_db().execute("SELECT title, status FROM growth_ideas WHERE title LIKE 'HK test%'").fetchall()}
    assert statuses["HK test: BTC +6% push to watchers"] == "expired" and statuses["HK test: fix delivery on Winback"] == "expired" and statuses["HK test: evergreen tactic"] == "new"
    assert all(i["title"] != "HK test: BTC +6% push to watchers" for i in growth.list_ideas(status="new", limit=500))
    # data surfaces the hook again → revived
    growth.upsert_ideas([{"kind": "market_play", "title": "HK test: BTC +6% push to watchers", "why": "move again", "how": "push", "market_hook_id": "mover_crypto_BTC"}], "rules")
    assert get_db().execute("SELECT status FROM growth_ideas WHERE title='HK test: BTC +6% push to watchers'").fetchone()["status"] == "new"
    # hook gone → expired
    out2 = growth.expire_stale(current_hook_ids=[], active_anomaly_campaigns=None)
    assert out2["expired_hook_gone"] >= 1
    # purge old expired rows
    conn = get_db(); conn.execute("UPDATE growth_ideas SET updated_at=datetime('now','-40 days') WHERE title LIKE 'HK test%' AND status='expired'"); conn.commit(); conn.close()
    assert growth.expire_stale()["purged"] >= 1


def test_stale_proposals_expire_and_plans_archive():
    from backend import approvals, plans
    from backend.moengage.executors import register_all
    from backend.database import set_setting
    register_all(); set_setting("mock_mode", "true")
    p = approvals.propose("create_campaign", "HK stale market push", {"name": "HK_stale", "channel": "push", "target_segment": "HVT_Sep26", "ttl_hours": 4, "variants": [{"title": "t", "body": "b", "cta": "c"}],
                                                                       "goal": {"transition": "activated_habitual", "hypothesis": "h", "primary_kpi": "k", "target": "t", "guardrail_metric": "g", "control_group_pct": 20, "measurement_window_days": 7, "kill_criteria": "k"}}, created_by="test")
    conn = get_db(); conn.execute("UPDATE proposals SET created_at=datetime('now','-10 hours') WHERE id=?", (p["id"],)); conn.commit(); conn.close()
    assert approvals.expire_stale()["expired"] >= 1
    assert approvals.get_proposal(p["id"])["status"] == "expired"
    plans.create_plan({"title": "HK old plan", "month": "2025-01", "objective": "o", "transition": "habitual_core", "cohort_family": "HVT", "sequence": [{"day": 0, "channel": "email", "purpose": "x"}], "kpi": {"primary": "k", "target": "t"}}, "test")
    assert plans.archive_past("2026-09") >= 1
