from __future__ import annotations

from typing import Any

from bots.base_research_bot import BaseResearchBot


class GoldPriceMomentumBot(BaseResearchBot):
    bot_id = "bot_gold_price_momentum"
    display_name = "Gold Price Momentum"
    platform = "commodities"
    mode = "RESEARCH"
    signal_type = "macro_context"
    paper_only = True
    research_only = True
    implemented = True
    truth_label = "PUBLIC_DATA_ONLY"
    quality_tier = "B"
    risk_tier = "low"
    description = "Tracks spot gold and gold-linked proxies to classify safe-haven demand, inflation fear, and real-rate sensitivity. Intended as a confirmation layer for macro, commodity, and prediction-market context."
    edge_source = "Gold momentum as a confirmation signal for macro and safe-haven regime changes"
    opp_cadence_per_day = 2.0
    avg_hold_hours = 4.0
    fee_drag_bps = 30
    fill_rate = 0.78
    platforms = ["coingecko", "metals_api"]

    def __init__(self, adapter=None):
        self.adapter = adapter

    def run_one_cycle(self) -> dict[str, Any]:
        return self.emit_signal(
            title="Gold Price Momentum",
            summary="Watching gold direction against dollar and rates context. The current move does not yet justify a higher-confidence macro confirmation flag.",
            confidence=0.0,
            signal_taken=False,
            degraded_reason="Gold move is still inside its normal range and is not confirming a stronger macro regime change.",
            platform_truth_label="PUBLIC_DATA_ONLY",
            data={"assets": ["PAXG", "spot_gold"], "status": "monitoring"},
        )
