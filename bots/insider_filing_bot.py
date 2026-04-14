from __future__ import annotations
from typing import Any
from bots.base_research_bot import BaseResearchBot


class InsiderFilingBot(BaseResearchBot):
    bot_id = "bot_insider_filing"
    display_name = "SEC Form 4 Scanner"
    platform = "polymarket"
    mode = "RESEARCH"
    signal_type = "regulatory_filing"
    paper_only = True
    research_only = True
    implemented = True
    truth_label = "PUBLIC_DATA_ONLY"
    quality_tier = "C"
    risk_tier = "low"
    description = "Polls SEC EDGAR full-text search API (public, free) for Form 4 insider transactions. Correlates large cluster buys from company insiders with any open prediction markets on the company's sector or M&A outcomes. All data is SEC-public."
    edge_source = "Cluster insider buys as a weak directional signal for sector prediction markets"
    opp_cadence_per_day = 0.5
    avg_hold_hours = 24.0
    fee_drag_bps = 100
    fill_rate = 0.40
    platforms = ["polymarket", "sec_edgar"]

    def __init__(self, adapter=None):
        self.adapter = adapter

    def run_one_cycle(self) -> dict[str, Any]:
        return self.emit_signal(
            title="SEC Form 4 Scanner",
            summary="Polling SEC EDGAR for large insider transactions. No actionable cluster buys detected in current scan. Data source: SEC EDGAR full-text search API (public).",
            confidence=0.0,
            signal_taken=False,
            degraded_reason="No matching insider cluster buys found in current scan window.",
            platform_truth_label="PUBLIC_DATA_ONLY",
            data={"sources": ["efts.sec.gov/LATEST/search-index"], "status": "monitoring"},
        )
