"""
The operator's terminal: everything the quiet terminal can do, as commands a person or Claude Code can run.

The division of labour this is built for: Claude does the heavy lifting (reads state as JSON, judges, designs the campaign,
writes the copy, chooses the cohort, explains) and the CLI does the doing (saves answers, queues launches, sends tests,
approves). Two things stay with a human and cannot be scripted past: secrets are typed at a hidden prompt (never in an
argument, never in chat, never in shell history), and every MoEngage write is an approval that a named person makes.

Every command takes --json for machine reading; without it, plain text for people.
"""
from __future__ import annotations
import getpass
import json
import os
import sys
from typing import Any, Dict, List, Optional

from .settings_policy import save_plain


def _out(a, obj: Any, text: Optional[str] = None) -> None:
    if getattr(a, "json", False) or text is None:
        print(json.dumps(obj, indent=2, default=str, ensure_ascii=False))
    else:
        print(text)


def _hidden(prompt: str, env: str = "", stdin: bool = False) -> str:
    """A secret from the operator: an env var they set, stdin they piped, or a hidden prompt. Never an argument."""
    if env:
        v = os.environ.get(env, "")
        if not v:
            sys.exit(f"environment variable {env} is empty")
        return v.strip()
    if stdin or not sys.stdin.isatty():
        return sys.stdin.read().strip()
    return getpass.getpass(prompt).strip()


def _boot():
    from .moengage.executors import register_all
    from . import test_sends
    register_all(); test_sends.register()
    for mod in ("signals", "research", "sop_improve", "sop_requests", "alerts2.service", "alerts2.discovery"):
        try:
            __import__("importlib").import_module(f"backend.{mod}").register()
        except Exception:
            pass


# ── read ─────────────────────────────────────────────────────────────────────
def today(a):
    from . import v3
    t = v3.today()
    lines = [t["dateline"], t["headline"], ""]
    for d in t["decisions"]:
        lines.append(f"[{d['n']}] {d['id']:32} {d['tag']:22} {d['title']}")
        if d.get("blocked"):
            lines.append(f"      blocked: {d['blocked']}  → ./cli.py asks")
        lines.append(f"      {d['primary']['label']} · {d['facts'][0]['k'].lower()} {d['facts'][0]['v']} · confidence {d['facts'][1]['v']}")
    if t["moved"]:
        lines += ["", "what moved:"] + [f"  {m['delta']:14} {m['what'][:90]}  → {m['do']}" for m in t["moved"]]
    if t["handled"]:
        lines += ["", "handled without you:"] + [f"  ✓ {h}" for h in t["handled"]]
    _out(a, t, "\n".join(lines))


def asks(a):
    from . import v3_ops
    r = v3_ops.asks()
    lines = [r["lead"], ""]
    for x in r["asks"]:
        lines.append(f"[{x['n']}] {x['id']:36} {x['group']:14} {x['title']}")
        lines.append(f"      why: {x['why'][:160]}")
        lines.append(f"      where: {x['where'][:160]}")
        if x.get("fields"):
            lines.append("      answer with: ./cli.py answer " + x["id"] + " " + " ".join(f"{f['key']}={'<hidden prompt>' if f['type'] == 'secret' else '<' + f['label'].lower() + '>'}" for f in x["fields"]))
        elif x.get("button"):
            lines.append(f"      confirm with: ./cli.py answer {x['id']}   ({x['button']})")
        if x.get("command"):
            lines.append(f"      run: {x['command']}")
    if r["waiting"]:
        lines += ["", "waiting on other teams:"] + [f"  {w['id']:14} {w['title']}" for w in r["waiting"]]
    _out(a, r, "\n".join(lines))


def doctor(a):
    """One screen: connection, what is missing, the alerts preflight, Telegram, skills read. The first thing Claude runs."""
    from . import v3_ops
    from .alerts2 import discovery
    from . import telegram_out, skill_router
    from .database import get_setting
    from .llm import llm_settings
    cfg = llm_settings()
    d = {"mock_mode": get_setting("mock_mode", "true"), "model": {"provider": cfg["provider"], "model": cfg["model"], "configured": bool(cfg["api_key"]) or cfg["provider"] == "claude_cli"},
         "asks": [{"id": x["id"], "title": x["title"]} for x in v3_ops.asks()["asks"]], "preflight": discovery.preflight(), "telegram": telegram_out.status(),
         "skills_never_opened": skill_router.usage(7)["never_opened"]}
    lines = [f"mode: {'practice (mock)' if d['mock_mode'] == 'true' else 'LIVE'}   model: {cfg['provider']} · {cfg['model']} · {'ready' if d['model']['configured'] else 'NO KEY'}", ""]
    lines.append("missing answers:" if d["asks"] else "missing answers: none")
    lines += [f"  {x['id']:36} {x['title']}" for x in d["asks"]]
    lines.append(""); lines.append("alerts preflight:")
    for c in d["preflight"]:
        mark = "OK " if c["ok"] else ("?? " if c["ok"] is None else "XX ")
        lines.append(f"  {mark}{c['check']}: {c['detail']}" + (f"\n       fix: {c['fix']}" if c.get("fix") and not c["ok"] else ""))
    tg = d["telegram"]
    lines.append(""); lines.append(f"telegram: {'on · ' + str(tg['sent_today']) + ' posted today' if tg['on'] else 'set up, off' if tg['configured'] else 'bot saved, chat missing' if tg['token_set'] else 'not set up'}" + (f" · last error {tg['last_error']}" if tg.get("last_error") else ""))
    _out(a, d, "\n".join(lines))


