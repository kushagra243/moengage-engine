# Market intelligence for user growth

## Why the market belongs in the CRM

User behaviour on a trading platform is driven more by market conditions than by
anything a marketer does. Trading volume, session frequency, deposit rate and
churn all move with volatility and trend. A CRM programme that ignores this will
credit itself for the market's work in a rally and blame its copy in a drawdown.

Two practical consequences:

1. **Every campaign result must be read against the regime it ran in.** Tag it at
   send time. An uplift of 20% in a rally week may be underperformance.
2. **The right message for an audience changes with the regime.** The audience did
   not change; their situation did.

## The regimes, and what each does to users

| Regime | What users do | What to send | What not to send |
| --- | --- | --- | --- |
| **Trending up** | Dormant users return unprompted, deposits rise, risk appetite rises, new signups spike | Reactivation, product depth, graduation, feature discovery. Ride it, and use the traffic to build habits that survive the turn | Anything that pushes size or leverage harder than the user's history supports |
| **Trending down** | Volume falls, withdrawals rise, dormancy rises, support load rises | Risk education, short-side education where legal, earn and stablecoin products, portfolio review | Win framing, FOMO, leverage upsell |
| **Chop** | Everything slows, engagement falls, nothing to react to | Education, fee and feature messaging, habit tools like alerts and watchlists | Anything market-referencing. There is no move to reference and it reads as noise |
| **High volatility down** | Panic sessions, liquidations, loss realisation, support spikes | Capital preservation, risk tools, genuine check-ins, service messaging | Every acquisition and upsell angle. This is a listen-and-support week |
| **Capitulation** | Mass liquidation, balance withdrawal, notification disables | Almost nothing. Service, safety, and silence | Everything else. The cost of a tone-deaf send here is measured in years |

## Signals worth computing

* **Trend structure**: price against a fast and a slow moving average. Cheap,
  robust, and enough for a regime label.
* **Realised volatility as a percentile of its own history**, not as an absolute.
  40% annualised vol means nothing until you know whether that is this asset's
  calm or its panic.
* **Drawdown from a recent high.** How users feel is driven by distance from the
  local peak far more than by year-to-date return.
* **Breadth**: what share of the tracked universe is up on the week. A market
  where only BTC is up feels very different to a user holding altcoins.
* **Funding skew**: crowded positioning, which predicts the violence of a reversal
  more than its direction.
* **INR premium or discount** against the global mark, which is the price your
  Indian user actually sees and the one that drives their perception.

## Turning a signal into a campaign, defensibly

The chain has to hold at every link:

```
signal  ->  a fact the user can verify
fact    ->  a relevance to THIS user (their watchlist, their holdings, their alert)
relevance -> a tool they can use (review, set an alert, check positions, read)
tool    ->  a screen that delivers what the message promised
```

If any link is missing you have a market comment, not a campaign. A push that says
"BTC is up 6%" with no relevance to the recipient and a link to the home screen is
noise with a price attached.

## Personalising on market data without giving advice

Safe and effective:
* "Three assets on your watchlist moved more than 5% today."
* "Your portfolio is up 4% this week. Here is the breakdown."
* "The alert you set on ETH triggered."
* "Volatility is at a 6-month high. Here is how our risk tools work."

Unsafe, regardless of how it is phrased:
* Any forecast.
* Any implied return.
* Any instruction to buy or sell a named asset.
* Any urgency framing attached to a price.

The distinction is not tone, it is whether the message would still be defensible
if the user acted on it and lost money.

## Market-aware timing

* Volatility spikes drive session spikes. Deliverability and app performance
  matter more than copy on those days.
* The hours after a large move are when watchlist and alert adoption convert best,
  because the value is self-evident.
* Weekly recaps land best on Sunday evening or Monday morning IST, before the
  week's decisions.
* Never schedule a market-linked campaign more than a few hours ahead. Set a short
  TTL and accept that some sends will be cancelled by the market moving first.
