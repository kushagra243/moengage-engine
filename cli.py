#!/usr/bin/env python3
import sys
import os
import json
import argparse
from datetime import datetime

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)

from backend.database import init_db, get_all_settings, set_setting, get_journeys
from backend.moengage_client import MoEngageClient
from backend.clm_intelligence import CLMIntelligenceEngine
from backend.local_brain import LocalIntelligenceBrain

# Initialize database
init_db()

# Terminal ANSI Color Helpers
class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"

def print_banner():
    print(f"{C.BOLD}{C.CYAN}========================================================================{C.RESET}")
    print(f"{C.BOLD}{C.CYAN}       MOENGAGE AUTONOMOUS AGENT (ANTIGRAVITY LOCAL ENGINE)             {C.RESET}")
    print(f"{C.DIM}          100% Local • Zero External API Keys • Session Cookie Powered  {C.RESET}")
    print(f"{C.BOLD}{C.CYAN}========================================================================{C.RESET}\n")

def cmd_status(args):
    print_banner()
    moe = MoEngageClient()
    status = moe.verify_session()
    settings = get_all_settings()

    print(f"{C.BOLD}--- MoEngage Connection Status ---{C.RESET}")
    if status.get("valid"):
        mode_label = f"{C.YELLOW}[MOCK/DEMO MODE]{C.RESET}" if status.get("mode") == "mock" else f"{C.GREEN}[LIVE CONNECTED]{C.RESET}"
        print(f"Status:      {mode_label}")
        print(f"User:        {C.BOLD}{status.get('user')}{C.RESET}")
        print(f"Workspace:   {status.get('workspace')}")
        print(f"Cluster:     {status.get('region')}")
        print(f"Message:     {status.get('message')}")
    else:
        print(f"Status:      {C.RED}[DISCONNECTED / EXPIRED]{C.RESET}")
        print(f"Message:     {status.get('message')}")
        print(f"\n{C.YELLOW}💡 To configure your session cookies:{C.RESET}")
        print(f"   ./cli.py set-cookies \"<paste_your_cookie_string>\"")

    print(f"\n{C.BOLD}--- AI Engine Status ---{C.RESET}")
    print(f"AI Brain:    {C.GREEN}Antigravity Local Engine (Active, No API Key Needed){C.RESET}")
    print(f"Environment: Local SQLite ({os.path.join(PROJECT_ROOT, 'data', 'agent.db')})")

def cmd_campaigns(args):
    print_banner()
    moe = MoEngageClient()
    campaigns = moe.get_campaigns(channel_filter=args.channel, status_filter=args.status)

    print(f"{C.BOLD}MoEngage Campaigns ({len(campaigns)} Found):{C.RESET}\n")
    header = f"{'CAMPAIGN NAME':<32} {'CHANNEL':<8} {'CTR':<8} {'DELIVERY':<10} {'CONVERSIONS':<12} {'REVENUE':<10}"
    print(f"{C.DIM}{header}{C.RESET}")
    print("-" * 84)

    for c in campaigns:
        name = c['name'][:30]
        chan = c['channel']
        ctr = f"{c['ctr']}%"
        deliv = f"{c['delivery_rate']}%"
        conv = f"{c.get('conversions', 0):,}"
        rev = f"${c.get('revenue_generated', 0):,.0f}"

        # Color code CTR
        ctr_color = C.GREEN if c['ctr'] >= 8.0 else (C.YELLOW if c['ctr'] >= 3.0 else C.RED)
        print(f"{C.BOLD}{name:<32}{C.RESET} {chan:<8} {ctr_color}{ctr:<8}{C.RESET} {deliv:<10} {conv:<12} {C.GREEN}{rev:<10}{C.RESET}")

def cmd_segments(args):
    print_banner()
    moe = MoEngageClient()
    segments = moe.get_segments()

    print(f"{C.BOLD}MoEngage Customer Segments ({len(segments)} Active):{C.RESET}\n")
    for idx, s in enumerate(segments, 1):
        reach = f"~{s.get('estimated_reach', 0):,} users" if s.get('estimated_reach') else "Dynamic"
        print(f"{C.CYAN}{idx}. {s['name']}{C.RESET} ({C.YELLOW}{reach}{C.RESET}) [{s.get('type', 'Segment')}]")
        print(f"   {C.DIM}Description:{C.RESET} {s.get('description', 'N/A')}")
        crit = s.get('criteria', {})
        crit_str = json.dumps(crit) if isinstance(crit, dict) else str(crit)
        print(f"   {C.DIM}Criteria:{C.RESET}    {crit_str}\n")

