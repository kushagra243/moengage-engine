def test_edit_comment_classify_pending_proposal():
    from backend import approvals
    from backend.moengage.executors import register_all
    from backend.database import set_setting
    register_all(); set_setting("mock_mode", "true")
    p = approvals.propose("create_campaign", "Board test", {"name": "SOP_x_WEB3_SOL_Sep26", "channel": "push", "target_segment": "WEB3_SOL_Sep26", "variants": [{"label": "A", "title": "t", "body": "b", "cta": "c"}],
                                                          "goal": {"transition": "dormant_activated", "hypothesis": "h", "primary_kpi": "reactivation_rate_14d", "target": "+2", "guardrail_metric": "g", "control_group_pct": 10, "measurement_window_days": 14, "kill_criteria": "k", "suppressions": ["DND"]}}, created_by="test")
    assert p["category"] == "winback" and p["product"] == "web3" and p["stage"] == "awaiting_approval"
    p2 = approvals.update_payload(p["id"], {"goal": {"control_group_pct": 20}, "variants": [{"label": "A", "title": "Better title", "body": "b", "cta": "c"}]}, actor="agent", note="holdout to 20, tighter title")
    assert p2["payload"]["goal"]["control_group_pct"] == 20 and p2["payload"]["goal"]["primary_kpi"] == "reactivation_rate_14d" and p2["payload"]["variants"][0]["title"] == "Better title"
    assert p2["revisions"][-1]["type"] == "edit" and "goal" in p2["revisions"][-1]["changed"]
    # non-compliant edits are refused by the brief check
    import pytest
    with pytest.raises(approvals.ApprovalError):
        approvals.update_payload(p["id"], {"variants": [{"label": "A", "title": "Buy now on Binance, guaranteed", "body": "b", "cta": "c"}]}, actor="agent", note="bad")
    p3 = approvals.add_comment(p["id"], "Consider a WhatsApp step for opted-in users", actor="user")
    assert p3["revisions"][-1]["type"] == "comment"
    approvals.reject(p["id"], note="done", decided_by="test")
    with pytest.raises(approvals.ApprovalError):
        approvals.update_payload(p["id"], {"risk": "low"}, actor="agent", note="late")
    from backend.llm.tools import TOOLS
    for t in ("proposal_detail", "revise_proposal", "comment_proposal"):
        assert t in TOOLS
    assert TOOLS["proposal_detail"](proposal_id=p["id"])["stage"] == "rejected"
