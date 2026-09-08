# Measuring a campaign honestly

## The metric hierarchy

1. **Incremental conversion versus a holdout.** The only number that establishes
   the campaign caused something. Everything below is diagnostic.
2. **Conversion rate on the primary goal**, within an attribution window that
   matches the real decision latency.
3. **Click-through rate.** Measures the copy and the creative, not the campaign.
4. **Delivery rate.** Measures your reachability and your infrastructure. Diagnose
   here first when a campaign underperforms; it is more often delivery than copy.
5. **Guardrail metrics**: notification disable, unsubscribe, spam complaint,
   uninstall, support tickets. A campaign that wins on conversion and loses on
   these is borrowing from next quarter.

## Reading a bad result

Work down the funnel in order, because the answer is usually near the top:

* **Reach much lower than segment size** means reachability. Push opt-in, email
  subscription, WhatsApp opt-in, or frequency capping suppressed the send.
* **Delivery lower than reach** means device-level failure: OEM push kill, bounced
  email, invalid numbers. Not a copy problem.
* **Impressions fine, clicks low** is a copy and timing problem. Test the title and
  the send window before you touch anything else.
* **Clicks fine, conversions low** means the landing experience broke the promise.
  The message and the screen disagree.
* **Conversions fine, holdout equally good** means you targeted people who were
  going to convert anyway. This is the most expensive and most commonly missed
  failure, and only a control group reveals it.

## Attribution windows

Match the window to the decision, and pick it before you launch:

| Campaign type | Sensible window |
| --- | --- |
| Market-linked push | 6 to 24 hours |
| Reactivation | 72 hours |
| Graduation and education | 7 days |
| Onboarding sequence | 14 days from entry |

Extending the window after seeing the results is how teams talk themselves into
campaigns that did nothing.

## Comparing campaigns fairly

Only compare campaigns that had the same audience definition, the same channel and
a comparable market regime. A reactivation push in a rally and the same push in a
drawdown are different experiments. Tag every campaign with the regime it ran in,
or the retrospective is guesswork.

## Sample size, plainly

To detect a 10% relative lift on a 2% base conversion rate, you need roughly 30,000
users per arm for a result you would bet on. On a 10,000-person segment you can
detect a change of roughly 25% or larger, and nothing subtler. Decide up front
which of those you are actually running:

* An **experiment**, sized to learn something, or
* an **operation**, sized to do something useful, with the winner picked on
  judgement rather than significance.

Both are legitimate. Calling the second one the first is not.

## What to record for every campaign

Objective, audience definition in English, reach, deliverable reach, channel,
angle, market regime at send, control group size, primary goal, attribution
window, result, and one sentence on what you would change. Six months later this
record is the only thing that makes the next campaign better.
