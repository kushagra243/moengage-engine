---
name: campaign-sops
description: The SOP framework for running lifecycle campaigns — what a standard operating procedure must specify (objective, cohort family, exclusions, sequence, duration, frequency, holdout, KPI, kill rules, compliance, checks), the library of SOPs seeded from the best-performing patterns, how to define a new SOP, how to run one on a cohort through the approval queue, and the pre-, mid- and post-flight checks. Use before defining or running any multi-step campaign.
---

# Campaign SOPs

Brand rule for every step's copy: we are CoinDCX; no liquidity venue or competitor is ever named; "SIP" is our recurring-buy product.

An SOP turns a campaign type into a repeatable procedure that anyone on the team, or the agent, can run on a cohort with the same guardrails every time. SOPs live in the engine (`backend/sops.py`, editable via the SOPs tab or `define_sop`); the framework check is code, so an SOP that violates it cannot be saved or run.

## What every SOP specifies
| field | rule |
|---|---|
| `campaign_type` | onboarding · activation · retention · winback · risk · compliance · market · newsletter · competition · cohort_upload · education |
| `transition` + `objective` | one lifecycle transition; the objective states the mechanism |
| `audience` | `segment_family` (resolves to the latest monthly version), `exclusions` (standard set: unsubscribed/DND, KYC pending, open ticket, liquidated 14d, loss-dormant), `min_reach`, `jurisdictions_excluded` (UK/US for derivatives and incentives) |
| `steps[]` | `day` offset, `channel`, `purpose`, `copy_brief`, `send_time_ist`, optional `condition`, `ttl_hours` for market steps; internal analysis steps are prefixed "(internal)" |
| `duration_days`, `frequency` | `cadence` (event_triggered · weekly · monthly · quarterly · once_per_cohort_version) and `max_messages_per_user_per_week`; the framework rejects sequences that exceed the cap |
| `holdout_pct` | ≥ 5; 20 for anything new |
| `primary_kpi`, `target`, `guardrail_metric`, `measurement_window_days` | exactly one KPI; window ≥ sequence length |
| `kill_criteria[]` | at least one numeric stop rule (`unsubscribe_rate > 0.5%`, `delivery_rate < 85%`) plus regime rules where relevant |
| `compliance` | `disclaimer_channels` (email/whatsapp/in-app carry the ASCI VDA text; push relies on the landing screen), `banned_angles` |
| `checks` | preflight / midflight / postflight lists (below) |

## Framework rules enforced in code (`sop_check`)
Required fields · valid type and channels · push inside 08:00–22:00 IST, promotional SMS 10:00–21:00 · messages ÷ weeks ≤ weekly cap · derivatives/market SOPs exclude liquidated-14d and loss-dormant · disclaimer channels declared when email/WhatsApp/in-app steps exist (compliance/transactional SOPs exempt) · one KPI · kill criteria present · warnings for holdout < 20 on new SOPs, > 90-day sequences, UK not excluded from derivatives/incentives.

## The library (seeded; edit rather than fork)
`sop_verified_to_funded` (7-day deposit sequence) · `sop_funded_to_first_trade` (72h) · `sop_hvt_retention` (monthly, few rich messages, 20% holdout) · `sop_dormant_by_cause` (market / friction / loss tracks) · `sop_liquidation_recovery` (T+0 silence → T+14 spot path) · `sop_market_move_alert` (fact + tool, TTL 4h) · `sop_rekyc` (deadline escalation by channel) · `sop_monthly_cohort_upload` (baseline, re-point, retire) · `sop_weekly_digest` (Sunday 19:00 own numbers) · `sop_trading_competition` (volume-ranked, never P&L, not UK/US).

## Running an SOP
1. `sop_detail(id)` and `segment_study()` — confirm the family has a current version and the meaning is defined.
2. `run_sop(id, segment_name?, start_date?, dry_run=True)` — read pre-flight: framework, segment_exists, min_reach (reach is often a proxy or unknown; verify in the dashboard), exclusions_present, regime_allows, compliance; and the per-step brief checks.
3. Write two variants per step following `crypto-copywriting` and `crypto-compliance-copy`; pass them as `variants_by_step={"0":[...],"1":[...]}`. Without variants the engine queues placeholder copy marked "needs copy".
4. `run_sop(..., dry_run=False)` → one `create_campaign` proposal per step, each with the full goal brief, schedule (start + day offset, IST time, condition), exclusions and frequency cap; a `sop_runs` row records the run.
5. A human approves each step in Approvals. Nothing is sent before that.

## Checks
- **Pre-flight** (before queuing): the list above; any failure blocks.
- **Mid-flight** (daily, model-free): numeric kill rules evaluated against experiment readouts; a hit posts a priority fix to the growth feed and the operator pauses the step via `propose_pause_campaign`; holdout and suppression integrity; regime watch for market/winback/competition SOPs (stress regime → pause promotional tracks).
- **Post-flight** (window complete): readout with Wilson intervals and explicit claim limits (no control-group figures from the stats API), version delta for cohort SOPs, lesson pushed to the growth feed, SOP updated if the lesson changes the procedure (bump version).

## Defining a new SOP
Start from the closest library SOP, change only what the evidence supports, keep the standard exclusions, and set holdout 20. Submit with `define_sop(spec)`; fix framework problems it returns. Record in the growth feed why it exists and what readout would retire it.
