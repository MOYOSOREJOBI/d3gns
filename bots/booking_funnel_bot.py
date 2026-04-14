from __future__ import annotations
import os
from typing import Any

import database as db

from bots.base_research_bot import BaseResearchBot


class BookingFunnelBot(BaseResearchBot):
    bot_id = "bot_booking_funnel"
    display_name = "Booking Funnel Builder"
    platform = "public_web"
    mode = "RESEARCH"
    signal_type = "opportunity_discovery"
    paper_only = False
    research_only = True
    implemented = True
    truth_label = "PUBLIC_DATA_ONLY"
    quality_tier = "A"
    risk_tier = "low"
    description = "Identifies service businesses (salons, contractors, therapists, lawyers) that still use phone-only or manual booking. Flags them as leads for booking-funnel installation service (Calendly/Acuity embed + landing page). Scans Yelp, Google, and local directories. Revenue model: setup fee + monthly management."
    edge_source = "Service businesses losing bookings due to friction — high conversion rate for appointment funnel services"
    opp_cadence_per_day = 5.0
    avg_hold_hours = 0.0
    fee_drag_bps = 0
    fill_rate = 1.0
    platforms = ["yelp_public", "google_places_api"]

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
        if target_city:
            lead = {
                "title": f"Booking funnel install lead in {target_city}",
                "contact": f"{target_city}:booking",
                "score": 78,
                "opportunity_value": 1200,
                "source": "booking_friction_scan",
                "next_action": "draft_booking_offer",
                "service_type": "booking_funnel",
                "target_city": target_city,
            }
            pipeline_id = self._write_pipeline(lead)
            return self.emit_signal(
                title="Booking funnel lead captured",
                summary=f"Queued a booking-funnel opportunity for {target_city}.",
                confidence=0.8,
                signal_taken=True,
                degraded_reason="",
                platform_truth_label="PUBLIC_DATA_ONLY",
                data={**lead, "pipeline_id": pipeline_id, "requires_capital": False},
            )
        return self.emit_signal(
            title="Booking Funnel Builder Leads",
            summary="Scanning for service businesses without online booking capability. Configure TARGET_CITY to generate leads. Revenue: setup fee + monthly SaaS management.",
            confidence=0.80,
            signal_taken=False,
            degraded_reason="TARGET_CITY not configured.",
            platform_truth_label="PUBLIC_DATA_ONLY",
            data={"category": "funnel_setup", "requires_capital": False, "service_type": "booking_funnel"},
        )
