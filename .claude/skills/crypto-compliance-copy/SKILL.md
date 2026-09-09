---
name: crypto-compliance-copy
description: Compliance rules for crypto and derivatives marketing copy by jurisdiction (India ASCI VDA guidelines, tax/TDS and FIU-IND context; UK FCA financial promotions and the retail crypto-derivatives ban; EU MiCA marketing rules; US geo-fencing), mandatory disclaimers with exact wording, banned phrases, leverage disclosures, and a pre-send checklist. Use before approving any campaign copy, especially market-linked, leverage or reward messaging. Verify current rules with counsel; this reflects rules as understood in 2026.
---

# Compliance for crypto & derivatives copy

Not legal advice: an operating checklist so that nothing obviously non-compliant reaches a send. When a rule is uncertain, `campaign_brief_check` should fail closed and the brief should say "counsel review".

## India (primary market, DC-03)
**ASCI Guidelines for Virtual Digital Assets (in force since 1 April 2022)** — apply to every ad/promotion, including push, email, WhatsApp, in-app, social and influencer content:
- Mandatory disclaimer, verbatim: **"Crypto products and NFTs are unregulated and can be highly risky. There may be no regulatory recourse for any loss from such transactions."**
  - Print/static: prominent, ≥ 1/5 of the ad space. Video: at the end, ≥ 5 s, voiced and on screen. Audio: at the end, at normal speed. Formats too small for it (a push notification) should not be used for *product promotion* — use the landing screen/in-app to carry it, and keep the push factual and non-promotional.
- Do not use the words **"currency", "securities", "custodian", "depositories"** for VDA products.
- No comparison of VDA with any regulated asset class; no "past performance implies future"; profit/cost claims must be complete and not misleading; no minors; celebrities/influencers must do due diligence.
**Tax context** (Finance Act 2022): 30% tax on VDA gains (s.115BBH), no loss set-off across assets, 1% TDS on transfers (s.194S). Copy may inform ("1% TDS is deducted on sells") and must never imply tax avoidance.
**PMLA / FIU-IND**: VDA service providers must be registered reporting entities (since March 2023). Ad platforms (Google, Meta) require proof of registration for crypto exchange ads in India — keep the certificate reference in the brand kit.
**Derivatives to Indian retail**: perpetual futures on crypto/tokenised equities via an offshore venue are a regulatory grey area; the compliance posture must be set by counsel. Until it is: derivatives content to Indian users is **education only** (what leverage/funding/liquidation are, risk tools), never acquisition, never "trade X perps now", never leverage figures as a lure. `ANGLE_POLICY` blocks `leverage_upsell`, `first_futures_trade`, `size_up` in every stressed regime; treat them as blocked for India-resident cohorts in all regimes unless counsel clears.
**Other India rules**: SMS via DLT-registered templates only, promotional SMS 10:00–21:00; WhatsApp only to opted-in users with approved templates; DND respected; TRAI consent for calls.

## United Kingdom (if any UK-resident users)
- FCA financial promotion regime for cryptoassets (PS23/6, live since 8 Oct 2023): promotions must be approved by an authorised person or made by a registered firm; mandatory risk warning, verbatim: **"Don't invest unless you're prepared to lose all the money you invest. This is a high-risk investment and you should not expect to be protected if something goes wrong. Take 2 mins to learn more."**; **incentives to invest are banned** (refer-a-friend bonuses, sign-up bonuses, fee-free promos tied to investing); **24-hour cooling-off** for first-time investors before a direct-offer promotion; appropriateness assessment before DOFPs; personalised risk warnings.
- **Crypto derivatives (incl. perps, futures, CFDs, ETNs) are banned for UK retail (since 6 Jan 2021)** — no derivatives marketing whatsoever to UK users. Geo-suppress.

## European Union
- MiCA (CASP obligations since 30 Dec 2024): marketing communications must be clearly identifiable as marketing, fair, clear, not misleading, consistent with the white paper; no hidden fees; complaints handling. Crypto derivatives fall under MiFID II — retail marketing constraints (ESMA leverage caps, risk warnings "x% of retail investor accounts lose money…") apply where they are offered.

## United States
- Do not market perps or unregistered derivatives to US persons; geo-fence and suppress US-located users from perp content entirely. Spot promotion is state-dependent (NY BitLicense). "Not available to US persons" where relevant.

## Everywhere: what copy may never do
- Name a liquidity venue or a competitor (Binance, Hyperliquid, Bybit, OKX, CoinSwitch, Zerodha…). We are CoinDCX; venue data informs, it is never cited to users. `campaign_brief_check` blocks these words.
- Forecast, imply returns, or tell a user to buy/sell/long/short a named asset. "Could rally", "don't miss", "moon", "last chance", "guaranteed", "risk-free", "safe", "passive income", "earn while you sleep" are banned words in any market or derivatives message.
- Present **funding, staking or earn rates as income/yield without risk context**; APRs need "variable" and "not guaranteed".
- Use leverage as a lure: "up to 50x" only in product documentation and market-screen UI, never in acquisition pushes or emails.
- Attach urgency to price. Countdown copy is allowed only for genuine product deadlines (competition end, listing time) and never for a price.
- Show P&L of other users, leaderboards of returns, or "top traders made X%" without the full risk disclaimer and equal prominence of losses.
- Send promotional content to users who unsubscribed, are on DND, liquidated in the last 14 days, or are in an open-ticket/dispute state.

## Derivatives risk disclosure block (append to any derivatives education content; landing screen for push)
"Perpetual futures are leveraged products. You can lose your entire margin, and positions may be liquidated automatically. Funding payments apply while a position is open. Only trade with money you can afford to lose. Crypto products and NFTs are unregulated and can be highly risky. There may be no regulatory recourse for any loss from such transactions."

## Pre-send checklist (the agent runs this in `campaign_brief_check`; the human confirms)
1. Jurisdiction of the audience known (attribute `country`, `state` for US); UK/US derivatives suppression applied.
2. Disclaimer present where required (email footer, WhatsApp template body, in-app card, landing screen); wording exact.
3. Banned-word scan clean; no price-tied urgency; no direction words on a named asset.
4. Numbers: verifiable in-app, time-stamped, TTL ≤ 4h for market facts.
5. Exclusions applied: liquidated 14d, loss-dormant, KYC pending, open ticket, unsubscribed/DND, minors (age attribute).
6. Incentive check: referral/bonus copy not sent to UK users; India bonus copy compliant with terms (no "guaranteed").
7. Channel compliance: DLT template id (SMS), approved WhatsApp template, email unsubscribe link, push not used for product promotion of VDA to India users where the disclaimer cannot be shown.
8. Record the brief; note "counsel review" if any item is uncertain.