def cmd_create_segment(args):
    print_banner()
    name = args.name.strip()
    desc = args.description.strip() if args.description else f"Created via Antigravity CLI for {name}"
    
    criteria = {}
    if args.criteria:
        try:
            criteria = json.loads(args.criteria)
        except Exception:
            criteria = {"filter_text": args.criteria}
    else:
        # Default smart criteria
        criteria = {
            "event_filter": "Added to Cart >= 1 in last 24 hours",
            "exclusion_filter": "Purchase Completed >= 1 in last 24 hours"
        }

    print(f"Creating Segment in MoEngage...")
    print(f"• Name:        {C.BOLD}{name}{C.RESET}")
    print(f"• Description: {desc}")
    print(f"• Criteria:    {json.dumps(criteria, indent=2)}")

    moe = MoEngageClient()
    result = moe.create_segment(name=name, description=desc, criteria=criteria)

    if result.get("success"):
        print(f"\n{C.GREEN}✅ SUCCESS: Segment '{name}' successfully registered!{C.RESET}")
        print(f"Segment ID: {C.CYAN}{result.get('segment_id')}{C.RESET}")
        print(f"Status:     {result.get('status')}")
    else:
        print(f"\n{C.RED}❌ FAILED: {result.get('message', 'Unknown error')}{C.RESET}")

def cmd_clm_board(args):
    print_banner()
    engine = CLMIntelligenceEngine()
    board = engine.get_full_board()
    score = board.get("health_scorecard", {})

    print(f"{C.BOLD}--- 📈 CLM WAR ROOM SCORECARD ---{C.RESET}")
    print(f"Retention Health Index:     {C.GREEN}{score.get('overall_retention_index')} / 100{C.RESET}")
    print(f"Average Campaign CTR:       {C.CYAN}{score.get('average_ctr')}%{C.RESET}")
    print(f"Push Deliverability:        {C.GREEN}{score.get('average_delivery_rate')}%{C.RESET}")
    print(f"Tracked Attributed Revenue: {C.GREEN}${score.get('total_tracked_revenue', 0):,.2f}{C.RESET}")
    print(f"Unlocked GMV Opportunity:   {C.YELLOW}+${score.get('unlocked_gmv_potential', 0):,.2f}{C.RESET}")
    print(f"Lifecycle Automation:       {C.MAGENTA}{score.get('clm_coverage_pct')}% of stages active{C.RESET}\n")

    print(f"{C.BOLD}--- 🔍 ACTIVE CAMPAIGN AUDIT VERDICTS ---{C.RESET}")
    for a in board.get("audited_campaigns", []):
        verdict_color = C.GREEN if "SCALE" in a['verdict'] else (C.RED if "FATIGUE" in a['verdict'] or "DELIVERABILITY" in a['verdict'] else C.YELLOW)
        print(f"• {C.BOLD}{a['name']}{C.RESET} ({a['channel']}): {verdict_color}[{a['verdict']}]{C.RESET} (CTR: {a['ctr']}%)")
        print(f"  {C.DIM}Action:{C.RESET} {a['recommendation']}\n")

    print(f"{C.BOLD}--- 💡 'WHAT ELSE CAN BE DONE?' (GROWTH DOCTOR) ---{C.RESET}")
    for idx, opp in enumerate(board.get("opportunities", []), 1):
        print(f"{C.YELLOW}{idx}. {opp['title']}{C.RESET} ({opp['pillar']})")
        print(f"   Problem:  {opp['problem']}")
        print(f"   Solution: {opp['what_to_do']}")
        print(f"   Lift:     {C.GREEN}{opp['estimated_lift']}{C.RESET}\n")

def cmd_query(args):
    print_banner()
    question = " ".join(args.question).strip()
    if not question:
        print("Please enter a question, e.g. ./cli.py query \"Audit top campaigns\"")
        return

    print(f"{C.DIM}Analyzing MoEngage data with Antigravity Local Engine...{C.RESET}\n")
    brain = LocalIntelligenceBrain()
    response = brain.chat_query(question)
    print(response.get("reply", "No response generated."))

