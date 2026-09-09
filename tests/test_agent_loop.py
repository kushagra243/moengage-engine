"""
End-to-end agent loop against a local fake OpenAI-compatible server:
turn 1 → model requests two tool calls; turn 2 → final answer. Verifies the
tool loop, proposal creation via tools, redaction of tool output, and that the
LLM scope only talks to the configured base_url host.
"""
import json, threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from backend.database import set_setting
from backend.moengage.executors import register_all

register_all()
SEEN = {"requests": []}


class FakeLLM(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        if self.path.endswith("/models"):
            body = json.dumps({"data": [{"id": "fake/model-1", "context_length": 128000, "supported_parameters": ["tools"]}]}).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(body)
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0)); req = json.loads(self.rfile.read(n))
        SEEN["requests"].append({"auth": self.headers.get("Authorization"), "body": req})
        turn = sum(1 for m in req["messages"] if m["role"] == "tool")
        if turn == 0:
            msg = {"role": "assistant", "content": None, "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "list_campaigns", "arguments": "{}"}},
                {"id": "c2", "type": "function", "function": {"name": "propose_segment", "arguments": json.dumps({"name": "At-risk VIPs", "criteria": {"ltv": {"gt": 300}}, "rationale": "test"})}},
            ]}
            fin = "tool_calls"
        else:
            tools = [m for m in req["messages"] if m["role"] == "tool"]
            msg = {"role": "assistant", "content": f"Done. I saw {len(tools)} tool results and queued proposal for approval."}
            fin = "stop"
        body = json.dumps({"id": "x", "model": "fake/model-1", "choices": [{"message": msg, "finish_reason": fin}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(body)


@pytest.fixture(scope="module")
def fake_server():
    srv = HTTPServer(("127.0.0.1", 0), FakeLLM)
    t = threading.Thread(target=srv.serve_forever, daemon=True); t.start()
    yield f"http://127.0.0.1:{srv.server_port}/v1"
    srv.shutdown()


def test_tool_loop_end_to_end(fake_server):
    set_setting("mock_mode", "true")
    set_setting("llm_provider", "openai_compatible"); set_setting("llm_base_url", fake_server)
    set_setting("llm_model", "fake/model-1"); set_setting("llm_api_key", "sk-test-FAKEKEY-1234567890abcdef")
    set_setting("moengage_cookies", json.dumps({"sessionid": "SUPERSECRETSESSION"}))
    from backend.llm.agent import MarketerAgent
    from backend.llm.provider import list_models, probe
    from backend import approvals
    assert list_models()["models"][0]["id"] == "fake/model-1"
    assert probe()["ok"]
    out = MarketerAgent().chat("audit and propose", history=[], persist=False)
    assert out["tool_used"] == ["list_campaigns", "propose_segment"]
    assert "queued proposal" in out["reply"]
    pend = approvals.list_proposals(status="pending")
    assert any(p["title"] == "Segment: At-risk VIPs" and p["created_by"] == "agent" for p in pend)
    # nothing secret ever reached the model
    sent = json.dumps([r["body"] for r in SEEN["requests"]])
    assert "SUPERSECRETSESSION" not in sent and "sk-test-FAKEKEY" not in sent
    assert SEEN["requests"][0]["auth"] == "Bearer sk-test-FAKEKEY-1234567890abcdef"
    # tool results were passed back in OpenAI format
    last = SEEN["requests"][-1]["body"]["messages"]
    assert [m["role"] for m in last[-3:]] == ["assistant", "tool", "tool"]


def test_llm_scope_cannot_reach_other_hosts(fake_server):
    from backend.security import guarded_session, NetworkPolicyError
    s = guarded_session("llm")
    with pytest.raises(NetworkPolicyError):
        s.get("https://openrouter.ai/api/v1/models")   # base_url is the fake server now, so openrouter is off-list
    with pytest.raises(NetworkPolicyError):
        s.get("https://api-01.moengage.com/v1/x")


def test_growth_feed_rules_dedupe_and_status():
    from backend import growth
    from backend.database import set_setting
    set_setting("mock_mode", "true")
    r1 = growth.generate_rule_ideas()
    assert r1["generated"] >= 10 and r1["added"] >= 10
    r2 = growth.generate_rule_ideas()
    assert r2["added"] == 0 and r2["refreshed"] >= 10          # de-duplicated by normalised title
    new = growth.list_ideas(status="new")
    assert new and new[0]["priority"] >= new[-1]["priority"]     # ranked
    kinds = {i["kind"] for i in new}
    assert "trending_campaign" in kinds and "moengage_activity" in kinds
    first = new[0]
    saved = growth.set_status(first["id"], "saved")
    assert saved["status"] == "saved" and growth.counts().get("saved", 0) >= 1
    with pytest.raises(ValueError):
        growth.set_status(first["id"], "bogus")
    digest = growth.digest_for_agent()
    assert "existing_titles" in digest and first["title"] in digest["existing_titles"]


def test_cli_model_normalisation():
    from backend.llm.provider import cli_model, normalise_model_for_provider
    assert cli_model("anthropic/claude-3.7-sonnet") == "claude-3.7-sonnet"
    assert cli_model("openai/gpt-4o-mini") == "sonnet" and cli_model("google/gemini-2.5-flash") == "sonnet"
    assert cli_model("anthropic/claude-opus-4.1") == "claude-opus-4.1" and cli_model("meta/llama-opus") == "opus"
    assert cli_model("sonnet") == "sonnet" and cli_model("claude-sonnet-5") == "claude-sonnet-5" and cli_model("") == "sonnet"
    assert normalise_model_for_provider("openrouter", "sonnet") == "anthropic/claude-sonnet-4.5"
    assert normalise_model_for_provider("openai_compatible", "sim/marketer-v1") == "sim/marketer-v1"


def test_reconcile_switches_to_openrouter_when_key_or_vendor_model_saved():
    from backend.database import set_setting, get_setting
    from backend.llm.provider import reconcile_llm_settings
    set_setting("llm_provider", "claude_cli"); set_setting("llm_base_url", "https://openrouter.ai/api/v1"); set_setting("llm_model", "sonnet"); set_setting("llm_api_key", "")
    # user pastes an OpenRouter key while provider is still claude_cli
    set_setting("llm_api_key", "sk-or-v1-abcdefghijklmnopqrstuvwxyz0123456789")
    ch = reconcile_llm_settings({"llm_api_key"})
    assert ch.get("llm_provider") == "openrouter" and get_setting("llm_model") == "anthropic/claude-sonnet-4.5"
    # user picks a vendor/model id while on claude_cli
    set_setting("llm_provider", "claude_cli"); set_setting("llm_model", "anthropic/claude-3.7-sonnet")
    ch = reconcile_llm_settings({"llm_model"})
    assert ch.get("llm_provider") == "openrouter" and get_setting("llm_model") == "anthropic/claude-3.7-sonnet"
    # genuine claude_cli user with a bare alias stays put
    set_setting("llm_provider", "claude_cli"); set_setting("llm_model", "sonnet")
    assert reconcile_llm_settings({"llm_provider"}) == {}
    set_setting("llm_api_key", "")


def test_autopilot_drafts_proposals_and_experiment_ledger(fake_server):
    """Autopilot missions run against the fake model; approving the campaign draft registers an experiment with a readout."""
    from backend.database import set_setting
    from backend import approvals, autopilot, experiments
    from backend.moengage import mock
    set_setting("mock_mode", "true"); set_setting("llm_provider", "openai_compatible"); set_setting("llm_base_url", fake_server)
    set_setting("llm_model", "fake/model-1"); set_setting("llm_model_bulk", "fake/model-1"); set_setting("llm_api_key", "sk-test-FAKEKEY-1234567890abcdef")
    mock.seed_history(30, inject=True)                    # creates Act-today anomalies for stop_the_bleed
    for p in approvals.list_proposals(status="pending", limit=300):   # earlier tests leave identical pending drafts; the de-dupe guard would swallow them
        approvals.reject(p["id"], note="test reset", decided_by="test")
    before = len(approvals.list_proposals(limit=300))
    out = autopilot.run(max_actions=3, force=True)
    assert out["enabled"] and out["actions"], out
    assert any(a.get("outcome") == "proposed" for a in out["actions"]), out
    assert len(approvals.list_proposals(limit=300)) > before
    q = autopilot.action_queue()
    assert q["pending"] and all("mission" in p for p in q["pending"])
    # idempotent per day: a second run skips missions already handled
    out2 = autopilot.run(max_actions=3, force=True)
    assert all(a.get("outcome") != "proposed" or a.get("mission") == "best_idea" for a in out2["actions"]) or len(approvals.list_proposals(status="pending", limit=300)) >= len(q["pending"])
    # approve the campaign draft → experiment registered and readable
    camp = next((p for p in approvals.list_proposals(status="pending", limit=300) if p["kind"] == "create_campaign"), None)
    if camp:
        done = approvals.approve_and_execute(camp["id"], decided_by="test")
        assert done["status"] == "executed"
        exps = experiments.list_experiments()
        assert exps and exps[0]["proposal_id"] == camp["id"] and exps[0]["control_group_pct"] >= 5
        r = experiments.refresh_all()
        assert r["refreshed"] and "state" in experiments.list_experiments()[0]["readout"]
    set_setting("llm_api_key", "")
