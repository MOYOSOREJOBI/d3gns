from __future__ import annotations

from typing import Any

from bots.base_research_bot import BaseResearchBot


class WeatherForecastDislocationBot(BaseResearchBot):
    bot_id = "bot_weather_forecast_dislocation"
    display_name = "Weather Forecast Dislocation"
    platform = "polymarket"
    mode = "RESEARCH"
    signal_type = "forecast_revision"
    paper_only = True
    research_only = True
    implemented = True
    truth_label = "PUBLIC_DATA_ONLY"
    quality_tier = "B"
    risk_tier = "low"
    description = (
        "Monitors Open-Meteo (global, no auth) and NOAA/NWS (US, no auth) for extreme "
        "weather events across major US cities. When severe weather is detected in an area "
        "with active Polymarket weather/event markets, flags a potential dislocation. "
        "Also scans NWS active alerts for severe/extreme events."
    )
    edge_source = "NWS/Open-Meteo extreme event detection vs. Polymarket weather market pricing lag"
    opp_cadence_per_day = 2.0
    avg_hold_hours = 6.0
    fee_drag_bps = 80
    fill_rate = 0.65
    platforms = ["polymarket", "open_meteo", "noaa_nws"]

    def __init__(self, adapter=None):
        self.adapter = adapter

    _TARGET_CITIES = ["new_york", "los_angeles", "chicago", "miami", "houston", "washington_dc"]
    _TARGET_STATES = ["NY", "CA", "IL", "FL", "TX", "DC", "MA", "WA"]

    def run_one_cycle(self) -> dict[str, Any]:
        from adapters.open_meteo import OpenMeteoAdapter
        from adapters.noaa_nws import NOAANWSAdapter

        om = OpenMeteoAdapter()
        nws = NOAANWSAdapter()

        errors: list[str] = []
        source_status: dict[str, str] = {}
        all_extreme_events: list[dict] = []
        all_alerts: list[dict] = []
        city_forecasts: dict[str, Any] = {}

        # --- Open-Meteo: scan all target cities for extreme events ---
        for city in self._TARGET_CITIES:
            res = om.get_extreme_event_signal(city=city)
            if res.get("ok"):
                events = res["data"].get("extreme_events", [])
                city_forecasts[city] = {
                    "extreme_events": events,
                    "event_count": res["data"].get("event_count", 0),
                    "forecast_days": res["data"].get("forecast_days", 0),
                }
                if events:
                    for ev in events:
                        all_extreme_events.append({
                            "city": city,
                            "date": ev.get("date"),
                            "flags": ev.get("flags", []),
                            "details": {k: v for k, v in (ev.get("details") or {}).items() if k != "weather_code"},
                        })
                source_status[f"open_meteo_{city}"] = "live"
            else:
                errors.append(f"open_meteo/{city}: {res.get('error', 'failed')}")
                source_status[f"open_meteo_{city}"] = "error"

        # --- NOAA NWS: active alerts for target states ---
        for state in self._TARGET_STATES[:5]:  # limit to 5 states
            res = nws.get_active_alerts(state=state)
            if res.get("ok"):
                severe = [a for a in res["data"].get("alerts", []) if a.get("severity") in ("Extreme", "Severe")]
                if severe:
                    for alert in severe:
                        all_alerts.append({
                            "state": state,
                            "event": alert.get("event"),
                            "severity": alert.get("severity"),
                            "urgency": alert.get("urgency"),
                            "headline": alert.get("headline"),
                            "areas": alert.get("areas"),
                            "effective": alert.get("effective"),
                            "expires": alert.get("expires"),
                        })
                source_status[f"nws_{state}"] = "live"
            else:
                errors.append(f"nws/{state}: {res.get('error', 'failed')}")
                source_status[f"nws_{state}"] = "error"

        live_count = sum(1 for v in source_status.values() if v == "live")
        extreme_city_count = sum(1 for c in city_forecasts.values() if c.get("event_count", 0) > 0)
        severe_alert_count = len(all_alerts)

        # Signal logic
        confidence = 0.0
        signal_taken = False
        degraded_reason = ""

        if live_count == 0:
            degraded_reason = "All weather sources failed. Check network."
        elif extreme_city_count == 0 and severe_alert_count == 0:
            degraded_reason = "No extreme weather events or NWS severe alerts detected in target cities/states."
        else:
            # Extreme weather found — potential prediction market dislocation
            if severe_alert_count >= 2 or extreme_city_count >= 2:
                confidence = min(0.35 + severe_alert_count * 0.05 + extreme_city_count * 0.05, 0.55)
            elif severe_alert_count >= 1 or extreme_city_count >= 1:
                confidence = 0.25
            degraded_reason = "" if confidence > 0 else "Weather events detected but below dislocation threshold."

        # Summarize extreme event types
        event_flags = {}
        for ev in all_extreme_events:
            for flag in ev.get("flags", []):
                event_flags[flag] = event_flags.get(flag, 0) + 1

        alert_types = {}
        for al in all_alerts:
            evt = al.get("event", "Unknown")
            alert_types[evt] = alert_types.get(evt, 0) + 1

        summary = (
            f"Weather scan: {extreme_city_count}/{len(self._TARGET_CITIES)} cities with extreme events. "
            f"NWS severe alerts: {severe_alert_count} across {len(self._TARGET_STATES)} states. "
            f"Event types: {event_flags}. "
            f"NWS alerts: {alert_types}. "
            f"Sources: Open-Meteo (global, no auth), NOAA/NWS (US, no auth)."
        )

        return self.emit_signal(
            title="Weather Forecast Dislocation Scanner",
            summary=summary,
            confidence=confidence,
            signal_taken=signal_taken,
            degraded_reason=degraded_reason,
            platform_truth_label="PUBLIC_DATA_ONLY",
            data={
                "city_forecasts": city_forecasts,
                "extreme_events": all_extreme_events,
                "nws_severe_alerts": all_alerts,
                "extreme_city_count": extreme_city_count,
                "severe_alert_count": severe_alert_count,
                "event_type_counts": event_flags,
                "alert_type_counts": alert_types,
                "cities_scanned": self._TARGET_CITIES,
                "states_scanned": self._TARGET_STATES[:5],
                "source_status": source_status,
                "errors": errors,
                "sources": [
                    "api.open-meteo.com/v1 (no auth, global)",
                    "api.weather.gov (no auth, US NWS)",
                ],
            },
        )
