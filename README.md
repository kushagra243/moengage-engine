# moengage-engine (local)

A local, single-user growth/CRM agent for MoEngage. It reads your workspace,
finds statistical outliers in campaign performance, brings in live market
context (crypto, equities, commodities, macro, news), and proposes segments,
campaigns and flows through a live LLM — but it can only **propose**: every
write to MoEngage waits for a human to approve it in the local UI.

Nothing leaves your machine except the calls you configure: MoEngage
(`*.moengage.com`), your LLM provider (default OpenRouter), and public market
feeds. Cookies and API keys are encrypted at rest with a key held in the macOS
Keychain, are never logged, and never enter an LLM prompt.

## Setup, step by step (clone `main`)

Every command runs in a terminal on the Mac that will run the engine.

**1. Prerequisites** (skip what you already have)
```bash
xcode-select --install
brew install python@3.12 node
```

**2. Clone and set up**
```bash
git clone https://github.com/kushagra243/moengage-engine.git && cd moengage-engine
./setup.sh --no-start
```
Creates `.venv`, installs dependencies, creates the encryption key in your Keychain (allow once), initialises `data/agent.db`, runs the tests.

**3. Choose the model**

No API key, using your Claude Code login:
```bash
claude login
./cli.py set llm_provider claude_cli && ./cli.py set llm_model claude-sonnet-5
```
Or an OpenRouter key, entered once (hidden prompt, stored encrypted):
```bash
./cli.py set llm_provider openrouter && ./cli.py set llm_base_url https://openrouter.ai/api/v1 && ./cli.py set llm_model anthropic/claude-sonnet-4.5
./cli.py set-key llm
```

**4. Enter MoEngage keys once** (MoEngage → Settings → Account → API keys; region = your dashboard host, `dashboard-03` for India)
```bash
./cli.py set moengage_region dashboard-03.moengage.com
./cli.py set moengage_app_id YOUR_WORKSPACE_ID
./cli.py set-key data
./cli.py set-key segmentation
./cli.py set-key campaigns
./cli.py set-key inform          # optional
./cli.py set mock_mode false && ./cli.py probe-keys
```

**5. Optional: dashboard session** (only for dashboard-only actions such as creating flows). In Chrome: log in to MoEngage → DevTools → Network → click any dashboard request → copy *Request Headers* → save to a file.
```bash
./cli.py set-cookies --file ~/Downloads/headers.txt
./cli.py verify
```

**6. Start and check**
```bash
python3 start.py          # console at http://127.0.0.1:8080
```
In a second terminal:
```bash
./cli.py status && ./cli.py campaigns
```

**7. First end-to-end run**
```bash
./cli.py daily-run
./cli.py chat "Audit the programme, find the outliers and propose one campaign for the biggest lifecycle gap. Record every idea."
./cli.py approvals list --status pending
./cli.py approvals show 1
./cli.py approvals approve 1 --note "looks right"     # nothing reaches MoEngage before this
```

**8. Keep it running without a model or credits**
```bash
./cli.py service install && ./cli.py service status
```

**9. Housekeeping**
```bash
./cli.py set-key campaigns                                # rotate a key
git pull && ./setup.sh --no-start                         # update
./cli.py service uninstall && rm -rf data && security delete-generic-password -s moengage-engine -a master-key   # wipe
```

Prefer to have Claude do steps 2–7? Run `claude` inside the folder and type `/setup`.
Full detail, storage map and team hosting: [SETUP-MAC.md](SETUP-MAC.md), [HOSTING.md](HOSTING.md).

## Teach the agent, change the engine, know the whole API

- **Teach** — Agent tab → *Teach the agent*: standing instructions (brand voice, exclusions, cadence) that apply to every conversation; or just say "from now on…" in chat and the agent saves it. Toggle or delete any entry.
- **Knobs** — the agent can change allowlisted settings on the spot (autopilot, schedule, refresh cadence, market universe, taxonomy codes, model temperature). Secrets and security settings are never in the list.
- **Code** — *Change request* in the Agent tab (or ask the agent): the change is implemented on an isolated git branch by Claude Code headless (fallback: the configured model writes a diff), tests run, and the diff waits in Approvals. Approve = merge + automatic restart. Reject = branch discarded.
- **API catalog** — every documented MoEngage API (131 operations, 32 OpenAPI specs) is vendored under `backend/knowledge/moengage-api/` and searchable: `GET /api/api-catalog?q=flow status`, or the agent's `moengage_api_reference` / `moengage_api_read` (read-safe calls only). Refresh with `.venv/bin/python tools/build_api_catalog.py`.
- **Skills** — `.claude/skills/{moengage,moengage-api,moengage-engine,clm-operator}` are shared by Claude Code and the in-app agent. `CLAUDE.md` is the committed project memory, so a fresh clone on another Mac starts with full context and `./setup.sh` re-runs are instant.