# ── write ────────────────────────────────────────────────────────────────────
def answer(a):
    """Answer one Ask. key=value pairs; a secret field is never taken from the argument — it is prompted hidden."""
    from . import v3_ops
    ask = next((x for x in v3_ops.asks()["asks"] if x["id"] == a.id), None)
    if not ask:
        sys.exit(f"no open question with id {a.id}  (./cli.py asks)")
    values: Dict[str, Any] = {}
    for kv in a.values or []:
        k, _, v = kv.partition("=")
        values[k] = v
    for f in ask.get("fields") or []:
        if f["type"] == "secret":
            if values.get(f["key"]):
                sys.exit(f"{f['key']} is a secret: do not pass it as an argument. Run the command without it and type it at the hidden prompt (or pipe it on stdin).")
            values[f["key"]] = _hidden(f"{f['label']} (hidden): ", env=a.env or "", stdin=a.stdin)
        elif f["type"] == "list" and values.get(f["key"]) and os.path.exists(str(values[f["key"]])):
            values[f["key"]] = open(values[f["key"]]).read()
    r = v3_ops.answer(a.id, values, save_plain, actor="cli")
    text = ("done · " if r.get("ok") else "NOT done · ") + str(r.get("toast") or "")
    if r.get("next"):
        text += f"\nnext: {r['next']['label']} ({r['next']['go']})"
    left = (r.get("asks") or {}).get("open")
    if left is not None:
        text += f"\n{left} question(s) still open"
    _out(a, {k: v for k, v in r.items() if k != "asks"} | {"open": left}, text)
    if not r.get("ok"):
        sys.exit(1)


def secret(a):
    """Save one secret setting at a hidden prompt: telegram_bot_token, moengage_campaign_key, llm_api_key …"""
    from .security import is_secret_key
    from .settings_policy import allowed
    if not allowed(a.key):
        sys.exit("refusing unknown setting")
    if not is_secret_key(a.key):
        sys.exit(f"{a.key} is not a secret; use ./cli.py set {a.key} <value>")
    v = _hidden(f"{a.key} (hidden): ", env=a.env or "", stdin=a.stdin)
    if not v:
        sys.exit("nothing entered")
    save_plain({a.key: v})
    _out(a, {"saved": a.key}, f"saved {a.key} (encrypted on this machine)")


def telegram(a):
    from . import telegram_out as t
    if a.action == "status":
        st = t.status()
        if st["token_set"] and not st["chat_set"]:
            st["chats"] = t.discover_chats()
        _out(a, st, json.dumps(st, indent=2))
    elif a.action == "setup":
        if not t.token() or a.new_token:
            t.save(_hidden("bot token from @BotFather (hidden): ", env=a.env or "", stdin=a.stdin), actor="cli")
        if a.chat:
            t.save(cid=a.chat, actor="cli")
        h = t.hello(actor="cli")
        if h.get("step") == "bot":
            _out(a, h, f"Telegram rejects the token: {h.get('error')}"); sys.exit(1)
        if h.get("step") == "chat" and not h.get("ok"):
            chats = h.get("chats") or []
            text = f"bot @{(h.get('bot') or {}).get('username')} works.\n" + (("chats the bot has heard from:\n" + "\n".join(f"  {c['id']:>16}  {c['name']} ({c['type']})" for c in chats) + "\npick one:  ./cli.py telegram setup --chat <id>")
                                                                              if chats else "it has heard from nobody yet: message the bot (or add it to the group and say hi), then run this again")
            _out(a, h, text); sys.exit(2)
        _out(a, h, f"connected · a hello landed in the chat from @{(h.get('bot') or {}).get('username')}")
    elif a.action == "hello":
        h = t.hello(actor="cli"); _out(a, h, "hello sent" if h.get("ok") else f"not sent · {h.get('error')}")
    elif a.action == "test":
        r = t.send_test(a.title, a.body, {"signal": a.signal or "test"}, actor="cli")
        _out(a, r, "posted to the chat (labelled as a test)" if r.get("ok") else f"not posted · {r.get('error')}")
        if not r.get("ok"):
            sys.exit(1)
    elif a.action in ("on", "off"):
        save_plain({"telegram_alerts": a.action}); _out(a, {"telegram_alerts": a.action}, f"telegram mirror {a.action}")


