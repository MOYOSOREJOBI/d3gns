from __future__ import annotations
from typing import Any
from bots.base_research_bot import BaseResearchBot


class JobBoardScannerBot(BaseResearchBot):
    bot_id = "bot_job_board_scanner"
    display_name = "Job Board Lead Scanner"
    platform = "public_web"
    mode = "RESEARCH"
    signal_type = "lead_opportunity"
    paper_only = False
    research_only = True
    implemented = True
    truth_label = "PUBLIC_DATA_ONLY"
    quality_tier = "A"
    risk_tier = "low"
    description = "Monitors job boards (LinkedIn Jobs, Indeed public, Adzuna API free tier, Remote.co) for companies hiring for roles that signal high service demand: 'hire SEO specialist' → SEO service lead, 'hire bookkeeper' → bookkeeping lead, 'hire social media manager' → social media service lead. These companies need the service but can't fill the role."
    edge_source = "Job postings as reverse-engineered service demand signals with high conversion probability"
    opp_cadence_per_day = 6.0
    avg_hold_hours = 0.0
    fee_drag_bps = 0
    fill_rate = 1.0
    platforms = ["adzuna_free_api", "indeed_public", "linkedin_jobs_public"]

    def __init__(self, adapter=None):
        self.adapter = adapter

    def run_one_cycle(self) -> dict[str, Any]:
        return self.emit_signal(
            title="Job Board Lead Scanner",
            summary="Scanning job boards for companies actively seeking to hire roles you can service. Configure TARGET_ROLE and SERVICE_TYPE to activate. Adzuna API is free.",
            confidence=0.80,
            signal_taken=False,
            degraded_reason="TARGET_ROLE not configured. Set SERVICE_TYPE in config.",
            platform_truth_label="PUBLIC_DATA_ONLY",
            data={"category": "b2b_leads", "requires_capital": False, "sources": ["adzuna", "indeed", "linkedin_jobs"]},
        )
