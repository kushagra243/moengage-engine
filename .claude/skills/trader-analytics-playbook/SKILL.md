---
name: trader-analytics-playbook
description: The weekly and daily analysis routine for a trading-app CLM team using MoEngage analytics (v5 Analytics Query API for funnels, retention, behavior, user analysis; campaign stats; dashboards) plus this engine's snapshots — which questions to run, exact query shapes, how to read the outputs against regime and holdouts, and what action each finding triggers. Use when asked "how are we doing", for weekly reviews, or before proposing a programme change.
---

# Trader analytics playbook

Two data planes: **MoEngage analytics** (events/users the app sends; `analytics_query`, `get_analytics`, `moengage_api_read GET /v5/analytics/dashboards`) and **this engine** (daily campaign snapshots, anomalies, experiments, market snapshots). Always state which plane a number came from and its window.

## Daily (model-free; the scheduler does it, the agent reads it)
1. `anomaly_report` → act-today items; diagnose with `campaign_diagnosis` before acting.
2. `channel_kpis` coverage: stats present for N of M campaigns — if coverage < 80%, fix data before reading trends.
3. Market regime + hooks; suppression state if stressed.
4. Experiment readouts that crossed their window.

## Weekly review (Monday, IST morning) — the seven questions
| # | question | query | read it against | action if bad |
|---|---|---|---|---|
| 1 | Verified → Funded → First trade → Second trade | funnels: `kyc_approved` → `deposit_completed` → `order_filled{is_first_ever}` → `order_filled` (≥2), 7d conversion window, split by `utm_source` | last 4 weeks; regime | biggest drop step becomes the `close_the_gap` target |
| 2 | Perp adoption | funnels: `market_viewed{product=perp}` → `order_filled{is_first_perp}` → second perp fill | by `country`, leverage band | intent without trade ↑ → education flow, not promos |
| 3 | Retention of new traders | retention: first `order_filled` → any `order_filled`, weekly, 8 weeks, split by acquisition week | earlier cohorts | week-2 cliff → habit tools (alerts/watchlist) campaign |
| 4 | Liquidation → churn | retention: `position_liquidated` → `order_filled`, 30 days; behavior: `position_liquidated` count by leverage band | previous month | rising → recovery flow coverage + leverage education |
| 5 | Campaign effectiveness | `get_campaign_stats` per campaign; `campaign_taxonomy` groups; `experiment_readouts` | own history; peers only as Watch | rate down with delivery stable → creative test; delivery down → audience clean/pause |
| 6 | Suppression hygiene | behavior: campaign deliveries to users with `position_liquidated` in 14d (should be ~0); unsubscribes/notification_disabled by campaign | zero tolerance | fix exclusions before any new send |
| 7 | Market-linked send quality | this engine: hooks used, TTL respected, CTR by angle and regime | angle policy | angles with high CTR but rising unsubscribes get retired |

## Query shapes (v5 Analytics Query API; async: register → poll status → results)
The full schemas and complete examples are in `backend/knowledge/moengage-api/specs/analytics-query.json` (also `moengage_api_reference method=POST path=/v5/analytics/funnels`). Skeleton the agent fills:
```json
{"version":"2.0","type":"funnel",
 "events":[{"step_number":1,"step_type":"include","c_at_trigger_seg_v2":{"included_filters":{"filter_operator":"and","filters":[{"filter_type":"actions","action_name":"deposit_completed","executed":true,"execution":{"type":"atleast","count":1},"attributes":{"filter_operator":"and","filters":[]}}]}},"c_at_act_seg_v2":{"included_filters":{"filter_operator":"and","filters":[]}}},
           {"step_number":2,"step_type":"include","c_at_trigger_seg_v2":{"included_filters":{"filter_operator":"and","filters":[{"filter_type":"actions","action_name":"order_filled","executed":true,"execution":{"type":"atleast","count":1},"attributes":{"filter_operator":"and","filters":[{"name":"is_first_ever","operator":"is","value":true}]}}]}},"c_at_act_seg_v2":{"included_filters":{"filter_operator":"and","filters":[]}}}],
 "segmentation":[{"filters":{"included_filters":{"filter_operator":"and","filters":[{"id":"moe_all_users","name":"All Users","filter_type":"custom_segments"}]}}}],
 "timerange":{"start":"2026-08-01 00:00:00","end":"2026-08-31 23:59:59"},"conversion_window":{"value":7,"unit":"days"},"granularity":"d"}
```
Retention uses `events:[{"id":"first",...},{"id":"return",...}]`; behavior uses `analysis_type:"events"|"users"` with `split_by` on an attribute. Rate limits: ~5 req/s, 20/min, 50–350/h per type — batch the weekly set, cache results in the growth feed notes.

## Reading rules
- Compare a campaign to **its own** history first; peers are context. Day-of-week matters for trading apps (weekend volume differs); the anomaly module already uses DoW baselines.
- Any uplift claim needs a holdout; without one say "pre-period comparison, not incremental". The stats API does not expose control-group figures.
- Tag every reading with the regime; a 20% lift in a trending_up week may be underperformance.
- Denominators: rates on **delivered**, not sent; conversions within the campaign's attribution window only.
- Small n: use Wilson intervals (`experiments.wilson`); below ~30 conversions, say "directional".

## Output of the weekly review
A one-page brief: 3 numbers that moved, why (evidence + tool named), 3 actions as proposals (with briefs), risks/suppressions in force, and open experiments with days remaining. Record ideas with `record_ideas`.
