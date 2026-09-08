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
