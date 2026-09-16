"""
The launch brief for the discovery alerts experiment — everything a reviewer needs before anything runs.

Nothing here is a summary of a summary: the copy is the exact template plus a rendered sample, the thresholds are the
live config values, the MoEngage draft is the exact body that will be posted, and "what would fire now" is a real dry run.
The same brief travels with both approval proposals, so the approver on the Ideas board reads what the launcher read.
"""
from __future__ import annotations
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

IST = timezone(timedelta(hours=5, minutes=30))

TEMPLATE_KEYS_BY_SIGNAL = {
    "large_trades": ["whale:buy", "whale:sell", "burst:up", "burst:down"],
    "most_traded": ["most_traded:any"],
    "milestone": ["milestone:up", "milestone:down"],
    "btc_move": ["market_move:up", "market_move:down"],
    "ath_atl": ["ath:up", "atl:down"],
    "agent_pick": [],                      # the agent writes these itself; every line is linted before it can send
}
SAMPLES = {
    "whale:buy": {"token": "ETH", "product": "futures", "size_usd": 3_800_000}, "whale:sell": {"token": "SOL", "product": "futures", "size_usd": 2_600_000},
    "burst:up": {"token": "ETH", "product": "futures", "size_usd": 4_200_000, "move": 1.2}, "burst:down": {"token": "BTC", "product": "futures", "size_usd": 6_100_000, "move": -0.9},
    "most_traded:any": {"token": "HYPE", "product": "futures"},
    "milestone:up": {"token": "BTC", "product": "futures", "price": 78_050, "level": 78_000}, "milestone:down": {"token": "ETH", "product": "futures", "price": 3_190, "level": 3_200},
    "market_move:up": {"token": "BTC", "product": "futures", "move": 3.4, "price": 78_050}, "market_move:down": {"token": "ETH", "product": "futures", "move": -4.1, "price": 3_020},
    "ath:up": {"token": "LIT", "product": "futures", "price": 4.09}, "atl:down": {"token": "XRP", "product": "futures", "price": 0.41},
}
WHEN = {
    "large_trades": "on detection (perishable): a closed 5-minute candle with notional ≥ {large_trade_min_usd} and ≥ {large_trade_vol_multiple}× its median volume with average trade size ≥ {large_trade_size_multiple}× its median; or a trade posted by the whale module",
    "most_traded": "once a day, released in the day's send window: highest 24h volume among futures tokens, BTC/ETH/SOL excluded",
    "milestone": "on detection (perishable): BTC crosses a $1,000 level or ETH a $200 level on the closed 5-minute price path; the same level not repeated for {milestone_level_cooldown_min} min",
    "btc_move": "on detection (perishable): a closed hourly BTC or ETH candle whose return is ≥ {z_threshold} standard deviations from its previous {z_window_h} hourly returns",
    "ath_atl": "once a day, released in the day's send window: the price reaches its highest or lowest level of the last {ath_window_days} days; at most {ath_cap_per_token_per_week} days per token per week",
    "agent_pick": "internal stage only: every {agent_every_min} min the agent reads the market picture and may add up to {agent_max_picks} alerts of its own (funding crowd, OI flush or build, listing, rotation, volume leader, level in play, macro, dominance); each must cite a number from the snapshot and pass the linter",
}


