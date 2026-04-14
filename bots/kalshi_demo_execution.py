from __future__ import annotations

"""
Bot 4 — Kalshi Demo Execution Bot.

Scans Kalshi DEMO markets for EV opportunities and simulates order placement
against the demo environment. NEVER routes to production.

Execution is always simulated here (execution_enabled=False on the adapter).
This bot demonstrates what a live execution bot would do on Kalshi
without touching real funds or the production API.

Mode:     DEMO — sandbox credentials only.
Platform: Kalshi demo environment (separate from production).
Truth:    DEMO — no real funds. Isolated from production.
Hard rules:
  - Never accesses production Kalshi API.
  - Adapter must validate demo credentials separately.
  - execution_enabled on the adapter must remain False by default.
"""

import uuid
from typing import Any

from bots.base_research_bot import BaseResearchBot
from models.proposal import Proposal


# Minimum EV threshold to emit a "would-place" signal
_EV_THRESHOLD = 0.03
# Price range considered "edge-worthy" (avoid extreme contracts)
_PRICE_MIN = 0.15
_PRICE_MAX = 0.85


def _extract_best_implied(market: dict[str, Any]) -> float | None:
    """Extract the best YES implied probability from a Kalshi market dict."""
    for key in ("yes_ask", "yes_bid", "last_price", "last_trade_price", "close_price"):
        val = market.get(key)
        if val is None:
            continue
        try:
            f = float(val)
            return f / 100.0 if f > 1.0 else f
        except Exception:
            continue
    return None


class KalshiDemoExecutionBot(BaseResearchBot):
    bot_id = "bot_kalshi_demo_execution"
    display_name = "Kalshi Demo Execution"
    platform = "kalshi_demo"
    mode = "DEMO"
    signal_type = "demo_execution"
    demo_only = True
    implemented = True

    DEMO_NOTE = (
        "DEMO ENVIRONMENT ONLY — This bot uses Kalshi demo credentials. "
        "No real funds are at risk. Orders are simulated. "
        "Execution is disabled by default on the adapter."
    )

    def __init__(self, adapter=None):
        self.adapter = adapter
        self._simulated_positions: list[dict[str, Any]] = []

    def run_one_cycle(self) -> dict[str, Any]:
        if self.adapter is None:
            return self.disabled_result(
                "Kalshi demo adapter is not wired. "
                "Set ENABLE_KALSHI=true and KALSHI_USE_DEMO=true with valid KALSHI_API_KEY."
            )

        health = self.adapter.healthcheck()
        if not health.get("ok"):
            return self.disabled_result(
                health.get("degraded_reason",
                           "Kalshi demo adapter is not healthy or not configured.")
            )

        markets_resp = self.adapter.list_markets(limit=20)
        if not markets_resp.get("ok"):
            return self.disabled_result(
                markets_resp.get("degraded_reason", "Kalshi demo markets unavailable.")
            )

        markets = (markets_resp.get("data") or {}).get("markets", [])
        candidate: dict[str, Any] | None = None
        best_ev = 0.0

        for market in markets[:20]:
            market_id = (
                market.get("ticker")
                or market.get("market_ticker")
                or market.get("id")
            )
            if not market_id:
                continue

            implied = _extract_best_implied(market)
            if implied is None:
                continue
            if not (_PRICE_MIN <= implied <= _PRICE_MAX):
                continue

            # Simple EV estimate: deviation from 0.5 after spread deduction
            # (In a real bot this would use a proper probability model.)
            mid_deviation = abs(implied - 0.50)
            spread = market.get("yes_ask", 0) - market.get("yes_bid", 0)
            spread_cost = float(spread) / 100.0 if spread else 0.05
            ev_estimate = mid_deviation - spread_cost / 2.0

            if ev_estimate > best_ev:
                best_ev = ev_estimate
                candidate = {
                    "market_id": market_id,
                    "title": market.get("title") or market.get("subtitle") or market_id,
                    "yes_implied": round(implied, 4),
                    "ev_estimate": round(ev_estimate, 4),
                    "spread_cost": round(spread_cost, 4),
                }

        if not candidate:
            return self.disabled_result(
                "No Kalshi demo markets cleared the EV threshold or had readable prices."
            )

        taken = best_ev >= _EV_THRESHOLD
        simulated_size = self._demo_size(best_ev)

        # Record simulated position (never a real order)
        if taken:
            self._simulated_positions.append({
                "market_id": candidate["market_id"],
                "simulated_size": simulated_size,
                "ev_estimate": best_ev,
                "note": "DEMO SIMULATED — not a real order",
            })
            # Keep at most 20 recent simulated positions
            if len(self._simulated_positions) > 20:
                self._simulated_positions = self._simulated_positions[-20:]

        return self.emit_signal(
            title=f"Demo EV Signal — {candidate['title']}",
            summary=(
                f"EV estimate {best_ev:.4f} "
                + ("≥ threshold — would place DEMO order of "
                   f"${simulated_size:.2f}." if taken
                   else "< threshold — signal skipped.")
            ),
            confidence=min(0.99, best_ev * 5.0),
            signal_taken=taken,
            degraded_reason="" if taken else f"EV {best_ev:.4f} < threshold {_EV_THRESHOLD}.",
            data={
                **candidate,
                "simulated_size": simulated_size if taken else 0.0,
                "execution_note": (
                    "DEMO SIMULATED — execution_enabled=False on adapter. "
                    "No order was placed on any Kalshi environment."
                ),
                "simulated_position_count": len(self._simulated_positions),
                "truth_note": self.DEMO_NOTE,
            },
        )

    def generate_proposal(self, context: dict[str, Any] | None = None) -> Proposal | None:
        result = self.run_one_cycle()
        if not result.get("signal_taken"):
            return None
        data = result.get("data", {}) or {}
        ev_estimate = float(data.get("ev_estimate", 0) or 0)
        edge_bps = ev_estimate * 10000
        edge_post_fee = edge_bps - 10.0
        if edge_post_fee <= 0:
            return None
        ctx = context or {}
        return Proposal(
            proposal_id=f"p_{uuid.uuid4().hex[:12]}",
            bot_id=self.bot_id,
            platform="kalshi_demo",
            market_id=str(data.get("market_id", "")),
            side="BUY_YES",
            confidence=round(float(result.get("confidence", 0) or 0), 4),
            edge_bps=round(edge_bps, 2),
            edge_post_fee_bps=round(edge_post_fee, 2),
            expected_hold_s=900,
            max_slippage_bps=float(data.get("spread_cost", 0.05) or 0.05) * 10000,
            correlation_key="kalshi_demo_execution",
            reason_code="demo_ev_signal",
            runtime_mode=str(ctx.get("runtime_mode", "demo")).lower(),
            truth_label="DEMO — EXCHANGE SANDBOX",
            metadata={
                "market_id": data.get("market_id", ""),
                "yes_implied": data.get("yes_implied"),
                "ev_estimate": ev_estimate,
                "spread_cost": data.get("spread_cost"),
                "simulated_size": data.get("simulated_size", 0),
            },
        )

    @staticmethod
    def _demo_size(ev: float) -> float:
        """Phase-aware demo sizing. Conservative fixed-fraction for demo mode."""
        base = 1.0  # $1 demo base
        multiplier = min(3.0, 1.0 + ev * 10.0)
        return round(base * multiplier, 2)
