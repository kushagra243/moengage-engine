# moengage-engine — project memory for Claude Code

This file is committed so every machine and every session starts with the same context. Keep it short and current; details live in the skills under `.claude/skills/`.

## Who we are
CoinDCX: spot + SIP (recurring buy), crypto perps, US stock / index / commodity perps (tokenised, 24/7), options, earn, and a Web3 wallet/DEX (Solana, Base, BNB Chain, Ethereum, Robinhood Chain; trending tokens from GeckoTerminal/DexScreener as unverified data). Binance (spot listings) and Hyperliquid (perps, builder dexes) are **data sources only** — never named in user copy, nor any competitor. `campaign_brief_check` blocks venue/competitor names.

## What this is
A local, leak-proof MoEngage CLM (lifecycle marketing) engine for CoinDCX: FastAPI backend (`backend/`), vanilla-JS console (`frontend/`), CLI (`cli.py`), SQLite in `data/agent.db`, secrets encrypted with a Keychain-held key. Everything runs on 127.0.0.1; nothing leaves the Mac except allowlisted calls to MoEngage, the chosen model provider, and public market data.

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
- `crypto-derivatives-marketing` — spot / perps / tokenised-perps lifecycle, trader states, liquidation recovery, funding/OI/listing triggers.
- `crypto-compliance-copy` — India ASCI VDA, UK FCA, EU MiCA, US geo-fencing; disclaimers, banned words, pre-send checklist.
- `trading-event-taxonomy` — the tracking plan (events, attributes, derived trader_state) segments depend on.
- `crypto-copywriting` — frameworks and a compliant copy bank per trigger and channel, Hinglish examples, tests to run first.
- `trader-analytics-playbook` — weekly seven questions with v5 Analytics Query shapes and reading rules.
- `crypto-growth-calendar` — funding windows, expiries, macro prints in IST, US hours for tokenised perps, India moments.
- `clm-campaign-playbook` — best campaign ideas per transition, what works / doesn't, anti-patterns, benchmark ranges, how to pick the next one.
- `product-marketing` — positioning, messaging hierarchy, value props per trader state, launch/adoption playbook, competitive framing, fee comms.
- `cohort-studies` — decoding segment nomenclature (HVT_Sep26), families/versions, monthly routine, standard studies.
- `campaign-sops` — the SOP framework, library, run procedure and pre/mid/post-flight checks.
- `flight-plans-and-guardrails` — the Flight Plan (campaign requirement doc) template, north star, hard communication limits, peace index, SOP monitor.
- `product-cohort-playbook` — product affinity from names, treatment matrix per product, Tier-0 announcements with lenses, web3 lane rules, data requests.
- `competitive-intelligence` — tracked venues, share/surge/gap/edge signals, action owners, real-time counter playbook, honesty and no-naming rules.
Load the relevant one before working; the agent does the same via its `skill` tool.

## Invariants (do not weaken)
Loopback bind · per-process `X-Local-Token` · Host check · outbound only through `guarded_session` allowlists · CSP self-only (no CDN) · `redact()` on logs/prompts/tool output · hash-chained audit log · every MoEngage write and every code change goes through the Approvals queue with a human click · route handlers are sync `def`.

## Conventions
- Tests: `.venv/bin/python -m pytest -q tests` must pass before finishing. Fake OpenAI server pattern in `tests/test_agent_loop.py`; `responses` mocks for HTTP.
- Restart the server with `pkill -f start.py` then `.venv/bin/python start.py` (cmdline is start.py, not uvicorn).
- Commit style: imperative subject, body explains why; PRs to `main` on `kushagra243/moengage-engine`, merged when green.
- Keep numbers honest: label mock data, state coverage (stats present for N of M campaigns), never invent data for missing endpoints.

## Model routing (OpenRouter, free + paid)
Purpose → model chains in `backend/llm/provider.py` (`routes()`), overridable with the `llm_routes` setting: chat/code/copy/review on the main model, autopilot/analysis/brief/test on the free bulk chain, classification on small free models; the first model that answers wins and the main model is the last fallback. Skills, tools and prompts are model-agnostic (plain OpenAI-compatible chat + tool calling). The agent's `model_routes` tool shows routes and 7-day spend per tier.

## Token economy (the platform runs on paid credits)
Heavy work goes to the free bulk tier (`llm_model_bulk=auto-free`): autopilot, deep analysis, the daily brief (`llm_brief_tier=bulk`). Every model call lands in the `llm_usage` ledger (`/api/llm/usage`, agent tool `token_usage`, "tokens today" in the Agent tab). Tool output is compacted (raw/debug keys dropped, floats rounded) and capped per tool (`TOOL_BUDGETS` in `backend/llm/agent.py`, default `llm_tool_output_chars`=7000); identical tool calls within one conversation are answered once; chat history is `llm_history_messages` turns (default 8) at 2 500 chars each; rounds are `llm_max_rounds` (8) / `llm_max_rounds_autopilot` (6); skills load at most 9 000 chars, one at a time. Claude models get Anthropic prompt caching through OpenRouter (`cache_control` on the system prompt, which also covers the tool schemas) and OpenRouter returns real cost per call. When adding a tool, give it a budget and return only what the model needs.

## How the team steers the agent
- **Teach**: Agent tab → "Teach the agent" (or say "from now on…" in chat → `remember_guidance`). Stored in `agent_guidance`, injected into the system prompt, toggle/delete in the UI.
- **Knobs**: agent may call `set_engine_setting` on the allowlist in `backend/guidance.py`.
- **Self-repair**: tool and job failures are recorded (`tool_errors`); `self_diagnose` groups them into fix requests; the `self_heal` autopilot mission files them as code changes; `start.py` reverts the last agent merge automatically if the backend no longer imports (`./cli.py selfheal rollback` does it by hand).
- **Code**: "Change request" in the Agent tab or `propose_code_change` → `backend/devagent.py` drafts on branch `agent/change-<id>` (Claude Code headless, fallback: model diff), runs tests, shows the diff in Approvals → approve merges and restarts the server.

## Current state (2026-09-09)
Merged through PR #12: autopilot missions → approval-ready proposals; experiment ledger with Wilson readouts; taxonomy from naming conventions; deep per-campaign analysis on the free bulk model tier; Growth hacks tab; exchange-native market universe (Binance ∪ Hyperliquid). PR #13 added the full API catalog, shared skills, operator guidance, engine knobs, code-change proposals and this file. PR #15 adds north star + hard communication limits + peace index + SOP monitor (`backend/guardrails.py`), Flight Plans (`backend/plans.py`), cohort decoding from segment names (`backend/segments.py`, Overview Cohorts card, define-code box), the SOP engine (`backend/sops.py`, SOPs tab, run → proposals, mid-flight checks) and four more skills. PR #14 added six marketing skills for crypto derivatives, listing detection + open-interest movers as hooks (`backend/market/derivs.py`), derivatives taxonomy codes and six derivatives tactics in the hacks library.
Known limits: campaign-stats API exposes no control-group figures (readouts are pre-period comparisons); v5 analytics query is experimental; campaign creation via API is Push/Email only.
