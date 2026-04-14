from __future__ import annotations

from typing import Any

from adapters.base_adapter import BaseAdapter


class KalshiDemoAdapter(BaseAdapter):
    """
    Kalshi demo environment adapter.

    Truth labels: DEMO
    Execution:    DISABLED by default; requires KALSHI_USE_DEMO=true + KALSHI_API_KEY.
    Safety:       Will never route to production. Demo credentials are checked separately
                  from any production flow. Missing credentials → safe not_configured state.
    """

    platform_name = "kalshi_demo"
    mode = "DEMO"
    live_capable = False
    execution_enabled = False
    auth_required = True
    data_truth_label = "DEMO"
    base_url = "https://demo-api.kalshi.co/trade-api/v2"

    # ── Configuration ────────────────────────────────────────────────────────

    def _kalshi_enabled(self) -> bool:
        return self._bool_setting("ENABLE_KALSHI", False)

    def _demo_enabled(self) -> bool:
        return self._bool_setting("KALSHI_USE_DEMO", False)

    def _api_key(self) -> str:
        return self._setting("KALSHI_API_KEY", "").strip()

    def is_configured(self) -> bool:
        return self._kalshi_enabled() and self._demo_enabled() and bool(self._api_key())

    # ── Internal helpers ─────────────────────────────────────────────────────

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key()}"}

    def _config_check(self) -> dict[str, Any] | None:
        """Return an error payload if not configured, else None."""
        if not self._kalshi_enabled():
            return self._error(
                "disabled",
                "Kalshi adapter is disabled. Set ENABLE_KALSHI=true.",
                degraded_reason="Set ENABLE_KALSHI=true to enable Kalshi.",
                status="disabled",
                auth_truth="missing",
            )
        if not self._demo_enabled():
            return self._error(
                "demo_disabled",
                "Kalshi demo mode is off. Set KALSHI_USE_DEMO=true.",
                degraded_reason=(
                    "Set KALSHI_USE_DEMO=true to use the Kalshi demo environment. "
                    "Demo environment is always isolated from production."
                ),
                status="disabled",
                auth_truth="missing",
            )
        if not self._api_key():
            return self._error(
                "not_configured",
                "KALSHI_API_KEY is required for Kalshi demo.",
                degraded_reason="Provide KALSHI_API_KEY to access the Kalshi demo environment.",
                status="not_configured",
                auth_truth="missing",
            )
        return None

    # ── BaseAdapter interface ────────────────────────────────────────────────

    def healthcheck(self) -> dict[str, Any]:
        err = self._config_check()
        if err:
            return err
        try:
            resp = self._request("GET", "/markets", params={"limit": 1}, headers=self._auth_headers())
            payload = resp.json()
            markets = payload.get("markets", payload if isinstance(payload, list) else [])
            return self._ok(
                {
                    "sample_count": len(markets) if isinstance(markets, list) else 0,
                    "base_url": self.base_url,
                    "truth_note": "This is a DEMO environment. No real funds are at risk.",
                },
                status="ready",
                auth_truth="validated",
            )
        except Exception as exc:
            return self._error(
                "healthcheck_failed",
                f"Kalshi demo healthcheck failed: {exc}",
                degraded_reason="Kalshi demo API could not be reached. Check credentials and network.",
                status="degraded",
                auth_truth="failed",
            )

    def list_markets(self, **kwargs) -> dict[str, Any]:
        err = self._config_check()
        if err:
            return err
        params: dict[str, Any] = {"limit": kwargs.get("limit", 25)}
        for key in ("cursor", "series_ticker", "event_ticker", "status", "tickers"):
            if kwargs.get(key) is not None:
                params[key] = kwargs[key]
        try:
            resp = self._request("GET", "/markets", params=params, headers=self._auth_headers())
            payload = resp.json()
            markets = payload.get("markets", payload if isinstance(payload, list) else [])
            return self._ok(
                {
                    "markets": markets if isinstance(markets, list) else [],
                    "cursor": payload.get("cursor") if isinstance(payload, dict) else None,
                    "count": len(markets) if isinstance(markets, list) else 0,
                    "truth_note": "DEMO environment — not production markets.",
                },
                status="ready",
                auth_truth="validated",
            )
        except Exception as exc:
            return self._error(
                "list_markets_failed",
                f"Kalshi demo markets request failed: {exc}",
                degraded_reason="Kalshi demo markets could not be listed.",
                status="degraded",
                auth_truth="failed",
            )

    def get_market(self, market_id: str, **kwargs) -> dict[str, Any]:
        err = self._config_check()
        if err:
            return err
        try:
            resp = self._request("GET", f"/markets/{market_id}", headers=self._auth_headers())
            payload = resp.json()
            market = payload.get("market", payload)
            return self._ok({"market": market}, status="ready", auth_truth="validated")
        except Exception as exc:
            return self._error(
                "get_market_failed",
                f"Kalshi demo market fetch failed: {exc}",
                degraded_reason=f"Kalshi demo market {market_id} could not be fetched.",
                status="degraded",
                auth_truth="failed",
            )

    def get_orderbook(self, market_id: str, **kwargs) -> dict[str, Any]:
        err = self._config_check()
        if err:
            return err
        try:
            resp = self._request(
                "GET", f"/markets/{market_id}/orderbook", headers=self._auth_headers()
            )
            raw = resp.json()
            book = raw.get("orderbook", raw)
            normalized = self._normalize_orderbook(book)
            return self._ok(
                {"orderbook": normalized, "raw": book, "market_id": market_id},
                status="ready",
                auth_truth="validated",
            )
        except Exception as exc:
            return self._error(
                "get_orderbook_failed",
                f"Kalshi demo orderbook fetch failed: {exc}",
                degraded_reason=f"Kalshi demo orderbook for {market_id} could not be fetched.",
                status="degraded",
                auth_truth="failed",
            )

    @staticmethod
    def _normalize_orderbook(raw: dict) -> dict:
        """
        Convert Kalshi's fixed-point orderbook to generic asks/bids format.

        Kalshi returns:
          yes: [[price_cents, contracts], ...] or yes_dollars format
          no: [[price_cents, contracts], ...]

        We normalize to:
          asks: [{"price": float_0_to_1, "size": int}, ...]
          bids: [{"price": float_0_to_1, "size": int}, ...]
        """
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
        err = self._config_check()
        if err:
            return err
        try:
            resp = self._request("GET", "/portfolio/balance", headers=self._auth_headers())
            payload = resp.json()
            balance = payload.get("balance", payload)
            return self._ok(
                {
                    "balance": balance,
                    "truth_note": (
                        "DEMO BALANCE — these are simulated demo funds, "
                        "not real money. Do not treat as withdrawable."
                    ),
                },
                status="ready",
                auth_truth="validated",
            )
        except Exception as exc:
            return self._error(
                "get_balance_failed",
                f"Kalshi demo balance request failed: {exc}",
                degraded_reason="Demo balance could not be fetched.",
                status="degraded",
                auth_truth="failed",
            )

    def place_order(self, *, market_id: str = "", side: str = "BUY", size: float = 0.0, price_limit: float | None = None, **kwargs) -> dict[str, Any]:
        """
        Place an order on the Kalshi demo (sandbox) environment.
        Disabled by default; requires execution_enabled=True on the adapter instance.
        NEVER routes to production — demo credentials are isolated.
        """
        err = self._config_check()
        if err:
            return err

        if not self.execution_enabled:
            # Simulate a fill without touching the exchange when execution is off
            import uuid as _uuid
            return {
                "ok": True,
                "order_id": f"demo_shadow_{market_id}_{_uuid.uuid4().hex[:8]}",
                "fill_price": price_limit,
                "simulated": True,
                "truth_label": "DEMO — SIMULATED (execution_enabled=False)",
            }

        try:
            import uuid as _uuid
            payload: dict[str, Any] = {
                "ticker": market_id,
                "action": "buy" if "BUY" in side.upper() else "sell",
                "side": "yes" if "YES" in side.upper() else "no",
                "type": "limit" if price_limit is not None else "market",
                "count": max(1, int(size)),  # Kalshi uses contract count
            }
            if price_limit is not None:
                payload["yes_price"] = int(price_limit * 100)  # cents

            resp = self._request(
                "POST",
                "/portfolio/orders",
                json_body=payload,
                headers=self._auth_headers(),
                timeout=10,
            )
            data = resp.json()
            order_data = data.get("order", {})

            return {
                "ok": resp.status_code in (200, 201),
                "order_id": order_data.get("order_id", f"demo_{_uuid.uuid4().hex[:12]}"),
                "fill_price": (order_data.get("yes_price") or 0) / 100,
                "raw": data,
                "truth_label": "DEMO — EXCHANGE SANDBOX",
            }
        except Exception as exc:
            return {
                "ok": False,
                "error": str(exc),
                "truth_label": "DEMO — ERROR",
            }

    def get_positions(self, **kwargs) -> dict[str, Any]:
        err = self._config_check()
        if err:
            return err
        try:
            resp = self._request("GET", "/portfolio/positions", headers=self._auth_headers())
            payload = resp.json()
            positions = payload.get("market_positions", payload if isinstance(payload, list) else [])
            return self._ok(
                {
                    "positions": positions if isinstance(positions, list) else [],
                    "truth_note": "DEMO positions — no real funds.",
                },
                status="ready",
                auth_truth="validated",
            )
        except Exception as exc:
            return self._error(
                "get_positions_failed",
                f"Kalshi demo positions request failed: {exc}",
                degraded_reason="Demo positions could not be fetched.",
                status="degraded",
                auth_truth="failed",
            )
