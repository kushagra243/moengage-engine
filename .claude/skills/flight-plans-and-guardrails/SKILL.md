---
name: flight-plans-and-guardrails
description: The Flight Plan (campaign requirement document) template the agent writes for every programme and for the monthly plan, the north-star metric the programme optimises, the global hard communication limits per user (channel × day/week, stage overrides, regime multipliers), the cohort peace index (too much / too little contact), and the SOP miss/breach monitor. Use when planning a month, writing a proposal document, setting or changing limits, or reporting adherence.
---

# Flight Plans, north star and guardrails

## North star (one sentence, editable by the team)
Default: *Incremental active trading weeks per user at the lowest message load — maximise weekly_active_weeks_4w lift vs holdout while every cohort stays inside the communication limits and no user is contacted during a stress regime except for service.*
KPI tree beneath it: transition KPIs (`clm-operator`) → programme coverage (`clm_program_audit`) → message load (`peace_index`) → adherence (`guardrail_monitor`). The agent reads `north_star` before any plan and states which branch a proposal moves.

## Hard communication limits (`comms_limits`; enforced in code)
Defaults per user: push 1/day 4/week · email 1/day 3/week · in-app 2/day 6/week · cards 1/day 7/week · WhatsApp 1/day 2/week · SMS 1/week · **total 8/week** · market-linked push 1/day · quiet hours 22:00–08:00 IST.
Stage overrides: Core (HVT/VIP/whales) push 2/week, email 2/week, total 4 · Liquidated push 1/week, total 2 · Loss-dormant push 0, email 1, total 1.
Regime multipliers: capitulation ×0 (service only) · high_volatility_down ×0.5 · trending_down ×0.75 · unknown ×0.75 · others ×1.
Where they bite: `sop_check` rejects SOPs whose weekly cap or per-channel step density exceeds the global limits; `run_sop` pre-flight adds the plan's touches to what is already planned for the cohort and fails over cap; `write_flight_plan` records the limits check in the document; `peace_index` reports the state of every cohort.
When the team says "max 3 pushes a week" or "HVT get one email a month": `set_comms_limits({"per_user":{"push":{"per_week":3}}})`, `set_comms_limits({"stage_overrides":{"Core":{"email":{"per_week":1}}}})`, then confirm the before/after. Learnings that are not numeric go to `remember_guidance`.

## Peace index (cohort-level, honest about the estimate)
touches/user/week = planned proposals in the next 7 days targeting the family + observed sends ÷ reach over the last 7 days, by channel. Compared with the effective cap for the regime and stage → `too_much` (any channel or total over cap, with the breach listed), `too_little` (an active, non-Core cohort with no contact for 14+ days and no campaign attached), `in_band`. Per-user send logs are not exposed by the MoEngage public API; if the dashboard's user-level activity is learned via HAR, the same index can be computed per user. Market movement enters through the regime multiplier: the same plan can be in band on Monday and over cap after a regime flip.

## SOP monitor (daily, model-free)
- **Miss**: a step whose scheduled date has passed while its proposal is still pending.
- **Breach**: a promotional step (market, competition, winback, activation SOPs) executed during capitulation or high-volatility-down; a run over cap.
Findings post to the growth feed at priority 95 and appear in the Overview guardrails card; the agent reports them plainly and proposes pauses.

## Flight Plan (the requirement document; `write_flight_plan`)
Sections, always in this order:
1. **Objective and north-star link** — which branch of the tree moves and by how much.
2. **Cohort and sizing** — family, version, reach (or "unknown; verify"), exclusions, jurisdictions excluded.
3. **Journey** — table of day / channel / purpose / condition / send time IST.
4. **Copy** — two variants per step written to `crypto-copywriting` and `crypto-compliance-copy`.
5. **KPI and experiment design** — one KPI, target vs baseline, guardrail, holdout, window, sample size / days to read from `experiment_plan`, kill criteria.
6. **Communication limits and peace check** — computed by the engine.
7. **Month timeline** — dates for launch, checks, readout.
8. **Month-on-month** — last month's result (from `experiment_readouts`), what changes, expected gain.
9. **Risks and compliance** — regime, deliverability, legal.
10. **Checks** — pre / mid / post-flight lists.
11. **What's possible now vs what is needed** — split into now / needs data (events, attributes) / needs API or dashboard access. Never promise a segment that cannot be built.
Every plan is stored, rendered to Markdown, linked to an SOP where one exists, and recorded in the growth feed. Plans are drafts until the team marks them proposed; proposals are then queued via `run_sop` (or `propose_*` for one-offs) and approved step by step.

## Monthly rhythm
Day 1–5: `monthly_flight_plans` mission writes up to three plans from `clm_program_audit`, `segment_study`, `experiment_readouts` and last month's plans. Weekly: peace index and monitor in the review. Month end: readouts close the loop and next month's plans state what changed and why.
