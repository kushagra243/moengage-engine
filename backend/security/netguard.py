"""
Outbound network policy.

Every module obtains a session via guarded_session(scope). Each scope has a
fixed allowlist of hostnames; a request to any other host raises
NetworkPolicyError before a socket is opened. Redirects are re-checked.
Environment proxies are ignored (trust_env=False) so a poisoned HTTPS_PROXY
cannot siphon traffic. Only https is allowed except for explicit loopback LLM
servers (Ollama / LM Studio).
"""
from __future__ import annotations
import logging
import os
import time
from typing import Dict, Iterable, Optional
from urllib.parse import urlsplit

import requests
from requests.adapters import HTTPAdapter

from .redact import redact

log = logging.getLogger("moengage.net")

LOOPBACK = {"127.0.0.1", "localhost", "::1"}


class NetworkPolicyError(RuntimeError):
    pass


def _host_allowed(host: str, allow: Iterable[str]) -> bool:
    host = (host or "").lower().rstrip(".")
    for rule in allow:
        rule = rule.lower()
        if rule.startswith("*."):
            if host == rule[2:] or host.endswith(rule[1:]):
                return True
        elif host == rule:
            return True
    return False


class GuardedSession(requests.Session):
    def __init__(self, scope: str, allow_hosts: Iterable[str], allow_http_loopback: bool = False, default_timeout: float = 15.0):
        super().__init__()
        self.scope = scope
        self.allow_hosts = list(allow_hosts)
        self.allow_http_loopback = allow_http_loopback
        self.default_timeout = default_timeout
        self.trust_env = False  # ignore HTTP(S)_PROXY, .netrc, etc.
        self.max_redirects = 3
        adapter = HTTPAdapter(pool_connections=4, pool_maxsize=8, max_retries=0)
        self.mount("https://", adapter)
        self.mount("http://", adapter)

    def _check(self, url: str) -> None:
        parts = urlsplit(url)
        host = parts.hostname or ""
        if parts.scheme == "http":
            if not (self.allow_http_loopback and host in LOOPBACK):
                raise NetworkPolicyError(f"[{self.scope}] plaintext http blocked: {host}")
        elif parts.scheme != "https":
            raise NetworkPolicyError(f"[{self.scope}] scheme not allowed: {parts.scheme}")
        if parts.username or parts.password:
            raise NetworkPolicyError(f"[{self.scope}] credentials in URL are not allowed")
        if not _host_allowed(host, self.allow_hosts):
            raise NetworkPolicyError(f"[{self.scope}] host not in allowlist: {host}")

    def request(self, method, url, *args, **kwargs):  # type: ignore[override]
        self._check(url)
        kwargs.setdefault("timeout", self.default_timeout)
        # We handle redirects manually so each hop is policy-checked.
        follow = kwargs.pop("allow_redirects", True)
        kwargs["allow_redirects"] = False
        started = time.time()
        resp = super().request(method, url, *args, **kwargs)
        hops = 0
        while follow and resp.is_redirect and hops < self.max_redirects:
            nxt = resp.headers.get("location", "")
            if not nxt:
                break
            nxt = requests.compat.urljoin(url, nxt)
            self._check(nxt)
            hops += 1
            url = nxt
            resp = super().request("GET" if resp.status_code in (301, 302, 303) else method, url, allow_redirects=False, timeout=kwargs.get("timeout"))
        try:
            p = urlsplit(url)
            log.info("outbound scope=%s %s %s%s -> %s %dB %.0fms", self.scope, method.upper(), p.netloc, redact(p.path), resp.status_code, len(resp.content or b""), (time.time() - started) * 1000)
        except Exception:
            pass
        return resp


# ── scope definitions ─────────────────────────────────────────────────────────
MOENGAGE_HOSTS = ["*.moengage.com"]
MARKET_HOSTS = [
    "api.hyperliquid.xyz", "api.coingecko.com", "api.binance.com", "fapi.binance.com", "www.okx.com", "api.coindcx.com", "api.coincap.io", "rest.coincap.io",
    "query1.finance.yahoo.com", "query2.finance.yahoo.com", "stooq.com",
    "api.alternative.me", "nfs.faireconomy.media",
    "news.google.com", "feeds.feedburner.com", "www.coindesk.com", "coindesk.com",
    "cointelegraph.com", "www.theblock.co", "decrypt.co", "www.cnbc.com", "search.cnbc.com",
    "feeds.marketwatch.com", "feeds.content.dowjones.io", "www.investing.com", "economictimes.indiatimes.com",
    "www.moneycontrol.com", "feeds.a.dj.com", "finance.yahoo.com", "feeds.bloomberg.com",
    "www.alphavantage.co", "finnhub.io", "newsapi.org", "cryptopanic.com", "api.polygon.io",
    "www.ft.com", "data-api.binance.vision", "api.binance.us", "api.coinpaprika.com", "cdn.cboe.com", "www.nseindia.com", "fred.stlouisfed.org",
    "api.frankfurter.app", "open.er-api.com", "production.dataviz.cnn.io", "www.livemint.com", "www.business-standard.com", "www.sec.gov", "www.sebi.gov.in", "www.rbi.org.in",
    "feeds.finance.yahoo.com", "www.nasdaq.com", "api.nasdaq.com", "feeds.bbci.co.uk",
    "api.dexscreener.com", "api.geckoterminal.com",          # web3: trending pools / token profiles (public, keyless)
    "api.india.delta.exchange", "api.delta.exchange", "api.wazirx.com", "api.bybit.com",   # competitive intelligence: public tickers (keyless)
    "api.coinmarketcap.com",                                                                 # CMC public data-api: exchange market pairs / listings (keyless)
    "eapi.binance.com", "api.bitget.com", "www.deribit.com",                                 # category benchmarks: Binance options, Bitget futures, Deribit options (keyless)
]

_sessions: Dict[str, GuardedSession] = {}


def _llm_hosts() -> list:
    """LLM allowlist is exactly the configured base_url host (default OpenRouter)."""
    from ..database import get_setting  # local import to avoid cycles
    base = get_setting("llm_base_url", "https://openrouter.ai/api/v1")
    host = urlsplit(base).hostname or "openrouter.ai"
    return [host]


def guarded_session(scope: str, extra_hosts: Optional[Iterable[str]] = None, fresh: bool = False) -> GuardedSession:
    """
    scope: 'moengage' | 'llm' | 'market'
    Sessions are cached per scope except when fresh=True (used when credentials
    change so stale cookies are dropped).
    """
    if scope == "moengage":
        hosts = list(MOENGAGE_HOSTS)
        s = GuardedSession(scope, hosts, allow_http_loopback=False, default_timeout=20.0)
        return s  # never cached: cookies are attached per client instance
    if scope == "llm":
        hosts = _llm_hosts()
        # always rebuilt: base_url may change in settings
        return GuardedSession(scope, hosts, allow_http_loopback=True, default_timeout=120.0)
    if scope == "market":
        hosts = list(MARKET_HOSTS) + list(extra_hosts or [])
        if fresh or scope not in _sessions:
            _sessions[scope] = GuardedSession(scope, hosts, allow_http_loopback=False, default_timeout=15.0)
        return _sessions[scope]
    raise ValueError(f"unknown network scope: {scope}")
