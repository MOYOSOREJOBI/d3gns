from __future__ import annotations

import uuid
from typing import Any

from services.truth_labels import runtime_truth_label


def _coerce_price(value: Any) -> float | None:
    try:
        numeric = float(value)
        return numeric / 100.0 if numeric > 1.0 else numeric
    except Exception:
        return None


def _extract_book_levels(payload: dict[str, Any], keys: tuple[str, ...]) -> list[dict[str, float]]:
    for key in keys:
        raw = payload.get(key)
        if not isinstance(raw, list):
            continue
        levels: list[dict[str, float]] = []
        for row in raw[:5]:
            if not isinstance(row, dict):
                continue
            price = _coerce_price(row.get("price"))
            size = row.get("size", row.get("quantity", row.get("qty", row.get("volume", 0))))
            try:
                levels.append({"price": float(price or 0), "size": float(size or 0)})
            except Exception:
                continue
        if levels:
            return levels
    return []


def _best_shadow_price(payload: dict[str, Any], side: str, metadata: dict[str, Any]) -> float | None:
    side_upper = str(side or "").upper()
    asks = _extract_book_levels(payload, ("asks", "sell", "offers"))
    bids = _extract_book_levels(payload, ("bids", "buy"))
    if "BUY" in side_upper and asks:
        return min(level["price"] for level in asks if level["price"] > 0)
    if "SELL" in side_upper and bids:
        return max(level["price"] for level in bids if level["price"] > 0)

    yes_levels = _extract_book_levels(payload, ("yes", "yes_levels", "buy_yes"))
    no_levels = _extract_book_levels(payload, ("no", "no_levels", "buy_no"))
    if "YES" in side_upper and yes_levels:
        prices = [level["price"] for level in yes_levels if level["price"] > 0]
        if prices:
            return max(prices) / 100.0 if max(prices) > 1 else max(prices)
    if "NO" in side_upper and no_levels:
        prices = [level["price"] for level in no_levels if level["price"] > 0]
        if prices:
            return max(prices) / 100.0 if max(prices) > 1 else max(prices)

    for key in ("reference_price", "kalshi_price", "poly_price", "mark_price", "index_price"):
        price = _coerce_price(metadata.get(key))
        if price is not None:
            return price
    return None


def _within_limit(fill_price: float | None, side: str, price_limit: float | None) -> bool:
    if fill_price is None or price_limit is None:
        return fill_price is not None
    side_upper = str(side or "").upper()
    if "BUY" in side_upper:
        return fill_price <= price_limit
    if "SELL" in side_upper:
        return fill_price >= price_limit
    return True


def execute_shadow_order(
    db_module: Any,
    registry: Any,
    proposal: dict[str, Any],
    *,
    size_usd: float,
    price_limit: float | None = None,
    risk_decision: dict[str, Any] | None = None,
) -> dict[str, Any]:
    platform = str(proposal.get("platform", "") or "")
    market_id = str(proposal.get("market_id", "") or "")
    metadata = dict(proposal.get("metadata") or {})
    orderbook_payload: dict[str, Any] = {}
    degraded_reason = ""

    try:
        adapter = registry.get(platform)
        if market_id and hasattr(adapter, "get_orderbook"):
            response = adapter.get_orderbook(market_id)
            orderbook_payload = (response.get("data") or {}).get("orderbook") or {}
            if not response.get("ok"):
                degraded_reason = response.get("degraded_reason", "")
    except Exception as exc:
        degraded_reason = str(exc)

    synthetic_fill_price = _best_shadow_price(orderbook_payload, str(proposal.get("side", "")), metadata)
    filled = _within_limit(synthetic_fill_price, str(proposal.get("side", "")), price_limit)
    state = "filled" if filled else "shadow_pending"
    shadow_order_id = f"s_{uuid.uuid4().hex[:12]}"
    request_id = db_module.save_order_request(
        platform=platform,
        bot_id=str(proposal.get("bot_id", "")),
        market_id=market_id,
        side=str(proposal.get("side", "")),
        size=float(size_usd or 0),
        price=float(synthetic_fill_price or price_limit or 0),
        execution_mode="SHADOW",
        state=state,
        payload={
            "proposal_id": proposal.get("proposal_id", ""),
            "truth_label": runtime_truth_label("shadow"),
            "price_limit": price_limit,
            "risk_decision": risk_decision or {},
            "orderbook": orderbook_payload,
        },
    )
    if filled and synthetic_fill_price is not None:
        db_module.save_order_fill(
            request_id,
            platform,
            synthetic_fill_price,
            float(size_usd or 0),
            fill_type="synthetic",
            payload={"proposal_id": proposal.get("proposal_id", ""), "truth_label": runtime_truth_label("shadow")},
        )
    db_module.save_order_lifecycle(
        {
            "order_id": shadow_order_id,
            "bot_id": str(proposal.get("bot_id", "")),
            "platform": platform,
            "execution_mode": "SHADOW",
            "side": str(proposal.get("side", "")),
            "market_id": market_id,
            "amount": float(size_usd or 0),
            "price": synthetic_fill_price or price_limit,
            "status": state,
            "fill_price": synthetic_fill_price if filled else None,
            "fill_amount": float(size_usd or 0) if filled else None,
            "proposal_id": proposal.get("proposal_id", ""),
            "truth_label": runtime_truth_label("shadow"),
            "payload": {
                "risk_decision": risk_decision or {},
                "orderbook": orderbook_payload,
                "degraded_reason": degraded_reason,
            },
        }
    )
    db_module.save_reconciliation_event(
        platform,
        str(request_id),
        "shadow_execution",
        state,
        reason=degraded_reason,
        payload={
            "shadow_order_id": shadow_order_id,
            "proposal_id": proposal.get("proposal_id", ""),
            "synthetic_fill_price": synthetic_fill_price,
            "filled": filled,
            "truth_label": "SHADOW — NO EXTERNAL ORDER SENT",
        },
    )
    return {
        "ok": True,
        "shadow_order_id": shadow_order_id,
        "request_id": request_id,
        "filled": filled,
        "synthetic_fill_price": synthetic_fill_price,
        "truth_label": "SHADOW — NO EXTERNAL ORDER SENT",
        "degraded_reason": degraded_reason,
    }
