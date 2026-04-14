from __future__ import annotations
import logging
from typing import Any
from bots.base_research_bot import BaseResearchBot

logger = logging.getLogger(__name__)

AFFILIATE_PROGRAMS = [
    {"program": "Vultr VPS", "commission": "up to $100/ref", "niche": "hosting", "cookie_days": 30},
    {"program": "DigitalOcean", "commission": "$25/ref", "niche": "hosting", "cookie_days": 30},
    {"program": "Trading212", "commission": "$50/ref", "niche": "trading", "cookie_days": 90},
    {"program": "Notion", "commission": "20% recurring", "niche": "productivity", "cookie_days": 90},
    {"program": "Vercel", "commission": "$150/ref", "niche": "devtools", "cookie_days": 30},
]

CONTENT_ANGLES = [
    "Best {niche} tools in 2026 — ranked and reviewed",
    "How I use {program} for my {niche} projects",
    "{program} vs competitors: honest comparison",
]


class AffiliateContentBot(BaseResearchBot):
    bot_id = "bot_affiliate_content"
    display_name = "Affiliate Content Bot"
    platform = "content_automation"
    mode = "RESEARCH"
    signal_type = "revenue_opportunity"
    research_only = True
    implemented = True
    truth_label = "PUBLIC_DATA_ONLY"
    quality_tier = "B"
    risk_tier = "low"
    description = "Identifies high-commission affiliate programs and generates content briefs to promote them."
    edge_source = "Affiliate program arbitrage via targeted content"
    opp_cadence_per_day = 4.0
    platforms = ["affiliate_networks"]

    def __init__(self, adapter=None):
        self.adapter = adapter
        self._cycle  = 0

    def run_one_cycle(self) -> dict[str, Any]:
        prog  = AFFILIATE_PROGRAMS[self._cycle % len(AFFILIATE_PROGRAMS)]
        angle = CONTENT_ANGLES[self._cycle % len(CONTENT_ANGLES)]
        self._cycle += 1

        title   = angle.format(niche=prog["niche"], program=prog["program"])
        data = {
            **prog,
            "suggested_title"  : title,
            "requires_capital" : False,
            "action"           : "create_content_and_publish",
        }

        return self.emit_signal(
            title=f"Affiliate Opp: {prog['program']}",
            summary=f"{prog['commission']} | Content: {title[:60]}",
            confidence=0.68,
            signal_taken=True,
            data=data,
        )
