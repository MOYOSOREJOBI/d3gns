from __future__ import annotations

import logging
from typing import Any

from adapters.base_adapter import BaseAdapter


logger = logging.getLogger(__name__)

_DEFAULT_MAX_POSITION_USD = 10.0


class KalshiLiveAdapter(BaseAdapter):
    """
    Kalshi live adapter.

    Truth contract:
    - Registered in platform health like every other adapter.
    - Disabled by default even when credentials are present.
    - Requires explicit instance-level enablement before any live request path.
    - Uses the existing Kalshi credential env vars only:
      KALSHI_API_KEY, KALSHI_PRIVATE_KEY, ENABLE_KALSHI
    """

    platform_name = "kalshi_live"
    live_capable = True
    auth_required = True
    base_url = "https://trading-api.kalshi.com/trade-api/v2"
    data_truth_label = "LIVE DISABLED"

    def get_mode(self) -> str:
        return "LIVE" if self.execution_enabled else "LIVE DISABLED"

    def _truth(self, *, auth_validated: bool = False, auth_truth: str = "missing") -> dict[str, Any]:
        label = self.get_mode()
        return {
            "mode": label,
            "live_capable": True,
            "execution_enabled": bool(self.execution_enabled),
            "auth_validated": bool(auth_validated),
            "auth_truth": auth_truth,
            "data_truth_label": label,
        }

    def _kalshi_enabled(self) -> bool:
        return self._bool_setting("ENABLE_KALSHI", False)

    def _api_key(self) -> str:
        return self._setting("KALSHI_API_KEY", "").strip()

    def _private_key(self) -> str:
        return self._setting("KALSHI_PRIVATE_KEY", "").strip()

    def _max_position_usd(self) -> float:
        try:
            return float(self._setting("KALSHI_MAX_POSITION_USD", str(_DEFAULT_MAX_POSITION_USD)))
        except Exception:
            return _DEFAULT_MAX_POSITION_USD

    def is_configured(self) -> bool:
        return self._kalshi_enabled() and bool(self._api_key() and self._private_key())

    def _config_check(self) -> dict[str, Any] | None:
        if not self._kalshi_enabled():
            return self._error(
                "disabled",
                "Kalshi live adapter is disabled. Set ENABLE_KALSHI=true to expose it.",
                degraded_reason="ENABLE_KALSHI=false",
                status="disabled",
                auth_truth="missing",
            )
        if not self._api_key() or not self._private_key():
            return self._error(
                "not_configured",
                "Kalshi live requires KALSHI_API_KEY and KALSHI_PRIVATE_KEY.",
                degraded_reason="Missing Kalshi live credentials.",
                status="not_configured",
                auth_truth="missing",
            )
        return None

    def _disabled_live_error(self, operation: str) -> dict[str, Any]:
        return self._error(
            "live_disabled",
            f"{operation} is disabled by default for Kalshi live.",
            degraded_reason=(
                "Kalshi live execution remains disabled by default. "
                "This adapter is registered for truth labeling and future lifecycle wiring only."
            ),
            status="disabled",
            auth_truth="present",
        )

    def _auth_headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if self._api_key():
            headers["Authorization"] = f"Bearer {self._api_key()}"
        if self._private_key():
            headers["X-Kalshi-Private-Key"] = self._private_key()
        return headers

    def _live_request(self, method: str, path: str, **kwargs) -> dict[str, Any] | Any:
        err = self._config_check()
        if err:
            return err
        if not self.execution_enabled:
            return self._disabled_live_error(path)
        try:
            return self._request(method, path, headers=self._auth_headers(), **kwargs)
        except Exception as exc:
            logger.warning("Kalshi live request failed for %s %s: %s", method, path, exc)
            return self._error(
                "request_failed",
                f"Kalshi live request failed: {exc}",
                degraded_reason=f"Kalshi live request to {path} failed.",
                status="degraded",
                auth_truth="failed",
            )

    def healthcheck(self) -> dict[str, Any]:
        err = self._config_check()
        if err:
            return err
        if not self.execution_enabled:
            return self._ok(
                {
                    "execution_mode": "LIVE DISABLED",
                    "execution_enabled": False,
                    "truth_note": (
                        "Kalshi live adapter is installed but intentionally disabled. "
                        "No live orders will be sent from this path by default."
                    ),
                    "max_position_usd": self._max_position_usd(),
                },
                status="live_disabled",
                auth_truth="present",
                degraded_reason="Kalshi live is registered but disabled by default.",
            )
        return self._ok(
            {
                "execution_mode": "LIVE",
                "execution_enabled": True,
                "truth_note": "Kalshi live execution has been explicitly enabled on this adapter instance.",
                "max_position_usd": self._max_position_usd(),
            },
            status="ready",
            auth_truth="present",
        )

    def list_markets(self, **kwargs) -> dict[str, Any]:
        response = self._live_request("GET", "/markets", params={"limit": kwargs.get("limit", 25)})
        if isinstance(response, dict):
            return response
        payload = response.json()
        markets = payload.get("markets", payload if isinstance(payload, list) else [])
        return self._ok({"markets": markets, "count": len(markets) if isinstance(markets, list) else 0}, status="ready", auth_truth="validated")

    def get_market(self, market_id: str, **kwargs) -> dict[str, Any]:
        response = self._live_request("GET", f"/markets/{market_id}")
        if isinstance(response, dict):
            return response
        payload = response.json()
        return self._ok({"market": payload.get("market", payload)}, status="ready", auth_truth="validated")

    def get_orderbook(self, market_id: str, **kwargs) -> dict[str, Any]:
        response = self._live_request("GET", f"/markets/{market_id}/orderbook")
        if isinstance(response, dict):
            return response
        raw = response.json()
        book = raw.get("orderbook", raw)
        normalized = self._normalize_orderbook(book)
        return self._ok(
            {"orderbook": normalized, "raw": book, "market_id": market_id},
            status="ready",
            auth_truth="validated",
        )

    @staticmethod
    def _normalize_orderbook(raw: dict) -> dict:
        """Convert Kalshi fixed-point orderbook to generic asks/bids format."""
        asks = []
        bids = []
        yes_data = raw.get("yes") or raw.get("yes_levels") or []
        no_data = raw.get("no") or raw.get("no_levels") or []
        for level in yes_data:
            if isinstance(level, (list, tuple)) and len(level) >= 2:
                price_cents, size = level[0], level[1]
            elif isinstance(level, dict):
                price_cents = level.get("price", level.get("yes_price", 0))
                size = level.get("size", level.get("quantity", level.get("count", 0)))
            else:
                continue
            price = float(price_cents) / 100.0 if float(price_cents) > 1 else float(price_cents)
            asks.append({"price": round(price, 4), "size": int(size)})
        for level in no_data:
            if isinstance(level, (list, tuple)) and len(level) >= 2:
                price_cents, size = level[0], level[1]
            elif isinstance(level, dict):
                price_cents = level.get("price", level.get("no_price", 0))
                size = level.get("size", level.get("quantity", level.get("count", 0)))
            else:
                continue
            price = float(price_cents) / 100.0 if float(price_cents) > 1 else float(price_cents)
            bids.append({"price": round(1.0 - price, 4), "size": int(size)})
        asks.sort(key=lambda x: x["price"])
        bids.sort(key=lambda x: x["price"], reverse=True)
        return {"asks": asks, "bids": bids}

    def get_balance(self, **kwargs) -> dict[str, Any]:
        response = self._live_request("GET", "/portfolio/balance")
        if isinstance(response, dict):
            return response
        payload = response.json()
        return self._ok(
            {
                "balance": payload.get("balance", payload),
                "execution_mode": "LIVE",
                "truth_note": "LIVE balance — real funds if this path is explicitly enabled and authenticated.",
            },
            status="ready",
            auth_truth="validated",
        )

    def place_order(
        self,
        ticker: str,
        side: str,
        amount_usd: float,
        price_cents: int,
        order_type: str = "limit",
        **kwargs,
    ) -> dict[str, Any]:
        err = self._config_check()
        if err:
            return err
        if not self.execution_enabled:
            return self._disabled_live_error("place_order")
        if amount_usd > self._max_position_usd():
            return self._error(
                "position_size_exceeded",
                f"Order amount ${amount_usd:.2f} exceeds cap ${self._max_position_usd():.2f}.",
                degraded_reason=f"Order blocked: amount ${amount_usd:.2f} > cap ${self._max_position_usd():.2f}.",
                status="blocked",
                auth_truth="present",
            )
        count = max(1, int(amount_usd * 100 / max(int(price_cents or 1), 1)))
        order_body = {
            "ticker": ticker,
            "type": order_type,
            "action": "buy",
            "side": side,
            "count": count,
            "yes_price": price_cents if side == "yes" else (100 - price_cents),
            "no_price": price_cents if side == "no" else (100 - price_cents),
        }
        response = self._live_request("POST", "/portfolio/orders", json_body=order_body)
        if isinstance(response, dict):
            return response
        payload = response.json()
        order = payload.get("order", payload)
        status = str(order.get("status") or "placed").lower()
        return self._ok(
            {
                "order": order,
                "execution_mode": "LIVE",
                "ticker": ticker,
                "side": side,
                "amount_usd": amount_usd,
                "price_cents": price_cents,
                "truth_note": "LIVE ORDER — real funds at risk when explicitly enabled.",
            },
            status=status,
            auth_truth="validated",
        )

    def cancel_order(self, order_id: str, **kwargs) -> dict[str, Any]:
        response = self._live_request("DELETE", f"/portfolio/orders/{order_id}")
        if isinstance(response, dict):
            return response
        return self._ok(
            {"order_id": order_id, "cancelled": True, "response": response.json(), "execution_mode": "LIVE"},
            status="cancelled",
            auth_truth="validated",
        )

    def get_order(self, order_id: str, **kwargs) -> dict[str, Any]:
        response = self._live_request("GET", f"/portfolio/orders/{order_id}")
        if isinstance(response, dict):
            return response
        payload = response.json()
        return self._ok({"order": payload.get("order", payload), "execution_mode": "LIVE"}, status="ready", auth_truth="validated")

    def get_positions(self, **kwargs) -> dict[str, Any]:
        response = self._live_request("GET", "/portfolio/positions")
        if isinstance(response, dict):
            return response
        payload = response.json()
        positions = payload.get("market_positions", payload if isinstance(payload, list) else [])
        return self._ok(
            {
                "positions": positions if isinstance(positions, list) else [],
                "execution_mode": "LIVE",
                "truth_note": "LIVE positions — real funds if live execution is explicitly enabled.",
            },
            status="ready",
            auth_truth="validated",
        )
