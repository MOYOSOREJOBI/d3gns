from __future__ import annotations

"""
Bot 9 — Polymarket Microstructure.

Analyzes CLOB orderbook microstructure on Polymarket public markets.
Looks for:
  - Thin spread (tight yes/no top-of-book)
  - Price extremity (implied near 0 or 1 — often mispriced)
  - Short-term liquidity imbalance (yes vs no top-of-book depth)

Mode:     PAPER — no order placement.
Platform: Polymarket public (Gamma + CLOB)
Truth:    PUBLIC DATA ONLY / PAPER
"""

from typing import Any

from bots.base_research_bot import BaseResearchBot


def _safe_float(val: Any, divisor: float = 1.0) -> float | None:
    if val is None:
        return None
    try:
        f = float(val)
        return f / divisor if divisor != 1.0 else f
    except Exception:
        return None


def _extract_orderbook_depth(book: dict[str, Any], side: str) -> tuple[float, float]:
    """Return (best_price, top3_size) for a given side of the orderbook."""
    levels = book.get(side, [])
    if not levels:
        return 0.0, 0.0
    best_price = 0.0
    size = 0.0
    for i, level in enumerate(levels[:3]):
        if isinstance(level, dict):
            p = _safe_float(level.get("price"))
            s = _safe_float(level.get("size") or level.get("quantity"))
            if p is not None and i == 0:
                best_price = p
            if s is not None:
                size += s
    return best_price, size


