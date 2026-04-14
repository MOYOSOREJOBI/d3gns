from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class Proposal(BaseModel):
    proposal_id: str
    bot_id: str
    platform: str
    market_id: str = ""
    side: str
    confidence: float = 0.0
    edge_bps: float = 0.0
    edge_post_fee_bps: float = 0.0
    expected_hold_s: float = 0.0
    max_slippage_bps: float = 0.0
    correlation_key: str = ""
    reason_code: str = ""
    runtime_mode: str = "paper"
    truth_label: str = "PAPER - NO REAL ORDER"
    metadata: dict[str, Any] = Field(default_factory=dict)
