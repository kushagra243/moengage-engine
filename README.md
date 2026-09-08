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

## Quick start

Setting this up on a fresh Mac with the Claude Code login as the model?
Follow [SETUP-MAC.md](SETUP-MAC.md) (10 minutes, no API keys).

```bash
python3 start.py          # creates .venv (Python ≥ 3.10), installs deps, serves http://127.0.0.1:8080
```

The server binds to loopback only. The UI receives a per-process token that
every API call must carry, so a page in another tab cannot drive the engine.

Then in **Settings**:

1. **LLM** — pick one of the provider options below and click *Test LLM*.
2. **Integration** — see *Connecting to MoEngage* below. Turn off *Demo/mock mode*.
3. Run *Test connection*, then *Verify endpoints*.

### LLM provider options

| Provider | When to use | Setup |
|---|---|---|
| `openrouter` (default) | Any frontier model by id, one key | Paste the key in Settings → LLM, set model (e.g. `anthropic/claude-sonnet-4.5`), *Load models* to browse. |
| `openai_compatible` | OpenAI, Groq, Together, Ollama, LM Studio | Set base URL (`https://api.openai.com/v1`, `http://127.0.0.1:11434/v1` …) and key (empty for local servers). |
| `claude_cli` | No API key: reuse the Claude Code login already on this device | See below. |

**Claude CLI on a new device (no key needed)**

```bash
# 1. install Claude Code if it is not there yet
npm install -g @anthropic-ai/claude-code
# 2. log in once in a terminal (opens the browser)
claude login
# 3. sanity check: should answer without an auth error
claude -p "reply with ok" --output-format json
# 4. point the engine at it
./cli.py set llm_provider claude_cli
./cli.py set llm_model claude-sonnet-5
```

The engine drives `claude -p` headlessly and emulates tool calling through a
strict JSON protocol. If the login lapses, the LLM probe reports
"OAuth session expired" and `claude login` fixes it. Nothing else changes:
the same redaction, tool set and approval gate apply.

### Simulated model (exercise the agent loop with no key at all)

`tools/sim_llm.py` is a local OpenAI-compatible server that is *not* a model:
it is a scripted marketer that drives the real tool protocol (status →
programme audit → anomalies → market hooks → segment + campaign proposals with
a full goal brief) and writes its answer from the actual tool results. Every
reply is labelled `[SIMULATED MODEL]`.

```bash
.venv/bin/python tools/sim_llm.py &            # 127.0.0.1:8791
./cli.py set llm_provider openai_compatible
./cli.py set llm_base_url http://127.0.0.1:8791/v1
./cli.py set llm_model sim/marketer-v1
./cli.py set-key llm --value sim
./cli.py chat "Audit the programme and propose a reactivation campaign"
```

Switch back to a real model by changing the provider/base URL/key in Settings.

### Mock walkthrough (no MoEngage access, no LLM key)

Demo/mock mode is on by default. This exercises every non-LLM path end to end:

```bash
./cli.py status                      # mode, transports, integration counts
./cli.py campaigns                   # 6 simulated campaigns, tagged src=mock
./cli.py snapshot                    # record today's metrics + run detection
./cli.py anomalies                   # report (thin history → hard rules / peer checks)
./cli.py market --hooks              # live regime, movers, news risk flags, campaign hooks
./cli.py approvals list --status pending
./cli.py daily-run                   # snapshot → anomalies → market → (brief if LLM set)
./cli.py audit-log -n 10             # hash-chained audit trail
```

In the UI the same flow is Overview → *Record snapshot now* → Anomalies →
Market → Approvals → *New proposal* (a `create_campaign` payload must include
a `goal` block or it is refused) → Approve → Integration → audit log. Once an
LLM is configured, the Agent tab and the daily brief light up; in mock mode
the agent labels every number as simulated.

The CLI mirrors the UI: `./cli.py status | set-key | set-cookies | learn | verify | campaigns | snapshot | anomalies | market | chat | approvals | daily-run | audit-log`.

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
