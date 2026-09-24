"""
Live: one page that answers "what is the market doing, what is MoEngage doing, what is the engine doing", every number
against a baseline.

Baselines: the operator presses SET BASELINE and the page remembers today's programme, channel, campaign and market
numbers (`live_baseline_json`); every later read shows the delta against it. Where no baseline was set, a campaign's own
28-day median from the snapshot store stands in, and channels use the playbook ranges. Nothing here calls a model.
"""
from __future__ import annotations
import json
import statistics
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from .database import get_setting, set_setting
from .security import audit
from .v3 import IST, plain

BASELINE_KEY = "live_baseline_json"
METRICS = ("delivery_rate", "ctr", "conversion_rate")


def baseline() -> Dict[str, Any]:
    try:
        return json.loads(get_setting(BASELINE_KEY, "") or "{}")
    except Exception:
        return {}


def _delta(cur: Optional[float], base: Optional[float], unit: str = "pp", good_up: bool = True) -> Dict[str, Any]:
    if cur is None or base is None:
        return {"delta": None, "word": "no baseline", "tone": "dim"}
    d = (cur - base) if unit == "pp" else ((cur - base) / base * 100.0 if base else 0.0)
    tol = 0.3 if unit == "pp" else 5.0
    if abs(d) <= tol:
        return {"delta": round(d, 2), "word": "at baseline", "tone": "green"}
    better = (d > 0) == good_up
    return {"delta": round(d, 2), "word": ("above" if d > 0 else "below") + " baseline", "tone": "green" if better else "magenta"}


def _own_median(cid: str, metric: str, source: str, days: int = 28) -> Optional[float]:
    try:
        from .anomaly.store import get_history
        today = datetime.now(IST).strftime("%Y-%m-%d")
        vals = [h[metric] for h in get_history(cid, source, days) if h.get(metric) is not None and h.get("snapshot_date") != today]
        return round(statistics.median(vals), 3) if len(vals) >= 3 else None
    except Exception:
        return None


def _mode() -> str:
    return "mock" if get_setting("mock_mode", "true").lower() == "true" else "live"


# ── markets ───────────────────────────────────────────────────────────────────
def _markets(ctx: Dict[str, Any], base: Dict[str, Any]) -> Dict[str, Any]:
    from . import sentiment
    mood = sentiment.read(ctx)
    cg = ctx.get("crypto_global") or {}; fg = ctx.get("fear_greed") or {}; reg = (ctx.get("crypto") or {}).get("regime") or {}
    bm = base.get("markets") or {}
    facts = [{"k": "MOOD", "v": mood["label"].upper(), "sub": "; ".join(mood["evidence"][:3]), "tone": {"fearful": "magenta", "cautious": "amber", "neutral": "dim", "constructive": "green", "euphoric": "amber"}[mood["label"]]},
             {"k": "REGIME", "v": str(reg.get("label") or "unknown").replace("_", " "), "sub": "; ".join(reg.get("reasons") or [])[:160], "tone": "text"},
             {"k": "FEAR & GREED", "v": str(fg.get("value") or "—"), "sub": f"{fg.get('label') or ''} · last 7 days {', '.join(str(x) for x in (fg.get('history') or [])[:7])}", **_delta(_f(fg.get("value")), _f(bm.get("fng")), "pp")},
             {"k": "MARKET CAP 24H", "v": f"{_f(cg.get('mcap_chg_24h')) or 0:+.1f}%", "sub": f"${(_f(cg.get('total_mcap_usd')) or 0) / 1e12:.2f}T", "tone": "green" if (_f(cg.get("mcap_chg_24h")) or 0) >= 0 else "magenta"},
             {"k": "BTC DOMINANCE", "v": f"{_f(cg.get('btc_dominance')) or 0:.1f}%", "sub": f"ETH {_f(cg.get('eth_dominance')) or 0:.1f}%", **_delta(_f(cg.get("btc_dominance")), _f(bm.get("btc_dominance")), "pp")},
             {"k": "BREADTH", "v": f"{_f(reg.get('breadth_up_pct')) or 0:.0f}% up", "sub": f"funding bias {_funding_word(reg.get('funding_bias'))} · vol percentile {reg.get('vol_percentile')}", "tone": "text"}]
    movers = ctx.get("crypto_movers") or []
    up = sorted([m for m in movers if _f(m.get("chg_24h")) is not None], key=lambda m: -_f(m["chg_24h"]))[:6]
    down = sorted([m for m in movers if _f(m.get("chg_24h")) is not None], key=lambda m: _f(m["chg_24h"]))[:6]
    row = lambda m: {"symbol": m.get("symbol"), "chg": round(_f(m.get("chg_24h")) or 0, 1), "vol": _usd(m.get("vol_24h_usd")), "oi": _usd(m.get("oi_usd")), "funding": m.get("funding_apr_pct")}   # noqa: E731
    oi = ctx.get("oi_movers") or {}
    other = {k: [{"symbol": m.get("symbol"), "chg": round(_f(m.get("chg_24h")) or 0, 1), "vol": _usd(m.get("vol_24h_usd")), "oi": _usd(m.get("oi_usd"))} for m in (ctx.get(k) or [])[:4]] for k in ("equity_movers", "index_movers", "commodity_movers")}
    cal = [c for c in (ctx.get("calendar") or []) if str(c.get("impact", "")).lower() in ("high", "medium")][:6]
    news = ((ctx.get("news") or {}).get("risk_flags") or [])[:5]
    return {"facts": facts, "mood": mood, "movers_up": [row(m) for m in up], "movers_down": [row(m) for m in down],
            "oi_surge": [{"symbol": x.get("symbol"), "oi_chg": x.get("oi_chg_pct"), "price_chg": x.get("price_chg_pct"), "reading": x.get("reading")} for x in (oi.get("surge") or [])[:5]],
            "oi_drop": [{"symbol": x.get("symbol"), "oi_chg": x.get("oi_chg_pct"), "price_chg": x.get("price_chg_pct"), "reading": x.get("reading")} for x in (oi.get("drop") or [])[:5]],
            "tokenised": other, "listings_new": [str(x.get("symbol") or x) for x in ((ctx.get("listings") or {}).get("new") or [])][:8],
            "calendar": [{"title": c.get("title"), "when": str(c.get("date"))[:16], "impact": c.get("impact"), "country": c.get("country")} for c in cal],
            "risk_news": [{"title": plain(n.get("title")), "source": n.get("source"), "published": str(n.get("published"))[:16]} for n in news],
            "tier0": (ctx.get("tier0") or {}).get("kind"), "prices_at": ctx.get("prices_at") or ctx.get("generated_at"), "hooks": ((ctx.get("hooks") or {}).get("angle_policy") or {})}


