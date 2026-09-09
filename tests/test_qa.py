"""QA layer: figure tracing in agent replies, claim verification, the fact-check run and its persistence, and the defects it caught."""
import json


def test_check_reply_traces_figures_to_tool_outputs():
    from backend import qa
    tools = [json.dumps({"price": 78715.1, "chg_24h": 0.28, "vol_24h_usd": 3712144411.0, "btc_dominance": 58.414, "fng": 66, "oi_usd": 2.9e9})]
    q = qa.check_reply("BTC is at 78,715 (+0.28% 24h) with $3.71B volume and $2.9B OI; dominance 58.4%. Fear & Greed 66. Expect a 40% rally on Binance, guaranteed.", tools)
    assert q["figures"] >= 6 and q["verified"] == q["figures"] - 1
    assert q["unverified"] == ["40%"] and q["venue_names"] == ["Binance"] and "guaranteed" in q["banned_phrases"] and not q["ok"]
    line = qa.qa_line(q)
    assert line.startswith("QA ·") and "40%" in line and "Binance" in line
    clean = qa.check_reply("Two proposals were queued in 2026 for step 3.", tools)      # years and small counts are labels, not claims
    assert clean["figures"] == 0 and clean["ok"] and qa.qa_line(clean) == ""


def test_numbers_in_handles_units_and_skips_labels():
    from backend.qa import _numbers_in, _matches
    figs = _numbers_in("volume $2.1B, OI 450M, funding 11%, 3.2× last week, 12 users, 2024")
    raws = [f[0] for f in figs]
    assert "$2.1B" in raws and "450M" in raws and "11%" in raws and "3.2×" in raws
    assert not any(r.strip() in ("12", "2024") for r in raws)
    assert _matches(2.1, "b", [2.1e9]) and _matches(11, "%", [0.11]) and _matches(78715, "", [78715.1])
    assert not _matches(40, "%", [0.28, 66, 58.4]) and not _matches(12345.6, "%", [123.0, 12345.0 * 1.02])


def test_verify_claims_against_live_context(monkeypatch):
    from backend import qa
    from backend.market import context
    monkeypatch.setattr(context, "_latest", lambda *_a, **_k: {"generated_at": "2026-09-09T00:00:00+00:00", "crypto_global": {"btc_dominance": 58.41, "total_mcap_usd": 2.7e12}, "fear_greed": {"value": 66}})
    import backend.market.benchmarks as b
    monkeypatch.setattr(b, "benchmarks", lambda *a, **k: {})
    r = qa.verify_claims([{"claim": "BTC dominance", "value": 58.4, "unit": "%"}, {"claim": "F&G", "value": 66}, {"claim": "invented", "value": 9876.5, "unit": "%"}])
    assert [c["verified"] for c in r["claims"]] == [True, True, False] and r["verified"] == 2


def test_run_produces_checks_persists_and_improves(monkeypatch):
    from backend import qa, growth
    from backend.database import set_setting
    set_setting("mock_mode", "true")
    growth.upsert_ideas([{"title": "QA thin idea for enrichment", "kind": "growth_hack", "why": "test", "how": "test", "transition": "verified_funded", "priority": 40}], "rules")
    r = qa.run(persist=True)
    assert 0 <= r["score"] <= 100 and set(r["counts"]) == {"pass", "warn", "fail"} and r["checks"]
    assert all({"id", "area", "status", "fact", "evidence", "fix"} <= set(c) for c in r["checks"])
    ids = {c["id"] for c in r["checks"]}
    assert {"ctx_fresh", "structural_sops_exist", "matrix_sops_exist", "copy_no_venues", "ideas_complete", "families_staged"} <= ids
    statuses = [c["status"] for c in r["checks"]]
    assert statuses == sorted(statuses, key=lambda s: {"fail": 0, "warn": 1, "pass": 2}[s])
    assert next(c for c in r["checks"] if c["id"] == "structural_sops_exist")["status"] == "pass"
    assert next(c for c in r["checks"] if c["id"] == "matrix_sops_exist")["status"] == "pass"
    assert any("enriched" in i["what"] for i in r["improvements"])
    idea = next(i for i in growth.list_ideas(status="new", limit=300) if i["title"] == "QA thin idea for enrichment")
    assert idea["kpi"] == "first_deposit_rate_7d" and (idea.get("data") or {}).get("tagline")
    hist = qa.history(5)
    assert hist and hist[0]["score"] == r["score"]
    rep = qa.report()
    assert rep.get("cached") and "history" in rep


def test_qa_caught_defects_are_fixed():
    from backend.guardrails import _stage_for_family
    assert _stage_for_family("MVT") == "Habitual" and _stage_for_family("DORMANT_D60_LOWPROP") == "Dormant"
    assert _stage_for_family("FTD_NOTRADE") == "New" and _stage_for_family("REKYC_PENDING") == "New"
    assert _stage_for_family("HFT_FUTURES") == "Habitual perp" and _stage_for_family("HVT") == "Core" and _stage_for_family("LIQUIDATED_14D") == "Liquidated"
    from backend.market import competitors
    src = open(competitors.__file__, encoding="utf-8").read()
    assert "pv >= 1_000_000" in src and "<= 500" in src         # history gaps are not surges


def test_agent_reply_gets_qa_footer(monkeypatch):
    from backend.llm.agent import MarketerAgent

    class FakeClient:
        cfg = {"provider": "openai_compatible", "model": "fake"}
        purpose = "chat"

        def chat(self, messages, tools=None, **kw):
            return {"model": "fake", "content": "Volume is $3.71B and CTR will be 25% next week.", "tool_calls": [], "usage": {}}
    a = MarketerAgent(FakeClient(), purpose="chat")
    a._seen_calls = {"market_snapshot:{}": json.dumps({"vol_24h_usd": 3712144411.0})}
    out = a.chat("how is volume", history=[], persist=False)
    assert out["qa"]["verified"] == 1 and out["qa"]["unverified"] == ["25%"]
    assert out["reply"].rstrip().endswith("_") and "QA ·" in out["reply"] and "25%" in out["reply"].split("QA ·")[-1]
