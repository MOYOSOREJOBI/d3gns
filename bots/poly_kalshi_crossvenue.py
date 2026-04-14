from __future__ import annotations

"""
Bot 8 — Polymarket vs Kalshi Crossvenue Spread.

Compares implied probabilities between Polymarket and Kalshi for similar events.
Uses the crossvenue_matcher service to find comparable markets.

Conservative matching rules:
  - Only surfaces pairs above the WATCHLIST_THRESHOLD.
  - Pairs below HIGH_CONFIDENCE_THRESHOLD are watchlist-only, not active spread signals.
  - Never assumes exact event equivalence without a confidence score.
  - No order placement. Spread detection only.

Mode:     PAPER — no order placement. Watchlist or paper signal.
Platforms: Polymarket (public) + Kalshi (public)
Truth:    PUBLIC DATA ONLY / PAPER / WATCHLIST ONLY for low-confidence pairs
"""

import uuid
from typing import Any

from bots.base_research_bot import BaseResearchBot
from models.proposal import Proposal
from services.crossvenue_matcher import (
    HIGH_CONFIDENCE_THRESHOLD,
    WATCHLIST_THRESHOLD,
    match_market_lists,
)


class PolyKalshiCrossvenueBot(BaseResearchBot):
    bot_id = "bot_poly_kalshi_crossvenue_spread"
    display_name = "Poly vs Kalshi Crossvenue Spread"
    platform = "polymarket_public+kalshi_public"
    mode = "PAPER"
    signal_type = "crossvenue_spread"
    paper_only = True
    implemented = True

    SPREAD_THRESHOLD = 0.05  # Minimum implied probability gap to flag

    def __init__(self, poly_adapter=None, kalshi_adapter=None):
        self.poly_adapter = poly_adapter
        self.kalshi_adapter = kalshi_adapter

    def run_one_cycle(self) -> dict[str, Any]:
        if self.poly_adapter is None or self.kalshi_adapter is None:
            return self.disabled_result(
                "Both Polymarket and Kalshi public adapters are required. "
                "Ensure polymarket_public is enabled (always-on by default) and "
                "ENABLE_KALSHI=true is set."
            )

        # Check Polymarket health
        poly_health = self.poly_adapter.healthcheck()
        if not poly_health.get("ok"):
            return self.disabled_result(
                f"Polymarket: {poly_health.get('degraded_reason', 'unavailable')}"
            )

        # Check Kalshi health (gracefully skip if disabled, emit watchlist-only)
        kalshi_health = self.kalshi_adapter.healthcheck()
        kalshi_ok = kalshi_health.get("ok", False)

        # Fetch Polymarket markets
        poly_resp = self.poly_adapter.list_markets(limit=30, active="true")
        if not poly_resp.get("ok"):
            return self.disabled_result(
                f"Polymarket markets: {poly_resp.get('degraded_reason', 'unavailable')}"
            )
        poly_markets = (poly_resp.get("data") or {}).get("markets", [])

        # Fetch Kalshi markets (if available)
        kalshi_markets: list[dict[str, Any]] = []
        if kalshi_ok:
            kalshi_resp = self.kalshi_adapter.list_markets(limit=30)
            if kalshi_resp.get("ok"):
                kalshi_markets = (kalshi_resp.get("data") or {}).get("markets", [])

        if not poly_markets:
            return self.disabled_result("Polymarket returned no active markets.")

        if not kalshi_markets:
            return self.emit_signal(
                title="Poly↔Kalshi Crossvenue — Kalshi unavailable",
                summary=(
                    "Polymarket data available but Kalshi is not configured or disabled. "
                    "Enable KALSHI to see cross-venue spread analysis."
                ),
                confidence=0.0,
                signal_taken=False,
                degraded_reason="Kalshi adapter is not configured. Set ENABLE_KALSHI=true.",
                data={
                    "poly_market_count": len(poly_markets),
                    "kalshi_market_count": 0,
                    "truth_note": "PUBLIC DATA ONLY. Enable Kalshi for crossvenue comparison.",
                },
            )

        # Run crossvenue matcher
        pairs = match_market_lists(
            poly_markets, "polymarket_public",
            kalshi_markets, "kalshi_public",
            title_key_a="question",
            title_key_b="title",
            id_key_a="conditionId",
            id_key_b="ticker",
        )

        if not pairs:
            return self.disabled_result(
                f"No crossvenue pairs found above the watchlist threshold "
                f"({WATCHLIST_THRESHOLD:.0%}) between Polymarket and Kalshi. "
                "Markets may cover different topics this cycle."
            )

        # For each pair, estimate implied spread
        best_pair = None
        best_spread = 0.0

        for pair in pairs[:10]:
            conf = pair.get("composite_confidence", 0.0)
            if conf < WATCHLIST_THRESHOLD:
                continue

            # Get implied prices for matched markets
            poly_implied = self._get_poly_implied(
                poly_markets, pair.get("id_a", "")
            )
            kalshi_implied = self._get_kalshi_implied(
                kalshi_markets, pair.get("id_b", "")
            )

            if poly_implied is None or kalshi_implied is None:
                continue

            spread = abs(poly_implied - kalshi_implied)
            if spread > best_spread:
                best_spread = spread
                best_pair = {
                    **pair,
                    "poly_yes_implied": round(poly_implied, 4),
                    "kalshi_yes_implied": round(kalshi_implied, 4),
                    "implied_spread": round(spread, 4),
                }

        if not best_pair:
            return self.disabled_result(
                "Crossvenue pairs found but implied prices unavailable for spread calculation."
            )

        is_active = best_pair.get("composite_confidence", 0) >= HIGH_CONFIDENCE_THRESHOLD
        spread_signal = best_spread >= self.SPREAD_THRESHOLD
        taken = is_active and spread_signal

        return self.emit_signal(
            title=f"Crossvenue — {best_pair.get('title_a', 'event')}",
            summary=(
                f"Poly {best_pair['poly_yes_implied']:.4f} vs "
                f"Kalshi {best_pair['kalshi_yes_implied']:.4f} "
                f"(spread {best_pair['implied_spread']:.4f}). "
                f"Match confidence {best_pair['composite_confidence']:.2f}. "
                + ("ACTIVE PAIR." if is_active else "WATCHLIST ONLY — below active threshold.")
            ),
            confidence=min(0.99, best_pair["composite_confidence"] * best_spread * 5),
            signal_taken=taken,
            degraded_reason=(
                "" if taken else
                (f"Match confidence {best_pair['composite_confidence']:.2f} < active threshold {HIGH_CONFIDENCE_THRESHOLD}."
                 if not is_active else
                 f"Spread {best_spread:.4f} < threshold {self.SPREAD_THRESHOLD}.")
            ),
            data={
                "best_pair": best_pair,
                "total_pairs": len(pairs),
                "poly_market_count": len(poly_markets),
                "kalshi_market_count": len(kalshi_markets),
                "truth_note": (
                    "PAPER / PUBLIC DATA ONLY. "
                    "Crossvenue spread is indicative only. "
                    "Verify market equivalence manually before any trade decision."
                ),
            },
        )

    def generate_proposal(self, context: dict[str, Any] | None = None) -> Proposal | None:
        result = self.run_one_cycle()
        if not result.get("signal_taken"):
            return None
        data = result.get("data", {}) or {}
        best = data.get("best_pair", {}) or {}
        spread = float(best.get("implied_spread", 0) or 0)
        edge_bps = spread * 10000
        fee_drag_bps = 70.0
        edge_post_fee = edge_bps - fee_drag_bps
        if edge_post_fee <= 0:
            return None
        poly_price = float(best.get("poly_yes_implied", 0.5) or 0.5)
        kalshi_price = float(best.get("kalshi_yes_implied", 0.5) or 0.5)
        poly_cheaper = poly_price < kalshi_price
        ctx = context or {}
        return Proposal(
            proposal_id=f"p_{uuid.uuid4().hex[:12]}",
            bot_id=self.bot_id,
            platform="polymarket_public+kalshi_public",
            market_id=str(best.get("condition_id_a", "") or best.get("ticker_b", "")),
            side="BUY_YES" if poly_cheaper else "SELL_YES",
            confidence=round(float(result.get("confidence", 0) or 0), 4),
            edge_bps=round(edge_bps, 2),
            edge_post_fee_bps=round(edge_post_fee, 2),
            expected_hold_s=3600,
            max_slippage_bps=100,
            correlation_key="crossvenue_spread",
            reason_code="crossvenue_spread_detected",
            runtime_mode=str(ctx.get("runtime_mode", "paper")).lower(),
            truth_label="PAPER — NO REAL ORDER",
            metadata={
                "spread": spread,
                "poly_implied": poly_price,
                "kalshi_implied": kalshi_price,
                "total_pairs": data.get("total_pairs", 0),
            },
        )

    @staticmethod
    def _get_poly_implied(markets: list[dict], condition_id: str) -> float | None:
        for m in markets:
            if m.get("conditionId") == condition_id:
                for key in ("lastTradePrice", "bestBid", "midpoint", "outcomePrices"):
                    val = m.get(key)
                    if val is None:
                        continue
                    if isinstance(val, list) and val:
                        try:
                            return float(val[0])
                        except Exception:
                            pass
                    try:
                        f = float(val)
                        return f / 100.0 if f > 1.0 else f
                    except Exception:
                        pass
        return None

    @staticmethod
    def _get_kalshi_implied(markets: list[dict], ticker: str) -> float | None:
        for m in markets:
            if m.get("ticker") == ticker:
                for key in ("yes_ask", "yes_bid", "last_price", "last_trade_price"):
                    val = m.get(key)
                    if val is None:
                        continue
                    try:
                        f = float(val)
                        return f / 100.0 if f > 1.0 else f
                    except Exception:
                        pass
        return None