def _funding_word(v: Any) -> str:
    x = _f(v)
    if x is None:
        return str(v or "unknown")
    return "longs crowded" if x > 0.0001 else "shorts crowded" if x < -0.0001 else "balanced"


def _f(v: Any) -> Optional[float]:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _usd(v: Any) -> str:
    x = _f(v)
    if x is None:
        return "—"
    return f"${x / 1e9:.1f}B" if x >= 1e9 else f"${x / 1e6:.1f}M" if x >= 1e6 else f"${x / 1e3:.0f}K"


# ── moengage ──────────────────────────────────────────────────────────────────
def _moengage(base: Dict[str, Any]) -> Dict[str, Any]:
    from . import workspace_analysis as wa, brain, experiments
    f = wa.facts()
    prog = f.get("programme") or {}; bp = base.get("programme") or {}
    src = _mode()
    totals = [{"k": "CAMPAIGNS", "v": str(prog.get("campaigns", 0)), "sub": f"stats for {prog.get('with_stats', 0)} ({prog.get('coverage_pct', 0)}%)", "tone": "text"},
              {"k": "SENT · DELIVERED", "v": f"{_int(prog.get('sent')):,} · {_int(prog.get('delivered')):,}", "sub": f"delivery {prog.get('delivery_rate', 0)}%", **_delta(_f(prog.get("delivery_rate")), _f(bp.get("delivery_rate")), "pp")},
              {"k": "CLICK RATE", "v": f"{prog.get('click_rate', 0)}%", "sub": "programme, weighted", **_delta(_f(prog.get("click_rate")), _f(bp.get("click_rate")), "pp")},
              {"k": "CONVERSION", "v": f"{prog.get('conversion_rate', 0)}%", "sub": f"revenue attributed {_usd(prog.get('revenue'))}", **_delta(_f(prog.get("conversion_rate")), _f(bp.get("conversion_rate")), "pp")}]
    bc = base.get("channels") or {}
    channels = [{"channel": c.get("channel"), "campaigns": c.get("campaigns"), "delivered": _int(c.get("delivered")), "engagement": c.get("engagement_rate"), "engagement_label": c.get("engagement_label"),
                 "delivery": c.get("delivery_rate"), "conversion": c.get("conversion_rate"), "benchmark": c.get("benchmark"), "verdict": c.get("verdict"), "why": plain(c.get("why")),
                 "vs_baseline": _delta(_f(c.get("engagement_rate")), _f((bc.get(c.get("channel")) or {}).get("engagement_rate")), "pp")} for c in f.get("channels") or []]
    league = []
    bcamp = base.get("campaigns") or {}
    for c in ((f.get("campaigns") or {}).get("league") or [])[:20]:
        cid = str(c.get("id"))
        own = {m: (_f((bcamp.get(cid) or {}).get(m)) if bcamp.get(cid) else _own_median(cid, m, src)) for m in METRICS}
        league.append({"id": cid, "name": plain(c.get("name")), "channel": c.get("channel"), "status": c.get("status"), "segment": c.get("segment"), "sent": _int(c.get("sent")), "delivered": _int(c.get("delivered")),
                       "delivery": c.get("delivery_rate"), "ctr": c.get("ctr"), "conversion": c.get("conversion_rate"), "revenue": _usd(c.get("revenue")), "health": c.get("health"), "verdict": c.get("verdict"),
                       "do": plain(c.get("recommendation") or ""), "baseline_source": "set" if bcamp.get(cid) else "own 28-day median",
                       "vs": {m: _delta(_f(c.get({"ctr": "ctr", "conversion_rate": "conversion_rate", "delivery_rate": "delivery_rate"}[m])), own[m], "pp") for m in METRICS}})
    anomalies = []
    try:
        for a in brain.anomalies_view()[:8]:
            anomalies.append({"campaign": plain(a.get("campaign")), "metric": str(a.get("metric") or "").replace("_", " "), "delta": plain(a.get("delta")), "severity": a.get("severity"), "cause": plain(a.get("cause") or ""), "option": plain(a.get("option") or ""), "campaign_id": a.get("campaign_id")})
    except Exception:
        pass
    exps = []
    try:
        for e in experiments.list_experiments(40):
            if e.get("status") not in ("running", "live", "window_complete", "read"):
                continue
            r = e.get("readout") or {}
            exps.append({"id": e.get("id"), "proposal_id": e.get("proposal_id"), "name": plain(e.get("campaign_name") or e.get("name") or ""), "kpi": e.get("primary_kpi"), "state": r.get("state") or e.get("status"), "day": r.get("days_run"), "of": r.get("window_days"),
                         "value": r.get("value"), "metric": r.get("metric"), "baseline": (r.get("baseline") or {}).get("mean"), "ci95": r.get("ci95"), "verdict": plain(r.get("verdict") or r.get("message") or ""), "holdout": e.get("control_group_pct"), "limits": plain(r.get("claim_limits") or "")})
    except Exception:
        pass
    ex_counts = f.get("experiments") or {}
    return {"source": prog.get("source"), "totals": totals, "channels": channels, "league": league, "anomalies": anomalies, "experiments": exps,
            "experiment_counts": {k: ex_counts.get(k) for k in ("proposed", "running", "read", "with_holdout")}, "history_days": (f.get("campaigns") or {}).get("history_days"),
            "lifecycle": f.get("lifecycle"), "north_star": get_setting("north_star", "") or "", "generated_at": f.get("generated_at")}


