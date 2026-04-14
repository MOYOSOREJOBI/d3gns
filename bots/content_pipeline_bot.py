from __future__ import annotations

import logging
from typing import Any

from bots.base_research_bot import BaseResearchBot

logger = logging.getLogger(__name__)


class ContentPipelineBot(BaseResearchBot):
    bot_id = "bot_content_pipeline"
    display_name = "Content Pipeline Operator"
    platform = "content_automation"
    mode = "RESEARCH"
    signal_type = "content_opportunity"
    research_only = True
    implemented = True
    truth_label = "PUBLIC_DATA_ONLY"
    quality_tier = "B"
    risk_tier = "low"
    description = (
        "Generates actionable content briefs for blog, YouTube, and newsletter "
        "pipelines based on rotating high-value niches."
    )
    edge_source = "Trend detection + automated brief generation"
    opp_cadence_per_day = 5.0
    platforms = ["content"]

    TOPIC_TEMPLATES = [
        {
            "niche": "AI automation",
            "angles": [
                "How to automate repetitive tasks with Python in 2026",
                "5 AI tools every small business owner needs",
                "Building income-generating bots: a beginner's guide",
            ],
        },
        {
            "niche": "Algorithmic trading",
            "angles": [
                "Algorithmic trading for beginners — getting started",
                "API trading setup guide: Polymarket, Kalshi, and more",
                "Risk management systems that protect your capital",
            ],
        },
        {
            "niche": "Web development",
            "angles": [
                "FastAPI vs Django in 2026: which to choose",
                "Build a real-time React dashboard in 30 minutes",
                "Full-stack project ideas that clients actually pay for",
            ],
        },
        {
            "niche": "Prediction markets",
            "angles": [
                "How to profit from Polymarket using probability edges",
                "Kalshi vs Polymarket: a trader's comparison",
                "Why prediction markets beat traditional sports betting",
            ],
        },
    ]

    def __init__(self, adapter=None):
        self.adapter = adapter
        self._cycle  = 0

    def run_one_cycle(self) -> dict[str, Any]:
        template     = self.TOPIC_TEMPLATES[self._cycle % len(self.TOPIC_TEMPLATES)]
        self._cycle += 1

        brief = {
            "niche"              : template["niche"],
            "suggested_titles"   : template["angles"],
            "content_type"       : "blog_post",
            "word_count"         : 1500,
            "target_platforms"   : ["medium", "dev.to", "linkedin"],
            "monetization"       : "affiliate_links + service_cta",
            "priority"           : "high" if self._cycle % 3 == 0 else "medium",
            "requires_capital"   : False,
        }

        return self.emit_signal(
            title=f"Content Brief: {template['niche']}",
            summary=(
                f"Generated {len(template['angles'])} angles for '{template['niche']}'. "
                f"Ready for writer assignment."
            ),
            confidence=0.75,
            signal_taken=True,
            data=brief,
        )
