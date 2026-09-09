"""
Growth feed: a persistent, de-duplicated stream of things to try in MoEngage,
generated from data every run.

Two generators feed the same table:
  * rules  – deterministic ideas derived from live/mock data: uncovered lifecycle
             transitions, anomalies, rule-audit verdicts, market hooks, trending
             assets, macro calendar, channel-mix gaps, and a curated library of
             MoEngage capabilities ("trending activities") that surface when a
             data condition says they are relevant.
  * agent  – the LLM proposes new ideas as JSON given a data digest and the list
             of titles already in the feed (so it keeps finding new angles).

Every idea carries the data that justified it (`why`), the concrete MoEngage
steps (`how`), the goal fields the brief checker will demand, and a status the
user controls: new → saved | dismissed | proposed.
"""
from __future__ import annotations
import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .database import get_db
from .security import redact

KINDS = ("trending_campaign", "growth_hack", "market_play", "moengage_activity", "fix")
STATUSES = ("new", "saved", "dismissed", "proposed", "expired")
# how long an idea stays relevant unless the data keeps re-surfacing it (refresh extends expiry); saved/proposed never expire
TTL_HOURS = {"market_play": 6, "fix": 72, "trending_campaign": 24 * 14, "growth_hack": 24 * 21, "moengage_activity": 24 * 60}


def init_growth_tables() -> None:
    conn = get_db()
    conn.execute("""CREATE TABLE IF NOT EXISTS growth_ideas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        source TEXT NOT NULL,            -- rules | agent
        kind TEXT NOT NULL,
        title TEXT NOT NULL,
        title_key TEXT NOT NULL UNIQUE,  -- normalised for de-dupe
        why TEXT, how TEXT, segment TEXT, channel TEXT, angle TEXT, kpi TEXT,
        transition TEXT, effort TEXT, expected_impact TEXT, market_hook_id TEXT,
        priority INTEGER DEFAULT 50, status TEXT DEFAULT 'new',
        data_json TEXT, seen_count INTEGER DEFAULT 1, last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.commit(); conn.close()


def _ensure_columns(conn) -> None:
    try:
        conn.execute("ALTER TABLE growth_ideas ADD COLUMN expires_at TIMESTAMP")
        conn.commit()
    except Exception:
        pass


def _ttl_hours(i: Dict[str, Any]) -> int:
    kind = i.get("kind") or "growth_hack"
    data = i.get("data") or {}
    if data.get("ttl_hours"):
        return int(data["ttl_hours"])
    t = (i.get("title") or "").lower()
    if kind == "market_play" or i.get("market_hook_id") or "trending today" in t or "pre/post brief" in t:
        return TTL_HOURS["market_play"]
    if data.get("competitive") or t.startswith("compete") or "surge" in t:
        return 24
    if "listing" in t or "newly listed" in t:
        return 24 * 7
    return TTL_HOURS.get(kind, 24 * 21)


def _key(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()[:120]


def upsert_ideas(ideas: List[Dict[str, Any]], source: str) -> Dict[str, int]:
    init_growth_tables()
    conn = get_db()
    _ensure_columns(conn)
    added = refreshed = 0
    for i in ideas:
        title = (i.get("title") or "").strip()
        if not title:
            continue
        k = _key(title)
        ttl = _ttl_hours(i)
        row = conn.execute("SELECT id, status FROM growth_ideas WHERE title_key=?", (k,)).fetchone()
        if row:
            # the data surfaced it again → still relevant: extend expiry and revive if it had expired
            conn.execute("UPDATE growth_ideas SET seen_count=seen_count+1, last_seen=CURRENT_TIMESTAMP, why=COALESCE(?, why), priority=?, updated_at=CURRENT_TIMESTAMP, expires_at=datetime('now', ?), status=CASE WHEN status='expired' THEN 'new' ELSE status END WHERE id=?",
                         (i.get("why"), int(i.get("priority", 50)), f"+{ttl} hours", row["id"]))
            refreshed += 1
            continue
        conn.execute("""INSERT INTO growth_ideas (source, kind, title, title_key, why, how, segment, channel, angle, kpi, transition, effort, expected_impact, market_hook_id, priority, data_json)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                     (source, i.get("kind") if i.get("kind") in KINDS else "growth_hack", title[:160], k, i.get("why", "")[:1500], i.get("how", "")[:2000],
                      i.get("segment", "")[:300], i.get("channel", "")[:80], i.get("angle", "")[:80], i.get("kpi", "")[:120], i.get("transition", "")[:60],
                      i.get("effort", "medium"), i.get("expected_impact", "")[:300], i.get("market_hook_id"), int(i.get("priority", 50)),
                      json.dumps(i.get("data") or {}, default=str)[:4000]))
        conn.execute("UPDATE growth_ideas SET expires_at=datetime('now', ?) WHERE title_key=?", (f"+{ttl} hours", k))
        added += 1
    conn.commit(); conn.close()
    return {"added": added, "refreshed": refreshed}


