"""
The pilot cohort for Market Alerts 2.0.

The per-user BRD logic (positions, PnL, watchlists, products used) needs exposure data the engine does not have and must
not fetch from MoEngage's per-user endpoints. The data team drops a cohort file instead: opaque MoEngage customer ids plus
exposure. It lives under data/ (never in git), is rejected outright if it carries personal details, and everything that
persists beyond the file — the cap ledger, run records, anything shown to the model — uses a keyed hash of the id.

JSON (preferred): {"users": [...], "whales": [...]} or a bare list of users. Per user:
  user_id                    opaque MoEngage customer_id (required)
  products                   ["futures","us_futures","options","spot"]
  positions                  [{"token","product","side","entry_price","leverage","pnl_pct"}]   pnl_pct preferred when the app has it
  spot_holdings              [{"token","auc_inr","avg_buy_price"}]
  watchlist, traded_tokens   ["BTC", ...]
  futures_screen_views_7d    int          futures_ever   bool
  profitable_trades          [{"kind": "realized_futures"|"unrealized_spot", "token", "profit_pct", "volume_inr"}]
  push_disabled, dnd, liquidated_14d  bool          country  "IN"
CSV: the same columns; lists split on "|"; positions "TOKEN:product:side:entry:leverage:pnl_pct",
holdings "TOKEN:auc_inr:avg_buy_price", trades "kind:TOKEN:profit_pct:volume_inr".
"""
from __future__ import annotations
import csv
import hashlib
import hmac
import io
import json
import os
import re
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from .rules import PRODUCTS