def build(days: int = 0, signals: Optional[List[str]] = None, audience: str = "", control_pct: int = 10, kpi: str = "sessions_per_week",
          with_dry_run: bool = True, cohort_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    from . import discovery, rules, timing
    from ..moengage.executors import v5_campaign_payload
    base_cfg = rules.config()
    audience = audience or str(base_cfg.get("internal_segment") or "INTERNAL_EMPLOYEES")      # the first experiment goes to employees, nowhere else
    stage0 = discovery.stage_of(audience, base_cfg)
    cfg = rules.config(stage=stage0)                                                          # the internal profile changes caps, lanes and quiet hours
    tpls = rules.templates()
    lint = {r["key"]: r for r in rules.lint_all(tpls)["templates"]}
    sigs = [s for s in (signals or [k for k in discovery.SIGNALS if k != "agent_pick"]) if s in discovery.SIGNALS and s != "agent_pick"]
    if cfg.get("agent_autonomy") and stage0 == "internal":
        sigs.append("agent_pick")
    continuous = int(days) <= 0
    now = datetime.now(IST)
    if stage0 == "internal":
        control_pct = 5                                                                       # employees should all see it; 5% is the smallest control the framework allows

    # 1. signals, with the exact numbers they fire on
    fmt = {**{k: v for k, v in cfg.items() if isinstance(v, (int, float, str))}, "z_window_h": (cfg.get("z_window") or {}).get("1h", 168),
           "large_trade_min_usd": f"${float(cfg['large_trade_min_usd']) / 1e6:.1f}M", "agent_every_min": cfg.get("agent_every_min", 15), "agent_max_picks": cfg.get("agent_max_picks", 3)}
    sig_rows = []
    for k in sigs:
        meta = discovery.SIGNALS[k]
        lane = "now" if k in (cfg.get("perishable_signals") or []) else "window"
        sig_rows.append({"signal": k, "label": meta["label"], "lane": lane, "when": WHEN[k].format(**fmt),
                         "daily_cap": (cfg.get("discovery_signal_caps") or {}).get(k, 1),
                         "freshness_min": ((cfg.get("perishable_max_age_min") or {}).get(k) or (cfg.get("queue_ttl_min") or {}).get(k) or 60) if lane == "now" else ((cfg.get("queue_ttl_min") or {}).get(k) or 720),
                         "source": meta["why"]})

    # 2. every push that can go out, exactly as it will read
    copy_rows = []
    for k in sigs:
        for key in TEMPLATE_KEYS_BY_SIGNAL[k]:
            t = tpls.get(key)
            if not t:
                continue
            sample = rules.render(key, SAMPLES.get(key, {}), t)
            l = lint.get(key) or {}
            copy_rows.append({"signal": k, "template": key, "title": t["title"], "body": t["body"], "sample_title": sample["title"], "sample_body": sample["body"],
                              "lint": "passes" if not l.get("findings") else "; ".join(f"{f['rule']} ({f['severity']})" for f in l["findings"]),
                              "blocks": bool(l.get("blocks")), "landing": "token page (token + product deep link)"})

    # 3. timing
    tv = timing.view(cfg, now)
    timing_rows = {"lanes": {"now": [s for s in sigs if s in (cfg.get("perishable_signals") or [])], "window": [s for s in sigs if s in (cfg.get("evergreen_signals") or [])]},
                   "windows": [{"id": w["id"], "ist": f"{w['start']}–{w['end']}", "why": w.get("why", ""), "days_with_stats": w.get("days_with_stats", 0), "ctr": w.get("ctr")} for w in tv["windows"]],
                   "today": f"{tv['today']['id']} {tv['today']['start']}–{tv['today']['end']} IST ({tv['today']['chosen_by']})", "best": tv["best"]["why"],
                   "quiet_hours": f"{cfg['quiet_start']}–{cfg['quiet_end']} IST: perishable facts are held and released at {cfg['quiet_end']} if still true",
                   "explore": f"{cfg.get('window_explore_pct')}% of days try a window other than the best so far; a window needs {cfg.get('window_min_days')} days of stats to be called best"}

    # 4. governance
    gov = {"profile": "internal (unhinged: higher caps, no window lane, agent picks on)" if cfg.get("stage_profile") == "internal" else "standard",
           "platform_daily_cap": cfg.get("discovery_daily_cap"), "per_signal_daily_caps": cfg.get("discovery_signal_caps"),
           "per_user_cap": "held by MoEngage frequency capping (the engine sends no user ids, so it caps per signal and per day)",
           "stress_pause": "in capitulation or high-volatility-down regimes nothing discovery-related sends; queued items expire",
           "one_per_key_per_day": "the same signal, token and direction never repeats within a day",
           "never_late": "a fact older than its freshness bound at detection is recorded as stale and not announced",
           "kill_switch": "Engine → Market Alerts → kill switch stops every send at once"}

    # 5. audience, the cohort plan, and the exact MoEngage draft per cohort
    plan = discovery.cohorts(base_cfg)
    ids = [c for c in (cohort_ids or []) if any(x["id"] == c for x in plan)] or (["all" if stage0 == "all" else "internal"])
    chosen = [c for c in plan if c["id"] in ids]
    cohort_rows = []
    for c in chosen:
        cb = discovery.campaign_brief(c["segment"], int(c.get("control_pct") or control_pct), days or 28, kpi, continuous=continuous, cohort_id=c["id"])
        cp = {k: cb[k] for k in ("name", "channel", "target_segment", "variants", "schedule", "ttl_hours", "goal", "frequency_cap")}
        dl = discovery.internal_delivery() if c["id"] == "internal" else {"mode": "event"}
        cohort_rows.append({"id": c["id"], "label": c["label"], "segment": c["segment"], "event": c["event"], "control_pct": int(c.get("control_pct") or control_pct),
                            "signals": c["signals"], "why": c.get("why", ""), "campaign": cb["name"], "draft": v5_campaign_payload(cp), "stage": c["stage"],
                            "delivery": (f"MoEngage Inform, direct to {dl['users']} employee ids (no campaign)" if dl["mode"] == "inform" else f"business event {c['event']} → campaign {cb['name']}")})
    b = discovery.campaign_brief(audience, control_pct, days or 28, kpi, continuous=continuous, cohort_id=ids[0])
    p = {k: b[k] for k in ("name", "channel", "target_segment", "variants", "schedule", "ttl_hours", "goal", "frequency_cap")}
    draft = v5_campaign_payload(p)
    stage = b.get("stage", "internal")
    aud = {"stage": stage, "segment": b["target_segment"] + (" (every push-enabled user)" if stage == "all" else " (internal employee test cohort)" if stage == "internal" else ""),
           "promotion": discovery.promotion() if stage != "all" else None,
           "exclusions": b["exclusions"], "control_group_pct": control_pct, "platforms": draft["basic_details"].get("platforms"),
           "note": "exclusions are set on the segment or the campaign in MoEngage; the engine cannot see users"}

    # 6. experiment design
    g = b["goal"]
    exp = {"name": b["name"], "type": "continuous, no end date, permanent control group" if continuous else f"{days} days, then read out",
           "hypothesis": g["hypothesis"], "primary_kpi": g["primary_kpi"], "target": g["target"], "guardrail": g["guardrail_metric"],
           "control_group_pct": g["control_group_pct"], "measurement_window_days": g["measurement_window_days"], "kill_criteria": g["kill_criteria"],
           "how_it_is_read": "lift = treated vs the campaign control group on the KPI; the engine's send-window learning uses the campaign's daily click rate; "
                             "MoEngage campaign stats do not expose control figures over the API, so the lift line needs the weekly aggregate export or the dashboard",
           "signals_in_scope": sigs}

    out = {"generated_at": now.isoformat(), "title": f"Discovery alerts — launch brief · {b['name']}",
           "summary": {"what": "Market-level push alerts that need no personal data: large trades, the day's most traded token, BTC/ETH round-number milestones, unusual major moves, 1-year highs and lows.",
                       "who": aud["segment"], "stage": ("STAGE 1 · internal employees only. The whole base is a later promotion, gated on this stage having run." if stage == "internal"
                                                           else "STAGE 2 · whole base" if stage == "all" else f"named segment {b['target_segment']}"), "when": (f"every alert on detection, outside quiet hours {cfg['quiet_start']}–{cfg['quiet_end']} IST (internal profile: no waiting for a send window)" if not timing_rows["lanes"]["window"] else f"perishable facts on detection outside quiet hours {cfg['quiet_start']}–{cfg['quiet_end']} IST; evergreen facts in the day's learned send window"),
                       "how": "; ".join(dict.fromkeys(c["delivery"] for c in cohort_rows)) + " — MoEngage picks the audience and holds the per-user cap",
                       "measured": f"{g['primary_kpi']} vs a {control_pct}% control group" + (", permanently" if continuous else f", read after {days} days")},
           "signals": sig_rows, "copy": copy_rows, "timing": timing_rows, "governance": gov, "audience": aud, "experiment": exp, "cohorts": cohort_rows,
           "keepalive": discovery.keepalive(),
           "moengage_draft": draft, "moengage_setup": discovery.status().get("setup"), "preflight": discovery.preflight(),
           "assumptions": rules.ASSUMPTIONS, "copy_blocking": [r["template"] for r in copy_rows if r["blocks"]]}
    if with_dry_run:
        try:
            r = discovery.run("dry_run", actor="brief")
            s = r.get("summary") or {}
            out["would_fire_now"] = {"detected": r.get("detected"), "would_send": r.get("would_send"), "would_queue": r.get("queued"), "held": r.get("held_quiet_hours"),
                                     "suppressed": r.get("suppressed"), "reasons": s.get("suppression_reasons"), "regime": s.get("regime"),
                                     "decisions": [{k: d.get(k) for k in ("signal", "token", "direction", "decision", "reason", "title", "body", "window", "due_at", "cohorts")} for d in (s.get("decisions") or [])[:12]]}
        except Exception as e:
            out["would_fire_now"] = {"error": str(e)[:200]}
    return out


def markdown(b: Dict[str, Any]) -> str:
    L: List[str] = [f"# {b['title']}", f"_generated {b['generated_at'][:16]} IST · MoEngage Engine_", ""]
    s = b["summary"]
    L += ["## In one paragraph", f"**Stage.** {s['stage']}", f"**What.** {s['what']}", f"**Who.** {s['who']}", f"**When.** {s['when']}", f"**How.** {s['how']}", f"**Measured.** {s['measured']}", ""]
    L += ["## Signals and exactly when they fire", "| signal | lane | fires when | cap/day | fresh for |", "|---|---|---|---|---|"]
    for r in b["signals"]:
        L.append(f"| {r['label']} | {r['lane']} | {r['when']} | {r['daily_cap']} | {r['freshness_min']} min |")
    L += ["", "## Every push, word for word", "Placeholders are filled by the engine from the market fact. Each row shows the template and a rendered sample.", ""]
    for r in b["copy"]:
        L += [f"### {r['template']}  ·  {r['signal']}", f"- **Title:** {r['title']}", f"- **Body:** {r['body']}", f"- **Sample:** **{r['sample_title']}** — {r['sample_body']}",
              f"- **Compliance:** {r['lint']}", f"- **Landing:** {r['landing']}", ""]
    t = b["timing"]
    L += ["## When it goes out", f"- Now lane (on detection, 08:00–22:00 IST): {', '.join(t['lanes']['now']) or '—'}",
          f"- Window lane (queued for the day's window): {', '.join(t['lanes']['window']) or '—'}", f"- Today's window: {t['today']}", f"- Learning: {t['best']}. {t['explore']}", f"- {t['quiet_hours']}", "",
          "| window | IST | why | days with stats | click % |", "|---|---|---|---|---|"]
    for w in t["windows"]:
        L.append(f"| {w['id']} | {w['ist']} | {w['why']} | {w['days_with_stats']} | {w['ctr'] if w['ctr'] is not None else '—'} |")
    g = b["governance"]
    L += ["", "## Caps and safety", f"- Profile: {g['profile']}", f"- Platform cap: {g['platform_daily_cap']} alerts per day; per signal: {json.dumps(g['per_signal_daily_caps'])}",
          f"- Per user: {g['per_user_cap']}", f"- {g['one_per_key_per_day']}", f"- {g['never_late']}", f"- Stress: {g['stress_pause']}", f"- {g['kill_switch']}", ""]
    a = b["audience"]
    L += ["## Audience", f"- Stage: {a['stage']}", f"- Segment: {a['segment']}"] + ([f"- Promotion to all users: {a['promotion']['why']}"] if a.get("promotion") else []) + [ f"- Platforms: {', '.join(a['platforms'] or [])}", f"- Control group: {a['control_group_pct']}%",
          f"- Exclusions: {'; '.join(a['exclusions'])}", f"- {a['note']}", ""]
    if b.get("cohorts"):
        L += ["## One MoEngage campaign per cohort (MoEngage delivers; the engine only emits the event)", "| cohort | segment | delivery | control | signals it hears | why |", "|---|---|---|---|---|---|"]
        for c in b["cohorts"]:
            L.append(f"| {c['label']} | {c['segment']} | {c['delivery']} | {c['control_pct']}% | {', '.join(c['signals'])} | {c['why']} |")
        L.append("")
    k = b.get("keepalive") or {}
    if k:
        L += ["## How it stays alive", "MoEngage owns: " + "; ".join(k.get("moengage_owns", [])), "The engine is still needed for: " + "; ".join(k.get("engine_still_needed_for", [])),
              "The engine is kept up by: " + "; ".join(k.get("engine_kept_up_by", [])), f"If the Mac is off: {k.get('if_the_mac_is_off', '')}", ""]
    e = b["experiment"]
    L += ["## Experiment design", f"- Campaign: {e['name']} — {e['type']}", f"- Hypothesis: {e['hypothesis']}", f"- Primary KPI: {e['primary_kpi']} · target: {e['target']} · guardrail: {e['guardrail']}",
          f"- Control group: {e['control_group_pct']}% · window: {e['measurement_window_days']} days", "- Kill criteria: " + "; ".join(e["kill_criteria"]), f"- How it is read: {e['how_it_is_read']}", ""]
    w = b.get("would_fire_now") or {}
    if w:
        L += ["## What would fire right now (dry run, nothing sent)"]
        if w.get("error"):
            L.append(f"- dry run unavailable: {w['error']}")
        else:
            L += [f"- detected {w.get('detected')} · would send {w.get('would_send')} · would queue {w.get('would_queue')} · held for quiet hours {w.get('held')} · suppressed {w.get('suppressed')} ({json.dumps(w.get('reasons') or {})}) · regime {w.get('regime')}"]
            for d in w.get("decisions") or []:
                L.append(f"- {d['signal']} {d['token']} {d['direction']} → **{d['decision']}**{(' (' + d['reason'] + ')') if d.get('reason') else ''}: {d['title']} — {d['body']}")
        L.append("")
    L += ["## Before it can reach MoEngage"]
    for c in b["preflight"]:
        mark = "OK" if c["ok"] else ("?" if c["ok"] is None else "BLOCKED")
        L.append(f"- [{mark}] {c['check']}: {c['detail']}" + (f" → {c['fix']}" if c.get("fix") else ""))
    L += ["", "## MoEngage setup (one time, in the dashboard)"]
    for i, st in enumerate(b.get("moengage_setup") or [], 1):
        L.append(f"{i}. **{st['step']}** — {st['detail']}")
    L += ["", "## The exact draft(s) the engine will create in MoEngage"]
    for c in (b.get("cohorts") or [{"label": "campaign", "draft": b["moengage_draft"]}]):
        L += [f"### {c['label']}", "```json", json.dumps(c["draft"], indent=1), "```", ""]
    L += ["## Assumptions where the BRD is silent"]
    for a_ in b["assumptions"]:
        L.append(f"- **{a_['topic']}** — {a_['choice']} _({a_['why']})_")
    L.append("")
    return "\n".join(L)