def expire_stale(current_hook_ids: Optional[List[str]] = None, active_anomaly_campaigns: Optional[List[str]] = None, purge_after_days: int = 30) -> Dict[str, int]:
    """Retire ideas that stopped being relevant: past their TTL, tied to a market hook that no longer exists, or a fix for an anomaly that cleared.
    Saved / proposed ideas are kept. Expired rows older than purge_after_days are deleted so the feed stays honest and small."""
    init_growth_tables()
    conn = get_db(); _ensure_columns(conn)
    n_ttl = conn.execute("UPDATE growth_ideas SET status='expired', updated_at=CURRENT_TIMESTAMP WHERE status IN ('new') AND expires_at IS NOT NULL AND expires_at < datetime('now')").rowcount
    n_hook = 0
    if current_hook_ids is not None:
        rows = conn.execute("SELECT id, market_hook_id FROM growth_ideas WHERE status='new' AND market_hook_id IS NOT NULL AND market_hook_id <> ''").fetchall()
        live = set(current_hook_ids)
        for r in rows:
            if r["market_hook_id"] not in live:
                conn.execute("UPDATE growth_ideas SET status='expired', updated_at=CURRENT_TIMESTAMP WHERE id=?", (r["id"],)); n_hook += 1
    n_anom = 0
    if active_anomaly_campaigns is not None:
        act = {str(c).lower() for c in active_anomaly_campaigns}
        rows = conn.execute("SELECT id, title, data_json FROM growth_ideas WHERE status='new' AND kind='fix'").fetchall()
        for r in rows:
            try:
                d = json.loads(r["data_json"] or "{}")
            except Exception:
                d = {}
            camp = str(d.get("campaign_name") or d.get("campaign") or "").lower()
            if camp and camp not in act:
                conn.execute("UPDATE growth_ideas SET status='expired', updated_at=CURRENT_TIMESTAMP WHERE id=?", (r["id"],)); n_anom += 1
    n_purged = conn.execute("DELETE FROM growth_ideas WHERE status IN ('expired', 'dismissed') AND updated_at < datetime('now', ?)", (f"-{int(purge_after_days)} days",)).rowcount
    conn.commit(); conn.close()
    return {"expired_ttl": n_ttl, "expired_hook_gone": n_hook, "expired_anomaly_cleared": n_anom, "purged": n_purged}


def list_ideas(status: Optional[str] = "new", limit: int = 40, kind: Optional[str] = None) -> List[Dict[str, Any]]:
    """status=None returns everything except expired."""
    init_growth_tables()
    conn = get_db()
    q = "SELECT * FROM growth_ideas WHERE 1=1"; args: List[Any] = []
    if status:
        q += " AND status=?"; args.append(status)
    else:
        q += " AND status <> 'expired'"
    if kind:
        q += " AND kind=?"; args.append(kind)
    q += " ORDER BY priority DESC, last_seen DESC, id DESC LIMIT ?"; args.append(limit)
    rows = [dict(r) for r in conn.execute(q, args).fetchall()]
    conn.close()
    for r in rows:
        try:
            r["data"] = json.loads(r.pop("data_json") or "{}")
        except Exception:
            r["data"] = {}
    return rows


