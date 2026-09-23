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
    if a.kind == "llm":
        from backend.llm.provider import reconcile_llm_settings
        for kk, vv in reconcile_llm_settings({"llm_api_key"}).items():
            print(f"  adjusted {kk} → {vv}")


def cmd_set(a):
    from backend.settings_policy import allowed, normalise
    from backend.security import is_secret_key
    if not allowed(a.key):
        sys.exit("refusing unknown setting")
    if is_secret_key(a.key):
        sys.exit(f"{a.key} is a secret: use ./cli.py secret {a.key} (hidden prompt, never an argument)")
    try:
        a.value = normalise(a.key, a.value)
    except ValueError as e:
        sys.exit(str(e))
    set_setting(a.key, a.value)
    if a.key in ("llm_provider", "llm_model", "llm_base_url"):
        from backend.llm.provider import reconcile_llm_settings
        for k, v in reconcile_llm_settings({a.key}).items():
            print(f"  adjusted {k} → {v}")
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


def cmd_housekeeping(a):
    from backend.growth import expire_stale
    from backend.approvals import expire_stale as expire_props
    from backend.plans import archive_past
    print(json.dumps({"ideas": expire_stale(), "proposals": expire_props(), "plans_archived": archive_past()}, indent=2))


def cmd_selfheal(a):
    from backend.selfheal import health_report, rollback_last_merge
    if a.action == "rollback":
        print(json.dumps(rollback_last_merge("cli"), indent=2)); return
    r = health_report(run_tests=a.tests)
    print(f"status: {r['status']}  tool errors 48h: {r['tool_errors_48h']}  fix requests: {len(r['fix_requests'])}  last merge: {(r.get('last_merge') or {}).get('post_commit')}")
    for f in r["fix_requests"]:
        print(f"- [{f['signature']}] {f['title']}")
    if r.get("tests"):
        print("tests:", "passed" if r["tests"]["passed"] else "FAILED"); print(r["tests"]["tail"][-600:])


def _port_open(port):
    import socket as _s
    with _s.socket(_s.AF_INET, _s.SOCK_STREAM) as c:
        c.settimeout(1.5)
        return c.connect_ex(("127.0.0.1", port)) == 0


def _build_of(port):
    """(commit, boot id) the running engine is serving, read from the shell it hands the browser.
    (None, None) means it is not serving one — either it is down, or it predates the build stamp."""
    import re, urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as r:
            html = r.read(4096).decode("utf-8", "ignore")
        b = re.search(r'name="build" content="([^"]+)"', html)
        k = re.search(r'name="boot" content="([^"]+)"', html)
        return (b.group(1) if b else None), (k.group(1) if k else None)
    except Exception:
        return None, None


def _engine_pids():
    import subprocess as _sp
    return [int(x) for x in _sp.run(["pgrep", "-f", "start.py"], capture_output=True, text=True).stdout.split() if int(x) != os.getpid()]


def _restart_detached(root, port, pids):
    """Used only when the running engine is too old to reload itself. Nothing under data/ is touched,
    so the MoEngage keys, the model key and every setting come back with it."""
    import signal as _sig, subprocess as _sp, time as _t
    for pid in pids:
        try:
            os.kill(pid, _sig.SIGTERM)
        except ProcessLookupError:
            pass
    for _ in range(20):
        if not _port_open(port):
            break
        _t.sleep(0.5)
    logs = os.path.join(root, "data", "logs")
    os.makedirs(logs, exist_ok=True)
    out = open(os.path.join(logs, "engine.log"), "a")
    vpy = os.path.join(root, ".venv", "bin", "python")
    _sp.Popen([vpy if os.path.exists(vpy) else sys.executable, os.path.join(root, "start.py")], cwd=root,
              stdout=out, stderr=out, start_new_session=True, env={**os.environ, "PORT": str(port)})
    for _ in range(40):
        _t.sleep(1)
        if _port_open(port):
            return True
    return False


