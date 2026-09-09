---
name: cohort-studies
description: How to read MoEngage segments and the team's monthly cohort uploads — decoding the nomenclature (HVT_Sep26 = high-value traders, September version), families and versions, reach proxies, version-over-version deltas, migration analysis, RFM for traders, and the standard studies to run each month with the engine's segment_study tool and the analytics API. Use for any question about cohorts, segments, uploads or "who responds".
---

# Cohort studies

## The nomenclature (decoded by the engine; teach unknown codes)
Segment and campaign names are built from codes joined by `_`/spaces plus a period token: `HVT_Sep26`, `Dormant_D60_LowProp_Sep26`, `GMC_Sep'26_CS_Res_HighProp_HVS_8thSept`.
- Value tiers: **HVT / MVT / LVT** high / mid / low-value trader; **HVS / LVS / LIS** value-tier variants used in campaign names; **NVT** new trader; **WHALES**, **TOP100**.
- Cohort states: **FTD** first-time depositor, **NODEP**, **FTT** first-time trader, **NOTRADE**, **Dormant/D30/D60/D90**, **Active**, **Slipping**, **Liquidated**, **Re-KYC Pending/Approved**.
- Programme / lifecycle: **GMC** growth marketing calendar, **CS / RES / RET** cross-sell / resurrection / retention, **HighProp / LowProp**, trader type **HFT / BC / LIF**, product **Futures / Perps / Spot / TG / INJ / tokenised**.
- Period: `Sep26`, `Sep'26`, `4Sep26`, `2026-09` → version `2026-09`. The family is the name without the period.
Unknown codes are surfaced (Overview → Cohorts → "Undefined codes"). Define with `define_nomenclature('XQ', 'cohort:Experiment group Q')` or the Define box; the meaning is `facet:label` with facets value | cohort | trader | product | programme | propensity | risk | message | window.

## Facts about MoEngage segment data
- The segments API returns names, ids, type, source and created time — **not sizes**. Reach in the engine is an explicit reach when present, else the largest `sent` of a one-time campaign that targeted the segment (a proxy; say so).
- File segments (monthly uploads) are static; filter segments are live. A new month's upload is a new segment, so families accumulate versions — always compare version to version, never a family to itself across months.
- Segment membership by user is not exposed; migration analysis needs either the upload files or a `trader_state` user attribute (see `trading-event-taxonomy`).

## Monthly routine (first working day after the upload)
1. `segment_study(refresh=True)`: list new versions, reach vs previous, orphan families, undefined codes.
2. For each family with a new version: which standing campaigns targeted the old version → re-point (SOP `sop_monthly_cohort_upload`), then archive the old version in MoEngage.
3. Read version deltas: click-rate pp and reach change; flag `worse_than_previous_version` and `reach_shrank` families for `campaign_diagnosis`.
4. Run the studies below that the data supports; record findings with `record_ideas` (kind fix or trending_campaign) so they persist.

## Standard studies
| study | question | method | decision |
|---|---|---|---|
| Value-tier response | do HVT/MVT/LVT respond differently to the same programme | `campaign_taxonomy by=value`, `segment_study` performance, two-proportion test, regime tag | split creative/cadence by tier |
| Cohort baseline | how big/new is each upload vs last month | `segment_study` delta | re-point or ask why it changed > 40% |
| Orphans | cohorts nobody messages | flags `no_campaign_attached` + `clm_program_audit` | run the matching SOP or archive |
| Version decay | why this month's version underperforms | facets diff, `campaign_diagnosis`, regime | creative test before audience change |
| Migration matrix | HVT→HVT/MVT/LVT/dormant month over month | needs user ids or `trader_state`; analytics retention split by state | downgrade risk becomes the HVT KPI |
| RFM for traders | recency of trade × frequency × volume | attributes `last_trade_at`, `perp_fills_30d`/`spot_fills_30d`, `volume_30d_usd`; 3×3×3 grid | cadence and channel per cell; VIP cell gets least |
| Overlap audit | the same user in N campaigns this week | segment intersections (dashboard) or campaign sent counts vs family reach | global frequency cap; suppression lists |
| Liquidation→churn | do liquidated users churn more by leverage band | analytics retention `position_liquidated` → `order_filled` split by band | recovery SOP coverage |

## Reading rules
- Compare like with like: same channel, same regime tag, same version age.
- Reach proxies are lower bounds; performance rates are volume-weighted on delivered.
- A family's meaning is what its codes say; if a code is unknown, the study is on hold until it is defined — do not guess.
- Every study ends with one decision and, where warranted, one SOP run or one proposal.
