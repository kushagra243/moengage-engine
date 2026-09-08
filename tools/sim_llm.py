#!/usr/bin/env python3
"""
Local LLM simulator (OpenAI-compatible) for offline demos of the agent loop.

It is NOT a language model. It is a scripted "marketer" that exercises the
real tool-calling protocol end to end: it asks for status, the programme
audit, anomalies and market hooks, then files a segment + a campaign proposal
with a complete goal brief, and finally writes a structured answer built
from the actual tool results it received. Every reply is labelled
"[SIMULATED MODEL]" so nobody mistakes it for judgement.

Run:  .venv/bin/python tools/sim_llm.py            (listens on 127.0.0.1:8791)
Then: ./cli.py set llm_provider openai_compatible
      ./cli.py set llm_base_url http://127.0.0.1:8791/v1
      ./cli.py set llm_model sim/marketer-v1
      ./cli.py set-key llm --value sim
"""
import json
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8791
MODEL = "sim/marketer-v1"

PLAN = ["get_status", "clm_program_audit", "anomaly_report", "market_campaign_hooks"]


def _tool_results(messages):
    """Collect tool results in order → {name: parsed json or text}."""
    out = {}
    names = {}
    for m in messages:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            for tc in m["tool_calls"]:
                names[tc["id"]] = tc["function"]["name"]
    for m in messages:
        if m.get("role") == "tool":
            raw = m.get("content", "")
            body = re.sub(r"^<tool_data[^>]*>\n?|\n?</tool_data>$", "", raw.strip())
            try:
                out[names.get(m.get("tool_call_id"), m.get("name"))] = json.loads(body)
            except Exception:
                out[names.get(m.get("tool_call_id"), m.get("name"))] = body
    return out


