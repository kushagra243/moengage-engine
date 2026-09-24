---
name: atpu-portfolio-playbook
description: The growth programme this engine optimises — average trades per active user (ATPU) across the whole CoinDCX portfolio (spot, SIP, crypto perps, tokenised US stocks, indices and commodities, options, earn, web3) — using sentiment-timed nudges on every channel (push, in-app, email, WhatsApp, the Telegram community channel) to move users from one product to the next they have shown intent for. Mood reads, the nudge per product per mood, channel roles and limits, what is never said, and how it is measured. Use for any "what should we push this week", cross-sell, diversification, sentiment or multi-channel question.
---

# ATPU across the portfolio, timed by sentiment, on every channel

## The number
**North star:** average trades per active user per week, lifted versus a 20% holdout, across every product. **Second number:** portfolio breadth — products traded per user in 30 days. **Guardrail:** push opt-out and unsubscribe. One product trade more per user per week beats any single campaign's click rate.

## The mood read (`sentiment_pushes`, `/api/sentiment`)
Five labels from public data the engine already has: Fear & Greed, breadth of markets up, funding bias, regime, 24h market-cap change → *fearful · cautious · neutral · constructive · euphoric*, each with evidence lines. The mood chooses the **tone and the angle**, never the direction: fear → calm, tools, risk education, defined risk; euphoria → discipline, crowded-funding costs, breadth beyond crypto, protection; neutral → learn and set alerts. Stress regime pauses perps, options and web3 nudges (service tone only).

## The nudge per product (one per product per week, all channels combined)
| product | fear angle | euphoria angle | the tool |
|---|---|---|---|
| spot | calm, alerts | discipline, take-profit alerts | price alert |
| SIP | habit over timing | keep the plan, no lump sums | SIP schedule |
| crypto perps | risk first: margin, liquidation | crowded funding costs | liquidation calculator, funding tracker |
| tokenised US stocks | a different rhythm, 24/7 | breadth beyond crypto | tokenised markets explainer |
| indices & commodities | macro hedge education | diversification | macro calendar |
| options | defined risk | protection | options explainer |
| earn | park while you wait | idle gains can earn | earn terms |
| web3 | safety, verify contracts | trending is unverified | token checker |

Cross-sell is **only on shown intent** (viewed, watchlisted, asked) — the next product is the one the user looked at, never a random one. Geo: perps not to UK residents and US persons geo-fenced; options not to UK or US; India education-only for derivatives with the ASCI VDA disclaimer where the channel can carry it.

## Channel roles (the same nudge, five shapes)
- **Push** (API draft): the fact, ≤ 60 / 140 characters, one tool CTA. Quiet hours 22:00–08:00 IST.
- **In-app** (dashboard): the tool itself, on the next open.
- **Email** (API draft): the education, disclaimer in the footer.
- **WhatsApp** (Inform, utility templates only): alerts and summaries, 10:00–21:00 IST, DLT-registered; never promotional.
- **Telegram community channel** (`propose_telegram_post`, kind `telegram_post`): fact + why + tool for everyone, capped per day, approved like any send. The **team mirror** chat is a different chat and never a distribution channel.

Comms limits still bind across channels: ≤ 4 messages per user per week, stage overrides, regime multipliers (`flight-plans-and-guardrails`).

## Never
A direction ("BTC will…"), a price target, a venue or competitor name, a leverage multiple, a hypothetical return, an onboarding bonus, price urgency, a buy CTA on web3. The alert linter blocks all of it before a nudge is even shown.

## How it is measured
Per product programme: `primary_kpi trades_per_active_user`, `secondary products_traded_30d`, `guardrail push_opt_out_rate`, holdout 20%, read after 14 days, kill on opt-out > 0.5% or complaints > 0.1%. Readouts land in `our-learnings`; the mood at send time is recorded so lift can be read per mood.

## Data the programme still needs
Product-view and watchlist events per product, `products_traded_30d` as a user attribute, and a per-user trades-per-week attribute in MoEngage (`trading-event-taxonomy`; file with `request_data`). Until then breadth is read at cohort-family level (`sentiment.breadth()`).
