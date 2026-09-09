---
name: clm-campaign-playbook
description: The best lifecycle (CLM) campaign ideas for a trading app by transition — what works, what does not, with the mechanism and the measurement behind each; anti-patterns that quietly destroy programmes; realistic benchmark ranges; and how to pick the next campaign from data. Use when asked for campaign ideas, "what should we run", or to sanity-check a proposal against practice.
---

# CLM campaign playbook (what works, what doesn't)

Every idea below names the transition, the mechanism, the KPI and the trap. Pair with `clm-operator` for the brief and `campaign-sops` for the run procedure.

## Acquired → Verified (KYC)
- **Works**: progress framing ("2 of 3 done"), document-specific help at the exact failure point (blurry PAN → retry tips), a support hand-off after the second failure, WhatsApp utility reminders. KPI kyc_completion_72h.
- **Doesn't**: bonuses for finishing KYC (attracts fraud, cannibalises), daily pushes (uninstall driver), fear framing.
## Verified → Funded
- **Works**: objection removal in order (money stuck? → instant withdrawals; safety? → custody facts; how much? → ₹100 is enough), UPI failure recovery within minutes (highest ROI message in Indian fintech), salary-week timing (2–6 of month), one clear first-deposit path. KPI first_deposit_rate_7d.
- **Doesn't**: deposit bonuses as default (train bonus-seekers, ASCI risk), generic "add money" blasts, more than four touches in a week.
## Funded → Activated (first trade)
- **Works**: guided first trade with the user's own watchlist, recurring buy as a low-anxiety first action, "follow before you trade" (alerts) for hesitant users, fee/TDS transparency. KPI first_trade_rate_7d.
- **Doesn't**: naming an asset to buy (compliance and trust), leverage or perps to anyone at this stage, urgency.
## Activated → Habitual
- **Works**: price-move alerts on the user's own assets (highest CTR class), weekly recap from own numbers, streak-free habit loops (watchlist → alert → review), Cards inbox for non-urgent recaps, feature discovery one at a time. KPI second_trade_7d, sessions_per_week, alert_adoption.
- **Doesn't**: daily market commentary pushes (fatigue → notification disable), gamified streaks that punish breaks, more than one market push per day.
## Habitual → Core (depth, tiers, products)
- **Works**: fee-tier distance nudges when within 15%, product graduation by *intent* (viewed perps twice → education), hedging education for concentrated spot holders, VIP service tone (fewer messages, richer), tokenised markets to weekend/US-hours traders. KPI products_per_user, weekly_active_weeks_4w.
- **Doesn't**: leverage upsell, size-up prompts, P&L leaderboards, competitions ranked by returns.
## Slipping → Recovered
- **Works**: detect decline vs the user's own baseline, portfolio review with their numbers, cause-based check-in (market vs friction vs loss), lighter cadence not heavier. KPI trade_frequency_recovery_14d.
- **Doesn't**: "we miss you" at day 7, discounts to lossy users, market pushes to loss-dormant users.
## Dormant → Reactivated
- **Works**: segment by cause; market-dormant get "what changed" facts in trending_up/chop; friction-dormant get "fixed" messages; loss-dormant get service and education or silence; email + push combined; re-entry via a habit tool not a trade. KPI reactivation_rate_14d/30d against a 20% holdout (reactivation is where the market steals credit).
- **Doesn't**: blasts during stress regimes, offers to loss-dormant users, more than one attempt per month.
## Risk moments (derivatives)
- **Works**: liquidation-recovery flow (silence → explainer → spot-first), funding cost nudges to crowded positions, OI crowding notes, macro-print T−24h risk briefs, isolated-margin education. KPI return_to_trade_30d, liquidation_rate by band.
- **Doesn't**: any "get back in", leverage as lure, competitions to recently liquidated users.
## Compliance and service
- **Works**: Re-KYC with escalating channel and constant tone, incident status messages before users ask, tax-season explainers (July). KPI completion/support-contact avoided.
## Programmes as a whole
- **Works**: one primary KPI per campaign, holdouts everywhere, suppression lists synced daily, cadence by stage (best users hear least), regime tagging of every result, monthly cohort refresh with version deltas, an experiment ledger.
- **Anti-patterns**: broadcast share > 30% of sends; "engagement" as a KPI; discount escalation; copying e-commerce cart-abandonment logic onto order tickets; reading peer campaigns as baselines; launching during capitulation; shipping without kill criteria; unbounded frequency across overlapping segments (the same HVT user in five campaigns).

## Benchmark ranges (directional; own history beats all of them)
Push CTR fintech 2–8% (alerts 10–20%), delivery ≥ 90% healthy / < 85% investigate, email open 18–30% with click 2–5%, WhatsApp utility read 70%+, in-app click 5–15%, reactivation of 30–90d dormant 1–4% incremental, unsubscribe per email < 0.3%, notification-disable per push < 0.1%.

## Choosing the next campaign (the order)
1. `clm_program_audit` → the largest uncovered transition. 2. `segment_study` → the cohort family it maps to and its month-over-month state. 3. `campaign_taxonomy` comparisons → which facet responds. 4. Pick the SOP (`list_sops`) or define one. 5. `experiment_plan` → is the target readable at that reach. 6. Run via `run_sop`; record the idea. If step 5 fails, widen the audience or lengthen the window before touching copy.
