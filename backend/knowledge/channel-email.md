# Email

## Where the performance actually comes from

Deliverability first, copy second. A brilliant email in the promotions tab loses
to a plain one in the primary inbox.

* **Sender reputation** is earned by sending to people who engage. Sending to a
  90-day-inactive list to "reactivate" is the fastest way to damage it. Segment
  out chronic non-openers and either sunset them or use a separate IP/subdomain.
* **Authentication**: SPF, DKIM and DMARC aligned on the sending domain. Without
  DMARC alignment, bulk senders are increasingly rejected outright by Gmail and
  Yahoo.
* **List hygiene**: hard bounces suppressed immediately, soft bounces after a
  threshold, unsubscribes honoured instantly and globally.
* **One-click unsubscribe** in the header is now effectively mandatory for bulk
  senders. Make it easy on purpose; a spam complaint costs far more than an
  unsubscribe.

## Structure

* **Subject line**: 35 to 50 characters. Mobile clients truncate around 40. Put the
  distinctive word first, not the brand name.
* **Preheader**: treat it as a second subject line, not a repeat. It is the highest
  leverage 60 characters in the whole email and most senders waste it on "View in
  browser".
* **One message per email.** The second call to action reduces clicks on the first.
* **Above the fold**: the point, and the button. Everything else is optional
  reading.
* **Plain text alternative** always. Some of your best users read it.
* **Dark mode**: test it. Transparent-background logos and hardcoded dark text are
  the two things that break.

## Email in a fintech context

* Regulatory and account emails must never be mixed with marketing in the same
  send or the same subscription category. Keep transactional on a separate stream
  with its own template, sender and suppression rules.
* Statements, tax documents and settlement notices belong in email, not push. Email
  is your channel of record.
* Long-form education, weekly market recaps and portfolio reviews outperform in
  email because there is room to be useful. This is where the market-data
  intelligence has the most space to be genuinely valuable rather than a hook.
* Include the risk disclosure in the footer of anything touching leveraged
  products, and in the body if the body discusses them.

## Cadence

* A weekly recap is a habit. A daily email is a tax on the reader's attention that
  most fintech brands cannot fund.
* Separate subscription categories (product updates, market recap, education,
  offers) and let people keep some while dropping others. Granular preferences
  reduce total unsubscribes substantially.
* Suppress marketing email for anyone who has an open support ticket or a failed
  withdrawal in flight.
