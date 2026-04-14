from __future__ import annotations

from typing import Any

from adapters.base_adapter import BaseAdapter


class FearGreedAdapter(BaseAdapter):
    """Alternative.me Crypto Fear & Greed Index — no auth, daily updated sentiment."""

    platform_name = "fear_greed"
    mode = "PUBLIC DATA ONLY"
    live_capable = False
    execution_enabled = False
    auth_required = False
    data_truth_label = "PUBLIC DATA ONLY"
    base_url = "https://api.alternative.me"

    # Classification thresholds
    _LABELS = {
        (0, 25): "Extreme Fear",
        (25, 46): "Fear",
        (46, 55): "Neutral",
        (55, 75): "Greed",
        (75, 101): "Extreme Greed",
    }

    def is_configured(self) -> bool:
        return True

    def _classify(self, value: int) -> str:
        for (lo, hi), label in self._LABELS.items():
            if lo <= value < hi:
                return label
        return "Unknown"

    def healthcheck(self) -> dict[str, Any]:
        result = self.get_current()
        if result.get("ok"):
            return self._ok(data={"status": "ok", "index": result["data"]}, auth_truth="no_auth_required")
        return self._error("health_failed", result.get("error", "unknown"), auth_truth="no_auth_required")

    def get_current(self) -> dict[str, Any]:
        """Fetch the current Fear & Greed index value."""
        try:
            r = self._request("GET", "/fng/", params={"limit": 1, "format": "json"})
            raw = r.json()
            entry = raw.get("data", [{}])[0]
            value = int(entry.get("value", 0))
            return self._ok(
                data={
                    "value": value,
                    "classification": entry.get("value_classification") or self._classify(value),
                    "timestamp": entry.get("timestamp"),
                    "time_until_update": raw.get("metadata", {}).get("error"),
                },
                status="ok",
                auth_truth="no_auth_required",
            )
        except Exception as exc:
            return self._error("fng_failed", str(exc), auth_truth="no_auth_required")

    def get_history(self, days: int = 30) -> dict[str, Any]:
        """Fetch historical F&G index values."""
        try:
            r = self._request("GET", "/fng/", params={"limit": days, "format": "json"})
            raw = r.json()
            entries = [
                {
                    "value": int(e.get("value", 0)),
                    "classification": e.get("value_classification"),
                    "timestamp": e.get("timestamp"),
                }
                for e in raw.get("data", [])
            ]
            if not entries:
                return self._error("no_data", "No F&G history returned", auth_truth="no_auth_required")
            avg = sum(e["value"] for e in entries) / len(entries)
            return self._ok(
                data={
                    "history": entries,
                    "days": days,
                    "average": round(avg, 1),
                    "avg_classification": self._classify(int(avg)),
                    "current": entries[0] if entries else None,
                    "prior_30d_low": min(e["value"] for e in entries),
                    "prior_30d_high": max(e["value"] for e in entries),
                },
                status="ok",
                auth_truth="no_auth_required",
            )
        except Exception as exc:
            return self._error("history_failed", str(exc), auth_truth="no_auth_required")

    def get_sentiment_signal(self) -> dict[str, Any]:
        """Derived signal: contrarian direction based on F&G extremes."""
        result = self.get_history(days=7)
        if not result.get("ok"):
            return result
        data = result["data"]
        current_val = data.get("current", {}).get("value", 50)
        avg_7d = data.get("average", 50)
        # Contrarian: extreme fear → bullish signal, extreme greed → bearish signal
        if current_val <= 20:
            direction = "bullish_contrarian"
            strength = (20 - current_val) / 20
        elif current_val >= 80:
            direction = "bearish_contrarian"
            strength = (current_val - 80) / 20
        else:
            direction = "neutral"
            strength = 0.0
        return self._ok(
            data={
                **data,
                "signal_direction": direction,
                "signal_strength": round(min(strength, 1.0), 3),
                "avg_7d": avg_7d,
                "note": "Contrarian signal only — not a direct trade trigger.",
            },
            status="ok",
            auth_truth="no_auth_required",
        )
