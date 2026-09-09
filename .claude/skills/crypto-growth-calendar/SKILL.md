---
name: crypto-growth-calendar
description: The recurring calendar that shapes trading-app behaviour and campaign timing — crypto market microstructure (funding hours, options/futures expiries, Asia/US sessions), macro prints in IST (FOMC, CPI, NFP), US equity hours for tokenised perps, token unlock and listing rhythms, and India-specific moments (Budget, ITR deadline, festivals, tax-loss season). Use when scheduling campaigns, building autopilot timing rules, or explaining a weekly pattern.
---

# Growth calendar for a crypto + tokenised-perps app (IST)

Timing is a lever as large as copy. Every entry below is a scheduling rule the agent may apply and a pattern to check before calling a number anomalous.

## Daily rhythm
- **Hyperliquid funding**: paid hourly; the 8-hour reference windows (00:00 / 08:00 / 16:00 UTC → 05:30 / 13:30 / 21:30 IST) are when position reviews spike. Funding nudges land best 30–60 min before 13:30 IST.
- **Asia open** (06:30–09:30 IST) and **US open** (19:00 IST; 18:30 in US DST) are session peaks for Indian users; **US close** 01:30 IST. Tokenised-perp content about US names: send at 18:00 IST ("before Wall Street opens") or 08:00 IST ("overnight moves").
- **India daytime, 10:00–21:00 IST**: SMS/WhatsApp promotional window (DLT/TRAI); push DND typically 22:00–08:00.
- **Best Time to Send** in MoEngage for non-urgent sends; **never** for market-linked (they carry a TTL).

## Weekly rhythm
- Weekend crypto volume drops 20–40%; weekend movers are often thin — hooks require ≥ $1M volume. Tokenised equities/indices on CoinDCX **trade through the weekend** — a genuine weekend feature to educate on (Saturday 11:00 IST).
- **Friday 13:30 IST (08:00 UTC)**: Deribit weekly/monthly options expiry — volatility pocket; risk-education timing. Last Friday of the month is the big one; quarterly (Mar/Jun/Sep/Dec last Friday) larger still, plus CME futures expiry.
- Monday 09:00 IST: weekly review; Sunday 19:00 IST: user weekly recap.

## Macro prints (US, in IST; times shift with US DST)
- **CPI** (monthly, ~18:00 IST), **NFP** (first Friday, ~18:00 IST), **FOMC** decision (8×/yr, ~23:30 IST; presser 00:00 IST), **PCE**, **GDP**. Rule: T−24h → risk-education to leverage users; T−2h → freeze market-linked promotional sends until T+2h; T+1h → factual recap allowed if the move is real.
- Indian macro: **RBI policy** (bi-monthly, 10:00 IST), **Union Budget** (1 Feb, 11:00 IST — watch for VDA tax changes; prepare an explainer template in advance).
- Source in engine: `market_snapshot.calendar` (high-impact list); the hook `cal_*` exists for these.

## Crypto-specific events
- **Token unlocks** (large cliff unlocks for held symbols): education/alerts, never "sell before unlock". Add an unlock source when available; until then treat as manual notes in the growth feed.
- **New listings**: Binance spot and Hyperliquid perps (main and builder dexes) are detected daily by the engine (`listings.new`). Listing-day copy is factual; the angle is blocked in stress regimes.
- **Bitcoin halving** (~every 4 years; last April 2024), ETF flow days, major protocol upgrades: education content weeks ahead, nothing predictive on the day.
- **Exchange incidents / regulatory headlines**: `news_risk_hold` hook — acquisition/upsell frozen 24h.

## India moments
- **1 Feb** Budget · **31 Jul** ITR deadline (crypto tax explainer campaign 1–15 Jul; TDS statements) · **Mar** tax-loss/harvesting season (education, no advice) · **Diwali / Muhurat** (equities tradition; a "markets that never close" tokenised-perp educational moment, festival greetings without offers to loss-dormant users) · **Salary week (1–7)**: deposit propensity peaks — deposit-friction and recurring-buy campaigns land best 2–6 of the month · **Exam/holiday seasons**: lower engagement, do not read as churn.

## US equity calendar for tokenised perps
- Regular hours 19:00–01:30 IST (18:30–01:00 in US DST); pre/post market moves show up in our 24/7 markets first — the core educational hook for builder-dex assets.
- **Earnings season** (mid-Jan, mid-Apr, mid-Jul, mid-Oct, ~4 weeks each): for users holding/watching a name, T−1d alert ("NVDA reports after US close; expect a larger move") with risk tools; never a trade suggestion. Requires an earnings source (Finnhub key optional in Settings) — until configured, the agent should say so rather than guess dates.
- **US market holidays** (NYSE closed): tokenised perps keep trading on CoinDCX — a factual education send in the morning IST.

## How the agent uses this
- `autopilot` mission timing: ride_the_market only inside 08:00–21:00 IST unless the hook is a service message; protect runs immediately on regime flips.
- Every scheduled campaign proposal states the IST send time and the calendar reason ("T−24h before CPI", "salary week").
- Anomaly reads check this calendar before labelling a Monday dip or an expiry-Friday spike as a problem.
