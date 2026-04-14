from __future__ import annotations

from typing import Any

from adapters.base_adapter import BaseAdapter


class TheSportsDBAdapter(BaseAdapter):
    """
    TheSportsDB — free tier (no auth), multi-sport data.
    Covers NFL, NBA, MLB, NHL, Premier League, La Liga, UEFA, MMA, Rugby, Golf, Tennis.
    """

    platform_name = "thesportsdb"
    mode = "PUBLIC DATA ONLY"
    live_capable = False
    execution_enabled = False
    auth_required = False
    data_truth_label = "PUBLIC DATA ONLY"
    # Free API key = "3" (test key, public)
    base_url = "https://www.thesportsdb.com/api/v1/json/3"

    # League IDs for major leagues
    LEAGUES = {
        "nfl":            "4391",
        "nba":            "4387",
        "mlb":            "4424",
        "nhl":            "4380",
        "premier_league": "4328",
        "la_liga":        "4335",
        "bundesliga":     "4331",
        "serie_a":        "4332",
        "ligue_1":        "4334",
        "mls":            "4346",
        "formula1":       "4370",
        "mma_ufc":        "4443",
        "tennis_atp":     "4429",
        "golf_pga":       "4439",
    }

    def is_configured(self) -> bool:
        return True

    def healthcheck(self) -> dict[str, Any]:
        res = self.get_next_events(league="nfl", limit=2)
        if res.get("ok"):
            return self._ok(data={"status": "ok"}, auth_truth="no_auth_required")
        return self._error("health_failed", res.get("error", "unknown"), auth_truth="no_auth_required")

    def get_next_events(self, league: str = "nfl", limit: int = 10) -> dict[str, Any]:
        """Fetch next upcoming events for a league."""
        league_id = self.LEAGUES.get(league.lower(), league)
        try:
            r = self._request("GET", f"/eventsnextleague.php", params={"id": league_id})
            raw = r.json()
            events = raw.get("events") or []
            result = [
                {
                    "id": e.get("idEvent"),
                    "name": e.get("strEvent"),
                    "home_team": e.get("strHomeTeam"),
                    "away_team": e.get("strAwayTeam"),
                    "date": e.get("dateEvent"),
                    "time": e.get("strTime"),
                    "venue": e.get("strVenue"),
                    "league": e.get("strLeague"),
                    "season": e.get("strSeason"),
                    "round": e.get("intRound"),
                    "tv": e.get("strTvStation"),
                }
                for e in events[:limit]
            ]
            return self._ok(
                data={"league": league, "events": result, "count": len(result)},
                status="ok", auth_truth="no_auth_required",
            )
        except Exception as exc:
            return self._error("events_failed", str(exc), auth_truth="no_auth_required")

    def get_last_events(self, league: str = "nfl", limit: int = 10) -> dict[str, Any]:
        """Fetch last completed events for a league."""
        league_id = self.LEAGUES.get(league.lower(), league)
        try:
            r = self._request("GET", "/eventspastleague.php", params={"id": league_id})
            raw = r.json()
            events = raw.get("events") or []
            result = [
                {
                    "id": e.get("idEvent"),
                    "name": e.get("strEvent"),
                    "home_team": e.get("strHomeTeam"),
                    "away_team": e.get("strAwayTeam"),
                    "home_score": e.get("intHomeScore"),
                    "away_score": e.get("intAwayScore"),
                    "date": e.get("dateEvent"),
                    "venue": e.get("strVenue"),
                    "league": e.get("strLeague"),
                    "season": e.get("strSeason"),
                    "result": "home_win" if (int(e.get("intHomeScore") or -1) > int(e.get("intAwayScore") or -1)) else
                              "away_win" if (int(e.get("intHomeScore") or -1) < int(e.get("intAwayScore") or -1)) else
                              "draw" if (e.get("intHomeScore") is not None) else "unknown",
                }
                for e in events[:limit]
            ]
            return self._ok(
                data={"league": league, "events": result, "count": len(result)},
                status="ok", auth_truth="no_auth_required",
            )
        except Exception as exc:
            return self._error("last_events_failed", str(exc), auth_truth="no_auth_required")

    def get_league_table(self, league: str = "premier_league", season: str = "2024-2025") -> dict[str, Any]:
        """Fetch current standings/table for a league."""
        league_id = self.LEAGUES.get(league.lower(), league)
        try:
            r = self._request("GET", "/lookuptable.php", params={"l": league_id, "s": season})
            raw = r.json()
            table = raw.get("table") or []
            standings = [
                {
                    "rank": t.get("intRank"),
                    "team": t.get("strTeam"),
                    "played": t.get("intPlayed"),
                    "wins": t.get("intWin"),
                    "draws": t.get("intDraw"),
                    "losses": t.get("intLoss"),
                    "goals_for": t.get("intGoalsFor"),
                    "goals_against": t.get("intGoalsAgainst"),
                    "goal_diff": t.get("intGoalDifference"),
                    "points": t.get("intPoints"),
                    "form": t.get("strForm"),
                    "description": t.get("strDescription"),
                }
                for t in table
            ]
            return self._ok(
                data={"league": league, "season": season, "standings": standings, "count": len(standings)},
                status="ok", auth_truth="no_auth_required",
            )
        except Exception as exc:
            return self._error("table_failed", str(exc), auth_truth="no_auth_required")

    def search_team(self, team_name: str) -> dict[str, Any]:
        """Search for a team by name."""
        try:
            r = self._request("GET", "/searchteams.php", params={"t": team_name})
            raw = r.json()
            teams = raw.get("teams") or []
            result = [
                {
                    "id": t.get("idTeam"),
                    "name": t.get("strTeam"),
                    "sport": t.get("strSport"),
                    "league": t.get("strLeague"),
                    "country": t.get("strCountry"),
                    "stadium": t.get("strStadium"),
                    "formed_year": t.get("intFormedYear"),
                    "description": (t.get("strDescriptionEN") or "")[:200],
                    "website": t.get("strWebsite"),
                }
                for t in teams[:5]
            ]
            return self._ok(
                data={"query": team_name, "teams": result, "count": len(result)},
                status="ok", auth_truth="no_auth_required",
            )
        except Exception as exc:
            return self._error("search_failed", str(exc), auth_truth="no_auth_required")

    def get_team_last_events(self, team_id: str, limit: int = 10) -> dict[str, Any]:
        """Fetch last events for a specific team."""
        try:
            r = self._request("GET", "/eventslast.php", params={"id": team_id})
            raw = r.json()
            events = raw.get("results") or []
            result = [
                {
                    "name": e.get("strEvent"),
                    "home_team": e.get("strHomeTeam"),
                    "away_team": e.get("strAwayTeam"),
                    "home_score": e.get("intHomeScore"),
                    "away_score": e.get("intAwayScore"),
                    "date": e.get("dateEvent"),
                    "league": e.get("strLeague"),
                }
                for e in events[:limit]
            ]
            # Compute form (W/D/L from perspective of searched team)
            form = []
            for e in result:
                hs = int(e.get("home_score") or -1)
                as_ = int(e.get("away_score") or -1)
                if hs < 0:
                    continue
                if hs > as_:
                    form.append("W")
                elif hs < as_:
                    form.append("L")
                else:
                    form.append("D")
            return self._ok(
                data={"team_id": team_id, "events": result, "count": len(result), "form": "".join(form[:5])},
                status="ok", auth_truth="no_auth_required",
            )
        except Exception as exc:
            return self._error("team_events_failed", str(exc), auth_truth="no_auth_required")

    def get_multi_sport_snapshot(self) -> dict[str, Any]:
        """Quick snapshot of next events across major sports for prediction market scanning."""
        sports = ["nfl", "nba", "premier_league", "formula1"]
        snapshot = {}
        for sport in sports:
            res = self.get_next_events(league=sport, limit=3)
            if res.get("ok"):
                events = res["data"].get("events", [])
                snapshot[sport] = {
                    "next_events": [
                        f"{e.get('home_team')} vs {e.get('away_team')} on {e.get('date')}"
                        for e in events[:3]
                    ],
                    "event_count": len(events),
                }
            else:
                snapshot[sport] = {"error": res.get("error")}
        return self._ok(
            data={"snapshot": snapshot, "sports_scanned": len(sports)},
            status="ok", auth_truth="no_auth_required",
        )
