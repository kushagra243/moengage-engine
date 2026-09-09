#!/usr/bin/env python3
"""moengage-engine CLI — same engine as the UI, for terminal use and scripts."""
import argparse
import getpass
import json
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
_VPY = os.path.join(ROOT, ".venv", "bin", "python3")
if os.path.exists(_VPY) and os.path.realpath(sys.executable) != os.path.realpath(_VPY):
    os.execv(_VPY, [_VPY] + sys.argv)   # always run inside the project venv

from backend.database import init_db, set_setting, get_setting  # noqa: E402
from backend.security import install_log_redaction  # noqa: E402

init_db()
install_log_redaction()


def _print(obj):
    print(json.dumps(obj, indent=2, default=str))


def cmd_status(a):
    from backend.moengage import MoEngageClient, registry_status
    from backend.llm import llm_settings
    c = MoEngageClient()
    cfg = llm_settings()
    _print({"moengage": c.verify_session(), "integration": {k: v for k, v in registry_status().items() if k != "roles"},
            "llm": {"provider": cfg["provider"], "model": cfg["model"], "configured": bool(cfg["api_key"]) or cfg["provider"] == "claude_cli"}})


def cmd_set_cookies(a):
    raw = a.value or (open(a.file).read() if a.file else getpass.getpass("Paste cookie header / JSON (hidden): "))
    from backend.moengage.session import parse_cookies, cookie_summary
    parsed = parse_cookies(raw)
    if not parsed:
        sys.exit("no cookies parsed")
    set_setting("moengage_cookies", json.dumps(parsed))
    _print({"saved": True, "cookies": cookie_summary(parsed)})


def cmd_set_key(a):
    key_map = {"data": "moengage_data_api_key", "segmentation": "moengage_segmentation_key", "campaigns": "moengage_campaign_key", "inform": "moengage_inform_key", "llm": "llm_api_key"}
    k = key_map[a.kind]
    v = a.value or getpass.getpass(f"{a.kind} key (hidden): ")
    set_setting(k, v)
    print(f"saved {k} (encrypted)")


def cmd_set(a):
    if not a.key.startswith(("moengage_", "llm_", "market_", "schedule_", "refresh_", "mock_mode")):
        sys.exit("refusing unknown setting")
    set_setting(a.key, a.value)
    print(f"{a.key} = {a.value if 'key' not in a.key and 'cookie' not in a.key else '<hidden>'}")


def cmd_learn(a):
    from backend.moengage import capture
    with open(a.har) as f:
        text = f.read()
    _print(capture.learn(text, app_id=get_setting("moengage_app_id", ""), db_name=get_setting("moengage_db_name", "")))


def cmd_verify(a):
    from backend.moengage import capture
    from backend.moengage.session import DashboardSession
    _print(capture.verify(DashboardSession(), roles=a.roles or None))


def cmd_probe_keys(a):
    from backend.moengage.public_api import PublicAPI
    _print(PublicAPI().probe())


def cmd_campaigns(a):
    from backend.moengage import MoEngageClient, DataUnavailable
    try:
        rows = MoEngageClient().get_campaigns()
        for r in rows:
            print(f"- {r.get('name')} [{r.get('channel')}] {r.get('status')}  ctr={r.get('ctr')} src={r.get('_source')}")
    except DataUnavailable as e:
        print("unavailable:", e)


def cmd_segments(a):
    from backend.moengage import MoEngageClient, DataUnavailable
    try:
        for r in MoEngageClient().get_segments():
            print(f"- {r.get('name')} ({r.get('estimated_reach', '?')}) src={r.get('_source')}")
    except DataUnavailable as e:
        print("unavailable:", e)


def cmd_snapshot(a):
    from backend.moengage import MoEngageClient
    from backend.anomaly import record_snapshot, detect_anomalies
    c = MoEngageClient()
    snap = record_snapshot(c.get_campaigns(), source=c.mode, snapshot_date=a.date)
    _print({"snapshot": snap, "anomalies": detect_anomalies(source=c.mode, snapshot_date=a.date)})


def cmd_anomalies(a):
    from backend.moengage import MoEngageClient
    from backend.anomaly import detect_anomalies
    _print(detect_anomalies(source=MoEngageClient().mode, persist=False))


