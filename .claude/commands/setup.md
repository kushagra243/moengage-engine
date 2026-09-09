---
description: Set up moengage-engine on this machine after clone (venv, deps, Claude login, provider, server) and walk the user through MoEngage access
---

You are setting up this repository on a freshly cloned machine. Do the steps below yourself with the Bash tool; ask the user only for the two things that need their browser (Claude login and MoEngage headers).

0. Read `CLAUDE.md` (project memory) and skim `.claude/skills/moengage-engine/SKILL.md`. If `.venv` already exists this is a re-run: setup skips installs automatically; do not delete or recreate the venv, and do not reinstall Claude Code or Python.
1. Run `./setup.sh --no-start` from the repo root and read its output. Fix anything it warns about:
   - Python ≥ 3.10 missing → install with Homebrew (`brew install python@3.12`) and re-run.
   - Claude Code missing → `npm install -g @anthropic-ai/claude-code` (install Node with `brew install node` first if needed).
   - Claude login missing or expired → tell the user to run `claude login` in a terminal (it opens the browser). Do not attempt it yourself. Wait for them, then confirm with `claude -p "reply with ok" --output-format json` (must contain `"is_error":false`).
   - If the user prefers an API key instead, run `./setup.sh --no-start --openrouter` and tell them to paste the key in Settings → LLM.
2. Start the server in the background: `PORT=8080 nohup .venv/bin/python start.py > data/logs/server.log 2>&1 &` then confirm `curl -s http://127.0.0.1:8080/api/health` returns `{"ok":true,...}`. If 8080 is taken, use another PORT and tell the user.
3. Verify the model from the CLI: `./cli.py status` should show `llm.configured: true`. Then run `./cli.py chat "reply with the single word ready" --no-persist`. If it errors, go back to step 1.
4. MoEngage access. Tell the user, in these words: log in to the MoEngage dashboard in Chrome → DevTools → Network → click any dashboard request (for example `campaigns/all`) → Headers → Request Headers → select all → copy. Then in the console at http://127.0.0.1:8080 open Settings → Integration, choose the dashboard region they log in to, paste the block into *Session cookies*, switch Demo/mock mode off, Save. Never ask them to paste credentials into the chat with you; they go into the Settings form only.
5. After they confirm saving, wait ~60 s, then run `./cli.py status` and read the Integration tab data via `curl -s -H "X-Local-Token: $(curl -s http://127.0.0.1:8080/ | grep -o 'name="local-token" content="[^"]*"' | sed 's/.*content="//;s/"//')" http://127.0.0.1:8080/api/integration/status` and report which read roles verified. If roles show `auth rejected (path exists)`, the tokens expired: ask them to copy fresh headers.
6. Run the first end-to-end pass: `./cli.py daily-run` and `./cli.py chat "Audit the programme, find the outliers and propose one campaign for the biggest lifecycle gap"`. Then tell the user to review the queued items in the Approvals tab; remind them nothing reaches MoEngage until they approve.
7. Finish with a short status: model provider in use, MoEngage roles verified, number of pending proposals, and the URL of the console.

Rules: never print or log secret values; never commit anything under `data/`; if the user has no MoEngage access yet, leave Demo/mock mode on and show them the mock walkthrough from the README instead.
