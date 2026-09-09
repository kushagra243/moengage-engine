<!-- vendored from https://www.moengage.com/docs/skill.md on 2026-09-09; regenerate with tools/build_api_catalog.py -->
---
name: Moengage
description: Use when building customer engagement campaigns, managing multi-channel messaging (push, email, SMS, WhatsApp, in-app), creating user segments, tracking events, analyzing user behavior, setting up automations with flows, or integrating MoEngage APIs into applications.
metadata:
    mintlify-proj: moengage
    version: "1.0"
---

# MoEngage Skill Reference

## Product Summary

MoEngage is an insights-led customer engagement platform that helps you understand audiences, engage them across multiple channels, and build lasting relationships from a single dashboard. Agents use MoEngage to create campaigns, manage user data, build segments, trigger automations, and analyze engagement metrics across push notifications, email, SMS, WhatsApp, in-app messages, web push, cards, and more.

**Key files and paths:**
- Dashboard: `https://dashboard-{dc}.moengage.com` (replace `{dc}` with your data center: 01, 02, 03, 04, 05, 06, or 101)
- API base URL: `https://api-{dc}.moengage.com`
- Settings > Account > APIs: Get Workspace ID and API keys
- Workspace ID and API keys required for all API calls (Basic Authentication)

**Primary docs:** https://moengage.com/docs

---

## When to Use

Reach for this skill when:

- **Building campaigns**: Creating push, email, SMS, WhatsApp, in-app, or web push campaigns
- **Managing user data**: Tracking events, updating user attributes, merging users, importing bulk data
- **Segmentation**: Building audience segments based on behavior, attributes, or affinity
- **Automation**: Setting up flows (cross-channel journeys) with triggers, conditions, and branching
- **Analytics**: Analyzing user behavior, funnels, retention, cohorts, or campaign performance
- **API integration**: Programmatically managing users, events, campaigns, segments, or templates
- **Transactional messaging**: Sending OTPs, order confirmations, or alerts via Inform
- **Personalization**: Delivering tailored web or in-app experiences
- **Testing**: Validating campaign content, testing integrations, or previewing personalization

---

## Quick Reference

### Data Centers and Base URLs

| Data Center | Dashboard URL | API Base URL |
|---|---|---|
| DC-01 (US) | `https://dashboard-01.moengage.com` | `https://api-01.moengage.com` |
| DC-02 (EU) | `https://dashboard-02.moengage.com` | `https://api-02.moengage.com` |
| DC-03 (India) | `https://dashboard-03.moengage.com` | `https://api-03.moengage.com` |
| DC-04 (US) | `https://dashboard-04.moengage.com` | `https://api-04.moengage.com` |
| DC-06 (Indonesia) | `https://dashboard-06.moengage.com` | `https://api-06.moengage.com` |

### API Authentication

All API requests require Basic Authentication:

```
Authorization: Basic {base64_encoded_workspace_id:api_key}
Content-Type: application/json
MOE-APPKEY: {workspace_id}  # Required only for File Import and Test Connection APIs
```

Generate credentials:
```bash
echo -n "YOUR_WORKSPACE_ID:YOUR_API_KEY" | base64
```

### Messaging Channels

| Channel | Best For | Key Features |
|---|---|---|
| **Mobile Push** | Immediate action, re-engagement | Android/iOS, rich templates, deep links |
| **Email** | Detailed content, nurturing | HTML editor, drag-drop, personalization |
| **SMS/MMS/RCS** | Urgent alerts, high open rates | Text + media, interactive elements |
| **WhatsApp** | Personal, trusted channel | Templates, 2-way conversations |
| **In-App** | Contextual, non-intrusive | Drag-drop editor, surveys, forms |
| **Web Push** | Website visitors | Desktop/mobile browsers, no email needed |
| **Cards** | User-initiated content | App inbox feed, evergreen content |
| **On-Site** | Lead capture, real-time | Pop-ups, surveys, exit-intent |

### Campaign Delivery Types

| Type | Trigger | Use Case |
|---|---|---|
| **One-Time** | Manual or scheduled | Flash sales, announcements, newsletters |
| **Periodic** | Recurring schedule | Weekly emails, monthly campaigns |
| **Event-Triggered** | User action | Cart abandonment, welcome series, post-purchase |
| **Business Event-Triggered** | Backend system event | Price drops, payment failures, stock alerts |
| **Device-Triggered** | Local device activity | Offline reminders, app open nudges |
| **Location-Triggered** | Geofence entry/exit | Store promotions, location-based offers |