## Connecting to MoEngage

Two transports are implemented; the client prefers the documented one and
falls back to the cookie session for anything dashboard-only.

| | Public API (documented) | Cookie session (dashboard) |
|---|---|---|
| Auth | Workspace ID + feature API keys (Settings → Account → API keys) | Browser session cookies pasted from DevTools |
| Stability | Stable, rate-limited, supported by MoEngage | Undocumented; idle timeout 24h, hard limit 60 days |
| Reads | campaign search + stats, segments list, flows search, business events, analytics dashboards | anything you capture in a HAR |
| Writes | create filter segment, cohort sync, create campaign **draft** (v5), pause/resume campaign, flow status | segment save, campaign save, **flow creation** (MoEngage has no public API for this) |

Keys: **Data** (Data API / connection test), **Segmentation** (segments,
cohort sync), **Campaigns / Reports** (campaign search, stats, create draft,
status, flows, business events), **Inform** (transactional alerts). Data
centre is derived from the dashboard host (`dashboard-03` → `api-03`) or set
explicitly.

### Dashboard session: paste once, the engine does the rest

The dashboard SPA authenticates its API calls with a short-lived Bearer JWT
plus a `RefreshToken` header (and cookies), and renews the JWT via
`/session/refresh`. So the simplest working setup is:

1. Log in to MoEngage. DevTools → Network → click any dashboard request
   (e.g. `campaigns/all`) → **Request Headers** → select all → copy.
2. Settings → Integration → *Session cookies* → paste the whole block → Save.
   The engine extracts `cookie`, `authorization: Bearer …`, `refreshtoken`
   and `moe-appkey` from it (a plain Cookie header or a cookie-editor export
   also works). Everything is stored encrypted.
3. On save the engine automatically **discovers** the dashboard's API paths
   from its public JavaScript bundle (258 paths in ~4 s, no auth needed),
   classifies them into roles, and **probes the read roles** with your
   credentials. The Integration tab shows what answered. With a refresh
   token present, the engine renews the JWT itself when it expires.

If a role still shows *unknown* or *failed* after that, a HAR capture teaches
it exactly:

1. Log in to MoEngage. DevTools → Network → tick **Preserve log**.
2. Click through: campaigns list → one campaign → its analytics; segments →
   create a segment (estimate reach, save); flows → open one; optionally save
   a campaign as draft.
3. Right-click the request list → **Save all as HAR with content**.
4. Integration tab → **Import HAR** (or `./cli.py learn file.har`).

The learner keeps URLs, methods, header *names*, query keys and JSON shape
only. Cookie/authorization values are dropped; opaque header values are
blanked and must be re-entered as settings (then encrypted). The HAR is never
stored. Afterwards **Verify endpoints** probes read endpoints with your session
(GET, or an empty-body POST for list endpoints that reject GET) and records
which respond. Write endpoints are never probed; they are exercised only when
you approve a proposal, and the proposal shows the exact request first.
*Discover from dashboard bundle* can be re-run at any time; it only reads
string literals from public JavaScript and never executes it.

## What runs every day

`Run daily process` (or the scheduler at the configured time):

1. Snapshot every campaign's metrics into SQLite (per source: mock and live
   baselines never mix).
2. Detect anomalies against each campaign's own history: modified z-score on
   median/MAD (|M| ≥ 3.5 critical, ≥ 2.5 warning) confirmed by Tukey fences;
   a Wilson-interval measurability gate and minimum denominators for rate
   metrics; day-of-week baselines once ≥ 21 days exist; hard rules
   (delivery < 90%, zero CTR at volume) when history is thin; peer comparison
   at low confidence before 7 days.
3. Refresh market context: crypto regime (EMA structure, realised-vol
   percentile, drawdown, breadth, funding), movers, Fear & Greed, INR marks,
   indices/commodities/macro quotes, high-impact calendar, headline news with
   risk flags, and the **campaign hooks** those imply — each hook names the
   segments, channel, angle, timing, TTL and guardrails, filtered by the angle
   policy for today's regime.
4. Ask the agent for a written brief (executive summary, alerts, prioritised
   actions, market note, suppressions).

Market sources are keyless with fallbacks (CoinGecko → CoinPaprika → Binance;
CBOE and NSE for indices; FRED for yields/oil; gold and silver derived from
BTC priced in XAU/XAG). Yahoo Finance and FRED rate-limit or block some
networks; when a source is unavailable the quote is simply absent and listed
under *data gaps* rather than invented. An optional CoinGecko demo key
(`market_coingecko_key`) raises the crypto rate limit.

## Markets & growth hacks feed

The Overview's right-hand column is a persistent, de-duplicated feed of things
to try in MoEngage, re-derived from data on every run (`backend/growth.py`):

