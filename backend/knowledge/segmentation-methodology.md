# Segmentation methodology in MoEngage

## The filter types, and what each is actually for

* **User attribute filters** describe a state: `kyc_status`, `lifetime_volume_inr`,
  `days_since_last_trade`. Cheap, but they are only as fresh as your last attribute
  sync. Never build a time-sensitive segment on a nightly-synced attribute.
* **User behaviour / action filters** describe what someone did or did not do, with
  a count and a window, optionally filtered on event attributes. This is where real
  cohorts live. `Did 'Trade Executed' at least 3 times where market_type is spot in
  the last 90 days` is a cohort. `lifetime_volume_inr > 10000` is a threshold.
* **Affinity filters** find the value of an event attribute a user engages with
  most. "Users whose most-traded asset is BTC" without you precomputing it.
* **Technology / reachability filters**: platform, app version, push opt-in,
  notification permission. Always intersect a push campaign with reachability, or
  your reported reach will be much larger than your deliverable audience.
* **Geo and timezone**: matters more than people expect for send timing.

## Behaviour beats attributes

A segment built only on attributes describes a snapshot. A segment built on
behaviour describes a trajectory, and trajectory is what predicts response.

Weak: `current_balance_inr > 5000 AND has_traded_futures = false`
Strong: the same, plus `did 'Asset Page Viewed' at least 3 times in the last 14
days` and `did NOT do 'Futures Position Opened' in the last 180 days`

The second one finds people who are looking and have not acted. The first finds
everybody with money.

## The four questions a good segment answers

1. **What did they do, and when?** A window with a real number in it.
2. **What have they not done?** The absence is usually the point of the campaign.
3. **Who should be excluded?** Internal accounts, users who already converted, users
   in an active journey, users who churned out and should not be poked, users who
   received a similar message this week.
4. **Can we actually reach them?** Push opt-in, email subscribed, WhatsApp opt-in.

A segment missing question 3 is the most common cause of a campaign that annoys
exactly the wrong people.

## Complex segment patterns worth stealing

**Recency-frequency-monetary lifecycle.** Three behaviour filters, one each on
recency (`days_since_last_trade`), frequency (`trade_count_30d`) and monetary
(`spot_volume_30d_inr`). Cut each into three bands and you have nine cells. Do not
message all nine. Pick the three where a message plausibly changes behaviour.

**The near-miss.** Users who did step N but not step N+1 inside a window where
step N+1 was realistic. Deposited but did not trade within 72 hours. Opened the
futures screen but never opened a position. This is the highest-yield pattern in
consumer fintech because the intent is already demonstrated.

**The lookalike-by-behaviour.** Instead of a black-box lookalike, define the
behaviour signature of your successful cohort and target people who match the
signature minus the conversion. "Traded spot at least 8 times, holds more than
one asset, has set a price alert, has never opened a futures position."

**The negative-signal exclusion.** Users who recently got liquidated, took a large
loss, raised a support ticket, or withdrew most of their balance should be excluded
from anything upsell-shaped and routed to a different message entirely, or to
nothing. This costs you reach and saves you churn.

**The fatigue-aware audience.** Exclude anyone who received a campaign in this
family in the last N days, using a campaign-received filter or a custom attribute
you stamp on send.

## Dynamic versus static

Most segments should be dynamic: evaluated at send time, so membership is current.
Use a static or exported list only when you need the exact same people across
several campaigns for a controlled test, or when you are coordinating with an
offline channel.

## Sizing

* Under about 1,000 users, you cannot learn anything from the result. Either widen
  the definition or accept it as an operational message, not an experiment.
* Over about 500,000, ask whether the segment is a real cohort or just "most of the
  base with two filters on it". A segment that big usually needs splitting, because
  one message cannot be right for all of them.
* The workable band for a learning campaign is roughly 5,000 to 400,000.

## Before you ship a segment

Read it back in plain English to someone who did not build it. If they cannot
predict what message it should get, the segment is not a segment yet. Then check
the estimated reach against your intuition; a count that is 10x off what you
expected almost always means a window or an operator is wrong, not that you
discovered something.
