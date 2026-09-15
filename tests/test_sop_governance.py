"""
SOP governance: recommended changes from evidence, the product and team lens over
the library, request → draft → peer sign-off → library, and the derived list of
data we are still missing.
"""
import json
import pytest


# ---------------------------------------------------------------- improvements
def test_recommendations_carry_evidence_and_a_concrete_change():
    from backend import sop_improve, sops
    rid = "sop_liquidation_recovery"
    assert sops.get_sop(rid)
    one = sop_improve.review(rid)["sops"][0]
    assert one["sop_id"] == rid and 0 <= one["score"] <= 100
    recs = one["recommendations"]
    assert recs, "a library SOP with no runs should always have something to recommend"
    for r in recs:
        assert r["severity"] in ("high", "medium", "low")
        assert r["why"] and r["evidence"], "every recommendation states why and what it saw"
        assert 1 <= r["ice"]["score"] <= 1000
        if r["auto"]:
            assert r["change"] and r["change"]["path"] and r["change"]["to"] is not None
    assert one["counts"]["high"] + one["counts"]["medium"] + one["counts"]["low"] == len(recs)


def test_never_run_is_not_shouted_when_the_cohort_is_missing():
    """Two rules used to fire on the same cause and score every SOP at zero."""
    from backend import sop_improve
    for s in sop_improve.review()["sops"]:
        ids = {r["id"] for r in s["recommendations"]}
        if "family_missing" in ids:
            assert "never_run" not in ids
    scores = [s["score"] for s in sop_improve.review()["sops"]]
    assert max(scores) > 0 and all(0 <= x <= 100 for x in scores)


def test_apply_writes_a_new_framework_checked_version():
    from backend import sop_improve, sops
    rid = "sop_market_move_alert"
    before = sops.get_sop(rid)
    recs = [r for r in sop_improve.review(rid)["sops"][0]["recommendations"] if r["auto"]]
    if not recs:
        pytest.skip("nothing auto-applicable on this SOP")
    r = sop_improve.apply(rid, [recs[0]["id"]], actor="test")
    assert r["ok"] and r["version"] > (before.get("version") or 1)
    after = sops.get_sop(rid)
    assert sops.sop_check(after)["ok"], "an applied change must leave the SOP framework-complete"
    assert r["score"]["after"] >= r["score"]["before"]


def test_every_new_approval_kind_has_an_executor():
    """The registration loop in main.py once ran against the wrong function and silently did nothing."""
    from backend import approvals, signals, research, sop_improve, sop_requests
    for mod in (signals, research, sop_improve, sop_requests):
        mod.register()
    for kind in ("signal_rule", "skill_update", "sop_change", "sop_new"):
        assert kind in approvals.KINDS and approvals._executors.get(kind), f"{kind} would fail at approval time"


def test_propose_queues_a_reviewable_change():
    from backend import sop_improve, approvals
    rid = "sop_rekyc"
    recs = [r for r in sop_improve.review(rid)["sops"][0]["recommendations"] if r["auto"]]
    if not recs:
        pytest.skip("nothing auto-applicable on this SOP")
    p = sop_improve.propose(rid, [recs[0]["id"]], "test", created_by="test")
    d = approvals.get_proposal(p["proposal_id"])
    assert d["kind"] == "sop_change" and d["status"] == "pending"
    diff = (p.get("preview") or {}).get("diff") or p["plan"]["diff"]
    assert diff, "the proposal carries the diff, not just a title"
    assert p["plan"]["score"]["after"] >= p["plan"]["score"]["before"]


# ------------------------------------------------------------ product and team
def test_every_product_gets_a_readable_brief_and_honest_coverage():
    from backend import sop_catalog
    from backend.products import PRODUCTS
    d = sop_catalog.by_product()
    assert {p["product"] for p in d["products"]} == set(PRODUCTS)
    for p in d["products"]:
        assert 0 <= p["own_coverage_pct"] <= p["coverage_pct"] <= 100, "own-lens coverage can never exceed coverage by anything"
        assert len(p["own_covered"]) + len(p["missing"]) == len(d["ladder"])
        assert p["name"] in p["brief"] and p["lens"] in p["brief"]
        assert str(p["own_sops"]) in p["brief"]
        for m in p["missing"]:
            assert isinstance(m["shared_cover"], bool), "a stage with no product lens must say whether a shared journey covers it"
    one = sop_catalog.by_product("options")
    assert one["product"] == "options" and one["sops"]


