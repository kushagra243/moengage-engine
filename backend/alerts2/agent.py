"""
The autonomous agent pass for the internal-employee stage.

On the test cohort the agent is let off the leash: every tick it sees the fresh deterministic detections and the market
picture, may rewrite each alert's copy so it says why the fact matters, and may add its own discovery picks — a funding
crowd, an open-interest flush, a sector rotation, a listing — as alerts in their own right. No human clicks per alert.

What stays fixed even here: the copy linter (venue names, forecasts, leverage lures, hypothetical returns, implied
safety, length), the caps of the internal profile, the freshness rule, the kill switch, and the segment — this runs for
the internal-employee campaign only; on the whole base the agent's own picks are off until the team turns them on.
The model is the free bulk tier; if it is unavailable or answers badly, the deterministic copy goes out unchanged.
"""
from __future__ import annotations
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

IST = timezone(timedelta(hours=5, minutes=30))
TOPICS = ("funding_crowding", "oi_flush", "oi_build", "listing", "sector_rotation", "volume_leader", "level_watch", "macro", "dominance_shift")
TOPIC_LABEL = {"funding_crowding": "Funding is crowded", "oi_flush": "Leverage flushed", "oi_build": "Leverage building", "listing": "New listing", "sector_rotation": "Sector rotation",
               "volume_leader": "Volume leader", "level_watch": "Level in play", "macro": "Macro print", "dominance_shift": "Dominance shift"}

SYSTEM = """You write push alerts for CoinDCX's internal test cohort of employees. You are given (a) alerts already detected by deterministic rules with their default copy, and (b) a snapshot of the market.
Return strict JSON: {"rewrites": [{"det_key": str, "title": str, "body": str, "why": str}], "picks": [{"token": str, "topic": str, "direction": "up"|"down"|"any", "title": str, "body": str, "why": str, "evidence": str}]}.
Rules that are not negotiable:
- Title <= 60 characters, body <= 140. State a fact and give a tool (see the chart, set an alert, review positions). Never a forecast, never buy/sell/long/short, never a price target, never "don't miss", never a leverage multiple, never hypothetical profit, never "safe" or "cap your downside".
- Never name any exchange, venue or competitor. Say "the market", never where the data came from.
- A rewrite may only sharpen an alert you were given (add the number, the why). Keep the same fact.
- A pick must come from the snapshot you were given: quote the number you saw in "evidence" (funding, OI change, 24h change, volume, dominance). Tokens not in the snapshot are rejected. At most the number of picks allowed. topic must be one of: %s.
- If nothing in the snapshot deserves an alert, return an empty picks list. Silence is a valid answer."""


def _snapshot(ctx: Dict[str, Any], cfg: Dict[str, Any]) -> Tuple[Dict[str, Any], set]:
    """The market picture the agent may draw on, compacted, with the set of tokens it is allowed to name."""
    rows = [r for r in ctx.get("crypto_markets") or [] if isinstance(r, dict) and r.get("symbol")]
    top = sorted(rows, key=lambda r: -(r.get("vol_24h_usd") or 0))[:25]
    md = ctx.get("crypto_movers_detail") or {}
    snap = {
        "regime": ((ctx.get("crypto") or {}).get("regime") or {}).get("label"),
        "assets": [{"t": str(r["symbol"]).upper(), "px": r.get("price"), "chg24": r.get("chg_24h"), "vol24_musd": round((r.get("vol_24h_usd") or 0) / 1e6), "oi_musd": round((r.get("oi_usd") or 0) / 1e6),
                    "funding_apr": r.get("funding_apr_pct")} for r in top],
        "crowded_long": [{"t": m.get("symbol"), "funding_apr": m.get("funding_apr_pct")} for m in (md.get("crowded_long") or [])[:5]],
        "crowded_short": [{"t": m.get("symbol"), "funding_apr": m.get("funding_apr_pct")} for m in (md.get("crowded_short") or [])[:5]],
        "oi_surge": [{"t": m.get("symbol"), "oi_chg_pct": m.get("oi_chg_pct"), "px_chg_pct": m.get("price_chg_pct")} for m in ((ctx.get("oi_movers") or {}).get("surge") or [])[:5] if isinstance(m, dict)],
        "oi_drop": [{"t": m.get("symbol"), "oi_chg_pct": m.get("oi_chg_pct"), "px_chg_pct": m.get("price_chg_pct")} for m in ((ctx.get("oi_movers") or {}).get("drop") or [])[:5] if isinstance(m, dict)],
        "new_listings": [{"t": l.get("symbol"), "product": l.get("product")} for l in ((ctx.get("listings") or {}).get("new") or [])[:5] if isinstance(l, dict)],
        "fear_greed": (ctx.get("fear_greed") or {}).get("value"),
        "btc_dominance": ((ctx.get("crypto_global") or {}).get("btc_dominance") if isinstance(ctx.get("crypto_global"), dict) else None),
    }
    allowed = {a["t"] for a in snap["assets"]} | {x["t"] for k in ("crowded_long", "crowded_short", "oi_surge", "oi_drop", "new_listings") for x in snap[k] if x.get("t")}
    return snap, {str(t).upper() for t in allowed if t}