def cmd_draft(args):
    print_banner()
    stage = args.stage or "at_risk"
    engine = CLMIntelligenceEngine()
    draft = engine.generate_draft_clm_campaign(stage)

    print(f"{C.BOLD}CLM Draft Campaign ({draft['clm_stage']}):{C.RESET}")
    print(f"• Campaign Name:   {C.CYAN}{draft['campaign_name']}{C.RESET}")
    print(f"• Channel:         {draft['channel']}")
    print(f"• Target Audience: {draft['target_segment_name']}")
    print(f"• Trigger:         {draft['trigger_condition']}")
    print(f"• Expected Impact: {C.GREEN}{draft['expected_impact']}{C.RESET}\n")

    for v in draft.get("ab_variants", []):
        print(f"{C.BOLD}{v['variant_label']}:{C.RESET}")
        print(f"  Title: {C.YELLOW}{v['title']}{C.RESET}")
        print(f"  Body:  {v['body']}")
        print(f"  CTA:   [{v['cta']}]\n")

def cmd_set_cookies(args):
    cookies = args.cookies.strip()
    set_setting("moengage_cookies", cookies)
    set_setting("mock_mode", "false")
    print(f"{C.GREEN}✅ MoEngage session cookies saved successfully! Testing connection...{C.RESET}")
    moe = MoEngageClient()
    print(moe.verify_session())

def cmd_daily_run(args):
    print_banner()
    print(f"{C.DIM}Triggering local daily intelligence run...{C.RESET}")
    brain = LocalIntelligenceBrain()
    report = brain.run_daily_synthesis()

    print(f"\n{C.BOLD}Executive Summary:{C.RESET}")
    print(report.get("executive_summary", ""))

    print(f"\n{C.BOLD}Top 3 Insights:{C.RESET}")
    for ins in report.get("top_insights", []):
        print(f"• {ins}")

    print(f"\n{C.BOLD}Recommended Segments:{C.RESET}")
    for s in report.get("segments", []):
        print(f"• {C.CYAN}{s['name']}{C.RESET}: {s.get('description')}")

    print(f"\n{C.BOLD}Campaign Ideas:{C.RESET}")
    for c in report.get("campaign_ideas", []):
        print(f"• {C.YELLOW}{c['title']}{C.RESET} ({c['channel']}) -> {c['body']}")

def main():
    parser = argparse.ArgumentParser(
        description="MoEngage Local Autonomous Agent CLI (Antigravity Engine)",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

    # status
    p_status = subparsers.add_parser("status", help="Check MoEngage session & engine status")
    p_status.set_defaults(func=cmd_status)

    # campaigns
    p_camp = subparsers.add_parser("campaigns", help="Fetch running campaigns and performance metrics")
    p_camp.add_argument("--channel", help="Filter by channel (Push, Email, In-App)")
    p_camp.add_argument("--status", help="Filter by status (Active, Paused)")
    p_camp.set_defaults(func=cmd_campaigns)

    # segments
    p_seg = subparsers.add_parser("segments", help="List active customer segments and rules")
    p_seg.set_defaults(func=cmd_segments)

    # create-segment
    p_cseg = subparsers.add_parser("create-segment", help="Create a customer segment in MoEngage")
    p_cseg.add_argument("--name", required=True, help="Segment name")
    p_cseg.add_argument("--description", help="Segment description")
    p_cseg.add_argument("--criteria", help="Criteria JSON string or filter description")
    p_cseg.set_defaults(func=cmd_create_segment)

    # clm-board
    p_clm = subparsers.add_parser("clm-board", help="View full CLM Intelligence War Room and Campaign Audits")
    p_clm.set_defaults(func=cmd_clm_board)

    # query
    p_query = subparsers.add_parser("query", help="Ask questions about MoEngage data via Antigravity local engine")
    p_query.add_argument("question", nargs="+", help="Natural language query")
    p_query.set_defaults(func=cmd_query)

    # draft-campaign
    p_draft = subparsers.add_parser("draft-campaign", help="Generate production A/B draft campaigns")
    p_draft.add_argument("--stage", choices=["onboarding", "activation", "at_risk", "retention", "winback"], default="at_risk")
    p_draft.set_defaults(func=cmd_draft)

    # set-cookies
    p_cookies = subparsers.add_parser("set-cookies", help="Update MoEngage browser session cookies")
    p_cookies.add_argument("cookies", help="Raw Cookie header string copied from DevTools")
    p_cookies.set_defaults(func=cmd_set_cookies)

    # daily-run
    p_run = subparsers.add_parser("daily-run", help="Run the automated daily intelligence process locally")
    p_run.set_defaults(func=cmd_daily_run)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)

if __name__ == "__main__":
    main()
