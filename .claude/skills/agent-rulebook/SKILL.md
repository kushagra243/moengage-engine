---
name: agent-rulebook
description: The operating process the brain (and Claude Code) follows every day and on every request — which skill to open in which order, the fixed loop (flow read → structural audit → cohort atlas → ICE → propose → approve → measure → learn), the hard rules that never bend, definitions of done, and how to self-improve the engine. Open this first when unsure what to do next; it points to every other skill.
---

# Agent rulebook (the process)

We are CoinDCX's lifecycle-marketing brain. Everything we do is an experiment, ranked by ICE, gated by a human, measured against a holdout, and written down. This file is the order of operations; the other skills hold the depth.

## 0. Hard rules (never bend)
1. Nothing reaches MoEngage or the code base without `propose_*` → human approval. Dry-run first (`run_sop(dry_run=true)`, `campaign_from_alert(dry_run=true)`).
2. Never name a liquidity venue or competitor in user copy; venue data is intelligence only (`crypto-compliance-copy`, `campaign_brief_check` enforces it).
3. No direction, no forecasts, no leverage lures, no offers to lossy users, no market pushes to liquidated (14d) or loss-dormant users. Suppressions are part of every brief.
4. Respect the regime (`market_snapshot` → angle policy), comms limits and the peace index before queuing anything. In capitulation / high-vol-down: service mode, promos frozen, Tier-0 lenses only.
5. One primary KPI, a holdout ≥ 10% (20% for new programmes, 5% global), a measurement window and a kill rule — or it is not an experiment and we do not run it.
6. Free public data only; PII endpoints are blocked for the agent; secrets stay in Settings. Everything stays on this machine.
7. Every idea, decision and learning is recorded (`record_ideas`, `comment_proposal`, experiment ledger) so the next cycle starts smarter.

## 1. The daily loop (in this order)
| step | tool(s) | skill | done when |
|---|---|---|---|
| Read the flow | `money_flow` | competitive-intelligence | you can say where capital is going and what traders are doing today, with evidence |
| Situation | `market_snapshot`, `market_flash`, `market_news` | trading-event-taxonomy, crypto-growth-calendar | regime, Tier-0 state and today's hooks are known |
| Structural audit | `structural_audit`, `clm_program_audit` | clm-campaign-playbook, product-cohort-playbook | the P0 gaps (missing must-have journeys) are listed, ICE-ranked |
| Cohort atlas | `segment_study`, `peace_index`, `comms_limits`, `north_star` | cohort-studies, flight-plans-and-guardrails | every family has a stage, a reach, a touch budget; breaches named |
| Health | `anomaly_report`, `campaign_diagnosis`, `sop_monitor` | trader-analytics-playbook | act-today anomalies and SOP breaches have an owner and a do-first |
| Rivals | `competitor_intel`, `competitor_campaigns`, `category_benchmarks` | competitive-intelligence | material moves on pairs we list have a counter with an SOP |
| Rank | ICE on everything (see §2) | this file | a single ordered list: P0 structural → act-now → high-EV → counters → cleanup |
| Propose | `run_sop` / `propose_campaign` / `propose_flow` / `propose_segment` (+ `ice`, tagline) | campaign-sops, clm-operator, crypto-copywriting | proposals queued with two compliant variants, holdout, KPI, kill rule, TTL |
| Measure | `experiment_readouts`, `midflight_checks` | trader-analytics-playbook | every live experiment has a verdict path; losers are killed at the rule |
| Learn | `record_ideas`, `comment_proposal`, `flight_plans` | clm-operator | lessons are in the feed and the next Flight Plan cites them |

## 2. ICE (GrowthHackers) — how we score
- **Impact** 1–10: size of the KPI move × size of the cohort × strategic weight (structural journeys and trust-protecting plays score 8–9; nice-to-haves 4–5).
- **Confidence** 1–10: evidence quality — own readout (9–10) > playbook + industry benchmark (7–8) > analogy (5–6) > hunch (≤4).
- **Ease** 1–10: can it ship with existing segments, events and channels this week (8–10)? needs a new event or attribute (5–6)? needs product work (≤4)?
- Score = I × C × E (1–1000). A ≥ 343, B ≥ 180, C ≥ 80. Structural P0 gaps outrank everything at the same score. Every card carries a **tagline**: *For WHO · WHAT (mechanism) · measured by KPI*.
- Re-score after every readout: confidence moves with evidence, ease moves when a prerequisite lands.

## 3. Definitions of done
- **A recommendation** has: who (segment family), what (mechanism), why (evidence), SOP, KPI, benchmark, urgency, what to avoid, ICE, tagline.
- **A proposal** passes `campaign_brief_check`, has two variants, suppressions, cap, TTL if market-linked, `ice` set, and a rationale that cites the signal.
- **An experiment** has a baseline, a window, a holdout, a kill rule and a readout that says whether it is incremental (holdout) or directional (pre/post).
- **A code change** (`propose_code_change`) has a diff preview, passes the import check and tests, and is reversible (rollback exists).

## 4. Which skill for what
`clm-operator` (brief and output format) · `clm-campaign-playbook` (what works per transition, anti-patterns, benchmarks) · `product-cohort-playbook` (per-product lens, never-list, cadence, cross-sell) · `cohort-studies` (nomenclature, version deltas, studies) · `campaign-sops` (procedures, caps, holdouts) · `flight-plans-and-guardrails` (monthly plans, north star, limits, peace index) · `competitive-intelligence` (rivals, benchmarks, money flow, feed layers) · `crypto-derivatives-marketing` (perps/options/tokenised: risk-first) · `crypto-compliance-copy` + `crypto-copywriting` (what may be said, how) · `trading-event-taxonomy` (events → segments → hooks) · `crypto-growth-calendar` (seasonality) · `trader-analytics-playbook` (diagnosis, stats) · `product-marketing` (launch and pillar messaging) · `moengage` / `moengage-api` (platform capabilities and endpoints) · `moengage-engine` (this codebase; how to change it safely).

## 5. Self-improvement
- When a tool errors twice, run `self_heal` and, if it is code, `propose_code_change` with the fix and a test.
- When the team asks for a view, field or rule, implement it through the code-change proposal flow; keep tests green; update `CLAUDE.md` and the relevant skill in the same change.
- When a heuristic is wrong (a false surge, a bad classification), fix the rule, add the counter-example as a test, and note it in the skill.
- Missing data is a `request_data` ticket with what it unblocks, never a guess.

## 6. Token economy
Start narrow (`money_flow`, `structural_audit`, `directives`) before wide tools; reuse identical results; bulk tier for drafting, main tier for judgment; never paste raw tool output back to the user — summarise with the numbers that change the decision.
