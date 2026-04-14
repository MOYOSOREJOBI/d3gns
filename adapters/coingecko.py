from __future__ import annotations

from typing import Any

from adapters.base_adapter import BaseAdapter


class CoinGeckoAdapter(BaseAdapter):
    """CoinGecko public API — no auth required, free tier, global crypto data."""

    platform_name = "coingecko"
    mode = "PUBLIC DATA ONLY"
    live_capable = False
    execution_enabled = False
    auth_required = False
    data_truth_label = "PUBLIC DATA ONLY"
    base_url = "https://api.coingecko.com/api/v3"

    _COIN_IDS = {
        "BTC": "bitcoin",
        "ETH": "ethereum",
        "SOL": "solana",
        "MATIC": "matic-network",
        "BNB": "binancecoin",
        "AVAX": "avalanche-2",
        "DOGE": "dogecoin",
        "ADA": "cardano",
        "DOT": "polkadot",
        "LINK": "chainlink",
    }

    def is_configured(self) -> bool:
        return True

    def healthcheck(self) -> dict[str, Any]:
        try:
            r = self._request("GET", "/ping")
            data = r.json()
            return self._ok(data=data, status="ok", auth_truth="no_auth_required")
        except Exception as exc:
            return self._error("ping_failed", str(exc), auth_truth="no_auth_required")

    def get_price(self, coins: list[str] | None = None, vs_currency: str = "usd") -> dict[str, Any]:
        """Fetch current prices for a list of coin symbols."""
        symbols = coins or ["BTC", "ETH", "SOL"]
        ids = ",".join(self._COIN_IDS.get(s.upper(), s.lower()) for s in symbols)
        try:
            r = self._request(
                "GET", "/simple/price",
                params={
                    "ids": ids,
                    "vs_currencies": vs_currency,
                    "include_24hr_change": "true",
                    "include_24hr_vol": "true",
                    "include_market_cap": "true",
                },
            )
            return self._ok(data=r.json(), status="ok", auth_truth="no_auth_required")
        except Exception as exc:
            return self._error("price_fetch_failed", str(exc), auth_truth="no_auth_required")

    def get_market_data(self, coin: str = "BTC", vs_currency: str = "usd") -> dict[str, Any]:
        """Fetch detailed market data for a single coin."""
        coin_id = self._COIN_IDS.get(coin.upper(), coin.lower())
        try:
            r = self._request(
                "GET", f"/coins/{coin_id}",
                params={
                    "localization": "false",
                    "tickers": "false",
                    "community_data": "true",
                    "developer_data": "false",
                },
            )
            raw = r.json()
            mkt = raw.get("market_data", {})
            return self._ok(
                data={
                    "coin": coin.upper(),
                    "coin_id": coin_id,
                    "price_usd": mkt.get("current_price", {}).get("usd"),
                    "market_cap_usd": mkt.get("market_cap", {}).get("usd"),
                    "volume_24h_usd": mkt.get("total_volume", {}).get("usd"),
                    "price_change_24h_pct": mkt.get("price_change_percentage_24h"),
                    "price_change_7d_pct": mkt.get("price_change_percentage_7d"),
                    "ath_usd": mkt.get("ath", {}).get("usd"),
                    "ath_change_pct": mkt.get("ath_change_percentage", {}).get("usd"),
                    "sentiment_votes_up_pct": raw.get("sentiment_votes_up_percentage"),
                    "community_score": raw.get("community_score"),
                    "developer_score": raw.get("developer_score"),
                },
                status="ok",
                auth_truth="no_auth_required",
            )
        except Exception as exc:
            return self._error("market_data_failed", str(exc), auth_truth="no_auth_required")

    def get_ohlc(self, coin: str = "BTC", vs_currency: str = "usd", days: int = 1) -> dict[str, Any]:
        """Fetch OHLC candles. days: 1=hourly, 7/14/30/90/180/365=daily."""
        coin_id = self._COIN_IDS.get(coin.upper(), coin.lower())
        try:
            r = self._request(
                "GET", f"/coins/{coin_id}/ohlc",
                params={"vs_currency": vs_currency, "days": days},
            )
            raw = r.json()
            return self._ok(
                data={"coin": coin.upper(), "days": days, "candles": raw},
                status="ok",
                auth_truth="no_auth_required",
            )
        except Exception as exc:
            return self._error("ohlc_failed", str(exc), auth_truth="no_auth_required")

    def get_trending(self) -> dict[str, Any]:
        """Fetch trending coins (past 24h on CoinGecko)."""
        try:
            r = self._request("GET", "/search/trending")
            raw = r.json()
            coins = [
                {
                    "rank": item["item"].get("market_cap_rank"),
                    "symbol": item["item"].get("symbol"),
                    "name": item["item"].get("name"),
                    "score": item["item"].get("score"),
                }
                for item in raw.get("coins", [])
            ]
            return self._ok(data={"trending": coins}, status="ok", auth_truth="no_auth_required")
        except Exception as exc:
            return self._error("trending_failed", str(exc), auth_truth="no_auth_required")

    def get_global(self) -> dict[str, Any]:
        """Global crypto market stats: total market cap, dominance, etc."""
        try:
            r = self._request("GET", "/global")
            raw = r.json().get("data", {})
            return self._ok(
                data={
                    "total_market_cap_usd": raw.get("total_market_cap", {}).get("usd"),
                    "total_volume_usd": raw.get("total_volume", {}).get("usd"),
                    "btc_dominance_pct": raw.get("market_cap_percentage", {}).get("btc"),
                    "eth_dominance_pct": raw.get("market_cap_percentage", {}).get("eth"),
                    "active_cryptocurrencies": raw.get("active_cryptocurrencies"),
                    "market_cap_change_24h_pct": raw.get("market_cap_change_percentage_24h_usd"),
                    "defi_market_cap": raw.get("defi_market_cap"),
                    "defi_to_eth_ratio": raw.get("defi_to_eth_ratio"),
                },
                status="ok",
                auth_truth="no_auth_required",
            )
        except Exception as exc:
            return self._error("global_failed", str(exc), auth_truth="no_auth_required")
