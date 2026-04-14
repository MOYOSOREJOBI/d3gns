from __future__ import annotations

from typing import Any

from adapters.base_adapter import BaseAdapter


class PredictItAdapter(BaseAdapter):
    platform_name = "predictit"
    mode = "PUBLIC DATA ONLY"
    live_capable = False
    execution_enabled = False
    auth_required = False
    data_truth_label = "PUBLIC DATA ONLY"
    base_url = "https://www.predictit.org/api/marketdata"

    def is_configured(self) -> bool:
        return True

    def healthcheck(self) -> dict[str, Any]:
        try:
            response = self._request("GET", "/all/")
            payload = response.json()
            markets = payload.get("markets", payload if isinstance(payload, list) else [])
            return self._ok({"sample_count": len(markets) if isinstance(markets, list) else 0}, status="ready", auth_truth="validated")
        except Exception as exc:
            return self._error(
                "healthcheck_failed",
                f"PredictIt healthcheck failed: {exc}",
                degraded_reason="PredictIt public market data is unavailable right now.",
                status="degraded",
                auth_truth="failed",
            )

    def list_markets(self, **kwargs) -> dict[str, Any]:
        try:
            response = self._request("GET", "/all/")
            payload = response.json()
            markets = payload.get("markets", payload if isinstance(payload, list) else [])
            return self._ok({"markets": markets if isinstance(markets, list) else []}, status="ready", auth_truth="validated")
        except Exception as exc:
            return self._error(
                "list_markets_failed",
                f"PredictIt markets fetch failed: {exc}",
                degraded_reason="PredictIt market data is unavailable right now.",
                status="degraded",
                auth_truth="failed",
            )
