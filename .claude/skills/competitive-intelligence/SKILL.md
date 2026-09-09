---
name: competitive-intelligence
description: How CoinDCX reads and acts on competitor data — which venues are tracked (Delta Exchange India, WazirX, Mudrex, ZebPay, Bitbns, Giottus, KoinBX, Bybit as global reference) from public tickers, how volume share, surges, listing gaps, edges and funding edges are computed, what each action type means and who owns it, the real-time playbook to counter a surge or defend a pair, the honesty rules about self-reported volumes, and the strict rule that none of it ever appears in user copy. Use for any "what are competitors doing", "where are we losing volume", or "should we list X" question.
---

# Competitive intelligence (internal)

## What we track and how (`competitor_intel`, Market tab card)
- **Free sources only.** Primary: CoinMarketCap public data-api (`api.coinmarketcap.com/data-api/v3/exchange/...`): per-exchange market pairs with USD volume (CoinDCX, WazirX, ZebPay, Bitbns, Giottus, KoinBX, Unocoin, Delta, Bybit) and the exchange listing (24h spot/derivatives/total volume, 24h/7d change, market share, maker/taker fees, weekly visits, score). Realtime depth: Delta Exchange India, WazirX, Bybit public tickers. Fallback: CoinGecko exchange pages. **CoinDCX's own volume comes from CMC, never from internal data** (`competitor_own_source=cmc`). INR markets = own volume; USDT markets = routed liquidity (*reported*); perps = liquidity-venue *reference*.
- **Normalisation**: one row per venue × pair × product with 24h USD volume, OI, funding, 24h change; symbol synonyms (XAU→GOLD, 1000PEPE→PEPE …). Every refresh is stored so surges compare against a snapshot ≥ 45 minutes old and listing first-seen dates are known.
- **Signals**: INR-spot share table · pair battles (our share per pair vs each venue) · surges (≥ `competitor_surge_pct`, ≥ `competitor_surge_min_usd`) · listing gaps (they have it, we don't) · our edges (we lead by 1.5× or more) · funding edges on shared perps.
- **Honesty**: volumes are self-reported and vary in quality; USDT/perps volumes are not "ours" for share; CoinGecko pages carry only the top 100 tickers. Say which bucket a number comes from.

## Category benchmarks (`competitor_benchmarks`, Intel → Best in industry)
Four categories, each with India and international leaders and our gap multiple:
- **Spot**: Binance, OKX, Bybit, Coinbase, Kraken, KuCoin, Gate, MEXC, HTX, Bitget (intl); CoinDCX, WazirX, ZebPay, Giottus, KoinBX, Unocoin (India). Source: CMC listing (24h volume, markets, fees, traffic).
- **Perps**: Binance, OKX, Bybit, Bitget, Gate, MEXC, KuCoin, Kraken, HTX, Hyperliquid (intl); Delta Exchange India (direct tickers). Pair-level from Binance futures, OKX swaps, Bitget futures, Bybit linear. Ours = liquidity-venue reference, labelled.
- **Options**: Deribit, Binance, Bybit, OKX (intl); Delta India (direct). Ours unknown until the team names a source.
- **Commodities / tokenised**: Bybit, Bitget, OKX, Gate RWA symbols (gold, silver, oil, indices, tokenised stocks) vs our builder-dex markets (reference).
`targets` translate the gap into numbers: daily volume needed to match the India leader and the global leader, a first step to 25 % of the leader, and the leader's top pairs where we are furthest behind (with the SOP to run or "not listed here" for product). Trends: a daily row per venue/category is kept 120 days.

## Campaign listening (`competitor_campaigns`, Intel → Campaigns detected)
Sources (free): Binance CMS announcements, Bybit and OKX announcement APIs, Bitget announcements (news, listings, product updates), Mudrex blog RSS, Google News per Indian venue (Delta, CoinSwitch, Mudrex, WazirX, ZebPay, Pi42, plus Binance/Bybit/Bitget/Coinbase/OKX India), App Store release notes and Finance-chart ranks (India) for CoinDCX and rivals. Keyword classification into trading_competition, fee_promo, cashback_bonus, stock_perps, options, listing, product_launch, earn_apy, airdrop, referral, learn_earn, festival_offer, vip_program (delisting/maintenance are noise). Ranked by type weight × recency × venue pressure; MATERIAL items become `counter_campaign` actions and hooks. Counters are our SOPs — never a matched bonus, never a named rival: competitions → volume-ranked competition or asset spotlight; fee promos → total-cost transparency and tiers; bonuses → habit tools and a 14-day watch on deposit rate; stock perps / options → education to intent users and a product check; listings → spotlight or listing request; launches → adoption plan if we have the feature.

## Hyperliquid vs centralised (`onchain_vs_cex`, Intel → Hyperliquid vs centralised)
Hyperliquid is the venue behind our perps, so its figures are the ceiling of what our perps product can show — never our own volume. Sources: Hyperliquid info API (daily volume, OI, users, per-coin contexts), CMC derivatives listing (CEX volume/OI), DefiLlama open-interest overview (free; the on-chain *volume* overview is paid), Binance futures funding (all symbols, one call) + OI per symbol, Bybit linear tickers, OKX OI + funding. Outputs: HL rank and share against the CEX top five, HL OI vs Binance OI, the on-chain landscape (Hyperliquid Perps, tradeXYZ and other builder dexes, Aster, Lighter, edgeX, GRVT…) with HL's share, and per coin the OI share and hourly funding across HL/Binance/Bybit/OKX with the cheapest venue for longs — a factual funding-education angle when HL is cheapest (copy never names venues). Coinglass (free key in Settings → Market as `market_coinglass_api_key`, stored encrypted) adds cross-venue OI, funding and liquidations; CoinMarketMan has no public API.

## Dossiers (`competitor_dossier`, Intel → Open dossier on a rival card)
One marketing profile per rival: pressure index and threat; volume, 24h/7d change, INR share, fees, traffic, CMC score; position by category (rank, volume, OI, leader, top pairs); App Store rank/rating/version with 7-day deltas; campaigns in the last 7 days (count, cadence per day, by type, channels, audience focus: acquisition / activation / retention / product); inferred playbook (e.g. "bonus-led acquisition + listing-velocity"); latest surges and listing gaps; what changed in 7 days; our counters per play; "how we beat them" written for a marketer. Use it before a counter-campaign brief or a monthly plan.

## Money flow and trader behaviour (`money_flow`, Brain Lab top)
Start here. Signals (all free): risk-appetite composite; BTC/ETH dominance shift (rising = consolidation into BTC, alts cut; falling = alt rotation); stablecoin supply 1d/7d and which chains gain (net minting = dry powder → deposit-readiness; shrinking = liquidity leaving → retention over acquisition); CoinGecko sector rotation in/out (spotlight the pairs we list in sectors rotating in; protect holders in sectors bleeding); our venue's volume share by asset class (US-stock / index / commodity perps rising = cross-sell window in US hours); attention spikes vs 7-day average (watchlist/alert campaigns, one per user per day); leverage build (risk education to crowded longs) or flush (liquidation recovery, promo silence); web3 gauge (safety-first trend watch). Each read carries evidence and a confidence; each recommendation carries who/what/SOP/KPI/urgency/avoid and an ICE score. Stress regime overrides everything: service mode, promos frozen.

## Market Feed layers (`market_flash`, Brain Lab → Markets)
Flash strip = breaking now, ordered alert → watch → good → info: Tier-0, regime, big moves (crypto ≥5 %, US-stock perps ≥3 %, indices ≥1.5 %, commodities ≥2 %), OI surges/flushes, listings, funding extremes, risk headlines from the last 6 h, material competitor actions, high-impact macro prints in the next 24 h, web3 spikes — each tagged with the CoinDCX products it touches. Biggest news (risk first, then freshest, de-duplicated, with product tags). Top open interest (reference venue OI, 24h change, funding APR, HL share of OI and the cheapest venue for longs when the HL-vs-CEX comparison is cached). Top assets per category (crypto, US stocks, indices, commodities, FX, web3). Intel by product: for each product its lens, today's facts, matching hooks and SOPs, and the never-list — status act / watch / quiet.

## Actions and owners
| type | trigger | owner | what happens | SOP |
|---|---|---|---|---|
| counter_surge | pair surging elsewhere and we list it | marketing | same-day asset spotlight to our watchers/holders of the pair (fact + tool, TTL 4h); liquidity checks spread | `sop_asset_spotlight` |
| share_defence | our share of a ≥ $1M pair < 25% | liquidity + marketing | maker incentives / spread review; product-cohort programme for that pair's traders | `sop_product_cohort_monthly` |
| listing_request / listing_gap | pair surging or trading ≥ min USD elsewhere, not listed here | product | `request_data(kind='other', title='List X')` with the volume evidence | — |
| funding_edge | our funding cheaper for the crowded side | marketing | funding-cost education to habitual perp traders | `sop_funding_crowding_nudge` |
| press_advantage | we lead a pair by 1.5×+ | marketing | keep the depth story: spotlight to sector watchers, fee-tier nudges | `sop_asset_spotlight` |
| fee_position | a tracked Indian venue lists a lower taker fee (CMC) | product + marketing | lead with total-cost transparency and tiers; review thresholds | `sop_fee_tier_nudge` |
| venue_volume_jump | a venue's total volume +40% in 24h (CMC) | marketing | inspect its surging pairs; counter on ours | `sop_asset_spotlight` |
The `compete` autopilot mission drafts the marketing actions and files product asks daily; the human approves.

## Real-time playbook (surge on pair X elsewhere)
1. `pair_battle('X')` — where the volume is, our share, funding. 2. Regime check — stress regimes block spotlights. 3. If we list X: `run_sop('sop_asset_spotlight', segment_name=<X watchers/holders family>)` with two compliant variants; ask liquidity to check spread/depth. 4. If we don't: `request_data` listing ask with evidence; consider a related pair we do list only if the user intent is the same sector. 5. Next day: our share of X in `pair_battle` is the readout.

## Rules that never bend
- No competitor or venue name in any user-facing message; the brief check blocks them. The intelligence is for decisions, the copy is about CoinDCX.
- Never imply a price move from a competitor surge; the spotlight states verifiable facts (volume record on CoinDCX, availability, 24h change) and a tool.
- Listing asks are evidence, not decisions: product and compliance decide.

## More free sources worth adding (no keys or free tiers)
DefiLlama (DEX and chain volumes, free) · Coinalyze free tier (OI/liquidations, key) · Binance/Bybit/OKX public tickers (already partly used) · iTunes lookup API (app rating counts as a growth proxy) · CoinGecko free (already). Paid feeds (Kaiko/CCData, Similarweb) are out of scope for now.
