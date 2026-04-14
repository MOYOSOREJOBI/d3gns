from __future__ import annotations

from typing import Any

from adapters.base_adapter import BaseAdapter


class OpenMeteoAdapter(BaseAdapter):
    """Open-Meteo — free global weather forecast API, no auth required."""

    platform_name = "open_meteo"
    mode = "PUBLIC DATA ONLY"
    live_capable = False
    execution_enabled = False
    auth_required = False
    data_truth_label = "PUBLIC DATA ONLY"
    base_url = "https://api.open-meteo.com/v1"

    # Major cities used as prediction market weather event proxies
    CITY_COORDS = {
        "new_york":     (40.7128, -74.0060),
        "london":       (51.5074, -0.1278),
        "los_angeles":  (34.0522, -118.2437),
        "chicago":      (41.8781, -87.6298),
        "miami":        (25.7617, -80.1918),
        "washington_dc": (38.9072, -77.0369),
        "san_francisco": (37.7749, -122.4194),
        "houston":      (29.7604, -95.3698),
        "toronto":      (43.6532, -79.3832),
        "paris":        (48.8566, 2.3522),
        "berlin":       (52.5200, 13.4050),
        "tokyo":        (35.6762, 139.6503),
    }

    def is_configured(self) -> bool:
        return True

    def healthcheck(self) -> dict[str, Any]:
        result = self.get_forecast(city="new_york", days=1)
        if result.get("ok"):
            return self._ok(data={"status": "ok"}, auth_truth="no_auth_required")
        return self._error("health_failed", result.get("error", "unknown"), auth_truth="no_auth_required")

    def get_forecast(self, city: str = "new_york", days: int = 7, lat: float | None = None, lon: float | None = None) -> dict[str, Any]:
        """Fetch daily weather forecast for a city or lat/lon."""
        if lat is None or lon is None:
            coords = self.CITY_COORDS.get(city.lower().replace(" ", "_"))
            if not coords:
                return self._error("unknown_city", f"City '{city}' not in presets. Provide lat/lon.", auth_truth="no_auth_required")
            lat, lon = coords
        try:
            r = self._request(
                "GET", "/forecast",
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max,weathercode,windspeed_10m_max,uv_index_max",
                    "timezone": "auto",
                    "forecast_days": min(days, 16),
                },
            )
            raw = r.json()
            daily = raw.get("daily", {})
            days_data = []
            times = daily.get("time", [])
            for i, t in enumerate(times):
                days_data.append({
                    "date": t,
                    "temp_max_c": daily.get("temperature_2m_max", [None])[i] if i < len(daily.get("temperature_2m_max", [])) else None,
                    "temp_min_c": daily.get("temperature_2m_min", [None])[i] if i < len(daily.get("temperature_2m_min", [])) else None,
                    "precip_mm": daily.get("precipitation_sum", [None])[i] if i < len(daily.get("precipitation_sum", [])) else None,
                    "precip_prob_pct": daily.get("precipitation_probability_max", [None])[i] if i < len(daily.get("precipitation_probability_max", [])) else None,
                    "wind_max_kmh": daily.get("windspeed_10m_max", [None])[i] if i < len(daily.get("windspeed_10m_max", [])) else None,
                    "weather_code": daily.get("weathercode", [None])[i] if i < len(daily.get("weathercode", [])) else None,
                    "uv_index_max": daily.get("uv_index_max", [None])[i] if i < len(daily.get("uv_index_max", [])) else None,
                })
            return self._ok(
                data={
                    "city": city,
                    "lat": lat,
                    "lon": lon,
                    "timezone": raw.get("timezone"),
                    "days": days_data,
                },
                status="ok",
                auth_truth="no_auth_required",
            )
        except Exception as exc:
            return self._error("forecast_failed", str(exc), auth_truth="no_auth_required")

    def get_hourly(self, city: str = "new_york", hours: int = 24, lat: float | None = None, lon: float | None = None) -> dict[str, Any]:
        """Fetch hourly weather data."""
        if lat is None or lon is None:
            coords = self.CITY_COORDS.get(city.lower().replace(" ", "_"))
            if not coords:
                return self._error("unknown_city", f"City '{city}' not found.", auth_truth="no_auth_required")
            lat, lon = coords
        try:
            r = self._request(
                "GET", "/forecast",
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "hourly": "temperature_2m,precipitation,weathercode,windspeed_10m,precipitation_probability",
                    "timezone": "auto",
                    "forecast_days": max(1, min(hours // 24 + 1, 7)),
                },
            )
            raw = r.json()
            hourly = raw.get("hourly", {})
            times = hourly.get("time", [])
            data = [
                {
                    "time": t,
                    "temp_c": hourly.get("temperature_2m", [None])[i] if i < len(hourly.get("temperature_2m", [])) else None,
                    "precip_mm": hourly.get("precipitation", [None])[i] if i < len(hourly.get("precipitation", [])) else None,
                    "precip_prob_pct": hourly.get("precipitation_probability", [None])[i] if i < len(hourly.get("precipitation_probability", [])) else None,
                    "wind_kmh": hourly.get("windspeed_10m", [None])[i] if i < len(hourly.get("windspeed_10m", [])) else None,
                    "weather_code": hourly.get("weathercode", [None])[i] if i < len(hourly.get("weathercode", [])) else None,
                }
                for i, t in enumerate(times[:hours])
            ]
            return self._ok(
                data={"city": city, "lat": lat, "lon": lon, "hours": data},
                status="ok",
                auth_truth="no_auth_required",
            )
        except Exception as exc:
            return self._error("hourly_failed", str(exc), auth_truth="no_auth_required")

    def get_extreme_event_signal(self, city: str = "new_york") -> dict[str, Any]:
        """Detect extreme weather events relevant to prediction markets (storms, heatwaves, freezes)."""
        result = self.get_forecast(city=city, days=7)
        if not result.get("ok"):
            return result
        days_data = result["data"].get("days", [])
        events = []
        for day in days_data:
            flags = []
            temp_max = day.get("temp_max_c")
            temp_min = day.get("temp_min_c")
            precip = day.get("precip_mm") or 0
            wind = day.get("wind_max_kmh") or 0
            precip_prob = day.get("precip_prob_pct") or 0
            if temp_max is not None and temp_max >= 38:
                flags.append("extreme_heat")
            if temp_min is not None and temp_min <= -10:
                flags.append("extreme_cold")
            if precip >= 50:
                flags.append("heavy_rain_or_snow")
            if wind >= 80:
                flags.append("high_wind_storm")
            if precip_prob >= 80 and precip >= 20:
                flags.append("high_precip_probability")
            if flags:
                events.append({"date": day.get("date"), "flags": flags, "details": day})
        return self._ok(
            data={
                "city": city,
                "extreme_events": events,
                "event_count": len(events),
                "forecast_days": len(days_data),
                "signal": "extreme_weather_detected" if events else "no_extreme_events",
            },
            status="ok",
            auth_truth="no_auth_required",
        )
