# WhatsApp, SMS, in-app and on-site

## WhatsApp

WhatsApp is the highest-open-rate channel you have and the easiest one to ruin.

* **Opt-in is mandatory and must be explicit.** Capture it with clear language,
  store when and where it was captured, and honour opt-out immediately. An
  inferred opt-in is a policy violation waiting to happen.
* **Template (HSM) approval.** Anything sent outside a 24-hour customer service
  window must use a pre-approved template. Approval takes time, so campaign
  templates need to exist before the campaign is planned, not after. Build a
  library of approved templates with variables, and design campaigns around what
  is already approved.
* **Categories matter commercially and legally.** Marketing, Utility and
  Authentication are priced and policed differently. A marketing message sent as a
  utility template is the most common cause of a quality-rating downgrade.
* **The 24-hour session window**: once a user messages you, you can reply freely
  for 24 hours. This is where conversational flows belong, not broadcast.
* **Quality rating** is per phone number and it degrades from blocks and reports,
  not from volume. One badly targeted marketing blast can throttle your messaging
  limits for weeks, including your utility messages. Treat WhatsApp marketing sends
  as high-blast-radius by default.
* **What suits WhatsApp**: time-sensitive account events, KYC and deposit
  completion, order and settlement confirmations, and genuinely wanted alerts a
  user configured. What does not: generic market commentary and promotional
  broadcasts.

## SMS in India

* **DLT registration is mandatory.** Sender ID (header) and every template must be
  registered on a DLT platform before sending. An unregistered template is not
  delivered, it is dropped, and you often will not get a useful error.
* **Template variables are constrained.** Most DLT templates allow a limited number
  of variable fields with length limits. Design the copy around the registered
  template, not the other way round.
* **Transactional versus promotional** routing determines whether the message
  reaches DND-registered numbers. Promotional SMS to a DND number is blocked.
  Never route marketing through a transactional header; that is the violation
  regulators actually act on.
* 160 characters for GSM-7. A single emoji or a curly quote flips the message to
  Unicode and drops the limit to 70, tripling your cost silently.
* SMS is your fallback channel for account-critical messages when push and email
  fail. Use it for OTP, security, settlement and failed-transaction alerts. Almost
  never for marketing.

## In-app and on-site

* In-app messages reach the user when they are already in context, which makes them
  the best channel for education, feature discovery and graduation nudges. The user
  is not being interrupted; they are being helped mid-task.
* **Trigger on the right event, not on app open.** An in-app on app open is a
  toll booth. An in-app when the user opens the futures screen for the third time
  without trading is a helpful moment.
* **Display frequency and priority**: set a per-user display cap and a priority
  order. Two in-apps competing on the same screen means one of them loses silently
  and your reporting will not tell you which.
* Do not put anything time-critical in-app. Delivery depends on the user opening
  the app, so latency is unbounded.
* **Cards / inbox** are for messages that should persist: statements, campaign
  recaps, announcements. Use them as the durable home for anything you also pushed,
  so the push can be short and the detail lives somewhere.

## Choosing a channel

| Need | Channel |
| --- | --- |
| Time-critical and account-related | Push, with SMS fallback |
| Time-critical and market-related | Push only, short TTL |
| Explanation, education, long-form | Email, or in-app if the user is already in context |
| Confirmation of something the user did | WhatsApp or email, as a utility message |
| Feature discovery and graduation | In-app, triggered on the relevant screen |
| Regulatory or record | Email, plus cards for persistence |

Never send the same campaign on three channels at once "for coverage". Pick the
channel the message belongs on, and use a second channel only as a timed fallback
for people the first one did not reach.
