from __future__ import annotations

import uuid
from typing import Any

from engine_bridge.orders import adapter_place_order, build_normalized_order
from engine_bridge.reconciliation import build_execution_evidence
from engine_bridge.runtime_modes import mode_truth_label
from models.proposal import Proposal
from services import execution_authority
from services.risk_kernel import evaluate_proposal
from services.shadow_broker import execute_shadow_order
from services.truth_labels import normalize_runtime_mode, runtime_truth_label


def _build_forcefield_request(
    proposal: Proposal,
    risk_decision: dict[str, Any],
    execution_mode: str,
) -> dict[str, Any]:
    size_usd = float(risk_decision.get("size_usd", 0) or 0)
    fee_drag_bps = max(0.0, float(proposal.edge_bps or 0) - float(proposal.edge_post_fee_bps or 0))
    max_slippage_bps = max(0.0, float(proposal.max_slippage_bps or 0))
    fee_budget = size_usd * fee_drag_bps / 10000.0
    slippage_budget = size_usd * max_slippage_bps / 10000.0
    return {
        "proposal_id": proposal.proposal_id,
        "strategy_id": proposal.bot_id,
        "platform": proposal.platform,
        "venue_symbol": proposal.market_id,
        "side": proposal.side,
        "desired_notional": size_usd,
        "worst_case_loss": size_usd,
        "fee_budget": fee_budget,
        "slippage_budget": slippage_budget,
        "reason_code": proposal.reason_code,
        "runtime_mode": proposal.runtime_mode,
        "execution_mode": execution_mode,
        "idempotency_key": f"{proposal.proposal_id}:{execution_mode}",
    }


def _finalize_forcefield_reservation(
    db_module: Any,
    reservation: dict[str, Any] | None,
    *,
    order_ref: str = "",
    execution_mode: str = "",
    success: bool,
    hold_open: bool = True,
    payload: dict[str, Any] | None = None,
    reason: str = "",
) -> None:
    if not reservation or not reservation.get("reservation_id"):
        return
    try:
        from services.portfolio_forcefield import mark_reservation_executed, release_reservation

        if success:
            mark_reservation_executed(
                db_module,
                str(reservation["reservation_id"]),
                order_ref=order_ref,
                execution_mode=execution_mode,
                payload=payload,
                hold_open=hold_open,
            )
        else:
            release_reservation(
                db_module,
                str(reservation["reservation_id"]),
                reason=reason or "execution_failed",
                order_ref=order_ref,
                execution_mode=execution_mode,
                failed=True,
                payload=payload,
            )
    except Exception:
        pass


def _save_reconciliation_event(
    db_module: Any,
    proposal: Proposal,
    event_type: str,
    reconciliation_status: str,
    *,
    reason: str = "",
    payload: dict[str, Any] | None = None,
) -> None:
    if not hasattr(db_module, "save_reconciliation_event"):
        return
    try:
        db_module.save_reconciliation_event(
            proposal.platform or "proposal_router",
            proposal.proposal_id,
            event_type,
            reconciliation_status,
            reason=reason,
            payload={
                "proposal_id": proposal.proposal_id,
                "bot_id": proposal.bot_id,
                "market_id": proposal.market_id,
                **(payload or {}),
            },
        )
    except Exception:
        pass


