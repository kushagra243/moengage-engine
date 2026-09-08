# Goal discipline: every campaign is an experiment against one lifecycle transition

## The rule

A campaign that cannot name the transition it is trying to cause, the single
metric that proves it, the guardrail it must not break, and the moment it
should be killed, is a broadcast. Broadcasts are rare and deliberate; they are
not the default.

Before proposing any campaign, fill the brief completely:

| Field | What it must contain | Example |
| --- | --- | --- |
| transition | one lifecycle move (see stages) | Funded → Activated (first trade) |
| hypothesis | "If we <do X> to <who>, <metric> moves by <Y> because <mechanism>" | If we show one concrete first action to funded-no-trade users within 24h of deposit, 7-day first-trade rate rises 3 pts because the blocker is knowing what to do, not intent |
| primary_kpi | one rate, with its denominator and window | first_trade_rate_7d (users who trade within 7 days / users entered) |
| target | absolute or relative lift, plus current baseline | baseline 18% → target 21% |
| guardrail_metric | the thing that must not get worse | uninstall_rate_7d, unsubscribe_rate, support_tickets_7d |
| control_group_pct | holdout that never receives the message | 10% (min 5%; 20% for new programmes) |
| measurement_window_days | when the verdict is read | 7 |
| kill_criteria | what stops it early | guardrail breach at any check; primary KPI below baseline after 50% of window with n ≥ required |
| audience | inclusion criteria + explicit exclusions + expected reach | funded ≤ 72h, 0 trades, push opt-in; exclude KYC-pending, loss-dormant, support-open |
| frequency | cap and minimum gap | 1 in this programme per 48h; counts toward global cap |
| ttl_hours | for anything market-referencing | 4 |

## KPI trees by transition (choose ONE primary per campaign)

* Acquired → Verified: kyc_completion_rate_72h. Guardrail: kyc_rejection_rate.
* Verified → Funded: first_deposit_rate_7d, time_to_first_deposit. Guardrail: failed_deposit_rate.
* Funded → Activated: first_trade_rate_7d. Guardrail: support_tickets, uninstall.
* Activated → Habitual: second_trade_within_7d, sessions_per_week. Guardrail: notification_disable_rate.
* Habitual → Core: products_per_user, weekly_active_weeks_4w, fee_tier_upgrade. Guardrail: message volume per user (best users hear least).
* Slipping → recovered: trade_frequency back above 0.5× own baseline within 14d. Guardrail: unsubscribe.
* Dormant → Activated: reactivation_rate_14d split by dormancy cause. Guardrail: uninstall_rate (this is where tone-deaf sends cost years).
* Churned: usually suppress; if messaged, reactivation_rate_30d with a hard 1/quarter cap.

Revenue and GMV are outcome metrics for the programme, not proof for a single
campaign; attribute them only with a holdout.

## Sample size before you launch

Use the experiment_plan tool. For a rate metric, the arms needed for 80% power
at α = 0.05 grow fast as the baseline falls. If reach at current volume cannot
deliver the required n inside the window, either widen the audience, lengthen
the window, raise the minimum detectable effect, or do not run it as a test.

## Reading results honestly

* Compare against the holdout, not against last week.
* Read rates with their confidence intervals (Wilson); do not call a winner on
  a 0.3 pt difference from 900 users.
* Tag every result with the market regime it ran in; a +20% uplift in a rally
  week may be underperformance.
* Record a lesson: what moved, what did not, and the next hypothesis.

## Suppression is part of every brief

Standing suppressions across the programme:
1. Loss-dormant (recent liquidation or large realised loss): no market or upsell content.
2. Friction-dormant (failed deposit/withdrawal, KYC re-check, open ticket): only service messages, from support tooling.
3. Regulatory or security headline in the last 24h touching the platform or a named asset: hold acquisition/upsell/FOMO.
4. Capitulation regime: service and safety only.
5. Users over the global frequency cap or inside DND.
