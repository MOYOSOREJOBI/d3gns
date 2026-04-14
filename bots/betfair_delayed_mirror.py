from __future__ import annotations

"""
Bot 10 — Betfair Delayed Mirror.

Mirrors Betfair Exchange market movement data using the delayed app key.
Tracks best back/lay prices and top-of-book depth across active markets.
Identifies significant movement between polling cycles.

Mode:     DELAYED — data has variable latency. NOT FOR LIVE EXECUTION.
Platform: Betfair delayed (dev/research key)
Truth:    DELAYED DATA ONLY — do not use for order placement or live decisions.

Hard rules:
  - Never interprets delayed prices as executable live quotes.
  - Signals are clearly labeled DELAYED.
  - No order placement.
  - Requires ENABLE_BETFAIR_DELAYED=true + credentials.
"""

from typing import Any

from bots.base_research_bot import BaseResearchBot

# Minimum price movement (in decimal odds) to surface as a signal
_MIN_MOVE = 0.05
# Price range for tracking (filter out extreme odds)
_PRICE_MIN = 1.10
_PRICE_MAX = 20.0


def _extract_runner_best(runner: dict[str, Any]) -> tuple[float | None, float | None, float]:
    """
    Extract (best_back, best_lay, available_to_back) from a Betfair runner dict.
    Betfair runner format: {"availableToBack": [{"price": ..., "size": ...}], ...}
    """
    back_levels = runner.get("availableToBack") or runner.get("ex", {}).get("availableToBack", [])
    lay_levels = runner.get("availableToLay") or runner.get("ex", {}).get("availableToLay", [])

    best_back: float | None = None
    best_lay: float | None = None
    available_size = 0.0

    if back_levels and isinstance(back_levels, list):
        lvl = back_levels[0]
        if isinstance(lvl, dict):
            try:
                best_back = float(lvl.get("price", 0))
            except (TypeError, ValueError):
                pass
            try:
                available_size = float(lvl.get("size", 0))
            except (TypeError, ValueError):
                pass

    if lay_levels and isinstance(lay_levels, list):
        lvl = lay_levels[0]
        if isinstance(lvl, dict):
            try:
                best_lay = float(lvl.get("price", 0))
            except (TypeError, ValueError):
                pass

    return best_back, best_lay, available_size


