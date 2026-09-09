---
name: moengage-engine
description: How this repository works and how to change it safely — layout, run/test commands, security invariants, the approval and executor pattern, adding agent tools, views (frontend/app.js), CLI commands, scheduler jobs, and the code-change proposal flow. Use before modifying the engine or when the agent proposes a code change.
---

# moengage-engine (codebase skill)

Local, leak-proof MoEngage CLM console: FastAPI (`backend/`), vanilla-JS console (`frontend/`), CLI (`cli.py`), SQLite (`data/agent.db`), tests (`tests/`, pytest). Read `CLAUDE.md` at the repo root for the short version and current state.

## Run / test / restart
```bash
./setup.sh --no-start        # idempotent: venv, deps (skipped when unchanged), DB, provider
.venv/bin/python start.py    # http://127.0.0.1:8080 (loopback only)
.venv/bin/python -m pytest -q tests
pkill -f start.py            # the server's cmdline is start.py, not uvicorn
./cli.py status | chat "…" | approvals list | daily-run | service install
```

## Invariants (never weaken)
- Bind 127.0.0.1 only; every `/api` route needs `X-Local-Token` (injected into index.html); Host header checked; identity headers trusted only from loopback proxies (`MOE_ALLOWED_HOSTS`).
- Outbound HTTP only via `guarded_session(scope)` host allowlists (moengage / llm / market). No CDN in the frontend (CSP `default-src 'self'`).
- Secrets: Fernet, key in macOS Keychain; `redact()` on logs, prompts and tool output; audit log is hash-chained.
- Writes to MoEngage and to the code base go through `backend/approvals.py` (propose → preview → human approve → executor). Tool output is wrapped as untrusted `<tool_data>`.
- Route handlers are sync `def` (threadpool); never block in `async def`.

## Where things live
| concern | file |
|---|---|
| routes, startup init | `backend/main.py` |
| MoEngage client (mode: public API / session / mock), normalisation | `backend/moengage/client.py`, `public_api.py`, `session.py`, `mock.py` |
| endpoint registry + HAR learning | `backend/moengage/registry.py`, `endpoints.default.json`, `discover.py`, `capture.py` |
| approvals, executors | `backend/approvals.py`, `backend/moengage/executors.py`, `backend/devagent.py` (code_change) |
| anomaly snapshots/detection/diagnosis, metric vocabulary | `backend/anomaly/*.py`, `backend/metrics.py` |
| taxonomy, deep analysis, growth feed, hacks, autopilot, experiments | `backend/taxonomy.py`, `analysis.py`, `growth.py`, `hacks.py`, `autopilot.py`, `experiments.py` |
| market data (Binance ∪ Hyperliquid universe), listings + OI signals, hooks | `backend/market/exchanges.py`, `derivs.py`, `hooks.py`, `context.py` |
| LLM provider, agent loop, tools | `backend/llm/provider.py`, `agent.py`, `tools.py` |
| skills, guidance, API catalog | `backend/skills.py`, `guidance.py`, `api_catalog.py`, `backend/knowledge/` |
| console | `frontend/index.html` (tabs), `app.js` (`loaders.<tab>`), `style.css` (tokens + HUD theme) |
| scheduler (daily run, intraday refresh, autopilot) | `backend/scheduler.py` |

## Recipes
- **New agent tool**: function in `backend/llm/tools.py` returning a dict → add `_fn(...)` schema to `TOOL_SCHEMAS` → register in `TOOLS` with `_safe(...)`. Mention it in `SYSTEM_PROMPT` only if it changes doctrine.
- **New view / column**: markup in `frontend/index.html` inside the tab `<section>`; data + render in `frontend/app.js` under `loaders.<tab>`; helpers `table()`, `esc()`, `fmt()`, `pct()`, `empty()`, `help(metric)`. Add a route in `main.py` if new data is needed. Keep it theme-token based (`var(--…)`).
- **New CLI command**: `cmd_x(a)` in `cli.py` + `sp.add_parser("x")…set_defaults(fn=cmd_x)`.
- **New setting the agent may change**: add to `ENGINE_SETTINGS` in `backend/guidance.py` (type + bounds). Secrets never.
- **New MoEngage write**: executor (validate/preview/execute) + `propose_*` tool; add kind to `approvals.KINDS`.
- **Scheduled job**: extend `scheduler.py` run sequence; keep model-free steps before model steps.
- **Tests**: mirror `tests/test_agent_loop.py` (fake OpenAI server) and `tests/test_security.py`; run the suite before finishing.

## Code-change proposals (agent self-modification)
`propose_code_change` → `backend/devagent.py` drafts on branch `agent/change-<id>` in `data/worktrees/change-<id>` using Claude Code headless (`claude -p … --permission-mode acceptEdits --allowedTools …`) or a model-produced unified diff; runs tests; stores the diff on the proposal. Approve → merge into the checked-out branch → `start.py` re-exec. Reject → branch removed. The checkout must be clean to merge.
