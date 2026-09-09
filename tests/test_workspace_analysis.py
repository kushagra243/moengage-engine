"""Workspace analysis: the complete MoEngage programme report the Analysis module renders."""


def test_facts_cover_every_section():
    from backend.database import set_setting
    from backend import workspace_analysis as wa
    set_setting("mock_mode", "true")
    f = wa.facts()
    assert {"programme", "lifecycle", "channels", "campaigns", "facets", "cohorts", "guardrails", "experiments", "deep_dives"} <= set(f)
    p = f["programme"]
    assert p["campaigns"] >= 1 and 0 <= p["coverage_pct"] <= 100 and p["delivery_rate"] is not None and "caveat" in p
    assert any(t["status"] == "uncovered" for t in f["lifecycle"]) and all({"transition", "campaigns", "kpi_options", "status"} <= set(t) for t in f["lifecycle"])
    assert f["channels"] and all(c["verdict"] in ("strong", "in range", "below", "investigate", "no data") and c["benchmark"] for c in f["channels"])
    league = f["campaigns"]["league"]
    assert league and all({"id", "name", "channel", "health", "verdict", "recommendation", "severity", "do_first"} <= set(r) for r in league)
    sev = [{"critical": 0, "watch": 1}.get(r.get("severity") or "", 2) for r in league]
    assert sev == sorted(sev)                                   # critical first
    assert f["campaigns"]["best_ctr"] and f["campaigns"]["audience_overlap"]
    assert "comparisons" in f["facets"] and "families" in f["cohorts"]


def test_report_narrative_persists_and_reuses(monkeypatch):
    from backend.database import set_setting
    from backend import workspace_analysis as wa
    set_setting("mock_mode", "true")
    monkeypatch.setattr(wa, "_model_narrative", lambda f: None)       # no model → deterministic, clearly labelled
    r = wa.report(force=True)
    n = r["narrative"]
    assert n["_deterministic"] and n["executive_summary"] and n["top_actions"]
    assert all("ice" in a and 1 <= a["ice"]["impact"] <= 10 for a in n["top_actions"])
    scores = [a["ice"]["score"] for a in n["top_actions"]]
    assert scores == sorted(scores, reverse=True)
    assert r["narrative_meta"]["tier"] == "deterministic" and r["history"] and r["history"][0]["actions"] == len(n["top_actions"])
    # unchanged inputs → the stored narrative is reused
    calls = {"n": 0}

    def boom(f):
        calls["n"] += 1
        return None
    monkeypatch.setattr(wa, "_model_narrative", boom)
    r2 = wa.report()
    assert calls["n"] == 0 and r2["narrative_meta"]["inputs_hash"] == r["narrative_meta"]["inputs_hash"]
    # a model answer is accepted and scored
    monkeypatch.setattr(wa, "_model_narrative", lambda f: {"executive_summary": "Model says hi.", "whats_working": ["x"], "whats_broken": [], "structural_gaps": [], "top_actions": [{"action": "do a thing", "why": "because", "who": "HVT", "impact": 9, "confidence": 7, "ease": 5}], "risks": [], "data_gaps": [], "confidence": "high", "_model": "fake/model"})
    r3 = wa.report(force=True)
    assert r3["narrative_meta"]["model"] == "fake/model" and r3["narrative"]["top_actions"][0]["ice"]["score"] == 315


def test_analysis_route_and_stats():
    from backend.database import set_setting
    from backend import brain
    set_setting("mock_mode", "true")
    st = brain.state()
    assert "analysis" in st["stats"] and len(st["stats"]["analysis"]) == 5