def cmd_update(a):
    """Pull the latest code and apply it to the running engine. Settings and keys live in data/ and are never touched."""
    import signal as _sig, subprocess as _sp, time as _t
    root = os.path.dirname(os.path.abspath(__file__))
    port = int(a.port or os.environ.get("PORT", "8080"))
    before_head = _sp.run(["git", "rev-parse", "--short", "HEAD"], cwd=root, capture_output=True, text=True).stdout.strip()
    running = _port_open(port)
    before_run, before_boot = _build_of(port) if running else (None, None)
    if not a.no_pull:
        r = _sp.run(["git", "pull", "--rebase", "--autostash", "origin", a.branch], cwd=root, capture_output=True, text=True)
        print((r.stdout or "").strip() or (r.stderr or "").strip())
        if r.returncode != 0:
            print("[!] pull failed — nothing changed and the engine is still serving the old code"); return
    head = _sp.run(["git", "rev-parse", "--short", "HEAD"], cwd=root, capture_output=True, text=True).stdout.strip()
    if head != before_head:
        print(_sp.run(["git", "log", "--oneline", f"{before_head}..{head}"], cwd=root, capture_output=True, text=True).stdout.strip() or f"{before_head} → {head}")
    elif not a.no_pull:
        print(f"already up to date at {head}")
    chk = _sp.run([sys.executable, "-c", "import sys; sys.path.insert(0, %r); import backend.main" % root], cwd=root,
                  capture_output=True, text=True, env={**os.environ, "MOE_IMPORT_CHECK": "1"})
    if chk.returncode != 0:
        print("[!] the new code does not import — the running engine was left alone:\n" + chk.stderr[-800:]); return
    pids = _engine_pids()
    if not running or not pids:
        print("engine is not running · start it with: .venv/bin/python start.py"); return
    if before_run is None:
        # too old to have the in-place reload; SIGHUP would kill it, so stop and start it instead
        print("the running engine predates the in-place reload, so it is being stopped and started once.")
        ok = _restart_detached(root, port, pids)
        if ok:
            print(f"engine back up on http://127.0.0.1:{port} · keys and settings live in data/agent.db and came back with it")
            print("from now on `./cli.py update` reloads in place, with no stop at all.")
        else:
            print("[!] it did not come back — start it by hand: .venv/bin/python start.py (see data/logs/engine.log)")
        return
    for pid in pids:
        try:
            os.kill(pid, _sig.SIGHUP)
        except ProcessLookupError:
            pass
    for _ in range(30):
        _t.sleep(1)
        now, boot = _build_of(port)
        if boot and boot != before_boot:                       # a new boot id proves the process actually reloaded
            print(f"reloaded in place · pid {pids[0]} now serving {now} · nothing restarted, keys and settings untouched")
            return
    print(f"[!] the reload did not land within 30s — check data/logs/engine.log; it is still serving {before_run} (boot {before_boot})")


