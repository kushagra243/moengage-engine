import json
from backend.database import get_db, set_setting


def test_decode_nomenclature_and_versions():
    from backend.segments import decode
    d = decode("HVT_Sep26")
    assert d["family"] == "HVT" and d["version"] == "2026-09" and "High value trader" in d["meaning"]
    d2 = decode("Dormant_D60_LowProp_Sep'26")
    assert d2["family"] == "DORMANT_D60_LOWPROP" and d2["version"] == "2026-09" and "Dormant" in d2["meaning"]
    d3 = decode("GMC_Sep26_CS_Res_HighProp_HVS_8thSept")
    assert d3["version"] == "2026-09" and "HVS" not in d3["unknown_tokens"]
    assert decode("XQ_TG_Sep26")["unknown_tokens"] == ["XQ"]


def test_registry_sync_study_and_define_code():
    from backend import segments
    from backend.moengage import mock
    from backend.llm.tools import define_nomenclature, segment_study
    set_setting("mock_mode", "true")
    conn = get_db(); conn.execute("DROP TABLE IF EXISTS segment_registry"); conn.commit(); conn.close()
    r = segments.sync(mock.segments())
    assert r["seeded"] and r["new"] == []
    extra = mock.segments() + [{"id": "seg_hvt_oct", "name": "HVT_Oct26", "estimated_reach": 4600, "created_at": "2026-10-02"}]
    r2 = segments.sync(extra)
    assert [n["family"] for n in r2["new"]] == ["HVT"] and r2["new"][0]["version"] == "2026-10"
    st = segments.study(mock.campaigns())
    hvt = next(f for f in st["families"] if f["family"] == "HVT")
    assert len(hvt["versions"]) == 3 and hvt["latest"]["name"] == "HVT_Oct26" and hvt["reach"] == 4600
    assert any("XQ" == t for t, n in st["unknown_tokens"])
    assert {s["id"] for s in st["studies"]} & {"value_tier_response", "orphan_cohorts", "new_cohort_baseline", "migration_matrix"}
    assert segments.resolve("HVT")["name"] == "HVT_Oct26" and segments.resolve("hvt_sep26")["name"] == "HVT_Sep26"
    out = define_nomenclature("XQ", "Experiment group Q")
    assert out["ok"]
    assert "XQ" not in segments.decode("XQ_TG_Sep26")["unknown_tokens"]
    assert segment_study()["segments"] >= 9


def test_sop_framework_blocks_bad_specs():
    from backend import sops
    good = next(s for s in sops.LIBRARY if s["id"] == "sop_hvt_retention")
    assert sops.sop_check(good)["ok"]
    bad = json.loads(json.dumps(good)); bad["holdout_pct"] = 2; bad["steps"][2]["send_time_ist"] = "23:30"; bad["kill_criteria"] = []
    chk = sops.sop_check(bad)
    assert not chk["ok"] and any("holdout" in p for p in chk["problems"]) and any("DND" in p for p in chk["problems"]) and any("kill" in p for p in chk["problems"])
    market = json.loads(json.dumps(next(s for s in sops.LIBRARY if s["id"] == "sop_market_move_alert"))); market["audience"]["exclusions"] = ["DND"]
    assert any("liquidated" in p for p in sops.sop_check(market)["problems"])
    assert all(sops.sop_check(s)["ok"] for s in sops.LIBRARY)


def test_define_and_run_sop_queues_proposals(monkeypatch):
    from backend import sops, segments, approvals
    from backend.moengage import mock
    from backend.moengage.executors import register_all
    register_all(); set_setting("mock_mode", "true")
    conn = get_db(); conn.execute("DROP TABLE IF EXISTS segment_registry"); conn.execute("DROP TABLE IF EXISTS sops"); conn.execute("DROP TABLE IF EXISTS sop_runs"); conn.commit(); conn.close()
    segments.sync(mock.segments())
    sops.init_sop_tables()
    assert len(sops.list_sops()) == len(sops.LIBRARY)
    dry = sops.run_sop("sop_hvt_retention", dry_run=True)
    assert dry["ok"], dry
    assert dry["segment"]["name"] == "HVT_Sep26" and len([p for p in dry["plan"] if not p.get("internal")]) == 3
    before = len(approvals.list_proposals(limit=400))
    run = sops.run_sop("sop_hvt_retention", start_date="2026-09-15", variants_by_step={"0": [{"label": "A", "title": "Your September statement", "body": "Fees, tier progress and funding paid — your numbers, no outlook.", "cta": "Open statement"}]}, created_by="test")
    assert run["ok"] and len(run["proposal_ids"]) == 3 and len(approvals.list_proposals(limit=400)) == before + 3
    p0 = approvals.get_proposal(run["proposal_ids"][0])
    assert p0["payload"]["target_segment"] == "HVT_Sep26" and p0["payload"]["goal"]["control_group_pct"] == 20 and p0["payload"]["schedule"]["date"] == "2026-09-15"
    assert p0["payload"]["variants"][0]["title"] == "Your September statement"
    runs = sops.list_runs()
    assert runs and runs[0]["proposal_status"].get("pending") == 3
    # custom SOP through the framework
    spec = json.loads(json.dumps(next(s for s in sops.LIBRARY if s["id"] == "sop_funded_to_first_trade"))); spec.pop("id"); spec["name"] = "FTD nudge lite"; spec["holdout_pct"] = 20
    d = sops.define_sop(spec, author="test")
    assert d["ok"] and d["id"] == "sop_ftd_nudge_lite" and any(s["id"] == "sop_ftd_nudge_lite" for s in sops.list_sops())
    unknown = sops.run_sop("sop_nope", dry_run=True); assert not unknown["ok"]
    checks = sops.midflight_checks(); assert "runs" in checks


def test_brief_check_jurisdiction_and_suppression_rules():
    from backend.llm.tools import campaign_brief_check
    goal = {"transition": "habitual_core", "hypothesis": "h", "primary_kpi": "x", "target": "t", "guardrail_metric": "g", "control_group_pct": 20, "measurement_window_days": 7, "kill_criteria": "k", "suppressions": ["DND"]}
    r = campaign_brief_check(goal, [{"title": "Trade perps now with up to 50x", "body": "Don't miss it", "cta": "go"}], channel="push", audience_countries=["IN", "GB"])
    txt = " ".join(r["problems"])
    assert "UK" in txt and "lure" in txt and "liquidated" in txt and "loss-dormant" in txt and "forecast/urgency" in txt
    ok = campaign_brief_check({**goal, "suppressions": ["liquidated 14d", "loss-dormant", "DND"]}, [{"title": "Funding on BTC-PERP: 0.04%/h", "body": "Longs pay ≈0.96% a day. Check your position.", "cta": "View position"}], channel="in-app", market_linked=True, ttl_hours=4, audience_countries=["IN"], disclaimer_included=True)
    assert ok["ok"], ok
    nod = campaign_brief_check(goal, [{"title": "Fee update", "body": "New tiers", "cta": "See"}], channel="email", audience_countries=["IN"], disclaimer_included=False)
    assert any("disclaimer" in p for p in nod["problems"])


def test_new_skills_and_tools_registered():
    from backend.skills import list_skills
    from backend.llm.tools import TOOLS
    names = {s["name"] for s in list_skills()}
    assert {"clm-campaign-playbook", "product-marketing", "cohort-studies", "campaign-sops"} <= names
    for t in ("segment_study", "define_nomenclature", "list_sops", "sop_detail", "define_sop", "run_sop", "sop_runs"):
        assert t in TOOLS
