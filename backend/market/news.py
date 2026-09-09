"""
Headline news via RSS (feedparser). Feeds are grouped by asset class; Google News
RSS gives keyword queries for anything else. Titles/links/publish times only —
we never fetch article bodies.
"""
from __future__ import annotations
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

import feedparser

from ..database import get_setting
from ..security import guarded_session, redact
from .sources import cached, UA

log = logging.getLogger("moengage.news")

FEEDS: Dict[str, List[Dict[str, str]]] = {
    "crypto": [
        {"name": "CoinDesk", "url": "https://www.coindesk.com/arc/outboundfeeds/rss/"},
        {"name": "Cointelegraph", "url": "https://cointelegraph.com/rss"},
        {"name": "The Block", "url": "https://www.theblock.co/rss.xml"},
        {"name": "Decrypt", "url": "https://decrypt.co/feed"},
        {"name": "Bloomberg Crypto", "url": "https://feeds.bloomberg.com/crypto/news.rss"},
    ],
    "stocks": [
        {"name": "CNBC Markets", "url": "https://www.cnbc.com/id/20910258/device/rss/rss.html"},
        {"name": "WSJ Markets", "url": "https://feeds.content.dowjones.io/public/rss/RSSMarketsMain"},
        {"name": "Bloomberg Markets", "url": "https://feeds.bloomberg.com/markets/news.rss"},
        {"name": "ET Markets", "url": "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"},
        {"name": "LiveMint Markets", "url": "https://www.livemint.com/rss/markets"},
        {"name": "Business Standard Markets", "url": "https://www.business-standard.com/rss/markets-106.rss"},
    ],
    "commodities": [
        {"name": "Investing.com Commodities", "url": "https://www.investing.com/rss/news_11.rss"},
        {"name": "Google News: gold oil commodities", "url": "https://news.google.com/rss/search?q=(gold+OR+crude+oil+OR+silver+OR+commodities)+prices+when:1d&hl=en-IN&gl=IN&ceid=IN:en"},
    ],
    "macro": [
        {"name": "Bloomberg Economics", "url": "https://feeds.bloomberg.com/economics/news.rss"},
        {"name": "Google News: Fed RBI inflation", "url": "https://news.google.com/rss/search?q=(Federal+Reserve+OR+RBI+OR+inflation+OR+CPI+OR+FOMC)+when:1d&hl=en-IN&gl=IN&ceid=IN:en"},
    ],
    "regulatory": [
        {"name": "SEC press", "url": "https://www.sec.gov/news/pressreleases.rss"},
        {"name": "SEBI", "url": "https://www.sebi.gov.in/sebirss.xml"},
        {"name": "RBI press", "url": "https://www.rbi.org.in/pressreleases_rss.xml"},
        {"name": "Google News: crypto regulation India", "url": "https://news.google.com/rss/search?q=(crypto+regulation+OR+crypto+tax+OR+SEBI+crypto+OR+exchange+hack)+when:1d&hl=en-IN&gl=IN&ceid=IN:en"},
    ],
}

RISK_WORDS = re.compile(r"\b(hack|exploit|breach|ban|lawsuit|sec charges|fraud|bankrupt|insolven|liquidat|crash|plunge|halt|outage|freeze|arrest|scam|rug)\b", re.I)
POS_WORDS = re.compile(r"\b(record high|all-time high|ath|surge|rally|approval|approved|etf inflow|listing|upgrade|breakout|soar)\b", re.I)


def _parse(url: str, limit: int = 15) -> List[Dict[str, Any]]:
    s = guarded_session("market")
    r = s.get(url, timeout=15, headers={"User-Agent": UA, "Accept": "application/rss+xml, application/xml, text/xml, */*"})
    r.raise_for_status()
    fp = feedparser.parse(r.content)
    out = []
    for e in fp.entries[:limit]:
        ts = None
        for k in ("published_parsed", "updated_parsed"):
            if getattr(e, k, None):
                ts = datetime.fromtimestamp(time.mktime(getattr(e, k)), tz=timezone.utc).isoformat(); break
        title = (getattr(e, "title", "") or "").strip()
        out.append({"title": title[:200], "link": getattr(e, "link", ""), "published": ts,
                    "sentiment": "risk" if RISK_WORDS.search(title) else ("positive" if POS_WORDS.search(title) else "neutral")})
    return out


def headlines(category: Optional[str] = None, limit_per_feed: int = 10) -> Dict[str, Any]:
    cats = [category] if category else list(FEEDS.keys())
    result: Dict[str, Any] = {"fetched_at": datetime.now(timezone.utc).isoformat(), "categories": {}, "errors": []}
    from concurrent.futures import ThreadPoolExecutor

    def one(cat, feed):
        def fetch(url=feed["url"], name=feed["name"]):
            try:
                rows = _parse(url, limit_per_feed)
                for r in rows:
                    r["source"] = name
                return rows
            except Exception as e:
                log.info("feed %s failed: %s", name, redact(str(e)))
                return None
        return cat, feed["name"], cached("rss_" + re.sub(r"[^a-z0-9]+", "_", feed["name"].lower()), fetch, 900)

    jobs = [(cat, feed) for cat in cats for feed in FEEDS.get(cat, [])]
    per_cat: Dict[str, List[Dict[str, Any]]] = {c: [] for c in cats}
    with ThreadPoolExecutor(max_workers=8) as ex:
        for cat, name, rows in ex.map(lambda cf: one(*cf), jobs):
            if rows is None:
                result["errors"].append(name)
            else:
                per_cat[cat].extend(rows)
    # keyed API news first (Finnhub) — merged and de-duplicated with RSS
    try:
        from .sources import finnhub_news
        fh_map = {"crypto": "crypto", "stocks": "general", "macro": "forex", "commodities": "general"}
        for cat in cats:
            if cat in fh_map:
                rows = finnhub_news(fh_map[cat], 12)
                for r in rows:
                    r["sentiment"] = "risk" if RISK_WORDS.search(r["title"]) else ("positive" if POS_WORDS.search(r["title"]) else "neutral")
                per_cat[cat] = rows + per_cat[cat]
    except Exception as e:
        log.info("finnhub news merge: %s", redact(str(e)))
    seen = set()
    for cat in cats:
        items = []
        for it in sorted(per_cat[cat], key=lambda x: x.get("published") or "", reverse=True):
            key = re.sub(r"[^a-z0-9]+", " ", (it.get("title") or "").lower()).strip()[:80]
            if key in seen:
                continue
            seen.add(key); items.append(it)
        result["categories"][cat] = items[:40]
    result["risk_flags"] = [i for c in result["categories"].values() for i in c if i["sentiment"] == "risk"][:15]
    return result


def search(query: str, limit: int = 15) -> List[Dict[str, Any]]:
    region = get_setting("market_region", "IN")
    url = f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=en-{region}&gl={region}&ceid={region}:en"
    def fetch():
        try:
            rows = _parse(url, limit)
            for r in rows:
                r["source"] = "Google News"
            return rows
        except Exception as e:
            log.info("news search failed: %s", redact(str(e))); return None
    return cached("gnews_" + re.sub(r"[^a-z0-9]+", "_", query.lower())[:60], fetch, 900) or []