def test_product_detection_uses_wording_and_family_codes():
    from backend import sop_catalog
    sip = {"name": "Recurring buy nurture", "objective": "get SIP started", "campaign_type": "retention",
           "audience": {"segment_family": "*"}, "steps": [{"purpose": "explain rupee-cost averaging", "copy_brief": "a weekly recurring buy"}]}
    assert "sip" in sop_catalog.products_for(sip)
    generic = {"name": "Welcome", "objective": "onboard", "campaign_type": "onboarding", "audience": {"segment_family": "*"}, "steps": []}
    assert sop_catalog.products_for(generic) == ["all"]
    # one passing hint is not a product claim: a perps SOP that mentions a watchlist is not a spot procedure
    hint = {"name": "Funding reset note", "objective": "explain the funding reset", "campaign_type": "market", "audience": {"segment_family": "PERP_ACTIVE"},
            "steps": [{"purpose": "funding is due", "copy_brief": "add it to your watchlist"}]}
    assert "spot" not in sop_catalog.products_for(hint) and "perps_crypto" in sop_catalog.products_for(hint)


def test_teams_see_what_they_own_review_and_are_on_the_hook_for():
    from backend import sop_catalog
    d = sop_catalog.by_team()
    owners = [t for t in d["teams"] if t["owns_count"]]
    assert owners and owners[0]["owns_count"] >= owners[-1]["owns_count"]
    for t in d["teams"]:
        assert t["role"] and isinstance(t["must_know"], list)
        assert len(t["must_know"]) <= 5
    assert any(t["segments"] for t in d["teams"]), "process segments must land on a team"


def test_coverage_gaps_are_ready_to_become_requests():
    from backend import sop_catalog
    g = sop_catalog.gaps()
    assert g["count"] == len(g["gaps"])
    for x in g["gaps"][:5]:
        assert x["title"] and x["why"] and x["product"] and x["transition"] and x["lens"]
    assert [x["shared_cover"] for x in g["gaps"]] == sorted(x["shared_cover"] for x in g["gaps"]), "stages with nothing at all come first"


# ------------------------------------------------- request → draft → sign-off
def test_request_draft_needs_peers_then_lands_in_the_library(monkeypatch):
    from backend import sop_requests, approvals, sops
    from backend.database import set_setting
    set_setting("sop_peer_signoffs", "2")
    sop_requests.register()
    monkeypatch.setattr(sop_requests, "_polish", lambda spec, req: spec)     # no model call in tests

    req = sop_requests.create("Perps funding-reset education", "Crypto perps traders hold through funding resets without knowing what it costs them. "
                              "Teach the mechanic the day before a reset and give them the hedge or reduce path.",
                              product="perps_crypto", transition="activated_habitual", requested_by="asker")
    assert req["id"] and req["status"] == "open"

    d = sop_requests.draft(req["id"], actor="asker")
    assert d["sop_id"].startswith("sop_") and d["proposal_id"]
    spec = d["spec"]
    assert sops.sop_check(spec)["ok"], "the draft must be framework-complete before anyone reads it"
    assert spec["holdout_pct"] >= 20 and spec["steps"], "our own defaults apply to drafts too"

    # the requester cannot wave their own request through
    assert sop_requests.signoff(req["id"], actor="asker", verdict="approve").get("error")
    with pytest.raises(Exception):
        approvals.approve_and_execute(d["proposal_id"], decided_by="peer1")
    assert approvals.get_proposal(d["proposal_id"])["status"] == "pending", "a premature approval must not burn the proposal"

    s1 = sop_requests.signoff(req["id"], actor="peer1", verdict="approve", note="reads well")
    assert s1["approvals"] == 1 and not s1["ready"]
    again = sop_requests.signoff(req["id"], actor="peer1", verdict="approve")
    assert again["approvals"] == 1, "a second vote from the same person replaces the first, it does not stack"
    s2 = sop_requests.signoff(req["id"], actor="peer2", verdict="approve")
    assert s2["approvals"] == 2 and s2["ready"]

    approvals.approve_and_execute(d["proposal_id"], decided_by="peer2")
    assert sops.get_sop(d["sop_id"]), "an approved draft enters the library"
    row = [r for r in sop_requests.list_requests() if r["id"] == req["id"]][0]
    assert row["status"] == "delivered" and row["sop_id"] == d["sop_id"]
    sops.retire_sop(d["sop_id"]) if hasattr(sops, "retire_sop") else None


