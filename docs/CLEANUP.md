# Cleanup: keep the marketer's job, retire the rest

Goal: the engine is intelligence + experiments + analytics for agentic CLM marketing on MoEngage, and nothing else.
This is a living list. A surface is retired when it has not fed a decision in 30 days (checked against the audit log
and `telemetry`), or when a challenge shows it misleads. Retirement = remove the view and the route, keep the data
model if a readout still needs it, note it here with the commit.

## Stays (the job)
- Intelligence: market context, anomalies, rivals and campaign listening, money flow, benchmarks, market-moving news.
- Experiments: proposals → approvals (Today / Slack / CLI) → MoEngage drafts, Market Alerts 2.0, experiment ledger and readouts, launch briefs, test sends.
- Analytics: workspace analysis, campaign league table, QA fact checks, compliance sweep, weekly seven questions, our-learnings.
- Operating layer: skills, personas, CLI, Asks, challenges, telemetry, roles, budget.

## Candidates (decide from usage during testing; do not delete blind)
| surface | why it is a candidate | evidence needed |
|---|---|---|
| Growth hacks tab / `growth` feed | ideas duplicated by the Ideas board and structural P0s | no idea promoted from it in 30 days |
| HAR capture / dashboard-session transport | the public APIs cover reads; cookies are the riskiest credential | no `cookie` transport used in 30 days |
| Brain Lab "comparison" panels | intelligence nobody acts on | no rival decision referenced them |
| Methodology radar auto-refresh (6 h) | credits on reading blogs | move to weekly, on demand |
| Council refresher (30 min) | credits on re-reviewing drafts nobody approves | review once per draft, on approval |
| Two consoles (`/` and `/ops`) | one shell is enough once every module has a native screen | Workbench iframe use drops to zero |
| Mock seeding of 30 days history | a builder needs it; an operator never does | role = operator |

## Done
- 2026-09-24: builder/operator roles (builders hold no credentials or ids); basic model everywhere with a daily ration.
