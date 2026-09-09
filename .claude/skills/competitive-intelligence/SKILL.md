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
