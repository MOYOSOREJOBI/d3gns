from __future__ import annotations

from typing import Any

from adapters.base_adapter import BaseAdapter


class CoinCapAdapter(BaseAdapter):
    """CoinCap public API — no auth, real-time crypto prices via REST + WebSocket."""

    platform_name = "coincap"
    mode = "PUBLIC DATA ONLY"
    live_capable = False
    execution_enabled = False
    auth_required = False
    data_truth_label = "PUBLIC DATA ONLY"
    base_url = "https://api.coincap.io/v2"

    _ASSET_IDS = {
        "BTC": "bitcoin",
        "ETH": "ethereum",
        "SOL": "solana",
        "BNB": "binance-coin",
        "MATIC": "polygon",
        "AVAX": "avalanche",
        "DOGE": "dogecoin",
        "ADA": "cardano",
        "DOT": "polkadot",
        "LINK": "chainlink",
        "XRP": "xrp",
        "LTC": "litecoin",
    }

    def is_configured(self) -> bool:
        return True

    def healthcheck(self) -> dict[str, Any]:
        try:
            r = self._request("GET", "/assets", params={"limit": 1})
            return self._ok(data={"status": "ok"}, auth_truth="no_auth_required")
        except Exception as exc:
            return self._error("health_failed", str(exc), auth_truth="no_auth_required")

    def get_asset(self, symbol: str = "BTC") -> dict[str, Any]:
        """Get current price and stats for a single asset."""
        asset_id = self._ASSET_IDS.get(symbol.upper(), symbol.lower())
        try:
            r = self._request("GET", f"/assets/{asset_id}")
            raw = r.json().get("data", {})
            return self._ok(
                data={
                    "symbol": raw.get("symbol"),
                    "name": raw.get("name"),
                    "rank": raw.get("rank"),
                    "price_usd": float(raw.get("priceUsd") or 0),
                    "market_cap_usd": float(raw.get("marketCapUsd") or 0),
                    "volume_24h_usd": float(raw.get("volumeUsd24Hr") or 0),
                    "change_24h_pct": float(raw.get("changePercent24Hr") or 0),
                    "vwap_24h": float(raw.get("vwap24Hr") or 0),
                    "supply": float(raw.get("supply") or 0),
                    "max_supply": raw.get("maxSupply"),
                },
                status="ok",
                auth_truth="no_auth_required",
            )
        except Exception as exc:
            return self._error("asset_failed", str(exc), auth_truth="no_auth_required")

    def get_top_assets(self, limit: int = 20) -> dict[str, Any]:
        """Get top N assets by market cap with price data."""
        try:
            r = self._request("GET", "/assets", params={"limit": limit})
            raw = r.json().get("data", [])
            assets = [
                {
                    "rank": int(a.get("rank") or 0),
                    "symbol": a.get("symbol"),
                    "name": a.get("name"),
                    "price_usd": float(a.get("priceUsd") or 0),
                    "change_24h_pct": float(a.get("changePercent24Hr") or 0),
                    "market_cap_usd": float(a.get("marketCapUsd") or 0),
                    "volume_24h_usd": float(a.get("volumeUsd24Hr") or 0),
                }
                for a in raw
            ]
            return self._ok(data={"assets": assets, "count": len(assets)}, status="ok", auth_truth="no_auth_required")
        except Exception as exc:
            return self._error("top_assets_failed", str(exc), auth_truth="no_auth_required")

    def get_history(self, symbol: str = "BTC", interval: str = "h1", limit: int = 24) -> dict[str, Any]:
        """Fetch historical price data. interval: m1, m5, m15, m30, h1, h2, h6, h12, d1."""
        asset_id = self._ASSET_IDS.get(symbol.upper(), symbol.lower())
        try:
            r = self._request(
                "GET", f"/assets/{asset_id}/history",
                params={"interval": interval},
            )
            raw = r.json().get("data", [])
            candles = [
                {
                    "time": c.get("time"),
                    "price_usd": float(c.get("priceUsd") or 0),
                }
                for c in raw[-limit:]
            ]
            return self._ok(
                data={"symbol": symbol.upper(), "interval": interval, "candles": candles},
                status="ok",
                auth_truth="no_auth_required",
            )
        except Exception as exc:
            return self._error("history_failed", str(exc), auth_truth="no_auth_required")

    def get_exchange_rates(self) -> dict[str, Any]:
        """Fiat and crypto exchange rates relative to USD."""
        try:
            r = self._request("GET", "/rates")
            raw = r.json().get("data", [])
            rates = {item["symbol"]: float(item.get("rateUsd") or 0) for item in raw if item.get("symbol")}
            return self._ok(data={"rates": rates}, status="ok", auth_truth="no_auth_required")
        except Exception as exc:
            return self._error("rates_failed", str(exc), auth_truth="no_auth_required")
