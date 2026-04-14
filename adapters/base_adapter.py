from __future__ import annotations

import os
import time
from abc import ABC
from typing import Any, Callable

import requests

from engine_bridge.base import (
    NormalizedFill,
    NormalizedMarket,
    NormalizedOrder,
    OrderType,
    Side,
    VenueSlot,
    normalize_adapter_fill,
)
from engine_bridge.fills import canonical_fill_state
from engine_bridge.orders import normalize_order_side
from engine_bridge.positions import normalize_position_payload
from engine_bridge.runtime_modes import adapter_supports_mode, mode_truth_label
from engine_bridge.symbols import normalize_symbol
from engine_bridge.types import CapabilityReport


SettingsGetter = Callable[[str, str], str] | None
ProxyGetter = Callable[[], dict[str, str] | None] | None


class BaseAdapter(ABC):
    platform_name = "base"
    mode = "RESEARCH"
    live_capable = False
    execution_enabled = False
    auth_required = False
    data_truth_label = "RESEARCH"
    base_url = ""

    def __init__(
        self,
        settings_getter: SettingsGetter = None,
        proxy_getter: ProxyGetter = None,
        timeout: float = 10.0,
    ):
        self.settings_getter = settings_getter
        self.proxy_getter = proxy_getter
        self.timeout = timeout

    def get_platform_name(self) -> str:
        return self.platform_name

    def is_configured(self) -> bool:
        return True

    def get_mode(self) -> str:
        return self.mode

    def healthcheck(self) -> dict[str, Any]:
        return self._ok(
            data={},
            status="ready" if self.is_configured() else "not_configured",
            auth_truth="validated" if self.is_configured() else "missing",
            degraded_reason="" if self.is_configured() else "Adapter not configured.",
        )

    def supports_mode(self, mode: str) -> bool:
        return adapter_supports_mode(self, mode)

    def normalize_market(self, payload: dict[str, Any] | None = None, **kwargs) -> dict[str, Any]:
        raw = payload or {}
        raw_id = str(
            raw.get("ticker")
            or raw.get("market_id")
            or raw.get("condition_id")
            or raw.get("token_id")
            or raw.get("id")
            or kwargs.get("market_id")
            or "unknown"
        )
        title = str(raw.get("title") or raw.get("question") or raw.get("name") or raw_id)
        category = str(raw.get("category") or raw.get("sector") or raw.get("sport_key") or "general")
        market = NormalizedMarket(
            symbol=normalize_symbol(self.get_platform_name(), raw_id),
            venue=self.get_platform_name(),
            raw_id=raw_id,
            title=title,
            category=category,
            slot=VenueSlot.PREDICTION_MARKET,
            yes_price=raw.get("yes_price"),
            no_price=raw.get("no_price"),
            volume_24h=raw.get("volume_24h") or raw.get("volume"),
            open_interest=raw.get("open_interest"),
            closes_at=raw.get("closes_at") or raw.get("close_time"),
            truth_label=self.data_truth_label,
            raw=raw,
        )
        return market.to_dict()

    def normalize_orderbook(self, payload: dict[str, Any] | None = None, **kwargs) -> dict[str, Any]:
        raw = payload or {}
        if "orderbook" in raw and isinstance(raw.get("orderbook"), dict):
            raw = raw["orderbook"]
        if hasattr(self, "_normalize_orderbook"):
            try:
                raw = getattr(self, "_normalize_orderbook")(raw)
            except Exception:
                raw = raw or {}

        def _level(level: Any) -> dict[str, float]:
            if isinstance(level, dict):
                return {
                    "price": float(level.get("price", 0) or 0),
                    "size": float(level.get("size", level.get("quantity", level.get("count", 0))) or 0),
                }
            if isinstance(level, (list, tuple)) and len(level) >= 2:
                return {"price": float(level[0] or 0), "size": float(level[1] or 0)}
            return {"price": 0.0, "size": 0.0}

        asks = [_level(level) for level in raw.get("asks", [])]
        bids = [_level(level) for level in raw.get("bids", [])]
        if not asks and raw.get("yes"):
            asks = [_level(level) for level in raw.get("yes", [])]
        if not bids and raw.get("no"):
            bids = [_level(level) for level in raw.get("no", [])]

        asks = [level for level in asks if level["size"] > 0]
        bids = [level for level in bids if level["size"] > 0]
        asks.sort(key=lambda row: row["price"])
        bids.sort(key=lambda row: row["price"], reverse=True)
        return {
            "market_id": kwargs.get("market_id") or raw.get("market_id") or "",
            "platform": self.get_platform_name(),
            "truth_label": self.data_truth_label,
            "asks": asks,
            "bids": bids,
            "best_ask": asks[0]["price"] if asks else None,
            "best_bid": bids[0]["price"] if bids else None,
            "depth": len(asks) + len(bids),
        }

    def normalize_fill(self, payload: dict[str, Any] | None = None, *, order: NormalizedOrder | None = None, **kwargs) -> dict[str, Any]:
        raw = payload or {}
        if order is None:
            order = NormalizedOrder(
                order_id=str(raw.get("order_id") or raw.get("id") or ""),
                symbol=normalize_symbol(
                    self.get_platform_name(),
                    str(raw.get("market_id") or raw.get("ticker") or raw.get("symbol") or "unknown"),
                ),
                venue=self.get_platform_name(),
                side=normalize_order_side(raw.get("side")),
                order_type=OrderType.LIMIT if raw.get("price_limit") is not None else OrderType.MARKET,
                size=float(raw.get("size") or raw.get("amount") or 0.0),
                price_limit=float(raw.get("price_limit") or raw.get("price") or 0.0) or None,
                raw=raw,
            )
        fill = normalize_adapter_fill(raw, order)
        return {
            **fill.to_dict(),
            "fill_state": canonical_fill_state(raw),
            "truth_label": raw.get("truth_label") or self.data_truth_label,
        }

    def normalize_position(self, payload: dict[str, Any] | None = None, **kwargs) -> dict[str, Any]:
        return normalize_position_payload(
            payload or {},
            venue=self.get_platform_name(),
            symbol_hint=kwargs.get("symbol_hint"),
            bot_id=kwargs.get("bot_id", ""),
        )

    def reconcile_state(self, payload: dict[str, Any] | None = None, **kwargs) -> dict[str, Any]:
        mode = kwargs.get("mode") or self.get_mode()
        report = CapabilityReport(
            platform=self.get_platform_name(),
            mode=str(mode),
            supported=self.supports_mode(str(mode)),
            truth_label=mode_truth_label(str(mode)),
            degraded_reason="" if self.supports_mode(str(mode)) else "Adapter does not support this runtime mode.",
        )
        return {
            **report.to_dict(),
            "configured": self.is_configured(),
            "execution_enabled": bool(self.execution_enabled),
            "externally_reconciled": False,
            "payload": payload or {},
        }

    def list_markets(self, **kwargs) -> dict[str, Any]:
        return self._unsupported("list_markets")

    def get_market(self, market_id: str, **kwargs) -> dict[str, Any]:
        return self._unsupported("get_market", market_id=market_id)

    def get_orderbook(self, market_id: str, **kwargs) -> dict[str, Any]:
        return self._unsupported("get_orderbook", market_id=market_id)

    def get_recent_trades(self, market_id: str, **kwargs) -> dict[str, Any]:
        return self._unsupported("get_recent_trades", market_id=market_id)

    def get_balance(self, **kwargs) -> dict[str, Any]:
        return self._unsupported("get_balance")

    def place_order(self, **kwargs) -> dict[str, Any]:
        return self._unsupported("place_order", degraded_reason="Live execution is disabled by default.")

    def cancel_order(self, **kwargs) -> dict[str, Any]:
        return self._unsupported("cancel_order", degraded_reason="Live execution is disabled by default.")

    def get_positions(self, **kwargs) -> dict[str, Any]:
        return self._unsupported("get_positions")

    def get_settlements(self, **kwargs) -> dict[str, Any]:
        return self._unsupported("get_settlements")

    def _setting(self, key: str, default: str = "") -> str:
        if not self.settings_getter:
            return os.getenv(key, os.getenv(key.lower(), default))
        try:
            value = self.settings_getter(key, default)
        except TypeError:
            value = self.settings_getter(key) or default
        if value not in (None, ""):
            return value
        return os.getenv(key, os.getenv(key.lower(), default))

    def _bool_setting(self, key: str, default: bool = False) -> bool:
        raw = str(self._setting(key, str(default).lower())).strip().lower()
        return raw in {"1", "true", "yes", "on"}

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        json_body: dict[str, Any] | list[Any] | None = None,
        data_body: Any = None,
        base_url: str | None = None,
        timeout: float | None = None,
        attempts: int = 2,
    ) -> requests.Response:
        url = f"{base_url or self.base_url}{path}"
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                response = requests.request(
                    method=method,
                    url=url,
                    params=params,
                    headers=headers,
                    json=json_body,
                    data=data_body,
                    proxies=self.proxy_getter() if self.proxy_getter else None,
                    timeout=timeout or self.timeout,
                )
                response.raise_for_status()
                return response
            except requests.RequestException as exc:
                last_error = exc
                if attempt >= attempts:
                    raise
                time.sleep(0.35 * attempt)
        if last_error:
            raise last_error
        raise RuntimeError("request failed without an error")

    def _truth(self, *, auth_validated: bool = False, auth_truth: str = "missing") -> dict[str, Any]:
        return {
            "mode": self.get_mode(),
            "live_capable": bool(self.live_capable),
            "execution_enabled": bool(self.execution_enabled),
            "auth_validated": bool(auth_validated),
            "auth_truth": auth_truth,
            "data_truth_label": self.data_truth_label,
        }

    def _ok(self, data: dict[str, Any] | list[Any] | None = None, **extra: Any) -> dict[str, Any]:
        auth_truth = extra.pop("auth_truth", "validated" if self.is_configured() else "missing")
        auth_validated = bool(extra.pop("auth_validated", auth_truth == "validated"))
        payload = {
            "ok": True,
            "platform": self.get_platform_name(),
            "configured": self.is_configured(),
            "degraded_reason": "",
            "truth_labels": self._truth(auth_validated=auth_validated, auth_truth=auth_truth),
            "data": data if data is not None else {},
        }
        payload.update(extra)
        return payload

    def _error(
        self,
        code: str,
        message: str,
        *,
        degraded_reason: str | None = None,
        auth_truth: str = "missing",
        auth_validated: bool = False,
        status: str = "error",
        data: dict[str, Any] | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        payload = {
            "ok": False,
            "platform": self.get_platform_name(),
            "configured": self.is_configured(),
            "status": status,
            "error_code": code,
            "error": message,
            "degraded_reason": degraded_reason or message,
            "truth_labels": self._truth(auth_validated=auth_validated, auth_truth=auth_truth),
            "data": data or {},
        }
        payload.update(extra)
        return payload

    def _unsupported(self, operation: str, **extra: Any) -> dict[str, Any]:
        return self._error(
            "unsupported",
            f"{operation} is not supported for {self.get_platform_name()} in {self.get_mode()} mode.",
            status="unsupported",
            auth_truth="validated" if self.is_configured() else "missing",
            auth_validated=self.is_configured(),
            **extra,
        )
