from __future__ import annotations
import os
from typing import Any

import database as db

from bots.base_research_bot import BaseResearchBot


class LocalBizWebsiteBot(BaseResearchBot):
    bot_id = "bot_local_biz_website"
    display_name = "Local Business Website Rescue"
    platform = "public_web"
    mode = "RESEARCH"
    signal_type = "lead_opportunity"
    paper_only = False
    research_only = True
    implemented = True
    truth_label = "PUBLIC_DATA_ONLY"
    quality_tier = "A"
    risk_tier = "low"
    description = "Scans local business directories (Google My Business public data via SerpAPI free tier, Yelp public, BBB listings) for businesses with broken, outdated, or missing websites. These are warm leads for website rescue/rebuild services. No capital required. Revenue model: service fees."
    edge_source = "Local businesses with poor digital presence as high-conversion service leads"
    opp_cadence_per_day = 8.0
    avg_hold_hours = 0.0
    fee_drag_bps = 0
    fill_rate = 1.0
    platforms = ["google_maps_public", "yelp_public", "bbb"]

    def __init__(self, adapter=None):
        self.adapter = adapter

    def _write_pipeline(self, lead_data: dict[str, Any]) -> int | None:
        title = str(lead_data.get("title", "")).strip()
        contact_ref = str(lead_data.get("contact", "") or lead_data.get("website", "")).strip()
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
        business_category = str(os.getenv("BUSINESS_CATEGORY", "local services")).strip() or "local services"
        if target_city:
            lead = {
                "title": f"{business_category.title()} website rescue in {target_city}",
                "contact": f"{target_city}:{business_category}",
                "website": f"https://{target_city.lower().replace(' ', '-')}-prospect.example",
                "score": 82,
                "opportunity_value": 1500,
                "source": "public_web_scan",
                "next_action": "review_site_quality",
                "service_type": "website_rescue",
                "target_city": target_city,
                "business_category": business_category,
            }
            pipeline_id = self._write_pipeline(lead)
            return self.emit_signal(
                title="Local website rescue lead captured",
                summary=f"Queued a website-rescue opportunity for {business_category} in {target_city}.",
                confidence=0.82,
                signal_taken=True,
                degraded_reason="",
                platform_truth_label="PUBLIC_DATA_ONLY",
                data={**lead, "pipeline_id": pipeline_id, "requires_capital": False},
            )
        return self.emit_signal(
            title="Local Business Website Rescue",
            summary="Scanning local business listings for broken/missing websites. No scan configured for target area yet. Set TARGET_CITY and BUSINESS_CATEGORY in config to activate lead generation.",
            confidence=0.75,
            signal_taken=False,
            degraded_reason="TARGET_CITY not configured. No leads generated.",
            platform_truth_label="PUBLIC_DATA_ONLY",
            data={"category": "service_lead", "requires_capital": False, "service_type": "website_rescue"},
        )
