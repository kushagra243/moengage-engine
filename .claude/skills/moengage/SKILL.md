---
name: moengage
description: MoEngage product knowledge for building campaigns, segments, flows, analytics and integrations — channels, delivery types, data centres, auth, gotchas. Use when the task is about what MoEngage can do or how it is configured. Adapted from MoEngage's official agent skill (moengage.com/docs/skill.md).
---

# MoEngage (product skill)

Source of truth: MoEngage's own agent skill, vendored verbatim in [official-skill.md](official-skill.md) — read it for the channel matrix, delivery types, decision tables, campaign/flow workflows, gotchas and the verification checklist. This file adds what matters for *this* team (crypto/equities trading app, India DC-03, CLM operator use).

## Quick facts
- Dashboard `https://dashboard-{dc}.moengage.com`, API `https://api-{dc}.moengage.com`; DC-03 = India. Data never migrates between DCs.
- Auth for every public API: HTTP Basic `WorkspaceID:API-key`; some endpoints also need `MOE-APPKEY: WorkspaceID`. Keys are per feature (Data, Segmentation, Campaigns, Inform) — a wrong key returns 401.
- Rate limits are per workspace; `429` carries `x-ratelimit-*` headers. Track user / merge payloads ≤ 128 KB.
- Campaign stats API: ≤ 10 (docs say up to 50 on MCP) campaign ids per call, 30-day window, mandatory `request_id`, `start_date`, `end_date`, `attribution_type`, `metric_type`. Control-group figures are **not** exposed.
- Campaign creation via API covers Push and Email only; other channels are dashboard-only (or Inform for transactional).
- Business events: 200 triggers/day; Inform rejects duplicate transaction ids within 5 min; frequency caps max 7 days; control group is not sticky across instances.
- Personalisation drops users whose attributes are missing unless a fallback is set.

## How this engine uses MoEngage
- Reads: public APIs when keys are set (`Settings → Integration`), else dashboard session headers (learned/verified endpoints), else demo data (always labelled).
- Writes: only through the Approvals queue; drafts are created with v5 campaigns / v3 custom-segments / cohort sync.
- The full endpoint catalog is local: see the `moengage-api` skill.

## MCP servers (know they exist; decide deliberately)
- Docs MCP `https://moengage.com/docs/mcp` — documentation search only; safe to add to Claude Code (`.mcp.json` in this repo).
- Workspace MCP `https://mcp.moengage.com` — OAuth, runs in the AI vendor's cloud with workspace data. **Not** used by this engine (data must stay local); PII fields are masked there anyway.
