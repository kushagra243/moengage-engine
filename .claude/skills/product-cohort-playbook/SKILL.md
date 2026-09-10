---
name: product-cohort-playbook
description: How CoinDCX treats each product cohort (spot, SIP, crypto perps, US-stock / index / commodity perps, options, earn, web3) — affinity from segment nomenclature, the treatment matrix (pillars, cadence, never-list, cross-sell only on intent), the three communication tiers with Tier-0 global announcements and per-product lenses for major moves and geopolitical events, and the web3 lane rules (trending tokens as unverified data, safety-first). Use when planning programmes across products, reacting to a major event, or working with web3 users.
---

# Product cohorts, tiers and the web3 lane

## Affinity is the first split
Segment and campaign names carry product codes; the engine maps them (`products.py`) and `segment_study.by_product` shows reach and performance per product. Codes: SPOT · SIP/DCA/RECURRING · PERP/PERPS/FUTURES/LEV/HFT · USS/USSTOCK/STOCK/TOKENISED · IDX/INDEX/SPX/NDX · COMM/GOLD/XAU/OIL · OPT/OPTIONS · EARN/STAKE/YIELD · WEB3/DEX/ONCHAIN/WALLET/SWAP + chains SOL/BASE/BNB/BSC/ETH/ROBINHOOD/MEME. A user with several products gets the most specific programme (web3 > options > tokenised > crypto perps > earn > SIP > spot) and every Tier-0 lens that applies. Unknown codes: `define_nomenclature`.
Pre-uploaded segment names are inspiration for new cohorts; propose new uploads with `request_data(kind='segment', …)` when a product cohort cannot be built from what exists.

## Treatment matrix (summary; live version: `product_cohorts` / SOPs tab)
| cohort | pillars | cadence | never | cross-sell (intent only) |
|---|---|---|---|---|
| Spot | watchlist, alerts, fees/TDS, SIP | ≤4 push/3 email wk | leverage, asset picks | SIP; perps on intent; earn on idle balance |
| SIP | continuity, DCA education, statements | ≤2/wk | market-timing, pause suggestions | spot alerts, earn |
| Crypto perps | risk tools, funding/OI notes, fee tiers, macro briefs | ≤4 push/wk, 1 market push/day | size-up, leverage lure, P&L boards | tokenised (US hours/equity watchlist), commodities (education) |
| US-stock perps | 24/7 access, after-hours facts, earnings-week risk notes | ≤3 push/wk around US hours | "trade earnings", forecasts, venue names | indices, commodities |
| Index & ETF perps (indices and ETFs on the liquidity venue: SP500, USTECH, SMALL2000, SOXL, EWY, IBIT …) | macro calendar, hedging education, ETF basics incl. 3× decay | ≤2/wk | direction, '3× returns' framing | US stocks, commodities |
| Commodity perps | macro/geopolitics context | ≤2/wk | safe-haven claims, direction | indices |
| Options | defined-risk education, expiry notes | ≤2/wk + expiry notes | strategy tips, "cheap premium" | perps risk tools |
| Earn | variable-APR transparency, risks | ≤1 push/wk + statement | guaranteed, passive income | SIP |
| Web3 | chain trends as unverified data, safety, watchlist | ≤3 push/wk, 1 trend/day | token picks, "100x", paid boosts, airdrop hype | spot for majors, earn for stables |
Communication limits, stage overrides and regime multipliers apply on top. No venue or competitor names anywhere.

## Three tiers
- **Tier 0 — global announcement** (major move |BTC 24h| ≥ 8%, regime flip into stress, cluster of geopolitical/regulatory headlines, incidents): `announcement_lenses(event)` → one verified, time-stamped fact for everyone + a lens per product cohort (spot: watchlist/alerts; SIP: plan continues; perps: margin/funding/liquidation distance; tokenised: US names after hours; indices/commodities: macro context; options: expiry/IV; earn: what changes for your position; web3: what it does to gas/liquidity + safety). Channels: in-app bottom sheet for all active users, push only where a tool applies (TTL 4h), same-day email, cards recap. Promotions frozen that day; in stress regimes every lens is service-only. SOP: `sop_global_announcement_lenses`; geopolitical/macro shocks: `sop_geopolitical_event_brief`. The `major_event_broadcast` autopilot mission drafts it for approval.
- **Tier 1 — product programme**: `sop_product_cohort_monthly` plus the product's SOPs; monthly statement with own numbers, one tool/explainer, one habit nudge, intent-only cross-sell step.
- **Tier 2 — individual triggers**: alerts, funding/OI notes, liquidation recovery, deposit failures, SIP balance checks.

## Web3 lane rules
Data: `web3_trending` (GeckoTerminal trending pools + DexScreener profiles/boosts) for Solana, Base, BNB Chain, Ethereum, Robinhood Chain; quality gate liquidity ≥ $150k, 24h volume ≥ $300k, pool age ≥ 24h, one-sided-flow and extreme-move flags; stable/wrapped bases excluded; **paid boosts are never trends**. Copy: facts (volume, liquidity, 24h change), the words "unverified token", a safety line (slippage, contract risk, only what you can afford to lose), watchlist CTA; never a pick, never "100x", never the data venue. Blocked in trending_down / high-vol-down / capitulation. Onboarding is safety-first (`sop_web3_onboarding_safety`) before any trend content (`sop_web3_trending_watch`). Compliance: ASCI disclaimer applies; memecoin content is the highest-risk copy we send — 20% holdout and complaint rate as guardrail always.

## Experiments and asks
Every proposed campaign is registered as an experiment at proposal time (the Experiments tab is the approval queue). When data is missing — a product-affinity segment, `market_viewed{product}` events, a `trader_state` attribute, an earnings calendar, dashboard captures for segment sizes — file `request_data` with why and what it unblocks, and put it in the flight plan's "needs data" section.
