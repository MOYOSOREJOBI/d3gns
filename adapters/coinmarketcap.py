from __future__ import annotations

from typing import Any

from adapters.base_adapter import BaseAdapter


class CoinMarketCapAdapter(BaseAdapter):
    """
    CoinMarketCap API — free basic tier (10k credits/month).
    Industry-standard crypto prices, market cap, and sentiment data.
    Register free at coinmarketcap.com/api
    """

    platform_name = "coinmarketcap"
    mode = "PUBLIC DATA ONLY"
    live_capable = False
    execution_enabled = False
    auth_required = False
    data_truth_label = "PUBLIC DATA ONLY"
    base_url = "https://pro-api.coinmarketcap.com/v1"

    def is_configured(self) -> bool:
        return bool(self._setting("CMC_API_KEY", "").strip())

    def _headers(self) -> dict[str, str]:
        return {
            "X-CMC_PRO_API_KEY": self._setting("CMC_API_KEY", ""),
            "Accept": "application/json",
        }

    def healthcheck(self) -> dict[str, Any]:
        if not self.is_configured():
            return self._ok(
                data={"status": "degraded", "note": "Set CMC_API_KEY. Free 10k credits/month at coinmarketcap.com/api"},
                status="degraded", auth_truth="missing",
                degraded_reason="CMC_API_KEY not set. Register free at coinmarketcap.com/api.",
            )
        res = self.get_latest_listings(limit=1)
        if res.get("ok"):
            return self._ok(data={"status": "ok"}, auth_truth="validated")
        return self._error("health_failed", res.get("error", "unknown"), auth_truth="invalid")

    def get_latest_listings(self, limit: int = 20, sort: str = "market_cap") -> dict[str, Any]:
        """Top cryptocurrencies by market cap with price data."""
        if not self.is_configured():
            return self._error("no_key", "CMC_API_KEY not set. Register free at coinmarketcap.com/api", auth_truth="missing")
        try:
            r = self._request("GET", "/cryptocurrency/listings/latest",
                params={"limit": limit, "sort": sort, "convert": "USD"},
                headers=self._headers())
            raw = r.json()
            if raw.get("status", {}).get("error_code", 0) != 0:
                return self._error("api_error", raw["status"].get("error_message", "CMC error"), auth_truth="invalid")
            coins = [
                {
                    "rank": c.get("cmc_rank"),
                    "name": c.get("name"),
                    "symbol": c.get("symbol"),
                    "price_usd": c.get("quote", {}).get("USD", {}).get("price"),
                    "market_cap_usd": c.get("quote", {}).get("USD", {}).get("market_cap"),
                    "volume_24h_usd": c.get("quote", {}).get("USD", {}).get("volume_24h"),
                    "change_1h_pct": c.get("quote", {}).get("USD", {}).get("percent_change_1h"),
                    "change_24h_pct": c.get("quote", {}).get("USD", {}).get("percent_change_24h"),
                    "change_7d_pct": c.get("quote", {}).get("USD", {}).get("percent_change_7d"),
                    "circulating_supply": c.get("circulating_supply"),
                    "max_supply": c.get("max_supply"),
                    "dominance_pct": c.get("quote", {}).get("USD", {}).get("market_cap_dominance"),
                }
                for c in raw.get("data", [])
            ]
            return self._ok(
                data={"coins": coins, "count": len(coins)},
                status="ok", auth_truth="validated",
            )
        except Exception as exc:
            return self._error("listings_failed", str(exc), auth_truth="validated")

    def get_quotes(self, symbols: list[str] | None = None) -> dict[str, Any]:
        """Get latest quotes for specific coins by symbol."""
        if not self.is_configured():
            return self._error("no_key", "CMC_API_KEY not set.", auth_truth="missing")
        syms = ",".join(symbols or ["BTC", "ETH", "SOL", "BNB"])
        try:
            r = self._request("GET", "/cryptocurrency/quotes/latest",
                params={"symbol": syms, "convert": "USD"},
                headers=self._headers())
            raw = r.json()
            if raw.get("status", {}).get("error_code", 0) != 0:
                return self._error("api_error", raw["status"].get("error_message", "CMC error"), auth_truth="invalid")
            quotes = {}
            for sym, data in raw.get("data", {}).items():
                if isinstance(data, list):
                    data = data[0]
                q = data.get("quote", {}).get("USD", {})
                quotes[sym] = {
                    "price_usd": q.get("price"),
                    "change_1h_pct": q.get("percent_change_1h"),
                    "change_24h_pct": q.get("percent_change_24h"),
                    "change_7d_pct": q.get("percent_change_7d"),
                    "market_cap_usd": q.get("market_cap"),
                    "volume_24h_usd": q.get("volume_24h"),
                    "last_updated": q.get("last_updated"),
                }
            return self._ok(data={"quotes": quotes}, status="ok", auth_truth="validated")
        except Exception as exc:
            return self._error("quotes_failed", str(exc), auth_truth="validated")

    def get_global_metrics(self) -> dict[str, Any]:
        """Global crypto market metrics: total market cap, dominance, active currencies."""
        if not self.is_configured():
            return self._error("no_key", "CMC_API_KEY not set.", auth_truth="missing")
        try:
            r = self._request("GET", "/global-metrics/quotes/latest",
                params={"convert": "USD"}, headers=self._headers())
            raw = r.json().get("data", {})
            q = raw.get("quote", {}).get("USD", {})
            return self._ok(
                data={
                    "total_market_cap_usd": q.get("total_market_cap"),
                    "total_volume_24h_usd": q.get("total_volume_24h"),
                    "btc_dominance_pct": raw.get("btc_dominance"),
                    "eth_dominance_pct": raw.get("eth_dominance"),
                    "active_cryptocurrencies": raw.get("active_cryptocurrencies"),
                    "active_exchanges": raw.get("active_exchanges"),
                    "defi_market_cap_usd": q.get("defi_market_cap"),
                    "defi_volume_24h_usd": q.get("defi_volume_24h"),
                    "stablecoin_market_cap_usd": q.get("stablecoin_market_cap"),
                    "market_cap_change_24h_pct": q.get("total_market_cap_yesterday_percentage_change"),
                },
                status="ok", auth_truth="validated",
            )
        except Exception as exc:
            return self._error("global_failed", str(exc), auth_truth="validated")

    def get_fear_greed_index(self) -> dict[str, Any]:
        """CMC Fear & Greed Index (separate from Alternative.me)."""
        if not self.is_configured():
            return self._error("no_key", "CMC_API_KEY not set.", auth_truth="missing")
        try:
            r = self._request("GET", "/fear-and-greed/latest", headers=self._headers())
            raw = r.json().get("data", {})
            return self._ok(
                data={
                    "value": raw.get("value"),
                    "value_classification": raw.get("value_classification"),
                    "update_time": raw.get("update_time"),
                },
                status="ok", auth_truth="validated",
            )
        except Exception as exc:
            return self._error("fng_failed", str(exc), auth_truth="validated")

    def get_trending(self) -> dict[str, Any]:
        """Latest trending cryptocurrencies on CMC."""
        if not self.is_configured():
            return self._error("no_key", "CMC_API_KEY not set.", auth_truth="missing")
        try:
            r = self._request("GET", "/cryptocurrency/trending/latest",
                params={"limit": 10, "convert": "USD"}, headers=self._headers())
            raw = r.json()
            trending = [
                {
                    "rank": i + 1,
                    "name": c.get("name"),
                    "symbol": c.get("symbol"),
                    "price_usd": c.get("quote", {}).get("USD", {}).get("price"),
                    "change_24h_pct": c.get("quote", {}).get("USD", {}).get("percent_change_24h"),
                }
                for i, c in enumerate(raw.get("data", []))
            ]
            return self._ok(data={"trending": trending}, status="ok", auth_truth="validated")
        except Exception as exc:
            return self._error("trending_failed", str(exc), auth_truth="validated")
