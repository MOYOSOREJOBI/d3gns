from __future__ import annotations
import logging
from typing import Any
from bots.base_research_bot import BaseResearchBot

logger = logging.getLogger(__name__)

NEWSLETTER_TOPICS = [
    ("AI Tools Roundup", "Weekly digest of the best new AI tools for builders and founders", ["beehiiv", "substack"]),
    ("Prediction Markets Weekly", "Recap of biggest market moves on Polymarket and Kalshi", ["substack"]),
    ("Side Income Report", "How to build automated income streams with code and data", ["convertkit", "beehiiv"]),
    ("Algo Trader's Digest", "Strategy, tools, and edge for retail algorithmic traders", ["substack"]),
]

SPONSORSHIP_NICHES = [
    {"niche": "SaaS tools", "avg_cpm": 35, "sponsor_type": "product"},
    {"niche": "Trading platforms", "avg_cpm": 55, "sponsor_type": "financial"},
    {"niche": "AI services", "avg_cpm": 45, "sponsor_type": "tech"},
]


class NewsletterBot(BaseResearchBot):
    bot_id = "bot_newsletter"
    display_name = "Newsletter Monetization Bot"
    platform = "content_automation"
    mode = "RESEARCH"
    signal_type = "content_opportunity"
    research_only = True
    implemented = True
    truth_label = "PUBLIC_DATA_ONLY"
    quality_tier = "B"
    risk_tier = "low"
    description = "Generates newsletter edition briefs, identifies sponsorship opportunities, and tracks content pipeline for newsletter business."
    edge_source = "Newsletter sponsorship arbitrage — high-margin content business"
    opp_cadence_per_day = 2.0
    platforms = ["beehiiv", "substack"]

    def __init__(self, adapter=None):
        self.adapter = adapter
        self._cycle  = 0

    def run_one_cycle(self) -> dict[str, Any]:
        topic = NEWSLETTER_TOPICS[self._cycle % len(NEWSLETTER_TOPICS)]
        sponsor = SPONSORSHIP_NICHES[self._cycle % len(SPONSORSHIP_NICHES)]
        self._cycle += 1

        brief = {
            "edition_title"        : topic[0],
            "angle"                : topic[1],
            "target_platforms"     : topic[2],
            "sponsorship_niche"    : sponsor["niche"],
            "estimated_cpm"        : sponsor["avg_cpm"],
            "estimated_revenue_1k_subs": round(sponsor["avg_cpm"] * 1, 2),
            "requires_capital"     : False,
            "action"               : "draft_and_schedule",
        }

        return self.emit_signal(
            title=f"Newsletter Brief: {topic[0]}",
            summary=f"{topic[1]} | Sponsor angle: {sponsor['niche']} @ ${sponsor['avg_cpm']} CPM",
            confidence=0.70,
            signal_taken=True,
            data=brief,
        )
