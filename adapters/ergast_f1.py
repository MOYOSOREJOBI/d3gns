from __future__ import annotations

from typing import Any

from adapters.base_adapter import BaseAdapter


class ErgastF1Adapter(BaseAdapter):
    """Ergast F1 API — no auth, complete F1 race data from 1950 to present."""

    platform_name = "ergast_f1"
    mode = "PUBLIC DATA ONLY"
    live_capable = False
    execution_enabled = False
    auth_required = False
    data_truth_label = "PUBLIC DATA ONLY"
    base_url = "https://ergast.com/api/f1"

    def is_configured(self) -> bool:
        return True

    def healthcheck(self) -> dict[str, Any]:
        result = self.get_current_season()
        if result.get("ok"):
            return self._ok(data={"status": "ok"}, auth_truth="no_auth_required")
        return self._error("health_failed", result.get("error", "unknown"), auth_truth="no_auth_required")

    def get_current_season(self) -> dict[str, Any]:
        """Fetch the race schedule for the current season."""
        try:
            r = self._request("GET", "/current.json")
            raw = r.json()
            races = raw.get("MRData", {}).get("RaceTable", {}).get("Races", [])
            schedule = [
                {
                    "round": int(race.get("round", 0)),
                    "race_name": race.get("raceName"),
                    "circuit": race.get("Circuit", {}).get("circuitName"),
                    "location": f"{race.get('Circuit', {}).get('Location', {}).get('locality')}, {race.get('Circuit', {}).get('Location', {}).get('country')}",
                    "date": race.get("date"),
                    "time": race.get("time"),
                    "qualifying_date": race.get("Qualifying", {}).get("date"),
                    "sprint_date": race.get("Sprint", {}).get("date"),
                }
                for race in races
            ]
            return self._ok(
                data={"season": raw.get("MRData", {}).get("RaceTable", {}).get("season"), "races": schedule, "total": len(schedule)},
                status="ok",
                auth_truth="no_auth_required",
            )
        except Exception as exc:
            return self._error("season_failed", str(exc), auth_truth="no_auth_required")

    def get_driver_standings(self, season: str = "current") -> dict[str, Any]:
        """Fetch current driver championship standings."""
        try:
            r = self._request("GET", f"/{season}/driverStandings.json")
            raw = r.json()
            standings_list = (
                raw.get("MRData", {})
                .get("StandingsTable", {})
                .get("StandingsLists", [{}])[0]
                .get("DriverStandings", [])
            )
            standings = [
                {
                    "position": int(s.get("position", 0)),
                    "driver": f"{s.get('Driver', {}).get('givenName')} {s.get('Driver', {}).get('familyName')}",
                    "driver_id": s.get("Driver", {}).get("driverId"),
                    "nationality": s.get("Driver", {}).get("nationality"),
                    "constructor": s.get("Constructors", [{}])[0].get("name") if s.get("Constructors") else None,
                    "points": float(s.get("points", 0)),
                    "wins": int(s.get("wins", 0)),
                }
                for s in standings_list
            ]
            return self._ok(
                data={"season": season, "standings": standings},
                status="ok",
                auth_truth="no_auth_required",
            )
        except Exception as exc:
            return self._error("standings_failed", str(exc), auth_truth="no_auth_required")

    def get_constructor_standings(self, season: str = "current") -> dict[str, Any]:
        """Fetch constructor (team) championship standings."""
        try:
            r = self._request("GET", f"/{season}/constructorStandings.json")
            raw = r.json()
            standings_list = (
                raw.get("MRData", {})
                .get("StandingsTable", {})
                .get("StandingsLists", [{}])[0]
                .get("ConstructorStandings", [])
            )
            standings = [
                {
                    "position": int(s.get("position", 0)),
                    "constructor": s.get("Constructor", {}).get("name"),
                    "constructor_id": s.get("Constructor", {}).get("constructorId"),
                    "nationality": s.get("Constructor", {}).get("nationality"),
                    "points": float(s.get("points", 0)),
                    "wins": int(s.get("wins", 0)),
                }
                for s in standings_list
            ]
            return self._ok(
                data={"season": season, "constructor_standings": standings},
                status="ok",
                auth_truth="no_auth_required",
            )
        except Exception as exc:
            return self._error("constructor_standings_failed", str(exc), auth_truth="no_auth_required")

    def get_last_race_results(self) -> dict[str, Any]:
        """Fetch results from the most recently completed race."""
        try:
            r = self._request("GET", "/current/last/results.json")
            raw = r.json()
            race_table = raw.get("MRData", {}).get("RaceTable", {})
            races = race_table.get("Races", [])
            if not races:
                return self._error("no_race", "No recent race found.", auth_truth="no_auth_required")
            race = races[0]
            results = [
                {
                    "position": int(r2.get("position", 0)),
                    "driver": f"{r2.get('Driver', {}).get('givenName')} {r2.get('Driver', {}).get('familyName')}",
                    "constructor": r2.get("Constructor", {}).get("name"),
                    "grid": int(r2.get("grid", 0)),
                    "laps": int(r2.get("laps", 0)),
                    "status": r2.get("status"),
                    "points": float(r2.get("points", 0)),
                    "fastest_lap_rank": r2.get("FastestLap", {}).get("rank"),
                }
                for r2 in race.get("Results", [])
            ]
            return self._ok(
                data={
                    "race_name": race.get("raceName"),
                    "circuit": race.get("Circuit", {}).get("circuitName"),
                    "date": race.get("date"),
                    "round": race.get("round"),
                    "results": results,
                },
                status="ok",
                auth_truth="no_auth_required",
            )
        except Exception as exc:
            return self._error("last_race_failed", str(exc), auth_truth="no_auth_required")

    def get_next_race(self) -> dict[str, Any]:
        """Get the next upcoming race on the calendar."""
        season_res = self.get_current_season()
        if not season_res.get("ok"):
            return season_res
        import datetime
        today = datetime.date.today().isoformat()
        upcoming = [r for r in season_res["data"].get("races", []) if (r.get("date") or "") >= today]
        if not upcoming:
            return self._error("no_upcoming", "No upcoming races found.", auth_truth="no_auth_required")
        next_race = upcoming[0]
        return self._ok(data={"next_race": next_race}, status="ok", auth_truth="no_auth_required")