def _call(i, name, args):
    return {"id": f"sim_{i}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def _d(x):
    return x if isinstance(x, dict) else {}


def _pick_actions(r, user_text):
    """Derive a segment + campaign proposal from real tool data."""
    an = _d(r.get("anomaly_report"))
    hooks = _d(r.get("market_campaign_hooks")).get("hooks") or []
    audit = _d(r.get("clm_program_audit"))
    regime = _d(r.get("market_campaign_hooks")).get("regime", "unknown")
    worst = next((a for a in an.get("anomalies", []) if a.get("impact") == "bad"), None)
    gap = (audit.get("uncovered_transitions") or ["Dormant → Activated"])[0]
    hook = next((h for h in hooks if h.get("angle") not in ("suppression",)), None)
    seg = {
        "name": "Market-dormant returners (30–90d, no loss event)",
        "description": "Users with no trade in 30–90 days, balance > 0, no liquidation or large realised loss in last 90d, push opt-in.",
        "criteria": {"last_trade_days_ago": {"gte": 30, "lte": 90}, "balance_usd": {"gt": 0}, "liquidation_90d": {"eq": 0},
                     "realised_loss_90d_pct": {"lt": 20}, "push_opt_in": True, "exclude": ["kyc_pending", "support_ticket_open"]},
        "rationale": f"Programme audit shows '{gap}' has no standing campaign; regime is {regime}, when market-dormant users return on their own if invited without pressure.",
        "estimated_reach": 42000,
    }
    camp = {
        "name": "CLM_Dormant_MarketReturn_Recap",
        "channel": "push",
        "target_segment": seg["name"],
        "variants": [
            {"label": "A · fact + tool", "title": "Your watchlist moved this week", "body": "3 assets you follow moved more than 5%. See the breakdown and set an alert.", "cta": "Open watchlist"},
            {"label": "B · portfolio", "title": "Your portfolio, this week", "body": "A 30-second breakdown of what changed while you were away.", "cta": "See breakdown"},
        ],
        "rationale": f"Closes the '{gap}' gap with a fact-plus-tool angle allowed in the {regime} regime; hook {hook['id'] if hook else 'n/a'}.",
        "goal": {"transition": "dormant_activated",
                 "hypothesis": "If we show market-dormant users a verifiable recap of assets they follow within 3h of a move, reactivation_rate_14d rises 2 pts because the blocker is relevance, not intent",
                 "primary_kpi": "reactivation_rate_14d", "target": "baseline 6.0% → 8.0%", "guardrail_metric": "uninstall_rate_14d",
                 "control_group_pct": 20, "measurement_window_days": 14,
                 "kill_criteria": "uninstall_rate_14d > baseline + 0.3 pts at any check; reactivation below baseline at day 7 with n ≥ required",
                 "suppressions": ["loss-dormant", "friction-dormant", "regulatory/security headline in last 24h", "over global cap", "DND"]},
        "exclusions": ["liquidation_90d > 0", "support_ticket_open", "kyc_pending"],
        "frequency_cap": "1 per 14 days in this programme; counts toward global cap",
        "ttl_hours": 4,
        "market_hook_id": hook["id"] if hook else None,
    }
    return worst, seg, camp, gap, regime


def _final(r, user_text):
    st = _d(r.get("get_status")); an = _d(r.get("anomaly_report")); audit = _d(r.get("clm_program_audit"))
    mk = _d(r.get("market_campaign_hooks")); ps = _d(r.get("propose_segment")); pc = _d(r.get("propose_campaign"))
    mode = st.get("mode", "?")
    lines = [f"**[SIMULATED MODEL — scripted demo, not judgement]** Data mode: `{mode}`" + (" — every number below is simulated demo data." if mode == "mock" else "."), ""]
    lines += ["### Programme", f"- {audit.get('verdict', 'n/a')}", f"- Uncovered transitions: {', '.join(audit.get('uncovered_transitions') or []) or 'none'}", f"- Broadcast share: {audit.get('broadcast_share_pct', '?')}%", ""]
    lines += ["### Anomalies (vs each campaign's own history)", "| Sev | Campaign | Metric | Today | Baseline | Method |", "|---|---|---|---|---|---|"]
    for a in (an.get("anomalies") or [])[:6]:
        lines.append(f"| {a['severity']} | {a.get('campaign_name')} | {a['metric']} | {a['value']:g} | {a['baseline']:g} | {a['method']} |")
    if not an.get("anomalies"):
        lines.append("| — | no anomalies | | | | |")
    lines += ["", "### Market", f"- Regime: **{mk.get('regime')}** · allowed angles: {', '.join((mk.get('angle_policy') or {}).get('prefer') or [])}",
              f"- Blocked angles: {', '.join((mk.get('angle_policy') or {}).get('block') or []) or 'none'}", f"- {len(mk.get('hooks') or [])} hooks available; first: {((mk.get('hooks') or [{}])[0]).get('trigger', 'n/a')}", ""]
    lines += ["### Queued for your approval"]
    if ps.get("proposal_id"):
        lines.append(f"- Segment proposal **#{ps['proposal_id']}** — {ps.get('note', '')}")
    if pc.get("proposal_id"):
        lines.append(f"- Campaign draft proposal **#{pc['proposal_id']}** — goal: dormant_activated · KPI reactivation_rate_14d · 20% holdout · 14-day window · TTL 4h")
        if pc.get("brief_warnings"):
            lines.append(f"  - brief warnings: {'; '.join(pc['brief_warnings'])}")
    elif pc.get("error"):
        lines.append(f"- Campaign proposal was refused by the brief gate: {pc.get('problems')}")
    lines += ["", "### Prioritised actions", "1. Approve or reject the two proposals in the Approvals tab (each shows the exact request).",
              "2. Investigate the critical anomalies above before any new send to those audiences.",
              "3. Keep acquisition/upsell sends on hold while risk-flagged headlines are live."]
    return "\n".join(lines)


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        if self.path.endswith("/models"):
            return self._send({"data": [{"id": MODEL, "name": "Simulated marketer (scripted)", "context_length": 128000, "supported_parameters": ["tools"]}]})
        self._send({"error": "not found"}, 404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0)); req = json.loads(self.rfile.read(n) or b"{}")
        msgs = req.get("messages", []); tools = {t["function"]["name"] for t in req.get("tools") or []}
        user_text = next((m["content"] for m in reversed(msgs) if m.get("role") == "user" and isinstance(m.get("content"), str)), "")
        done = _tool_results(msgs)
        # probe / no tools → plain answer
        if not tools or "single word" in user_text.lower():
            return self._send(self._resp({"role": "assistant", "content": "ready"}, "stop"))
        # daily brief request → answer as JSON after reading context
        if user_text.startswith("Produce today's CLM brief"):
            ctx = {}
            try:
                ctx = json.loads(user_text[user_text.index("Context:") + 8:])
            except Exception:
                pass
            if "rule_based_audit" not in done:
                return self._send(self._resp({"role": "assistant", "content": None, "tool_calls": [_call(0, "rule_based_audit", {})]}, "tool_calls"))
            ra = _d(done.get("rule_based_audit"))
            bad = [a for a in (ra.get("audit") or []) if a.get("health_score", 100) < 70]
            brief = {
                "executive_summary": f"[SIMULATED MODEL] {ctx.get('anomaly_counts', {}).get('campaigns_evaluated', 0)} campaigns evaluated; {ctx.get('anomaly_counts', {}).get('critical', 0)} critical anomalies. Market regime per narrative below; sends should follow the angle policy.",
                "top_insights": [f"{a.get('campaign')}: {a.get('metric')} {a.get('value')} vs baseline {a.get('baseline')}" for a in (ctx.get("anomalies") or [])[:3]] or ["No anomalies against baseline today."],
                "critical_alerts": [{"campaign": a["name"], "issue": a["verdict"], "action": a["recommendation"]} for a in bad][:3],
                "recommended_actions": [{"title": h.get("id"), "segment": (h.get("segments") or ["-"])[0], "channel": h.get("channel"), "angle": h.get("angle"), "timing": h.get("timing"), "kpi": "per hook", "why": h.get("trigger")} for h in (ctx.get("hooks") or [])[:3]],
                "market_note": (ctx.get("market_narrative") or "")[:300],
                "suppressions": ["loss-dormant", "friction-dormant", "regulatory headline hold"],
            }
            return self._send(self._resp({"role": "assistant", "content": json.dumps(brief)}, "stop"))
        # scripted plan for chat
        for i, name in enumerate(PLAN):
            if name in tools and name not in done:
                return self._send(self._resp({"role": "assistant", "content": None, "tool_calls": [_call(i, name, {})]}, "tool_calls"))
        wants_action = bool(re.search(r"propos|draft|segment|campaign|reactivat|hook", user_text, re.I))
        if wants_action and "propose_segment" in tools and "propose_segment" not in done:
            _, seg, camp, _, _ = _pick_actions(done, user_text)
            return self._send(self._resp({"role": "assistant", "content": None, "tool_calls": [_call(10, "propose_segment", seg), _call(11, "propose_campaign", camp)]}, "tool_calls"))
        return self._send(self._resp({"role": "assistant", "content": _final(done, user_text)}, "stop"))

    def _resp(self, msg, fin):
        return {"id": "sim", "object": "chat.completion", "model": MODEL, "choices": [{"index": 0, "message": msg, "finish_reason": fin}],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0}}


if __name__ == "__main__":
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    print(f"simulated LLM on http://127.0.0.1:{PORT}/v1  (model {MODEL})  — scripted demo, not a real model")
    srv.serve_forever()