- **from data, no model needed** — uncovered lifecycle transitions become
  "stand up a standing campaign" items; bad anomalies become fixes and good
  spikes become scale-ups; rule-audit verdicts become Flow conversions and
  lookalike expansions; today's market hooks become market plays; trending
  assets and high-impact macro events become educational cards and briefs;
  and a curated library of MoEngage capabilities (Smart Triggers on price
  events, Content APIs, BTS, control groups, frequency caps, Business Events,
  RFM, Inform, Cards, WhatsApp fallback, suppression rules) surfaces when a
  data condition makes it relevant.
- **from the agent** — when a model is configured, the agent is given a data
  digest plus the titles already in the feed and asked for genuinely new
  ideas as JSON; they are stored with the same fields.

Each item carries *why* (the data), *how* (MoEngage steps), segment, channel,
KPI, effort and expected impact. Buttons: *Draft with agent* (turns it into
proposals with a goal brief), *Save*, *Dismiss*. Refresh re-derives on demand.

Hosting for a team (Tailscale / Cloudflare Access in front of one host, with
per-person identity on every approval): see [HOSTING.md](HOSTING.md).

## Running persistently (no model required)

```bash
./cli.py service install        # launchd: starts at login, restarts on crash
./cli.py service status
./cli.py service uninstall
```

The service runs the scheduler: a full daily process at `schedule_time`
and a model-free intraday refresh every `refresh_interval_hours` (default 6)
that re-reads MoEngage via the API keys or dashboard session, snapshots
metrics, detects anomalies, refreshes market context and regenerates the
rules-based feed. Nothing calls a model unless one is configured, so it
spends no credits.

## The agent

A tool-calling loop over an OpenAI-compatible chat API (OpenRouter by default;
`claude_cli` uses your local Claude Code login). Tools cover status, campaigns,
segments, flows, analytics, rule audit, anomaly report and history, market
snapshot/news/hooks, the local MoEngage playbooks in `backend/knowledge/`, and
`propose_*` tools that enqueue approvals. Tool output is redacted and
truncated before it reaches the model. The system prompt forbids forecasts,
implied returns and buy/sell instructions in copy, requires mock data to be
labelled as simulated, and refuses market angles blocked in the current regime.

### Goal discipline

The agent operates as the CLM owner, not a copywriter. `clm_program_audit`
maps every campaign to a lifecycle transition and shows the gaps;
`experiment_plan` checks a target is measurable at the audience's reach;
`campaign_brief_check` validates the brief. A campaign proposal is refused,
by the tool and again by the approval executor, unless it carries a
transition, hypothesis, one primary KPI, target, guardrail metric, control
group of at least 5%, measurement window, kill criteria and suppressions.
See `backend/knowledge/clm-goals-and-measurement.md`.

## Security model

- **Secrets**: Fernet-encrypted in SQLite; key in macOS Keychain
  (`moengage-engine`), file fallback `data/secret.key` (0600). `/api/settings`
  returns only `<key>_set` flags.
- **Network**: every outbound call goes through a scoped session with a host
  allowlist. The MoEngage scope carries cookies; the LLM and market scopes can
  never reach `*.moengage.com`. HTTPS only, env proxies ignored, redirects
  re-checked.
- **Local API**: loopback bind, per-process `X-Local-Token`, Host-header
  check, strict CSP on the UI (no CDN, no inline scripts), no OpenAPI docs
  exposed.
- **Writes**: only via the approval queue; each proposal carries a dry-run
  preview of the exact request. Session-level write gate blocks any non-GET
  that is not an approved execution.
- **Prompt injection**: tool output reaches the model wrapped as untrusted
  data; the agent is instructed to treat instruction-like text inside it as
  data, and its only side effect is a proposal a human must approve.
- **Audit**: hash-chained `data/logs/audit.jsonl` for secret changes, HAR
  imports, verifications, proposals and executions; redaction applied to
  every log line.
- Nothing under `data/` is committed (see `.gitignore`).

## Layout

```
backend/
  security/     secrets, redaction, network policy, audit, local token
  moengage/     registry (+endpoints.default.json), session, public_api, capture (HAR), client, executors, mock
  anomaly/      snapshots + detectors
  market/       sources (verified keyless feeds + fallbacks), regime, news, hooks, context
  llm/          provider (OpenAI-compatible / claude_cli), tools, agent loop
  knowledge/    MoEngage / CLM playbooks the agent can search
  approvals.py  proposal queue and executors
  scheduler.py  daily process
  main.py       FastAPI app
frontend/       vanilla JS/CSS console (no external resources)
tests/          security, anomaly, approvals, HAR learner, fake-LLM agent loop
```

## Tests

```bash
.venv/bin/python -m pytest -q tests
```

Tests run against a throwaway database and key; they never touch the
Keychain or `data/`.