def cmd_market(a):
    from backend.market import market_context
    ctx = market_context(force=a.force)
    print(ctx["narrative"])
    if a.hooks:
        for h in ctx["hooks"]["hooks"]:
            print(f"\n[{h['id']}] {h['trigger']}\n  segments: {h['segments']}\n  channel: {h['channel']} | angle: {h['angle']} | timing: {h['timing']}")


def cmd_chat(a):
    from backend.llm.agent import MarketerAgent
    from backend.llm.provider import LLMError
    try:
        out = MarketerAgent().chat(" ".join(a.message), persist=not a.no_persist)
    except LLMError as e:
        sys.exit(f"LLM not available: {e}\n  → ./cli.py set-key llm   (OpenRouter / OpenAI-compatible)\n  → or: claude login && ./cli.py set llm_provider claude_cli")
    print(out["reply"])
    if out.get("tool_used"):
        print("\n[tools]", ", ".join(out["tool_used"]), "| model:", out.get("model"))


def cmd_approvals(a):
    from backend import approvals
    from backend.moengage.executors import register_all
    register_all()
    if a.action == "list":
        for p in approvals.list_proposals(status=a.status):
            print(f"#{p['id']} [{p['status']}] {p['kind']}: {p['title']}")
    elif a.action == "show":
        _print(approvals.get_proposal(a.id))
    elif a.action == "approve":
        _print(approvals.approve_and_execute(a.id, decided_by="cli", note=a.note or ""))
    elif a.action == "reject":
        _print(approvals.reject(a.id, note=a.note or "", decided_by="cli"))


def cmd_daily_run(a):
    from backend.moengage.executors import register_all
    from backend.scheduler import scheduler_service
    register_all()
    r = scheduler_service.trigger_run(trigger_type="cli")
    print(r.get("summary") or r)
    if a.full:
        _print(r)


def cmd_growth(a):
    from backend import growth
    if a.action == "refresh":
        out = {"rules": growth.generate_rule_ideas()}
        if a.llm:
            from backend.llm.agent import MarketerAgent
            out["agent"] = growth.generate_agent_ideas(MarketerAgent(), n=a.n)
        _print(out)
    elif a.action == "list":
        for i in growth.list_ideas(status=a.status or "new", limit=a.n or 40):
            print(f"#{i['id']:<4} [{i['kind']:18}] p{i['priority']:<3} {i['title']}\n      why: {i['why'][:140]}")
    elif a.action == "set":
        _print(growth.set_status(a.id, a.status))


PLIST = os.path.expanduser("~/Library/LaunchAgents/com.moengage-engine.plist")


def cmd_service(a):
    """Run persistently under launchd (starts at login, restarts on crash). No model needed."""
    import subprocess
    label = "com.moengage-engine"
    if a.action == "install":
        port = a.port or "8080"
        plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{label}</string>
  <key>ProgramArguments</key><array><string>{os.path.join(ROOT, '.venv', 'bin', 'python3')}</string><string>{os.path.join(ROOT, 'start.py')}</string></array>
  <key>WorkingDirectory</key><string>{ROOT}</string>
  <key>EnvironmentVariables</key><dict><key>PORT</key><string>{port}</string><key>MOE_ALLOWED_HOSTS</key><string>{a.allowed_hosts or ''}</string><key>PATH</key><string>/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin</string></dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>{os.path.join(ROOT, 'data', 'logs', 'service.out.log')}</string>
  <key>StandardErrorPath</key><string>{os.path.join(ROOT, 'data', 'logs', 'service.err.log')}</string>
