from __future__ import annotations

from typing import Any

from adapters.base_adapter import BaseAdapter


class CoinpaprikaAdapter(BaseAdapter):
    """Coinpaprika API — no auth, comprehensive crypto market data and analytics."""

    platform_name = "coinpaprika"
    mode = "PUBLIC DATA ONLY"
    live_capable = False
    execution_enabled = False
    auth_required = False
    data_truth_label = "PUBLIC DATA ONLY"
    base_url = "https://api.coinpaprika.com/v1"

    _COIN_IDS = {
        "BTC": "btc-bitcoin",
        "ETH": "eth-ethereum",
        "SOL": "sol-solana",
        "BNB": "bnb-binance-coin",
        "MATIC": "matic-polygon",
        "AVAX": "avax-avalanche",
        "DOGE": "doge-dogecoin",
        "ADA": "ada-cardano",
        "DOT": "dot-polkadot",
        "LINK": "link-chainlink",
        "XRP": "xrp-xrp",
        "LTC": "ltc-litecoin",
    }

    def is_configured(self) -> bool:
        return True

    def healthcheck(self) -> dict[str, Any]:
        try:
            r = self._request("GET", "/global")
            data = r.json()
            return self._ok(data={"status": "ok", "market_cap_usd": data.get("market_cap_usd")}, auth_truth="no_auth_required")
        except Exception as exc:
            return self._error("health_failed", str(exc), auth_truth="no_auth_required")

    def get_global(self) -> dict[str, Any]:
        """Global crypto market overview."""
        try:
            r = self._request("GET", "/global")
            raw = r.json()
            return self._ok(
                data={
                    "market_cap_usd": raw.get("market_cap_usd"),
                    "volume_24h_usd": raw.get("volume_24h_usd"),
                    "btc_dominance_pct": raw.get("bitcoin_dominance_percentage"),
                    "market_cap_ath_value": raw.get("market_cap_ath_value"),
                    "market_cap_ath_date": raw.get("market_cap_ath_date"),
                    "market_cap_change_24h_pct": raw.get("market_cap_change_24h"),
                    "volume_change_24h_pct": raw.get("volume_change_24h"),
                    "active_cryptocurrencies": raw.get("cryptocurrencies_number"),
                    "icos_total": raw.get("icos_total"),
                },
                status="ok",
                auth_truth="no_auth_required",
            )
        except Exception as exc:
            return self._error("global_failed", str(exc), auth_truth="no_auth_required")

    def get_ticker(self, symbol: str = "BTC") -> dict[str, Any]:
        """Get price and market data for a single coin."""
        coin_id = self._COIN_IDS.get(symbol.upper(), f"{symbol.lower()}-{symbol.lower()}")
        try:
            r = self._request("GET", f"/tickers/{coin_id}", params={"quotes": "USD"})
            raw = r.json()
            usd = raw.get("quotes", {}).get("USD", {})
            return self._ok(
                data={
                    "symbol": raw.get("symbol"),
                    "name": raw.get("name"),
                    "rank": raw.get("rank"),
                    "price_usd": usd.get("price"),
                    "market_cap_usd": usd.get("market_cap"),
                    "volume_24h_usd": usd.get("volume_24h"),
                    "change_1h_pct": usd.get("percent_change_1h"),
                    "change_24h_pct": usd.get("percent_change_24h"),
                    "change_7d_pct": usd.get("percent_change_7d"),
                    "change_30d_pct": usd.get("percent_change_30d"),
                    "ath_price": usd.get("ath_price"),
                    "ath_date": usd.get("ath_date"),
                    "percent_from_ath_pct": usd.get("percent_from_price_ath"),
                },
                status="ok",
                auth_truth="no_auth_required",
            )
        except Exception as exc:
            return self._error("ticker_failed", str(exc), auth_truth="no_auth_required")

    def get_top_coins(self, limit: int = 20) -> dict[str, Any]:
        """Fetch top N coins by market cap."""
        try:
            r = self._request("GET", "/tickers", params={"quotes": "USD", "limit": limit})
            raw = r.json()
            coins = [
                {
                    "rank": c.get("rank"),
                    "symbol": c.get("symbol"),
                    "name": c.get("name"),
                    "price_usd": c.get("quotes", {}).get("USD", {}).get("price"),
                    "change_24h_pct": c.get("quotes", {}).get("USD", {}).get("percent_change_24h"),
                    "market_cap_usd": c.get("quotes", {}).get("USD", {}).get("market_cap"),
                }
                for c in (raw if isinstance(raw, list) else [])[:limit]
            ]
            return self._ok(data={"coins": coins, "count": len(coins)}, status="ok", auth_truth="no_auth_required")
        except Exception as exc:
            return self._error("top_coins_failed", str(exc), auth_truth="no_auth_required")

    def get_events(self, coin: str = "BTC") -> dict[str, Any]:
        """Fetch upcoming events for a coin (forks, listings, partnerships)."""
        coin_id = self._COIN_IDS.get(coin.upper(), f"{coin.lower()}-{coin.lower()}")
        try:
            r = self._request("GET", f"/coins/{coin_id}/events")
            raw = r.json()
            events = [
                {
                    "date": e.get("date"),
                    "name": e.get("name"),
                    "description": (e.get("description") or "")[:200],
                    "is_conference": e.get("is_conference"),
                    "link": e.get("link"),
                    "proof_image_link": e.get("proof_image_link"),
                }
                for e in (raw if isinstance(raw, list) else [])[:10]
            ]
            return self._ok(data={"coin": coin.upper(), "events": events, "count": len(events)}, status="ok", auth_truth="no_auth_required")
        except Exception as exc:
            return self._error("events_failed", str(exc), auth_truth="no_auth_required")

    def get_exchanges(self, limit: int = 20) -> dict[str, Any]:
        """Fetch top crypto exchanges by volume."""
        try:
            r = self._request("GET", "/exchanges", params={"quotes": "USD", "limit": limit})
            raw = r.json()
            exchanges = [
                {
                    "rank": e.get("rank"),
                    "name": e.get("name"),
                    "id": e.get("id"),
                    "volume_24h_usd": e.get("quotes", {}).get("USD", {}).get("reported_volume_24h"),
                    "adjusted_volume_24h_usd": e.get("quotes", {}).get("USD", {}).get("adjusted_volume_24h"),
                    "confidence_score": e.get("confidence_score"),
                }
                for e in (raw if isinstance(raw, list) else [])[:limit]
            ]
            return self._ok(data={"exchanges": exchanges, "count": len(exchanges)}, status="ok", auth_truth="no_auth_required")
        except Exception as exc:
            return self._error("exchanges_failed", str(exc), auth_truth="no_auth_required")