def set_status(idea_id: int, status: str) -> Optional[Dict[str, Any]]:
    if status not in STATUSES:
        raise ValueError("bad status")
    conn = get_db()
    conn.execute("UPDATE growth_ideas SET status=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (status, idea_id))
    conn.commit()
    r = conn.execute("SELECT * FROM growth_ideas WHERE id=?", (idea_id,)).fetchone()
    conn.close()
    return dict(r) if r else None


def counts() -> Dict[str, int]:
    init_growth_tables()
    conn = get_db()
    rows = conn.execute("SELECT status, COUNT(*) c FROM growth_ideas GROUP BY status").fetchall()
    conn.close()
    return {r["status"]: r["c"] for r in rows}


# ── curated "trending in MoEngage" activities, each with a relevance test ─────
def _activities(ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    channels = {str(c.get("channel", "")).lower() for c in ctx.get("campaigns", [])}
    regime = ctx.get("regime", "unknown")
    risk = ctx.get("risk_headlines", 0)
    n_flows = len(ctx.get("flows", []))
    out = []
    lib = [
        dict(title="Smart Trigger on per-user price-move events", kind="moengage_activity", when=True, priority=82,
             why="Per-user market relevance is the highest-CTR message class in trading apps (Robinhood-style 5%/10% alerts). MoEngage event-triggered campaigns fire within a minute of a Data-API event.",
             how="1) Emit `asset_moved_5pct {symbol,pct,direction}` per holder/watcher via Data API. 2) Campaign → Event-triggered → IF asset_moved_5pct THEN push immediately. 3) Ignore FC = on, Count for FC = on, TTL 4h, DND respected. 4) Copy = fact + tool, no forecast.",
             segment="Holders + watchlisters of the moved asset", channel="Push", angle="alerts_adoption", kpi="alert_adoption_rate", transition="activated_habitual", effort="medium", expected_impact="CTR 3–5× broadcast; alert adoption +20–30%"),
        dict(title="Content API for live prices at send time", kind="moengage_activity", when=True, priority=76,
             why="Market facts go stale within minutes; MoEngage Content APIs pull JSON at send time (5 s timeout, 3 retries). Azimo saw +24% CTR from live rates in messages.",
             how="1) Serve `/content/quote?symbols=` from this engine (cached). 2) Settings → Content APIs → register it. 3) In templates: `{% set q = ContentApi.quote({\"params\":{\"symbols\":\"BTC,ETH\"}}) %} {{ q.BTC.chg_24h }}`. 4) Add a fallback string for timeouts.",
             segment="Any market-referencing campaign", channel="Email + Push", angle="product_education", kpi="ctr", transition="activated_habitual", effort="medium", expected_impact="+15–25% CTR on market-linked sends"),
        dict(title="Best Time to Send for the weekly recap", kind="moengage_activity", when="email" in channels or True, priority=60,
             why="BTS picks each user's best hour from 60 days of engagement (weekly refresh). It is not applicable to triggered sends, so use it for digests.",
             how="Campaign → Send time → Best time to send (fallback: Sunday 19:00 IST). Pair with a 10% holdout at fixed time to measure the lift.",
             segment="Habitual + Core", channel="Email", angle="portfolio_review", kpi="open_rate", transition="habitual_core", effort="low", expected_impact="+8–12% opens vs fixed slot"),
        dict(title="Global control group + per-campaign holdouts", kind="moengage_activity", when=True, priority=70,
             why="Without a holdout every uplift is the market's uplift. MoEngage supports a global control group plus per-campaign control groups.",
             how="Settings → Global control group 5%. Every campaign brief: control_group_pct ≥ 5 (20 for new programmes). Read results with Wilson intervals (experiment_plan tool).",
             segment="All", channel="—", angle="measurement", kpi="incremental_lift", transition="", effort="low", expected_impact="Truthful attribution; kills ~30% of ‘winning’ campaigns that were noise"),
        dict(title="Frequency cap + minimum delay tuned by lifecycle stage", kind="moengage_activity", when=True, priority=66,
             why="Best users should hear from you least; volatile days multiply alerts. FC with 'count for FC' on alerts shrinks marketing volume automatically.",
             how="Delivery controls → Frequency capping: Push 3/day marketing, cross-channel 5/day; Minimum delay 30 min with Message Queuing on for Flows; alerts Ignore FC + Count for FC.",
             segment="All", channel="Push", angle="suppression", kpi="uninstall_rate", transition="", effort="low", expected_impact="−20–40% uninstalls on high-volume days"),
        dict(title="Business Event for regime flips (market-stress mode)", kind="moengage_activity", when=regime in ("high_volatility_down", "capitulation", "high_volatility_up"), priority=88,
             why=f"Regime is {regime}. Business Events fire attached campaigns/flows on a regime-level occurrence (limits: 10 per 5 min, 200/day).",
             how="1) Register business event `market_stress` with attributes {regime, btc_chg_24h}. 2) Attach: pause acquisition/upsell campaigns; send calm explainer in-app; tighten FC to 1/day. 3) Trigger from this engine when regime flips (debounce 30 min).",
             segment="All actives, split holders vs cash", channel="In-app + Email", angle="capital_preservation", kpi="uninstall_rate", transition="", effort="medium", expected_impact="Avoids the tone-deaf send that costs years of trust"),
        dict(title="RFM segments for traders (recency, frequency, volume)", kind="moengage_activity", when=True, priority=64,
             why="Slipping is defined against a user's own baseline; RFM makes that a standing segment instead of a query.",
             how="Segmentation → RFM on `trade_executed` with value = notional. Create 'Slipping' = frequency down ≥ 50% vs prior 90d; 'Whales at risk' = high value, falling recency.",
             segment="Slipping / Whales at risk", channel="Email + In-app", angle="portfolio_review", kpi="trade_frequency_recovery_14d", transition="slipping", effort="low", expected_impact="Diagnostic outreach to the 5% of users worth 40% of volume"),
        dict(title="Inform API for user-set price targets", kind="moengage_activity", when=True, priority=72,
             why="A user's own alert is transactional: DND-exempt, multichannel with fallback, idempotent by transaction_id.",
             how="1) Price-watch service evaluates targets. 2) POST /v1.0/alerts/send with alert_id, transaction_id = user:symbol:level:date, channels push→sms fallback. 3) Cap 1/hour/asset.",
             segment="Users with a price target", channel="Push → SMS", angle="alerts_adoption", kpi="alert_engagement", transition="activated_habitual", effort="medium", expected_impact="Highest-retention message in the app"),
        dict(title="Cards (app inbox) for market recaps instead of push", kind="moengage_activity", when="cards" not in channels, priority=58,
             why="Recaps do not need interruption; Cards persist and avoid push fatigue while still measurable.",
             how="Enable Cards inbox; publish a daily 'Your watchlist today' card via Content API; push only when the user's own asset moved > 5%.",
             segment="Habitual + Core", channel="Cards", angle="portfolio_review", kpi="card_click_rate", transition="habitual_core", effort="medium", expected_impact="Push volume −30% at equal engagement"),
        dict(title="WhatsApp fallback for undelivered high-value push", kind="moengage_activity", when="whatsapp" not in channels, priority=55,
             why="A third of users have push disabled; Flows can branch on 'not delivered' to a WhatsApp or email fallback.",
             how="Flow: send push → wait 3h → if not delivered AND value tier high → WhatsApp template (utility category) else exit.",
             segment="High-value users with push disabled", channel="WhatsApp", angle="service_status", kpi="delivery_rate", transition="", effort="medium", expected_impact="+15–25% reach on the segment that matters"),
        dict(title="Regulatory / security headline suppression rule", kind="moengage_activity", when=risk > 0, priority=90,
             why=f"{risk} risk-flagged headlines in the last day. Sending acquisition or FOMO copy during a hack or regulatory story is the fastest way to lose trust.",
             how="Segment 'promo_hold' toggled by this engine (cohort sync) → exclusion in every acquisition/upsell campaign; service/safety in-app message if the story concerns the platform or a listed asset.",
             segment="All", channel="—", angle="suppression", kpi="unsubscribe_rate", transition="", effort="low", expected_impact="Zero regret sends"),
        dict(title="Standing Flow for Funded → Activated (first trade in 7 days)", kind="moengage_activity", when=n_flows < 3, priority=68,
             why="The retention cliff is the second trade inside 7 days; only a Flow can sequence education, nudge and fallback with waits and splits.",
             how="Flow: entry = first_deposit; wait 24h; if no trade → in-app 'one concrete first action'; wait 48h; if no trade → email education; exit on trade; 20% holdout.",
             segment="Funded ≤ 7d, 0 trades", channel="In-app + Email", angle="product_education", kpi="first_trade_rate_7d", transition="funded_activated", effort="medium", expected_impact="+3–5 pts first-trade rate"),
    ]
    for a in lib:
        if a.pop("when"):
            out.append(a)
    return out


def generate_rule_ideas() -> Dict[str, Any]:
    """Derive ideas from current data (never fails; each source is best-effort)."""
    from .moengage import MoEngageClient, DataUnavailable
    from .llm.tools import clm_program_audit, anomaly_report, rule_based_audit
    from .market import market_context
    c = MoEngageClient()
    ideas: List[Dict[str, Any]] = []
    ctx: Dict[str, Any] = {"campaigns": [], "flows": [], "regime": "unknown", "risk_headlines": 0}
    try:
        ctx["campaigns"] = c.get_campaigns()
    except Exception:
        pass
    try:
        ctx["flows"] = c.get_flows()
    except Exception:
        pass
    # programme gaps → trending campaigns to stand up
    try:
        pa = clm_program_audit()
        for t in pa.get("uncovered_transitions", []):
            tid = next((k for k, v in pa["coverage"].items() if v["transition"] == t), "")
            kpis = pa["coverage"].get(tid, {}).get("kpi_options") or ["primary KPI"]
            ideas.append(dict(kind="trending_campaign", priority=80, transition=tid, kpi=kpis[0],
                              title=f"Stand up a standing campaign for {t}",
                              why=f"Programme audit: no campaign currently serves '{t}' ({pa['campaigns_total']} campaigns, {pa['broadcast_share_pct']}% broadcast).",
                              how=f"One triggered campaign per transition: define entry event, exclusions (loss-/friction-dormant), 20% holdout, KPI {kpis[0]}, 7–14 day window, kill criteria. Use the agent: 'propose a campaign for {t}'.",
                              segment=t.split(" → ")[0], channel="Push + In-app", angle="product_education", effort="medium", expected_impact="Closes a lifecycle gap; measurable within two weeks"))
        if pa.get("broadcast_share_pct", 0) > 30:
            ideas.append(dict(kind="fix", priority=74, title="Convert the largest broadcast into a triggered lifecycle send",
                              why=f"{pa['broadcast_share_pct']}% of campaigns are broadcasts. Broadcasts should be rare and deliberate.",
                              how="Pick the biggest promotional blast; give it a transition, an entry event and a holdout; retire the schedule.", channel="Push", angle="graduation", kpi="incremental_lift", effort="low", expected_impact="Same reach, honest attribution"))
    except Exception:
        pass
    # anomalies → fixes (with diagnosis) / scale
    try:
        from .anomaly.diagnose import diagnose_campaign
        an = anomaly_report()
        diag_cache: Dict[str, Dict[str, Any]] = {}
        for a in an.get("anomalies", [])[:6]:
            if a.get("impact") == "bad":
                dg = diag_cache.setdefault(a["campaign_id"], diagnose_campaign(a["campaign_id"], c.mode))
                cause = (dg.get("likely_causes") or [{}])[0]
                opt = (dg.get("options") or [{}])[0]
                ideas.append(dict(kind="fix", priority=85 if a["severity"] == "critical" else 65,
                                  title=f"Fix {a['metric']} on {a.get('campaign_name')}: {cause.get('cause', 'investigate')}",
                                  why=f"{a['metric']} {a['value']:g} vs baseline {a['baseline']:g} ({a['method']}, {a.get('confidence')} confidence). {cause.get('evidence', '')} Check first: {cause.get('check_first', '')}",
                                  how=f"{opt.get('action', '')}. {opt.get('how_in_moengage', '')} (effort {opt.get('effort')}, expected: {opt.get('expected_effect')}, risk: {opt.get('risk')})",
                                  segment=a.get("campaign_name"), channel=a.get("channel", ""), angle="fix", kpi=a["metric"], effort=opt.get("effort", "low"), expected_impact=opt.get("expected_effect", ""), data={"anomaly": a, "headline": dg.get("headline")}))
            elif a.get("impact") == "good" and a["direction"] == "up":
                ideas.append(dict(kind="growth_hack", priority=70, title=f"Scale what spiked: {a.get('campaign_name')} ({a['metric']})",
                                  why=f"{a['metric']} {a['value']:g} vs baseline {a['baseline']:g}. Positive outliers are where a lookalike expansion pays.",
                                  how="Expand criteria to adjacent cohort with a holdout; keep creative; check the spike was not a tracking bug first.",
                                  segment=a.get("campaign_name"), channel=a.get("channel", ""), angle="graduation", kpi=a["metric"], effort="low", expected_impact="+20–40% of the spike, sustained", data=a))
    except Exception:
        pass
    # rule audit verdicts
    try:
        for r in rule_based_audit().get("audit", []):
            if r["action_type"] == "convert_to_journey":
                ideas.append(dict(kind="growth_hack", priority=66, title=f"Turn '{r['name']}' into a Flow node",
                                  why=f"Conversion {r['conversion_rate']}% on a one-time blast; recurring value belongs in a lifecycle Flow.",
                                  how="Recreate as event-triggered step inside the relevant transition Flow; add holdout; retire the blast.", segment=r["name"], channel=r["channel"], angle="graduation", kpi="conversion_rate", effort="medium", expected_impact="Compounding conversions instead of one-offs"))
            if r["action_type"] == "scale":
                ideas.append(dict(kind="growth_hack", priority=62, title=f"Lookalike expansion for '{r['name']}'",
                                  why=f"CTR {r['ctr']}% is well above benchmark.", how="Widen one criterion at a time with a 10% holdout; watch delivery and unsubscribes.",
                                  segment=r["name"], channel=r["channel"], angle="graduation", kpi="ctr", effort="low", expected_impact="+15–30% reach at ≥ 80% of current CTR"))
    except Exception:
        pass
    # market → plays
    try:
        mk = market_context(force=False)
        hooks = (mk.get("hooks") or {})
        ctx["regime"] = hooks.get("regime", "unknown")
        ctx["risk_headlines"] = len(((mk.get("news") or {}).get("risk_flags")) or [])
        for h in (hooks.get("hooks") or [])[:6]:
            ideas.append(dict(kind="market_play", priority=78 if h.get("angle") == "suppression" else 60, market_hook_id=h["id"],
                              title=f"Market play: {h['trigger'][:90]}",
                              why=f"Regime {ctx['regime']}; angle '{h['angle']}' is allowed today. {h.get('copy_direction', '')[:160]}",
                              how=f"Segment: {'; '.join(h.get('segments') or [])}. Channel {h.get('channel')}. Timing {h.get('timing')}. Guardrails: {', '.join(h.get('guardrails') or [])}. Use 'Draft with agent'.",
                              segment=(h.get("segments") or [""])[0], channel=h.get("channel", ""), angle=h.get("angle", ""), kpi=h.get("kpi", ""), transition=(h.get("clm_stages") or [""])[0], effort="low", expected_impact=h.get("kpi", "")))
        tr = [t for t in (mk.get("crypto_trending") or []) if (t.get("chg_24h") or 0) > 5][:2]
        if tr and ctx["regime"] in ("trending_up", "chop", "high_volatility_up") and ctx["risk_headlines"] == 0:
            names = ", ".join(f"{t['symbol']} {t['chg_24h']:+.1f}%" for t in tr)
            ideas.append(dict(kind="trending_campaign", priority=58, title=f"‘Trending today’ educational card: {names}",
                              why=f"CoinGecko trending with >5% 24h moves ({names}); regime {ctx['regime']} permits new_listing/feature_discovery angles.",
                              how="Daily card/in-app to alt traders (90d) excluding current holders of the asset: 3 verifiable facts + link to asset page + risk disclaimer. 1/day cap, rotate assets (no repeat in 7d), no ‘buy’ language.",
                              segment="Traded an altcoin in last 90d, not holding the asset", channel="Cards / In-app", angle="feature_discovery", kpi="asset_page_views_per_1k", transition="activated_habitual", effort="low", expected_impact="+10–15% asset page visits; watchlist adds"))
        for e in [e for e in (mk.get("calendar") or []) if e.get("impact") == "High"][:2]:
            ideas.append(dict(kind="market_play", priority=56, title=f"Pre/post brief for {e.get('title')} ({e.get('country')})",
                              why=f"High-impact macro event {e.get('date', '')[:16]}; volatility windows drive sessions and support load.",
                              how="Email T−1 (what it is, how alerts/limit orders work); quiet window ±60 min; push the factual print within 2h to rate-sensitive holders; TTL 3h; no directional language.",
                              segment="Active traders; rate-sensitive holders", channel="Email + Push", angle="product_education", kpi="alert_adoption_rate", transition="activated_habitual", effort="low", expected_impact="Alert adoption spike; fewer panic tickets"))
    except Exception:
        pass
    ideas += _activities(ctx)
    res = upsert_ideas(ideas, "rules")
    res["generated"] = len(ideas)
    return res


def digest_for_agent(limit_titles: int = 60) -> Dict[str, Any]:
    """Compact context the agent receives to invent NEW ideas (existing titles included to avoid repeats)."""
    from .llm.tools import clm_program_audit, anomaly_report, market_snapshot, rule_based_audit
    d: Dict[str, Any] = {}
    for name, fn in (("program", clm_program_audit), ("anomalies", anomaly_report), ("market", market_snapshot), ("audit", rule_based_audit)):
        try:
            v = fn()
            if name == "anomalies":
                v = {"critical": v.get("critical"), "warnings": v.get("warnings"), "top": v.get("anomalies", [])[:6]}
            if name == "market":
                v = {k: v.get(k) for k in ("regime", "narrative", "crypto_movers", "equity_movers", "commodity_movers", "fear_greed", "calendar_high_impact")}
            if name == "program":
                v = {"verdict": v.get("verdict"), "uncovered": v.get("uncovered_transitions"), "broadcast_share_pct": v.get("broadcast_share_pct")}
            if name == "audit":
                v = [{"name": a["name"], "channel": a["channel"], "ctr": a["ctr"], "verdict": a["verdict"]} for a in v.get("audit", [])[:12]]
            d[name] = v
        except Exception as e:
            d[name] = {"error": redact(str(e))[:120]}
    d["existing_titles"] = [r["title"] for r in list_ideas(status=None, limit=limit_titles)]
    return d


def generate_agent_ideas(agent, n: int = 5) -> Dict[str, Any]:
    """Ask the live model for n new ideas; persist them. Returns counts + raw list."""
    digest = digest_for_agent()
    prompt = (f"You are the growth lead. Using ONLY the data below, propose {n} NEW growth hacks or campaigns for MoEngage that are not in existing_titles "
              "(different mechanism, segment or trigger — not rewordings). Each must be specific to the numbers you see. Return STRICT JSON: "
              '{"ideas":[{"kind":"trending_campaign|growth_hack|market_play|moengage_activity|fix","title":"<= 90 chars","why":"cite the data point(s)",'
              '"how":"3-5 concrete MoEngage steps (trigger, segment, channel, FC/TTL, holdout)","segment":"","channel":"","angle":"","kpi":"","transition":"<stage id>",'
              '"effort":"low|medium|high","expected_impact":"","priority":1-100}]}. No prose outside the JSON.\n\nDATA:\n' + json.dumps(digest, default=str)[:12000])
    out = agent.chat(prompt, history=[], persist=False)
    text = out["reply"]
    ideas: List[Dict[str, Any]] = []
    try:
        s, e = text.find("{"), text.rfind("}")
        obj = json.loads(text[s:e + 1])
        ideas = [i for i in obj.get("ideas", []) if isinstance(i, dict) and i.get("title")]
    except Exception:
        pass
    for i in ideas:
        i["data"] = {"model": out.get("model"), "tools": out.get("tool_used")}
    res = upsert_ideas(ideas, "agent")
    res.update({"generated": len(ideas), "model": out.get("model")})
    return res
