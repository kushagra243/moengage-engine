---
name: moengage-ops
description: How Claude operates MoEngage for CoinDCX through this engine's CLI — Claude does the heavy lifting (read state as JSON, judge, design the campaign, write compliant copy, choose the cohort, explain), the CLI does the doing (answer asks, queue launches, test-send, approve), and two things stay human (secrets at a hidden prompt, the approval click). Use whenever asked to "run", "set up", "launch", "test" or "fix" anything in MoEngage from a terminal or a Claude Code session.
---

# Operating MoEngage: Claude thinks, the CLI does, a person approves

## The loop (every session starts here)
```bash
./cli.py doctor --json        # mode, model, missing answers, alerts preflight, telegram — in one read
./cli.py today --json         # the decisions waiting, what moved, what was handled
./cli.py asks --json          # every missing input, each with the exact command that answers it
```
Read the JSON, decide, then act only with the commands below. Never edit `data/agent.db`, never call MoEngage yourself, never paste a secret.

## What Claude does (heavy lifting)
- Reads state (`doctor`, `today`, `asks`, `alerts preflight`, `alerts detect`, `approvals list/show`) and explains it in plain words.
- Designs: which cohort, which signal set, the copy (crypto-compliance-copy + crypto-copywriting rules; never a venue or competitor name, never a leverage lure, never a hypothetical return), the KPI and holdout, the stop rules. The brain's own tools (`./cli.py chat "…"`) may draft; Claude reviews.
- Fills in what a draft is missing: `./cli.py answer proposal:<id> target_segment=… goal.kill_criteria="a > b%"` — the same fields Asks shows. A draft that only lacks an input is kept pending; a draft that breaks a rule is refused and must be redesigned, not forced.
- Prepares the launch: `./cli.py launch --cohorts internal` prints the whole brief (copy word for word, thresholds, caps, audience, experiment design, the exact MoEngage body, what would fire this minute). Claude reads it and summarises the risks before anyone queues it.
- Diagnoses silence: `doctor` and `alerts preflight` name the failing check and its fix; the Business-event 401 is narrowed to workspace/dc, the Campaigns key, or a key without permission.

## What the CLI does (the doing)
```bash
./cli.py answer <ask-id> key=value …      # settings, draft fields; secrets → hidden prompt automatically
./cli.py secret telegram_bot_token        # any *_key / *_token / *_secret, typed hidden (or --env VAR / --stdin)
./cli.py telegram setup [--chat ID]       # bot check → chat discovery → hello
./cli.py test-users set --file ids.txt    # up to 10 emails/customer ids, encrypted
./cli.py test-send --alert large_trades --to telegram|moengage
./cli.py test-send --proposal 41          # a draft's copy to the test users before approving it
./cli.py launch --cohorts internal --yes  # queues the MoEngage draft + the engine's permission; sends nothing
./cli.py decide proposal:41 approve       # THE human click: asks "type yes" unless --yes
./cli.py alerts-run run | kill | lift     # one live pass; the stop switch
./cli.py update                           # pull + reload in place on any machine, keys untouched
```
Every command takes `--json`.

## What stays human, always
1. **Secrets.** The CLI refuses a secret passed as an argument; it prompts hidden, or reads `--env VAR` / `--stdin` that the person set up. Claude never types, echoes or reads a token; if one appears in chat, say it is exposed and ask for it to be revoked.
2. **Approval.** `decide … approve` and `approvals approve` are the person's click. Claude may run them only when the person has said, in this conversation, to approve that specific draft; otherwise Claude stops at "queued, waiting for your approval" and names the id. `--yes` is for the person's own scripts.
3. **Going live.** `answer setting:mock_mode` flips practice mode off. Claude explains the consequence and waits for the word.

## Order of operations for a machine that has nothing yet
1. `./cli.py update && ./cli.py doctor`
2. Person: `./cli.py secret moengage_campaign_key` (and data / segmentation keys, `answer setting:moengage_app_id …`, `answer setting:moengage_created_by …`).
3. `./cli.py telegram setup` → message the bot → `./cli.py telegram setup --chat <id>` (one machine only; a second would post every alert twice).
4. `./cli.py test-users set --file testers.txt`, then `./cli.py test-send --alert large_trades --to telegram` to see copy land.
5. `./cli.py launch` (read) → `./cli.py launch --yes` (queue) → person: `./cli.py decide proposal:<id> approve` ×2 → `./cli.py alerts-run run` → `./cli.py alerts coverage` next day (missed must be 0).
6. `./cli.py service install` so it survives the terminal closing.

## Model for the in-app brain
The engine's own agent runs on the configured provider (`llm_provider`); `claude_cli` uses this machine's Claude Code login for it. Claude Code driving the CLI is independent of that setting.