def _int(v: Any) -> int:
    x = _f(v)
    return int(x) if x is not None else 0


# ── alerts + engine ───────────────────────────────────────────────────────────
def _alerts() -> Dict[str, Any]:
    try:
        from .alerts2 import discovery, service, timing
        st = discovery.status()
        today = st.get("today") or {}
        fired = today.get("fired") if isinstance(today.get("fired"), dict) else {}
        det = (st.get("determinism") or {}).get("outcomes_today") or {}
        return {"live": bool(st.get("live")) and not service.kill_switch(), "why": plain(st.get("why_not_live") or ""), "stage": st.get("stage"), "kill": service.kill_switch(),
                "fired_today": {k: v for k, v in fired.items() if not str(k).startswith("_")}, "fired_total": fired.get("_total", 0), "outcomes_today": det,
                "approval_mode": discovery.approval_mode(), "queue": len((st.get("timing") or {}).get("queue") or []), "window_today": ((st.get("timing") or {}).get("today") or {}).get("id"),
                "recent": [{"at": str(x.get("created_at") or "")[11:16], "title": plain(x.get("title")), "status": x.get("status"), "signal": x.get("signal"), "token": x.get("token")} for x in (st.get("fires") or [])[:8]],
                "cohorts": [{"id": c["id"], "state": "RUNNING" if c.get("active") else "LOCKED" if c.get("locked") else "READY"} for c in st.get("cohorts") or []]}
    except Exception as e:
        return {"live": False, "why": str(e)[:120], "fired_today": {}, "fired_total": 0, "outcomes_today": {}, "recent": [], "cohorts": []}


