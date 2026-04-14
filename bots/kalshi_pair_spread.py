from __future__ import annotations

from collections import defaultdict

from bots.base_research_bot import BaseResearchBot


def _implied_yes_price(market: dict) -> float | None:
    for key in ("last_price", "last_trade_price", "yes_ask", "yes_bid"):
        value = market.get(key)
        if value is None:
            continue
        try:
            numeric = float(value)
            return numeric / 100.0 if numeric > 1 else numeric
        except Exception:
            continue
    return None


class KalshiPairSpreadBot(BaseResearchBot):
    bot_id = "bot_kalshi_pair_spread_paper"
    display_name = "Kalshi Pair Spread"
    platform = "kalshi_public"
    mode = "PAPER"
    signal_type = "related_market_spread"
    paper_only = True
    implemented = True

    def __init__(self, adapter):
        self.adapter = adapter

    def run_one_cycle(self) -> dict:
        health = self.adapter.healthcheck()
        if not health.get("ok"):
            return self.disabled_result(health.get("degraded_reason"))

        markets_resp = self.adapter.list_markets(limit=40)
        markets = (markets_resp.get("data") or {}).get("markets", [])
        grouped = defaultdict(list)

        for market in markets:
            family = (
                market.get("event_ticker")
                or market.get("series_ticker")
                or market.get("series")
                or "misc"
            )
            implied = _implied_yes_price(market)
            if implied is None:
                continue
            grouped[family].append((market, implied))

        best_result = None
        for family, entries in grouped.items():
            if len(entries) < 2:
                continue
            entries = sorted(entries, key=lambda item: item[1])
            low_market, low_price = entries[0]
            high_market, high_price = entries[-1]
            spread = high_price - low_price
            result = self.emit_signal(
                title=f"{family} related-market spread",
                summary=(
                    f"{low_market.get('ticker') or low_market.get('id')} at {low_price:.3f} versus "
                    f"{high_market.get('ticker') or high_market.get('id')} at {high_price:.3f}."
                ),
                confidence=min(0.99, spread * len(entries)),
                signal_taken=spread >= 0.12,
                degraded_reason="" if spread >= 0.12 else "Related Kalshi markets did not show a wide enough implied spread.",
                data={
                    "family": family,
                    "low_market_id": low_market.get("ticker") or low_market.get("id"),
                    "high_market_id": high_market.get("ticker") or high_market.get("id"),
                    "low_price": round(low_price, 4),
                    "high_price": round(high_price, 4),
                    "spread": round(spread, 4),
                    "market_count": len(entries),
                },
            )
            if not best_result or result["confidence"] > best_result["confidence"]:
                best_result = result

        return best_result or self.disabled_result("No related Kalshi market family produced a usable spread signal.")

