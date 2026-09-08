import sqlite3
import json
import os
from datetime import datetime
from typing import Dict, Any, List, Optional

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "agent.db")

def get_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()

    # Settings table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Daily runs
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS daily_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            trigger_type TEXT DEFAULT 'manual',
            status TEXT DEFAULT 'completed',
            summary TEXT,
            raw_report_json TEXT
        )
    """)

    # Recommended Campaign Ideas
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS campaign_ideas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER,
            title TEXT,
            channel TEXT,
            target_segment TEXT,
            body TEXT,
            cta TEXT,
            expected_impact TEXT,
            status TEXT DEFAULT 'recommended',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (run_id) REFERENCES daily_runs (id)
        )
    """)

    # Segments (generated or imported)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS segments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER,
            name TEXT,
            description TEXT,
            criteria_json TEXT,
            estimated_reach INTEGER DEFAULT 0,
            moengage_segment_id TEXT,
            status TEXT DEFAULT 'draft',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (run_id) REFERENCES daily_runs (id)
        )
    """)

    # Chat messages history
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            role TEXT,
            content TEXT,
            tool_calls TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Journeys table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS journeys (
            id TEXT PRIMARY KEY,
            name TEXT,
            description TEXT,
            entry_trigger TEXT,
            cohort_name TEXT,
            wait_condition TEXT,
            warmup_segment TEXT,
            status TEXT DEFAULT 'Active',
            stats_json TEXT,
            flow_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Default settings if not already present
    default_settings = {
        "moengage_cookies": "",
        "moengage_access_token": "",
        "moengage_refresh_token": "",
        "moengage_region": "dashboard-01.moengage.com",
        "moengage_app_id": "",
        "moengage_db_name": "",
        "moengage_dc": "",
        "moengage_data_api_key": "",
        "moengage_segmentation_key": "",
        "moengage_campaign_key": "",
        "moengage_inform_key": "",
        "llm_provider": "openrouter",
        "llm_base_url": "https://openrouter.ai/api/v1",
        "llm_model": "anthropic/claude-sonnet-4.5",
        "llm_api_key": os.getenv("OPENROUTER_API_KEY", ""),
        "llm_temperature": "0.3",
        "llm_max_tokens": "2000",
        "market_universe": "BTC,ETH,SOL,XRP,BNB,DOGE,ADA,AVAX,LINK,TON,SUI,APT,ARB,OP,NEAR,INJ,SEI,PEPE,WIF,TIA",
        "market_equities": "^GSPC,^NDX,^NSEI,^NSEBANK,^BSESN,COIN,MSTR,HOOD",
        "market_commodities": "GC=F,SI=F,CL=F,BZ=F,NG=F,HG=F",
        "market_macro": "DX-Y.NYB,^TNX,^VIX,INR=X",
        "market_region": "IN",
        "schedule_time": "09:00",
        "schedule_enabled": "true",
        "mock_mode": "true"  # Defaults to true so users have working experience instantly
    }

    for k, v in default_settings.items():
        cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))

    # Seed default mock journey if not present
    seed_default_journey(cursor)

    conn.commit()
    conn.close()

def _secrets():
    from .security.secrets import secret_store, is_secret_key
    return secret_store, is_secret_key

def get_setting(key: str, default: str = "") -> str:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return default
    val = row["value"] if row["value"] is not None else default
    store, is_secret = _secrets()
    if is_secret(key):
        return store.decrypt(val)
    return val

def set_setting(key: str, value: str):
    store, is_secret = _secrets()
    stored = store.encrypt(value) if (is_secret(key) and value) else value
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO settings (key, value, updated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP
    """, (key, stored))
    conn.commit()
    conn.close()
    if is_secret(key):
        from .security import audit
        audit("setting.secret_updated", {"key": key, "cleared": not bool(value)}, actor="user")

