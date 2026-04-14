"""
CoinGlass adapter — free public endpoints (no API key required for basic data).
Covers: funding rates, liquidations, long/short ratio, open interest history.

Note: CoinGlass has a public free tier at coinglass.com/api that doesn't
require authentication for aggregated market data. Some endpoints require
a key (use COINGLASS_API_KEY env var to unlock).
"""
from __future__ import annotations

from typing import Any

from adapters.base_adapter import BaseAdapter


class CoinGlassAdapter(BaseAdapter):
    platform_name = "coinglass"
    mode = "PUBLIC DATA ONLY"
    live_capable = False
    execution_enabled = False
    auth_required = False
    data_truth_label = "PUBLIC DATA ONLY"
    base_url = "https://open-api.coinglass.com/public/v2"
    _ALT_BASE = "https://open-api.coinglass.com"

    def is_configured(self) -> bool:
        return True  # works without key; key unlocks more endpoints

    def _cg_headers(self) -> dict[str, str]:
        key = self._setting("COINGLASS_API_KEY", "")
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if key:
            headers["CG-API-KEY"] = key
        return headers

    def healthcheck(self) -> dict[str, Any]:
        res = self.get_funding_rate_summary()
        if res.get("ok"):
            return self._ok(data={"status": "ok"}, auth_truth="no_auth_required")
        return self._error("health_failed", res.get("error", "unknown"), auth_truth="no_auth_required")

    def get_funding_rate_summary(self, symbol: str = "BTC") -> dict[str, Any]:
        """
        Funding rate across major exchanges for a symbol.
        Returns average, max, min funding rates.
        """
        try:
            r = self._request("GET", "/indicator/funding_rates_ohlc",
                              params={"symbol": symbol, "interval": "1d", "limit": 1},
                              headers=self._cg_headers(), timeout=10.0)
            data = r.json()
            if not data.get("success") and data.get("code") != "0":
                return self._error("api_error", str(data.get("msg", "error")), auth_truth="no_auth_required")
            items = data.get("data", {}).get("dataMap", {})
            rates = []
            for exchange, history in items.items():
                if history:
                    latest = history[-1]
                    if isinstance(latest, (int, float)):
                        rates.append({"exchange": exchange, "rate": latest})

            avg_rate = sum(r["rate"] for r in rates) / len(rates) if rates else 0
            signal = "bearish" if avg_rate > 0.0003 else "bullish" if avg_rate < -0.0003 else "neutral"

            return self._ok(data={
                "symbol":      symbol,
                "avg_rate":    round(avg_rate, 8),
                "avg_rate_pct": round(avg_rate * 100, 6),
                "annualised_pct": round(avg_rate * 3 * 365 * 100, 2),
                "signal":      signal,
                "by_exchange": rates[:10],
            }, auth_truth="no_auth_required")
        except Exception as exc:
            return self._error("funding_failed", str(exc), auth_truth="no_auth_required")

    def get_liquidations(self, symbol: str = "BTC", time_range: str = "1h") -> dict[str, Any]:
        """
        Recent liquidations — high liquidation = capitulation signal.
        time_range: 1h, 4h, 12h, 24h
        """
        try:
            r = self._request("GET", "/indicator/liquidation_history",
                              params={"symbol": symbol, "time_type": time_range},
                              headers=self._cg_headers(), timeout=10.0)
            data = r.json()
            items = data.get("data", [])
            if not items:
                return self._ok(data={"symbol": symbol, "liquidations": [], "signal": "neutral"},
                                auth_truth="no_auth_required")

            latest = items[-1] if items else {}
            buy_liq  = float(latest.get("buyLiquidationUsd", 0) or 0)   # shorts liquidated
            sell_liq = float(latest.get("sellLiquidationUsd", 0) or 0)  # longs liquidated
            total    = buy_liq + sell_liq

            # High sell liquidations = long squeeze = potential bottom
            # High buy liquidations  = short squeeze = potential top
            if total > 0:
                liq_ratio = sell_liq / total
                if liq_ratio > 0.70 and total > 1e7:   # >70% longs liquidated, >$10M
                    signal = "bullish_contrarian"  # capitulation likely
                elif liq_ratio < 0.30 and total > 1e7: # >70% shorts liquidated
                    signal = "bearish_contrarian"  # short squeeze peak
                else:
                    signal = "neutral"
            else:
                signal = "neutral"

            return self._ok(data={
                "symbol":            symbol,
                "time_range":        time_range,
                "long_liq_usd":      sell_liq,
                "short_liq_usd":     buy_liq,
                "total_liq_usd":     total,
                "long_liq_pct":      round(sell_liq / total * 100, 1) if total else 0,
                "signal":            signal,
            }, auth_truth="no_auth_required")
        except Exception as exc:
            return self._error("liquidation_failed", str(exc), auth_truth="no_auth_required")

    def get_long_short_ratio(self, symbol: str = "BTC") -> dict[str, Any]:
        """
        Global long/short ratio across exchanges.
        Ratio > 1 = more longs than shorts (bearish contrarian signal when extreme).
        """
        try:
            r = self._request("GET", "/indicator/top_long_short_account_ratio",
                              params={"symbol": symbol, "interval": "1h", "limit": 24},
                              headers=self._cg_headers(), timeout=10.0)
            data = r.json()
            items = data.get("data", [])
            if not items:
                return self._error("no_data", "No long/short data", auth_truth="no_auth_required")

            latest = items[-1] if items else {}
            ratio  = float(latest.get("longShortRatio", 1) or 1)
            long_pct  = float(latest.get("longAccount", 50) or 50)
            short_pct = float(latest.get("shortAccount", 50) or 50)

            # Extremes: >65% long = crowded long = bearish risk
            if long_pct > 65:
                signal = "bearish_crowded"
            elif long_pct < 35:
                signal = "bullish_crowded"
            else:
                signal = "neutral"

            return self._ok(data={
                "symbol":    symbol,
                "ls_ratio":  round(ratio, 4),
                "long_pct":  round(long_pct, 2),
                "short_pct": round(short_pct, 2),
                "signal":    signal,
            }, auth_truth="no_auth_required")
        except Exception as exc:
            return self._error("ls_ratio_failed", str(exc), auth_truth="no_auth_required")

    def get_open_interest_history(self, symbol: str = "BTC", interval: str = "1h") -> dict[str, Any]:
        """Open interest history — rising OI + rising price = trend confirmation."""
        try:
            r = self._request("GET", "/indicator/open_interest_history",
                              params={"symbol": symbol, "interval": interval, "limit": 24},
                              headers=self._cg_headers(), timeout=10.0)
            data = r.json()
            items = data.get("data", [])
            if not items:
                return self._error("no_data", "No OI data", auth_truth="no_auth_required")

            oi_vals = [float(d.get("openInterestUsd", 0) or 0) for d in items]
            if len(oi_vals) >= 2:
                oi_change_pct = round((oi_vals[-1] - oi_vals[0]) / oi_vals[0] * 100, 2) if oi_vals[0] else 0
            else:
                oi_change_pct = 0

            signal = "increasing_oi" if oi_change_pct > 5 else "decreasing_oi" if oi_change_pct < -5 else "stable_oi"

            return self._ok(data={
                "symbol":            symbol,
                "current_oi_usd":    oi_vals[-1] if oi_vals else None,
                "oi_change_24h_pct": oi_change_pct,
                "signal":            signal,
                "history":           oi_vals[-12:],
            }, auth_truth="no_auth_required")
        except Exception as exc:
            return self._error("oi_history_failed", str(exc), auth_truth="no_auth_required")

    def get_market_signal(self, symbols: list[str] | None = None) -> dict[str, Any]:
        """Composite on-chain derivatives signal."""
        targets = symbols or ["BTC", "ETH"]
        signals: list[dict[str, Any]] = []
        for sym in targets:
            ls = self.get_long_short_ratio(sym)
            fr = self.get_funding_rate_summary(sym)
            liq = self.get_liquidations(sym)
            if ls.get("ok") and fr.get("ok"):
                signals.append({
                    "symbol":      sym,
                    "ls_signal":   ls["data"].get("signal"),
                    "fr_signal":   fr["data"].get("signal"),
                    "liq_signal":  liq.get("data", {}).get("signal") if liq.get("ok") else None,
                    "long_pct":    ls["data"].get("long_pct"),
                    "funding_pct": fr["data"].get("avg_rate_pct"),
                })

        # Composite
        bull = sum(1 for s in signals if s.get("fr_signal") == "bullish")
        bear = sum(1 for s in signals if s.get("fr_signal") == "bearish")
        composite = "bullish" if bull > bear else "bearish" if bear > bull else "neutral"

        return self._ok(data={
            "composite_signal": composite,
            "signals": signals,
            "sources": ["coinglass.com (derivatives data)"],
        }, auth_truth="no_auth_required")
