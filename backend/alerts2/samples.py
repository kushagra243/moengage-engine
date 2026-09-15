"""A cohort template the data team can copy. Every id and number here is illustrative, never a real user."""
from __future__ import annotations
from typing import Any, Dict


def cohort_template() -> Dict[str, Any]:
    return {
        "cohort_name": "MA2_pilot_Sep26",
        "_readme": "Opaque MoEngage customer ids only. No names, emails, phones, PAN, Aadhaar, device or bank ids: the upload is rejected if any appear. "
                   "Refresh positions at least hourly while the pilot runs; PnL alerts pause when the file is older than 60 minutes.",
        "users": [
            {"user_id": "moe_cust_0001", "products": ["futures", "spot"], "futures_ever": True, "futures_screen_views_7d": 9,
             "positions": [{"token": "BTC", "product": "futures", "side": "long", "entry_price": 74200, "leverage": 5, "pnl_pct": 18.4}],
             "spot_holdings": [{"token": "ETH", "auc_inr": 42000, "avg_buy_price": 2950}],
             "watchlist": ["SOL", "DOGE"], "traded_tokens": ["BTC", "ETH", "XRP"],
             "profitable_trades": [{"kind": "realized_futures", "token": "BTC", "profit_pct": 14.2, "volume_inr": 25000}], "country": "IN"},
            {"user_id": "moe_cust_0002", "products": ["spot"], "futures_ever": False, "futures_screen_views_7d": 2,
             "spot_holdings": [{"token": "SOL", "auc_inr": 18000, "avg_buy_price": 150}, {"token": "XRP", "auc_inr": 6000, "avg_buy_price": 0.55}],
             "watchlist": ["BTC"], "traded_tokens": ["SOL", "XRP"], "country": "IN"},
            {"user_id": "moe_cust_0003", "products": ["options", "futures"], "futures_ever": True,
             "positions": [{"token": "ETH", "product": "futures", "side": "short", "entry_price": 3100, "leverage": 3}],
             "watchlist": ["BTC", "ETH"], "traded_tokens": ["ETH"], "liquidated_14d": False, "push_disabled": False, "country": "IN"},
        ],
        "whales": [{"token": "ETH", "product": "futures", "side": "buy", "size_usd": 3500000, "at": "2026-09-15T10:05:00+05:30"}],
    }
