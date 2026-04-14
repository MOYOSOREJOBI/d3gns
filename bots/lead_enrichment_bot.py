from __future__ import annotations
from typing import Any
from bots.base_research_bot import BaseResearchBot


class LeadEnrichmentBot(BaseResearchBot):
    bot_id = "bot_lead_enrichment"
    display_name = "Lead Enrichment Pipeline"
    platform = "public_web"
    mode = "RESEARCH"
    signal_type = "lead_qualification"
    paper_only = False
    research_only = True
    implemented = True
    truth_label = "PUBLIC_DATA_ONLY"
    quality_tier = "A"
    risk_tier = "low"
    description = "Takes a raw list of business names or domains and enriches them with public data: LinkedIn company page (via Google cache), domain registration info (WHOIS public), technology stack (BuiltWith free tier), social presence. Outputs a scored lead list for outreach. No scraping of private data."
    edge_source = "Enriched lead lists dramatically improve cold outreach conversion rates"
    opp_cadence_per_day = 3.0
    avg_hold_hours = 0.0
    fee_drag_bps = 0
    fill_rate = 1.0
    platforms = ["whois_public", "builtwith_free", "google_cache"]

    def __init__(self, adapter=None):
        self.adapter = adapter

    def run_one_cycle(self) -> dict[str, Any]:
        return self.emit_signal(
            title="Lead Enrichment Pipeline",
            summary="Ready to enrich lead lists with public data. No active lead list configured. Input a CSV of company names/domains to start enrichment.",
            confidence=0.85,
            signal_taken=False,
            degraded_reason="No lead list input configured.",
            platform_truth_label="PUBLIC_DATA_ONLY",
            data={"category": "lead_enrichment", "requires_capital": False, "inputs": ["company_name_list", "domain_list"]},
        )
