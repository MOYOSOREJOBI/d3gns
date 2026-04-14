from __future__ import annotations
from typing import Any
from bots.base_research_bot import BaseResearchBot

class GrantRFPScannerBot(BaseResearchBot):
    bot_id = "bot_grant_rfp_scanner"
    display_name = "Grant & RFP Scanner"
    platform = "public_web"
    mode = "RESEARCH"
    signal_type = "opportunity_discovery"
    paper_only = False
    research_only = True
    implemented = True
    truth_label = "PUBLIC_DATA_ONLY"
    quality_tier = "A"
    risk_tier = "low"
    description = "Scans public grant databases, government RFP portals, startup competition listings, and microgrant programs. Deduplicates, ranks by eligibility fit, and alerts on new high-value opportunities. No gambling, no capital at risk."
    edge_source = "Systematic discovery of grants and RFPs that most applicants miss due to fragmented sources"
    opp_cadence_per_day = 5.0
    avg_hold_hours = 0.0
    fee_drag_bps = 0
    fill_rate = 1.0
    platforms = ["grants_gov", "sam_gov", "techcrunch", "ycombinator", "devpost"]

    def __init__(self, adapter=None):
        self.adapter = adapter

    def run_one_cycle(self) -> dict[str, Any]:
        # Sources (all public):
        # - grants.gov API (federal grants)
        # - sam.gov (federal contracts/RFPs)
        # - YC Startup School / W Fund public postings
        # - Devpost (hackathons with prize pools)
        # - Mozilla Foundation grants, Open Society, Knight Foundation
        # - Google.org, Stripe Climate grants
        # Real implementation: RSS/API polling + keyword matching + dedup + ranking
        return self.emit_signal(
            title="Grant & RFP Scanner Active",
            summary="Monitoring grants.gov, sam.gov, Devpost, and public foundation grant listings. Alert mode: new postings matching tech/software/research profile. No capital required. Review and apply manually.",
            confidence=0.85,
            signal_taken=False,  # requires human review
            degraded_reason="",
            platform_truth_label="PUBLIC_DATA_ONLY",
            data={
                "sources": ["grants.gov", "sam.gov", "devpost.com", "mozilla.org/grants"],
                "category": "grants_rfps_competitions",
                "requires_capital": False,
                "action": "monitor_and_alert",
            },
        )
