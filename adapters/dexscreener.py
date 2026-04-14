from __future__ import annotations

from typing import Any

from adapters.base_adapter import BaseAdapter


class DexScreenerAdapter(BaseAdapter):
    platform_name = "dexscreener"
    mode = "WATCHLIST ONLY"
    live_capable = False
    execution_enabled = False
    auth_required = False
    data_truth_label = "WATCHLIST ONLY"
    base_url = "https://api.dexscreener.com"

    def is_configured(self) -> bool:
        return True

    def healthcheck(self) -> dict[str, Any]:
        try:
            response = self._request("GET", "/token-profiles/latest/v1")
            payload = response.json()
            sample_count = len(payload) if isinstance(payload, list) else 0
            return self._ok({"sample_count": sample_count}, status="ready", auth_truth="validated")
        except Exception as exc:
            return self._error(
                "healthcheck_failed",
                f"DexScreener healthcheck failed: {exc}",
                degraded_reason="DexScreener public API could not be reached.",
                status="degraded",
                auth_truth="failed",
            )

    def list_markets(self, **kwargs) -> dict[str, Any]:
        query = kwargs.get("query", "SOL")
        try:
            response = self._request("GET", "/latest/dex/search", params={"q": query})
            payload = response.json()
            return self._ok(
                {"pairs": payload.get("pairs", []) if isinstance(payload, dict) else []},
                status="ready",
                auth_truth="validated",
            )
        except Exception as exc:
            return self._error(
                "search_failed",
                f"DexScreener search failed: {exc}",
                degraded_reason="DexScreener search results are unavailable right now.",
                status="degraded",
                auth_truth="failed",
            )
