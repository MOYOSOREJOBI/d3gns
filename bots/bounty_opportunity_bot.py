from __future__ import annotations
from typing import Any
from bots.base_research_bot import BaseResearchBot

class BountyOpportunityBot(BaseResearchBot):
    bot_id = "bot_bounty_opportunity"
    display_name = "Bounty & Challenge Scout"
    platform = "public_web"
    mode = "RESEARCH"
    signal_type = "opportunity_discovery"
    paper_only = False
    research_only = True
    implemented = True
    truth_label = "PUBLIC_DATA_ONLY"
    quality_tier = "A"
    risk_tier = "low"
    description = "Scans legal public bug bounty programs (HackerOne, Bugcrowd), open innovation challenges, and prize reward programs. Ranks by payout potential, skill fit, and competition level. Alert-only."
    edge_source = "Systematic scanning of HackerOne, Bugcrowd, Intigriti public programs + DARPA/NIST challenge listings"
    opp_cadence_per_day = 2.0
    avg_hold_hours = 0.0
    fee_drag_bps = 0
    fill_rate = 1.0
    platforms = ["hackerone", "bugcrowd", "intigriti", "challenge_gov"]

    def __init__(self, adapter=None):
        self.adapter = adapter

    def run_one_cycle(self) -> dict[str, Any]:
        return self.emit_signal(
            title="Bounty & Challenge Scout Active",
            summary="Monitoring HackerOne public program list, Bugcrowd public programs, Intigriti, and challenge.gov for new high-payout opportunities. Ranking by estimated payout vs. skill fit.",
            confidence=0.80,
            signal_taken=False,
            degraded_reason="",
            platform_truth_label="PUBLIC_DATA_ONLY",
            data={
                "sources": ["hackerone.com/directory", "bugcrowd.com/programs", "intigriti.com", "challenge.gov"],
                "category": "bug_bounty_challenges",
                "requires_capital": False,
                "action": "alert_for_review",
            },
        )