</dict></plist>
"""
        os.makedirs(os.path.dirname(PLIST), exist_ok=True); os.makedirs(os.path.join(ROOT, "data", "logs"), exist_ok=True)
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", PLIST], capture_output=True)
        open(PLIST, "w").write(plist)
        r = subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", PLIST], capture_output=True, text=True)
        print("installed", PLIST); print(r.stdout or r.stderr or "loaded"); print(f"console: http://127.0.0.1:{port}  (starts at login, restarts on crash; runs snapshots, anomalies, market and growth rules on schedule without any model)")
    elif a.action == "uninstall":
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", PLIST], capture_output=True)
        if os.path.exists(PLIST):
            os.remove(PLIST)
        print("removed", PLIST)
    else:
        r = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/{label}"], capture_output=True, text=True)
        if r.returncode == 0:
            state = [l.strip() for l in r.stdout.splitlines() if "state =" in l or "pid =" in l]
            print("service loaded;", "; ".join(state) or "running")
        else:
            print("service not installed (run: ./cli.py service install)")


def cmd_mock(a):
    from backend.moengage import mock
    if a.action == "seed":
        _print(mock.seed_history(a.days, inject=not a.clean))


def cmd_audit_log(a):
    from backend.security.audit import tail, verify_chain
    _print({"chain": verify_chain(), "entries": tail(a.n)})


def main():
    p = argparse.ArgumentParser(description="moengage-engine CLI")
    sp = p.add_subparsers(dest="cmd", required=True)
    sp.add_parser("status").set_defaults(fn=cmd_status)
    s = sp.add_parser("set-cookies"); s.add_argument("--value"); s.add_argument("--file"); s.set_defaults(fn=cmd_set_cookies)
    s = sp.add_parser("set-key"); s.add_argument("kind", choices=["data", "segmentation", "campaigns", "inform", "llm"]); s.add_argument("--value"); s.set_defaults(fn=cmd_set_key)
    s = sp.add_parser("set"); s.add_argument("key"); s.add_argument("value"); s.set_defaults(fn=cmd_set)
    s = sp.add_parser("learn", help="import a HAR capture"); s.add_argument("har"); s.set_defaults(fn=cmd_learn)
    s = sp.add_parser("verify", help="probe dashboard endpoints read-only"); s.add_argument("roles", nargs="*"); s.set_defaults(fn=cmd_verify)
    sp.add_parser("probe-keys").set_defaults(fn=cmd_probe_keys)
    sp.add_parser("campaigns").set_defaults(fn=cmd_campaigns)
    sp.add_parser("segments").set_defaults(fn=cmd_segments)
    s = sp.add_parser("snapshot"); s.add_argument("--date"); s.set_defaults(fn=cmd_snapshot)
    sp.add_parser("anomalies").set_defaults(fn=cmd_anomalies)
    s = sp.add_parser("market"); s.add_argument("--force", action="store_true"); s.add_argument("--hooks", action="store_true"); s.set_defaults(fn=cmd_market)
    s = sp.add_parser("chat"); s.add_argument("message", nargs="+"); s.add_argument("--no-persist", action="store_true"); s.set_defaults(fn=cmd_chat)
    s = sp.add_parser("approvals"); s.add_argument("action", choices=["list", "show", "approve", "reject"]); s.add_argument("id", nargs="?", type=int); s.add_argument("--status"); s.add_argument("--note"); s.set_defaults(fn=cmd_approvals)
    s = sp.add_parser("daily-run"); s.add_argument("--full", action="store_true"); s.set_defaults(fn=cmd_daily_run)
    s = sp.add_parser("mock", help="demo data: seed"); s.add_argument("action", choices=["seed"]); s.add_argument("--days", type=int, default=30); s.add_argument("--clean", action="store_true", help="no injected faults"); s.set_defaults(fn=cmd_mock)
    s = sp.add_parser("audit-log"); s.add_argument("-n", type=int, default=30); s.set_defaults(fn=cmd_audit_log)
    s = sp.add_parser("growth", help="growth feed: refresh | list | set"); s.add_argument("action", choices=["refresh", "list", "set"]); s.add_argument("id", nargs="?", type=int); s.add_argument("--status"); s.add_argument("--llm", action="store_true"); s.add_argument("-n", type=int, default=5); s.set_defaults(fn=cmd_growth)
    s = sp.add_parser("service", help="persistent background service (launchd): install | uninstall | status"); s.add_argument("action", choices=["install", "uninstall", "status"]); s.add_argument("--port"); s.add_argument("--allowed-hosts", help="comma list of proxy hostnames allowed in the Host header, e.g. engine.tailnet.ts.net"); s.set_defaults(fn=cmd_service)
    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
