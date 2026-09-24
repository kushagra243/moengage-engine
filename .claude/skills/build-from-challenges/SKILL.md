---
name: build-from-challenges
description: The build machine's loop for this engine — pull the challenges the operating agent logged (what it could not do in MoEngage and why), turn each into a change with a test first, ship it to main, mark it resolved with the commit, and let the operating machine pick it up with ./cli.py update. Use when asked to "fix what the agent hit", "work the challenges", "what is missing in the build", or at the start of a session on the build machine.
---

# Build from challenges

Two machines. The **operating machine** runs the brain on the enterprise Anthropic key (Haiku for the heavy lifting) against real MoEngage data and logs every task it could not finish as a *challenge*. The **build machine** (this Claude Code session) never touches workspace data: it reads the challenges, changes the engine, and ships.

## The loop
```bash
./cli.py challenges pull            # GitHub issues labelled `challenge` → local ledger (or: challenges import --file x.json)
./cli.py challenges list            # open first, most frequent first
./cli.py challenges prompt --id 12  # the full build prompt for one; `prompt` alone prints all open ones
```
For each challenge, in order of count:
1. `./cli.py challenges building 12` so the operator sees it is taken.
2. Read the prompt. Decide whether it is a missing capability, a wrong rule, a bad error message, or a data problem the engine cannot fix (then `wontfix` with a note that says who can).
3. Reproduce with a test first (`tests/`: fake OpenAI server in `test_agent_loop.py`, monkeypatched sessions, `register_all()` for executors). The test must fail before the fix.
4. Fix the cause. Keep every invariant in CLAUDE.md: loopback only, `guarded_session` allowlists, `redact()` on anything that leaves, every MoEngage write through an approval, no venue or competitor names in copy, secrets never in arguments or chat.
5. `.venv/bin/python -m pytest -q tests` green. Commit with a subject that says what and a body that says why; push to `main`.
6. `./cli.py challenges resolve 12 --commit <sha> --note "<what changed and what the agent should do now>"` — this closes the issue with the note.
7. Tell the operator: `./cli.py update` on the operating machine brings it in without a restart.

## Reading a challenge well
- `tool_error` × many with one signature → the tool, not the model. Fix the tool; add the missing input validation message.
- `brief_rejected` → the rule was right; the agent needs a better path (a skill section, an example, a tool that drafts compliant copy) — do not weaken `campaign_brief_check`.
- `blocked_draft` / `execution_failed` → read the proposal id in the context; the fix is usually in an executor or in the MoEngage body shape (`v5_campaign_payload`).
- `unknown_tool` → the model wanted a capability. Decide if it deserves a tool; if so add it with a budget, a persona allowlist entry and a test.
- `out_of_steps` → too many rounds for the task: a compound tool, a bigger budget for that purpose, or a skill that gives the plan.
- `agent` (`log_challenge`) → read *what would help* literally; it is the operator's brain asking for a feature.
- `job_failed` → a source or an allowlist; check `guarded_session` hosts and the venue fallbacks.

## What never goes into a fix
Real customer identifiers, campaign copy copied from the workspace, a weakened redaction, a write that skips the approval queue, a secret in a file or a test.
