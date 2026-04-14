from __future__ import annotations

from typing import Any

from adapters.base_adapter import BaseAdapter


# SportsDataIO v3 base (sport-specific paths appended at call time)
_BASE_URL = "https://api.sportsdata.io/v3"

_TRIAL_DISCLAIMER = (
    "TRIAL / SCRAMBLED DATA — SportsDataIO free trial data may be scrambled "
    "or synthetic. Do not use for live execution or treat as real tradable prices. "
    "Research and study purposes only."
)


class SportsDataIoTrialAdapter(BaseAdapter):
    """
    SportsDataIO free trial adapter.

    Truth labels: TRIAL — SCRAMBLED DATA / RESEARCH ONLY
    Execution:    NEVER. Trial data must not drive any order placement.
    Safety:       All responses include a prominent disclaimer. Data may be
                  scrambled or synthetic. Use only for research/study.

    SportsDataIO trial notes:
    - Free trial keys give access to endpoints but some data is scrambled.
    - Historical endpoints and closing-line data may require a paid plan.
    - Never present trial odds as real tradable truth.
    """

    platform_name = "sportsdataio_trial"
    mode = "TRIAL"
    live_capable = False
    execution_enabled = False
    auth_required = True
    data_truth_label = "TRIAL — SCRAMBLED DATA"
    base_url = _BASE_URL

    # ── Configuration ────────────────────────────────────────────────────────

    def _enabled(self) -> bool:
        return self._bool_setting("ENABLE_SPORTSDATAIO_TRIAL", False)

    def _api_key(self) -> str:
        return self._setting("SPORTSDATAIO_API_KEY", "").strip()

    def is_configured(self) -> bool:
        return self._enabled() and bool(self._api_key())

    def _config_check(self) -> dict[str, Any] | None:
        if not self._enabled():
            return self._error(
                "disabled",
                "SportsDataIO trial adapter is disabled.",
                degraded_reason="Set ENABLE_SPORTSDATAIO_TRIAL=true to enable SportsDataIO trial data.",
                status="disabled",
                auth_truth="missing",
            )
        if not self._api_key():
            return self._error(
                "not_configured",
                "SPORTSDATAIO_API_KEY is missing.",
                degraded_reason="Provide SPORTSDATAIO_API_KEY to access SportsDataIO trial data.",
                status="not_configured",
                auth_truth="missing",
            )
        return None

    def _subscription_key_header(self) -> dict[str, str]:
        return {"Ocp-Apim-Subscription-Key": self._api_key()}

    # ── BaseAdapter interface ────────────────────────────────────────────────

    def healthcheck(self) -> dict[str, Any]:
        err = self._config_check()
        if err:
            return err
        try:
            # Smoke test: fetch NFL scores (a common free endpoint)
            resp = self._request(
                "GET",
                "/nfl/scores/json/Scores/2024REG1",
                headers=self._subscription_key_header(),
                base_url=_BASE_URL,
            )
            payload = resp.json()
            count = len(payload) if isinstance(payload, list) else 1
            return self._ok(
                {
                    "sample_count": count,
                    "truth_note": _TRIAL_DISCLAIMER,
                    "base_url": self.base_url,
                },
                status="ready",
                auth_truth="validated",
            )
        except Exception as exc:
            return self._error(
                "healthcheck_failed",
                f"SportsDataIO trial healthcheck failed: {exc}",
                degraded_reason=(
                    "SportsDataIO API could not be reached. "
                    "Verify SPORTSDATAIO_API_KEY and trial entitlements."
                ),
                status="degraded",
                auth_truth="failed",
            )

    def list_markets(self, **kwargs) -> dict[str, Any]:
        """
        For SportsDataIO, 'list_markets' returns available sports/games for a sport+season.
        sport: "nfl" | "nba" | "mlb" | "nhl" (default: nfl)
        season: e.g. "2024REG" (default)
        week: e.g. 1 (optional, for NFL)
        """
        err = self._config_check()
        if err:
            return err
        sport = kwargs.get("sport", "nfl").lower()
        season = kwargs.get("season", "2024REG")
        week = kwargs.get("week", "1")
        try:
            resp = self._request(
                "GET",
                f"/{sport}/scores/json/Scores/{season}{week}",
                headers=self._subscription_key_header(),
                base_url=_BASE_URL,
            )
            payload = resp.json()
            games = payload if isinstance(payload, list) else [payload]
            return self._ok(
                {
                    "games": games,
                    "count": len(games),
                    "sport": sport,
                    "season": season,
                    "week": week,
                    "truth_note": _TRIAL_DISCLAIMER,
                    "scrambled_data_warning": True,
                },
                status="ready",
                auth_truth="validated",
            )
        except Exception as exc:
            return self._error(
                "list_markets_failed",
                f"SportsDataIO games request failed: {exc}",
                degraded_reason=(
                    "Could not fetch SportsDataIO game data. "
                    "Check sport/season parameters and trial key entitlements."
                ),
                status="degraded",
                auth_truth="failed",
            )

    def get_market(self, market_id: str, **kwargs) -> dict[str, Any]:
        """
        market_id is treated as a game ID. Returns betting odds for a specific game.
        sport: "nfl" | "nba" | etc.
        """
        err = self._config_check()
        if err:
            return err
        sport = kwargs.get("sport", "nfl").lower()
        try:
            resp = self._request(
                "GET",
                f"/{sport}/odds/json/GameOddsByGameID/{market_id}",
                headers=self._subscription_key_header(),
                base_url=_BASE_URL,
            )
            payload = resp.json()
            return self._ok(
                {
                    "game_odds": payload,
                    "game_id": market_id,
                    "truth_note": _TRIAL_DISCLAIMER,
                    "scrambled_data_warning": True,
                },
                status="ready",
                auth_truth="validated",
            )
        except Exception as exc:
            return self._error(
                "get_market_failed",
                f"SportsDataIO game odds request failed: {exc}",
                degraded_reason=(
                    f"Game odds for {market_id} could not be fetched from SportsDataIO. "
                    "Odds endpoints may require a paid plan beyond the free trial."
                ),
                status="degraded",
                auth_truth="failed",
            )

    def get_line_movement(self, game_id: str, **kwargs) -> dict[str, Any]:
        """
        Fetch line movement data for research. Returns opening + current lines where available.
        Always marked TRIAL / SCRAMBLED.
        """
        err = self._config_check()
        if err:
            return err
        sport = kwargs.get("sport", "nfl").lower()
        try:
            resp = self._request(
                "GET",
                f"/{sport}/odds/json/BettingMarketsByGameID/{game_id}",
                headers=self._subscription_key_header(),
                base_url=_BASE_URL,
            )
            payload = resp.json()
            return self._ok(
                {
                    "betting_markets": payload if isinstance(payload, list) else [payload],
                    "game_id": game_id,
                    "truth_note": _TRIAL_DISCLAIMER,
                    "scrambled_data_warning": True,
                    "historical_note": (
                        "Historical line movement requires a paid SportsDataIO plan. "
                        "Trial data is current-state only and may be scrambled."
                    ),
                },
                status="ready",
                auth_truth="validated",
            )
        except Exception as exc:
            return self._error(
                "get_line_movement_failed",
                f"SportsDataIO line movement fetch failed: {exc}",
                degraded_reason=(
                    "Line movement data may require a paid SportsDataIO plan. "
                    "Free trial has limited access to odds endpoints."
                ),
                status="degraded",
                auth_truth="failed",
            )

    def place_order(self, **kwargs) -> dict[str, Any]:
        """SportsDataIO is a data provider. Order placement is never supported."""
        return self._error(
            "unsupported",
            "SportsDataIO is a data provider. Order placement is not supported.",
            degraded_reason="RESEARCH ONLY — SportsDataIO provides data, not execution.",
            status="unsupported",
            auth_truth="validated" if self.is_configured() else "missing",
            auth_validated=self.is_configured(),
        )
