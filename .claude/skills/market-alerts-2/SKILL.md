---
name: market-alerts-2
description: CoinDCX Market Alerts 2.0 as the engine runs it — the BRD's Relevance (P1) and Discovery (P2) themes, the 1+1 per-category daily cap, breach extras, priority orders, Futures cross-sell and Referral outside the cap, the assumptions made where the BRD is silent, the pilot and holdout process, the cohort file contract, MoEngage setup, and the copy rules alerts must pass. Use for anything about automated push market alerts, the Market Alerts tab, or the MA2 pilot.
---

# Market Alerts 2.0

Source: *BRD: Market Alerts 2.0 — Automated Market Alerts via Push Notification*. Code: `backend/alerts2/`. Terminal module: Market Alerts (`#alerts`).

## The framework
| Priority | Category | Theme | Baseline | Breach extra |
|---|---|---|---|---|
| P1 | Relevance | PnL, position level | 1/user/day at ±5% | +1 when PnL moves a further 10 points from the last alert on that position |
| P1 | Relevance | Price movement on relevant tokens | 1/user/day on Z-score | +1 when Z exceeds 2x the threshold |
| P2 | Discovery | Price trending: 1-year ATH/ATL, BTC $1,000 / ETH $200 milestones | 1/user/day | +1 on each further $2,000 BTC band, max 2 milestone alerts/day |
| P2 | Discovery | Volume trending: whale over most traded | 1/user/day | none |
| exempt | Moment of truth | Futures cross-sell | 1/user/month | none |
| exempt | Moment of truth | Referral | 1/user/lifetime | none |

**Caps.** One baseline plus one breach extra per category per day, so 2 a day normally and 4 only when both categories breach. A second alert in a category needs its breach criteria; baselines never stack. Relevance is the anchor. Cross-sell and Referral ignore the cap. Capping lives in the engine, so MoEngage frequency capping must be off for these campaigns.

**Priority inside a theme.** Active token or position first, then previously traded or watchlisted (equal), then product Futures > US Futures > Options > Spot, by the products the user actually uses. Volume: whale before most traded, then product order.

**Coverage.** PnL: futures positions. Price movement and price trending: Futures, Spot, Options users. Volume trending: Futures and Options users; most-traded excludes BTC, ETH, SOL for futures and spot. Cross-sell: pure spot users holding a futures-listed token who opened the futures screen in the last 7 days; highest AUC token wins. Referral: realised futures trade ≥10% profit and ≥₹1,000 volume first, else unrealised spot on the same thresholds.

**Candles.** Futures, US Futures and Options hourly (5-minute variant via `futures_fast_variant`); Spot daily. Options move with their underlying.

## Assumptions the engine made (the BRD is silent)
Z threshold 2.0 (breach 4.0) · Z on the latest candle return against the previous 168 hourly / 288 five-minute / 90 daily returns · PnL breach is 10 percentage points from the last alert on the same position, any day · PnL before Price Movement, Price Trending before Volume Trending · ETH breach $400 · ATH/ATL cap is 2 days per token per ISO week across the cohort · Volume audience Futures|Options · US Futures ranks right after crypto futures · cross-sell x = 5% over 24h · one alert per category per run · quiet hours 22:00–08:00 IST · Discovery and Cross-sell pause in capitulation or high-volatility-down regimes · 20% holdout. All are settings in `ma2_config_json` and shown in the tab. Say which assumption you relied on.

## Process
1. **Cohort.** The data team uploads JSON or CSV with opaque MoEngage customer ids and exposure: products, positions (with the app's PnL where possible), spot holdings with AUC, watchlist, traded tokens, futures-screen views, profitable trades, flags. Names, emails, phones, PAN, Aadhaar, device or bank ids reject the whole file. Template: `GET /api/alerts2/cohort/sample`.
2. **Dry run.** Always allowed. Shows would-send per theme, holdout, suppression reasons and signals. Nothing sends, nothing is written to the cap ledger.
3. **Pilot.** `market_alerts_propose_pilot` queues kind `ma2_pilot` with the cohort's membership digest, holdout and days. A human approves on the Ideas board. If the membership changes, the pilot must be proposed again; refreshing positions for the same users is fine.
4. **Live.** The `market_alerts` refresher job runs every 15 minutes while the pilot is live and the kill switch is off. Each alert is one Data API user event (`MA2_Alert`, `MOT_FuturesCrossSell`, `MOT_Referral`); mock mode records instead. PnL alerts pause when the cohort file is over 60 minutes old.
5. **Measure.** Holdout users are decided and capped like treatment but never sent. Compare trades, deposits and futures activation for treated versus holdout from aggregate counts, not user rows.

## Copy
Push carries a fact, the user's relevance and a tool. The content team finalises wording; every template is linted before a pilot can be approved. Blocking rules: venue or competitor names, forecasts, ASCI forbidden words, direction, **hypothetical returns** ("could have made 25% profit"), **any leverage multiple** ("5x"), **implied safety** ("cap your downside"). The BRD's example cross-sell copy fails these rules and must not be used; the engine's compliant versions teach how futures work and the risk instead. The landing screen carries the ASCI disclaimer.

## MoEngage setup
Create the three user events and one event-triggered push campaign each, with title and body from event attributes and the deep link routed by `landing` + `token` + `product`. Switch MoEngage frequency capping off for those campaigns. Keep the send window consistent with 08:00–22:00 IST. Name them `MA2_Alert_Push`, `MOT_FuturesCrossSell_Push`, `MOT_Referral_Push`.

## Privacy
Raw ids exist only in the uploaded file under `data/ma2/` and in the outgoing API call. The ledger, run records and everything shown in the terminal or to the model use a keyed hash. Agent tools return aggregates only.