def _valid_copy(title: str, body: str) -> Tuple[bool, str]:
    from . import rules
    title, body = (title or "").strip(), (body or "").strip()
    if not title or not body or len(title) > 60 or len(body) > 140:
        return False, "length"
    findings = rules.lint(title, body)
    bad = [f for f in findings if f["severity"] in ("high", "medium")]
    return (not bad), ("; ".join(f["rule"] for f in bad) or "ok")


def _has_number(s: str) -> bool:
    return bool(re.search(r"\d", s or ""))


def pass_once(cands: List[Dict[str, Any]], ctx: Dict[str, Any], cfg: Dict[str, Any], now: datetime) -> Dict[str, Any]:
    """One model call. Returns {rewrites: {det_key: {title, body, why}}, picks: [...], note, model}. Never raises."""
    from ..llm.provider import LLMClient
    snap, allowed = _snapshot(ctx, cfg)
    max_picks = int(cfg.get("agent_max_picks") or 3)
    given = [{"det_key": c.get("det_key"), "signal": c["signal"], "token": c["token"], "direction": c.get("direction"), "fields": c.get("fields"), "title": c.get("title"), "body": c.get("body")} for c in cands[:12]]
    user = json.dumps({"detected": given, "market": snap, "max_picks": max_picks, "now_ist": now.strftime("%Y-%m-%d %H:%M")}, default=str)
    out = {"rewrites": {}, "picks": [], "rejected": [], "note": "", "model": None}
    try:
        cli = LLMClient(); cli.purpose = "analysis"
        r = cli.chat([{"role": "system", "content": SYSTEM % ", ".join(TOPICS)}, {"role": "user", "content": user}], max_tokens=900, temperature=0.3,
                     response_format={"type": "json_object"}, tier="bulk")
        out["model"] = r.get("model")
        text = r.get("content") or ""
        m = re.search(r"\{.*\}", text, re.S)
        data = json.loads(m.group(0)) if m else {}
    except Exception as e:
        out["note"] = f"agent pass skipped: {str(e)[:120]}"
        return out
    keys = {c.get("det_key") for c in cands}
    for rw in (data.get("rewrites") or [])[:12]:
        if not isinstance(rw, dict) or rw.get("det_key") not in keys:
            continue
        ok, why = _valid_copy(rw.get("title", ""), rw.get("body", ""))
        if ok:
            out["rewrites"][rw["det_key"]] = {"title": rw["title"].strip(), "body": rw["body"].strip(), "why": str(rw.get("why") or "")[:200]}
        else:
            out["rejected"].append({"det_key": rw.get("det_key"), "why": why})
    day = now.strftime("%Y-%m-%d")
    for pk in (data.get("picks") or [])[:max_picks]:
        if not isinstance(pk, dict):
            continue
        tok = str(pk.get("token") or "").upper(); topic = str(pk.get("topic") or "")
        if tok not in allowed or topic not in TOPICS:
            out["rejected"].append({"pick": tok, "why": "token not in snapshot" if tok not in allowed else "unknown topic"}); continue
        if not _has_number(str(pk.get("evidence") or "")):
            out["rejected"].append({"pick": tok, "why": "no number in evidence"}); continue
        ok, why = _valid_copy(pk.get("title", ""), pk.get("body", ""))
        if not ok:
            out["rejected"].append({"pick": tok, "why": why}); continue
        direction = pk.get("direction") if pk.get("direction") in ("up", "down", "any") else "any"
        out["picks"].append({"det_key": f"agent_pick|{tok}|{topic}|{day}", "signal": "agent_pick", "token": tok, "product": "futures", "direction": direction,
                             "value": 0.0, "candle_t": now.timestamp(), "source": f"agent · {TOPIC_LABEL.get(topic, topic)} · {str(pk.get('evidence'))[:80]}",
                             "topic": topic, "title": pk["title"].strip(), "body": pk["body"].strip(), "why": str(pk.get("why") or "")[:200],
                             "fields": {"token": tok, "product": "futures"}, "from_agent": True})
    out["note"] = f"{len(out['rewrites'])} rewrite(s), {len(out['picks'])} pick(s), {len(out['rejected'])} rejected by the linter"
    return out
