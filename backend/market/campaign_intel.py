"""
Competitor campaign listening — what rivals are running today, from free
public channels: exchange announcement APIs (Binance, Bybit, OKX, Bitget),
blog feeds (Mudrex), Google News searches per Indian venue, App Store release
notes, and App Store finance rankings (India) as a growth proxy. Each item is
classified into a campaign type, de-duplicated, persisted 30 days and ranked
by impact × recency × venue pressure, with the counter-play SOP we would run.
Internal only: never named in user copy.
"""
from __future__ import annotations
import hashlib
import json
import re
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from ..database import get_db, get_setting
from ..security import redact
from . import sources

H = {"User-Agent": "Mozilla/5.0 (compatible; moengage-engine intelligence)", "Accept": "application/json, text/xml, */*"}
APPS = {"coindcx": ("CoinDCX", 1517787269), "coinswitch": ("CoinSwitch", 1540214951), "mudrex": ("Mudrex", 1609440707), "delta": ("Delta Exchange", 6478332344), "wazirx": ("WazirX", 1349082789),
        "zebpay": ("ZebPay", 944854686), "pi42": ("Pi42", 6478278938), "binance": ("Binance", 1436799971), "bybit": ("Bybit", 1488296980)}
NEWS_VENUES = {"delta": '"Delta Exchange"', "coinswitch": '"CoinSwitch"', "mudrex": '"Mudrex"', "wazirx": '"WazirX"', "zebpay": '"ZebPay"', "pi42": '"Pi42"', "binance": '"Binance" India', "bybit": '"Bybit" India', "bitget": '"Bitget" India', "coinbase": '"Coinbase" India', "okx": '"OKX" India'}
TYPES = [  # (type, weight, regex)
    ("trading_competition", 90, r"competition|tournament|leaderboard|trading (challenge|battle|league|festival)|prize pool|earn a share of|\$\d[\d,]*,000"),
    ("fee_promo", 85, r"zero[- ]fee|0 ?fee|fee[- ]?free|discounted fees?|fee (cut|discount|rebate|waiver)|no fees?|maker rebate"),
    ("cashback_bonus", 80, r"cashback|bonus|reward(s)? (pool|event)|welcome (bonus|reward)|deposit (bonus|reward)|trade to earn|earn up to|voucher|coupon"),
    ("stock_perps", 78, r"tokeni[sz]ed stock|stock perp|equity x-perp|x-perps?|tradfi perp|stock buzz|xstocks?|nvda|tsla|aapl"),
    ("options", 74, r"\boptions?\b|straddle|call and put|move contract"),
    ("listing", 70, r"\bwill (list|add)\b|\bto list\b|new (listing|crypto|spot|pair|trading pair)|now available for trading|launch(es|ed)? .*perpetual|added? on (earn|spot|margin)"),
    ("product_launch", 68, r"introduc|launch(es|ed|ing)?|now live|new feature|grid trading|copy trading|bot|pay|card"),
    ("earn_apy", 60, r"\bearn\b.*(apr|apy|%)|staking|yield|dual investment|savings|fixed deposit"),
    ("airdrop", 62, r"airdrop|token distribution|hodler"),
    ("referral", 66, r"refer(ral)?|invite (friends|a friend)|affiliate"),
    ("learn_earn", 50, r"learn (and|&) earn|quiz|academy|masterclass|webinar|course"),
    ("festival_offer", 72, r"diwali|dussehra|navratri|holi|independence day|republic day|festival|festive|new year"),
    ("vip_program", 55, r"\bvip\b|loyalty|tier upgrade|elite"),
    ("delisting", 30, r"delist|suspend|remov(e|al) of|cease"),
    ("maintenance", 10, r"maintenance|upgrade|downtime|api update"),
]
COUNTER = {"trading_competition": ("sop_trading_competition", "volume-ranked, never P&L; only if regime allows; else asset spotlight on the same pairs"),
           "fee_promo": ("sop_fee_tier_nudge", "lead with total cost (fee + TDS + spread) and tiers; product reviews thresholds"),
           "cashback_bonus": ("sop_first_week_habit", "do not match bonuses; win with habit tools and transparency; watch our deposit rate for 14d"),
           "stock_perps": ("sop_tokenised_after_hours", "our 24/7 tokenised markets already exist: education to US-hours traders; product checks the named tickers"),
           "options": ("sop_options_education", "defined-risk education to options-intent users; product ask if the instrument is missing"),
           "listing": ("sop_asset_spotlight", "if we list it: spotlight to watchers; else listing request with evidence"),
           "product_launch": ("sop_feature_launch_adoption", "product to assess; marketing prepares an adoption plan if we have the feature"),
           "earn_apy": ("sop_cross_sell_earn", "earn cross-sell to idle balances with variable-APR wording"),
           "airdrop": (None, "no counter: airdrop hype is off-policy; record for product"),
           "referral": ("sop_referral_program", "terms-first referral to habitual users (not UK)"),
           "learn_earn": ("sop_email_education_series", "education series; quiz-gated derivatives content"),
           "festival_offer": ("sop_pm_pillar_message_test", "festival moment with facts and tools, not discounts"),
           "vip_program": ("sop_vip_concierge_quarterly", "concierge for top cohorts; fee review"),
           "delisting": (None, "product/liquidity note; no user campaign"), "maintenance": (None, "none"), "other": (None, "monitor")}