def route_proposal(
    proposal: Proposal,
    *,
    db_module: Any,
    registry: Any,
    execution_mode: str | None = None,
    price_limit: float | None = None,
    executor_owner_id: str | None = None,
    risk_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    mode = normalize_runtime_mode(execution_mode or proposal.runtime_mode, default="paper")
    proposal_payload = proposal.model_dump()
    db_module.save_proposal_log(proposal_payload, decision_state="pending")
    _risk_ctx = risk_context or {}
    # Provide safe defaults for required risk params if caller omits them
    if "working_capital" not in _risk_ctx:
        _risk_ctx = {"working_capital": 100.0, "floor": 80.0, "repel_zone": 15.0, **_risk_ctx}
    risk_decision = evaluate_proposal(
        proposal,
        runtime_mode=mode,
        **_risk_ctx,
    )
    _save_reconciliation_event(
        db_module,
        proposal,
        "proposal_decision",
        str(risk_decision.get("decision", "unknown") or "unknown"),
        reason=", ".join(risk_decision.get("reasons", []) or []),
        payload={
            "execution_mode": mode,
            "size_usd": float(risk_decision.get("size_usd", 0) or 0),
            "truth_label": runtime_truth_label(mode),
        },
    )
    db_module.set_proposal_decision_state(proposal.proposal_id, risk_decision["decision"])
    owner_state = execution_authority.get_state(db_module, owner_id=executor_owner_id)
    result = {
        "ok": True,
        "proposal": proposal_payload,
        "risk_decision": risk_decision,
        "owner_state": owner_state,
        "truth_label": runtime_truth_label(mode),
    }
    if risk_decision["decision"] == "reject":
        return result
    if mode in {"research", "replay", "paper", "live-disabled"}:
        _save_reconciliation_event(
            db_module,
            proposal,
            "proposal_routed",
            mode,
            payload={"truth_label": runtime_truth_label(mode)},
        )
        return result
    if not owner_state.get("owner_present"):
        _save_reconciliation_event(
            db_module,
            proposal,
            "execution_gate",
            "owner_missing",
            reason="No active execution owner lease is present.",
            payload={"execution_mode": mode},
        )
        return {
            **result,
            "ok": False,
            "error": "execution_owner_missing",
            "degraded_reason": "No active execution owner lease is present for executable routing.",
        }
    forcefield = None
    try:
        from services.portfolio_forcefield import approve_reservation

        forcefield = approve_reservation(
            db_module,
            _build_forcefield_request(proposal, risk_decision, mode),
        )
    except Exception as exc:
        _save_reconciliation_event(
            db_module,
            proposal,
            "forcefield_gate",
            "error",
            reason=str(exc),
            payload={"execution_mode": mode},
        )
        return {
            **result,
            "ok": False,
            "error": "forcefield_error",
            "degraded_reason": str(exc),
        }
    if not forcefield.get("approved"):
        action = str(forcefield.get("action", "CONTINUE") or "CONTINUE")
        _save_reconciliation_event(
            db_module,
            proposal,
            "forcefield_gate",
            action.lower(),
            reason=str(forcefield.get("reason", "") or ""),
            payload={
                "execution_mode": mode,
                "forcefield_action": action,
                "portfolio": forcefield.get("portfolio", {}),
            },
        )
        return {
            **result,
            "ok": False,
            "error": "forcefield_rejected",
            "forcefield": forcefield,
            "degraded_reason": action,
        }
    _save_reconciliation_event(
        db_module,
        proposal,
        "forcefield_reservation",
        "reserved",
        payload={
            "execution_mode": mode,
            "reservation_id": forcefield.get("reservation_id"),
            "approved_notional": float(forcefield.get("approved_notional", 0) or 0),
            "reserved_worst_loss": float(forcefield.get("reserved_worst_loss", 0) or 0),
        },
    )
    result["forcefield"] = forcefield
    execution_risk = {
        **risk_decision,
        "size_usd": float(forcefield.get("approved_notional", risk_decision.get("size_usd", 0)) or 0),
        "forcefield_reservation_id": forcefield.get("reservation_id"),
        "forcefield_approved_notional": forcefield.get("approved_notional"),
    }
    if mode == "shadow":
        shadow_result = execute_shadow_order(
            db_module,
            registry,
            proposal_payload,
            size_usd=float(execution_risk.get("size_usd", 0) or 0),
            price_limit=price_limit,
            risk_decision=execution_risk,
        )
        _finalize_forcefield_reservation(
            db_module,
            forcefield,
            order_ref=str(shadow_result.get("shadow_order_id") or shadow_result.get("request_id") or ""),
            execution_mode="shadow",
            success=bool(shadow_result.get("ok")),
            hold_open=False,
            payload=shadow_result,
            reason=str(shadow_result.get("error", "") or shadow_result.get("degraded_reason", "") or ""),
        )
        final = {**result, **shadow_result}
        try:
            from services.notification_center import notify_proposal_executed
            notify_proposal_executed(proposal_payload, execution_risk, shadow_result, db_module=db_module)
        except Exception:
            pass
        return final
    if mode == "demo":
        demo_result = _execute_demo_order(
            db_module, registry, proposal, execution_risk, price_limit
        )
        _finalize_forcefield_reservation(
            db_module,
            forcefield,
            order_ref=str(demo_result.get("order_id", "") or ""),
            execution_mode="demo",
            success=bool(demo_result.get("ok")),
            payload=demo_result,
            reason=str(demo_result.get("error", "") or demo_result.get("degraded_reason", "") or ""),
        )
        final = {**result, **demo_result}
        try:
            from services.notification_center import notify_proposal_executed
            notify_proposal_executed(proposal_payload, execution_risk, demo_result, db_module=db_module)
        except Exception:
            pass
        return final

    if mode == "live":
        if not owner_state.get("owned_by_self"):
            _save_reconciliation_event(
                db_module,
                proposal,
                "execution_gate",
                "owner_not_self",
                reason="Live execution requires ownership by the current executor.",
                payload={"execution_mode": mode},
            )
            return {
                **result,
                "ok": False,
                "error": "execution_owner_not_self",
                "degraded_reason": "Live execution requires active execution authority ownership.",
            }
        live_result = _execute_live_order(
            db_module, registry, proposal, execution_risk, price_limit
        )
        _finalize_forcefield_reservation(
            db_module,
            forcefield,
            order_ref=str(live_result.get("order_id", "") or ""),
            execution_mode="live",
            success=bool(live_result.get("ok")),
            payload=live_result,
            reason=str(live_result.get("error", "") or live_result.get("degraded_reason", "") or ""),
        )
        final = {**result, **live_result}
        try:
            from services.notification_center import notify_proposal_executed
            notify_proposal_executed(proposal_payload, execution_risk, live_result, db_module=db_module)
        except Exception:
            pass
        return final

    return result


def _execute_demo_order(
    db_module: Any,
    registry: Any,
    proposal: Proposal,
    risk_decision: dict[str, Any],
    price_limit: float | None,
) -> dict[str, Any]:
    """Route order to venue demo/sandbox API."""
    platform = proposal.platform
    market_id = proposal.market_id
    size_usd = float(risk_decision.get("size_usd", 0) or 0)
    truth = runtime_truth_label("demo")

    try:
        adapter = None
        if registry is not None:
            adapter = registry.get(platform)
            if adapter is None:
                adapter = registry.get(f"{platform.split('+')[0]}_demo")

        if adapter is None or not hasattr(adapter, "place_order"):
            return execute_shadow_order(
                db_module,
                registry,
                proposal.model_dump(),
                size_usd=size_usd,
                price_limit=price_limit,
                risk_decision=risk_decision,
            )

        if hasattr(adapter, "supports_mode") and not adapter.supports_mode("demo"):
            return {
                "ok": False,
                "error": "mode_not_supported",
                "degraded_reason": f"{getattr(adapter, 'platform_name', platform)} does not support demo mode.",
                "truth_label": truth,
            }

        normalized_order = build_normalized_order(
            proposal=proposal,
            execution_mode="demo",
            size=size_usd,
            price_limit=price_limit,
        )

        order_response = adapter_place_order(adapter, normalized_order)
        engine_evidence = build_execution_evidence(adapter, normalized_order.to_dict(), order_response, normalized_order=normalized_order)

        order_id = order_response.get("order_id", f"demo_{uuid.uuid4().hex[:12]}")
        filled = bool(order_response.get("ok", False))
        fill_price = order_response.get("fill_price")

        db_module.save_order_lifecycle({
            "order_id": order_id,
            "bot_id": proposal.bot_id,
            "platform": platform,
            "execution_mode": "DEMO",
            "side": proposal.side,
            "market_id": market_id,
            "amount": size_usd,
            "price": fill_price or price_limit,
            "status": "filled" if filled else "demo_pending",
            "fill_price": fill_price,
            "fill_amount": size_usd if filled else None,
            "proposal_id": proposal.proposal_id,
            "truth_label": truth,
            "payload": {"risk_decision": risk_decision, **engine_evidence},
        })
        _save_reconciliation_event(
            db_module,
            proposal,
            "demo_execution",
            "filled" if filled else "demo_pending",
            reason=str(order_response.get("error", "") or ""),
            payload={
                "order_id": order_id,
                "fill_price": fill_price,
                "filled": filled,
                "size_usd": size_usd,
                **engine_evidence,
            },
        )

        return {
            "ok": True,
            "order_id": order_id,
            "filled": filled,
            "fill_price": fill_price,
            "truth_label": truth,
            "engine": engine_evidence,
        }
    except Exception as exc:
        _save_reconciliation_event(
            db_module,
            proposal,
            "demo_execution",
            "failed",
            reason=str(exc),
            payload={"size_usd": size_usd},
        )
        return {
            "ok": False,
            "error": "demo_execution_failed",
            "degraded_reason": str(exc),
            "truth_label": truth,
        }


def _execute_live_order(
    db_module: Any,
    registry: Any,
    proposal: Proposal,
    risk_decision: dict[str, Any],
    price_limit: float | None,
) -> dict[str, Any]:
    """Route order to live venue API. REAL MONEY."""
    platform = proposal.platform
    market_id = proposal.market_id
    size_usd = float(risk_decision.get("size_usd", 0) or 0)
    truth = runtime_truth_label("live")

    if size_usd <= 0:
        _save_reconciliation_event(
            db_module,
            proposal,
            "live_execution",
            "zero_size",
            reason="Live execution rejected because proposed size was zero.",
            payload={"size_usd": size_usd},
        )
        return {"ok": False, "error": "zero_size_live", "truth_label": truth}

    try:
        adapter = None
        if registry is not None:
            adapter = registry.get(f"{platform.split('+')[0]}_live")
            if adapter is None:
                adapter = registry.get(platform)

        if adapter is None or not hasattr(adapter, "place_order"):
            _save_reconciliation_event(
                db_module,
                proposal,
                "live_execution",
                "no_adapter",
                reason=f"No live adapter found for {platform}",
                payload={"size_usd": size_usd},
            )
            return {
                "ok": False,
                "error": "no_live_adapter",
                "degraded_reason": f"No live adapter found for {platform}",
                "truth_label": truth,
            }

        if not getattr(adapter, "execution_enabled", False):
            _save_reconciliation_event(
                db_module,
                proposal,
                "live_execution",
                "execution_disabled",
                reason="Live adapter exists but execution is disabled.",
                payload={"size_usd": size_usd},
            )
            return {
                "ok": False,
                "error": "execution_disabled",
                "degraded_reason": "Live adapter exists but execution_enabled is False",
                "truth_label": runtime_truth_label("live-disabled"),
            }

        if hasattr(adapter, "supports_mode") and not adapter.supports_mode("live"):
            return {
                "ok": False,
                "error": "mode_not_supported",
                "degraded_reason": f"{getattr(adapter, 'platform_name', platform)} does not support live mode.",
                "truth_label": truth,
            }

        normalized_order = build_normalized_order(
            proposal=proposal,
            execution_mode="live",
            size=size_usd,
            price_limit=price_limit,
        )
        order_response = adapter_place_order(adapter, normalized_order)
        engine_evidence = build_execution_evidence(adapter, normalized_order.to_dict(), order_response, normalized_order=normalized_order)

        order_id = order_response.get("order_id", f"live_{uuid.uuid4().hex[:12]}")
        filled = bool(order_response.get("ok", False))
        fill_price = order_response.get("fill_price")

        db_module.save_order_lifecycle({
            "order_id": order_id,
            "bot_id": proposal.bot_id,
            "platform": platform,
            "execution_mode": "LIVE",
            "side": proposal.side,
            "market_id": market_id,
            "amount": size_usd,
            "price": fill_price or price_limit,
            "status": "filled" if filled else "pending",
            "fill_price": fill_price,
            "fill_amount": size_usd if filled else None,
            "proposal_id": proposal.proposal_id,
            "truth_label": "LIVE — REAL CAPITAL",
            "payload": {"risk_decision": risk_decision, **engine_evidence},
        })
        _save_reconciliation_event(
            db_module,
            proposal,
            "live_execution",
            "filled" if filled else "pending",
            reason=str(order_response.get("error", "") or ""),
            payload={
                "order_id": order_id,
                "fill_price": fill_price,
                "filled": filled,
                "size_usd": size_usd,
                **engine_evidence,
            },
        )

        return {
            "ok": True,
            "order_id": order_id,
            "filled": filled,
            "fill_price": fill_price,
            "truth_label": "LIVE — REAL CAPITAL",
            "engine": engine_evidence,
        }
    except Exception as exc:
        _save_reconciliation_event(
            db_module,
            proposal,
            "live_execution",
            "failed",
            reason=str(exc),
            payload={"size_usd": size_usd},
        )
        return {
            "ok": False,
            "error": "live_execution_failed",
            "degraded_reason": str(exc),
            "truth_label": truth,
        }
