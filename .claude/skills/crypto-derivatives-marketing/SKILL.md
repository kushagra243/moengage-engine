---
name: crypto-derivatives-marketing
description: Lifecycle marketing for spot, perpetual futures and tokenised perps (Hyperliquid main dex + builder dexes for equities, indices, commodities) — trader states, the perp adoption ladder, liquidation-recovery playbook, funding/OI/basis signals as campaign triggers, segment definitions, KPIs and the angles that are allowed per regime. Use for any campaign, segment or analysis that touches derivatives or market-linked sends.
---

# Derivatives & spot lifecycle marketing

**Brand rule:** we are CoinDCX. Binance (spot listings) and Hyperliquid (perps, builder dexes) are data sources for intelligence; they never appear in user copy, and neither does any competitor.

The product has three surfaces the CRM must treat differently: **spot pairs** (Binance-listed USDT pairs), **crypto perps** (Hyperliquid main dex, hourly funding, up to 50x on majors) and **tokenised perps** on Hyperliquid builder dexes (xyz / flx / km …: US equities, indices, commodities, FX as 24/7 perps). Data for all three is in `market_snapshot` (universe strictly = Binance spot ∪ Hyperliquid perps), `market_campaign_hooks` and the hacks library.

## Trader states (segment by behaviour, never by time alone)
| state | definition (events) | what the CRM owns | primary KPI |
|---|---|---|---|
| Spot-only | ≥1 spot fill, 0 perp fills | education → first perp with tiny size, or explicit "spot is fine" path | first_perp_trade_rate_30d (opt-in cohort only) |
| First-perp | 1–3 perp fills in 30d | position hygiene: isolated margin, SL/TP, funding explainer | second_perp_within_14d, liquidation_rate_30d ↓ |
| Leverage climber | median leverage rising ≥2 steps in 30d | risk education, not encouragement; nudge toward isolated margin | liquidation_rate, avg_leverage_stability |
| Habitual perp | ≥8 fills / 4 weeks, ≥3 distinct weeks | fee tier, funding digest, tokenised markets cross-sell | weekly_active_weeks_4w, products_per_user |
| Hedger | perps opposite to spot holdings | portfolio views, basis/funding tools | retention_90d |
| Liquidated (last 14d) | ≥1 liquidation event | **recovery flow** (below); suppress all promos 14d | return_to_trade_rate_30d, support_contact_rate |
| Loss-dormant | realised loss > X% of deposits, no trade 21d | service tone, education, no market sends | reactivation_rate_60d (measure, do not push) |
| Tokenised explorer | ≥1 fill on a builder dex | earnings/macro calendar alerts for held names, 24/7 education | tokenised_weekly_active |

Derive these as MoEngage user attributes from the tracking plan (`trading-event-taxonomy` skill): `perp_fills_30d`, `median_leverage_30d`, `last_liquidation_at`, `realised_pnl_30d_pct`, `dexes_traded`, `held_symbols[]`, `watchlist[]`.

## The perp adoption ladder (one transition per campaign)
1. **Spot-only → first perp (opt-in)**: only for users who opened the perps tab or a perp market screen ≥2 times (intent signal). Copy: what a perp is, funding in one line, isolated margin default, "start with 1x–2x". Holdout 20%, KPI first_perp_trade_rate_30d, guardrail liquidation_rate_30d of the cohort ≤ baseline.
2. **First perp → second perp**: within 72h of the first fill, an in-app card: how funding was charged, how to set SL. KPI second_perp_within_14d.
3. **Habitual → tokenised markets**: users trading crypto perps who hold equity watchlists or trade in US hours → "NVDA / gold / SPX perps trade 24/7 on CoinDCX". Angle product_education, never "trade earnings".
4. **Any → Hedger**: spot holders with concentration >40% in one asset during high_volatility regimes → "how a small short hedges a spot position" (education), tool link to hedge calculator. Angle risk_education.
Never ladder users *up* leverage. Size-up and leverage-upsell angles are blocked in every regime except trending_up, and even there only to Habitual users with liquidation_rate_90d = 0.

