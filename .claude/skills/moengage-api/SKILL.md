---
name: moengage-api
description: The complete documented MoEngage public API surface (131 operations, 32 OpenAPI specs) and how this engine calls it — auth keys per spec, read-safe vs write, path templates, limits, request_id rules, and the local lookup tools. Use before writing any MoEngage API call or claiming an endpoint does not exist.
---

# MoEngage API (complete catalog, local)

Everything documented at moengage.com/docs/api is vendored under `backend/knowledge/moengage-api/`:
- `catalog.json` — 172 doc pages, 131 operations: method, path, server prefix, key kind, read-safe flag, rate limit, doc URL.
- `specs/*.json` — the 32 OpenAPI 3.0 specs (parameters, request/response schemas, examples).
- Rebuild: `.venv/bin/python tools/build_api_catalog.py` (public docs only; nothing from the workspace is sent).

## Look things up (no network)
```bash
./.venv/bin/python -c "from backend import api_catalog as c; import json; print(json.dumps(c.search('flow status'), indent=1))"
./.venv/bin/python -c "from backend import api_catalog as c; import json; print(json.dumps(c.operation_detail('PATCH','/v5/flows/{flow_id}/status'), indent=1))"
```
In the console/API: `GET /api/api-catalog?q=…` or `?method=POST&path=/v5/campaigns/search`. The in-app agent has `moengage_api_reference` and `moengage_api_read` (read-safe calls in live mode).

## Servers (prefix differs per spec — always take `server` from the catalog)
| spec | base | key kind |
|---|---|---|
| data, cards, catalog, coupons, recommendations, gdpr-ccpa, subscription-categories | `https://api-{dc}.moengage.com/v1` | data |
| business-events (legacy) | `…/v1.0` · v5: `…` root (`/v5/business-events*`) | data |
| custom-segments (`/v3/custom-segments`, `/v2/custom-segments/file-segment`), cohort-audience (`/v1/integrations/cohortsync`) | root | segmentation |
| campaigns v1 (`/core-services/v1/…`: campaigns/search, campaign-stats, campaigns/{id}, status), campaign-draft v5 (`/v5/campaigns*`), stats-report, flows (`/v5/flows*`), analytics (`/v5/analytics/dashboards*`), analytics-query (`/v5/analytics/{behavior|funnels|retention|session-source|user-analysis}` + `/query/{id}/status|results`) | root | campaigns |
| templates: email-templates-1 (`/v2/email-templates`), email-templates-2 / push / sms / in-app / osm (`/v1.0/custom-templates/{email|push|sms|inapp|osm}`), content-blocks (`/v1/external/campaigns/content-blocks`), content-apis (`/v5/content-apis`), offerings (`/v5/offers`), locales (`/v5/locales`) | see catalog | campaigns |
| inform (`/v1.0/alerts/send`, sandbox host available), live-activities (`/v1.0/live-activity/broadcast/*`) | | inform / data |
| message-archival | `https://archival-{dc}.moengage.com/v1` | campaigns |
| personalize-experience | `https://sdk-{dc}.moengage.com/v1` | data |

## Rules the engine enforces
- **Read-safe** = any GET, or POST whose path ends in search / meta / stats / get-by-ids / export / fetch / executions / status / history / usage-report / preview. Only these may be called directly (`PublicAPI.call_documented`). Everything else is a write → proposal → human approval → executor.
- `core-services` and `/v5/` POST bodies need a caller-supplied `request_id` (UUID); the client adds it.
- Campaign search: `page` + `limit ≤ 15`; stats: ≤ 10 ids per call, ≤ 30-day window, `attribution_type` + `metric_type` mandatory.
- 429 → honour `x-ratelimit-reset` / `retry-after` (client retries twice).
- Never log or echo `Authorization`; `redact()` strips tokens, cookies and keys from every log/prompt line.
- The dashboard SPA (`?api=1`, Bearer JWT + RefreshToken + MoeTraceId) is a separate, undocumented surface handled by `backend/moengage/session.py` + learned endpoints; prefer public APIs whenever a key exists.

## Adding a new public endpoint to the engine
1. Confirm it in the catalog (`operation_detail`).
2. Reads: call `PublicAPI.call_documented(method, path, path_vars=…, params=…, body=…)` or add a named entry to `backend/moengage/endpoints.default.json` under `public` (method, path, key, appkey_header, doc) and a thin wrapper in `public_api.py`.
3. Writes: add an executor in `backend/moengage/executors.py` (validate / preview / execute) and a `propose_*` tool; never call a write from a tool directly.
4. Test with `responses` mocks (see `tests/test_public_api.py` pattern) — no live calls in tests.