PII_KEYS = re.compile(r"(^|_)(e-?mail|phone|mobile|msisdn|whatsapp|name|first_?name|last_?name|full_?name|user_?name|pan|pan_?number|aadhaa?r|address|pincode|zip|dob|birth|ip|ip_address|device_?id|advertising_?id|gaid|idfa|bank|account_?number|ifsc|upi)($|_)", re.I)
PII_VALUES = [
    ("email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("phone", re.compile(r"(?<![\w.])(?:\+?91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}(?![\w.])")),
    ("pan", re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")),
    ("aadhaar", re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b")),
]
ALLOWED_KEYS = {"user_id", "products", "positions", "spot_holdings", "watchlist", "traded_tokens", "futures_screen_views_7d", "futures_ever",
                "profitable_trades", "push_disabled", "dnd", "liquidated_14d", "country", "token", "product", "side", "entry_price", "leverage",
                "pnl_pct", "auc_inr", "avg_buy_price", "kind", "profit_pct", "volume_inr", "size_usd", "at", "users", "whales", "cohort_name"}


class CohortError(ValueError):
    pass


def data_dir() -> str:
    from .. import database
    d = os.path.join(os.path.dirname(database.DB_PATH), "ma2")
    os.makedirs(d, exist_ok=True)
    return d


def _pepper() -> bytes:
    from ..database import get_setting, set_setting
    k = get_setting("ma2_id_hash_secret", "")
    if not k:
        k = secrets.token_hex(32)
        set_setting("ma2_id_hash_secret", k)
    return k.encode()


def hash_id(user_id: str) -> str:
    return hmac.new(_pepper(), str(user_id).encode(), hashlib.sha256).hexdigest()[:24]


def holdout_bucket(uid_hash: str) -> int:
    return int(uid_hash[:8], 16) % 100


def _tok(x: Any) -> str:
    return str(x or "").strip().upper()


def _bool(x: Any) -> bool:
    return str(x).strip().lower() in ("1", "true", "yes", "y") if not isinstance(x, bool) else x


def _num(x: Any) -> Optional[float]:
    try:
        return float(x) if x not in (None, "") else None
    except (TypeError, ValueError):
        return None


def pii_findings(raw: Any) -> List[str]:
    """Walk the parsed file: forbidden key names anywhere, and personal-detail patterns in any value."""
    found: List[str] = []

    def walk(o: Any, path: str):
        if len(found) >= 10:
            return
        if isinstance(o, dict):
            for k, v in o.items():
                if PII_KEYS.search(str(k)) and str(k) not in ALLOWED_KEYS:
                    found.append(f"column '{k}' looks like personal data")
                walk(v, f"{path}.{k}")
        elif isinstance(o, list):
            for i, v in enumerate(o[:200000]):
                walk(v, f"{path}[{i}]")
        elif isinstance(o, str):
            for label, rx in PII_VALUES:
                if rx.search(o):
                    found.append(f"a value at {path} looks like a {label}")
                    break
    walk(raw, "$")
    return found


def _split(v: Any) -> List[str]:
    if isinstance(v, list):
        return [str(x) for x in v]
    return [x.strip() for x in str(v or "").split("|") if x.strip()]


def _from_csv(text: str) -> Dict[str, Any]:
    rows = list(csv.DictReader(io.StringIO(text)))
    users = []
    for r in rows:
        pos = []
        for p in _split(r.get("positions")):
            f = p.split(":") + [""] * 6
            pos.append({"token": f[0], "product": f[1], "side": f[2] or "long", "entry_price": f[3], "leverage": f[4], "pnl_pct": f[5] or None})
        hold = []
        for h in _split(r.get("spot_holdings")):
            f = h.split(":") + [""] * 3
            hold.append({"token": f[0], "auc_inr": f[1], "avg_buy_price": f[2]})
        trades = []
        for t in _split(r.get("profitable_trades")):
            f = t.split(":") + [""] * 4
            trades.append({"kind": f[0], "token": f[1], "profit_pct": f[2], "volume_inr": f[3]})
        users.append({**{k: v for k, v in r.items() if k not in ("positions", "spot_holdings", "profitable_trades")},
                      "products": _split(r.get("products")), "watchlist": _split(r.get("watchlist")), "traded_tokens": _split(r.get("traded_tokens")),
                      "positions": pos, "spot_holdings": hold, "profitable_trades": trades})
    return {"users": users}


def parse(filename: str, data: bytes) -> Dict[str, Any]:
    text = data.decode("utf-8-sig", errors="replace")
    if filename.lower().endswith(".csv"):
        raw = _from_csv(text)
    else:
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as e:
            raise CohortError(f"not valid JSON: {e.msg} at line {e.lineno}")
        raw = {"users": raw} if isinstance(raw, list) else raw
    if not isinstance(raw, dict) or not isinstance(raw.get("users"), list):
        raise CohortError("expected {\"users\": [...]} or a list of users")
    problems = pii_findings(raw)
    if problems:
        raise CohortError("rejected: the file carries personal data, which must never enter the engine — " + "; ".join(problems[:5]) +
                          ". Send opaque MoEngage customer ids and exposure only.")
    return raw


def normalise(raw: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[str]]:
    users, notes, seen = [], [], set()
    for i, u in enumerate(raw.get("users") or []):
        if not isinstance(u, dict):
            continue
        uid = str(u.get("user_id") or "").strip()
        if not uid:
            notes.append(f"row {i}: no user_id, skipped")
            continue
        if uid in seen:
            notes.append(f"row {i}: duplicate user, kept the first")
            continue
        seen.add(uid)
        products = sorted({str(p).strip().lower() for p in u.get("products") or [] if str(p).strip().lower() in PRODUCTS})
        positions = []
        for p in u.get("positions") or []:
            if not isinstance(p, dict) or not _tok(p.get("token")):
                continue
            prod = str(p.get("product") or "futures").lower()
            side = str(p.get("side") or "long").lower()
            positions.append({"token": _tok(p["token"]), "product": prod if prod in PRODUCTS else "futures", "side": side,
                              "entry_price": _num(p.get("entry_price")), "leverage": _num(p.get("leverage")) or 1.0, "pnl_pct": _num(p.get("pnl_pct")),
                              "key": f"{_tok(p['token'])}|{prod}|{side}"})
        holdings = [{"token": _tok(h.get("token")), "auc_inr": _num(h.get("auc_inr")) or 0.0, "avg_buy_price": _num(h.get("avg_buy_price"))}
                    for h in u.get("spot_holdings") or [] if isinstance(h, dict) and _tok(h.get("token"))]
        trades = [{"kind": str(t.get("kind") or ""), "token": _tok(t.get("token")), "profit_pct": _num(t.get("profit_pct")) or 0.0, "volume_inr": _num(t.get("volume_inr")) or 0.0}
                  for t in u.get("profitable_trades") or [] if isinstance(t, dict)]
        if positions and not products:
            products = sorted({p["product"] for p in positions})
        if holdings and "spot" not in products:
            products = sorted(set(products) | {"spot"})
        users.append({"user_id": uid, "products": products, "positions": positions, "spot_holdings": holdings,
                      "watchlist": sorted({_tok(t) for t in u.get("watchlist") or [] if _tok(t)}), "traded": sorted({_tok(t) for t in u.get("traded_tokens") or [] if _tok(t)}),
                      "futures_screen_views_7d": int(_num(u.get("futures_screen_views_7d")) or 0), "futures_ever": _bool(u.get("futures_ever")) or any(p["product"] in ("futures", "us_futures") for p in positions),
                      "profitable_trades": trades, "country": str(u.get("country") or "").upper() or None,
                      "flags": {"push_disabled": _bool(u.get("push_disabled")), "dnd": _bool(u.get("dnd")), "liquidated_14d": _bool(u.get("liquidated_14d"))}})
    whales = [{"token": _tok(w.get("token")), "product": str(w.get("product") or "futures").lower(), "side": str(w.get("side") or "buy").lower(),
               "size_usd": _num(w.get("size_usd")) or 0.0, "at": w.get("at")} for w in raw.get("whales") or [] if isinstance(w, dict) and _tok(w.get("token"))]
    return users, whales, notes


def summary(users: List[Dict[str, Any]]) -> Dict[str, Any]:
    from collections import Counter
    prod = Counter(p for u in users for p in u["products"])
    tokens = Counter(t for u in users for t in ({p["token"] for p in u["positions"]} | {h["token"] for h in u["spot_holdings"]} | set(u["watchlist"]) | set(u["traded"])))
    return {"users": len(users), "by_product": dict(prod), "with_positions": sum(1 for u in users if u["positions"]),
            "positions": sum(len(u["positions"]) for u in users), "pnl_from_app": sum(1 for u in users for p in u["positions"] if p["pnl_pct"] is not None),
            "pure_spot": sum(1 for u in users if u["products"] == ["spot"] and not u["futures_ever"]),
            "with_profitable_trade": sum(1 for u in users if u["profitable_trades"]),
            "push_disabled": sum(1 for u in users if u["flags"]["push_disabled"] or u["flags"]["dnd"]),
            "liquidated_14d": sum(1 for u in users if u["flags"]["liquidated_14d"]),
            "top_tokens": tokens.most_common(12)}


def save(filename: str, data: bytes, name: str = "", actor: str = "user") -> Dict[str, Any]:
    raw = parse(filename, data)
    users, whales, notes = normalise(raw)
    if not users:
        raise CohortError("no usable users in the file")
    hashes = sorted(hash_id(u["user_id"]) for u in users)
    meta = {"name": name or raw.get("cohort_name") or os.path.splitext(filename)[0], "filename": filename, "uploaded_at": datetime.now(timezone.utc).isoformat(),
            "uploaded_by": actor, "members_digest": hashlib.sha256("".join(hashes).encode()).hexdigest()[:16], "notes": notes[:20], "whales": len(whales),
            "summary": summary(users)}
    with open(os.path.join(data_dir(), "cohort.json"), "w") as f:
        json.dump({"meta": meta, "users": users, "whales": whales}, f)
    with open(os.path.join(data_dir(), "members.json"), "w") as f:
        json.dump(hashes, f)
    with open(os.path.join(data_dir(), "meta.json"), "w") as f:
        json.dump(meta, f)
    from ..security import audit
    audit("ma2.cohort_uploaded", {"name": meta["name"], "users": len(users), "digest": meta["members_digest"]}, actor=actor)
    return meta


def load() -> Optional[Dict[str, Any]]:
    p = os.path.join(data_dir(), "cohort.json")
    if not os.path.exists(p):
        return None
    with open(p) as f:
        d = json.load(f)
    try:
        d["meta"]["age_min"] = round((datetime.now(timezone.utc) - datetime.fromisoformat(d["meta"]["uploaded_at"])).total_seconds() / 60.0, 1)
    except Exception:
        d["meta"]["age_min"] = None
    return d


def meta_only() -> Optional[Dict[str, Any]]:
    """Cohort metadata without reading every user — cheap enough for the header on every state poll."""
    p = os.path.join(data_dir(), "meta.json")
    if not os.path.exists(p):
        return None
    try:
        with open(p) as f:
            m = json.load(f)
        m["age_min"] = round((datetime.now(timezone.utc) - datetime.fromisoformat(m["uploaded_at"])).total_seconds() / 60.0, 1)
        return m
    except Exception:
        return None


def tokens_by_product(users: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    from collections import Counter
    out: Dict[str, Counter] = {p: Counter() for p in PRODUCTS}
    for u in users:
        rel = {p["token"] for p in u["positions"]} | {h["token"] for h in u["spot_holdings"]} | set(u["watchlist"]) | set(u["traded"])
        for p in u["positions"]:
            out[p["product"]][p["token"]] += 3
        for prod in u["products"]:
            for t in rel:
                out[prod][t] += 1
    return {p: [t for t, _ in c.most_common()] for p, c in out.items()}


# ── internal employees for MoEngage Inform (direct transactional sends, no campaign) ──────────────
def save_internal_users(user_ids: List[str], actor: str = "user") -> Dict[str, Any]:
    from ..roles import is_builder
    if is_builder():
        raise CohortError("this machine is a builder: it never holds customer ids; only the operator machine does")
    """Opaque MoEngage customer ids of the employee test cohort. Anything that looks like an email, phone, PAN or Aadhaar
    is refused: Inform addresses users by the id MoEngage already knows, never by a personal detail."""
    clean, bad = [], []
    for u in user_ids or []:
        v = str(u or "").strip()
        if not v:
            continue
        if any(rx.search(v) for _, rx in PII_VALUES):
            bad.append(v[:6] + "…")
            continue
        clean.append(v)
    if bad:
        raise CohortError(f"rejected: {len(bad)} value(s) look like personal details, not customer ids ({', '.join(bad[:3])}). Send MoEngage customer ids only.")
    clean = list(dict.fromkeys(clean))
    if not clean:
        raise CohortError("no customer ids in the list")
    meta = {"count": len(clean), "updated_at": datetime.now(timezone.utc).isoformat(), "updated_by": actor,
            "digest": hashlib.sha256("".join(sorted(hash_id(u) for u in clean)).encode()).hexdigest()[:16]}
    with open(os.path.join(data_dir(), "internal_users.json"), "w") as f:
        json.dump({"meta": meta, "user_ids": clean}, f)
    from ..security import audit
    audit("ma2.internal_users", {"count": len(clean), "digest": meta["digest"]}, actor=actor)
    return meta


def internal_users() -> List[str]:
    p = os.path.join(data_dir(), "internal_users.json")
    if not os.path.exists(p):
        return []
    try:
        with open(p) as f:
            return [str(u) for u in (json.load(f).get("user_ids") or [])]
    except Exception:
        return []


def internal_users_meta() -> Optional[Dict[str, Any]]:
    p = os.path.join(data_dir(), "internal_users.json")
    if not os.path.exists(p):
        return None
    try:
        with open(p) as f:
            return json.load(f).get("meta")
    except Exception:
        return None