## Liquidation-recovery playbook (highest-leverage retention moment)
Liquidated users churn at 3–5× the base rate in the next 30 days; the first 48h decide it.
- T+0–2h: **nothing promotional**. Optional in-app service card: what happened (their numbers), where to see the liquidation record, support link.
- T+24h: email/WhatsApp (not push): plain-language explainer of margin, funding and ADL; a "position size calculator" tool; no "get back in".
- T+3–7d: education series (isolated vs cross, stop-loss habits) — opt-in, 1 message max per 2 days.
- T+14d: if no trade, a spot-first path ("trade without leverage") — measured against holdout.
- Suppress from: all market movers, funding nudges, competitions, referral, leverage content for 14d (30d if second liquidation in 90d).
KPI: return_to_trade_rate_30d vs holdout; guardrail: repeat liquidation within 30d must not rise.

## Signals → triggers (all available in `market_snapshot` / hooks)
| signal | trigger rule | audience | angle | copy fact |
|---|---|---|---|---|
| Funding APR > +20% (crowded long) | `crowded_long` list | users long that perp; leverage history | risk_education | "Funding on X is 0.04%/h ≈ 0.96%/day paid by longs" |
| Funding APR < −10% (crowded short) | `crowded_short` | users short that perp | risk_education | same, shorts pay |
| OI up ≥ 30% in 24h with |price| < 3% | `oi_movers.surge` | holders/watchers of X | risk_education / alerts_adoption | "Open interest in X rose 34% today — positioning is crowded" |
| OI down ≥ 25% after a move | `oi_movers.drop` | traders of X in 7d | risk_education | "Leverage flushed out of X (OI −28%)" |
| New listing (spot or perp) | `listings.new` | watchers of the sector; Habitual traders | new_listing (blocked in stress regimes) | "X is now tradable as spot / as a perp" — no launch-pump framing |
| Tokenised perp mover ≥ 3% | `equity_movers` etc. | tokenised explorers; watchlist | watchlist_adoption | "NVDA perp moved 4.1% while US markets were closed — trade it 24/7 on CoinDCX" |
| Macro print in <24h (FOMC/CPI/NFP) | `calendar` | leverage users | risk_education | "CPI at 18:00 IST; volatility usually rises — review margin" |
| Regime flip to high_volatility_down / capitulation | `regime` | everyone | suppression + service | pause acquisition/upsell; FC 1/day |
Every trigger message: fact + relevance + tool + TTL ≤ 4h; funding/OI numbers rounded and time-stamped; no direction, no forecast.

## Segments worth building first (MoEngage filters)
- `Perp intent, no perp trade`: viewed `perp_market_viewed` ≥2 in 14d AND `perp_order_filled` = 0 ever.
- `Crowded-long exposure`: `position_opened{side=long, symbol in crowded_long}` in 3d, not closed.
- `Liquidated 14d`: `position_liquidated` in 14d → suppression list (cohort sync daily).
- `Weekend perp traders`: fills Sat/Sun ≥2 in 4 weeks → tokenised markets (24/7) audience.
- `Earnings-week holders`: `held_symbols` ∩ earnings this week (calendar) → risk/alerts content.
- `Fee-tier near threshold`: 30d volume within 15% of next tier → habit/fee angle (trending_up or chop only).

## Reading derivatives data honestly
- Funding is annualised only for comparison; show users the per-day cost. APR ≠ yield; never call funding "income" in acquisition copy.
- OI in USD is price-sensitive: quote OI change together with price change.
- Tokenised-perp prices can diverge from the underlying's last close (24/7 vs exchange hours); copy says "on CoinDCX" — the liquidity venue is never named to users.
- Movers below $1M 24h volume are excluded from hooks by design; do not message illiquid names.

## KPIs the programme reports weekly
perp adoption funnel (viewed → first fill → second fill), liquidation rate by leverage band, funding-nudge response (position reduced within 24h vs holdout), tokenised weekly actives, share of volume from Habitual vs new, suppression coverage (liquidated users who received zero promos), and every experiment readout with its claim limits.
