def test_brain_state_and_views_have_the_contract_shape():
    from backend import brain
    from backend.database import set_setting
    set_setting("mock_mode", "true")
    st = brain.state()
    assert [b["k"] for b in st["bus"]] == ["MODE", "BRAIN", "MEMORY", "GUARDRAILS", "SCHEDULER", "HISTORY"]
    assert [l["name"] for l in st["load"]] == ["INGEST", "REASONING", "MEMORY", "ACTUATION"] and all(0 <= l["pct"] <= 100 for l in st["load"])
    assert [p["stage"] for p in st["pipeline"]] == ["IDEATED", "PLANNED", "APPROVAL", "LIVE", "LEARNED"]
    assert set(st["stats"]) == {"brain", "intel", "ideas", "exp", "sops", "anom", "market"} and all(len(v) == 5 for v in st["stats"].values())
    assert st["brief"] and all(b["sev"] in ("act_now", "high_ev", "counter", "cleanup", "watch", "good", "info") for b in st["brief"])
    ds = brain.directives()
    assert all(d["severity"] in brain.SEV_COLOR and d["sev_label"] and d["actions"] for d in ds)
    board = brain.experiments_board()
    assert [c["key"] for c in board["columns"]] == ["proposed", "simulated", "live", "read", "archive"]
    ideas = brain.ideas("all")
    assert all({"id", "title", "hypothesis", "projectedLift", "confidence", "effort", "sourceSignal", "category"} <= set(i) for i in ideas)
    assert brain.ideas("market") == [i for i in ideas if i["category"] == "market"]
    an = brain.anomalies_view()
    assert all(a["severity"] in ("ACT TODAY", "WATCH", "GOOD", "NORMAL") and isinstance(a["trend"], list) for a in an)
    mk = brain.market_view(); assert "tiles" in mk and "hooks" in mk
    it = brain.intel(); assert "rivals" in it and "moves" in it and "sov" in it
    tr = brain.trace(5); assert all({"ts", "text", "level"} <= set(l) for l in tr)


def test_directive_actions_write_trace_lines():
    from backend import brain, approvals
    from backend.moengage.executors import register_all
    from backend.database import set_setting
    register_all(); set_setting("mock_mode", "true")
    p = approvals.propose("create_segment", "Brain test segment", {"name": "Brain test", "criteria": {"ltv": {"gt": 1}}}, rationale="t", created_by="test")
    sim = brain.act_on_directive(f"proposal:{p['id']}", "simulate", actor="test")
    assert sim["ok"] and sim["trace"].startswith("directive/simulate")
    hold = brain.act_on_directive(f"proposal:{p['id']}", "hold", actor="test", note="wait for Monday")
    assert hold["ok"] and approvals.get_proposal(p["id"])["revisions"][-1]["type"] == "comment"
    ap = brain.act_on_directive(f"proposal:{p['id']}", "approve", actor="test")
    assert ap["status"] == "executed" and "→ executed" in ap["trace"]
    assert not brain.act_on_directive("nonsense:1", "hold")["ok"]
    pr = brain.promote_idea("price_move_alerts", actor="test")
    assert pr["ok"] and pr["agent_prompt"]
