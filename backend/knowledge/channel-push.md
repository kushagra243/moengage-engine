# Push notifications

## Hard constraints

* **Title**: aim for 40 to 50 characters. Android truncates around 65, iOS earlier
  in the collapsed state. Never put the payload of the message in characters 50+.
* **Body**: 120 to 180 characters is the working range. Collapsed notifications
  show roughly one line; the rest is only seen if the user expands.
* **The first four words carry the message.** Everything after is read only by
  people you already convinced.
* **Deep link every push.** A push that opens the home screen wastes the intent it
  just created. Link to the exact screen the copy promised.
* **TTL**: for market-linked pushes, 2 to 6 hours. For evergreen, 24 to 72.
* **Rich push** (image, action buttons) lifts CTR meaningfully on Android and is
  worth the extra build for flagship sends. Keep the image meaningful, not
  decorative; a chart or an asset icon beats stock art.
* **Action buttons**: two maximum, and they must be genuinely different actions,
  not "Open" and "Open".

## What actually moves push performance

1. **Reachability, not creativity.** Push opt-in rate on iOS is the single biggest
   lever. Always intersect the segment with notification-enabled, and track the
   deliverable audience separately from the segment size.
2. **Relevance to a thing that just happened.** Event-triggered pushes outperform
   scheduled ones by a wide margin because the context is fresh.
3. **Timing.** For an Indian consumer base, weekday 09:00 to 10:30 and 19:00 to
   21:30 IST are the reliable windows. Avoid 13:00 to 15:00. Never send between
   22:00 and 08:00 without a transactional reason.
4. **Frequency.** The relationship between push volume and uninstall is not linear;
   it is a cliff. Watch notification-disable rate as a first-class metric.

## Market-centric push for a trading product

This is the highest-risk push category you will send, because it sits next to
financial advice. What works and stays defensible:

**Do**
* State a fact the user can verify: "BTC is up 6% this week." "Three assets on
  your watchlist moved more than 5% today."
* Reference the user's own relationship to the market: their watchlist, their held
  assets, an alert they set, a position they have open.
* Offer a tool, not a trade: review your watchlist, set an alert, check your
  positions, read the weekly recap.
* Time it to when the information is still true. Market pushes have a shelf life
  measured in hours.

**Do not**
* Predict. "BTC is heading to 100k" is not a marketing message, it is a call.
* Promise or imply a return, in numbers or in vibes.
* Tell the user to buy or sell a named asset.
* Manufacture urgency about a price move. "Last chance" attached to a market
  number is the exact phrasing that gets screenshotted.
* Send a leverage or futures nudge into a violent down move. It converts, and
  then it churns, and then it is a complaint.

**Regime discipline.** The same audience needs a different angle depending on the
market. In a strong uptrend, reactivation and trend-following land. In a
high-volatility drawdown, the only defensible angles are risk education, capital
preservation, and a genuine check-in. In chop, education and product features
outperform anything market-linked, because there is no move to reference.

**The honest opener beats the clever one.** "Been a while. Everything okay with
your account?" outperforms "Markets are moving without you" with dormant users who
left after a loss, because the second one is a taunt to someone who lost money.

## Payload and platform notes

* Android and iOS render differently enough that you should preview both. Emoji
  render inconsistently across OEM skins; two maximum, and never load-bearing.
* Collapse key / notification id: set it so a second market push replaces the
  first rather than stacking. Nobody wants three price notifications in a row.
* Silent / data pushes do not count toward the visible frequency cap but do count
  toward user patience if they wake the app.
* Android 13+ requires a runtime notification permission. Treat the permission
  prompt as a campaign of its own, with its own timing.
