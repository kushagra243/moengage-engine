---
name: product-marketing
description: Product marketing for a crypto spot, perps and tokenised-perps trading app — positioning and messaging hierarchy, value propositions per trader state, feature launch and adoption playbook through lifecycle channels, moments-that-matter map, competitive framing versus centralised and decentralised exchanges, pricing and fee communication, and message-market-fit testing. Use for launches, positioning, feature adoption programmes and any "how do we talk about X" question.
---

# Product marketing for the trading app

Lifecycle channels are the distribution for product marketing; product marketing gives lifecycle its message. This skill connects the two.

## Positioning (write it once, reuse everywhere)
Template: *For [trader state] who [job to be done], [product] is the [category] that [key benefit] because [proof]. Unlike [alternative], we [difference].*
- Spot for new users: the calm, transparent place to start — fees shown before you trade, instant withdrawals, alerts instead of screen-watching.
- Perps for habitual traders: risk tools first (isolated margin default, funding shown per day, liquidation distance visible), then depth.
- Tokenised perps (Hyperliquid builder dexes): the markets that never close — NVDA, gold, SPX 24/7, priced on-chain, in the same account. Proof: after-hours moves the user can see before Wall Street opens.
Never position on returns, leverage size, or "cheapest" unless verifiably true and stable.

## Messaging hierarchy (one line per level; every campaign picks a level)
1. Brand promise: know your numbers, keep your money moving.
2. Pillars: transparency (fees, TDS, funding), control (alerts, risk tools, instant withdrawals), access (spot ∪ perps ∪ tokenised markets), service (support that answers).
3. Proof points per pillar (facts a user can verify in-app): fee page, withdrawal times, funding-per-day display, 24/7 markets list, support response time.
4. Feature messages: one job, one screen, one CTA.

## Value propositions by trader state (from `crypto-derivatives-marketing`)
| state | job to be done | lead message | avoid |
|---|---|---|---|
| Spot-only | grow without stress | alerts + recurring buy + transparent fees | perps, leverage |
| First-perp | not get wrecked | isolated margin, SL habits, funding explained | size-up |
| Habitual perp | edge and cost | fee tiers, funding digest, tokenised markets | promos |
| Hedger | protect a portfolio | hedge calculator, basis/funding views | direction talk |
| Liquidated | be treated fairly | plain explanation, tools, support | any promo |
| Tokenised explorer | trade what they follow | 24/7 access, pre/post-market moves, earnings calendar (education) | "trade earnings" |

## Feature launch playbook (through lifecycle channels)
1. **Define**: the job, the trader states it serves, the adoption KPI (feature_used_7d among exposed), the guardrail (support tickets, unsubscribes), and the SOP that will carry it.
2. **Beta cohort** (1–5% of the target state, opt-in): in-app invite; instrument `feature_used`, `feature_abandoned {step}`; fix the abandonment step before broadening.
3. **Announce** (day 0): in-app card to the target states + email with the how-to; push only to users with the job (viewed the related screen in 30d). Disclaimer where required.
4. **Adopt** (day 1–14): trigger on intent (screen viewed, not used) → one nudge with the single most valuable use; never re-announce.
5. **Habit** (day 14–45): weekly recap includes the feature's own-number ("your 3 alerts fired"); Cards for reference content.
6. **Read** (day 30): adoption curve by state, retention delta of adopters vs matched non-adopters (say "associated", not "caused" unless holdout), lessons to the feed.
Checklist: naming consistent across app, docs and messages; screenshots current; deep link tested; personalisation fallbacks; compliance review for anything touching derivatives or rewards.

## Moments that matter (map messages to these, not to a calendar of your own)
first deposit landed · first fill · first loss · first liquidation · fee-tier change · large move on a held asset · funding extreme on an open position · macro print tonight · new listing in a watched sector · KYC/re-KYC due · withdrawal completed · long absence + regime change · salary week · tax season.

## Competitive framing (say what is true, never name-and-shame)
- vs large CEXs (Binance, Bybit, OKX): we compete on INR rails, service, transparency and risk tools, not on breadth or leverage.
- vs Indian incumbents (CoinDCX, CoinSwitch, Delta): tokenised 24/7 markets and on-chain perps are the distinct access story; keep compliance posture identical or stronger.
- vs DEX-native (Hyperliquid direct): one account, INR on/off-ramp, support, risk defaults; be honest that the venue is Hyperliquid.
Proof beats claims: show the fee page, the funding display, the withdrawal timer.

## Pricing and fee communication
Total cost per trade (fee + TDS + spread where relevant) shown before confirmation; tier tables in plain numbers; changes announced ≥ 7 days ahead by email to affected tiers; never "zero fee" if a spread exists.

## Message-market fit testing
For each pillar, test the lead message in in-app cards to the target state with a 20% holdout: click → feature_used within 7d. Keep the winner as the canonical line in this skill (update it), and record losers in the growth feed so nobody re-tests them.
