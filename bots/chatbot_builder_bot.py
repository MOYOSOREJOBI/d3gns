from __future__ import annotations
import logging
from typing import Any
from bots.base_research_bot import BaseResearchBot

logger = logging.getLogger(__name__)

CHATBOT_FLOWS = [
    {
        "use_case"  : "Lead Qualification Bot",
        "industry"  : "B2B SaaS",
        "steps"     : ["Greet", "Collect name/email", "Ask pain point", "Route to CRM"],
        "platform"  : "Tidio or Intercom",
        "price_est" : "$300-800 one-time",
    },
    {
        "use_case"  : "FAQ Bot for Local Business",
        "industry"  : "Restaurant/Retail",
        "steps"     : ["Hours/menu query", "Booking inquiry", "Escalate to human"],
        "platform"  : "ManyChat or WhatsApp Business",
        "price_est" : "$200-500 one-time",
    },
    {
        "use_case"  : "E-commerce Support Bot",
        "industry"  : "Shopify Store",
        "steps"     : ["Order tracking", "Return policy", "Product recommendation"],
        "platform"  : "Tidio",
        "price_est" : "$400-700 one-time + $100/mo maintenance",
    },
]


class ChatbotBuilderBot(BaseResearchBot):
    bot_id = "bot_chatbot_builder"
    display_name = "Chatbot Builder Bot"
    platform = "content_automation"
    mode = "RESEARCH"
    signal_type = "service_opportunity"
    research_only = True
    implemented = True
    truth_label = "PUBLIC_DATA_ONLY"
    quality_tier = "B"
    risk_tier = "low"
    description = "Generates chatbot flow blueprints for service delivery. Identifies use cases to pitch as a chatbot-as-a-service offering."
    edge_source = "High-demand, low-competition chatbot service for small businesses"
    opp_cadence_per_day = 3.0
    platforms = ["tidio", "manychat"]

    def __init__(self, adapter=None):
        self.adapter = adapter
        self._cycle  = 0

    def run_one_cycle(self) -> dict[str, Any]:
        flow = CHATBOT_FLOWS[self._cycle % len(CHATBOT_FLOWS)]
        self._cycle += 1

        return self.emit_signal(
            title=f"Chatbot Blueprint: {flow['use_case']}",
            summary=(
                f"{flow['industry']} | Platform: {flow['platform']} | "
                f"Price: {flow['price_est']}"
            ),
            confidence=0.74,
            signal_taken=True,
            data={**flow, "requires_capital": False, "action": "build_and_pitch"},
        )
