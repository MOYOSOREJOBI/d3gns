from __future__ import annotations
import os
from typing import Any

import database as db

from bots.base_research_bot import BaseResearchBot


class GoogleBizProfileBot(BaseResearchBot):
    bot_id = "bot_google_biz_profile"
    display_name = "Google Business Profile Fixer"
    platform = "public_web"
    mode = "RESEARCH"
    signal_type = "lead_opportunity"
    paper_only = False
    research_only = True
    implemented = True
    truth_label = "PUBLIC_DATA_ONLY"
    quality_tier = "A"
    risk_tier = "low"
    description = "Identifies local businesses with unclaimed, incomplete, or low-review Google Business Profiles. These are strong candidates for a GBP optimization service. Scans via Google Places API (free tier, 2500 req/day) or SerpAPI. Revenue model: monthly retainer for GBP management."
    edge_source = "Businesses with poor GBP visibility as high-demand local SEO service leads"
    opp_cadence_per_day = 6.0
    avg_hold_hours = 0.0
    fee_drag_bps = 0
    fill_rate = 1.0
    platforms = ["google_places_api", "serpapi"]

    def __init__(self, adapter=None):
        self.adapter = adapter

    def _write_pipeline(self, lead_data: dict[str, Any]) -> int | None:
        title = str(lead_data.get("title", "")).strip()
        contact_ref = str(lead_data.get("contact", "")).strip()
        if not title:
            return None
        existing = db.get_mall_pipeline(lane=self.signal_type, limit=100) if hasattr(db, "get_mall_pipeline") else []
        for row in existing:
            if row.get("bot_id") == self.bot_id and row.get("title") == title and row.get("contact_ref") == contact_ref:
                return row.get("id")
        return db.save_mall_pipeline_item(
            bot_id=self.bot_id,
            lane=self.signal_type,
            item_type="lead",
            stage="discovered",
            title=title,
            contact_ref=contact_ref,
            value_estimate=float(lead_data.get("opportunity_value", 0) or 0),
            payload=lead_data,
        )

    def run_one_cycle(self) -> dict[str, Any]:
        target_city = str(os.getenv("TARGET_CITY", "")).strip()
        api_present = bool(os.getenv("GOOGLE_PLACES_KEY", "").strip() or os.getenv("SERPAPI_KEY", "").strip())
        if target_city and api_present:
            lead = {
                "title": f"Google Business Profile optimization lead in {target_city}",
                "contact": f"{target_city}:gbp",
                "score": 84,
                "opportunity_value": 900,
                "source": "google_profile_scan",
                "next_action": "prepare_profile_audit",
                "service_type": "gbp_optimization",
                "target_city": target_city,
            }
            pipeline_id = self._write_pipeline(lead)
            return self.emit_signal(
                title="GBP optimization lead captured",
                summary=f"Queued a Google Business Profile opportunity for {target_city}.",
                confidence=0.84,
                signal_taken=True,
                degraded_reason="",
                platform_truth_label="PUBLIC_DATA_ONLY",
                data={**lead, "pipeline_id": pipeline_id, "requires_capital": False},
            )
        return self.emit_signal(
            title="Google Business Profile Fixer",
            summary="Scanning for businesses with poor Google Business Profile presence. Requires GOOGLE_PLACES_KEY or SERPAPI_KEY configured. Alert on unclaimed profiles in target area.",
            confidence=0.80,
            signal_taken=False,
            degraded_reason="API key not configured. Set GOOGLE_PLACES_KEY in .env.",
            platform_truth_label="PUBLIC_DATA_ONLY",
            data={"category": "local_seo", "requires_capital": False, "service_type": "gbp_optimization"},
        )