class BetfairDelayedMirrorBot(BaseResearchBot):
    bot_id = "bot_betfair_delayed_mirror"
    display_name = "Betfair Delayed Mirror"
    platform = "betfair_delayed"
    mode = "DELAYED"
    signal_type = "delayed_mirror"
    delayed_only = True
    implemented = True

    DELAYED_NOTE = (
        "DELAYED — DEVELOPMENT ONLY. "
        "Betfair delayed app key data has variable latency. "
        "Prices are NOT live executable quotes. "
        "Do not use for order placement or real-time trading decisions."
    )

    def __init__(self, adapter=None):
        self.adapter = adapter
        # market_id → {runner_id → (back, lay)} from previous cycle
        self._prev_prices: dict[str, dict[str, tuple[float, float]]] = {}

    def run_one_cycle(self) -> dict[str, Any]:
        if self.adapter is None:
            return self.disabled_result(
                "Betfair delayed adapter is not wired. "
                "Set ENABLE_BETFAIR_DELAYED=true and provide credentials."
            )

        health = self.adapter.healthcheck()
        if not health.get("ok"):
            return self.disabled_result(
                health.get("degraded_reason", "Betfair delayed adapter unavailable.")
            )

        # Fetch active markets (Soccer by default for event type 1)
        markets_resp = self.adapter.list_markets(limit=10, event_type_ids=["1"])
        if not markets_resp.get("ok"):
            return self.disabled_result(
                markets_resp.get("degraded_reason", "Betfair delayed markets unavailable.")
            )

        raw_markets = (markets_resp.get("data") or {}).get("markets", [])
        if not raw_markets:
            return self.disabled_result(
                "Betfair delayed adapter returned no active markets. "
                "Delayed key may have limited event coverage."
            )

        best_result: dict[str, Any] | None = None
        best_move = 0.0

        for mkt in raw_markets[:8]:
            market_id = mkt.get("marketId") or mkt.get("id", "")
            if not market_id:
                continue

            # Fetch market book for price detail
            book_resp = self.adapter.get_market(market_id)
            if not book_resp.get("ok"):
                continue

            market_book = (book_resp.get("data") or {}).get("market_book")
            if not market_book:
                continue

            # market_book may be a list (Betfair returns list of market books)
            if isinstance(market_book, list) and market_book:
                book = market_book[0]
            elif isinstance(market_book, dict):
                book = market_book
            else:
                continue

            runners = book.get("runners", [])
            if not runners:
                continue

            prev = self._prev_prices.get(market_id, {})
            max_move_this_market = 0.0
            moved_runner: dict[str, Any] | None = None

            for runner in runners[:5]:
                runner_id = str(runner.get("selectionId", runner.get("id", "")))
                if not runner_id:
                    continue

                best_back, best_lay, avail = _extract_runner_best(runner)
                if best_back is None or not (_PRICE_MIN <= best_back <= _PRICE_MAX):
                    continue

                lay = best_lay if best_lay else best_back + 0.02
                prev_back, prev_lay = prev.get(runner_id, (None, None))
                move = abs(best_back - prev_back) if prev_back is not None else 0.0

                if move > max_move_this_market:
                    max_move_this_market = move
                    moved_runner = {
                        "runner_id": runner_id,
                        "runner_name": runner.get("description") or runner.get("runnerName") or runner_id,
                        "best_back": round(best_back, 3),
                        "best_lay": round(lay, 3),
                        "available_to_back": round(avail, 2),
                        "prev_back": round(prev_back, 3) if prev_back else None,
                        "move": round(move, 4),
                    }

                # Update snapshot
                prev[runner_id] = (best_back if best_back else 0.0, lay)

            self._prev_prices[market_id] = prev

            if moved_runner is None or max_move_this_market < _MIN_MOVE:
                # No meaningful movement — still emit a zero-signal for this market
                # but only if it improves on what we have
                if best_result is None:
                    mkt_name = (
                        mkt.get("marketName")
                        or mkt.get("event", {}).get("name", "")
                        or market_id
                    )
                    best_result = self.emit_signal(
                        title=f"Delayed Mirror — {mkt_name[:60]}",
                        summary=(
                            f"Tracking {len(runners)} runners. "
                            "No significant price movement this cycle. "
                            "Building baseline snapshots."
                        ),
                        confidence=0.0,
                        signal_taken=False,
                        degraded_reason="Price movement below threshold. Snapshot recorded.",
                        data={
                            "market_id": market_id,
                            "runner_count": len(runners),
                            "truth_note": self.DELAYED_NOTE,
                        },
                    )
                continue

            if max_move_this_market > best_move:
                best_move = max_move_this_market
                mkt_name = (
                    mkt.get("marketName")
                    or mkt.get("event", {}).get("name", "")
                    or market_id
                )
                taken = max_move_this_market >= _MIN_MOVE
                best_result = self.emit_signal(
                    title=f"Delayed Mirror Move — {mkt_name[:55]}",
                    summary=(
                        f"Runner '{moved_runner['runner_name']}': "
                        f"back {moved_runner['prev_back']} → {moved_runner['best_back']} "
                        f"(Δ {moved_runner['move']:+.3f}). "
                        f"Lay {moved_runner['best_lay']}. "
                        + ("Movement detected." if taken else "Below threshold.")
                    ),
                    confidence=min(0.99, max_move_this_market / 0.50),
                    signal_taken=taken,
                    degraded_reason="" if taken else f"Move {max_move_this_market:.4f} < threshold {_MIN_MOVE}.",
                    data={
                        "market_id": market_id,
                        "market_name": mkt_name,
                        "runner": moved_runner,
                        "truth_note": self.DELAYED_NOTE,
                    },
                )

        return best_result or self.disabled_result(
            "Betfair delayed adapter returned markets but no runner price data was readable this cycle."
        )
