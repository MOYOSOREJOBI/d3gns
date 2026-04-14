"""
Binance Public API adapter — NO authentication required.
Covers: spot prices, 24h tickers, klines/OHLCV, funding rates,
        open interest, order book top, recent trades.

Free, no API key, no rate-limit issues for public endpoints.
Rate limit: ~1200 req/min (weight-based). We stay conservative.
"""
from __future__ import annotations

from typing import Any

from adapters.base_adapter import BaseAdapter


class BinancePublicAdapter(BaseAdapter):
    platform_name = "binance_public"
    mode = "PUBLIC DATA ONLY"
    live_capable = False
    execution_enabled = False
    auth_required = False
    data_truth_label = "PUBLIC DATA ONLY"
    base_url = "https://api.binance.com"
    _FUTURES_URL = "https://fapi.binance.com"

    def is_configured(self) -> bool:
        return True  # no auth needed

    def healthcheck(self) -> dict[str, Any]:
        res = self.get_price("BTCUSDT")
        if res.get("ok"):
            return self._ok(data={"status": "ok", "btc_price": res["data"].get("price")},
                            auth_truth="no_auth_required")
        return self._error("health_failed", res.get("error", "unknown"), auth_truth="no_auth_required")

    def get_price(self, symbol: str = "BTCUSDT") -> dict[str, Any]:
        """Latest spot price for a symbol."""
        try:
            r = self._request("GET", "/api/v3/ticker/price",
                              params={"symbol": symbol.upper()}, timeout=6.0)
            d = r.json()
            return self._ok(data={
                "symbol": d.get("symbol"),
                "price":  float(d.get("price", 0)),
            }, auth_truth="no_auth_required")
        except Exception as exc:
            return self._error("price_failed", str(exc), auth_truth="no_auth_required")

    def get_24h_ticker(self, symbol: str = "BTCUSDT") -> dict[str, Any]:
        """24-hour rolling window statistics."""
        try:
            r = self._request("GET", "/api/v3/ticker/24hr",
                              params={"symbol": symbol.upper()}, timeout=8.0)
            d = r.json()
            return self._ok(data={
                "symbol":               d.get("symbol"),
                "price":                float(d.get("lastPrice", 0)),
                "price_change_pct_24h": float(d.get("priceChangePercent", 0)),
                "high_24h":             float(d.get("highPrice", 0)),
                "low_24h":              float(d.get("lowPrice", 0)),
                "volume_24h":           float(d.get("volume", 0)),
                "quote_volume_24h":     float(d.get("quoteVolume", 0)),
                "trades_count":         d.get("count"),
                "open_price":           float(d.get("openPrice", 0)),
                "weighted_avg_price":   float(d.get("weightedAvgPrice", 0)),
            }, auth_truth="no_auth_required")
        except Exception as exc:
            return self._error("ticker_24h_failed", str(exc), auth_truth="no_auth_required")

    def get_top_tickers(self, symbols: list[str] | None = None) -> dict[str, Any]:
        """Batch 24h stats for multiple coins."""
        targets = symbols or ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]
        results = {}
        for sym in targets:
            r = self.get_24h_ticker(sym)
            if r.get("ok"):
                results[sym] = r["data"]
        return self._ok(data={"tickers": results, "count": len(results)}, auth_truth="no_auth_required")

    def get_klines(
        self,
        symbol: str = "BTCUSDT",
        interval: str = "1h",
        limit: int = 100,
    ) -> dict[str, Any]:
        """
        OHLCV candlestick data.
        interval: 1m, 5m, 15m, 1h, 4h, 1d, 1w
        """
        try:
            r = self._request("GET", "/api/v3/klines",
                              params={"symbol": symbol.upper(), "interval": interval, "limit": limit},
                              timeout=10.0)
            raw = r.json()
            candles = [
                {
                    "open_time":  c[0],
                    "open":       float(c[1]),
                    "high":       float(c[2]),
                    "low":        float(c[3]),
                    "close":      float(c[4]),
                    "volume":     float(c[5]),
                    "close_time": c[6],
                    "quote_vol":  float(c[7]),
                    "trades":     c[8],
                }
                for c in raw
            ]
            closes  = [c["close"]  for c in candles]
            highs   = [c["high"]   for c in candles]
            lows    = [c["low"]    for c in candles]
            volumes = [c["volume"] for c in candles]
            return self._ok(data={
                "symbol":   symbol,
                "interval": interval,
                "count":    len(candles),
                "candles":  candles,
                "closes":   closes,
                "highs":    highs,
                "lows":     lows,
                "volumes":  volumes,
                "latest_close": closes[-1] if closes else None,
            }, auth_truth="no_auth_required")
        except Exception as exc:
            return self._error("klines_failed", str(exc), auth_truth="no_auth_required")

    def get_orderbook_top(self, symbol: str = "BTCUSDT", limit: int = 10) -> dict[str, Any]:
        """Top N order book levels — useful for spread and liquidity analysis."""
        try:
            r = self._request("GET", "/api/v3/depth",
                              params={"symbol": symbol.upper(), "limit": limit}, timeout=6.0)
            d = r.json()
            asks = [[float(p), float(q)] for p, q in d.get("asks", [])]
            bids = [[float(p), float(q)] for p, q in d.get("bids", [])]
            best_ask = asks[0][0]  if asks else None
            best_bid = bids[0][0]  if bids else None
            spread   = round(best_ask - best_bid, 6) if best_ask and best_bid else None
            spread_pct = round(spread / best_bid * 100, 4) if spread and best_bid else None
            return self._ok(data={
                "symbol":     symbol,
                "best_ask":   best_ask,
                "best_bid":   best_bid,
                "spread":     spread,
                "spread_pct": spread_pct,
                "asks":       asks[:5],
                "bids":       bids[:5],
            }, auth_truth="no_auth_required")
        except Exception as exc:
            return self._error("orderbook_failed", str(exc), auth_truth="no_auth_required")

    def get_funding_rate(self, symbol: str = "BTCUSDT") -> dict[str, Any]:
        """
        Perpetual futures funding rate from Binance Futures.
        Positive = longs pay shorts (bearish sentiment from longs).
        Negative = shorts pay longs (bullish sentiment, bears overleveraged).
        """
        try:
            r = self._request("GET", "/fapi/v1/premiumIndex",
                              base_url=self._FUTURES_URL,
                              params={"symbol": symbol.upper()}, timeout=6.0)
            d = r.json()
            funding_rate = float(d.get("lastFundingRate", 0))
            mark_price   = float(d.get("markPrice", 0))
            index_price  = float(d.get("indexPrice", 0))
            basis_pct    = round((mark_price - index_price) / index_price * 100, 4) if index_price else None

            # Signal interpretation
            if funding_rate > 0.001:       # > 0.1%
                signal = "bearish"         # longs heavily overleveraged
            elif funding_rate < -0.001:    # < -0.1%
                signal = "bullish"         # shorts heavily overleveraged
            else:
                signal = "neutral"

            return self._ok(data={
                "symbol":       symbol,
                "funding_rate": funding_rate,
                "funding_rate_pct": round(funding_rate * 100, 6),
                "funding_8h_annualised": round(funding_rate * 3 * 365 * 100, 2),
                "mark_price":   mark_price,
                "index_price":  index_price,
                "basis_pct":    basis_pct,
                "signal":       signal,
                "next_funding_time": d.get("nextFundingTime"),
            }, auth_truth="no_auth_required")
        except Exception as exc:
            return self._error("funding_rate_failed", str(exc), auth_truth="no_auth_required")

    def get_open_interest(self, symbol: str = "BTCUSDT") -> dict[str, Any]:
        """Futures open interest — high OI + rising price = strong trend."""
        try:
            r = self._request("GET", "/fapi/v1/openInterest",
                              base_url=self._FUTURES_URL,
                              params={"symbol": symbol.upper()}, timeout=6.0)
            d = r.json()
            oi       = float(d.get("openInterest", 0))
            price    = float(self.get_price(symbol)["data"].get("price", 1) or 1)
            oi_usd   = round(oi * price, 0)
            return self._ok(data={
                "symbol":      symbol,
                "open_interest": oi,
                "open_interest_usd": oi_usd,
            }, auth_truth="no_auth_required")
        except Exception as exc:
            return self._error("open_interest_failed", str(exc), auth_truth="no_auth_required")

    def get_market_signal(self, symbols: list[str] | None = None) -> dict[str, Any]:
        """
        Composite signal from funding rates + 24h momentum for a basket of symbols.
        """
        targets = symbols or ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
        signals: list[dict[str, Any]] = []
        for sym in targets:
            fr_res = self.get_funding_rate(sym)
            tk_res = self.get_24h_ticker(sym)
            if fr_res.get("ok") and tk_res.get("ok"):
                fr = fr_res["data"]
                tk = tk_res["data"]
                signals.append({
                    "symbol":           sym,
                    "funding_signal":   fr.get("signal"),
                    "funding_rate_pct": fr.get("funding_rate_pct"),
                    "change_24h_pct":   tk.get("price_change_pct_24h"),
                    "volume_24h":       tk.get("quote_volume_24h"),
                })

        bull_count = sum(1 for s in signals if s.get("funding_signal") == "bullish")
        bear_count = sum(1 for s in signals if s.get("funding_signal") == "bearish")
        avg_change = sum(s.get("change_24h_pct", 0) or 0 for s in signals) / max(len(signals), 1)

        if bull_count > bear_count and avg_change > 0:
            composite = "bullish"
        elif bear_count > bull_count or avg_change < -2:
            composite = "bearish"
        else:
            composite = "neutral"

        return self._ok(data={
            "composite_signal": composite,
            "bull_count":       bull_count,
            "bear_count":       bear_count,
            "avg_change_24h":   round(avg_change, 3),
            "signals":          signals,
        }, auth_truth="no_auth_required")