### Essential API Endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| `POST` | `/customer/{workspace_id}` | Create/update user |
| `POST` | `/event/{workspace_id}` | Track user event |
| `POST` | `/customer/merge` | Merge two users |
| `POST` | `/customer/delete` | Delete users in bulk |
| `POST` | `/campaigns` | Create campaign (v5) |
| `PATCH` | `/campaigns/{campaign_id}` | Update campaign (v5) |
| `POST` | `/campaigns/search` | Search campaigns |
| `POST` | `/v3/custom-segments` | Create filter segment |
| `POST` | `/v2/custom-segments/file-segment` | Create file segment |
| `POST` | `/alerts/send` | Send transactional alert (Inform) |
| `POST` | `/transaction/sendpush` | Send push notification |

### Rate Limits

| Endpoint | Limit |
|---|---|
| Track User / Event | Per-workspace limit (check dashboard) |
| Send Transactional Alert | 10,000 requests/minute |
| Campaigns API | Per-workspace limit |
| Payload size (Track User, Merge) | 128 KB |
| Payload size (Catalog) | 5 MB |
| Payload size (Custom Segments - File) | 150 MB |

---

## Decision Guidance

### When to Use X vs Y

#### Campaign Type: One-Time vs Periodic vs Event-Triggered

| Scenario | Use One-Time | Use Periodic | Use Event-Triggered |
|---|---|---|---|
| Flash sale announcement | ✓ | | |
| Weekly newsletter | | ✓ | |
| Welcome email after signup | | | ✓ |
| Monthly re-engagement | | ✓ | |
| Cart abandonment reminder | | | ✓ |
| Scheduled broadcast | ✓ | | |

#### Channel Selection: Push vs Email vs SMS

| Goal | Push | Email | SMS |
|---|---|---|---|
| Immediate action | ✓ | | ✓ |
| Rich content | ✓ | ✓ | |
| High open rate | ✓ | | ✓ |
| Detailed messaging | | ✓ | |
| Cost-effective | | ✓ | ✓ |
| Personalization | ✓ | ✓ | ✓ |

#### Segmentation: Rule-Based vs File vs Affinity

| Use Case | Rule-Based | File-Based | Affinity |
|---|---|---|---|
| Behavioral targeting | ✓ | | |
| Bulk user import | | ✓ | |
| Similar user lookalike | | | ✓ |
| Dynamic segments | ✓ | | |
| One-time upload | | ✓ | |

#### Data Ingestion: SDK vs API vs File Import

| Scenario | SDK | API | File Import |
|---|---|---|---|
| Real-time app events | ✓ | ✓ | |
| Bulk historical data | | ✓ | ✓ |
| User attributes | ✓ | ✓ | ✓ |
| Server-side events | | ✓ | |
| Scheduled imports | | | ✓ |

---

## Workflow

### Typical Campaign Creation Workflow

1. **Understand the goal**
   - Define campaign objective (awareness, conversion, retention)
   - Identify target audience and channel
   - Determine delivery type (one-time, periodic, event-triggered)

2. **Check existing content**
   - Search for existing templates in Settings > Templates
   - Review past campaigns for similar messaging
   - Identify reusable content blocks

3. **Build the segment**
   - Navigate to Segment > Create Segment
   - Use rule-based filters (behavior, attributes, events) or file upload
   - Validate segment size and reachability
   - Test with a small sample first

4. **Create the campaign**
   - Go to Campaigns > Create Campaign
   - Select channel (Push, Email, SMS, etc.)
   - Choose delivery type
   - Set target segment
   - Add campaign content (subject, body, images, CTA)
   - Personalize with user attributes (e.g., `{{first_name}}`)
   - Configure scheduling or triggers

5. **Test before sending**
   - Use Test Campaign to preview on test device
   - Validate personalization with Personalized Preview
   - Check links, images, and formatting
   - Test on multiple devices/browsers if applicable

6. **Review and publish**
   - Confirm segment size and delivery settings
   - Set frequency capping if needed
   - Review campaign approval workflow (if enabled)
   - Publish or schedule campaign

7. **Monitor and analyze**
   - Track campaign stats (sent, delivered, opened, clicked)
   - Analyze performance in Campaign Analytics
   - Compare against control group (if enabled)
   - Iterate based on results

