from __future__ import annotations

from typing import Any

from adapters.base_adapter import BaseAdapter


class OddsApiAdapter(BaseAdapter):
    platform_name = "oddsapi"
    mode = "PUBLIC DATA ONLY"
    live_capable = False
    execution_enabled = False
    auth_required = True
    data_truth_label = "PUBLIC DATA ONLY"
    base_url = "https://api.the-odds-api.com"

    def is_enabled(self) -> bool:
        return self._bool_setting("ENABLE_ODDSAPI", False)

    def api_key(self) -> str:
        from services import quota_budgeter
        primary = self._setting("ODDS_API_KEY", "").strip()
        backup = self._setting("ODDS_API_KEY_BACKUP", "").strip()
        if not backup:
            return primary
        # Rotate to backup when primary is exhausted
        budget = quota_budgeter.get_budget()
        snap = budget.status_all().get("oddsapi", {})
        remote_remaining = snap.get("remote_remaining")
        if remote_remaining is not None and remote_remaining <= 0:
            return backup
        return primary

    def active_key_label(self) -> str:
        """Returns 'primary' or 'backup' — for diagnostics only."""
        from services import quota_budgeter
        backup = self._setting("ODDS_API_KEY_BACKUP", "").strip()
        if backup:
            budget = quota_budgeter.get_budget()
            snap = budget.status_all().get("oddsapi", {})
            if snap.get("remote_remaining") is not None and snap.get("remote_remaining", 1) <= 0:
                return "backup"
        return "primary"

    def is_configured(self) -> bool:
        return self.is_enabled() and bool(self._setting("ODDS_API_KEY", "").strip())

    @staticmethod
    def quota_from_headers(headers: Any) -> dict[str, Any]:
        return {
            "remaining": headers.get("x-requests-remaining"),
            "used": headers.get("x-requests-used"),
            "last_cost": headers.get("x-requests-last"),
        }

    def healthcheck(self) -> dict[str, Any]:
        if not self.is_enabled():
            return self._error(
                "disabled",
                "The Odds API adapter is disabled by feature flag.",
                degraded_reason="Set ENABLE_ODDSAPI=true to enable The Odds API adapter.",
                status="disabled",
                auth_truth="missing",
            )
        if not self.api_key():
            return self._error(
                "not_configured",
                "ODDS_API_KEY is missing.",
                degraded_reason="The Odds API requires ODDS_API_KEY before requests can be made.",
                status="not_configured",
                auth_truth="missing",
            )
        try:
            response = self._request("GET", "/v4/sports/", params={"apiKey": self.api_key()})
            data = response.json()
            return self._ok(
                {
                    "sport_count": len(data) if isinstance(data, list) else 0,
                    "quota": self.quota_from_headers(response.headers),
                },
                status="ready",
                auth_truth="validated",
            )
        except Exception as exc:
            return self._error(
                "healthcheck_failed",
                f"The Odds API healthcheck failed: {exc}",
                degraded_reason="The Odds API could not validate the configured key.",
                status="degraded",
                auth_truth="failed",
            )

    def list_sports(self, *, include_all: bool = False) -> dict[str, Any]:
        if not self.is_configured():
            return self.healthcheck()
        params = {"apiKey": self.api_key()}
        if include_all:
            params["all"] = "true"
        try:
            response = self._request("GET", "/v4/sports/", params=params)
            sports = response.json()
            return self._ok(
                {"sports": sports if isinstance(sports, list) else [], "quota": self.quota_from_headers(response.headers)},
                status="ready",
                auth_truth="validated",
            )
        except Exception as exc:
            return self._error(
                "list_sports_failed",
                f"The Odds API sports request failed: {exc}",
                degraded_reason="Sports listing could not be fetched from The Odds API.",
                status="degraded",
                auth_truth="failed",
            )

    def list_markets(self, **kwargs) -> dict[str, Any]:
        if not self.is_configured():
            return self.healthcheck()
        sport = kwargs.get("sport", "upcoming")
        params = {
            "apiKey": self.api_key(),
            "regions": kwargs.get("regions", "us"),
            "markets": kwargs.get("markets", "h2h"),
            "oddsFormat": kwargs.get("oddsFormat", "decimal"),
            "dateFormat": kwargs.get("dateFormat", "iso"),
        }
        if kwargs.get("bookmakers"):
            params["bookmakers"] = kwargs["bookmakers"]
        if kwargs.get("eventIds"):
            params["eventIds"] = kwargs["eventIds"]
        try:
            response = self._request("GET", f"/v4/sports/{sport}/odds/", params=params)
            events = response.json()
            return self._ok(
                {
                    "events": events if isinstance(events, list) else [],
                    "quota": self.quota_from_headers(response.headers),
                    "historical_mode": False,
                    "historical_note": "Historical odds are paid-only and are not used in free-first mode.",
                },
                status="ready",
                auth_truth="validated",
            )
        except Exception as exc:
            return self._error(
                "list_markets_failed",
                f"The Odds API odds request failed: {exc}",
                degraded_reason="Upcoming or live odds could not be fetched from The Odds API.",
                status="degraded",
                auth_truth="failed",
            )

    def get_market(self, market_id: str, **kwargs) -> dict[str, Any]:
        if not self.is_configured():
            return self.healthcheck()
        sport = kwargs.get("sport", "upcoming")
        params = {
            "apiKey": self.api_key(),
            "regions": kwargs.get("regions", "us"),
            "markets": kwargs.get("markets", "h2h"),
            "oddsFormat": kwargs.get("oddsFormat", "decimal"),
            "dateFormat": kwargs.get("dateFormat", "iso"),
        }
        try:
            response = self._request("GET", f"/v4/sports/{sport}/events/{market_id}/odds/", params=params)
            payload = response.json()
            return self._ok(
                {"event": payload, "quota": self.quota_from_headers(response.headers)},
                status="ready",
                auth_truth="validated",
            )
        except Exception as exc:
            return self._error(
                "get_market_failed",
                f"The Odds API event request failed: {exc}",
                degraded_reason=f"The Odds API event {market_id} could not be fetched.",
                status="degraded",
                auth_truth="failed",
            )

    def get_orderbook(self, market_id: str, **kwargs) -> dict[str, Any]:
        return self._error(
            "unsupported",
            "The Odds API does not provide exchange orderbooks.",
            degraded_reason="Orderbook support is unavailable for The Odds API public odds endpoints.",
            status="unsupported",
            auth_truth="validated" if self.is_configured() else "missing",
            auth_validated=self.is_configured(),
        )

    def get_recent_trades(self, market_id: str, **kwargs) -> dict[str, Any]:
        return self._error(
            "unsupported",
            "The Odds API does not provide recent trades.",
            degraded_reason="Recent trade data is unavailable for The Odds API public odds endpoints.",
            status="unsupported",
            auth_truth="validated" if self.is_configured() else "missing",
            auth_validated=self.is_configured(),
        )

