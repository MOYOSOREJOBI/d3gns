from __future__ import annotations

import uuid
from typing import Any

from models.withdrawal import WithdrawalRequest


def create_withdrawal_request(db_module: Any, body: dict[str, Any]) -> WithdrawalRequest:
    request_id = str(body.get("request_id") or f"w_{uuid.uuid4().hex[:12]}")
    record = {
        "request_id": request_id,
        "bot_id": str(body.get("bot_id", "") or ""),
        "venue": str(body.get("venue", "") or ""),
        "amount": float(body.get("amount", 0) or 0),
        "currency": str(body.get("currency", "USD") or "USD"),
        "destination_type": str(body.get("destination_type", "internal_vault") or "internal_vault"),
        "destination_ref_masked": str(body.get("destination_ref_masked", "") or ""),
        "status": "requested",
        "operator_note": str(body.get("operator_note") or body.get("note") or ""),
        "external_ref": "",
        "payload": dict(body.get("payload") or {}),
    }
    db_module.save_withdrawal_request(record)
    db_module.save_wallet_tx(
        "withdraw_request",
        record["amount"],
        currency=record["currency"],
        platform=record["venue"] or None,
        note=record["operator_note"] or "Withdrawal request",
    )
    return WithdrawalRequest(**record)


def approve_withdrawal_request(
    db_module: Any,
    request_id: str,
    *,
    approved: bool,
    operator_note: str = "",
    external_ref: str = "",
    payload: dict[str, Any] | None = None,
) -> WithdrawalRequest | None:
    current = db_module.get_withdrawal_request(request_id)
    if not current:
        return None
    status = "approved" if approved else "failed"
    merged_payload = dict(current.get("payload") or {})
    if payload:
        merged_payload.update(payload)
    db_module.update_withdrawal_request_status(
        request_id,
        status=status,
        operator_note=operator_note,
        external_ref=external_ref,
        payload=merged_payload,
    )
    db_module.save_wallet_tx(
        "withdraw_approved" if approved else "withdraw_declined",
        float(current.get("amount", 0) or 0),
        currency=str(current.get("currency", "USD") or "USD"),
        platform=str(current.get("venue", "") or None),
        note=operator_note or ("Withdrawal approved" if approved else "Withdrawal declined"),
    )
    updated = db_module.get_withdrawal_request(request_id)
    if not updated:
        return None
    return WithdrawalRequest(
        request_id=str(updated.get("request_id", "")),
        bot_id=str(updated.get("bot_id", "")),
        venue=str(updated.get("venue", "")),
        amount=float(updated.get("amount", 0) or 0),
        currency=str(updated.get("currency", "USD") or "USD"),
        destination_type=str(updated.get("destination_type", "internal_vault") or "internal_vault"),
        destination_ref_masked=str(updated.get("destination_ref_masked", "") or ""),
        status=str(updated.get("status", "requested") or "requested"),
        operator_note=str(updated.get("operator_note", "") or ""),
        external_ref=str(updated.get("external_ref", "") or ""),
        payload=dict(updated.get("payload") or {}),
    )
