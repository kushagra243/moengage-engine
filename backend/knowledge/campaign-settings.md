# MoEngage campaign settings: what to choose and why

## Delivery type

| Type | Use when | Watch out for |
| --- | --- | --- |
| **One Time** | A dated moment: a listing, a fee change, a market event, a launch. | Reach is frozen at send time. A segment that grows after you schedule will not pick up new members. |
| **Periodic** | A recurring nudge on a rolling audience: weekly dormant sweep, monthly statement. | Users re-qualify and get it again. Pair with an exclusion on "received this campaign in the last N days" or you will spam your best-qualifying users repeatedly. |
| **Event Triggered** | React to a behaviour: deposit completed, first trade, position liquidated, price alert fired. | The trigger event must be one your app actually sends reliably on all platforms. Check event volume before you build on it. |
| **Smart Trigger** | Trigger on an event but only if a second event does or does not follow within a window. Abandoned-flow logic. | The "has not done" leg needs a realistic window. Too short and you nag people mid-task. |
| **Flows / Journeys** | Multi-step sequences with waits and branches. Onboarding, winback ladders. | Do not rebuild a flow as five periodic campaigns. Frequency capping cannot see the relationship between them. |

Rule of thumb: if the message is about the user's own behaviour, it is event
triggered. If it is about the world (market, product, price), it is one time or
periodic.

## Conversion goals

Always set a primary conversion goal, and make it the business event, not the
click. Click-through rate measures the copy. Conversion measures the campaign.

* **Primary goal**: the action the campaign exists to cause. For reactivation
  that is a trade or a session, not an app open.
* **Secondary goals**: two or three leading indicators you would want to see even
  when the primary does not move. Watchlist added, price alert set, chart viewed.
* **Attribution window**: match it to the real decision latency. A push about a
  market move converts within hours, so a 24-hour window is honest. A
  graduation or education campaign takes days, so 72 hours or 7 days. A long
  window inflates your numbers with conversions you did not cause.
* Set a **negative goal** where the platform allows it (uninstall, unsubscribe,
  notification disable). A campaign that beats its conversion goal while driving
  opt-outs is losing money slowly.

## Frequency capping and DND

* **Global frequency cap** is the safety net, not the plan. Typical consumer
  fintech settings: 2 to 3 pushes per user per day, 8 to 12 per week, with
  per-channel caps beneath the global one.
* **Campaign-level exemption** exists so transactional and time-critical messages
  bypass the cap. Use it for security, KYC, order fills and settlement. Never use
  it for marketing, however urgent it feels.
* **DND hours** should reflect the audience's timezone, not the office's. For an
  Indian consumer base, 22:00 to 08:00 IST is the normal quiet window. Market
  alerts are the tempting exception, and mostly should not be one: a push at
  03:00 about a move is a notification-disable event, not engagement.
* Capping is per user, so overlapping segments quietly compete for the same slot.
  If two campaigns target the same person on the same day, the one that sends
  first wins and the second is silently suppressed. Check overlap before you
  schedule, not after you wonder why reach was low.

## Delivery controls

* **Throttling / drip**: spread a large send over minutes or hours. Do it when
  the campaign drives traffic to a screen with backend cost, or when a support
  spike would follow. A 2 million push delivered in 90 seconds is a load test.
* **Send Time Optimization** (Best Time to Send, Sherpa-driven): predicts each
  user's likely-open time from their history. Good for non-time-critical
  campaigns with a segment large enough to have per-user history. Useless and
  actively harmful for anything tied to a market moment, because it will hold
  your message until tomorrow morning.
* **Intelligent Delivery / Sherpa optimisation**: let the platform pick the
  winning variant automatically. Only with enough volume, and only when the
  variants differ in copy rather than in offer.
* **Time to live / expiry**: set TTL shorter than the relevance of the message.
  A push about today's market that lands tomorrow because the device was offline
  is worse than no push. For market-linked pushes use a TTL of a few hours.
* **Push Amplification (Plus)** on Android improves delivery on OEMs that kill
  FCM. Leave it on. It changes delivery rate, not content.

## A/B testing and control groups

* **Variant split**: 2 or 3 variants maximum for a normal campaign. More variants
  on a fixed audience just means none of them reaches significance.
* Size the test so the smaller arm can actually detect the effect you care about.
  Detecting a 10% relative lift on a 2% conversion base needs roughly 30,000 users
  per arm. If your segment is 5,000 people, you are not running an A/B test, you
  are picking a variant with a coin.
* **Campaign control group**: hold out 5 to 10% and send them nothing. This is the
  only way to know the campaign caused anything, as opposed to catching people who
  were going to trade anyway. For reactivation campaigns the control group is not
  optional; dormant users reactivate on their own at a measurable rate.
* **Global control group**: a permanent small holdout across all campaigns. Set it
  once, leave it alone, and report against it quarterly.

## Naming and hygiene

Adopt one scheme and never deviate, because campaign analysis six months later is
entirely a naming problem:

```
<objective>_<audience>_<channel>_<angle>_<YYYYMMDD>
reactivation_dormant30d-spot_push_support-checkin_20260904
```

Tag campaigns with the objective and the market regime they were built for. When
you look back at why a campaign underperformed, "we sent a graduation push during
a capitulation week" is the answer more often than the copy.
