from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class WithdrawalRequest(BaseModel):
    request_id: str
    bot_id: str = ""
    venue: str = ""
    amount: float
    currency: str = "USD"
    destination_type: str = "internal_vault"
    destination_ref_masked: str = ""
    status: str = "requested"
    operator_note: str = ""
    external_ref: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
