"""
Deterministic CLM rules: a threshold audit of live/mock campaigns plus
lifecycle playbook templates. Templates are labelled template=True so the UI
and the agent never present them as observed facts.
"""
from __future__ import annotations
from datetime import datetime
from typing import Any, Dict, List

from .moengage import MoEngageClient, DataUnavailable

BENCHMARKS = {"push": {"ctr_scale": 10.0, "ctr_low": 3.0}, "email": {"ctr_low": 2.0, "conv_high": 4.0}, "delivery_min": 90.0}


class CLMIntelligenceEngine:
    def __init__(self):
        self.moe = MoEngageClient()

    def audit_active_campaigns(self, campaigns: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        from .anomaly.store import normalise_campaign
        audited = []
        for c in campaigns:
            n = normalise_campaign(c)
            ctr = n["ctr"] or 0.0; conv = n["conversion_rate"] or 0.0; delivery = n["delivery_rate"]
            channel = (n["channel"] or "Push")
            verdict, rec, action, score = "HEALTHY", "Maintain current delivery schedule.", "maintain", 80
            if delivery is not None and delivery < BENCHMARKS["delivery_min"]:
                verdict, rec, action, score = "DELIVERABILITY ISSUE", "High bounce / invalid token rate. Purge stale tokens; check opt-in state.", "fix_deliverability", 55
            if channel.lower() == "push":
                if ctr > BENCHMARKS["push"]["ctr_scale"]:
                    verdict, rec, action, score = "SCALE REACH", f"CTR {ctr}% is well above benchmark. Expand to adjacent lookalike criteria with a control group.", "scale", 95
                elif ctr and ctr < BENCHMARKS["push"]["ctr_low"]:
                    verdict, rec, action, score = "CREATIVE FATIGUE", "CTR below 3% benchmark. Refresh copy, test personalised product tags, review send time.", "refresh_copy", 50
            elif channel.lower() == "email":
                if ctr and ctr < BENCHMARKS["email"]["ctr_low"]:
                    verdict, rec, action, score = "LOW ENGAGEMENT", "Click rate under 2%. A/B test subject/preheader; check list hygiene.", "ab_test", 60
                elif conv > BENCHMARKS["email"]["conv_high"]:
                    verdict, rec, action, score = "AUTOMATE", f"Conversion {conv}% is high. Convert this blast into a triggered lifecycle node.", "convert_to_journey", 92
            audited.append({"id": n["campaign_id"], "name": n["campaign_name"], "channel": channel, "ctr": ctr, "conversion_rate": conv, "delivery_rate": delivery,
                            "revenue": n["revenue_generated"] or 0, "sent": n["sent_count"], "verdict": verdict, "action_type": action, "recommendation": rec,
                            "health_score": max(20, min(100, score)), "source": c.get("_source", "unknown")})
        return audited

    def lifecycle_templates(self) -> List[Dict[str, Any]]:
        return [
            {"template": True, "stage_id": "acquired_verified", "stage_name": "Acquired → Verified → Funded", "question": "Do they trust us; what are they afraid of losing?", "intervention": "Trust proof, small-first-deposit framing, remove payment friction", "cadence": "2–3/week"},
            {"template": True, "stage_id": "funded_activated", "stage_name": "Funded → Activated (first trade)", "question": "Do they know the one sensible first action?", "intervention": "One concrete first action in-app; alerts & watchlist as habit tools", "cadence": "3–4/week"},
            {"template": True, "stage_id": "activated_habitual", "stage_name": "Activated → Habitual (2nd trade in 7d)", "question": "Will they come back unprompted?", "intervention": "Habit formation inside 7 days; market-aware nudges with TTL", "cadence": "3–4/week"},
            {"template": True, "stage_id": "core", "stage_name": "Habitual → Core", "question": "What would make them consolidate here?", "intervention": "Depth: more assets/products, fee tiers, status. Message least.", "cadence": "1–2/week"},
            {"template": True, "stage_id": "slipping", "stage_name": "Slipping (−50% vs own baseline)", "question": "What changed for them?", "intervention": "Diagnose the event (loss, failed withdrawal, support) before messaging", "cadence": "1/week"},
            {"template": True, "stage_id": "dormant", "stage_name": "Dormant (30d+, has balance)", "question": "Why did they stop?", "intervention": "Split by cause: market / loss / friction / competitor / life. Reason-specific winback only.", "cadence": "1 per 2 weeks, decaying"},
            {"template": True, "stage_id": "churned", "stage_name": "Churned (90d+ / withdrawn)", "question": "Is there anything honest to say?", "intervention": "Usually suppress.", "cadence": "0–1/quarter"},
        ]

    def get_full_board(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "mode": self.moe.mode}
        try:
            campaigns = self.moe.get_campaigns()
            out["source"] = campaigns[0].get("_source") if campaigns else self.moe.mode
            audited = self.audit_active_campaigns(campaigns)
            out["audited_campaigns"] = audited
            ctrs = [a["ctr"] for a in audited if a["ctr"]]
            dels = [a["delivery_rate"] for a in audited if a["delivery_rate"] is not None]
            out["health_scorecard"] = {
                "average_ctr": round(sum(ctrs) / len(ctrs), 2) if ctrs else None,
                "average_delivery_rate": round(sum(dels) / len(dels), 1) if dels else None,
                "total_tracked_revenue": round(sum(a["revenue"] or 0 for a in audited), 2),
                "active_campaigns_count": len(audited),
                "avg_health_score": round(sum(a["health_score"] for a in audited) / len(audited), 1) if audited else None,
            }
        except DataUnavailable as e:
            out["error"] = str(e); out["audited_campaigns"] = []; out["health_scorecard"] = {}
        try:
            out["segments_count"] = len(self.moe.get_segments())
        except DataUnavailable:
            out["segments_count"] = None
        # plain-language layer: per-channel KPIs, lights + so-what per campaign, 14d sparkline series
        try:
            from .metrics import channel_kpis, lights_from_diagnosis, so_what
            from .anomaly.diagnose import diagnose_campaign
            from .anomaly.store import get_history
            camps = campaigns if "campaigns" in dir() else []
            out["kpis"] = channel_kpis(camps)
            for a in out.get("audited_campaigns", []):
                try:
                    dg = diagnose_campaign(a["id"], self.moe.mode)
                    lights = lights_from_diagnosis(dg) if not dg.get("error") else {}
                    a["lights"] = lights
                    a["so_what"] = so_what(dg, lights) if lights else "No history yet."
                    a["urgency"] = "act_today" if any(v.get("state") == "red" for v in lights.values()) else ("watch" if any(v.get("state") == "amber" for v in lights.values()) else "normal")
                    hist = get_history(a["id"], self.moe.mode, 14)
                    a["spark"] = {"ctr": [h.get("ctr") for h in hist], "delivery_rate": [h.get("delivery_rate") for h in hist], "days": len(hist)}
                    a["delivered"] = a.get("sent") and round(a["sent"] * (a["delivery_rate"] or 0) / 100)
                except Exception:
                    a.setdefault("lights", {}); a.setdefault("so_what", "")
            order = {"act_today": 0, "watch": 1, "normal": 2}
            out["audited_campaigns"].sort(key=lambda a: (order.get(a.get("urgency"), 3), -(a.get("delivered") or 0)))
        except Exception as e:
            out["kpis_error"] = str(e)
        out["lifecycle_templates"] = self.lifecycle_templates()
        return out
