"""
Auto-withdrawal: when MALL revenue clears, lock to vault and optionally
request external withdrawal through the withdrawal service.
"""
from __future__ import annotations

import logging
from typing import Any

from services.withdrawal_service import create_withdrawal_request

logger = logging.getLogger(__name__)

MIN_AUTO_WITHDRAW_USD = 25.0
DEFAULT_DESTINATION = "internal_vault"


def check_auto_withdrawal(
    db_module: Any,
    vault: Any,
    *,
    min_amount: float = MIN_AUTO_WITHDRAW_USD,
    destination_type: str = DEFAULT_DESTINATION,
    destination_ref: str = "",
) -> dict[str, Any]:
    """
    Check if any MALL revenue has cleared and should be auto-withdrawn.

    Pipeline stages for revenue:
      discovered -> reviewed -> contacted -> quoted -> booked -> paid -> cleared -> vaulted
    """
    try:
        cleared = (
            db_module.get_mall_pipeline(stage="cleared")
            if hasattr(db_module, "get_mall_pipeline")
            else []
        )

        total_cleared = sum(
            float(item.get("value_estimate", 0) or 0) for item in cleared
        )

        if total_cleared < min_amount:
            return {
                "action": "below_minimum",
                "cleared": round(total_cleared, 2),
                "min": min_amount,
            }

        locked = vault.lock("mall_auto", total_cleared, reason="mall_revenue_cleared")

        for item in cleared:
            if hasattr(db_module, "update_mall_pipeline_stage"):
                db_module.update_mall_pipeline_stage(item.get("id"), "vaulted")
            if hasattr(db_module, "mall_add_history_event"):
                db_module.mall_add_history_event(
                    int(item.get("id")),
                    "vaulted",
                    actor="auto_withdraw",
                    previous_stage="cleared",
                    new_stage="vaulted",
                    amount=float(item.get("paid_amount") or item.get("quoted_amount") or item.get("value_estimate") or 0),
                    notes="Auto-withdraw vaulted cleared Mall revenue",
                    payload={"source": "auto_withdraw"},
                )
            if hasattr(db_module, "save_reconciliation_event"):
                db_module.save_reconciliation_event(
                    "mall",
                    str(item.get("id")),
                    "vaulted",
                    "vaulted",
                    reason="Cleared Mall revenue vaulted",
                    payload={"source": "auto_withdraw"},
                )

        result: dict[str, Any] = {
            "action": "vaulted",
            "amount": round(float(locked), 2),
            "items": len(cleared),
        }

        if destination_type != "internal_vault" and float(locked) >= min_amount:
            wr = create_withdrawal_request(
                db_module,
                {
                    "bot_id": "mall_auto",
                    "venue": "mall",
                    "amount": float(locked),
                    "currency": "USD",
                    "destination_type": destination_type,
                    "destination_ref_masked": destination_ref,
                    "operator_note": f"Auto-withdrawal of ${float(locked):.2f} MALL revenue",
                },
            )
            result["withdrawal_request_id"] = wr.request_id
            result["action"] = "withdrawal_requested"

        logger.info(
            "[AutoWithdraw] %s: $%.2f from %d items",
            result["action"],
            float(locked),
            len(cleared),
        )
        return result

    except Exception as exc:
        logger.error("[AutoWithdraw] Error: %s", exc)
        return {"action": "error", "error": str(exc)}
