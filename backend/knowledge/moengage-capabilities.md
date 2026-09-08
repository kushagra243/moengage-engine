# MoEngage capabilities the agent should use, with their real limits

Sourced from MoEngage docs (moengage.com/docs) and API specs; numbers are the
documented limits as of 2026-09.

## Delivery controls
* **Frequency capping** — per user and platform, rolling window, per channel plus optional cross-channel total; resets at midnight in workspace (or user) timezone. Campaign toggles: *Ignore frequency capping* and *Count for frequency capping*. Alerts should Ignore FC but Count for FC so marketing volume shrinks automatically on volatile days.
* **Minimum delay between campaigns** — a triggered message inside the window is dropped unless *Message Queuing* is on for Flows. Turn queuing on for anything lifecycle-critical.
* **DND** — per-channel quiet hours; campaign-level *ignore DND* only for user-set alerts and security notices.
* **Best Time to Send (Merlin)** — per-user hour from the last 60 days of engagement, refreshed weekly; not applicable to triggered campaigns. Use for digests and coin-of-the-day; never for market-move alerts.

## Triggers
* **Event-triggered (Smart Trigger)** — IF user does event THEN send immediately or with delay; exclusion segments refresh about every 3 hours. Per-user market events (`asset_moved_5pct`, `price_target_hit`) tracked via the Data API are the scalable route for per-user alerts.
* **Real-time device triggers** — on-device, push only, for in-app actions; not for server-side price data.
* **Business Events** — regime-level occurrences (market stress, CPI print, ATH). Limits: 10 triggers per 5 min, 50 per hour, 200 per day; Flows 3/hour, 10/day. Debounce ≥ 30 min per event type. Never per-coin ticks.
* **Inform API** — transactional, multichannel with fallback; unique `transaction_id` (duplicates within 5 min rejected). Use for user-set price targets and safety notices.

## Content
* **Content APIs** — call an external JSON endpoint at send time, personalised params, 5 s max timeout, 3 retries; render live prices in email/push so numbers are true at delivery.
* **Personalisation** — `{{UserAttribute['First Name']|default('there')}}`; keep fallbacks on every token.
* **Push copy** — title ≤ 60 chars, body ≤ 140 chars for full display; one CTA; emoji at most one, never in title for finance.

## Segmentation
* Filter segments on user attributes, events (count, time window, attributes), affinity, RFM; custom segments via file (CSV URL) or cohort sync (public API, existing users only).
* Reach estimate is dashboard-only; capture it in a HAR to make the estimate tool live.
* Segment by cause, not duration; compare each user to their own trailing baseline.

## Campaign API path (v5)
* Create draft → PATCH content/audience/schedule → validate → send test → publish (v1 PATCH for now). Rate limits: 5 creates/s, 25/min, 100/hour; 100 successful creations/day.
* Status changes: pause/resume/stop via v5 status or v1 status endpoint. Flows: search/get/status only; creation is dashboard-only.

## Measurement
* Campaign stats API: ≤ 10 campaign ids per call, ≤ 30-day window. Snapshot daily for anomaly baselines.
* Control groups per campaign plus a global control group for programme-level attribution.
* Conversion goals with an attribution window that matches the behaviour (first trade: 7 days; deposit: 3 days; reactivation: 14 days).

## Compliance (India + UK templates)
* India (ASCI VDA): every crypto ad carries "Crypto products and NFTs are unregulated and can be highly risky. There may be no regulatory recourse for any loss from such transactions." No "currency" wording; no comparison to regulated products.
* UK (FCA PS23/6): 24-hour cooling-off for first-time investors before direct-offer promotions; no incentives ("refer a friend", joining bonuses); risk warning mandatory.
* Everywhere: no forecasts, no implied returns, no buy/sell instruction on a named asset; only registered advisers give recommendations.
