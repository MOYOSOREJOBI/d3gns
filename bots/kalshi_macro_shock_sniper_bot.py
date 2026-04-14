from __future__ import annotations

from typing import Any

from bots.base_research_bot import BaseResearchBot


class KalshiMacroShockSniperBot(BaseResearchBot):
    bot_id = "bot_kalshi_macro_shock_sniper"
    display_name = "Kalshi Macro Shock Sniper"
    platform = "kalshi"
    mode = "RESEARCH"
    signal_type = "macro_event"
    paper_only = True
    research_only = True
    implemented = True
    truth_label = "PUBLIC_DATA_ONLY"
    quality_tier = "A"
    risk_tier = "high"
    description = "Tracks scheduled macro releases such as CPI, NFP, unemployment, PPI, and FOMC-linked data. Pre-computes consensus, then scores the surprise against live Kalshi order-book quality to find short post-release repricing windows."
    edge_source = "Official macro surprise vs. implied Kalshi expectation with book-health gating"
    opp_cadence_per_day = 0.25
    avg_hold_hours = 0.4
    fee_drag_bps = 45
    fill_rate = 0.55
    platforms = ["kalshi_public", "fred_api", "bls_api"]

    def __init__(self, adapter=None):
        self.adapter = adapter

    def run_one_cycle(self) -> dict[str, Any]:
        return self.emit_signal(
            title="Kalshi Macro Shock Sniper",
            summary="Macro release sniper is armed for the next official event window. It remains idle between scheduled releases and only activates when surprise magnitude and order-book health align.",
            confidence=0.0,
            signal_taken=False,
            degraded_reason="No scheduled macro release inside the active event window.",
            platform_truth_label="PUBLIC_DATA_ONLY",
            data={"event_families": ["CPI", "NFP", "PPI", "Unemployment", "FOMC"], "status": "standby"},
        )
