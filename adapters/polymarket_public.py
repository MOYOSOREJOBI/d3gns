from __future__ import annotations

from typing import Any

from adapters.base_adapter import BaseAdapter


_GAMMA_BASE = "https://gamma-api.polymarket.com"
_CLOB_BASE = "https://clob.polymarket.com"

_PUBLIC_DATA_NOTE = (
    "PUBLIC DATA ONLY — Polymarket public market data. "
    "No authentication required. No order placement."
)


class PolymarketPublicAdapter(BaseAdapter):
    """
    Polymarket public data adapter.

    Wraps Polymarket's unauthenticated Gamma and CLOB public endpoints.
    This adapter explicitly does NOT wrap the authenticated CLOB trading client.
    Existing paper_polymarket.py and live Polymarket bot logic is preserved
    and lives in the orchestrator/bots separately.

    Truth labels: PUBLIC DATA ONLY / PAPER
    Execution:    NEVER. Public data only.
    Safety:       No credentials required or used. Fails gracefully when API unreachable.
    """

    platform_name = "polymarket_public"
    mode = "PUBLIC DATA ONLY"
    live_capable = False
    execution_enabled = False
    auth_required = False
    data_truth_label = "PUBLIC DATA ONLY"
    base_url = _GAMMA_BASE

    # ── Configuration ────────────────────────────────────────────────────────

    def _enabled(self) -> bool:
        # Polymarket public data has no required credentials.
        # Feature-flagged via ENABLE_KALSHI (shared flag) or standalone.
        # We check for a dedicated env or fall back to always-on if no flag set.
        env = self._setting("ENABLE_POLYMARKET_PUBLIC", "").strip().lower()
        if env in ("false", "0", "no", "off"):
            return False
        return True  # on by default since no credentials needed

    def is_configured(self) -> bool:
        return self._enabled()

    # ── Internal helpers ─────────────────────────────────────────────────────

    def _config_check(self) -> dict[str, Any] | None:
        if not self._enabled():
            return self._error(
                "disabled",
                "Polymarket public adapter is disabled. Set ENABLE_POLYMARKET_PUBLIC=true.",
                degraded_reason="Set ENABLE_POLYMARKET_PUBLIC=true to re-enable.",
                status="disabled",
                auth_truth="missing",
            )
        return None

    # ── BaseAdapter interface ────────────────────────────────────────────────

    def healthcheck(self) -> dict[str, Any]:
        err = self._config_check()
        if err:
            return err
        try:
            resp = self._request("GET", "/markets", params={"limit": 1}, base_url=_GAMMA_BASE)
            payload = resp.json()
            markets = payload if isinstance(payload, list) else payload.get("markets", [])
            return self._ok(
                {
                    "sample_count": len(markets) if isinstance(markets, list) else 0,
                    "gamma_base_url": _GAMMA_BASE,
                    "truth_note": _PUBLIC_DATA_NOTE,
                },
                status="ready",
                auth_truth="validated",
            )
        except Exception as exc:
            return self._error(
                "healthcheck_failed",
                f"Polymarket public healthcheck failed: {exc}",
                degraded_reason="Polymarket Gamma API could not be reached.",
                status="degraded",
                auth_truth="failed",
            )

    def list_markets(self, **kwargs) -> dict[str, Any]:
        err = self._config_check()
        if err:
            return err
        params: dict[str, Any] = {
            "limit": kwargs.get("limit", 25),
            "active": kwargs.get("active", "true"),
            "closed": kwargs.get("closed", "false"),
        }
        if kwargs.get("tag_slug"):
            params["tag_slug"] = kwargs["tag_slug"]
        if kwargs.get("order"):
            params["order"] = kwargs["order"]
        if kwargs.get("ascending") is not None:
            params["ascending"] = str(kwargs["ascending"]).lower()
        try:
            resp = self._request("GET", "/markets", params=params, base_url=_GAMMA_BASE)
            payload = resp.json()
            markets = payload if isinstance(payload, list) else payload.get("markets", [])
            return self._ok(
                {
                    "markets": markets if isinstance(markets, list) else [],
                    "count": len(markets) if isinstance(markets, list) else 0,
                    "truth_note": _PUBLIC_DATA_NOTE,
                },
                status="ready",
                auth_truth="validated",
            )
        except Exception as exc:
            return self._error(
                "list_markets_failed",
                f"Polymarket list_markets failed: {exc}",
                degraded_reason="Polymarket Gamma market listing failed.",
                status="degraded",
                auth_truth="failed",
            )

    def get_market(self, market_id: str, **kwargs) -> dict[str, Any]:
        err = self._config_check()
        if err:
            return err
        try:
            resp = self._request("GET", f"/markets/{market_id}", base_url=_GAMMA_BASE)
            payload = resp.json()
            return self._ok(
                {"market": payload, "truth_note": _PUBLIC_DATA_NOTE},
                status="ready",
                auth_truth="validated",
            )
        except Exception as exc:
            return self._error(
                "get_market_failed",
                f"Polymarket get_market failed: {exc}",
                degraded_reason=f"Polymarket market {market_id} could not be fetched.",
                status="degraded",
                auth_truth="failed",
            )

    def get_orderbook(self, market_id: str, **kwargs) -> dict[str, Any]:
        """Fetch CLOB orderbook for a token ID (market condition ID)."""
        err = self._config_check()
        if err:
            return err
        try:
            resp = self._request(
                "GET", "/book", params={"token_id": market_id}, base_url=_CLOB_BASE
            )
            payload = resp.json()
            return self._ok(
                {"orderbook": payload, "truth_note": _PUBLIC_DATA_NOTE},
                status="ready",
                auth_truth="validated",
            )
        except Exception as exc:
            return self._error(
                "get_orderbook_failed",
                f"Polymarket CLOB orderbook fetch failed: {exc}",
                degraded_reason=f"Polymarket CLOB orderbook for token {market_id} could not be fetched.",
                status="degraded",
                auth_truth="failed",
            )

    def get_recent_trades(self, market_id: str, **kwargs) -> dict[str, Any]:
        """Fetch recent trades from CLOB public endpoint."""
        err = self._config_check()
        if err:
            return err
        params: dict[str, Any] = {"token_id": market_id, "limit": kwargs.get("limit", 25)}
        try:
            resp = self._request("GET", "/trades", params=params, base_url=_CLOB_BASE)
            payload = resp.json()
            trades = payload if isinstance(payload, list) else payload.get("trades", [])
            return self._ok(
                {
                    "trades": trades if isinstance(trades, list) else [],
                    "count": len(trades) if isinstance(trades, list) else 0,
                    "truth_note": _PUBLIC_DATA_NOTE,
                },
                status="ready",
                auth_truth="validated",
            )
        except Exception as exc:
            return self._error(
                "get_recent_trades_failed",
                f"Polymarket CLOB trades fetch failed: {exc}",
                degraded_reason=f"Polymarket CLOB trades for token {market_id} could not be fetched.",
                status="degraded",
                auth_truth="failed",
            )

    def place_order(self, **kwargs) -> dict[str, Any]:
        """Public adapter never places orders. Use authenticated CLOB client separately."""
        return self._error(
            "execution_disabled",
            "Polymarket public adapter does not support order placement.",
            degraded_reason=(
                "PUBLIC DATA ONLY — authenticated order placement requires the Polymarket "
                "CLOB client with valid credentials. This adapter is read-only."
            ),
            status="disabled",
            auth_truth="validated",
            auth_validated=True,
        )
