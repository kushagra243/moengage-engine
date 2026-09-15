"""
Market Alerts 2.0 — automated push market alerts per the BRD (Relevance P1 · Discovery P2 · Moments of Truth).

rules.py       every BRD number and order in one place (overridable with the ma2_config_json setting), copy templates, copy lint
signals.py     token-level signals from public market data: Z-score moves, 1-year ATH/ATL, round-number milestones, most traded, whale feed
cohort.py      the pilot cohort file (opaque MoEngage customer ids + exposure), PII rejection, hashed ids
governance.py  pure per-user decision logic: candidates, priorities, 1+1 category caps, breach extras, Moments-of-Truth exemptions
service.py     runs (dry run / live), the hashed cap ledger, pilot approval, kill switch, MoEngage delivery, views for the terminal

Nothing here ever hands a user row to the model: tools and views return aggregates and hashed ids only.
"""