def _engine() -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    try:
        from . import refresher
        rs = refresher.status()
        out["jobs"] = {"failing": rs.get("failing", 0), "overdue": rs.get("overdue", 0), "market_age_min": rs.get("market_age_min"), "list": [{"job": j["job"], "ok": j.get("ok"), "age_min": j.get("age_min"), "error": plain(j.get("error") or "")[:100]} for j in rs.get("jobs") or [] if j.get("ok") is False or j.get("overdue")]}
    except Exception:
        out["jobs"] = {}
    for name, fn in (("budget", lambda: __import__("backend.llm.budget", fromlist=["status"]).status()), ("challenges", lambda: __import__("backend.challenges", fromlist=["summary"]).summary()),
                     ("telegram", lambda: __import__("backend.telegram_out", fromlist=["status"]).status()), ("slack", lambda: __import__("backend.slack_out", fromlist=["status"]).status())):
        try:
            out[name] = fn()
        except Exception:
            out[name] = {}
    if isinstance(out.get("challenges"), dict):                  # engine kinds are named in plain words on this page
        out["challenges"]["top"] = [{**t, "task": plain(t.get("task"))} for t in out["challenges"].get("top") or []]
    try:
        from . import v3_ops
        out["asks_open"] = v3_ops.asks()["open"]
    except Exception:
        out["asks_open"] = None
    out["role"] = get_setting("engine_role", "operator")
    return out


def live() -> Dict[str, Any]:
    from .market.context import market_context
    try:
        ctx = market_context()
    except Exception:
        ctx = {}
    base = baseline()
    now = datetime.now(IST)
    m = _markets(ctx, base); moe = _moengage(base); al = _alerts(); en = _engine()
    n_bad = sum(1 for c in moe["league"] if c["vs"]["ctr"]["tone"] == "magenta") + len(moe["anomalies"])
    lead = (f"Market {m['mood']['label']}, {m['facts'][1]['v']}. " + (f"{al['fired_total']} alert{'s' if al['fired_total'] != 1 else ''} out today. " if al["live"] else "Alerts not live. ")
            + (f"{n_bad} campaign{'s' if n_bad != 1 else ''} below baseline. " if n_bad else "Every campaign at or above baseline. ")
            + (f"Baseline set {str(base.get('at'))[:10]}." if base.get("at") else "No baseline set yet — press SET BASELINE."))
    return {"at": now.strftime("%H:%M:%S"), "date": now.strftime("%A %d %B").upper(), "mode": "practice" if _mode() == "mock" else "live", "lead": lead,
            "baseline": {"at": base.get("at"), "by": base.get("by"), "note": base.get("note"), "campaigns": len(base.get("campaigns") or {})},
            "markets": m, "moengage": moe, "alerts": al, "engine": en}


def set_baseline(actor: str = "user", note: str = "") -> Dict[str, Any]:
    """Remember today's numbers as the reference every later read is compared against."""
    from . import workspace_analysis as wa
    from .market.context import market_context
    f = wa.facts()
    try:
        ctx = market_context()
    except Exception:
        ctx = {}
    prog = f.get("programme") or {}
    b = {"at": datetime.now(IST).isoformat(timespec="seconds"), "by": actor, "note": note[:200], "mode": _mode(),
         "programme": {k: prog.get(k) for k in ("delivery_rate", "click_rate", "conversion_rate", "sent", "delivered")},
         "channels": {c.get("channel"): {"engagement_rate": c.get("engagement_rate"), "delivery_rate": c.get("delivery_rate"), "conversion_rate": c.get("conversion_rate")} for c in f.get("channels") or []},
         "campaigns": {str(c.get("id")): {"ctr": c.get("ctr"), "conversion_rate": c.get("conversion_rate"), "delivery_rate": c.get("delivery_rate")} for c in ((f.get("campaigns") or {}).get("league") or [])},
         "markets": {"fng": (ctx.get("fear_greed") or {}).get("value"), "btc_dominance": (ctx.get("crypto_global") or {}).get("btc_dominance"), "mcap": (ctx.get("crypto_global") or {}).get("total_mcap_usd")}}
    set_setting(BASELINE_KEY, json.dumps(b, default=str))
    audit("live.baseline_set", {"campaigns": len(b["campaigns"]), "note": note[:80]}, actor=actor)
    return {"ok": True, "baseline": {"at": b["at"], "by": actor, "campaigns": len(b["campaigns"])}, "toast": f"Baseline set · {len(b['campaigns'])} campaigns, {len(b['channels'])} channels and the market remembered"}
