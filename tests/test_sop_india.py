"""India-fit review of SOPs: rules from the compliance, copywriting and calendar skills; deterministic fixes create a new version."""


def _sop(**over):
    base = {"id": "sop_test_india", "name": "Test deposit journey", "campaign_type": "onboarding", "transition": "verified_funded", "objective": "Get verified users to deposit.", "user": "verified, no deposit",
            "audience": {"segment_family": "KYC_APPROVED_NODEP", "description": "x", "exclusions": ["KYC pending"], "min_reach": 200, "jurisdictions_excluded": []},
            "steps": [{"day": 0, "channel": "push", "purpose": "Add money", "copy_brief": "Deposit now and get a bonus.", "send_time_ist": "19:00"},
                      {"day": 1, "channel": "whatsapp", "purpose": "Reminder", "copy_brief": "Your account is ready.", "send_time_ist": "09:30"},
                      {"day": 3, "channel": "email", "purpose": "How it works", "copy_brief": "Explain deposits.", "send_time_ist": "11:00"}],
            "duration_days": 7, "frequency": {"cadence": "event_triggered", "max_messages_per_user_per_week": 3}, "holdout_pct": 20, "primary_kpi": "first_deposit_rate_7d", "target": "+3 pp", "guardrail_metric": "unsubscribe_rate",
            "measurement_window_days": 7, "kill_criteria": ["unsubscribe > 0.5%"], "ideas": [], "compliance": {"disclaimer_channels": ["email", "whatsapp", "in-app"], "banned_angles": []}, "source": "test", "version": 1, "checks": {"preflight": ["framework"], "midflight": [], "postflight": []}}
    base.update(over)
    return base


def test_review_flags_india_rules_and_scores():
    from backend import sop_india
    r = sop_india.review_one(_sop())
    rules = {f["rule"] for f in r["flags"]}
    assert {"onboarding_incentive", "dlt_window", "whatsapp_utility", "tds_transparency", "upi_specifics", "dnd_exclusion", "hinglish_variant", "salary_week"} <= rules
    assert r["score"] < 60 and r["status"] == "rework" and r["auto_fixable"] >= 7
    bad_time = sop_india.review_one(_sop(steps=[{"day": 0, "channel": "push", "purpose": "x", "copy_brief": "y", "send_time_ist": "23:30"}], compliance={"disclaimer_channels": [], "banned_angles": ["bonus"]}))
    assert {"quiet_hours"} <= {f["rule"] for f in bad_time["flags"]}
    good = sop_india.review_one(_sop(steps=[{"day": 0, "channel": "email", "purpose": "How UPI deposits work, fees and 1% TDS", "copy_brief": "UPI in 20 seconds, IMPS fallback; fees and TDS stated plainly. Hinglish variant for app_language ≠ en. Salary week 2–6 of month.", "send_time_ist": "11:00"}],
                                     compliance={"disclaimer_channels": ["email"], "banned_angles": ["bonus"]}, audience={"segment_family": "KYC_APPROVED_NODEP", "description": "x", "exclusions": ["unsubscribed / DND"], "min_reach": 200, "jurisdictions_excluded": []}))
    assert good["score"] >= 85 and good["status"] == "india-ready"


def test_derivatives_posture_and_false_positive_guards():
    from backend import sop_india
    d = sop_india.review_one(_sop(id="sop_test_perp", name="Perp hygiene", campaign_type="risk", transition="habitual_core", objective="Teach margin buffer and funding cost; then trade perps now.", audience={"segment_family": "HFT_FUTURES", "description": "x", "exclusions": ["unsubscribed / DND"], "min_reach": 200, "jurisdictions_excluded": []},
                                    steps=[{"day": 0, "channel": "in-app", "purpose": "Margin buffer", "copy_brief": "Education only.", "send_time_ist": "11:00"}], compliance={"disclaimer_channels": ["in-app"], "banned_angles": []}))
    rules = {f["rule"] for f in d["flags"]}
    assert d["derivatives"] and {"derivatives_acquisition", "derivatives_banned_angles", "exclude_liquidated"} <= rules
    w = sop_india.review_one(_sop(id="sop_test_wa", name="WhatsApp utility journey", objective="Leverage WhatsApp's read rates for onboarding on Robinhood Chain.", transition="acquired_verified"))
    assert not w["derivatives"] and "venue_named" not in {f["rule"] for f in w["flags"]}


def test_apply_fixes_creates_new_version_and_raises_score():
    from backend import sop_india, sops
    spec = _sop(id="sop_test_india_fix", source="user")
    res = sops.define_sop(dict(spec), author="test")
    assert res["ok"], res
    before = sop_india.review_one(sops.get_sop("sop_test_india_fix"))
    out = sop_india.apply_fixes("sop_test_india_fix", actor="test")
    assert out["ok"] and out["version"] == 2 and out["after"] > out["before"] == before["score"]
    after = sops.get_sop("sop_test_india_fix")
    assert "unsubscribed / DND" in after["audience"]["exclusions"] and "bonus" in after["compliance"]["banned_angles"]
    assert {"whatsapp", "email"} <= set(after["compliance"]["disclaimer_channels"])
    assert after["steps"][1]["send_time_ist"] == "11:00"
    assert after["steps"][1]["copy_brief"].startswith("Utility template")
    assert any("TDS" in st["copy_brief"] for st in after["steps"]) and any("UPI" in st["copy_brief"] for st in after["steps"])
    again = sop_india.apply_fixes("sop_test_india_fix", actor="test")
    assert again["ok"] and not again["changed"]


def test_library_review_shape():
    from backend import sop_india
    r = sop_india.review()
    assert r["sops"] and set(r["counts"]) == {"india-ready", "needs edits", "rework"} and 0 <= r["avg_score"] <= 100
    assert all({"id", "score", "status", "flags", "auto_fixable"} <= set(x) for x in r["sops"])
    assert isinstance(r["missing_india_sops"], list)
