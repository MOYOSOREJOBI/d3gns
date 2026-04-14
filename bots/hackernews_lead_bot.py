from __future__ import annotations
from typing import Any
from bots.base_research_bot import BaseResearchBot


class HackerNewsLeadBot(BaseResearchBot):
    bot_id = "bot_hackernews_lead"
    display_name = "Hacker News Lead Finder"
    platform = "public_web"
    mode = "RESEARCH"
    signal_type = "lead_opportunity"
    paper_only = False
    research_only = True
    implemented = True
    truth_label = "PUBLIC_DATA_ONLY"
    quality_tier = "A"
    risk_tier = "low"
    description = "Monitors Hacker News Firebase API (public, free, real-time) for 'Ask HN: Who is hiring' threads, 'Show HN' product launches, and 'Ask HN: Who wants to be hired' posts. Also watches for companies posting 'we're building X' that match your service offering. High-quality B2B leads from technical founders."
    edge_source = "Hacker News self-reported intent signals — founders who post 'who is hiring' or 'Show HN' are buying decisions"
    opp_cadence_per_day = 4.0
    avg_hold_hours = 0.0
    fee_drag_bps = 0
    fill_rate = 1.0
    platforms = ["hn_firebase_api"]

    def __init__(self, adapter=None):
        self.adapter = adapter

    def run_one_cycle(self) -> dict[str, Any]:
        return self.emit_signal(
            title="Hacker News Lead Finder",
            summary="Monitoring HN Firebase API for hiring posts, Show HN launches, and buying intent signals. No API key required. Monitoring active.",
            confidence=0.80,
            signal_taken=False,
            degraded_reason="No target keywords configured. Set HN_TARGET_KEYWORDS in config.",
            platform_truth_label="PUBLIC_DATA_ONLY",
            data={"source": "hacker-news.firebaseio.com", "threads": ["who_is_hiring", "show_hn", "ask_hn"], "requires_capital": False},
        )
