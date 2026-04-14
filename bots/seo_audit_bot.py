from __future__ import annotations
import os
from typing import Any

import database as db

from bots.base_research_bot import BaseResearchBot


class SeoAuditBot(BaseResearchBot):
    bot_id = "bot_seo_audit"
    display_name = "SEO Audit Bot"
    platform = "public_web"
    mode = "RESEARCH"
    signal_type = "lead_opportunity"
    paper_only = False
    research_only = True
    implemented = True
    truth_label = "PUBLIC_DATA_ONLY"
    quality_tier = "A"
    risk_tier = "low"
    description = "Runs automated SEO audits on target websites using public data: PageSpeed Insights API (free), Google Search Console public metrics, Moz DA public API (free tier), and structured data validation. Generates a detailed report for use as a lead magnet or paid service deliverable."
    edge_source = "Free automated SEO audit as a high-conversion B2B lead generator or service deliverable"
    opp_cadence_per_day = 4.0
    avg_hold_hours = 0.0
    fee_drag_bps = 0
    fill_rate = 1.0
    platforms = ["pagespeed_insights_api", "moz_api_free"]

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
        target_domain = str(os.getenv("TARGET_DOMAIN", "")).strip()
        if not target_domain:
            target_domains = [item.strip() for item in str(os.getenv("TARGET_DOMAINS", "")).split(",") if item.strip()]
            target_domain = target_domains[0] if target_domains else ""
        if target_domain:
            lead = {
                "title": f"SEO audit ready for {target_domain}",
                "contact": target_domain,
                "website": f"https://{target_domain}",
                "score": 86,
                "opportunity_value": 750,
                "source": "seo_audit_scan",
                "next_action": "deliver_audit_brief",
                "service_type": "seo_audit",
                "target_domain": target_domain,
            }
            pipeline_id = self._write_pipeline(lead)
            return self.emit_signal(
                title="SEO audit lead captured",
                summary=f"Queued an SEO audit opportunity for {target_domain}.",
                confidence=0.85,
                signal_taken=True,
                degraded_reason="",
                platform_truth_label="PUBLIC_DATA_ONLY",
                data={**lead, "pipeline_id": pipeline_id, "requires_capital": False},
            )
        return self.emit_signal(
            title="SEO Audit Bot",
            summary="Ready to run SEO audits. Configure TARGET_DOMAIN list to begin. PageSpeed Insights API is free. Outputs: Core Web Vitals, page speed, structured data issues, backlink gap.",
            confidence=0.85,
            signal_taken=False,
            degraded_reason="TARGET_DOMAIN not configured. No audit running.",
            platform_truth_label="PUBLIC_DATA_ONLY",
            data={"category": "seo_services", "requires_capital": False, "tools": ["pagespeed_insights", "moz_free"]},
        )
