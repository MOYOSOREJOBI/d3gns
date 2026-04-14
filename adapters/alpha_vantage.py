from __future__ import annotations

from typing import Any

from adapters.base_adapter import BaseAdapter


class AlphaVantageAdapter(BaseAdapter):
    """
    Alpha Vantage — free tier 25 req/day (500 with free key registration).
    Stocks, forex, crypto OHLCV + 50+ technical indicators.
    Register free at alphavantage.co.
    """

    platform_name = "alpha_vantage"
    mode = "PUBLIC DATA ONLY"
    live_capable = False
    execution_enabled = False
    auth_required = False
    data_truth_label = "PUBLIC DATA ONLY"
    base_url = "https://www.alphavantage.co/query"

    def is_configured(self) -> bool:
        return bool(self._setting("ALPHA_VANTAGE_API_KEY", "").strip())

    def _key(self) -> str:
        return self._setting("ALPHA_VANTAGE_API_KEY", "demo")

    def healthcheck(self) -> dict[str, Any]:
        if not self.is_configured():
            return self._ok(
                data={"status": "degraded", "note": "Set ALPHA_VANTAGE_API_KEY. Free 25 req/day at alphavantage.co"},
                status="degraded", auth_truth="missing",
                degraded_reason="ALPHA_VANTAGE_API_KEY not set. Register free at alphavantage.co.",
            )
        res = self.get_quote("AAPL")
        if res.get("ok"):
            return self._ok(data={"status": "ok"}, auth_truth="validated")
        return self._error("health_failed", res.get("error", "unknown"), auth_truth="invalid")

    def get_quote(self, symbol: str = "AAPL") -> dict[str, Any]:
        """Real-time stock quote."""
        try:
            r = self._request("GET", "", base_url=self.base_url, params={
                "function": "GLOBAL_QUOTE", "symbol": symbol, "apikey": self._key(),
            })
            raw = r.json().get("Global Quote", {})
            if not raw:
                note = r.json().get("Note") or r.json().get("Information") or "empty"
                return self._error("rate_limit_or_empty", str(note)[:200], auth_truth="validated")
            return self._ok(
                data={
                    "symbol": raw.get("01. symbol"),
                    "price": float(raw.get("05. price") or 0),
                    "open": float(raw.get("02. open") or 0),
                    "high": float(raw.get("03. high") or 0),
                    "low": float(raw.get("04. low") or 0),
                    "volume": int(raw.get("06. volume") or 0),
                    "prev_close": float(raw.get("08. previous close") or 0),
                    "change": float(raw.get("09. change") or 0),
                    "change_pct": raw.get("10. change percent"),
                    "latest_trading_day": raw.get("07. latest trading day"),
                },
                status="ok", auth_truth="validated",
            )
        except Exception as exc:
            return self._error("quote_failed", str(exc), auth_truth="validated")

    def get_daily_ohlcv(self, symbol: str = "AAPL", outputsize: str = "compact") -> dict[str, Any]:
        """Daily OHLCV for a stock. outputsize: 'compact' (100 days) or 'full' (20+ years)."""
        try:
            r = self._request("GET", "", base_url=self.base_url, params={
                "function": "TIME_SERIES_DAILY", "symbol": symbol,
                "outputsize": outputsize, "apikey": self._key(),
            })
            raw = r.json()
            if "Note" in raw or "Information" in raw:
                return self._error("rate_limit", str(raw.get("Note") or raw.get("Information"))[:200], auth_truth="validated")
            ts = raw.get("Time Series (Daily)", {})
            candles = [
                {
                    "date": date,
                    "open": float(vals.get("1. open", 0)),
                    "high": float(vals.get("2. high", 0)),
                    "low": float(vals.get("3. low", 0)),
                    "close": float(vals.get("4. close", 0)),
                    "volume": int(vals.get("5. volume", 0)),
                }
                for date, vals in list(ts.items())[:50]
            ]
            return self._ok(
                data={"symbol": symbol, "candles": candles, "count": len(candles)},
                status="ok", auth_truth="validated",
            )
        except Exception as exc:
            return self._error("daily_failed", str(exc), auth_truth="validated")

    def get_rsi(self, symbol: str = "AAPL", interval: str = "daily", time_period: int = 14) -> dict[str, Any]:
        """RSI (Relative Strength Index) — momentum oscillator."""
        try:
            r = self._request("GET", "", base_url=self.base_url, params={
                "function": "RSI", "symbol": symbol, "interval": interval,
                "time_period": time_period, "series_type": "close", "apikey": self._key(),
            })
            raw = r.json()
            if "Note" in raw or "Information" in raw:
                return self._error("rate_limit", str(raw.get("Note") or raw.get("Information"))[:200], auth_truth="validated")
            ts = raw.get("Technical Analysis: RSI", {})
            values = [{"date": d, "rsi": float(v.get("RSI", 0))} for d, v in list(ts.items())[:10]]
            current_rsi = values[0]["rsi"] if values else None
            signal = "overbought" if current_rsi and current_rsi > 70 else "oversold" if current_rsi and current_rsi < 30 else "neutral"
            return self._ok(
                data={"symbol": symbol, "current_rsi": current_rsi, "signal": signal, "history": values},
                status="ok", auth_truth="validated",
            )
        except Exception as exc:
            return self._error("rsi_failed", str(exc), auth_truth="validated")

    def get_macd(self, symbol: str = "AAPL", interval: str = "daily") -> dict[str, Any]:
        """MACD — trend-following momentum indicator."""
        try:
            r = self._request("GET", "", base_url=self.base_url, params={
                "function": "MACD", "symbol": symbol, "interval": interval,
                "series_type": "close", "apikey": self._key(),
            })
            raw = r.json()
            if "Note" in raw or "Information" in raw:
                return self._error("rate_limit", str(raw.get("Note") or raw.get("Information"))[:200], auth_truth="validated")
            ts = raw.get("Technical Analysis: MACD", {})
            values = [
                {
                    "date": d,
                    "macd": float(v.get("MACD", 0)),
                    "signal": float(v.get("MACD_Signal", 0)),
                    "hist": float(v.get("MACD_Hist", 0)),
                }
                for d, v in list(ts.items())[:10]
            ]
            current = values[0] if values else {}
            crossover = "bullish" if current.get("macd", 0) > current.get("signal", 0) else "bearish"
            return self._ok(
                data={"symbol": symbol, "current": current, "crossover": crossover, "history": values},
                status="ok", auth_truth="validated",
            )
        except Exception as exc:
            return self._error("macd_failed", str(exc), auth_truth="validated")

    def get_crypto_daily(self, symbol: str = "BTC", market: str = "USD") -> dict[str, Any]:
        """Daily OHLCV for a cryptocurrency pair."""
        try:
            r = self._request("GET", "", base_url=self.base_url, params={
                "function": "DIGITAL_CURRENCY_DAILY", "symbol": symbol,
                "market": market, "apikey": self._key(),
            })
            raw = r.json()
            if "Note" in raw or "Information" in raw:
                return self._error("rate_limit", str(raw.get("Note") or raw.get("Information"))[:200], auth_truth="validated")
            ts = raw.get(f"Time Series (Digital Currency Daily)", {})
            candles = [
                {
                    "date": date,
                    "open": float(vals.get(f"1a. open ({market})", vals.get("1. open", 0))),
                    "high": float(vals.get(f"2a. high ({market})", vals.get("2. high", 0))),
                    "low": float(vals.get(f"3a. low ({market})", vals.get("3. low", 0))),
                    "close": float(vals.get(f"4a. close ({market})", vals.get("4. close", 0))),
                    "volume": float(vals.get("5. volume", 0)),
                    "market_cap": float(vals.get("6. market cap (USD)", 0)),
                }
                for date, vals in list(ts.items())[:30]
            ]
            return self._ok(
                data={"symbol": symbol, "market": market, "candles": candles, "count": len(candles)},
                status="ok", auth_truth="validated",
            )
        except Exception as exc:
            return self._error("crypto_daily_failed", str(exc), auth_truth="validated")

    def get_forex_rate(self, from_currency: str = "USD", to_currency: str = "EUR") -> dict[str, Any]:
        """Real-time forex exchange rate."""
        try:
            r = self._request("GET", "", base_url=self.base_url, params={
                "function": "CURRENCY_EXCHANGE_RATE",
                "from_currency": from_currency, "to_currency": to_currency,
                "apikey": self._key(),
            })
            raw = r.json().get("Realtime Currency Exchange Rate", {})
            if not raw:
                return self._error("no_data", "Empty forex response", auth_truth="validated")
            return self._ok(
                data={
                    "from": from_currency, "to": to_currency,
                    "rate": float(raw.get("5. Exchange Rate", 0)),
                    "bid": float(raw.get("8. Bid Price", 0)),
                    "ask": float(raw.get("9. Ask Price", 0)),
                    "last_refreshed": raw.get("6. Last Refreshed"),
                },
                status="ok", auth_truth="validated",
            )
        except Exception as exc:
            return self._error("forex_failed", str(exc), auth_truth="validated")

    def get_earnings_calendar(self) -> dict[str, Any]:
        """Upcoming earnings announcements (CSV endpoint)."""
        if not self.is_configured():
            return self._error("no_key", "ALPHA_VANTAGE_API_KEY not set.", auth_truth="missing")
        try:
            r = self._request("GET", "", base_url=self.base_url, params={
                "function": "EARNINGS_CALENDAR", "horizon": "3month", "apikey": self._key(),
            })
            # Response is CSV
            lines = r.text.strip().split("\n")
            if len(lines) < 2:
                return self._error("no_data", "Empty earnings calendar", auth_truth="validated")
            headers = lines[0].split(",")
            earnings = []
            for line in lines[1:21]:
                vals = line.split(",")
                if len(vals) >= len(headers):
                    earnings.append(dict(zip(headers, vals)))
            return self._ok(
                data={"earnings": earnings, "count": len(earnings)},
                status="ok", auth_truth="validated",
            )
        except Exception as exc:
            return self._error("earnings_failed", str(exc), auth_truth="validated")
