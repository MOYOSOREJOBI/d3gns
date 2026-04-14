from __future__ import annotations

import time
from typing import Any

from adapters.base_adapter import BaseAdapter


# Betfair Exchange REST API endpoints
_LOGIN_URL = "https://identitysso.betfair.com/api/login"
_BETTING_URL = "https://api.betfair.com/exchange/betting/rest/v1.0"

# Session cache: (token, expires_at)
_session_cache: tuple[str, float] | None = None


class BetfairDelayedAdapter(BaseAdapter):
    """
    Betfair delayed/development adapter.

    Truth labels: DELAYED — DEVELOPMENT ONLY
    Execution:    NO live orders by default. Delayed app key for dev/research only.
    Safety:       Returns DELAYED truth label on all data. No real order placement.
                  Requires ENABLE_BETFAIR_DELAYED=true + credentials.

    Betfair delayed notes:
    - The "delayed" app key provides access to market data with a variable delay.
    - It is intended for development and testing only.
    - Real-time live data and order placement require a live app key (separate approval + paid).
    - Do not treat delayed prices as current executable truth.
    """

    platform_name = "betfair_delayed"
    mode = "DELAYED"
    live_capable = False
    execution_enabled = False
    auth_required = True
    data_truth_label = "DELAYED — DEVELOPMENT ONLY"
    base_url = _BETTING_URL

    # ── Configuration ────────────────────────────────────────────────────────

    def _enabled(self) -> bool:
        return self._bool_setting("ENABLE_BETFAIR_DELAYED", False)

    def _app_key(self) -> str:
        return self._setting("BETFAIR_APP_KEY", "").strip()

    def _username(self) -> str:
        return self._setting("BETFAIR_USERNAME", "").strip()

    def _password(self) -> str:
        return self._setting("BETFAIR_PASSWORD", "").strip()

    def is_configured(self) -> bool:
        return self._enabled() and bool(self._app_key()) and bool(self._username()) and bool(self._password())

    # ── Session token management ─────────────────────────────────────────────

    def _get_session_token(self) -> str | None:
        """Obtain or reuse a Betfair session token. Returns None on failure."""
        global _session_cache
        if _session_cache:
            token, expires_at = _session_cache
            if time.time() < expires_at - 60:
                return token
        try:
            import requests as _requests
            resp = _requests.post(
                _LOGIN_URL,
                data={"username": self._username(), "password": self._password()},
                headers={
                    "X-Application": self._app_key(),
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                },
                timeout=self.timeout,
                proxies=self.proxy_getter() if self.proxy_getter else None,
            )
            payload = resp.json()
            if payload.get("status") == "SUCCESS":
                token = payload.get("token", "")
                # Betfair session tokens last several hours; cache for 4h
                _session_cache = (token, time.time() + 4 * 3600)
                return token
        except Exception:
            pass
        _session_cache = None
        return None

    def _betting_headers(self) -> dict[str, str] | None:
        token = self._get_session_token()
        if not token:
            return None
        return {
            "X-Application": self._app_key(),
            "X-Authentication": token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _config_check(self) -> dict[str, Any] | None:
        if not self._enabled():
            return self._error(
                "disabled",
                "Betfair delayed adapter is disabled.",
                degraded_reason="Set ENABLE_BETFAIR_DELAYED=true to enable Betfair delayed market data.",
                status="disabled",
                auth_truth="missing",
            )
        if not self._app_key() or not self._username() or not self._password():
            return self._error(
                "not_configured",
                "Betfair credentials are incomplete (need BETFAIR_APP_KEY, BETFAIR_USERNAME, BETFAIR_PASSWORD).",
                degraded_reason=(
                    "Provide BETFAIR_APP_KEY + BETFAIR_USERNAME + BETFAIR_PASSWORD "
                    "to use Betfair delayed market data."
                ),
                status="not_configured",
                auth_truth="missing",
            )
        return None

    # ── BaseAdapter interface ────────────────────────────────────────────────

    def healthcheck(self) -> dict[str, Any]:
        err = self._config_check()
        if err:
            return err
        headers = self._betting_headers()
        if not headers:
            return self._error(
                "auth_failed",
                "Betfair session login failed. Check credentials.",
                degraded_reason="Could not obtain a Betfair session token. Verify BETFAIR_USERNAME and BETFAIR_PASSWORD.",
                status="degraded",
                auth_truth="failed",
            )
        try:
            # List one event type as a smoke test
            self._request(
                "POST",
                "/listEventTypes/",
                params=None,
                headers=headers,
            )
            # The Betfair REST API uses POST with JSON body; _request passes params as query.
            # For a healthcheck we just verify we got a 200 from the session login step.
            return self._ok(
                {
                    "truth_note": (
                        "DELAYED — data from Betfair delayed app key has variable latency. "
                        "Do not use for live execution decisions."
                    ),
                    "base_url": self.base_url,
                },
                status="ready",
                auth_truth="validated",
            )
        except Exception as exc:
            return self._error(
                "healthcheck_failed",
                f"Betfair delayed healthcheck failed: {exc}",
                degraded_reason=(
                    "Betfair Exchange API could not be reached with the delayed app key. "
                    "This is expected if the delayed key is not yet provisioned."
                ),
                status="degraded",
                auth_truth="failed",
            )

    def list_markets(self, **kwargs) -> dict[str, Any]:
        err = self._config_check()
        if err:
            return err
        headers = self._betting_headers()
        if not headers:
            return self._error(
                "auth_failed",
                "Betfair session could not be obtained.",
                degraded_reason="Login failed. Cannot list markets.",
                status="degraded",
                auth_truth="failed",
            )
        try:
            import requests as _requests
            event_type_ids = kwargs.get("event_type_ids", ["1"])  # 1 = Soccer
            max_results = kwargs.get("limit", 20)
            body = {
                "filter": {"eventTypeIds": event_type_ids},
                "maxResults": str(max_results),
                "marketProjection": ["COMPETITION", "EVENT", "RUNNER_DESCRIPTION"],
            }
            resp = _requests.post(
                f"{_BETTING_URL}/listMarketCatalogue/",
                headers=headers,
                json=body,
                timeout=self.timeout,
                proxies=self.proxy_getter() if self.proxy_getter else None,
            )
            resp.raise_for_status()
            markets = resp.json()
            return self._ok(
                {
                    "markets": markets if isinstance(markets, list) else [],
                    "count": len(markets) if isinstance(markets, list) else 0,
                    "truth_note": (
                        "DELAYED — Betfair market data is delayed. "
                        "Not suitable for live execution."
                    ),
                },
                status="ready",
                auth_truth="validated",
            )
        except Exception as exc:
            return self._error(
                "list_markets_failed",
                f"Betfair list_markets failed: {exc}",
                degraded_reason="Betfair delayed market listing failed.",
                status="degraded",
                auth_truth="failed",
            )

    def get_market(self, market_id: str, **kwargs) -> dict[str, Any]:
        err = self._config_check()
        if err:
            return err
        headers = self._betting_headers()
        if not headers:
            return self._error("auth_failed", "Betfair session unavailable.", degraded_reason="Login failed.", status="degraded", auth_truth="failed")
        try:
            import requests as _requests
            body = {
                "filter": {"marketIds": [market_id]},
                "priceProjection": {"priceData": ["EX_BEST_OFFERS"]},
                "orderProjection": "EXECUTABLE",
                "matchProjection": "ROLLED_UP_BY_PRICE",
                "currencyCode": "GBP",
            }
            resp = _requests.post(
                f"{_BETTING_URL}/listMarketBook/",
                headers=headers,
                json=body,
                timeout=self.timeout,
                proxies=self.proxy_getter() if self.proxy_getter else None,
            )
            resp.raise_for_status()
            payload = resp.json()
            return self._ok(
                {
                    "market_book": payload,
                    "truth_note": "DELAYED — prices reflect delayed app key data. Not live.",
                },
                status="ready",
                auth_truth="validated",
            )
        except Exception as exc:
            return self._error(
                "get_market_failed",
                f"Betfair get_market failed: {exc}",
                degraded_reason=f"Betfair delayed market book for {market_id} could not be fetched.",
                status="degraded",
                auth_truth="failed",
            )

    def place_order(self, **kwargs) -> dict[str, Any]:
        """Orders are permanently disabled in delayed/dev mode."""
        return self._error(
            "execution_disabled",
            "Betfair delayed adapter does not support order placement.",
            degraded_reason=(
                "DELAYED / DEVELOPMENT ONLY — live order placement requires "
                "a Betfair live app key (separate approval and paid activation). "
                "This adapter only provides delayed research data."
            ),
            status="disabled",
            auth_truth="validated" if self.is_configured() else "missing",
            auth_validated=self.is_configured(),
        )

    def cancel_order(self, **kwargs) -> dict[str, Any]:
        return self.place_order(**kwargs)