def test_users(a):
    from . import test_sends
    if a.action == "show":
        _out(a, test_sends.meta(), json.dumps(test_sends.meta(), indent=2, ensure_ascii=False))
        return
    raw = open(a.file).read() if a.file else _hidden("test users, one per line, end with Ctrl-D:\n", stdin=True) if not sys.stdin.isatty() else "\n".join(iter(input, ""))
    try:
        m = test_sends.save_users(raw, actor="cli")
    except test_sends.TestSendError as e:
        sys.exit(str(e))
    _out(a, m, f"saved {m['count']} test user(s): {', '.join(m['masked'])}")


def test_send(a):
    _boot()
    from . import test_sends, telegram_out
    from .alerts2 import brief as brief_mod
    title, body, name, signal = a.title or "", a.body or "", "Test send", a.alert or ""
    if a.alert:
        b = brief_mod.build(0, None, "", 10, with_dry_run=False)
        c = next((c for c in b.get("copy") or [] if c["signal"] == a.alert or c["template"] == a.alert), None)
        if not c:
            sys.exit("no alert copy named " + a.alert + "; one of: " + ", ".join(sorted({c["signal"] for c in b.get("copy") or []})))
        title, body, name = c["sample_title"], c["sample_body"], f"MA2 test · {c['signal']}"
    if a.proposal:
        from . import approvals
        pr = approvals.get_proposal(a.proposal) or sys.exit("no such draft")
        v = ((pr.get("payload") or {}).get("variants") or [{}])[0]
        title, body, name = title or v.get("title", ""), body or v.get("body", ""), (pr.get("payload") or {}).get("name") or name
    if not (title and body):
        sys.exit("give --alert <signal>, --proposal <id>, or --title and --body")
    if a.to == "telegram":
        r = telegram_out.send_test(title, body, {"signal": signal}, actor="cli")
        _out(a, r, "posted to the Telegram chat" if r.get("ok") else f"not posted · {r.get('error')}")
    else:
        r = test_sends.send(title, body, name=name, actor="cli", source="cli")
        _out(a, r, f"test sent to {r.get('sent_to')} · {r.get('status')}" if r.get("ok") else f"not sent · {r.get('error')}")
    if not r.get("ok"):
        sys.exit(1)


def launch(a):
    """The alerts launch: print the brief; with --yes (after reading it) queue the drafts for approval. Nothing is sent by this."""
    _boot()
    from .alerts2 import discovery, brief as brief_mod
    cohorts = [x.strip() for x in (a.cohorts or "internal").split(",") if x.strip()]
    if not a.yes:
        b = brief_mod.build(0, None, "", a.control, with_dry_run=True, cohort_ids=cohorts)
        _out(a, b, brief_mod.markdown(b) + "\n\nRead it. Then queue it with:  ./cli.py launch --cohorts " + ",".join(cohorts) + " --yes")
        return
    r = discovery.launch("", 0, None, a.control, "sessions_per_week", created_by="cli", reviewed=True, cohort_ids=cohorts)
    if r.get("error"):
        _out(a, r, "not queued · " + str(r["error"])[:300]); sys.exit(1)
    props = r.get("proposals") or r.get("queued") or []
    _out(a, r, "queued · these now wait for a human's approval:\n" + "\n".join(f"  #{p.get('id') or p.get('proposal_id')}  {p.get('kind', '')}  {str(p.get('title', ''))[:80]}" for p in props if isinstance(p, dict))
         + "\napprove with: ./cli.py decide proposal:<id> approve   (or on Today)")


def decide(a):
    """approve | defer a decision from Today. Approve executes it: this is the human click, so it asks for confirmation unless --yes."""
    _boot()
    from . import v3
    if a.action == "approve" and not a.yes:
        t = v3.today()
        d = next((x for x in t["decisions"] if x["id"] == a.id), None)
        if not d:
            sys.exit(f"{a.id} is not an open decision (./cli.py today)")
        print(d["title"]); print(d["body"][:400]); print("if you approve:"); [print("  → " + p) for p in d["plan"]]
        if d.get("blocked"):
            sys.exit(f"blocked: {d['blocked']}  → ./cli.py asks")
        if input(f"{d['primary']['label']}? type yes: ").strip().lower() != "yes":
            sys.exit("not approved")
    r = v3.resolve(a.id, a.action, actor=a.actor or "cli")
    _out(a, r, r.get("toast") or json.dumps(r))
    if not r.get("ok"):
        sys.exit(1)


