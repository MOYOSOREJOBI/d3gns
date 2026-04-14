from __future__ import annotations

from typing import Any

from adapters.base_adapter import BaseAdapter


class FREDAdapter(BaseAdapter):
    """
    Federal Reserve Economic Data (FRED) API — St. Louis Fed.
    Free API key at fred.stlouisfed.org. No key = very limited access.
    """

    platform_name = "fred_api"
    mode = "PUBLIC DATA ONLY"
    live_capable = False
    execution_enabled = False
    auth_required = False  # degraded without key
    data_truth_label = "PUBLIC DATA ONLY"
    base_url = "https://api.stlouisfed.org/fred"

    # Key FRED series for macro prediction market signals
    SERIES = {
        "FEDFUNDS": "Federal Funds Rate",
        "CPIAUCSL": "CPI All Items (YoY inflation)",
        "CPILFESL": "Core CPI (excl. food/energy)",
        "PPIFIS": "PPI Final Demand",
        "UNRATE": "US Unemployment Rate",
        "PAYEMS": "Non-Farm Payrolls",
        "GDP": "US GDP (Quarterly)",
        "GDPC1": "Real GDP",
        "T10Y2Y": "10Y-2Y Treasury Yield Spread (Recession indicator)",
        "T10YIE": "10-Year Breakeven Inflation Rate",
        "DGS10": "10-Year Treasury Yield",
        "DGS2": "2-Year Treasury Yield",
        "SP500": "S&P 500",
        "VIXCLS": "CBOE VIX (Market Volatility Index)",
        "DCOILWTICO": "WTI Crude Oil Price",
        "DEXUSEU": "USD/EUR Exchange Rate",
        "M2SL": "M2 Money Supply",
        "WALCL": "Fed Balance Sheet (Total Assets)",
        "MORTGAGE30US": "30-Year Fixed Mortgage Rate",
        "UMCSENT": "University of Michigan Consumer Sentiment",
    }

    def is_configured(self) -> bool:
        return bool(self._setting("FRED_API_KEY", "").strip())

    def _api_key(self) -> str:
        return self._setting("FRED_API_KEY", "")

    def healthcheck(self) -> dict[str, Any]:
        if not self.is_configured():
            return self._ok(
                data={
                    "status": "degraded",
                    "note": "Set FRED_API_KEY. Free at fred.stlouisfed.org/docs/api/api_key.html",
                    "available_series": list(self.SERIES.keys()),
                },
                status="degraded",
                auth_truth="missing",
                degraded_reason="FRED_API_KEY not configured. Free key at fred.stlouisfed.org.",
            )
        result = self.get_series("FEDFUNDS", limit=1)
        if result.get("ok"):
            return self._ok(data={"status": "ok", "series_count": len(self.SERIES)}, auth_truth="validated")
        return self._error("health_failed", result.get("error", "unknown"), auth_truth="invalid")

    def get_series(self, series_id: str, limit: int = 12, sort_order: str = "desc") -> dict[str, Any]:
        """Fetch recent observations for a FRED data series."""
        if not self.is_configured():
            return self._error("no_key", "FRED_API_KEY not set. Free at fred.stlouisfed.org", auth_truth="missing")
        try:
            r = self._request(
                "GET", "/series/observations",
                params={
                    "series_id": series_id,
                    "api_key": self._api_key(),
                    "file_type": "json",
                    "sort_order": sort_order,
                    "limit": limit,
                },
            )
            raw = r.json()
            if "error_message" in raw:
                return self._error("api_error", raw.get("error_message", "API error"), auth_truth="invalid")
            obs = [
                {
                    "date": o.get("date"),
                    "value": None if o.get("value") == "." else float(o.get("value", 0) or 0),
                }
                for o in raw.get("observations", [])
                if o.get("value") != "."
            ]
            label = self.SERIES.get(series_id, series_id)
            # Most recent valid value
            latest = next((o for o in obs if o["value"] is not None), None)
            prior = next((o for o in obs[1:] if o["value"] is not None), None)
            delta = None
            delta_pct = None
            if latest and prior and latest["value"] is not None and prior["value"] is not None and prior["value"] != 0:
                delta = round(latest["value"] - prior["value"], 4)
                delta_pct = round((delta / abs(prior["value"])) * 100, 3)
            return self._ok(
                data={
                    "series_id": series_id,
                    "label": label,
                    "latest_value": latest["value"] if latest else None,
                    "latest_date": latest["date"] if latest else None,
                    "prior_value": prior["value"] if prior else None,
                    "prior_date": prior["date"] if prior else None,
                    "delta": delta,
                    "delta_pct": delta_pct,
                    "observations": obs,
                },
                status="ok",
                auth_truth="validated",
            )
        except Exception as exc:
            return self._error("series_failed", str(exc), auth_truth="validated")

    def get_macro_snapshot(self) -> dict[str, Any]:
        """Fetch a snapshot of key macro indicators for prediction market signals."""
        key_series = ["FEDFUNDS", "CPIAUCSL", "UNRATE", "T10Y2Y", "DGS10", "VIXCLS", "SP500"]
        snapshot = {}
        for sid in key_series:
            res = self.get_series(sid, limit=2)
            if res.get("ok"):
                snapshot[sid] = {
                    "label": res["data"].get("label"),
                    "latest_value": res["data"].get("latest_value"),
                    "latest_date": res["data"].get("latest_date"),
                    "delta": res["data"].get("delta"),
                    "delta_pct": res["data"].get("delta_pct"),
                }
            else:
                snapshot[sid] = {"error": res.get("error")}
        # Derive recession signal from yield curve
        t10y2y = snapshot.get("T10Y2Y", {}).get("latest_value")
        recession_signal = "inverted_yield_curve" if t10y2y is not None and t10y2y < 0 else "normal_curve"
        # VIX regime
        vix = snapshot.get("VIXCLS", {}).get("latest_value")
        vix_regime = "low" if vix and vix < 15 else "elevated" if vix and vix < 25 else "high" if vix and vix < 35 else "extreme"
        return self._ok(
            data={
                "snapshot": snapshot,
                "recession_signal": recession_signal,
                "yield_spread": t10y2y,
                "vix": vix,
                "vix_regime": vix_regime,
            },
            status="ok",
            auth_truth="validated" if self.is_configured() else "missing",
        )

    def search_series(self, query: str, limit: int = 10) -> dict[str, Any]:
        """Search FRED for series matching a query."""
        if not self.is_configured():
            return self._error("no_key", "FRED_API_KEY not set.", auth_truth="missing")
        try:
            r = self._request(
                "GET", "/series/search",
                params={
                    "search_text": query,
                    "api_key": self._api_key(),
                    "file_type": "json",
                    "limit": limit,
                    "order_by": "popularity",
                    "sort_order": "desc",
                },
            )
            raw = r.json()
            if "error_message" in raw:
                return self._error("api_error", raw.get("error_message"), auth_truth="invalid")
            series_list = [
                {
                    "id": s.get("id"),
                    "title": s.get("title"),
                    "frequency": s.get("frequency_short"),
                    "units": s.get("units_short"),
                    "last_updated": s.get("last_updated"),
                    "popularity": s.get("popularity"),
                }
                for s in raw.get("seriess", [])
            ]
            return self._ok(
                data={"query": query, "series": series_list, "count": len(series_list)},
                status="ok",
                auth_truth="validated",
            )
        except Exception as exc:
            return self._error("search_failed", str(exc), auth_truth="validated")

    def get_release_calendar(self, limit: int = 20) -> dict[str, Any]:
        """Fetch upcoming FRED data releases (CPI, NFP, GDP publication dates)."""
        if not self.is_configured():
            return self._error("no_key", "FRED_API_KEY not set.", auth_truth="missing")
        try:
            r = self._request(
                "GET", "/releases/dates",
                params={
                    "api_key": self._api_key(),
                    "file_type": "json",
                    "include_release_dates_with_no_data": "false",
                    "limit": limit,
                    "sort_order": "asc",
                },
            )
            raw = r.json()
            dates = raw.get("release_dates", [])
            return self._ok(
                data={"releases": dates, "count": len(dates)},
                status="ok",
                auth_truth="validated",
            )
        except Exception as exc:
            return self._error("calendar_failed", str(exc), auth_truth="validated")
