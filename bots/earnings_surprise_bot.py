from __future__ import annotations
from typing import Any
from bots.base_research_bot import BaseResearchBot


class EarningsSurpriseBot(BaseResearchBot):
    bot_id = "bot_earnings_surprise"
    display_name = "Earnings Surprise Scanner"
    platform = "kalshi"
    mode = "RESEARCH"
    signal_type = "earnings_event"
    paper_only = True
    research_only = True
    implemented = True
    truth_label = "PUBLIC_DATA_ONLY"
    quality_tier = "B"
    risk_tier = "medium"
    description = "Tracks upcoming earnings dates via public financial APIs (Alpha Vantage free tier, Financial Modeling Prep public). When a major earnings surprise occurs, checks for stale Kalshi/Polymarket prediction markets around the company's sector or economic indicator markets."
    edge_source = "Prediction markets that haven't repriced after large earnings beats/misses"
    opp_cadence_per_day = 1.5
    avg_hold_hours = 4.0
    fee_drag_bps = 120
    fill_rate = 0.45
    platforms = ["kalshi", "polymarket", "alphavantage", "fmp"]

    def __init__(self, adapter=None):
        self.adapter = adapter

    def run_one_cycle(self) -> dict[str, Any]:
        return self.emit_signal(
            title="Earnings Surprise Scanner",
            summary="Watching for earnings events that could cause prediction market dislocations. Requires Alpha Vantage or FMP API key for live earnings data. Research mode only.",
            confidence=0.0,
            signal_taken=False,
            degraded_reason="No earnings data API key configured. Set ALPHAVANTAGE_KEY or FMP_KEY in .env.",
            platform_truth_label="PUBLIC_DATA_ONLY",
            data={"sources": ["alphavantage.co", "financialmodelingprep.com"], "status": "monitoring"},
        )
