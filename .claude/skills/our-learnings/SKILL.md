---
name: our-learnings
description: What CoinDCX's own MoEngage programme has taught us so far — experiment readouts (wins, losses, flat), lessons recorded by the brain, deep-dive verdicts per campaign, channel health in our data, QA trend and India-fit status. Cite this BEFORE any generic benchmark; regenerated automatically by the daily cycle (backend/learnings.py).
---

# Our learnings (auto-compiled 2026-09-09 23:59 UTC)

Numbers below come from the mock workspace and are illustrative. Source workspace: mock. 0 experiments read out, 1 running.

## What worked here
- no positive readout yet — every proposal carries a holdout so this fills in

## What did not
- no negative readout yet

## Flat / inconclusive
- none

## Lessons the brain recorded
- none yet

## Deep-dive verdicts (per campaign)
- none yet — the daily cycle analyses the campaigns needing attention first

## Channels in our data
- **push** — in range: click rate 7.98% within 2.0–8.0%
- **email** — below: 1.98% under the 2.0–5.0% floor
- **in-app** — strong: 31.48% above the 5.0–15.0% range
- best click: New User Onboarding Guide click 31.47% (in-app), Price Drop Alert on Wishlist Items click 16.85% (push), Cart Abandonment 1-Hour Reminder click 12.89% (push)
- worst click: Reactivation: 30-Day Inactive Winback click 1.42% (email), Weekend Flash Sale 20% Off click 6.27% (push), VIP Loyalty Club Double Points click 8.94% (email)
- uncovered transitions: Verified → Funded, Funded → Activated, Slipping → recovered

## Running now
- CLM_Dormant_MarketReturn_Recap (reactivation_rate_14d, day 0 of 14)

## Quality trend (QA score)
- 2026-09-09 score 86 (2 fail / 6 warn)
- 2026-09-09 score 88 (2 fail / 4 warn)

## India fit of the SOP library
- 35 india-ready · 23 need edits · 0 rework · avg 89.0
- most common flags: hinglish_variant ×15, tds_transparency ×11, derivatives_banned_angles ×11, onboarding_ban_bonus ×7, push_promo ×6

## How to use this skill
1. Before proposing, check *What worked here* and *What did not* for the same transition or channel; cite the readout in the rationale.
2. Prefer repeating a win with one variable changed over a new idea with no evidence; prefer killing a loser over tweaking it.
3. If a claim needs a number that is not here, run `experiment_readouts` or `workspace_analysis` — do not quote a benchmark as if it were ours.
