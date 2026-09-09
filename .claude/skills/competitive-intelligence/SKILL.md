---
name: competitive-intelligence
description: How CoinDCX reads and acts on competitor data — which venues are tracked (Delta Exchange India, WazirX, Mudrex, ZebPay, Bitbns, Giottus, KoinBX, Bybit as global reference) from public tickers, how volume share, surges, listing gaps, edges and funding edges are computed, what each action type means and who owns it, the real-time playbook to counter a surge or defend a pair, the honesty rules about self-reported volumes, and the strict rule that none of it ever appears in user copy. Use for any "what are competitors doing", "where are we losing volume", or "should we list X" question.
---

# Competitive intelligence (internal)

## What we track and how (`competitor_intel`, Market tab card)
- **Venues**: Delta Exchange India (perps + options, `api.india.delta.exchange`), WazirX (INR spot), Bybit (global perps reference), and via CoinGecko exchange pages: Mudrex, ZebPay, Bitbns, Giottus, KoinBX (top tickers + 24h BTC volume). Ours: `api.coindcx.com` ticker (INR markets = own volume; USDT markets = routed liquidity, shown as *reported*), perps from the liquidity venue as *reference*.
- **Normalisation**: one row per venue × pair × product with 24h USD volume, OI, funding, 24h change; symbol synonyms (XAU→GOLD, 1000PEPE→PEPE …). Every refresh is stored so surges compare against a snapshot ≥ 45 minutes old and listing first-seen dates are known.
- **Signals**: INR-spot share table · pair battles (our share per pair vs each venue) · surges (≥ `competitor_surge_pct`, ≥ `competitor_surge_min_usd`) · listing gaps (they have it, we don't) · our edges (we lead by 1.5× or more) · funding edges on shared perps.
- **Honesty**: volumes are self-reported and vary in quality; USDT/perps volumes are not "ours" for share; CoinGecko pages carry only the top 100 tickers. Say which bucket a number comes from.

## Actions and owners
| type | trigger | owner | what happens | SOP |
|---|---|---|---|---|
| counter_surge | pair surging elsewhere and we list it | marketing | same-day asset spotlight to our watchers/holders of the pair (fact + tool, TTL 4h); liquidity checks spread | `sop_asset_spotlight` |
| share_defence | our share of a ≥ $1M pair < 25% | liquidity + marketing | maker incentives / spread review; product-cohort programme for that pair's traders | `sop_product_cohort_monthly` |
| listing_request / listing_gap | pair surging or trading ≥ min USD elsewhere, not listed here | product | `request_data(kind='other', title='List X')` with the volume evidence | — |
| funding_edge | our funding cheaper for the crowded side | marketing | funding-cost education to habitual perp traders | `sop_funding_crowding_nudge` |
| press_advantage | we lead a pair by 1.5×+ | marketing | keep the depth story: spotlight to sector watchers, fee-tier nudges | `sop_asset_spotlight` |
The `compete` autopilot mission drafts the marketing actions and files product asks daily; the human approves.

## Real-time playbook (surge on pair X elsewhere)
1. `pair_battle('X')` — where the volume is, our share, funding. 2. Regime check — stress regimes block spotlights. 3. If we list X: `run_sop('sop_asset_spotlight', segment_name=<X watchers/holders family>)` with two compliant variants; ask liquidity to check spread/depth. 4. If we don't: `request_data` listing ask with evidence; consider a related pair we do list only if the user intent is the same sector. 5. Next day: our share of X in `pair_battle` is the readout.

## Rules that never bend
- No competitor or venue name in any user-facing message; the brief check blocks them. The intelligence is for decisions, the copy is about CoinDCX.
- Never imply a price move from a competitor surge; the spotlight states verifiable facts (volume record on CoinDCX, availability, 24h change) and a tool.
- Listing asks are evidence, not decisions: product and compliance decide.

## Better data (when the team provides APIs)
CoinGecko Pro (historical exchange volumes, higher rate limits) · Kaiko / CCData / Coinalyze (cross-exchange trades, OI, liquidations by venue) · our internal trades API (true volume by pair and segment, market share by user cohort) · app-store rank and Similarweb/Sensor Tower (acquisition share) · social volume (LunarCrush / X API). Keys go into Settings; the module already has hooks for a per-venue direct API when one exists.
