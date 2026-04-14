from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from bots.base_research_bot import BaseResearchBot
from bots.kalshi_orderbook_imbalance import _extract_levels


def _parse_ts(value: str | None) -> float | None:
    if not value:
        return None
    try:
        clean = value.replace("Z", "+00:00")
        return datetime.fromisoformat(clean).astimezone(timezone.utc).timestamp()
    except Exception:
        return None


class KalshiResolutionDecayBot(BaseResearchBot):
    bot_id = "bot_kalshi_resolution_decay_paper"
    display_name = "Kalshi Resolution Decay"
    platform = "kalshi_public"
    mode = "PAPER"
    signal_type = "resolution_decay"
    paper_only = True
    implemented = True

    def __init__(self, adapter):
        self.adapter = adapter

    def run_one_cycle(self) -> dict[str, Any]:
        health = self.adapter.healthcheck()
        if not health.get("ok"):
            return self.disabled_result(health.get("degraded_reason"))

        markets_resp = self.adapter.list_markets(limit=20)
        markets = (markets_resp.get("data") or {}).get("markets", [])
        now_ts = datetime.now(timezone.utc).timestamp()
        best_result = None

        for market in markets[:20]:
            market_id = market.get("ticker") or market.get("id")
            expires_at = _parse_ts(
                market.get("close_time")
                or market.get("expiration_time")
                or market.get("settlement_time")
                or market.get("expiry_time")
            )
            if not market_id or not expires_at or expires_at <= now_ts:
                continue
            hours_left = (expires_at - now_ts) / 3600.0
            orderbook_resp = self.adapter.get_orderbook(market_id)
            payload = (orderbook_resp.get("data") or {}).get("orderbook", {})
            if not isinstance(payload, dict):
                payload = {}
            yes_levels = _extract_levels(payload, ["yes", "yes_levels", "buy_yes"])
            no_levels = _extract_levels(payload, ["no", "no_levels", "buy_no"])
            if not yes_levels or not no_levels:
                continue
            best_yes = max(level["price"] for level in yes_levels[:2]) / 100.0
            best_no = max(level["price"] for level in no_levels[:2]) / 100.0
            spread_width = max(0.0, 1.0 - (best_yes + best_no))
            decay_score = (1.0 / max(hours_left, 0.25)) * max(0.05, 1.0 - min(spread_width, 0.75))
            result = self.emit_signal(
                title=f"{market.get('title') or market_id}",
                summary=(
                    f"{hours_left:.2f}h to resolution with implied YES {best_yes:.3f} and "
                    f"spread width {spread_width:.3f}."
                ),
                confidence=min(0.99, decay_score / 4.0),
                signal_taken=hours_left <= 24 and spread_width <= 0.12,
                degraded_reason="" if hours_left <= 24 else "Market is not close enough to resolution for a decay signal.",
                data={
                    "market_id": market_id,
                    "hours_to_resolution": round(hours_left, 3),
                    "best_yes_implied": round(best_yes, 4),
                    "spread_width": round(spread_width, 4),
                    "decay_score": round(decay_score, 4),
                },
            )
            if not best_result or result["confidence"] > best_result["confidence"]:
                best_result = result

        return best_result or self.disabled_result("No Kalshi markets exposed enough time-to-resolution decay for the current sample.")

