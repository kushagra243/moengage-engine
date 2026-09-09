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

## Consoles
`/` serves the **Agentic Brain Terminal** (`frontend/terminal/`, design handoff in the team's zip: six modules — 01 Brain (`brain`), 02 Brain Lab (`lab`; the old Competitor Intel + Market Feed merged, hashes `#intel`/`#market` alias to it), 03 Ideas (`ideas`; Campaign Ideas + Experiments merged into one ICE-ranked board: Ideas (feed + recommendations) → Proposed → Simulated → Live → Read → Archive; `#exp` aliases to it; recommendations are queued through their SOP with `/api/brain/recommendations/queue`), 04 SOP Library (`sops`), 05 Anomalies (`anom`), 06 Cohorts (`atlas`) — strict tokens, no radius/no shadows, Chakra Petch + IBM Plex Mono vendored under `frontend/fonts/` because the CSP is self-only). Data comes from `backend/brain.py` (`/api/brain/*`). The previous console stays at `/classic` (set `console=classic` or `MOE_CONSOLE=classic` to make it the default again). Keyboard: 1–6 modules, ⌘K ask the brain, a approve focused directive, j/k move focus, Esc close.

## Privacy defaults (real workspace data is involved)
Outbound HTTP only through `guarded_session` allowlists; the agent may not read per-user endpoints (`api_catalog.is_pii_endpoint`: customer export, cards, experiences, preferences, GDPR, archival) — they are blocked in `call_documented` and flagged in the catalog; `redact()` masks keys, tokens, cookies, emails, Indian phone numbers, PAN and Aadhaar before anything reaches logs, prompts or the model; OpenRouter requests carry `provider.data_collection=deny` by default (`llm_data_collection`) so prompts never go to providers that store or train on them — free models that require data collection are skipped and the chain falls back; nothing under `data/` is tracked by git; code-change drafting runs in a git worktree that contains no `data/`.

## Campaign listening
`backend/market/campaign_intel.py` → `/api/market/competitor-campaigns`, tool `competitor_campaigns`, terminal Intel "Campaigns detected" + App Store panel. Free channels only (exchange announcement APIs, Mudrex RSS, Google News per venue, App Store lookup/rank feeds); keyword classification; persisted 30 days; MATERIAL items feed actions/hooks with the counter SOP.

## Brain Lab (money flow → behaviour → recommendations)
`backend/market/moneyflow.py`: risk-appetite composite (F&G, breadth, cap change, regime, funding skew, stablecoin minting), BTC/ETH dominance shift, DefiLlama stablecoin supply 1d/7d + chain in/outflows (`stablecoins.llama.fi`, free), CoinGecko sector rotation (`/coins/categories`, free), venue volume share crypto vs US-stock vs index vs commodity perps, attention spikes vs own 7-day average, leverage build/flush, web3 gauge; it writes `asset_daily`/`macro_daily` so week-over-week deltas exist after a week. `behaviour_reads()` = plain-English "what traders are doing" with evidence; `recommendations()` = rules → {who, what, SOP, KPI, urgency, avoid} (stress regime → service mode only). `brain.lab_view()` (`/api/brain/lab`) merges it with flash/news/calendar, rivals (majors vs collapsed long tail; per-venue counter play), markets, per-product intel, benchmarks and HL-vs-CEX. Dashboard order (user: "real gold first"): benchmarks scoreboard (gap to India leader per category) → HL vs CEX → top OI, top assets, per-product intel → trader behaviour (compact rows) → money flow → rivals → world; the tiles strip, compiled-hooks table and counter-actions panel were removed as redundant. Tool `money_flow`. Option contracts are excluded from surge/gap maths (they produced "+60855%" false positives).

## ICE and structural P0s
`backend/ice.py`: Impact × Confidence × Ease (1–10 each, score 1–1000, grade A ≥ 343) + `tagline()` ("For WHO · WHAT · measured by KPI"); `infer()` from expected-impact text/effort/confidence, `from_payload()` for proposals. `backend/structural.py`: 25 must-have lifecycle plays from the CLM playbooks (KYC rescue, deposit-failure recovery, funded→first trade, second trade 72h, own-asset alerts, weekly recap, fee-tier nudge, intent graduation, tokenised cross-sell, slipping vs own baseline, dormant by cause, liquidation recovery, funding nudges, stress mode, holdouts, broadcast cap, frequency caps, web3 safety, SIP nurture, WhatsApp utility, Cards, channel recovery, re-KYC, cohort refresh) detected against the live inventory; missing ones are **P0** recommendations (top of the Ideas board, `structural` filter, feed kind `structural_gap`, autopilot mission `close_structural_gaps`, tool `structural_audit`). `brain.recommendations()` is the shared backlog (structural P0 + money-flow recs, ICE, hides what is already proposed). `propose_campaign`/`run_sop` accept `ice`; the board's Proposed column sorts by ICE. Brain Lab is intelligence only; recommendations live on the Ideas board. Process for all of this: `.claude/skills/agent-rulebook/SKILL.md`.

## QA layer (verify facts, improve information)
`backend/qa.py`. (1) `run()` fact-checks what the boards show — freshness (context, competitor snapshot, daily run, stale proposals), cross-source agreement (CoinGecko vs venue prices and 24h change), sanity bounds (dominance, market cap, F&G, surges, funding, risk score, shares, attention, stablecoin supply), references (structural/money-flow/matrix/hook SOP ids exist, draft segments exist or are proposed, KPI vocabulary), copy claims in pending drafts (venue names, banned words, push length, untraceable percentages), source health, and completeness (thin ideas, undefined codes, unstaged families). Safe improvements are applied and listed (enrich ideas with KPI/segment/tagline, retire stale ideas, file a data request for undefined codes). Persisted in `qa_runs`; `/api/brain/qa` (cached 30 min) and `/api/brain/qa/run`; Brain module "QA · fact checks" panel; `frontend/terminal/boot.js` shows a reconnect notice and reloads when the server restarts mid-session; daily cycle step `qa`; tool `qa_report`. (2) `check_reply()` traces every figure in an agent reply to that conversation's tool outputs; untraceable figures, venue names and banned words get a QA footer (`qa_reply_footer`) and `qa` in the chat result. (3) `verify_claims()` / tool `verify_claims` checks numbers against live data before the agent asserts them. Defects QA caught and fixed: history-gap "surges" (prior volume < $1M or > 500% are no longer surges) and cohort families without a lifecycle stage (`guardrails._stage_for_family` now maps every nomenclature token).

## Market Feed layers and dossiers
`backend/market/feed.py` (flash, biggest_news, top_oi, top_by_category, by_product — all from the cached context) and `backend/market/dossiers.py` (per-rival marketing dossier from intel + benchmarks + campaign listening + app ranks). Routes `/api/market/feed`, `/api/market/competitors/dossiers`, `/api/market/competitor/{venue}`; tools `market_flash`, `competitor_dossier`; terminal Market Feed and Intel modules render them.

## Hyperliquid vs CEX
`backend/market/onchain_cex.py` → `/api/market/onchain-vs-cex`, tool `onchain_vs_cex`, terminal Intel panel. HL global stats + CMC CEX derivatives + DefiLlama OI (free) + Binance/Bybit/OKX per-coin OI and funding; optional Coinglass key (`market_coinglass_api_key`, encrypted).

## Category benchmarks
`backend/market/benchmarks.py` → `/api/market/benchmarks`, agent tool `competitor_benchmarks`, terminal Intel → "Best in industry". Spot / perps / options / commodities-tokenised leaderboards across Binance, OKX, Bybit, Bitget, Coinbase, Kraken, KuCoin, Gate, MEXC, HTX, Deribit, Hyperliquid (reference) and the Indian venues, from CMC listings + direct public tickers; our figure per category (spot = CMC; perps/tokenised = liquidity-venue reference; options unknown), gap multiples to the India and global leaders, and pair-level "match their numbers" targets. Daily rows kept 120 days for trends.

## Intelligence dashboard
The Market tab is the team's realtime knowledge page (`renderMarket` in `frontend/app.js`, data from `/api/market/context`): now strip → Tier-0 callout → Act-now hooks (each linked to an SOP and an "ask the agent" button) → crypto regime/movers/funding → listings + OI → tokenised markets → headlines → sources → competitive intelligence (internal) → web3 trending. Auto-refreshes every 5 minutes while open.

## Current state (2026-09-09)
Brain Lab + Cohort Atlas + ICE + structural audit shipped (PR #27). Prefill done as the agent: 1 SOP run (funded→first trade, 3 step proposals with written copy), 5 segment proposals for missing cohorts (DEP_FAILED, FTT_NOSECOND, KYC_APPROVED_NODEP, LIQUIDATED_14D, ACTIVE_30D), 6 data requests (deposit_failed/liquidation events, fee-tier distance, push flag, product-view events, trader_state upload). Claude CLI OAuth is expired on this Mac (`claude login` needed) so headless `claude -p` prefill/code-change drafting could not run.
Merged through PR #12: autopilot missions → approval-ready proposals; experiment ledger with Wilson readouts; taxonomy from naming conventions; deep per-campaign analysis on the free bulk model tier; Growth hacks tab; exchange-native market universe (Binance ∪ Hyperliquid). PR #13 added the full API catalog, shared skills, operator guidance, engine knobs, code-change proposals and this file. PR #15 adds north star + hard communication limits + peace index + SOP monitor (`backend/guardrails.py`), Flight Plans (`backend/plans.py`), cohort decoding from segment names (`backend/segments.py`, Overview Cohorts card, define-code box), the SOP engine (`backend/sops.py`, SOPs tab, run → proposals, mid-flight checks; terminal SOP Library also shows the SOPs-by-product cards and the user × channel matrix) and four more skills. PR #14 added six marketing skills for crypto derivatives, listing detection + open-interest movers as hooks (`backend/market/derivs.py`), derivatives taxonomy codes and six derivatives tactics in the hacks library.
Known limits: campaign-stats API exposes no control-group figures (readouts are pre-period comparisons); v5 analytics query is experimental; campaign creation via API is Push/Email only.
