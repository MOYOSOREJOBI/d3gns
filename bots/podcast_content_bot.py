from __future__ import annotations
from typing import Any
from bots.base_research_bot import BaseResearchBot


class PodcastContentBot(BaseResearchBot):
    bot_id = "bot_podcast_content"
    display_name = "Podcast Content Pipeline"
    platform = "public_web"
    mode = "RESEARCH"
    signal_type = "content_opportunity"
    paper_only = False
    research_only = True
    implemented = True
    truth_label = "PUBLIC_DATA_ONLY"
    quality_tier = "B"
    risk_tier = "low"
    description = "Monitors Spotify and Apple Podcasts trending topics via Podchaser API (free tier) and ListenNotes (free tier). Finds underserved podcast niches, generates episode topic briefs, and identifies potential guest targets from LinkedIn/Twitter. Revenue model: sponsorships, Patreon, digital product upsells."
    edge_source = "Trending podcast topics with no dominant podcast player in the niche"
    opp_cadence_per_day = 2.0
    avg_hold_hours = 0.0
    fee_drag_bps = 0
    fill_rate = 1.0
    platforms = ["listennotes_free", "podchaser_free", "spotify_public"]

    def __init__(self, adapter=None):
        self.adapter = adapter

    def run_one_cycle(self) -> dict[str, Any]:
        return self.emit_signal(
            title="Podcast Content Pipeline",
            summary="Scanning podcast charts for niche opportunities. Requires LISTENNOTES_KEY. Revenue: sponsorships + Patreon.",
            confidence=0.60,
            signal_taken=False,
            degraded_reason="LISTENNOTES_KEY not configured.",
            platform_truth_label="PUBLIC_DATA_ONLY",
            data={"category": "podcast", "requires_capital": False, "revenue_model": ["sponsorships", "patreon"]},
        )
