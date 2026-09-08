import random
from datetime import date, timedelta
from backend.anomaly import record_snapshot, detect_anomalies, get_history


def _camp(cid, ctr, delivery=96.0, sent=50000, rev=1000.0):
    delivered = int(sent * delivery / 100)
    return {"id": cid, "name": f"Camp {cid}", "channel": "Push", "status": "Active", "sent_count": sent, "delivered_count": delivered,
            "delivery_rate": delivery, "opened_count": int(delivered * ctr / 100), "ctr": ctr, "conversions": 100, "conversion_rate": 1.0, "revenue_generated": rev}


def test_detects_spike_and_drop_against_own_history():
    random.seed(7)
    today = date.today()
    for i in range(30, 0, -1):
        d = (today - timedelta(days=i)).isoformat()
        record_snapshot([_camp("A", 6.0 + random.uniform(-0.4, 0.4)), _camp("B", 12.0 + random.uniform(-0.6, 0.6)), _camp("C", 3.0 + random.uniform(-0.2, 0.2), delivery=97 + random.uniform(-0.5, 0.5))], source="test", snapshot_date=d)
    # today: A collapses, B spikes, C delivery drops
    record_snapshot([_camp("A", 1.2), _camp("B", 25.0), _camp("C", 3.0, delivery=81.0)], source="test", snapshot_date=today.isoformat())
    rep = detect_anomalies(source="test", persist=False)
    by = {(e["campaign_id"], e["metric"]): e for e in rep["anomalies"]}
    assert ("A", "ctr") in by and by[("A", "ctr")]["direction"] == "down" and by[("A", "ctr")]["severity"] == "critical"
    assert ("B", "ctr") in by and by[("B", "ctr")]["direction"] == "up"
    assert ("C", "delivery_rate") in by and by[("C", "delivery_rate")]["severity"] == "critical"
    assert rep["campaigns_evaluated"] == 3 and not rep["campaigns_with_insufficient_history"]
    assert all(e["confidence"] in ("high", "medium") for e in rep["anomalies"])


def test_no_false_alarm_on_normal_day_and_small_denominator():
    random.seed(3)
    today = date.today()
    for i in range(20, 0, -1):
        record_snapshot([_camp("N", 8.0 + random.uniform(-0.5, 0.5))], source="quiet", snapshot_date=(today - timedelta(days=i)).isoformat())
    record_snapshot([_camp("N", 8.3), _camp("tiny", 40.0, sent=30)], source="quiet", snapshot_date=today.isoformat())
    rep = detect_anomalies(source="quiet", persist=False)
    assert not [e for e in rep["anomalies"] if e["campaign_id"] == "N" and e["metric"] == "ctr"]
    assert not [e for e in rep["anomalies"] if e["campaign_id"] == "tiny"]      # denominator gate


def test_hard_rules_without_history():
    today = date.today().isoformat()
    record_snapshot([_camp("fresh", 5.0, delivery=70.0)], source="fresh", snapshot_date=today)
    rep = detect_anomalies(source="fresh", persist=False)
    assert any(e["method"] == "hard_rule_delivery" for e in rep["anomalies"])
    assert "fresh" in rep["campaigns_with_insufficient_history"]
    assert len(get_history("fresh", "fresh")) == 1


def test_weekend_seasonality_does_not_cause_false_alarms():
    random.seed(5)
    today = date.today()
    for i in range(35, 0, -1):
        d = today - timedelta(days=i)
        lift = 1.08 if d.weekday() >= 5 else 1.0
        record_snapshot([_camp("S", 12.0 * lift * random.gauss(1, 0.05))], source="season", snapshot_date=d.isoformat())
    lift = 1.08 if today.weekday() >= 5 else 1.0
    record_snapshot([_camp("S", 12.0 * lift * 0.96)], source="season", snapshot_date=today.isoformat())   # ordinary 4% dip
    rep = detect_anomalies(source="season", persist=False)
    assert not [e for e in rep["anomalies"] if e["campaign_id"] == "S" and e["metric"] in ("ctr", "opened_count")], rep["anomalies"]
    record_snapshot([_camp("S", 12.0 * lift * 0.55)], source="season", snapshot_date=today.isoformat())   # real 45% collapse
    rep = detect_anomalies(source="season", persist=False)
    assert any(e["campaign_id"] == "S" and e["metric"] == "ctr" and e["severity"] == "critical" for e in rep["anomalies"])