def test_changes_requested_blocks_and_suggestions_come_from_real_gaps(monkeypatch):
    from backend import sop_requests
    monkeypatch.setattr(sop_requests, "_polish", lambda spec, req: spec)
    req = sop_requests.create("Earn idle-balance nudge", "Funded users leave stablecoins idle; show the earn option once, education only.",
                              product="earn", transition="funded_activated", requested_by="asker")
    sop_requests.draft(req["id"], actor="asker")
    r = sop_requests.signoff(req["id"], actor="peer1", verdict="changes", note="add the TDS line")
    assert r["approvals"] == 0 and not r["ready"]
    row = [x for x in sop_requests.list_requests() if x["id"] == req["id"]][0]
    assert row["status"] == "changes_requested"

    sug = sop_requests.suggest()["suggestions"]
    assert sug and all(s["title"] and s["need"] for s in sug)
    assert any(s["source"] for s in sug)


# ------------------------------------------------------------------ data gaps
def test_data_gaps_ask_for_what_the_sops_and_segments_need():
    from backend import data_gaps
    d = data_gaps.detect()
    assert d["outstanding"] == len([i for i in d["items"] if not i["requested"] and i["status"] != "not needed yet"])
    kinds = {i["kind"] for i in d["items"]}
    assert {"event", "attribute", "segment", "capability"} & kinds
    for i in d["items"]:
        assert i["why"] and i["spec"] and i["severity"] in ("high", "medium", "low")
        assert isinstance(i["unblocks"], list)
        assert i["blocks"] == len(i["unblocks"]) or i["blocks"] >= 0
    liq = next((i for i in d["items"] if i["name"] == "liquidation"), None)
    assert liq and liq["unblocks"], "liquidation recovery must name the event it depends on"


def test_data_gaps_sync_files_requests_once():
    from backend import data_gaps, datarequests
    before = len(datarequests.list_requests(None))
    r = data_gaps.sync("high", actor="test")
    after = datarequests.list_requests(None)
    assert r["count"] == len(after) - before and r["count"] > 0
    filed = {x["title"] for x in after}
    again = data_gaps.sync("high", actor="test")
    assert again["count"] == 0, "the same gap is never asked for twice"
    assert len(datarequests.list_requests(None)) == len(after)
    assert filed & {x["title"] for x in datarequests.list_requests("open")}


# --------------------------------------------------- choosing what we talk about
def test_asset_selection_sop_covers_every_surface_and_sends_nothing():
    from backend import sops, sop_catalog, sop_india
    spec = sops.get_sop("sop_asset_selection")
    assert spec, "the platform needs a written procedure for choosing assets"
    assert sops.sop_check(spec)["ok"]
    text = " ".join(str(s.get("purpose", "")) + " " + str(s.get("copy_brief", "")) for s in spec["steps"]).lower()
    for surface in ("spot", "perps", "options", "web3"):
        assert surface in text, f"{surface} has no selection lens"
    assert all(str(s["purpose"]).startswith("(internal)") for s in spec["steps"]), "selection is a desk procedure, it messages nobody"
    assert (spec["frequency"] or {}).get("max_messages_per_user_per_week") == 0
    assert sop_catalog.products_for(spec) == ["all"], "every product team reads this one"
    india = sop_india.review_one(spec)
    assert india["score"] == 100 and india["status"] == "india-ready"
    for angle in ("leverage_upsell", "direction", "price_urgency"):
        assert angle in (spec["compliance"] or {}).get("banned_angles", [])