### Typical Flow (Automation) Workflow

1. **Define the journey**
   - Identify entry trigger (event, user attribute, segment)
   - Map out stages (wait, condition, action, branching)
   - Plan conversion goals

2. **Choose a template or start blank**
   - Select pre-built template (Onboard, Abandoned Cart, Reactivation)
   - Or create from scratch for custom logic

3. **Configure entry conditions**
   - Set trigger (event-based, segment-based, or manual)
   - Define audience filters
   - Set frequency capping and do-not-disturb rules

4. **Add stages and actions**
   - Add wait nodes (delay users)
   - Add condition nodes (branch based on user behavior)
   - Add campaign actions (push, email, SMS)
   - Personalize messages with user/event attributes

5. **Test the flow**
   - Use test mode to send to test users
   - Validate branching logic
   - Check personalization

6. **Publish and monitor**
   - Activate flow
   - Monitor user progression through stages
   - Analyze conversion metrics
   - Pause or edit if needed

---

## Common Gotchas

- **API key mismatch**: Ensure API key matches the feature (Data, Push, Campaign, Inform). Using wrong key returns 401 errors.
- **Data center mismatch**: Always use the correct data center URL. Data cannot be migrated between data centers after ingestion.
- **Rate limits exceeded**: Monitor `x-ratelimit-remaining` header. Implement exponential backoff for 429 responses.
- **Payload size limits**: Track User and Merge User endpoints have 128 KB limits. Compress or batch large payloads.
- **Personalization failures**: Users are dropped if personalization attributes are missing. Use fallback values or conditional logic.
- **Segment reachability**: Check push/email/SMS reachability before sending. Users without valid tokens/addresses won't receive messages.
- **Event naming**: Use consistent, snake_case event names. Inconsistent naming breaks segmentation and analytics.
- **Timezone issues**: Campaigns sent in "recipient's timezone" may delay delivery. Verify timezone settings in campaign scheduling.
- **Unsubscribe tracking**: Email campaigns require proper unsubscribe link configuration. Missing links violate compliance regulations.
- **Push token expiration**: Tokens expire over time. Implement token refresh logic in SDK integration.
- **Duplicate transaction IDs**: Inform API rejects duplicate transaction IDs within 5 minutes. Use unique IDs per request.
- **Template approval delays**: WhatsApp templates require approval before use. Plan ahead; approvals can take days.
- **Control group stickiness**: Control group membership is re-evaluated per campaign instance, not sticky across time.
- **Frequency capping limits**: FC values are capped at 7 days. Plan multi-week campaigns with periodic delivery instead.

---

## Verification Checklist

Before submitting campaign or flow work:

- [ ] **Segment validated**: Segment size is reasonable; reachability checked for target channel
- [ ] **Content reviewed**: No broken links, images load, personalization syntax correct (`{{attribute_name}}`)
- [ ] **Delivery settings correct**: Timezone, frequency capping, do-not-disturb rules configured
- [ ] **Test sent**: Campaign tested on test device or test user; personalization verified
- [ ] **Compliance checked**: Unsubscribe links present (email), opt-in verified (SMS/WhatsApp), GDPR/CCPA compliant
- [ ] **Analytics ready**: Conversion goals set; control group enabled if A/B testing
- [ ] **Approval workflow**: If enabled, campaign submitted for review; no pending rejections
- [ ] **Scheduling confirmed**: One-time campaigns have correct send time; periodic campaigns have correct recurrence
- [ ] **API calls validated**: Correct data center URL, authentication headers, payload format
- [ ] **Rate limits checked**: Batch size and request frequency within limits; monitoring headers in place

---

## Resources

**Comprehensive navigation:** https://moengage.com/docs/llms.txt

**Critical documentation pages:**
1. [API Introduction & Authentication](https://moengage.com/docs/api/introduction) — Data centers, base URLs, authentication, rate limits
2. [User Guide Introduction](https://moengage.com/docs/user-guide/introduction) — Platform overview, getting started, all features
3. [Campaigns & Channels Overview](https://moengage.com/docs/user-guide/campaigns-and-channels/getting-started/introduction/moengage-channels) — Channel capabilities, delivery types, use cases

---

> For additional documentation and navigation, see: https://moengage.com/docs/llms.txt