def get_all_settings(include_secrets: bool = False) -> Dict[str, str]:
    """Non-secret settings, plus for each secret a boolean '<key>_set'. Raw secrets never leave this function unless include_secrets=True (internal use only)."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT key, value FROM settings")
    rows = cursor.fetchall()
    conn.close()
    store, is_secret = _secrets()
    out: Dict[str, str] = {}
    for row in rows:
        k, v = row["key"], row["value"]
        if is_secret(k):
            if include_secrets:
                out[k] = store.decrypt(v or "")
            out[k + "_set"] = "true" if v else "false"
        else:
            out[k] = v
    return out

def migrate_plaintext_secrets() -> int:
    """Encrypt any legacy plaintext secret values in place. Returns count migrated."""
    store, is_secret = _secrets()
    conn = get_db()
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    n = 0
    for r in rows:
        if is_secret(r["key"]) and r["value"] and not store.is_encrypted(r["value"]):
            conn.execute("UPDATE settings SET value=? WHERE key=?", (store.encrypt(r["value"]), r["key"]))
            n += 1
    conn.commit(); conn.close()
    return n

def save_daily_run(trigger_type: str, summary: str, report_data: Dict[str, Any]) -> int:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO daily_runs (trigger_type, status, summary, raw_report_json)
        VALUES (?, 'completed', ?, ?)
    """, (trigger_type, summary, json.dumps(report_data)))
    run_id = cursor.lastrowid

    # Insert campaign ideas
    for idea in report_data.get("campaign_ideas", []):
        cursor.execute("""
            INSERT INTO campaign_ideas (run_id, title, channel, target_segment, body, cta, expected_impact, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'recommended')
        """, (
            run_id,
            idea.get("title", "Untitled Campaign"),
            idea.get("channel", "Push"),
            idea.get("target_segment", "All Active"),
            idea.get("body", ""),
            idea.get("cta", "Open App"),
            idea.get("expected_impact", "")
        ))

    # Insert segments
    for seg in report_data.get("segments", []):
        criteria_str = json.dumps(seg.get("criteria", {})) if isinstance(seg.get("criteria"), (dict, list)) else str(seg.get("criteria", ""))
        cursor.execute("""
            INSERT INTO segments (run_id, name, description, criteria_json, estimated_reach, status)
            VALUES (?, ?, ?, ?, ?, 'draft')
        """, (
            run_id,
            seg.get("name", "New Segment"),
            seg.get("description", ""),
            criteria_str,
            seg.get("estimated_reach", 0)
        ))

    conn.commit()
    conn.close()
    return run_id

def get_latest_daily_run() -> Optional[Dict[str, Any]]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM daily_runs ORDER BY id DESC LIMIT 1")
    row = cursor.fetchone()
    if not row:
        conn.close()
        return None

    run_id = row["id"]
    cursor.execute("SELECT * FROM campaign_ideas WHERE run_id = ?", (run_id,))
    campaigns = [dict(r) for r in cursor.fetchall()]

    cursor.execute("SELECT * FROM segments WHERE run_id = ?", (run_id,))
    segments = [dict(r) for r in cursor.fetchall()]
    conn.close()

    result = dict(row)
    result["campaign_ideas"] = campaigns
    result["segments"] = segments
    try:
        result["report_data"] = json.loads(row["raw_report_json"])
    except Exception:
        result["report_data"] = {}
    return result

def get_daily_run_history(limit: int = 10) -> List[Dict[str, Any]]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, created_at, trigger_type, status, summary FROM daily_runs ORDER BY id DESC LIMIT ?", (limit,))
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows

