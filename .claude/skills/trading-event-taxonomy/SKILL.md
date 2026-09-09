---
name: trading-event-taxonomy
description: The tracking plan a crypto spot + perps + tokenised-perps app needs in MoEngage — canonical event names, attributes, user attributes and derived traits (trader state, leverage band, liquidation recency, held symbols), naming rules, and what each event unlocks (segments, triggers, funnels, retention). Use when designing segments, diagnosing "we can't target that", or asking engineering for instrumentation.
---

# Trading event taxonomy (tracking plan)

Segmentation and triggers are only as good as the events. This is the minimum plan; names are `snake_case`, past tense for completed actions, attributes typed consistently. Send via SDK for in-app actions and via the Data API (`POST /v1/event/{workspace_id}`) for server-side facts (fills, funding, liquidations). Keep `customer_id` = internal user id everywhere.

## Lifecycle & account
| event | key attributes | unlocks |
|---|---|---|
| `signup_completed` | method, referral_code, country, utm_* | acquisition cohorts, referral loops |
| `kyc_started` / `kyc_submitted` / `kyc_approved` / `kyc_rejected` | level, reason, duration_s | Acquired→Verified funnel, Re-KYC reminders |
| `deposit_initiated` / `deposit_completed` / `deposit_failed` | method (UPI/IMPS/crypto), amount_inr, asset, is_first, failure_reason | Verified→Funded, friction dormancy |
| `withdrawal_initiated` / `withdrawal_completed` | amount, asset, is_full_balance | churn risk, capitulation detection |
| `watchlist_added` / `watchlist_removed` | symbol, asset_class, venue | mover hooks, alerts adoption |
| `price_alert_set` / `price_alert_triggered` | symbol, direction, threshold_pct | alert habit loops |
| `market_viewed` | symbol, asset_class (crypto/equity/index/commodity/fx), venue (binance/hyperliquid:<dex>), product (spot/perp), duration_s | intent segments (perp intent without trade) |

## Trading (spot and perps share one schema; `product` distinguishes)
| event | key attributes |
|---|---|
| `order_placed` | product (spot/perp), symbol, side, order_type, size_usd, leverage, margin_mode (isolated/cross), reduce_only, venue |
| `order_filled` | product, symbol, side, size_usd, price, fee_usd, leverage, is_first_ever, is_first_perp, venue, dex |
| `order_cancelled` | product, symbol, reason |
| `position_opened` / `position_closed` | symbol, side, size_usd, leverage, margin_mode, realised_pnl_usd (on close), holding_minutes, close_reason (manual/sl/tp/liquidation) |
| `position_liquidated` | symbol, side, size_usd, leverage, loss_usd, margin_mode, venue |
| `funding_paid` / `funding_received` | symbol, amount_usd, rate_1h, position_side (aggregate hourly → daily server-side) |
| `stop_loss_set` / `take_profit_set` | symbol, distance_pct |
| `margin_added` | symbol, amount_usd, margin_ratio_before |
| `fee_tier_changed` | old_tier, new_tier, volume_30d_usd |

## Product & engagement
`app_opened` (MoEngage default), `perps_tab_viewed`, `hedge_calculator_used`, `education_module_completed {module}`, `competition_joined {competition_id}`, `referral_sent` / `referral_converted`, `support_ticket_opened {category}` / `support_ticket_resolved`, `notification_disabled {channel}`, `earn_subscribed {product, apr}`.

## Business events (server → MoEngage `POST /v5/business-events/triggers`, ≤200/day)
`market_regime_changed {regime}`, `new_listing {symbol, product, venue}`, `funding_extreme {symbol, apr}`, `oi_surge {symbol, pct_24h}`, `macro_event_soon {name, at}`, `platform_incident {status}`. These drive Business-Event-triggered campaigns to *segments*, not to one user.

## User attributes (server-computed nightly; the segments in `crypto-derivatives-marketing` depend on them)
`country`, `state`, `kyc_level`, `first_deposit_at`, `last_trade_at`, `spot_fills_30d`, `perp_fills_30d`, `median_leverage_30d`, `max_leverage_30d`, `liquidations_30d`, `last_liquidation_at`, `realised_pnl_30d_pct` (vs deposits), `held_symbols` (array), `watchlist` (array), `dexes_traded` (array), `fee_tier`, `volume_30d_usd`, `trader_state` (spot_only / first_perp / leverage_climber / habitual_perp / hedger / liquidated / loss_dormant / tokenised_explorer), `preferred_channel`, `dnd_window`, `marketing_consent_{push,email,sms,whatsapp}`, `risk_profile` (self-declared), `age_verified`.
PII (email, phone, name) stays in standard MoEngage fields marked PII; never copy it into custom attributes.

## Naming and hygiene rules
- One verb tense (past), one case (snake), no channel in the event name, no PII in attributes, numeric attributes as numbers (not strings), money in USD with `_usd`/`_inr` suffix, symbols upper-case base ticker (`BTC`, `NVDA`), `venue` = `binance` | `hyperliquid:<dex>`.
- Every event has `ts` set by the server for server events; SDK events use device time.
- Version breaking changes with a new name (`order_filled_v2`) and dual-write for 30 days.
- Review monthly: events with zero campaigns/segments/analyses attached are candidates for removal.

## What each unlock looks like in MoEngage
- **Funnel** deposit → first trade → second trade (`analytics_query kind=funnels` with `deposit_completed` → `order_filled{is_first_ever}` → `order_filled` count ≥2).
- **Retention** first perp fill → any perp fill (weekly, 8 weeks), split by `median_leverage_30d` band.
- **Trigger** `position_liquidated` → recovery flow (24h wait, then email); `price_alert_triggered` → push with `{{EventAttribute['symbol']}}`.
- **Segment** `perp intent, no trade`: `market_viewed{product=perp}` ≥2 in 14d AND `order_filled{product=perp}` = 0.
- **Suppression** cohort sync daily of `liquidated_14d`, `loss_dormant`, `kyc_pending`, `open_ticket`, `uk_us_resident` into custom segments used as exclusions in every campaign.
