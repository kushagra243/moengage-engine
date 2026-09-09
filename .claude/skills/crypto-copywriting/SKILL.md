---
name: crypto-copywriting
description: Copy system for a crypto spot/perps trading app across push, in-app, email, WhatsApp and SMS — frameworks per trigger (fact → relevance → tool), a bank of compliant example lines and subject lines with A/B variants, tone rules, MoEngage personalisation syntax with fallbacks, Hinglish and regional examples, and what to test first. Use when drafting or reviewing variants.
---

# Copy for a trading app

## Rules that never bend
0. **We are CoinDCX.** Never name a liquidity venue (Binance, Hyperliquid) or a competitor in any message; say "on CoinDCX" or "here". Venue data is for our intelligence only. "SIP" is our name for recurring buy.
1. **Fact → relevance → tool.** Every market message states a verifiable fact, why it matters to *this* user, and one thing they can do in-app. No fourth sentence.
2. **No direction, no forecast, no urgency tied to price.** Banned words list in `crypto-compliance-copy`.
3. **Numbers are rounded, time-stamped, and true at send** (TTL ≤ 4h). Prefer the user's own numbers (their P&L, their alert) over the market's.
4. **Push**: title ≤ 60 chars, body ≤ 140, one CTA, no emoji stacks (max one, never 🚀/📈 on price). **Email subject** ≤ 50 chars, preheader adds the number. **WhatsApp**: approved template, utility tone. **In-app**: one idea, dismissible, never blocks trading. **SMS**: DLT template, ≤ 160 chars, no links except the brand short-link.
5. **Personalisation with fallbacks**: `{{UserAttribute['first_name'] | default('there')}}`, `{{EventAttribute['symbol']}}`, Content API values for live numbers. A missing attribute must never drop the user silently; use a default.
6. **Tone**: calm, specific, adult. We are the sober friend who knows the numbers, not the hype account.

## Frameworks by trigger (with example variants)
**Mover on watchlist** (angle: watchlist_adoption / alerts_adoption)
- A: `SOL moved 6.2% today` / `It's on your watchlist. See the move and set a 5% alert so you don't have to watch the screen.` CTA: *Set alert*
- B: `Your watchlist: 3 assets moved >5%` / `SOL +6.2%, AVAX −5.1%, LINK +5.4% (as of 14:30 IST). Review them in one screen.` CTA: *Open watchlist*

**Funding nudge** (risk_education; users long a crowded perp)
- `Funding on BTC-PERP: 0.04%/h` / `Longs are paying ≈0.96% a day right now. Check what your open position costs and adjust if you want.` CTA: *View position*
- Never: "shorts are about to squeeze", "close now".

**OI surge** (risk_education)
- `ETH open interest +34% in 24h` / `Leverage in ETH is building fast while price is flat. Crowded books move sharply. Review your margin buffer.` CTA: *Risk tools*

**New listing** (new_listing; trending_up / chop only)
- `PENDLE is now on the spot market` / `Trade it against USDT from today. Add it to your watchlist to follow it without trading.` CTA: *View PENDLE*
- Never: "get in early", "listing pump".

**Tokenised perp, market closed** (product_education)
- `NVDA moved 4.1% after US close` / `On CoinDCX, NVDA trades 24/7 as a perp. See how it's pricing before Wall Street opens.` CTA: *View NVDA perp*

**Liquidation recovery, T+24h email**
- Subject: `What happened to your ETH position` — Preheader: `The numbers, plainly, and what margin mode changes.`
- Body: their entry, liquidation price, margin mode, funding paid; a 4-line explainer of why isolated margin caps loss; one link to the position-size calculator; support link; full derivatives disclaimer. No CTA to trade.

**Deposit friction (UPI failed)**
- `Your ₹5,000 deposit didn't go through` / `UPI timed out on the bank side — nothing was debited. Retry takes 20 seconds, or use IMPS.` CTA: *Retry deposit*

**Re-KYC**
- `Your account needs a 2-minute update` / `Re-verify by 30 Sep to keep withdrawals uninterrupted. Aadhaar or PAN, in-app.` CTA: *Update now*

**Habit / alerts adoption after a volatile day**
- `Today moved fast. Alerts don't need you to watch.` / `Set one 5% alert on the asset you check most. We'll ping you once, not fifty times.`

**Weekly recap** (Sunday 19:00 IST, email or Cards)
- Subject: `Your week: +2.4%, 3 trades, 1 new asset` — content from the user's own data; market section is one paragraph of facts, no outlook.

**Regime stress (service tone only)**
- `Markets are volatile today` / `Trading and withdrawals are running normally. Support is faster in-app than on X. Risk tools are here if you want them.`

**Competition** (trending_up / chop; not to liquidated-14d, not UK)
- `Spot trading challenge: 7 days, fee rebates for the top 500` / `Ranked by volume, not P&L. Terms inside.` (P&L leaderboards need equal-prominence loss disclosure — avoid.)

## Hinglish / regional
Use for push and WhatsApp to users whose `app_language` ≠ en; keep tickers and numbers in English.
- `SOL aaj 6.2% move hua` / `Aapki watchlist pe hai. Ek 5% alert set karo — screen dekhne ki zaroorat nahi.`
- `Aapka ₹5,000 deposit complete nahi hua` / `Bank side pe UPI timeout — paise debit nahi hue. Dobara try karo ya IMPS use karo.`
Telugu/Tamil/Bengali: translate with a native reviewer; never machine-translate compliance text; the ASCI disclaimer stays in English on the landing screen.

## Subject-line patterns that test well in fintech (test, don't assume)
own-number > market-number > question > how-to > brand. `Your`, a specific figure, and a time anchor beat adjectives. Avoid ALL CAPS, "!!", "free", "guaranteed", and anything that looks like a price forecast.

## What to A/B first (one variable per test, 20% holdout, brief in `clm-operator`)
1. Own-number vs market-number title on watchlist movers.
2. Tool CTA ("Set alert") vs screen CTA ("View SOL").
3. Card vs push for daily recap (fatigue and unsubscribe as guardrails).
4. Hinglish vs English for `app_language=hi` users.
5. Send at move time vs batched at 19:00 IST for non-urgent market facts.

## Review checklist for any variant
fact verifiable · relevance stated · one tool · no banned words · numbers rounded + time · length limits · fallback on every token · disclaimer where required · exclusions in the brief · TTL set.
