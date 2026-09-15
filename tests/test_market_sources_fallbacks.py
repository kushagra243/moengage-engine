"""Candles for perps no spot exchange lists, and news feeds that stay inside the allowlist."""
from urllib.parse import urlparse


def test_klines_skips_binance_mirrors_on_unlisted_pair_and_uses_the_liquidity_venue(monkeypatch):
    import requests
    from backend.market import sources
    sources._MEM.clear()
    monkeypatch.setattr(sources, "CACHE_DIR", sources.CACHE_DIR + "_test_klines")
    calls = []

    def fake_json(url, params=None, timeout=15.0, headers=None):
        calls.append(url)
        if "binance" in url:
            resp = requests.Response(); resp.status_code = 400
            raise requests.HTTPError("400 Client Error: Bad Request", response=resp)
        raise AssertionError("OKX must not be reached when the venue has candles")

    monkeypatch.setattr(sources, "_json", fake_json)
    monkeypatch.setattr(sources, "_hl_candles", lambda s, i, l: [{"t": 1.0, "o": 1.0, "h": 2.0, "l": 0.5, "c": 1.5, "v": 10.0}])
    rows = sources.klines("HYPEX")
    assert rows and rows[-1]["c"] == 1.5
    assert sum("binance" in u for u in calls) == 1, "a 400 means not listed; the mirrors list the same pairs and only add noise"


def test_klines_caches_an_unlisted_symbol_instead_of_retrying_every_build(monkeypatch):
    import requests
    from backend.market import sources
    sources._MEM.clear()
    monkeypatch.setattr(sources, "CACHE_DIR", sources.CACHE_DIR + "_test_klines_none")
    n = {"calls": 0}

    def fake_json(url, params=None, timeout=15.0, headers=None):
        n["calls"] += 1
        resp = requests.Response(); resp.status_code = 400
        raise requests.HTTPError("400", response=resp)

    monkeypatch.setattr(sources, "_json", fake_json)
    monkeypatch.setattr(sources, "_hl_candles", lambda s, i, l: None)
    assert sources.klines("NOWHERE") == []
    first = n["calls"]
    assert sources.klines("NOWHERE") == [] and n["calls"] == first, "second build is served from cache"


def test_every_news_feed_host_is_on_the_market_allowlist():
    """Bloomberg moved its RSS from feeds.bloomberg.com to www.bloomberg.com/feeds/…; the redirect was blocked by the guard."""
    from backend.market import news
    from backend.security import netguard
    hosts = set(netguard.MARKET_HOSTS)
    for cat, feeds in news.FEEDS.items():
        for f in feeds:
            h = urlparse(f["url"]).hostname
            assert h in hosts, f"{f['name']} ({h}) would be blocked by the market allowlist"
    assert not any("feeds.bloomberg.com" in f["url"] for feeds in news.FEEDS.values() for f in feeds)
