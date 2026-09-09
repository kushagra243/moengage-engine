# Running moengage-engine on another Mac (Claude-powered)

Ten minutes, no API keys required. The agent runs on your Claude Code login;
MoEngage access comes from headers you copy out of the dashboard.

## 1. Prerequisites

```bash
xcode-select --install            # git + compilers (skip if already present)
python3 --version                 # need 3.10 or newer; if not: brew install python@3.12
node --version                    # only needed for Claude Code below; if missing: brew install node
```

## 2. Clone and set up (one command)

```bash
git clone https://github.com/kushagra243/moengage-engine.git && cd moengage-engine && ./setup.sh
```

`setup.sh` is idempotent: it finds or installs Python ≥ 3.10, creates `.venv`,
installs dependencies, initialises the encrypted store, installs Claude Code
if missing, checks the Claude login, sets the engine to run the agent
**headlessly through the Claude CLI** (`claude -p`, no API key), runs the
tests, prints the MoEngage steps, and starts the console at
**http://127.0.0.1:8080**. Flags: `--no-start`, `--openrouter`,
`--model=claude-opus-5`.

### Or let Claude Code do the whole setup for you

The repo ships a project command, `/setup`. After the clone, run it
interactively inside Claude Code:

```bash
cd moengage-engine && claude
# then type:  /setup
```

or fully headless, no prompts (Claude runs the script, starts the server,
verifies the model, and tells you exactly what to paste for MoEngage):

```bash
cd moengage-engine && claude -p "/setup" --permission-mode acceptEdits \
  --allowedTools "Bash(./setup.sh*),Bash(./cli.py*),Bash(curl*),Bash(nohup*),Bash(claude*),Bash(npm*),Bash(brew*),Read,Edit"
```

The only two things it cannot do for you are `claude login` (needs your
browser) and copying your MoEngage headers; it will stop and ask for each.

If you prefer to do it by hand, the manual steps follow.

```bash
python3 start.py          # manual start; first run creates .venv and installs deps
```

Leave that terminal open; use a second one for the commands below.

On first start the engine creates its secret key in the macOS Keychain
(item `moengage-engine`). macOS may ask you to allow access once.

## 3. Sign in to Claude on this Mac (the model)

```bash
npm install -g @anthropic-ai/claude-code      # skip if `claude --version` already works
claude login                                  # opens the browser; finish the login there
claude -p "reply with ok" --output-format json # must NOT say "Failed to authenticate"
```

Then point the engine at it:

```bash
./cli.py set llm_provider claude_cli
./cli.py set llm_model claude-sonnet-5        # or claude-opus-5 / claude-haiku-4-5-20251001
```

Check in the console: **Settings → LLM → Test LLM** should show a reply and a
latency. The top strip shows `llm claude-sonnet-5`.

If you would rather use an API key: Settings → LLM → provider `openrouter`,
paste the key, pick a model, *Test LLM*. Nothing else changes.

### No Claude CLI? Use an OpenRouter key instead

Everything works identically with a key and no Claude Code install:

```bash
./setup.sh --openrouter        # or, on an existing install: ./cli.py set llm_provider openrouter
```

then in the console **Settings → LLM**: provider `openrouter`, paste the key
into *API key*, *Load models* to pick one (default `anthropic/claude-sonnet-4.5`),
*Test LLM*. Any OpenAI-compatible endpoint works the same way with provider
`openai_compatible` and its base URL (OpenAI, Groq, Together, or a local
Ollama / LM Studio server with an empty key). The provider can be switched
at any time; tools, approvals and redaction are unchanged.

## 4. Connect MoEngage (paste once)

1. Log in to your MoEngage dashboard in Chrome.
2. DevTools (⌥⌘I) → **Network** → click any dashboard request, e.g.
   `campaigns/all` → **Headers** → *Request Headers* → select all → copy.
3. Console → **Settings → Integration**:
   - *Dashboard region*: the host you log in to (`dashboard-03.moengage.com` for India).
   - *Session cookies*: paste the whole header block. The engine pulls out
     `cookie`, `authorization: Bearer …`, `refreshtoken` and `moe-appkey`.
   - Turn **Demo / mock mode** off. Save.
4. Saving triggers, in the background, discovery of the dashboard's API paths
   from its public bundle and a read-only probe with your credentials. Open
   **Integration** after ~1 minute: roles that answered show `verified`.
5. If you also have API keys (Settings → Account → API keys on MoEngage),
   add Workspace ID plus the Data / Segmentation / Campaigns keys. This is the
   stable documented path and works regardless of dashboard changes.

With a refresh token pasted the engine renews the short-lived dashboard JWT
itself. If everything shows `failed` with "auth rejected", the tokens have
expired: copy fresh headers and save again.

## 4b. One-time keys, and where everything is stored

You enter each credential once; after that the engine renews what it can and
keeps everything on this Mac.

### Enter keys once

In the console → **Settings → Integration** (or from the terminal):

