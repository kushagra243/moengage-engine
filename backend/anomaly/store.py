"""
Daily campaign metric snapshots (one row per campaign per day).
Source is recorded ('live' | 'mock' | 'import') so mock data never pollutes a
live baseline: detection only compares rows with the same source.
"""
from __future__ import annotations
import json
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from ..database import get_db

METRICS = ["sent_count", "delivered_count", "delivery_rate", "opened_count", "ctr", "conversions", "conversion_rate", "revenue_generated"]


def init_anomaly_tables() -> None:
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS campaign_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_date TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'live',
            campaign_id TEXT NOT NULL,
            campaign_name TEXT,
            channel TEXT,
            status TEXT,
            sent_count REAL, delivered_count REAL, delivery_rate REAL,
            opened_count REAL, ctr REAL, conversions REAL, conversion_rate REAL,
            revenue_generated REAL,
            raw_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(snapshot_date, source, campaign_id)
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_snap_campaign ON campaign_snapshots(campaign_id, source, snapshot_date)")
    c.execute("""
        CREATE TABLE IF NOT EXISTS anomaly_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            snapshot_date TEXT, source TEXT, campaign_id TEXT, campaign_name TEXT,
            metric TEXT, value REAL, baseline REAL, score REAL, method TEXT,
            severity TEXT, direction TEXT, message TEXT, acknowledged INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()


def _num(v) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def normalise_campaign(c: Dict[str, Any]) -> Dict[str, Any]:
    """Map heterogeneous campaign dicts (mock, dashboard, public API) to a flat metric row."""
    sent = _num(c.get("sent_count") or c.get("sent") or c.get("attempted") or c.get("total_sent"))
    delivered = _num(c.get("delivered_count") or c.get("delivered") or c.get("impressions"))
    opened = _num(c.get("opened_count") or c.get("opened") or c.get("opens") or c.get("clicked") or c.get("clicks"))
    conv = _num(c.get("conversions") or c.get("conversion_count") or c.get("converted"))
    rev = _num(c.get("revenue_generated") or c.get("revenue") or c.get("total_revenue"))
    dr = _num(c.get("delivery_rate"))
    if dr is None and sent and delivered is not None and sent > 0:
        dr = round(100.0 * delivered / sent, 3)
    ctr = _num(c.get("ctr") or c.get("click_rate") or c.get("open_rate"))
    if ctr is None and delivered and opened is not None and delivered > 0:
        ctr = round(100.0 * opened / delivered, 3)
    cr = _num(c.get("conversion_rate"))
    if cr is None and delivered and conv is not None and delivered > 0:
        cr = round(100.0 * conv / delivered, 3)
    return {
        "campaign_id": str(c.get("id") or c.get("campaign_id") or c.get("_id") or c.get("name")),
        "campaign_name": c.get("name") or c.get("campaign_name") or "",
        "channel": c.get("channel") or c.get("channel_type") or c.get("platform") or "",
        "status": c.get("status") or c.get("state") or "",
        "sent_count": sent, "delivered_count": delivered, "delivery_rate": dr,
        "opened_count": opened, "ctr": ctr, "conversions": conv, "conversion_rate": cr,
        "revenue_generated": rev,
    }


def record_snapshot(campaigns: List[Dict[str, Any]], source: str = "live", snapshot_date: Optional[str] = None) -> Dict[str, Any]:
    init_anomaly_tables()
    snapshot_date = snapshot_date or date.today().isoformat()
    conn = get_db()
    c = conn.cursor()
    n = 0
    for camp in campaigns:
        row = normalise_campaign(camp)
        c.execute("""
            INSERT INTO campaign_snapshots
              (snapshot_date, source, campaign_id, campaign_name, channel, status,
               sent_count, delivered_count, delivery_rate, opened_count, ctr, conversions, conversion_rate, revenue_generated, raw_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(snapshot_date, source, campaign_id) DO UPDATE SET
              campaign_name=excluded.campaign_name, channel=excluded.channel, status=excluded.status,
              sent_count=excluded.sent_count, delivered_count=excluded.delivered_count, delivery_rate=excluded.delivery_rate,
              opened_count=excluded.opened_count, ctr=excluded.ctr, conversions=excluded.conversions,
              conversion_rate=excluded.conversion_rate, revenue_generated=excluded.revenue_generated, raw_json=excluded.raw_json,
              created_at=CURRENT_TIMESTAMP
        """, (snapshot_date, source, row["campaign_id"], row["campaign_name"], row["channel"], row["status"],
              row["sent_count"], row["delivered_count"], row["delivery_rate"], row["opened_count"], row["ctr"],
              row["conversions"], row["conversion_rate"], row["revenue_generated"], json.dumps(camp, default=str)[:20000]))
        n += 1
    conn.commit()
    conn.close()
    return {"snapshot_date": snapshot_date, "source": source, "campaigns_recorded": n}


def get_history(campaign_id: str, source: str = "live", days: int = 60) -> List[Dict[str, Any]]:
    init_anomaly_tables()
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        SELECT * FROM campaign_snapshots WHERE campaign_id=? AND source=?
        ORDER BY snapshot_date DESC LIMIT ?
    """, (campaign_id, source, days))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    rows.reverse()
    for r in rows:
        r.pop("raw_json", None)
    return rows


def list_tracked_campaigns(source: str = "live") -> List[Dict[str, Any]]:
    init_anomaly_tables()
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        SELECT campaign_id, MAX(campaign_name) AS campaign_name, MAX(channel) AS channel,
               COUNT(*) AS days, MIN(snapshot_date) AS first_seen, MAX(snapshot_date) AS last_seen
        FROM campaign_snapshots WHERE source=? GROUP BY campaign_id ORDER BY last_seen DESC
    """, (source,))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def snapshot_count(source: str = "live") -> int:
    init_anomaly_tables()
    conn = get_db()
    n = conn.execute("SELECT COUNT(DISTINCT snapshot_date) FROM campaign_snapshots WHERE source=?", (source,)).fetchone()[0]
    conn.close()
    return int(n or 0)


def save_events(events: List[Dict[str, Any]]) -> None:
    if not events:
        return
    conn = get_db()
    c = conn.cursor()
    for e in events:
        # de-dupe: same campaign/metric/date/method
        c.execute("""
            SELECT id FROM anomaly_events WHERE snapshot_date=? AND source=? AND campaign_id=? AND metric=? AND method=?
        """, (e["snapshot_date"], e["source"], e["campaign_id"], e["metric"], e["method"]))
        if c.fetchone():
            continue
        c.execute("""
            INSERT INTO anomaly_events (snapshot_date, source, campaign_id, campaign_name, metric, value, baseline, score, method, severity, direction, message)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """, (e["snapshot_date"], e["source"], e["campaign_id"], e.get("campaign_name"), e["metric"], e["value"], e["baseline"],
              e["score"], e["method"], e["severity"], e["direction"], e["message"]))
    conn.commit()
    conn.close()


def recent_events(limit: int = 50, source: Optional[str] = None) -> List[Dict[str, Any]]:
    init_anomaly_tables()
    conn = get_db()
    c = conn.cursor()
    if source:
        c.execute("SELECT * FROM anomaly_events WHERE source=? ORDER BY detected_at DESC LIMIT ?", (source, limit))
    else:
        c.execute("SELECT * FROM anomaly_events ORDER BY detected_at DESC LIMIT ?", (limit,))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows
