# Customer lifecycle for a crypto trading platform

## The stages, defined by behaviour rather than by time

| Stage | Definition | The one question | Primary intervention |
| --- | --- | --- | --- |
| **Acquired** | Signed up, no KYC | Do they believe this is safe? | Trust and proof, not features |
| **Verified** | KYC approved, no deposit | What are they afraid of losing? | Small-first-step framing, deposit friction removal |
| **Funded** | Deposited, no trade | Do they know what to do first? | One concrete first action, not a menu |
| **Activated** | First trade done | Will they come back unprompted? | Habit formation inside 7 days |
| **Habitual** | Traded in 3 of the last 4 weeks | What would make them consolidate here? | Depth: more assets, more products, better fees |
| **Core** | Consistent volume, multiple products | What would make them leave? | Service, reliability, status |
| **Slipping** | Volume or frequency down 50%+ versus their own baseline | What changed for them? | Diagnose before you message |
| **Dormant** | No trade in 30+ days, still has balance | Why did they stop? | Reason-specific winback, never generic |
| **Churned** | No session in 90+ days, or balance withdrawn | Is there anything honest to say? | Usually nothing. Suppress rather than nag |

The stage is computed per user from behaviour, and it moves in both directions.
The mistake most CRM programmes make is treating the stage as a funnel position
that only advances.

## Baselines are personal, not global

"Slipping" cannot be defined as "traded fewer than 4 times this month", because a
user who trades twice a month and always has is not slipping. Compare a user to
their own trailing baseline:

```
slipping = trade_count_30d < 0.5 * median(trade_count_30d over prior 3 months)
```

This is the single highest-value refinement in a CLM programme. It converts a
noisy volume metric into a per-user signal, and it stops you messaging your
steady low-frequency users as if they were leaving.

## Stage transitions worth instrumenting

The transitions matter more than the states:

* **Verified to Funded** is where most consumer fintech loses the majority of its
  signups. The blocker is almost never awareness; it is trust plus payment
  friction. Measure time-to-first-deposit and the drop by payment method.
* **Funded to Activated** is a knowledge gap. The user has committed money and
  does not know what the first sensible action is. This is the highest-ROI
  in-app moment in the whole product.
* **Activated to Habitual** is the retention cliff. If a user does not trade a
  second time within 7 days, the probability of them becoming habitual falls off
  sharply. Everything you do in that window is worth more than anything you do
  later.
* **Core to Slipping** is usually caused by an event, not by drift: a loss, a
  failed withdrawal, a support experience, or a competitor promotion. Look for
  the event before you write the message.
* **Dormant to Activated** is possible but the reason matters. A user who went
  quiet because the market went quiet comes back on their own when it moves. A
  user who went quiet after a liquidation will not, and messaging them about the
  market makes it worse.

## Segmenting dormancy by cause, not duration

"Dormant 30 days" is not a segment, it is a bucket. The useful split:

* **Market-dormant**: stopped when volatility dropped, no negative event. They
  come back with the market. Message with information, not persuasion.
* **Loss-dormant**: last sessions include a liquidation, a large realised loss, or
  a drawdown. Never send market or upsell content. A genuine check-in, education
  about risk tools, or nothing at all.
* **Friction-dormant**: last sessions include a failed deposit, a failed
  withdrawal, a KYC re-check, or a support ticket. The message is an apology and
  a fix, and it should come from support tooling, not marketing.
* **Competitor-dormant**: withdrew a large share of balance and went quiet. Only
  an honest product reason brings them back. Discounts read as desperation.
* **Life-dormant**: no signal at all, just stopped. The cheapest to message and the
  least likely to respond. Low frequency, low cost, high patience.

Building these five is the difference between a reactivation programme that works
and one that generates unsubscribes.

## Frequency by stage

Message volume should follow value and consent, not urgency:

| Stage | Sensible weekly cadence |
| --- | --- |
| Acquired, Verified | 2 to 3, mostly onboarding and trust |
| Funded, Activated | 3 to 4, mostly education and habit |
| Habitual, Core | 1 to 2, mostly information they asked for |
| Slipping | 1, diagnostic |
| Dormant | 1 every 2 weeks, decreasing over time |
| Churned | 0 to 1 per quarter, or suppress |

Your best users should hear from you least. This is counterintuitive to most
growth teams and it is correct: a Core user opening the app daily does not need a
push telling them to open the app.

## What a CLM programme should own

1. The lifecycle stage definition and its computation.
2. The set of standing campaigns mapped to stage transitions.
3. The suppression rules across all of them.
4. The measurement: incremental effect per stage, not aggregate sends.
5. The retrospective: which transitions improved, which regressed, and why.

If a campaign cannot be mapped to a stage transition it is trying to cause, it is
a broadcast, and broadcasts should be rare and deliberate.