```bash
./cli.py set moengage_region dashboard-03.moengage.com   # your dashboard host (03 = India DC)
./cli.py set moengage_app_id <Workspace ID>              # Settings → Account → API keys on MoEngage
./cli.py set-key data            # Data API key            (prompted, hidden)
./cli.py set-key segmentation    # Segmentation key
./cli.py set-key campaigns       # Campaigns / Reports key
./cli.py set-key inform          # Inform key (optional)
./cli.py set-key llm             # OpenRouter key (skip if using claude_cli)
./cli.py set mock_mode false
./cli.py probe-keys              # confirms which keys work against api-<dc>
```

Dashboard session (optional, for dashboard-only actions such as flow creation):
paste the DevTools *Request Headers* block into *Session cookies* once. The
engine extracts the bearer and refresh tokens and renews the short-lived JWT
itself; you re-paste only when the 60-day refresh token expires or you log out.

Keys never appear again in the UI, logs or the agent: Settings shows only
`set / not set`, and *Clear* removes one.

### Where things live (all under the repo folder unless noted)

| What | Where | Notes |
|---|---|---|
| Settings, encrypted secrets, snapshots, anomalies, proposals, growth feed, chat history | `data/agent.db` (SQLite) | Secrets are Fernet-encrypted rows; the file is useless without the key. |
| Encryption key | macOS **Keychain**, item `moengage-engine` | Created on first run; macOS may ask once. Linux fallback: `data/secret.key` (mode 0600). |
| Audit trail | `data/logs/audit.jsonl` | Hash-chained; Integration tab shows whether the chain is intact. |
| Server / service logs | `data/logs/*.log` | Redacted; safe to share. |
| Market cache | `data/cache/` | Public data only; safe to delete anytime. |
| Learned / discovered / verified dashboard endpoints | `data/endpoints.*.json` | Paths and header *names* only, never values. |
| Python environment | `.venv/` | Rebuilt by `./setup.sh`. |

Nothing under `data/` is committed to git (see `.gitignore`).

### Backup, move, rotate, wipe

- **Backup**: copy `data/agent.db` and export the Keychain item (or `data/secret.key` on Linux) together, to encrypted storage. One without the other restores nothing.
- **Move to another Mac**: copy `data/agent.db`, then run
  `security add-generic-password -U -s moengage-engine -a master-key -w '<key>'` with the exported key, then `./setup.sh`. Or simply re-enter the keys on the new Mac; it takes a minute.
- **Rotate a key**: regenerate on MoEngage, `./cli.py set-key <kind>` again. The audit log records the change (never the value).
- **Wipe**: stop the service, delete `data/`, and delete the Keychain item. The repo folder holds nothing else about you.

### What is never stored or sent

Raw keys in logs, chat history sent to the model, or the UI; MoEngage data to any third party; anything to the model provider except the redacted tool output the agent needs for the question you asked.

## 5. First run

In the console:

1. **Overview → Run daily process** — snapshot, anomalies, market context and
   the agent's written brief.
2. **Agent** — try "Audit the programme and propose a reactivation campaign".
   The agent files proposals; nothing is sent.
3. **Approvals** — read the goal brief and the exact request preview, then
   Approve or Reject. Only approved items reach MoEngage.
4. **Integration → Audit log** — every secret change, probe and execution is
   recorded in a hash-chained log.

Or from the terminal:

```bash
./cli.py status
./cli.py daily-run
./cli.py chat "Where are the outliers and what do we do about them?"
./cli.py approvals list --status pending
```

## 6. Before you have credentials: mock mode

Leave **Demo / mock mode** on. Everything works on a simulated workspace and
every number is labelled `mock`. The CLI walkthrough in the README exercises
each feature without touching MoEngage.

## 7. Keeping it running (persistent, no model needed)

```bash
./cli.py service install     # launchd agent: starts at login, restarts on crash, console on :8080
./cli.py service status
```

The service runs the daily process at **Settings → Scheduler** time and a
model-free refresh every 6 hours (`refresh_interval_hours`): API/dashboard
reads, snapshots, anomalies, market context and the growth feed. No model
credits are used unless a provider is configured.
- Update: `git pull && python3 start.py` (dependencies are re-checked).
- Restart cleanly: `pkill -f start.py && python3 start.py`.

## What never leaves the Mac

Cookies, tokens and keys are encrypted in `data/agent.db` with a Keychain-held
key and never shown in the UI or logs. Outbound calls go only to
`*.moengage.com`, the model provider you configured, and public market feeds.
Nothing under `data/` is committed to git.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `claude` says "OAuth session expired" | `claude login` again. |
| Test LLM fails with `claude cli not found` | `npm install -g @anthropic-ai/claude-code`, restart the terminal. |
| Integration shows `auth rejected (path exists)` | Credentials expired; paste fresh headers. |
| Console says "server restarted, reload the page" | Reload; the per-process token changed. |
| Port 8080 busy | `PORT=8090 python3 start.py` and open that port. |
| Market tiles missing equities | Yahoo rate-limits some networks; indices still come from CBOE and NSE. |
