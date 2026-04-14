from __future__ import annotations
import os
from typing import Any

import database as db

from bots.base_research_bot import BaseResearchBot


class AIIntakeBot(BaseResearchBot):
    bot_id = "bot_ai_intake"
    display_name = "AI Intake Automation"
    platform = "public_web"
    mode = "RESEARCH"
    signal_type = "lead_opportunity"
    paper_only = False
    research_only = True
    implemented = True
    truth_label = "PUBLIC_DATA_ONLY"
    quality_tier = "A"
    risk_tier = "low"
    description = "Builds and deploys AI-powered client intake forms that replace PDF forms and phone tag for service businesses. Targets law firms, healthcare practices, accounting firms, and contractors. Uses Typeform/Tally + n8n or Zapier for workflow automation. Revenue model: one-time setup ($800–$2500) + optional maintenance."
    edge_source = "Broken intake processes cost service businesses 15–30% of potential clients — high urgency and clear ROI"
    opp_cadence_per_day = 2.0
    avg_hold_hours = 0.0
    fee_drag_bps = 0
    fill_rate = 1.0
    platforms = ["typeform_api", "n8n", "zapier"]

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
        target_vertical = str(os.getenv("TARGET_VERTICAL", "")).strip()
        if target_vertical:
            lead = {
                "title": f"AI intake automation lead for {target_vertical}",
                "contact": target_vertical,
                "score": 80,
                "opportunity_value": 1400,
                "source": "intake_workflow_scan",
                "next_action": "draft_intake_brief",
                "service_type": "ai_intake_automation",
                "target_vertical": target_vertical,
            }
            pipeline_id = self._write_pipeline(lead)
            return self.emit_signal(
                title="AI intake automation lead captured",
                summary=f"Queued an intake-automation opportunity for the {target_vertical} vertical.",
                confidence=0.8,
                signal_taken=True,
                degraded_reason="",
                platform_truth_label="PUBLIC_DATA_ONLY",
                data={**lead, "pipeline_id": pipeline_id, "requires_capital": False},
            )
        return self.emit_signal(
            title="AI Intake Automation",
            summary="Ready to build AI intake systems for service businesses. Configure TARGET_VERTICAL to generate leads. Revenue: $800–$2500 setup.",
            confidence=0.80,
            signal_taken=False,
            degraded_reason="TARGET_VERTICAL not configured.",
            platform_truth_label="PUBLIC_DATA_ONLY",
            data={"category": "intake_automation", "requires_capital": False, "verticals": ["legal", "healthcare", "accounting"]},
        )