def init_tables() -> None:
    conn = get_db()
    conn.execute("""CREATE TABLE IF NOT EXISTS competitor_campaigns (hash TEXT PRIMARY KEY, venue TEXT, type TEXT, title TEXT, summary TEXT, url TEXT, source TEXT, published_at TEXT,
                    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP, weight INTEGER)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS app_rank_snapshots (day TEXT, app TEXT, chart TEXT, rank INTEGER, rating REAL, ratings_count INTEGER, version TEXT, PRIMARY KEY (day, app, chart))""")
    conn.commit(); conn.close()


def classify(title: str, desc: str = "") -> Dict[str, Any]:
    text = f"{title} {desc}".lower()
    for t, w, rx in TYPES:
        if re.search(rx, text, re.I):
            return {"type": t, "weight": w}
    return {"type": "other", "weight": 35}


def _ts(ms_or_iso) -> Optional[str]:
    try:
        if isinstance(ms_or_iso, (int, float)) or str(ms_or_iso).isdigit():
            v = float(ms_or_iso); v = v / 1000 if v > 1e12 else v
            return datetime.utcfromtimestamp(v).strftime("%Y-%m-%d %H:%M:%S")
        return str(ms_or_iso)[:19].replace("T", " ")
    except Exception:
        return None


# ── fetchers ──────────────────────────────────────────────────────────────────
def binance_announcements() -> List[Dict[str, Any]]:
    d = sources._json("https://www.binance.com/bapi/composite/v1/public/cms/article/list/query", {"type": 1, "pageNo": 1, "pageSize": 20}, 20.0, headers=H) or {}
    out = []
    for cat in ((d.get("data") or {}).get("catalogs") or []):
        for a in (cat.get("articles") or [])[:12]:
            out.append({"venue": "binance", "title": a.get("title"), "summary": cat.get("catalogName"), "url": f"https://www.binance.com/en/support/announcement/{a.get('code')}", "source": "binance announcements", "published_at": _ts(a.get("releaseDate"))})
    return out


def bybit_announcements() -> List[Dict[str, Any]]:
    d = sources._json("https://api.bybit.com/v5/announcements/index", {"locale": "en-US", "limit": 30}, 20.0, headers=H) or {}
    return [{"venue": "bybit", "title": i.get("title"), "summary": (i.get("description") or "")[:200], "url": i.get("url"), "source": f"bybit announcements · {(i.get('type') or {}).get('key') or ''}", "published_at": _ts(i.get("dateTimestamp"))} for i in ((d.get("result") or {}).get("list") or [])]


def okx_announcements() -> List[Dict[str, Any]]:
    d = sources._json("https://www.okx.com/api/v5/support/announcements", {"page": 1}, 20.0, headers=H) or {}
    out = []
    for blk in d.get("data") or []:
        for i in blk.get("details") or []:
            out.append({"venue": "okx", "title": i.get("title"), "summary": i.get("annType"), "url": i.get("url"), "source": "okx announcements", "published_at": _ts(i.get("pTime"))})
    return out


def bitget_announcements() -> List[Dict[str, Any]]:
    out = []
    for t in ("latest_news", "coin_listings", "product_updates"):
        d = sources._json("https://api.bitget.com/api/v2/public/annoucements", {"language": "en_US", "annType": t}, 20.0, headers=H) or {}
        for i in (d.get("data") or [])[:15]:
            out.append({"venue": "bitget", "title": i.get("annTitle"), "summary": i.get("annDesc") or t, "url": i.get("annUrl"), "source": f"bitget announcements · {t}", "published_at": _ts(i.get("cTime"))})
    return out


def _rss(url: str, venue: str, source: str, limit: int = 15) -> List[Dict[str, Any]]:
    import feedparser
    r = sources._get(url, None, 20.0, headers={"Accept": "application/rss+xml, application/xml, text/xml, */*"})
    f = feedparser.parse(r.text)
    out = []
    for e in f.entries[:limit]:
        pub = None
        if getattr(e, "published_parsed", None):
            pub = time.strftime("%Y-%m-%d %H:%M:%S", e.published_parsed)
        out.append({"venue": venue, "title": e.get("title"), "summary": re.sub(r"<[^>]+>", " ", e.get("summary", ""))[:200], "url": e.get("link"), "source": source, "published_at": pub})
    return out