def alerts_run(a):
    _boot()
    from .alerts2 import discovery, service
    if a.action == "kill":
        _out(a, service.set_kill(True, actor="cli"), "kill switch ON · nothing goes out")
    elif a.action == "lift":
        _out(a, service.set_kill(False, actor="cli"), "kill switch off")
    elif a.action == "run":
        r = discovery.run("live", actor="cli")
        s = r.get("summary") or {}
        _out(a, r, f"live run: sent {r.get('sent')}  queued {r.get('queued')}  held {r.get('held_quiet_hours')}  suppressed {r.get('suppressed')}  failed {r.get('failed')}  {s.get('suppression_reasons')}"
             + ("\n" + "\n".join(f"  {d['decision']:14} {d['signal']:14} {d['token']:6} → {', '.join(d.get('cohorts') or []) or '-'}  telegram {'ok' if (d.get('telegram') or {}).get('ok') else '-'}  {d['title']}" for d in (s.get("decisions") or [])[:20]) if s.get("decisions") else ""))
        if not r.get("ok"):
            sys.exit("not run · " + str(r.get("error")))


def _booted(fn):
    def run(a):
        _boot()                                   # the executors the server registers at start; without them every draft looks unexecutable
        return fn(a)
    return run


def add_parsers(sp) -> None:
    def J(p):
        p.add_argument("--json", action="store_true", help="machine-readable output"); return p
    for name in ("today", "asks", "doctor", "answer", "secret", "telegram", "test_users", "test_send", "launch", "decide", "alerts_run"):
        globals()[name] = _booted(globals()[name])
    J(sp.add_parser("today", help="today's decisions, what moved, what was handled")).set_defaults(fn=today)
    J(sp.add_parser("asks", help="every input the engine is missing, each with the command that answers it")).set_defaults(fn=asks)
    J(sp.add_parser("doctor", help="one screen: mode, model, missing answers, alerts preflight, telegram")).set_defaults(fn=doctor)
    s = J(sp.add_parser("answer", help="answer one Ask: ./cli.py answer <id> key=value …  (secrets are prompted hidden, never arguments)"))
    s.add_argument("id"); s.add_argument("values", nargs="*"); s.add_argument("--env", help="take the secret from this environment variable"); s.add_argument("--stdin", action="store_true"); s.set_defaults(fn=answer)
    s = J(sp.add_parser("secret", help="save one secret setting at a hidden prompt (telegram_bot_token, moengage_campaign_key, llm_api_key …)"))
    s.add_argument("key"); s.add_argument("--env"); s.add_argument("--stdin", action="store_true"); s.set_defaults(fn=secret)
    s = J(sp.add_parser("telegram", help="status | setup [--chat ID] | hello | test TITLE BODY | on | off"))
    s.add_argument("action", choices=["status", "setup", "hello", "test", "on", "off"]); s.add_argument("title", nargs="?"); s.add_argument("body", nargs="?")
    s.add_argument("--chat"); s.add_argument("--new-token", action="store_true"); s.add_argument("--signal"); s.add_argument("--env"); s.add_argument("--stdin", action="store_true"); s.set_defaults(fn=telegram)
    s = J(sp.add_parser("test-users", help="show | set [--file F]  (emails or customer ids, up to 10; stored encrypted)"))
    s.add_argument("action", choices=["show", "set"]); s.add_argument("--file"); s.set_defaults(fn=test_users)
    s = J(sp.add_parser("test-send", help="send a test: --alert <signal> | --proposal <id> | --title T --body B ; --to moengage|telegram"))
    s.add_argument("--alert"); s.add_argument("--proposal", type=int); s.add_argument("--title"); s.add_argument("--body"); s.add_argument("--to", choices=["moengage", "telegram"], default="moengage"); s.set_defaults(fn=test_send)
    s = J(sp.add_parser("launch", help="market alerts launch: print the brief; --yes queues the drafts for approval"))
    s.add_argument("--cohorts", default="internal"); s.add_argument("--control", type=int, default=10); s.add_argument("--yes", action="store_true"); s.set_defaults(fn=launch)
    s = J(sp.add_parser("decide", help="approve | defer a decision from Today (approve is the human click)"))
    s.add_argument("id"); s.add_argument("action", choices=["approve", "defer"]); s.add_argument("--yes", action="store_true"); s.add_argument("--actor"); s.set_defaults(fn=decide)
    s = J(sp.add_parser("alerts-run", help="run | kill | lift  (one live pass of the alerts engine; the stop switch)"))
    s.add_argument("action", choices=["run", "kill", "lift"]); s.set_defaults(fn=alerts_run)
