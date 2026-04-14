from __future__ import annotations
import logging
from typing import Any
from bots.base_research_bot import BaseResearchBot

logger = logging.getLogger(__name__)

SEQUENCES = [
    {
        "name": "Freelance Client Nurture",
        "emails": 5,
        "goal": "Convert inquiry to paid project",
        "triggers": ["new_lead", "proposal_sent"],
    },
    {
        "name": "Product Launch Sequence",
        "emails": 7,
        "goal": "Drive digital product sales",
        "triggers": ["signup", "cart_abandon"],
    },
    {
        "name": "Re-engagement Campaign",
        "emails": 3,
        "goal": "Reactivate cold leads",
        "triggers": ["60_days_inactive"],
    },
]


class EmailAutomationBot(BaseResearchBot):
    bot_id = "bot_email_automation"
    display_name = "Email Automation Bot"
    platform = "content_automation"
    mode = "RESEARCH"
    signal_type = "automation_opportunity"
    research_only = True
    implemented = True
    truth_label = "PUBLIC_DATA_ONLY"
    quality_tier = "B"
    risk_tier = "low"
    description = "Generates email sequence templates and automation flows for lead nurture, product launches, and re-engagement campaigns."
    edge_source = "Email automation for scalable revenue without ad spend"
    opp_cadence_per_day = 3.0
    platforms = ["mailchimp", "convertkit", "beehiiv"]

    def __init__(self, adapter=None):
        self.adapter = adapter
        self._cycle  = 0

    def run_one_cycle(self) -> dict[str, Any]:
        seq = SEQUENCES[self._cycle % len(SEQUENCES)]
        self._cycle += 1

        data = {
            "sequence_name"  : seq["name"],
            "email_count"    : seq["emails"],
            "goal"           : seq["goal"],
            "trigger_events" : seq["triggers"],
            "requires_capital": False,
            "action"         : "build_sequence",
            "platform"       : "convertkit",
        }

        return self.emit_signal(
            title=f"Email Sequence: {seq['name']}",
            summary=f"{seq['emails']}-email sequence to {seq['goal'].lower()}",
            confidence=0.73,
            signal_taken=True,
            data=data,
        )
