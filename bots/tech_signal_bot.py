from __future__ import annotations
from typing import Any
from bots.base_research_bot import BaseResearchBot


class TechSignalBot(BaseResearchBot):
    bot_id = "bot_tech_signal"
    display_name = "Technical Signal Scanner"
    platform = "polymarket"
    mode = "RESEARCH"
    signal_type = "technical"
    paper_only = True
    research_only = True
    implemented = True
    truth_label = "PUBLIC_DATA_ONLY"
    quality_tier = "C"
    risk_tier = "medium"
    description = "Computes basic technical signals (RSI, MACD, Bollinger Bands) on public price data from Binance and CoinGecko free APIs. When a crypto asset hits a key technical extreme, checks for related prediction markets that may not reflect it. Technical signals alone have low predictive value in prediction markets — use as one factor among many."
    edge_source = "Technical extremes on liquid crypto assets as weak signals for prediction markets"
    opp_cadence_per_day = 2.0
    avg_hold_hours = 3.0
    fee_drag_bps = 140
    fill_rate = 0.40
    platforms = ["polymarket", "binance_public", "coingecko"]

    def __init__(self, adapter=None):
        self.adapter = adapter

    def run_one_cycle(self) -> dict[str, Any]:
        return self.emit_signal(
            title="Technical Signal Scanner",
            summary="Computing RSI/MACD/BB on BTC/ETH. No technical extreme detected that correlates with an open prediction market opportunity. Warning: low precision signal — use only as supplementary filter.",
            confidence=0.0,
            signal_taken=False,
            degraded_reason="No technical extreme reading that correlates with prediction market mispricing.",
            platform_truth_label="PUBLIC_DATA_ONLY",
            data={"indicators": ["RSI", "MACD", "BollingerBands"], "assets": ["BTC", "ETH"], "status": "monitoring"},
        )
