---
name: clm-operator
description: The CLM (customer lifecycle marketing) operating doctrine this team runs on a crypto/equities trading app — goal briefs, one-KPI-per-campaign, holdouts and measurement, lifecycle transitions, market-linked send policy, compliance copy rules, and the recommendation output format. Use when designing, reviewing or approving any campaign, segment, flow or experiment.
---

# CLM operator doctrine

## Programme before campaign
Map every campaign to a lifecycle transition (installed → verified → funded → first trade → repeat → habitual; slipping → dormant → reactivated). Close uncovered transitions first; cut broadcast share in favour of triggered sends. Tools: `clm_program_audit`, `campaign_taxonomy`.

## One goal per campaign (the brief)
`transition · hypothesis ("if we do X to WHO, KPI moves Y because MECHANISM") · primary_kpi (denominator + window) · target vs baseline · guardrail_metric · control_group_pct (≥5, 20 for new programmes) · measurement_window_days · kill_criteria · suppressions/exclusions · frequency cap · TTL for market-linked sends`. Validate with `campaign_brief_check`; size with `experiment_plan` (two-proportion test, 80% power). If the target is unmeasurable at the reach, redesign, do not ship.

## Diagnose before you message
`rule_based_audit` (thresholds) → `anomaly_report` (each campaign vs its own history; modified z / IQR, ≥21 days, day-of-week baselines) → `campaign_history` → `campaign_diagnosis` (funnel stage that moved, ranked causes, do-first). State n and days of history. Separate "different" from "bad". For dormant users, segment by cause (loss, friction, market, competitor).

## Segments and cohorts
Naming facets: programme (GMC / Re-KYC / Newsletter), cohort (CS cross-sell / RES resurrection / RET retention), propensity (HighProp / LowProp), value (HVS / LVS / LIS), trader type (HFT / BC / LIF), product (Futures / TG / INJ). Compare within facet (`campaign_taxonomy` comparisons). Best users hear from us least; loss-dormant and friction-dormant users are excluded from market and upsell sends; KYC-pending and open-ticket users are excluded from promotions.

## Market-linked sends
Only via `market_snapshot` / `market_news` / `market_campaign_hooks`; universe is strictly Binance USDT spot ∪ Hyperliquid perps (incl. builder-dex equities/indices/commodities). Angle policy is binding: blocked angles are refused. Copy never forecasts, never implies returns, never tells a user to buy/sell a named asset; every price fact is verifiable in-app and carries TTL ≤ 4h. Risk/regulatory headlines or capitulation regimes suppress acquisition/upsell sends (autopilot mission `protect`).

## Copy rules
Push title ≤ 60, body ≤ 140, one CTA, fact + tool, personalisation with fallbacks, required risk disclaimer for derivatives. Two variants per test. Never include credentials, internal ids or unverifiable numbers.

## Close the loop
Executed drafts become experiments (`experiment_readouts`): KPI vs pre-period with Wilson 95% intervals; the stats API exposes no control-group figures, so readouts are *not* incremental lift and must say so. Lessons flow into the growth feed; cite them before repeating a tactic. Record every idea with `record_ideas`.

## Output format for recommendations
Goal → Audience (criteria + exclusions + reach) → Channel & timing (IST) → Copy variants → Holdout & KPI & window → Suppressions & caps → Kill criteria → Proposal id.