def mudrex_blog() -> List[Dict[str, Any]]:
    return _rss("https://mudrex.com/learn/feed/", "mudrex", "mudrex blog")


def news_for(venue: str, q: str) -> List[Dict[str, Any]]:
    url = "https://news.google.com/rss/search?q=" + __import__("urllib.parse").parse.quote(f"{q} (offer OR campaign OR competition OR cashback OR zero fee OR launch OR listing OR referral OR bonus OR festival) when:3d") + "&hl=en-IN&gl=IN&ceid=IN:en"
    rows = _rss(url, venue, "google news", limit=8)
    for r in rows:
        r["source"] = "news"; r["news_source"] = (r.get("title") or "").rsplit(" - ", 1)[-1][:40]
    return rows


def app_rankings() -> Dict[str, Any]:
    """App Store India: rank in Finance (top free + top grossing), rating, count, version and release notes for us and competitors."""
    out: Dict[str, Any] = {"apps": {}, "day": datetime.utcnow().strftime("%Y-%m-%d")}
    charts = {}
    for chart, url in (("top_free", "https://itunes.apple.com/in/rss/topfreeapplications/limit=200/genre=6015/json"), ("top_grossing", "https://itunes.apple.com/in/rss/topgrossingapplications/limit=200/genre=6015/json")):
        try:
            d = sources._json(url, None, 20.0, headers=H) or {}
            charts[chart] = {str(e.get("id", {}).get("attributes", {}).get("im:id")): i + 1 for i, e in enumerate(((d.get("feed") or {}).get("entry") or []))}
        except Exception:
            charts[chart] = {}
    ids = ",".join(str(v[1]) for v in APPS.values())
    try:
        look = sources._json("https://itunes.apple.com/lookup", {"id": ids, "country": "in"}, 20.0, headers=H) or {}
        for r in look.get("results") or []:
            key = next((k for k, v in APPS.items() if v[1] == r.get("trackId")), None)
            if not key:
                continue
            out["apps"][key] = {"name": APPS[key][0], "rating": r.get("averageUserRating"), "ratings_count": r.get("userRatingCount"), "version": r.get("version"), "updated": (r.get("currentVersionReleaseDate") or "")[:10],
                                "release_notes": (r.get("releaseNotes") or "")[:280], "rank_top_free": charts.get("top_free", {}).get(str(r.get("trackId"))), "rank_top_grossing": charts.get("top_grossing", {}).get(str(r.get("trackId")))}
    except Exception as e:
        out["error"] = redact(str(e))[:100]
    try:
        init_tables(); conn = get_db()
        for k, a in out["apps"].items():
            for chart in ("top_free", "top_grossing"):
                conn.execute("INSERT OR REPLACE INTO app_rank_snapshots (day, app, chart, rank, rating, ratings_count, version) VALUES (?,?,?,?,?,?,?)", (out["day"], k, chart, a.get(f"rank_{chart}"), a.get("rating"), a.get("ratings_count"), a.get("version")))
        for k, a in out["apps"].items():
            prev = conn.execute("SELECT rank, ratings_count FROM app_rank_snapshots WHERE app=? AND chart='top_free' AND day <= date('now','-7 days') ORDER BY day DESC LIMIT 1", (k,)).fetchone()
            if prev:
                a["rank_top_free_7d_ago"] = prev["rank"]; a["ratings_added_7d"] = (a.get("ratings_count") or 0) - (prev["ratings_count"] or 0)
        conn.commit(); conn.close()
    except Exception:
        pass
    return out


