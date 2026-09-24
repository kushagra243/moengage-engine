---
name: clm-marketer-job
description: The job the engine exists to do perfectly — the MoEngage lifecycle marketer for CoinDCX — as a daily/weekly operating rhythm with the intelligence, experiments and analytics it is made of, which skill answers which question, where credits are spent (knowing) and where they are not (doing), and what is being retired from the product so only this remains. Use to decide what to work on, what to build next, what to cut, and how to judge whether the engine did the marketer's job today.
---

# The MoEngage marketer's job, done by an agent

Three things and only three: **intelligence** (what is happening in the market, with our users and at rivals), **experiments** (the campaigns and alerts we run to move a number, with a holdout and a readout), **analytics** (whether it worked, what to keep, what to stop). Everything in the engine either serves one of these or is being retired.

## The daily rhythm (operator machine, Haiku doing the reading)
1. **Read** — `today`: the decisions; `doctor`: what is missing; market picture, anomalies, rival moves (deterministic jobs, no credits).
2. **Decide** — for each decision: is the number worth it, is the cohort right, is the copy compliant, is the holdout there? The brain judges (credits spent here, on knowing); the human clicks in Slack or on Today.
3. **Run** — alerts fire on detection (deterministic; a human gate per alert when `ma2_alert_approval=ask`); campaigns go out as MoEngage drafts once approved; test sends first.
4. **Learn** — readouts after the window, the anomaly board, `our-learnings` regenerated; what could not be done is logged as a challenge for the builder.

## The weekly rhythm
- Seven analytics questions (`trader-analytics-playbook`) on Haiku, once.
- Which experiments read out; keep, extend or stop (`clm-operator`, `clm-campaign-playbook`).
- The asset shortlist (`sop_asset_selection`), the India-fit review, the cohort refresh (`cohort-studies`).
- One look at spend: credits per purpose, background share, anything wasted on busywork → move it to a deterministic path.

## Which skill answers which question
| question | skill |
|---|---|
| what should we run next | clm-campaign-playbook, product-cohort-playbook |
| is this copy allowed | crypto-compliance-copy, crypto-copywriting |
| how do we measure it | clm-operator, trader-analytics-playbook |
| what does MoEngage let us do | moengage (official skill), moengage-api |
| who is this cohort | cohort-studies, trading-event-taxonomy |
| what did we learn already | our-learnings (before any benchmark) |
| what are rivals doing | competitive-intelligence |
| how do alerts behave | market-alerts-2 |
| how does the operator run it | moengage-ops; how the builder fixes it: build-from-challenges |

## Where credits go and where they never go
Spent on: judging a decision, writing and reviewing copy, designing an experiment, answering the operator's question, the weekly analytics read. Never on: detection, rules, lint, coverage, caps, timing, delivery, dashboards, refresh jobs — all deterministic. Basic model everywhere; a better one only for a named purpose (`llm_premium_purposes`). Daily budget and background share in `llm/budget.py`.

## Being retired so only the job remains (see docs/CLEANUP.md)
Anything that is neither intelligence, nor an experiment, nor analytics: decorative dashboards, duplicate consoles, features nobody approved a campaign from. Retirement happens through challenges and readouts, not by taste: a surface that no decision has needed in 30 days is a candidate.

## Did the engine do the marketer's job today?
Every waiting decision was judged with a number, a cohort and a holdout; nothing went to a user without a human's click; every alert that fired was fresh and within its cap; every failure became a challenge; credits went to knowing. That is the definition of done.
