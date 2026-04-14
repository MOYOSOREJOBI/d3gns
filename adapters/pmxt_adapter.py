"""
adapters/pmxt_adapter.py

Thin Python wrapper around the pmxt sidecar server (port 3847).
pmxt is "the ccxt for prediction markets" — unified API for Polymarket,
Kalshi, Limitless, Metaculus, and more via a single REST interface.

Usage:
    adapter = PmxtAdapter(exchange="kalshi")
    result  = adapter.fetch_markets()
    result  = adapter.fetch_orderbook(market_id="KXBTC100")

The pmxt server must be running (start.sh launches it automatically).
Auth token and host are read from env: PMXT_ACCESS_TOKEN, PMXT_HOST.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import requests


_DEFAULT_HOST = "http://localhost:3847"
_LOCK_PATH = Path.home() / ".pmxt" / "server.lock"
_SESSION = requests.Session()
_SESSION.headers["Content-Type"] = "application/json"


def _resolve_host_and_token() -> tuple[str, str]:
    """Read live port and token from pmxt lock file, fall back to env."""
    host = os.getenv("PMXT_HOST", _DEFAULT_HOST)
    token = os.getenv("PMXT_ACCESS_TOKEN", "")
    try:
        if _LOCK_PATH.exists():
            lock = json.loads(_LOCK_PATH.read_text())
            port = lock.get("port")
            lock_token = lock.get("accessToken", "")
            if port:
                host = f"http://localhost:{port}"
            if lock_token and not os.getenv("PMXT_ACCESS_TOKEN"):
                token = lock_token
    except Exception:
        pass
    return host, token


class PmxtAdapter:
    """
    Unified prediction market adapter via pmxt sidecar.

    Supported exchanges (pass as `exchange` param):
      - "polymarket"    — requires POLY_API_SECRET to trade; read-only without
      - "kalshi"        — demo API works without credentials
      - "limitless"     — fully public, no auth required
      - "metaculus"     — public questions/forecasts
    """

    def __init__(self, exchange: str = "kalshi"):
        self.exchange = exchange
        self._host, self._token = _resolve_host_and_token()

    @property
    def platform_name(self) -> str:
        return f"pmxt_{self.exchange}"

    # ── Internal request helper ───────────────────────────────────────────────

    def _call(
        self,
        method: str,
        http_method: str = "GET",
        params: dict | None = None,
        body: dict | None = None,
        timeout: int = 12,
    ) -> dict[str, Any]:
        url = f"{self._host}/api/{self.exchange}/{method}"
        headers = {"x-pmxt-access-token": self._token} if self._token else {}
        try:
            if http_method == "POST":
                resp = _SESSION.post(url, json=body or {}, headers=headers, timeout=timeout)
            else:
                resp = _SESSION.get(url, params=params or {}, headers=headers, timeout=timeout)
            resp.raise_for_status()
            data = resp.json()
            # pmxt wraps responses as {"success": true, "data": [...]}
            if isinstance(data, dict) and "data" in data:
                return {"ok": data.get("success", True), "data": data["data"], "raw": data}
            return {"ok": True, "data": data, "raw": data}
        except requests.Timeout:
            return {"ok": False, "error": "timeout", "data": [], "degraded_reason": f"pmxt/{self.exchange} timed out"}
        except requests.ConnectionError:
            return {"ok": False, "error": "pmxt_offline", "data": [], "degraded_reason": "pmxt sidecar not running"}
        except Exception as exc:
            return {"ok": False, "error": str(exc), "data": [], "degraded_reason": str(exc)}

    # ── Public API ────────────────────────────────────────────────────────────

    def healthcheck(self) -> dict[str, Any]:
        try:
            resp = _SESSION.get(f"{self._host}/health", timeout=4)
            if resp.status_code == 200:
                return {
                    "ok": True,
                    "status": "ready",
                    "exchange": self.exchange,
                    "pmxt_version": resp.json().get("version", "unknown"),
                }
        except Exception as exc:
            return {"ok": False, "status": "offline", "degraded_reason": f"pmxt offline: {exc}"}
        return {"ok": False, "status": "degraded", "degraded_reason": "pmxt health returned non-200"}

    def fetch_markets(self, limit: int = 100) -> dict[str, Any]:
        """Return normalized market list for the exchange."""
        result = self._call("fetchMarkets", params={"limit": limit})
        markets = result.get("data", [])
        if isinstance(markets, list):
            result["count"] = len(markets)
        return result

    def fetch_orderbook(self, market_id: str) -> dict[str, Any]:
        return self._call("fetchOrderBook", params={"marketId": market_id})

    def fetch_positions(self) -> dict[str, Any]:
        return self._call("fetchPositions")

    def fetch_balance(self) -> dict[str, Any]:
        return self._call("fetchBalance")

    def fetch_events(self, limit: int = 50) -> dict[str, Any]:
        return self._call("fetchEvents", params={"limit": limit})

    def list_markets(self, **kwargs) -> dict[str, Any]:
        """Compatibility alias used by expansion bots."""
        return self.fetch_markets(limit=kwargs.get("limit", 100))

    def place_order(
        self,
        *,
        market_id: str = "",
        side: str = "BUY",
        size: float = 0.0,
        price_limit: float | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """
        Place order via the pmxt sidecar.
        Requires pmxt to be running and the exchange to support order placement.
        For Polymarket, the sidecar handles CLOB signing internally.
        Falls back to shadow simulation if the sidecar is offline.
        """
        import os

        body: dict[str, Any] = {
            "marketId": market_id,
            "side": side.upper(),
            "size": size,
        }
        if price_limit is not None:
            body["price"] = price_limit

        result = self._call("createOrder", http_method="POST", body=body)

        if result.get("ok"):
            order_data = result.get("data") or {}
            return {
                "ok": True,
                "order_id": str(
                    order_data.get("orderId")
                    or order_data.get("id")
                    or f"pmxt_{self.exchange}_{market_id}"
                ),
                "fill_price": price_limit,
                "raw": order_data,
                "truth_label": f"DEMO — pmxt/{self.exchange}",
            }

        # Sidecar offline or exchange not supported — shadow simulate
        return {
            "ok": True,
            "order_id": f"pmxt_shadow_{self.exchange}_{market_id}",
            "fill_price": price_limit,
            "simulated": True,
            "truth_label": f"SHADOW — pmxt/{self.exchange} unavailable: {result.get('degraded_reason', result.get('error', 'unknown'))}",
        }


# ── Convenience factory ───────────────────────────────────────────────────────

def make_pmxt(exchange: str) -> PmxtAdapter:
    return PmxtAdapter(exchange=exchange)


# ── Quick connectivity check (called at server startup) ───────────────────────

def pmxt_status() -> dict[str, Any]:
    """Returns pmxt sidecar status + which exchanges are live."""
    if os.getenv("ENABLE_PMXT", "false").lower() not in ("true", "1", "yes"):
        return {"enabled": False}
    adapter = PmxtAdapter("kalshi")
    health = adapter.healthcheck()
    if not health["ok"]:
        return {"enabled": True, "online": False, "reason": health.get("degraded_reason")}

    results: dict[str, bool] = {}
    for exchange in ("kalshi", "limitless", "polymarket"):
        r = PmxtAdapter(exchange).fetch_markets(limit=1)
        results[exchange] = r.get("ok", False) and bool(r.get("data"))

    return {
        "enabled": True,
        "online": True,
        "host": adapter._host,
        "exchanges": results,
    }