class PolymarketMicrostructureBot(BaseResearchBot):
    bot_id = "bot_polymarket_microstructure_paper"
    display_name = "Polymarket Microstructure"
    platform = "polymarket_public"
    mode = "PAPER"
    signal_type = "microstructure"
    paper_only = True
    implemented = True

    # Signal thresholds
    SPREAD_THRESHOLD = 0.10       # Spread ≤ this is "tight"
    EXTREMITY_THRESHOLD = 0.88    # Implied ≥ this or ≤ (1-this) is "extreme"
    IMBALANCE_THRESHOLD = 0.30    # |yes_depth - no_depth| / total ≥ this

    def __init__(self, adapter=None):
        self.adapter = adapter

    def run_one_cycle(self) -> dict[str, Any]:
        if self.adapter is None:
            return self.disabled_result("Polymarket public adapter is not wired.")

        health = self.adapter.healthcheck()
        if not health.get("ok"):
            return self.disabled_result(
                health.get("degraded_reason", "Polymarket adapter unavailable.")
            )

        # Get active markets
        markets_resp = self.adapter.list_markets(limit=25, active="true")
        if not markets_resp.get("ok"):
            return self.disabled_result(
                markets_resp.get("degraded_reason", "Polymarket markets unavailable.")
            )

        markets = (markets_resp.get("data") or {}).get("markets", [])
        if not markets:
            return self.disabled_result("Polymarket returned no active markets.")

        best_result = None

        for market in markets[:15]:
            # Extract condition/token ID for CLOB orderbook
            condition_id = (
                market.get("conditionId")
                or market.get("condition_id")
                or market.get("id")
            )
            if not condition_id:
                continue

            # Attempt CLOB orderbook fetch
            book_resp = self.adapter.get_orderbook(condition_id)
            if not book_resp.get("ok"):
                # Orderbook unavailable — fall back to market-level data only
                implied = self._market_implied(market)
                if implied is None:
                    continue
                result = self._from_market_only(market, implied)
            else:
                book = (book_resp.get("data") or {}).get("orderbook", {})
                if not isinstance(book, dict):
                    book = {}
                result = self._from_orderbook(market, book)

            if result is None:
                continue
            if not best_result or result["confidence"] > best_result["confidence"]:
                best_result = result

        return best_result or self.disabled_result(
            "No Polymarket markets produced a clear microstructure signal this cycle."
        )

    def _from_orderbook(self, market: dict, book: dict) -> dict[str, Any] | None:
        """Analyze orderbook depth for microstructure signals."""
        # bids = YES side, asks = NO side (Polymarket CLOB convention)
        yes_price, yes_depth = _extract_orderbook_depth(book, "bids")
        no_price, no_depth = _extract_orderbook_depth(book, "asks")

        if yes_price == 0.0 and no_price == 0.0:
            return None

        spread = max(0.0, (no_price - yes_price) if no_price > yes_price else 0.0)
        implied_yes = yes_price  # Polymarket CLOB prices are already 0–1
        total_depth = yes_depth + no_depth
        imbalance = (yes_depth - no_depth) / max(total_depth, 1.0)
        extremity = max(abs(implied_yes - 0.5) * 2, 0.0) if implied_yes > 0 else 0.0

        # Signal scoring
        spread_score = max(0.0, 1.0 - spread / 0.20) if spread < 0.20 else 0.0
        extremity_score = max(0.0, implied_yes - self.EXTREMITY_THRESHOLD) if implied_yes >= self.EXTREMITY_THRESHOLD \
            else max(0.0, (1 - self.EXTREMITY_THRESHOLD) - implied_yes) if implied_yes <= (1 - self.EXTREMITY_THRESHOLD) \
            else 0.0
        imbalance_score = max(0.0, abs(imbalance) - self.IMBALANCE_THRESHOLD)
        score = 0.35 * spread_score + 0.35 * imbalance_score + 0.30 * extremity_score

        title = market.get("question") or market.get("title") or market.get("conditionId", "")
        taken = (spread <= self.SPREAD_THRESHOLD or abs(imbalance) >= self.IMBALANCE_THRESHOLD)

        return self.emit_signal(
            title=f"Microstructure — {title[:60]}",
            summary=(
                f"Spread {spread:.4f}, "
                f"imbalance {imbalance:+.3f}, "
                f"yes_implied {implied_yes:.4f}. "
                + ("Signal taken." if taken else "Below thresholds.")
            ),
            confidence=min(0.99, score),
            signal_taken=taken,
            degraded_reason="" if taken else f"Spread {spread:.4f} and imbalance {imbalance:.3f} below thresholds.",
            data={
                "market_id": market.get("conditionId") or market.get("id"),
                "title": title,
                "yes_implied": round(implied_yes, 4),
                "spread": round(spread, 4),
                "yes_top3_depth": round(yes_depth, 2),
                "no_top3_depth": round(no_depth, 2),
                "imbalance": round(imbalance, 4),
                "extremity": round(extremity, 4),
                "source": "clob_orderbook",
                "truth_note": "PAPER — PUBLIC DATA ONLY. No order placement.",
            },
        )

    def _from_market_only(self, market: dict, implied: float) -> dict[str, Any] | None:
        """Fallback: use market-level price data when CLOB orderbook is unavailable."""
        extremity_score = (
            max(0.0, implied - self.EXTREMITY_THRESHOLD) if implied >= self.EXTREMITY_THRESHOLD
            else max(0.0, (1 - self.EXTREMITY_THRESHOLD) - implied) if implied <= (1 - self.EXTREMITY_THRESHOLD)
            else 0.0
        )
        if extremity_score < 0.01:
            return None

        title = market.get("question") or market.get("title") or "market"
        return self.emit_signal(
            title=f"Microstructure (fallback) — {title[:60]}",
            summary=f"Implied YES {implied:.4f}. Extremity signal (orderbook unavailable).",
            confidence=min(0.60, extremity_score * 2),
            signal_taken=extremity_score >= 0.05,
            degraded_reason="" if extremity_score >= 0.05 else "Extremity below threshold.",
            data={
                "market_id": market.get("conditionId") or market.get("id"),
                "yes_implied": round(implied, 4),
                "extremity": round(extremity_score, 4),
                "source": "market_fallback",
                "truth_note": "PAPER — PUBLIC DATA ONLY. CLOB orderbook unavailable.",
            },
        )

    @staticmethod
    def _market_implied(market: dict) -> float | None:
        for key in ("lastTradePrice", "bestBid", "midpoint"):
            val = market.get(key)
            if val is None:
                continue
            try:
                f = float(val)
                return f / 100.0 if f > 1.0 else f
            except Exception:
                pass
        prices = market.get("outcomePrices")
        if isinstance(prices, list) and prices:
            try:
                return float(prices[0])
            except Exception:
                pass
        return None
