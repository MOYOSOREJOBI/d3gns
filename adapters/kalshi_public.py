from __future__ import annotations

from typing import Any

from adapters.base_adapter import BaseAdapter


class KalshiPublicAdapter(BaseAdapter):
    platform_name = "kalshi_public"
    mode = "PUBLIC DATA ONLY"
    live_capable = False
    execution_enabled = False
    auth_required = False
    data_truth_label = "PUBLIC DATA ONLY"
    base_url = "https://api.elections.kalshi.com/trade-api/v2"

    def is_configured(self) -> bool:
        return self._bool_setting("ENABLE_KALSHI", False)

    def healthcheck(self) -> dict[str, Any]:
        if not self.is_configured():
            return self._error(
                "disabled",
                "Kalshi public adapter is disabled by feature flag.",
                degraded_reason="Set ENABLE_KALSHI=true to enable Kalshi public market data.",
                status="disabled",
                auth_truth="missing",
            )
        try:
            response = self._request("GET", "/markets", params={"limit": 1})
            payload = response.json()
            markets = payload.get("markets", payload if isinstance(payload, list) else [])
            return self._ok(
                {
                    "sample_count": len(markets) if isinstance(markets, list) else 0,
                    "base_url": self.base_url,
                },
                status="ready",
                auth_truth="validated",
            )
        except Exception as exc:
            return self._error(
                "healthcheck_failed",
                f"Kalshi public healthcheck failed: {exc}",
                degraded_reason="Kalshi public market data could not be fetched.",
                status="degraded",
                auth_truth="failed",
            )

    def list_markets(self, **kwargs) -> dict[str, Any]:
        if not self.is_configured():
            return self.healthcheck()
        params = {
            "limit": kwargs.get("limit", 25),
        }
        for key in ("cursor", "series_ticker", "event_ticker", "status", "tickers"):
            if kwargs.get(key) is not None:
                params[key] = kwargs[key]
        try:
            response = self._request("GET", "/markets", params=params)
            payload = response.json()
            markets = payload.get("markets", payload if isinstance(payload, list) else [])
            return self._ok(
                {
                    "markets": markets if isinstance(markets, list) else [],
                    "cursor": payload.get("cursor") if isinstance(payload, dict) else None,
                    "count": len(markets) if isinstance(markets, list) else 0,
                },
                status="ready",
                auth_truth="validated",
            )
        except Exception as exc:
            return self._error(
                "list_markets_failed",
                f"Kalshi markets request failed: {exc}",
                degraded_reason="Kalshi public markets could not be listed.",
                status="degraded",
                auth_truth="failed",
            )

    def get_market(self, market_id: str, **kwargs) -> dict[str, Any]:
        if not self.is_configured():
            return self.healthcheck()
        try:
            response = self._request("GET", f"/markets/{market_id}")
            payload = response.json()
            market = payload.get("market", payload)
            return self._ok({"market": market}, status="ready", auth_truth="validated")
        except Exception as exc:
            return self._error(
                "get_market_failed",
                f"Kalshi market fetch failed: {exc}",
                degraded_reason=f"Kalshi market {market_id} could not be fetched.",
                status="degraded",
                auth_truth="failed",
            )

    def get_orderbook(self, market_id: str, **kwargs) -> dict[str, Any]:
        if not self.is_configured():
            return self.healthcheck()
        try:
            response = self._request("GET", f"/markets/{market_id}/orderbook")
            raw = response.json()
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
                f"Kalshi orderbook fetch failed: {exc}",
                degraded_reason=f"Kalshi orderbook for {market_id} could not be fetched.",
                status="degraded",
                auth_truth="failed",
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

    def get_recent_trades(self, market_id: str, **kwargs) -> dict[str, Any]:
        if not self.is_configured():
            return self.healthcheck()
        params = {"ticker": market_id, "limit": kwargs.get("limit", 25)}
        try:
            response = self._request("GET", "/markets/trades", params=params)
            payload = response.json()
            trades = payload.get("trades", payload if isinstance(payload, list) else [])
            return self._ok(
                {"trades": trades if isinstance(trades, list) else [], "count": len(trades) if isinstance(trades, list) else 0},
                status="ready",
                auth_truth="validated",
            )
        except Exception as exc:
            return self._error(
                "get_recent_trades_failed",
                f"Kalshi recent trades fetch failed: {exc}",
                degraded_reason=f"Kalshi recent trades for {market_id} could not be fetched.",
                status="degraded",
                auth_truth="failed",
            )

