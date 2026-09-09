import json, pytest, requests
from backend.security import redact, secret_store, guarded_session, NetworkPolicyError, audit
from backend.security.audit import verify_chain
from backend.database import set_setting, get_setting, get_all_settings, get_db


def test_secret_roundtrip_encrypted_at_rest():
    set_setting("llm_api_key", "sk-or-v1-abcdefghijklmnopqrstuvwxyz0123456789")
    raw = get_db().execute("SELECT value FROM settings WHERE key='llm_api_key'").fetchone()[0]
    assert raw.startswith("enc:v1:") and "sk-or" not in raw
    assert get_setting("llm_api_key") == "sk-or-v1-abcdefghijklmnopqrstuvwxyz0123456789"
    s = get_all_settings()
    assert "llm_api_key" not in s and s["llm_api_key_set"] == "true"


def test_redaction_patterns():
    samples = [
        "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.abcdefghijklmnopqrstuvwxyz",
        "cookie: sessionid=abcdef123456; csrftoken=zzzzzzzzzz",
        "key sk-or-v1-abcdefghijklmnopqrstuvwxyz0123456789 here",
        "https://user:pass1234@host/x?api_key=SECRETVALUE123",
    ]
    for s in samples:
        out = redact(s)
        for bad in ("abcdef123456", "zzzzzzzzzz", "sk-or-v1-abcdefghij", "SECRETVALUE123", "pass1234", "eyJhbGciOiJIUzI1NiJ9."):
            assert bad not in out, (s, out)
    # registered literal secrets are scrubbed anywhere
    set_setting("moengage_cookies", json.dumps({"sessionid": "LITERALSECRETVALUE"}))
    get_setting("moengage_cookies")
    assert "LITERALSECRETVALUE" not in redact("saw LITERALSECRETVALUE in log")


def test_netguard_blocks_off_allowlist_and_http():
    s = guarded_session("moengage")
    with pytest.raises(NetworkPolicyError):
        s.get("https://evil.example.com/steal")
    with pytest.raises(NetworkPolicyError):
        s.get("http://dashboard-01.moengage.com/insecure")
    with pytest.raises(NetworkPolicyError):
        s.get("https://user:pw@dashboard-01.moengage.com/x")
    assert s.trust_env is False
    m = guarded_session("market")
    with pytest.raises(NetworkPolicyError):
        m.get("https://dashboard-01.moengage.com/v4/campaigns")   # market scope can never reach MoEngage
    llm = guarded_session("llm")
    with pytest.raises(NetworkPolicyError):
        llm.post("https://api-01.moengage.com/v1/event/x", json={})  # LLM scope can never reach MoEngage


def test_audit_chain_tamper_evident(isolated_db):
    audit("test.one", {"a": 1}); audit("test.two", {"cookie": "sessionid=abcdefgh1234"})
    assert verify_chain()["ok"]
    import importlib; aud = importlib.import_module('backend.security.audit')
    lines = open(aud.AUDIT_FILE).read().splitlines()
    assert "abcdefgh1234" not in "".join(lines)
    idx = next(i for i, l in enumerate(lines) if '"test.one"' in l)
    lines[idx] = lines[idx].replace('"a": 1', '"a": 2')
    open(aud.AUDIT_FILE, "w").write("\n".join(lines) + "\n")
    assert verify_chain()["ok"] is False


def test_local_token_required():
    from fastapi.testclient import TestClient
    from backend.main import app, local_token
    c = TestClient(app, base_url="http://127.0.0.1")
    assert c.get("/api/health").status_code == 200
    assert c.get("/api/status").status_code == 401
    assert c.get("/api/status", headers={"X-Local-Token": "wrong"}).status_code == 401
    r = c.get("/api/status", headers={"X-Local-Token": local_token})
    assert r.status_code == 200 and r.json()["security"]["bind"] == "127.0.0.1"
    assert c.get("/api/status", headers={"X-Local-Token": local_token, "Host": "evil.com"}).status_code == 403
    html = c.get("/").text if c.get("/").status_code == 200 else ""
    assert (local_token in html) or html == ""


def test_proxy_identity_only_from_loopback(monkeypatch):
    from fastapi.testclient import TestClient
    from backend.main import app, local_token
    c = TestClient(app, base_url="http://127.0.0.1")
    # TestClient's peer is 'testclient' (not loopback) → identity header must be ignored
    r = c.post("/api/approvals/propose", headers={"X-Local-Token": local_token, "Tailscale-User-Login": "mallory@evil"},
               json={"kind": "create_segment", "title": "t", "payload": {"name": "n", "criteria": {"a": 1}}, "rationale": "r"})
    assert r.status_code == 200 and r.json()["created_by"] == "user"
    import backend.security.localauth as la
    class Req:  # loopback peer with proxy identity
        client = type("C", (), {"host": "127.0.0.1"})(); headers = {"tailscale-user-login": "kushagra@company.com"}
    assert la.request_actor(Req()) == "kushagra@company.com"
    class Req2:
        client = type("C", (), {"host": "10.0.0.5"})(); headers = {"tailscale-user-login": "x@y"}
    assert la.request_actor(Req2()) == "user"
