from __future__ import annotations

from typing import Any

from adapters.base_adapter import BaseAdapter


class NOAANWSAdapter(BaseAdapter):
    """NOAA National Weather Service API — no auth, US weather forecasts and alerts."""

    platform_name = "noaa_nws"
    mode = "PUBLIC DATA ONLY"
    live_capable = False
    execution_enabled = False
    auth_required = False
    data_truth_label = "PUBLIC DATA ONLY"
    base_url = "https://api.weather.gov"

    # NWS grid office + gridX, gridY for major cities
    _CITY_GRIDS = {
        "new_york":      ("OKX", 33, 37),
        "washington_dc": ("LWX", 96, 70),
        "chicago":       ("LOT", 75, 73),
        "miami":         ("MFL", 101, 42),
        "los_angeles":   ("LOX", 154, 41),
        "houston":       ("HGX", 68, 103),
        "seattle":       ("SEW", 124, 68),
        "denver":        ("BOU", 57, 63),
        "boston":        ("BOX", 69, 91),
        "atlanta":       ("FFC", 52, 82),
    }

    _HEADERS = {
        "User-Agent": "(DeGens Research Bot, research@degens.local)",
        "Accept": "application/geo+json",
    }

    def is_configured(self) -> bool:
        return True

    def healthcheck(self) -> dict[str, Any]:
        try:
            r = self._request("GET", "/", headers=self._HEADERS)
            return self._ok(data={"status": "ok"}, auth_truth="no_auth_required")
        except Exception as exc:
            return self._error("health_failed", str(exc), auth_truth="no_auth_required")

    def get_forecast(self, city: str = "new_york") -> dict[str, Any]:
        """Fetch 7-day forecast for a major US city."""
        grid = self._CITY_GRIDS.get(city.lower().replace(" ", "_"))
        if not grid:
            return self._error("unknown_city", f"City '{city}' not in NWS grid presets.", auth_truth="no_auth_required")
        office, grid_x, grid_y = grid
        try:
            r = self._request(
                "GET", f"/gridpoints/{office}/{grid_x},{grid_y}/forecast",
                headers=self._HEADERS,
            )
            raw = r.json()
            periods = raw.get("properties", {}).get("periods", [])
            forecast = [
                {
                    "name": p.get("name"),
                    "start_time": p.get("startTime"),
                    "is_daytime": p.get("isDaytime"),
                    "temp_f": p.get("temperature"),
                    "temp_unit": p.get("temperatureUnit"),
                    "wind_speed": p.get("windSpeed"),
                    "wind_direction": p.get("windDirection"),
                    "short_forecast": p.get("shortForecast"),
                    "detailed_forecast": p.get("detailedForecast"),
                    "precip_chance_pct": p.get("probabilityOfPrecipitation", {}).get("value"),
                }
                for p in periods[:14]
            ]
            return self._ok(
                data={"city": city, "office": office, "periods": forecast},
                status="ok",
                auth_truth="no_auth_required",
            )
        except Exception as exc:
            return self._error("forecast_failed", str(exc), auth_truth="no_auth_required")

    def get_active_alerts(self, state: str = "NY") -> dict[str, Any]:
        """Fetch active weather alerts for a US state (2-letter code)."""
        try:
            r = self._request(
                "GET", "/alerts/active",
                params={"area": state.upper()},
                headers=self._HEADERS,
            )
            raw = r.json()
            features = raw.get("features", [])
            alerts = [
                {
                    "id": f.get("id"),
                    "event": f.get("properties", {}).get("event"),
                    "severity": f.get("properties", {}).get("severity"),
                    "urgency": f.get("properties", {}).get("urgency"),
                    "certainty": f.get("properties", {}).get("certainty"),
                    "headline": f.get("properties", {}).get("headline"),
                    "description": (f.get("properties", {}).get("description") or "")[:300],
                    "effective": f.get("properties", {}).get("effective"),
                    "expires": f.get("properties", {}).get("expires"),
                    "areas": f.get("properties", {}).get("areaDesc"),
                }
                for f in features
            ]
            severe = [a for a in alerts if a.get("severity") in ("Extreme", "Severe")]
            return self._ok(
                data={
                    "state": state.upper(),
                    "total_alerts": len(alerts),
                    "severe_alerts": len(severe),
                    "alerts": alerts,
                    "signal": "severe_weather_alert" if severe else "no_severe_alerts",
                },
                status="ok",
                auth_truth="no_auth_required",
            )
        except Exception as exc:
            return self._error("alerts_failed", str(exc), auth_truth="no_auth_required")

    def get_hourly_forecast(self, city: str = "new_york") -> dict[str, Any]:
        """Fetch hourly forecast for a city."""
        grid = self._CITY_GRIDS.get(city.lower().replace(" ", "_"))
        if not grid:
            return self._error("unknown_city", f"City '{city}' not in presets.", auth_truth="no_auth_required")
        office, grid_x, grid_y = grid
        try:
            r = self._request(
                "GET", f"/gridpoints/{office}/{grid_x},{grid_y}/forecast/hourly",
                headers=self._HEADERS,
            )
            raw = r.json()
            periods = raw.get("properties", {}).get("periods", [])
            hours = [
                {
                    "start_time": p.get("startTime"),
                    "temp_f": p.get("temperature"),
                    "wind_speed": p.get("windSpeed"),
                    "short_forecast": p.get("shortForecast"),
                    "precip_chance_pct": p.get("probabilityOfPrecipitation", {}).get("value"),
                }
                for p in periods[:48]
            ]
            return self._ok(
                data={"city": city, "hours": hours},
                status="ok",
                auth_truth="no_auth_required",
            )
        except Exception as exc:
            return self._error("hourly_failed", str(exc), auth_truth="no_auth_required")