# ── assemble ──────────────────────────────────────────────────────────────────
def collect(force: bool = False) -> Dict[str, Any]:
    def fetch():
        init_tables()
        items: List[Dict[str, Any]] = []; errors: List[str] = []
        for name, fn in (("binance", binance_announcements), ("bybit", bybit_announcements), ("okx", okx_announcements), ("bitget", bitget_announcements), ("mudrex", mudrex_blog)):
            try:
                items += fn()
            except Exception as e:
                errors.append(f"{name}: {redact(str(e))[:80]}")
        for venue, q in NEWS_VENUES.items():
            try:
                items += news_for(venue, q)
            except Exception as e:
                errors.append(f"news {venue}: {redact(str(e))[:60]}")
        apps = {}
        try:
            apps = app_rankings()
            for k, a in (apps.get("apps") or {}).items():
                if a.get("release_notes") and k != "coindcx":
                    items.append({"venue": k, "title": f"{a['name']} app update {a.get('version')}: {a['release_notes'][:90]}", "summary": a["release_notes"], "url": None, "source": "app store release notes", "published_at": (a.get("updated") or "") + " 00:00:00"})
        except Exception as e:
            errors.append("app store: " + redact(str(e))[:80])
        conn = get_db(); now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"); new = 0
        for it in items:
            if not it.get("title"):
                continue
            c = classify(it["title"], it.get("summary") or "")
            h = hashlib.sha1(f"{it['venue']}|{it['title'].strip().lower()[:120]}".encode()).hexdigest()[:16]
            cur = conn.execute("INSERT OR IGNORE INTO competitor_campaigns (hash, venue, type, title, summary, url, source, published_at, first_seen, weight) VALUES (?,?,?,?,?,?,?,?,?,?)",
                               (h, it["venue"], c["type"], redact(it["title"])[:200], redact(it.get("summary") or "")[:300], it.get("url"), it.get("source"), it.get("published_at"), now, c["weight"]))
            new += cur.rowcount
        conn.execute("DELETE FROM competitor_campaigns WHERE first_seen < datetime('now', '-30 days')")
        conn.commit(); conn.close()
        return {"fetched_at": time.time(), "new": new, "errors": errors, "apps": apps}
    if force:
        sources._MEM.pop("competitor_campaigns", None)
        try:
            import os; os.remove(os.path.join(sources.CACHE_DIR, "competitor_campaigns.json"))
        except Exception:
            pass
    return sources.cached("competitor_campaigns", fetch, int(get_setting("competitor_cache_ttl_s", "900") or 900)) or {"errors": ["no data"], "apps": {}}


def campaigns(hours: int = 48, venue: Optional[str] = None, force: bool = False, limit: int = 40) -> Dict[str, Any]:
    meta = collect(force=force)
    init_tables(); conn = get_db()
    q = "SELECT * FROM competitor_campaigns WHERE (published_at >= datetime('now', ?) OR first_seen >= datetime('now', ?))" + (" AND venue=?" if venue else "") + " AND type NOT IN ('maintenance')"
    args: List[Any] = [f"-{int(hours)} hours", f"-{int(hours)} hours"] + ([venue] if venue else [])
    rows = [dict(r) for r in conn.execute(q, args).fetchall()]
    conn.close()
    try:
        from .competitors import intel as _intel
        pressure = {}
        ci = sources._MEM.get("competitors", (0, {}))[1] if isinstance(sources._MEM.get("competitors"), tuple) else {}
    except Exception:
        pressure = {}
    def score(r):
        age_h = 48.0
        try:
            age_h = max(0.0, (datetime.utcnow() - datetime.fromisoformat((r.get("published_at") or r.get("first_seen") or "").replace(" ", "T"))).total_seconds() / 3600)
        except Exception:
            pass
        recency = max(0.2, 1 - age_h / 72)
        venue_w = {"binance": 1.0, "bybit": 0.95, "okx": 0.9, "bitget": 0.85, "delta": 1.1, "coinswitch": 1.1, "mudrex": 1.0, "wazirx": 0.9, "zebpay": 0.8, "pi42": 0.9, "coinbase": 0.8}.get(r["venue"], 0.8)
        return round((r.get("weight") or 35) * recency * venue_w, 1)
    for r in rows:
        r["score"] = score(r); sop, how = COUNTER.get(r["type"], COUNTER["other"]); r["counter_sop"] = sop; r["counter"] = how
        r["impact"] = "MATERIAL" if r["score"] >= 70 else "WATCH" if r["score"] >= 45 else "INFO"
    rows.sort(key=lambda r: -r["score"])
    by_type: Dict[str, int] = {}
    for r in rows:
        by_type[r["type"]] = by_type.get(r["type"], 0) + 1
    by_venue: Dict[str, int] = {}
    for r in rows:
        by_venue[r["venue"]] = by_venue.get(r["venue"], 0) + 1
    top = rows[:limit]
    actions = [{"priority": 75 if r["impact"] == "MATERIAL" else 55, "owner": "marketing" if r["counter_sop"] else "product", "type": "counter_campaign", "campaign_type": r["type"], "symbol": r["venue"], "product": r["type"],
                "what": f"{r['venue']}: {r['title'][:110]} → {r['counter']}", "sop": r["counter_sop"], "url": r.get("url")} for r in top if r["impact"] == "MATERIAL"][:6]
    return {"generated_at": meta.get("fetched_at"), "window_hours": hours, "campaigns": top, "by_type": dict(sorted(by_type.items(), key=lambda kv: -kv[1])), "by_venue": by_venue, "actions": actions,
            "apps": (meta.get("apps") or {}).get("apps", {}), "errors": meta.get("errors"), "new_this_fetch": meta.get("new"),
            "note": "free public channels only (exchange announcement APIs, blog feeds, Google News, App Store). Classification is keyword-based; read the title before acting. Never named in copy."}