def save_chat_message(role: str, content: str, tool_calls: Optional[Any] = None):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO chat_messages (role, content, tool_calls)
        VALUES (?, ?, ?)
    """, (role, content, json.dumps(tool_calls) if tool_calls else None))
    conn.commit()
    conn.close()

def get_chat_history(limit: int = 30) -> List[Dict[str, Any]]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, role, content, tool_calls, created_at FROM chat_messages ORDER BY id ASC LIMIT ?", (limit,))
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows

def clear_chat_history():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM chat_messages")
    conn.commit()
    conn.close()

def update_segment_status(segment_id: int, status: str, moengage_id: Optional[str] = None):
    conn = get_db()
    cursor = conn.cursor()
    if moengage_id:
        cursor.execute("UPDATE segments SET status = ?, moengage_segment_id = ? WHERE id = ?", (status, moengage_id, segment_id))
    else:
        cursor.execute("UPDATE segments SET status = ? WHERE id = ?", (status, segment_id))
    conn.commit()
    conn.close()

def seed_default_journey(cursor):
    cursor.execute("SELECT id FROM journeys WHERE id = 'jny_reactivation_warmup_30d'")
    if cursor.fetchone():
        return

    warmup_schedule = [
        {"day": 1, "pn_number": 1, "timing": "10:00 AM", "title": "We missed you, {{First Name}}! 💫", "body": "Welcome back! Your personal recommendations and saved items are ready.", "cta": "View Saved Items"},
        {"day": 1, "pn_number": 2, "timing": "07:30 PM", "title": "Welcome Back Treat: 15% Off 🎁", "body": "Enjoy 15% off your next order today with code WELCOME15 at checkout.", "cta": "Claim 15% Off"},
        {"day": 2, "pn_number": 1, "timing": "10:30 AM", "title": "Trending Right Now 🔥", "body": "See what community favorites and top-rated items were added this week.", "cta": "Explore Trends"},
        {"day": 2, "pn_number": 2, "timing": "08:00 PM", "title": "Selling Out Fast ⏳", "body": "Items in your preferred categories have limited stock remaining. Don't miss out.", "cta": "Check Stock"},
        {"day": 3, "pn_number": 1, "timing": "11:00 AM", "title": "Handpicked for Your Taste 🎯", "body": "We curated a special collection tailored just for your style preferences.", "cta": "View Selection"},
        {"day": 3, "pn_number": 2, "timing": "07:00 PM", "title": "Free Express Delivery Tonight ⚡️", "body": "Complete any order over $35 tonight and unlock complimentary priority shipping!", "cta": "Shop With Free Ship"},
        {"day": 4, "pn_number": 1, "timing": "10:00 AM", "title": "New in the App 💡", "body": "Faster search, instant order tracking, and member rewards are live in app.", "cta": "Explore Features"},
        {"day": 4, "pn_number": 2, "timing": "08:30 PM", "title": "Members-Only Price Drops 👀", "body": "3 categories you frequently browse just received markdown discounts.", "cta": "See Price Drops"},
        {"day": 5, "pn_number": 1, "timing": "11:30 AM", "title": "Weekend Countdown Perks 🛍️", "body": "Add your favorites to wishlist now to be the first notified of weekend specials.", "cta": "Update Wishlist"},
        {"day": 5, "pn_number": 2, "timing": "07:30 PM", "title": "Double Reward Points Unlocked ⭐", "body": "Earn 2x loyalty reward points on any order placed this evening.", "cta": "Earn 2x Points"},
        {"day": 6, "pn_number": 1, "timing": "10:00 AM", "title": "Weekend Flash Drop 🌟", "body": "Our Saturday drop is here! Exclusive early access for returned VIPs.", "cta": "Shop Saturday Drop"},
        {"day": 6, "pn_number": 2, "timing": "06:30 PM", "title": "Hurry, Weekend Sale Ending Soon 🏷️", "body": "Top deals are moving fast. Finalize your bag before midnight.", "cta": "Review Bag"},
        {"day": 7, "pn_number": 1, "timing": "10:00 AM", "title": "Final Day: Warm-Up Week Finale! 🎉", "body": "It's the last day of your return bonus week! Check your reward balance.", "cta": "View Bonus Balance"},
        {"day": 7, "pn_number": 2, "timing": "09:00 PM", "title": "Last Chance: Reward Credit Expires at Midnight ⏰", "body": "Your $15 welcome return voucher expires at 11:59 PM. Don't leave it on the table!", "cta": "Redeem Before Midnight"}
    ]

    stats = {
        "enrolled_users": 42500,
        "cohort_size": 42500,
        "in_wait_window": 18200,
        "app_opened_yes": 6375,
        "conversion_to_warmup_pct": 15.0,
        "no_activity_exited": 17925,
        "exit_pct": 85.0,
        "warmup_pn_sent": 89250,
        "warmup_avg_ctr": 14.8,
        "repeat_purchase_rate": 8.2
    }

    flow = {
        "trigger": {
            "name": "Entry Condition",
            "rule": "Push Notification Sent >= 1 in last 30 days AND App Opened == 0 in last 30 days",
            "type": "Behavioral Filter"
        },
        "step_1_cohort": {
            "name": "Create Cohort",
            "cohort_name": "Dormant Reachable Users (PN Sent, App Inactive 30D)",
            "action": "Segment Tagging",
            "description": "Isolates users who are reachable via Push token but haven't engaged in 30 days"
        },
        "step_2_wait": {
            "name": "Wait For Activity",
            "event": "App Opened (or any app event)",
            "timeout_days": 14,
            "description": "Monitors if user launches the app within 14 days"
        },
        "step_3_split": {
            "condition": "If App Opens within 14 days",
            "branch_yes": {
                "label": "App Opened: YES",
                "segment_added": "Reactivated Warm-up Cohort",
                "cadence": "2 Push Notifications per day for 1 week (7 days, 14 total messages)",
                "frequency_capping": "Minimum 8 hours interval between daily notifications",
                "schedule": warmup_schedule
            },
            "branch_no": {
                "label": "App Opened: NO",
                "action": "No Activity (Exit Journey)",
                "note": "Mark user as Unresponsive Dormant to suppress fatigue & prevent app uninstalls"
            }
        }
    }

    cursor.execute("""
        INSERT INTO journeys (id, name, description, entry_trigger, cohort_name, wait_condition, warmup_segment, status, stats_json, flow_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'Active', ?, ?)
    """, (
        "jny_reactivation_warmup_30d",
        "Reactivation & Warm-up Flow (Dormant 30D)",
        "Isolates users with PN sent in last 30D but no app opens into a dedicated cohort, monitors for app activity, and activates a 7-day 2PN/day warm-up sequence upon reactivation.",
        "Push sent in last 30D & App not opened in last 30D",
        "Dormant Reachable Users (PN Sent, App Inactive 30D)",
        "Wait till user does any app activity (Max 14d)",
        "Warm-up Segment (2PN/day for 7 days)",
        json.dumps(stats),
        json.dumps(flow)
    ))

def get_journeys() -> List[Dict[str, Any]]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM journeys ORDER BY created_at DESC")
    rows = [dict(r) for r in cursor.fetchall()]
    for r in rows:
        try:
            r["stats"] = json.loads(r["stats_json"])
        except Exception:
            r["stats"] = {}
        try:
            r["flow"] = json.loads(r["flow_json"])
        except Exception:
            r["flow"] = {}
    conn.close()
    return rows

def get_journey(journey_id: str) -> Optional[Dict[str, Any]]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM journeys WHERE id = ?", (journey_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return None
    r = dict(row)
    try:
        r["stats"] = json.loads(r["stats_json"])
    except Exception:
        r["stats"] = {}
    try:
        r["flow"] = json.loads(r["flow_json"])
    except Exception:
        r["flow"] = {}
    conn.close()
    return r

def simulate_journey_run(sample_size: int = 1000) -> Dict[str, Any]:
    """Simulates sample users progressing through the journey for testing"""
    import random
    reactivation_rate = 0.165  # ~16.5% open app
    opened_count = int(sample_size * reactivation_rate)
    exited_count = sample_size - opened_count

    # Generate mock sample traces
    sample_users = [
        {"user_id": f"usr_sim_{random.randint(10000, 99999)}", "pn_sent_30d": True, "app_opened": True, "day_opened": random.randint(1, 5), "action_taken": "Enrolled in Warm-up Cadence (2 PN/day for 7d)"}
        for _ in range(3)
    ] + [
        {"user_id": f"usr_sim_{random.randint(10000, 99999)}", "pn_sent_30d": True, "app_opened": False, "day_opened": None, "action_taken": "No Activity - Exited Journey (Suppressed)"}
        for _ in range(2)
    ]

    return {
        "sample_size": sample_size,
        "entry_filter": "PN sent in last 30D & App not opened in last 30D",
        "cohort_created": "Dormant Reachable Cohort",
        "users_enrolled": sample_size,
        "app_opened_yes": opened_count,
        "app_opened_pct": round(reactivation_rate * 100, 2),
        "warmup_segment_added": opened_count,
        "warmup_notifications_scheduled": opened_count * 14,
        "no_activity_exited": exited_count,
        "exit_pct": round((exited_count / sample_size) * 100, 2),
        "sample_traces": sample_users
    }