def cmd_alerts(a):
    """Market Alerts 2.0 from the terminal: why nothing reaches MoEngage, what would fire now, and the campaign the agent designed."""
    from backend.alerts2 import discovery, brief
    from backend import approvals
    if a.action == "preflight":
        st = discovery.status()
        from backend.alerts2 import service as _svc
        from backend.database import get_setting as _gs
        print(f"stage: {st['stage']}   live: {st['live']}   why not: {st['why_not_live'] or '-'}   kill switch: {_svc.kill_switch()}   mock: {_gs('mock_mode', 'true')}")
        for c in st["preflight"]:
            mark = "OK " if c["ok"] else ("?? " if c["ok"] is None else "XX ")
            print(f"{mark}{c['check']}: {c['detail']}" + (f"\n     fix: {c['fix']}" if c.get("fix") else ""))
        print("\ncohorts:")
        for c in st["cohorts"]:
            print(f"  {c['id']:15} {c['segment']:22} {c['event']:24} {'ACTIVE' if c['active'] else ('locked' if c['locked'] else 'ready')}")
        props = [p for p in approvals.list_proposals(limit=100) if p.get("kind") in ("create_campaign", "ma2_discovery") and "iscovery" in str(p.get("title"))][:8]
        print("\nproposals:")
        for p in props or []:
            print(f"  #{p['id']:<4} {p['kind']:16} {p['status']:9} {str(p.get('title'))[:70]}" + (f"\n        error: {str(p.get('error'))[:200]}" if p.get("error") else ""))
        if not props:
            print("  none yet - press Review the launch brief in the Market Alerts tab, or: ./cli.py alerts brief")
    elif a.action == "detect":
        r = discovery.run("dry_run", actor="cli")
        s = r.get("summary") or {}
        print(f"detected {r.get('detected')}  would fire {r.get('would_send')}  queued {r.get('queued')}  held {r.get('held_quiet_hours')}  suppressed {r.get('suppressed')}  {s.get('suppression_reasons')}")
        for d in (s.get("decisions") or [])[:15]:
            print(f"  {d['decision']:14} {d['signal']:14} {d['token']:6} {d.get('reason') or ''}  -> {', '.join(d.get('cohorts') or []) or '-'}   {d['title']}")
    elif a.action == "brief":
        b = brief.build(days=0, cohort_ids=(a.cohorts.split(",") if a.cohorts else ["internal"]), with_dry_run=not a.no_dry_run)
        print(brief.markdown(b))
    elif a.action == "coverage":
        c = discovery.coverage(a.day)
        print(f"day {c['day']}  expected {c['expected']}  recorded {c['recorded']}  missed {c['missed_count']}  outcomes {c.get('outcomes')}")
        for m in c["missed"][:20]:
            print("  missed:", m["det_key"])


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
    sp.add_parser("housekeeping", help="expire stale ideas/proposals, archive past flight plans").set_defaults(fn=cmd_housekeeping)
    s = sp.add_parser("selfheal", help="engine health: report | rollback (revert last agent merge)"); s.add_argument("action", nargs="?", default="report", choices=["report", "rollback"]); s.add_argument("--tests", action="store_true"); s.set_defaults(fn=cmd_selfheal)
    from backend import cli_ops
    cli_ops.add_parsers(sp)
    s = sp.add_parser("audit-log"); s.add_argument("-n", type=int, default=30); s.set_defaults(fn=cmd_audit_log)
    s = sp.add_parser("alerts", help="Market Alerts 2.0: preflight (why nothing reaches MoEngage) | detect | brief | coverage")
    s.add_argument("action", choices=["preflight", "detect", "brief", "coverage"]); s.add_argument("--cohorts"); s.add_argument("--day"); s.add_argument("--no-dry-run", action="store_true"); s.set_defaults(fn=cmd_alerts)
    s = sp.add_parser("update", help="pull the latest code and reload the running engine in place (keys and settings untouched)")
    s.add_argument("--branch", default="main"); s.add_argument("--port"); s.add_argument("--no-pull", action="store_true", help="reload what is already checked out")
    s.set_defaults(fn=cmd_update)
    s = sp.add_parser("growth", help="growth feed: refresh | list | set"); s.add_argument("action", choices=["refresh", "list", "set"]); s.add_argument("id", nargs="?", type=int); s.add_argument("--status"); s.add_argument("--llm", action="store_true"); s.add_argument("-n", type=int, default=5); s.set_defaults(fn=cmd_growth)
    s = sp.add_parser("service", help="persistent background service (launchd): install | uninstall | status"); s.add_argument("action", choices=["install", "uninstall", "status"]); s.add_argument("--port"); s.add_argument("--allowed-hosts", help="comma list of proxy hostnames allowed in the Host header, e.g. engine.tailnet.ts.net"); s.set_defaults(fn=cmd_service)
    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
