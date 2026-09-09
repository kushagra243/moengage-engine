from backend.taxonomy import classify, group, comparisons, tokens
from backend.database import set_setting


def _c(i, name, ch="Push", **m):
    d = {"id": i, "name": name, "channel": ch, "sent_count": 100000, "delivered_count": 95000, "clicks": 9500, "ctr": 10.0, "delivery_rate": 95.0}
    d.update(m); return d


def test_classify_real_naming_conventions():
    t = classify({"id": "1", "name": "GMC - Sep'26- CS_ExpertPicksGTM_7thSept", "channel": "Push", "tags": ["expert"], "target_segment": "Active traders"})
    assert t["programme"].startswith("Growth") and t["cohort"] == "Cross-sell" and "Expert picks" in t["facets"]["message"] and t["facets"]["tag"] == ["expert"]
    t = classify({"id": "2", "name": "GMC_Sep26_CS_Res_HighProp_HVS_8thSept"})
    assert t["cohort"] == "Cross-sell" and "Resurrection (dormant)" in t["facets"]["cohort"] and t["facets"]["propensity"] == ["High propensity"] and t["facets"]["value"] == ["High value"]
    t = classify({"id": "3", "name": "Re-KYC_LowRisk_Update_Reminder_8Sep26"})
    assert t["programme"] == "Re-KYC compliance" and t["facets"]["risk"] == ["Low risk"] and set(t["facets"]["message"]) >= {"Update", "Reminder"}
    t = classify({"id": "4", "name": "GMC_Sep'26_HFT_Futures_Ret_INJ blog_4Sep26"})
    assert t["facets"]["trader"] == ["High-frequency trader"] and "Futures" in t["facets"]["product"] and t["cohort"] == "Retention" and "Blog" in t["facets"]["content"]
    t = classify({"id": "5", "name": "GMC_Sep26_Futures_RES_Null_TG_8Sep26"})
    assert t["cohort"] == "Resurrection (dormant)" and "No prior product" in t["facets"]["cohort"] and "Trading game / tournament" in t["facets"]["product"]
    assert "Newsletter" == classify({"id": "6", "name": "Newsletter_CryptoPulse_8Sep26"})["programme"]
    assert tokens("GMC - Sep'26- CS_ExpertPicksGTM_7thSept") == ["GMC", "Sep'26", "CS", "ExpertPicksGTM", "7thSept"]


def test_group_and_comparisons_weighted():
    camps = [_c("a", "GMC_Sep26_CS_Res_HighProp_HVS_8Sep26", ctr=14.0, clicks=13300),
             _c("b", "GMC_Sep26_CS_Res_LowProp_HVS_8Sep26", ctr=6.0, clicks=5700),
             _c("c", "GMC_Sep26_CS_Res_HighProp_LVS_8Sep26", ctr=9.0, clicks=8550),
             {"id": "d", "name": "GMC_Sep26_RES_CS_LowProp_LIS_8Sep26", "channel": "Push", "stats_missing": True}]
    byprop = {g["key"]: g for g in group(camps, "propensity")}
    assert byprop["High propensity"]["campaigns"] == 2 and byprop["High propensity"]["with_stats"] == 2 and byprop["High propensity"]["click_rate"] == 11.5
    assert byprop["Low propensity"]["campaigns"] == 2 and byprop["Low propensity"]["with_stats"] == 1
    cmp = comparisons(camps)
    top = cmp[0]
    assert top["facet"] in ("propensity", "value") and top["gap_pp"] > 0 and "pp gap" in top["reading"]


def test_custom_codes_setting():
    set_setting("taxonomy_codes", '{"GTM": "message:Go-to-market"}')
    t = classify({"id": "x", "name": "GMC_Sep26_GTM_Launch"})
    assert "Go-to-market" in t["facets"]["message"]
    set_setting("taxonomy_codes", "")


def test_deep_dive_deterministic_without_model():
    from backend.analysis import analyse, list_analyses, latest
    from backend.database import get_setting
    set_setting("mock_mode", "true")
    prev = (get_setting("llm_provider", ""), get_setting("llm_api_key", ""))
    set_setting("llm_provider", "openrouter"); set_setting("llm_api_key", "")
    r = analyse("cmp_002")
    assert r["tier"] == "deterministic" and r["analysis"].get("_deterministic") and r["dossier"]["taxonomy"]["name"]
    assert r["analysis"]["next_actions"] and "segment_insight" in r["analysis"]
    r2 = analyse("cmp_002")
    assert r2["cached"] is True
    assert any(a["campaign_id"] == "cmp_002" for a in list_analyses()) and latest("cmp_002")["analysis"]["verdict"]
    set_setting("llm_provider", prev[0] or "openrouter"); set_setting("llm_api_key", prev[1])
