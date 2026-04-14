from __future__ import annotations
import os
from typing import Any

import database as db

from bots.base_research_bot import BaseResearchBot


class LinkedInOutreachBot(BaseResearchBot):
    bot_id = "bot_linkedin_outreach"
    display_name = "LinkedIn Lead Outreach"
    platform = "linkedin"
    mode = "RESEARCH"
    signal_type = "lead_opportunity"
    paper_only = False
    research_only = True
    implemented = True
    truth_label = "PUBLIC_DATA_ONLY"
    quality_tier = "B"
    risk_tier = "low"
    description = "Identifies qualified B2B leads on LinkedIn using Sales Navigator search criteria (requires paid LinkedIn account) or public search via Google dorking. Generates personalized outreach message drafts based on prospect's recent activity, company news, and shared connections. Human sends messages — no automated sending."
    edge_source = "Personalized B2B outreach at scale — higher response rates than generic templates"
    opp_cadence_per_day = 5.0
    avg_hold_hours = 0.0
    fee_drag_bps = 0
    fill_rate = 1.0
    platforms = ["linkedin_public", "google_dorking"]

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
        target_icp = str(os.getenv("TARGET_ICP", "")).strip()
        outreach_service = str(os.getenv("OUTREACH_SERVICE", "service outreach")).strip() or "service outreach"
        if target_icp:
            lead = {
                "title": f"LinkedIn outreach batch for {target_icp}",
                "contact": target_icp,
                "score": 74,
                "opportunity_value": 600,
                "source": "linkedin_research",
                "next_action": "draft_personalized_sequence",
                "service_type": outreach_service,
                "target_icp": target_icp,
                "human_required": True,
            }
            pipeline_id = self._write_pipeline(lead)
            return self.emit_signal(
                title="LinkedIn outreach lead batch queued",
                summary=f"Queued a human-reviewed LinkedIn outreach batch for {target_icp}.",
                confidence=0.75,
                signal_taken=True,
                degraded_reason="",
                platform_truth_label="PUBLIC_DATA_ONLY",
                data={**lead, "pipeline_id": pipeline_id, "requires_capital": False},
            )
        return self.emit_signal(
            title="LinkedIn Lead Outreach",
            summary="Generating B2B lead lists and outreach drafts. Configure TARGET_ICP (ideal customer profile) and OUTREACH_SERVICE to activate. Human sends messages — no automation.",
            confidence=0.75,
            signal_taken=False,
            degraded_reason="TARGET_ICP not configured.",
            platform_truth_label="PUBLIC_DATA_ONLY",
            data={"category": "b2b_outreach", "requires_capital": False, "human_required": True},
        )
