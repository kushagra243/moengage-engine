# moengage-engine — project memory for Claude Code

This file is committed so every machine and every session starts with the same context. Keep it short and current; details live in the skills under `.claude/skills/`.

## What this is
A local, leak-proof MoEngage CLM (lifecycle marketing) engine for a crypto/equities trading app: FastAPI backend (`backend/`), vanilla-JS console (`frontend/`), CLI (`cli.py`), SQLite in `data/agent.db`, secrets encrypted with a Keychain-held key. Everything runs on 127.0.0.1; nothing leaves the Mac except allowlisted calls to MoEngage, the chosen model provider, and public market data.

## Start here on a new machine
```bash
./setup.sh --no-start      # idempotent; skips installs when nothing changed
.venv/bin/python start.py  # console at http://127.0.0.1:8080
```
Or type `/setup` in Claude Code. Persistent service: `./cli.py service install`. Never re-create the venv by hand; never commit anything under `data/`.

## Skills (single source of truth, shared with the in-app agent)
- `moengage` — product capabilities, gotchas, MCP servers (official MoEngage skill vendored).
- `moengage-api` — complete documented API catalog (131 operations, 32 specs) + how the engine calls it.
- `moengage-engine` — codebase map, invariants, recipes for tools/views/CLI/executors, code-change flow.
- `clm-operator` — goal-brief doctrine, measurement, market-linked send policy, copy rules.
Load the relevant one before working; the agent does the same via its `skill` tool.

## Invariants (do not weaken)
Loopback bind · per-process `X-Local-Token` · Host check · outbound only through `guarded_session` allowlists · CSP self-only (no CDN) · `redact()` on logs/prompts/tool output · hash-chained audit log · every MoEngage write and every code change goes through the Approvals queue with a human click · route handlers are sync `def`.

## Conventions
- Tests: `.venv/bin/python -m pytest -q tests` must pass before finishing. Fake OpenAI server pattern in `tests/test_agent_loop.py`; `responses` mocks for HTTP.
- Restart the server with `pkill -f start.py` then `.venv/bin/python start.py` (cmdline is start.py, not uvicorn).
- Commit style: imperative subject, body explains why; PRs to `main` on `kushagra243/moengage-engine`, merged when green.
- Keep numbers honest: label mock data, state coverage (stats present for N of M campaigns), never invent data for missing endpoints.

## How the team steers the agent
- **Teach**: Agent tab → "Teach the agent" (or say "from now on…" in chat → `remember_guidance`). Stored in `agent_guidance`, injected into the system prompt, toggle/delete in the UI.
- **Knobs**: agent may call `set_engine_setting` on the allowlist in `backend/guidance.py`.
- **Code**: "Change request" in the Agent tab or `propose_code_change` → `backend/devagent.py` drafts on branch `agent/change-<id>` (Claude Code headless, fallback: model diff), runs tests, shows the diff in Approvals → approve merges and restarts the server.

## Current state (2026-09-09)
Merged through PR #12: autopilot missions → approval-ready proposals; experiment ledger with Wilson readouts; taxonomy from naming conventions; deep per-campaign analysis on the free bulk model tier; Growth hacks tab; exchange-native market universe (Binance ∪ Hyperliquid). This branch adds: full API catalog + skills, operator guidance, engine knobs, code-change proposals, idempotent setup, this file.
Known limits: campaign-stats API exposes no control-group figures (readouts are pre-period comparisons); v5 analytics query is experimental; campaign creation via API is Push/Email only.
