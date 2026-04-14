from __future__ import annotations
import logging
from typing import Any
from bots.base_research_bot import BaseResearchBot

logger = logging.getLogger(__name__)

PRODUCT_IDEAS = [
    {"title": "Python Trading Bot Template", "price": 49, "platform": "gumroad", "niche": "algo trading"},
    {"title": "Polymarket Edge Tracker Spreadsheet", "price": 19, "platform": "gumroad", "niche": "prediction markets"},
    {"title": "FastAPI Starter Kit with Auth", "price": 39, "platform": "lemonsqueezy", "niche": "web dev"},
    {"title": "Freelance Client Onboarding Template Pack", "price": 29, "platform": "gumroad", "niche": "freelance"},
    {"title": "AI Prompt Library for Developers", "price": 24, "platform": "lemonsqueezy", "niche": "AI tools"},
]


class DigitalDownloadsBot(BaseResearchBot):
    bot_id = "bot_digital_downloads"
    display_name = "Digital Downloads Bot"
    platform = "content_automation"
    mode = "RESEARCH"
    signal_type = "product_opportunity"
    research_only = True
    implemented = True
    truth_label = "PUBLIC_DATA_ONLY"
    quality_tier = "B"
    risk_tier = "low"
    description = "Identifies and queues digital product opportunities (templates, scripts, spreadsheets) for Gumroad/LemonSqueezy."
    edge_source = "Digital product micro-business with zero marginal cost"
    opp_cadence_per_day = 3.0
    platforms = ["gumroad", "lemonsqueezy"]

    def __init__(self, adapter=None):
        self.adapter = adapter
        self._cycle  = 0

    def run_one_cycle(self) -> dict[str, Any]:
        product = PRODUCT_IDEAS[self._cycle % len(PRODUCT_IDEAS)]
        self._cycle += 1

        data = {
            **product,
            "requires_capital" : False,
            "time_to_create_h" : 4,
            "breakeven_units"  : 1,
            "action"           : "create_and_list",
        }

        return self.emit_signal(
            title=f"Digital Product: {product['title']}",
            summary=f"${product['price']} on {product['platform']} | Niche: {product['niche']}",
            confidence=0.72,
            signal_taken=True,
            data=data,
        )
