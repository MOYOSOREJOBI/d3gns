from __future__ import annotations

from typing import Any

from adapters.base_adapter import BaseAdapter


class BallDontLieAdapter(BaseAdapter):
    """Balldontlie NBA API — no auth, real NBA stats and game data."""

    platform_name = "balldontlie"
    mode = "PUBLIC DATA ONLY"
    live_capable = False
    execution_enabled = False
    auth_required = False
    data_truth_label = "PUBLIC DATA ONLY"
    base_url = "https://api.balldontlie.io/v1"

    def is_configured(self) -> bool:
        # Free tier: no auth needed for basic endpoints
        return True

    def _headers(self) -> dict[str, str]:
        api_key = self._setting("BALLDONTLIE_API_KEY", "")
        if api_key:
            return {"Authorization": api_key}
        return {}

    def healthcheck(self) -> dict[str, Any]:
        result = self.get_teams()
        if result.get("ok"):
            return self._ok(data={"status": "ok"}, auth_truth="no_auth_required")
        return self._error("health_failed", result.get("error", "unknown"), auth_truth="no_auth_required")

    def get_teams(self) -> dict[str, Any]:
        """Fetch all NBA teams."""
        try:
            r = self._request("GET", "/teams", headers=self._headers())
            raw = r.json()
            teams = [
                {
                    "id": t.get("id"),
                    "name": t.get("full_name"),
                    "abbreviation": t.get("abbreviation"),
                    "city": t.get("city"),
                    "conference": t.get("conference"),
                    "division": t.get("division"),
                }
                for t in raw.get("data", [])
            ]
            return self._ok(data={"teams": teams, "count": len(teams)}, status="ok", auth_truth="no_auth_required")
        except Exception as exc:
            return self._error("teams_failed", str(exc), auth_truth="no_auth_required")

    def get_games(self, season: int = 2024, team_ids: list[int] | None = None, per_page: int = 25) -> dict[str, Any]:
        """Fetch recent NBA games, optionally filtered by team."""
        params: dict[str, Any] = {"seasons[]": season, "per_page": per_page}
        if team_ids:
            params["team_ids[]"] = team_ids
        try:
            r = self._request("GET", "/games", params=params, headers=self._headers())
            raw = r.json()
            games = [
                {
                    "id": g.get("id"),
                    "date": g.get("date"),
                    "home_team": g.get("home_team", {}).get("full_name"),
                    "home_team_score": g.get("home_team_score"),
                    "visitor_team": g.get("visitor_team", {}).get("full_name"),
                    "visitor_team_score": g.get("visitor_team_score"),
                    "status": g.get("status"),
                    "period": g.get("period"),
                    "time": g.get("time"),
                }
                for g in raw.get("data", [])
            ]
            return self._ok(
                data={"games": games, "count": len(games), "season": season},
                status="ok",
                auth_truth="no_auth_required",
            )
        except Exception as exc:
            return self._error("games_failed", str(exc), auth_truth="no_auth_required")

    def get_player_stats(self, player_ids: list[int] | None = None, season: int = 2024, per_page: int = 25) -> dict[str, Any]:
        """Fetch season average stats for players."""
        params: dict[str, Any] = {"season": season, "per_page": per_page}
        if player_ids:
            params["player_ids[]"] = player_ids
        try:
            r = self._request("GET", "/season_averages", params=params, headers=self._headers())
            raw = r.json()
            return self._ok(data={"stats": raw.get("data", []), "season": season}, status="ok", auth_truth="no_auth_required")
        except Exception as exc:
            return self._error("stats_failed", str(exc), auth_truth="no_auth_required")

    def get_standings_signal(self, season: int = 2024) -> dict[str, Any]:
        """
        Derive momentum signal: teams with winning streaks vs. prediction market odds.
        Returns top home-win-streak teams as potential value in NBA betting markets.
        """
        games_res = self.get_games(season=season, per_page=100)
        if not games_res.get("ok"):
            return games_res
        games = games_res["data"].get("games", [])
        # Count recent wins per team from completed games
        team_record: dict[str, dict] = {}
        for g in games:
            if g.get("status") != "Final":
                continue
            hs = g.get("home_team_score") or 0
            vs = g.get("visitor_team_score") or 0
            home = g.get("home_team") or "Unknown"
            visitor = g.get("visitor_team") or "Unknown"
            for team, won in [(home, hs > vs), (visitor, vs > hs)]:
                if team not in team_record:
                    team_record[team] = {"wins": 0, "losses": 0, "games": 0}
                team_record[team]["wins" if won else "losses"] += 1
                team_record[team]["games"] += 1
        standings = [
            {
                "team": t,
                "wins": d["wins"],
                "losses": d["losses"],
                "games": d["games"],
                "win_pct": round(d["wins"] / d["games"], 3) if d["games"] > 0 else 0,
            }
            for t, d in team_record.items()
        ]
        standings.sort(key=lambda x: x["win_pct"], reverse=True)
        return self._ok(
            data={
                "standings": standings[:15],
                "top_team": standings[0]["team"] if standings else None,
                "season": season,
                "games_analyzed": len(games),
            },
            status="ok",
            auth_truth="no_auth_required",
        )
