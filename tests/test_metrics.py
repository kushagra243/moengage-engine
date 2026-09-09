from backend import metrics as M


def test_delta_units_and_direction():
    assert M.delta("ctr", 4.1, 13.5)["text"] == "-9.4 pp" and M.delta("ctr", 4.1, 13.5)["better"] is False
    assert M.delta("delivery_rate", 96.0, 94.0)["text"] == "+2.0 pp" and M.delta("delivery_rate", 96.0, 94.0)["better"] is True
    assert M.delta("revenue_generated", 214000, 89000)["text"] == "+140%" and M.delta("revenue_generated", 214000, 89000)["better"] is True
    assert M.delta("sent_count", 100, 200)["text"] == "-50%" and M.delta("sent_count", 100, 200)["better"] is None
    assert M.delta("ctr", None, 1)["text"] == "—"


def test_unusual_and_urgency_and_headline():
    e = {"campaign_name": "Cart Abandonment", "metric": "ctr", "value": 4.1, "baseline": 13.5, "score": -358.0, "method": "modified_z", "severity": "critical", "impact": "bad", "n_history": 30}
    h = M.anomaly_headline(e)
    assert "click rate 4.1%" in h["headline"] and "usually about 13.5%" in h["headline"] and "-9.4 pp" in h["headline"] and h["unusual"] == 4
    assert "own last 30 days" in h["how_we_know"]
    assert M.urgency(e) == "act_today"
    assert M.urgency({**e, "impact": "good", "value": 25, "baseline": 13.5}) == "good_surprise"
    assert M.urgency({"metric": "delivery_rate", "value": 71, "baseline": 94, "score": -2, "method": "modified_z", "severity": "warning", "impact": "bad"}) == "act_today"
    assert M.urgency({"metric": "ctr", "value": 12, "baseline": 13.5, "score": -2.6, "method": "modified_z", "severity": "warning", "impact": "bad"}) == "watch"
    rep = M.enrich_anomalies({"anomalies": [e]})
    assert rep["anomalies"][0]["urgency_label"] == "Act today" and rep["by_urgency"]["act_today"] == 1 and "how_detection_works" in rep


def test_channel_kpis_weighted_by_delivered():
    camps = [{"id": "a", "channel": "Push", "sent_count": 1000, "delivered_count": 900, "opened_count": 90, "conversions": 9, "revenue_generated": 100},
             {"id": "b", "channel": "Push", "sent_count": 100, "delivered_count": 100, "opened_count": 50, "conversions": 1, "revenue_generated": 10},
             {"id": "c", "channel": "Email", "sent_count": 500, "delivered_count": 490, "opened_count": 49, "conversions": 5, "revenue_generated": 50}]
    k = {x["channel"]: x for x in M.channel_kpis(camps)}
    assert k["Push"]["delivered"] == 1000 and k["Push"]["engagement_rate"] == 14.0      # (90+50)/1000, not the mean of 10% and 50%
    assert k["Email"]["engagement_rate"] == 10.0 and k["Email"]["delivery_rate"] == 98.0


def test_lights_and_so_what():
    diag = {"stats": {"delivery_rate": {"today": 71.0, "mean_28d": 94.0}, "ctr": {"today": 8.0, "mean_28d": 8.1}, "conversion_rate": {"today": 1.0, "mean_28d": 1.0}},
            "likely_causes": [{"cause": "Deliverability degradation"}], "options": [{"action": "Pause and clean the audience"}]}
    L = M.lights_from_diagnosis(diag)
    assert L["delivery"]["state"] == "red" and L["engagement"]["state"] == "green" and L["conversion"]["state"] == "green"
    assert "Delivery below usual" in M.so_what(diag, L) and "Pause and clean" in M.so_what(diag, L)
