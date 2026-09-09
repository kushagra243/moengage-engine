import random
from datetime import date, timedelta
from backend.anomaly import record_snapshot
from backend.anomaly.diagnose import diagnose_campaign, diagnose_all, trend_stats


def _c(cid, ctr, delivery=96.0, conv=1.0, rev=1000.0, sent=50000):
    d = int(sent * delivery / 100)
    return {"id": cid, "name": f"C{cid}", "channel": "Push", "sent_count": sent, "delivered_count": d, "delivery_rate": delivery, "opened_count": int(d * ctr / 100), "ctr": ctr, "conversions": int(d * conv / 100), "conversion_rate": conv, "revenue_generated": rev}


def test_diagnosis_finds_stage_cause_and_options():
    random.seed(1); today = date.today()
    for i in range(30, 0, -1):
        dd = (today - timedelta(days=i)).isoformat()
        record_snapshot([_c("A", 8 + random.uniform(-.3, .3)), _c("B", 8 + random.uniform(-.3, .3)), _c("C", 8 + random.uniform(-.3, .3), conv=1.0)], source="dx", snapshot_date=dd)
    # A: deliverability collapse; B: CTR fatigue with delivery stable; C: conversion drop with CTR stable
    record_snapshot([_c("A", 8.0, delivery=80.0), _c("B", 5.0), _c("C", 8.0, conv=0.6)], source="dx", snapshot_date=today.isoformat())
    a = diagnose_campaign("A", "dx"); b = diagnose_campaign("B", "dx"); c = diagnose_campaign("C", "dx")
    assert a["funnel"]["moved_most"] == "delivery_rate" and a["likely_causes"][0]["cause"].startswith("Deliverability") and a["severity"] == "critical"
    assert any("Pause" in o["action"] for o in a["options"]) and all({"how_in_moengage", "effort", "expected_effect", "risk"} <= set(o) for o in a["options"])
    assert b["funnel"]["moved_most"] in ("ctr",) and "fatigue" in b["likely_causes"][0]["cause"].lower()
    assert c["funnel"]["moved_most"] == "conversion_rate" and "friction" in c["likely_causes"][0]["cause"].lower()
    st = trend_stats([{"snapshot_date": (today - timedelta(days=k)).isoformat(), "ctr": v} for k, v in zip(range(9, -1, -1), [8, 8, 8, 8, 8, 7.5, 7, 6.5, 6, 5.5])], "ctr")
    assert st["streak_days"] <= -4 and st["vs_7d_pct"] < 0
    ranked = diagnose_all("dx")
    assert ranked[0]["severity"] == "critical" and len(ranked) == 3
