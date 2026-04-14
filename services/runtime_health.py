from __future__ import annotations

import threading
import time
from typing import Any


class RuntimeHealthTracker:
    def __init__(self):
        self._lock = threading.Lock()
        self._state: dict[str, dict[str, Any]] = {}

    def ensure(self, bot_id: str, *, enabled: bool, degraded_reason: str = ""):
        with self._lock:
            current = self._state.setdefault(
                bot_id,
                {
                    "enabled": enabled,
                    "loop_alive": False,
                    "market_data_ok": False,
                    "auth_ok": False,
                    "execution_ready": False,
                    "reconciliation_ready": False,
                    "degraded_reason": degraded_reason,
                    "last_heartbeat_ts": 0.0,
                    "last_error": "",
                },
            )
            current["enabled"] = enabled
            if degraded_reason:
                current["degraded_reason"] = degraded_reason

    def update(self, bot_id: str, **fields: Any):
        with self._lock:
            current = self._state.setdefault(bot_id, {})
            current.update(fields)
            if fields.get("loop_alive"):
                current["last_heartbeat_ts"] = time.time()

    def heartbeat(self, bot_id: str, **fields: Any):
        payload = {"loop_alive": True, "last_heartbeat_ts": time.time()}
        payload.update(fields)
        self.update(bot_id, **payload)

    def get(self, bot_id: str) -> dict[str, Any]:
        with self._lock:
            return dict(self._state.get(bot_id, {}))

    def all(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {bot_id: dict(state) for bot_id, state in self._state.items()}

