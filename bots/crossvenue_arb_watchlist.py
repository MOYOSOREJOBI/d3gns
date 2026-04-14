from __future__ import annotations

"""
Bot 12 — Crossvenue Arb Watchlist.

Aggregates comparable market pairs from all three supported public platforms
(Kalshi, Polymarket, OddsAPI) and surfaces the highest-confidence crossvenue
implied probability spreads for watchlist monitoring.

This is a WATCHLIST ONLY bot. It never produces a trade signal.
No realized PnL or execution probability is computed or stored.

Mode:     WATCHLIST ONLY — no order placement, no realized PnL.
Platforms: Kalshi (public) + Polymarket (public) + OddsAPI
Truth:    PUBLIC DATA ONLY / WATCHLIST ONLY

Hard rules:
  - signal_taken is always False.
  - Spreads are informational only. Event equivalence requires manual verification.
  - Active pairs (≥0.70 confidence) are surfaced but still labeled VERIFY BEFORE USE.
  - No ordering of events by profit potential. No PnL projection.
  - Requires at least 2 adapters to be available; degrades gracefully with 1 or 0.
"""

from typing import Any

from bots.base_research_bot import BaseResearchBot
from services.crossvenue_matcher import (
    WATCHLIST_THRESHOLD,
    match_market_lists,
    summarize_matches,
)

_WATCHLIST_NOTE = (
    "WATCHLIST ONLY — PUBLIC DATA. "
    "Crossvenue pairs are for monitoring only. "
    "No implied profit, realized PnL, or order placement is associated with this watchlist. "
    "Active pairs require manual event-equivalence verification before any comparison."
)


def _get_implied(market: dict[str, Any], platform: str) -> float | None:
    """Extract best YES/home implied probability from a market dict."""
    if platform == "kalshi_public":
        for key in ("yes_ask", "yes_bid", "last_price", "last_trade_price"):
            val = market.get(key)
            if val is None:
                continue
            try:
                f = float(val)
                return f / 100.0 if f > 1.0 else f
            except (TypeError, ValueError):
                pass
    elif platform == "polymarket_public":
        for key in ("lastTradePrice", "bestBid", "midpoint"):
            val = market.get(key)
            if val is None:
                continue
            try:
                f = float(val)
                return f / 100.0 if f > 1.0 else f
            except (TypeError, ValueError):
                pass
        prices = market.get("outcomePrices")
        if isinstance(prices, list) and prices:
            try:
                return float(prices[0])
            except (TypeError, ValueError):
                pass
    elif platform == "oddsapi":
        # OddsAPI events have nested bookmakers; extract median implied
        from statistics import median as _median
        implieds = []
        for bm in market.get("bookmakers", []):
            for mkt in bm.get("markets", []):
                if mkt.get("key") != "h2h":
                    continue
                for outcome in mkt.get("outcomes", []):
                    try:
                        price = float(outcome.get("price", 0))
                        if price > 1.0:
                            implieds.append(round(1.0 / price, 5))
                    except (TypeError, ValueError):
                        pass
        if implieds:
            return round(_median(implieds[:6]), 5)
    return None


class CrossvenueArbWatchlistBot(BaseResearchBot):
    bot_id = "bot_crossvenue_arb_watchlist"
    display_name = "Crossvenue Arb Watchlist"
    platform = "crossvenue"
    mode = "WATCHLIST ONLY"
    signal_type = "arb_watchlist"
    watchlist_only = True
    implemented = True

    def __init__(self, kalshi_adapter=None, poly_adapter=None, oddsapi_adapter=None):
        self.kalshi_adapter = kalshi_adapter
        self.poly_adapter = poly_adapter
        self.oddsapi_adapter = oddsapi_adapter

    def run_one_cycle(self) -> dict[str, Any]:
        adapters_ok = []

        # Collect available markets from each healthy adapter
        kalshi_markets: list[dict] = []
        poly_markets: list[dict] = []
        oddsapi_events: list[dict] = []

        if self.kalshi_adapter is not None:
            h = self.kalshi_adapter.healthcheck()
            if h.get("ok"):
                resp = self.kalshi_adapter.list_markets(limit=30)
                if resp.get("ok"):
                    kalshi_markets = (resp.get("data") or {}).get("markets", [])
                    if kalshi_markets:
                        adapters_ok.append("kalshi_public")

        if self.poly_adapter is not None:
            h = self.poly_adapter.healthcheck()
            if h.get("ok"):
                resp = self.poly_adapter.list_markets(limit=30, active="true")
                if resp.get("ok"):
                    poly_markets = (resp.get("data") or {}).get("markets", [])
                    if poly_markets:
                        adapters_ok.append("polymarket_public")

        if self.oddsapi_adapter is not None:
            h = self.oddsapi_adapter.healthcheck()
            if h.get("ok"):
                resp = self.oddsapi_adapter.list_markets(
                    sport="upcoming", regions="us", markets="h2h", oddsFormat="decimal"
                )
                if resp.get("ok"):
                    oddsapi_events = (resp.get("data") or {}).get("events", [])
                    if oddsapi_events:
                        adapters_ok.append("oddsapi")

        if not adapters_ok:
            return self.disabled_result(
                "No crossvenue adapters are available. "
                "Enable at least two of: Kalshi, Polymarket, OddsAPI."
            )

        if len(adapters_ok) < 2:
            return self.emit_signal(
                title="Crossvenue Watchlist — Only one platform available",
                summary=(
                    f"Only {adapters_ok[0]} is available. "
                    "Crossvenue comparison requires at least 2 platforms."
                ),
                confidence=0.0,
                signal_taken=False,
                degraded_reason=(
                    f"Only {adapters_ok[0]} is responding. "
                    "Enable additional platforms for crossvenue matching."
                ),
                data={
                    "available_platforms": adapters_ok,
                    "truth_note": _WATCHLIST_NOTE,
                },
            )

        # Run crossvenue matching across all available platform pairs
        all_pairs: list[dict[str, Any]] = []

        if kalshi_markets and poly_markets:
            pairs = match_market_lists(
                kalshi_markets, "kalshi_public",
                poly_markets, "polymarket_public",
                title_key_a="title",
                title_key_b="question",
                id_key_a="ticker",
                id_key_b="conditionId",
            )
            all_pairs.extend(pairs)

        if kalshi_markets and oddsapi_events:
            pairs = match_market_lists(
                kalshi_markets, "kalshi_public",
                oddsapi_events, "oddsapi",
                title_key_a="title",
                title_key_b="away_team",   # best available title key for OddsAPI
                id_key_a="ticker",
                id_key_b="id",
            )
            all_pairs.extend(pairs)

        if poly_markets and oddsapi_events:
            pairs = match_market_lists(
                poly_markets, "polymarket_public",
                oddsapi_events, "oddsapi",
                title_key_a="question",
                title_key_b="away_team",
                id_key_a="conditionId",
                id_key_b="id",
            )
            all_pairs.extend(pairs)

        if not all_pairs:
            return self.disabled_result(
                f"No crossvenue pairs found above the watchlist threshold "
                f"({WATCHLIST_THRESHOLD:.0%}). "
                f"Available platforms: {', '.join(adapters_ok)}."
            )

        # Deduplicate by (id_a, id_b) keeping best confidence
        seen: dict[tuple, dict] = {}
        for pair in all_pairs:
            key = (pair.get("id_a", ""), pair.get("id_b", ""))
            if key not in seen or pair["composite_confidence"] > seen[key]["composite_confidence"]:
                seen[key] = pair
        deduped = sorted(seen.values(), key=lambda p: p["composite_confidence"], reverse=True)

        # Enrich top pairs with implied prices
        enriched: list[dict[str, Any]] = []
        for pair in deduped[:10]:
            platform_a = pair.get("platform_a", "")
            platform_b = pair.get("platform_b", "")

            # Find market objects by id
            mkt_a = _find_market(pair.get("id_a", ""), kalshi_markets, poly_markets, oddsapi_events, platform_a)
            mkt_b = _find_market(pair.get("id_b", ""), kalshi_markets, poly_markets, oddsapi_events, platform_b)

            implied_a = _get_implied(mkt_a, platform_a) if mkt_a else None
            implied_b = _get_implied(mkt_b, platform_b) if mkt_b else None

            enriched.append({
                **pair,
                "implied_a": round(implied_a, 4) if implied_a is not None else None,
                "implied_b": round(implied_b, 4) if implied_b is not None else None,
                "implied_spread": (
                    round(abs(implied_a - implied_b), 4)
                    if implied_a is not None and implied_b is not None
                    else None
                ),
            })

        summary = summarize_matches(deduped)
        top = enriched[0]

        active_count = summary["active_pairs"]
        watchlist_count = summary["watchlist_pairs"]

        return self.emit_signal(
            title=(
                f"Crossvenue Watchlist — {active_count} active, "
                f"{watchlist_count} watching"
            ),
            summary=(
                f"Top pair: '{top.get('title_a', '?')}' ({top.get('platform_a', '?')}) "
                f"↔ '{top.get('title_b', '?')}' ({top.get('platform_b', '?')}). "
                f"Confidence {top['composite_confidence']:.2f}. "
                + (
                    f"Implied spread {top['implied_spread']:.4f}. "
                    if top.get("implied_spread") is not None else ""
                )
                + f"Platforms: {', '.join(adapters_ok)}."
            ),
            confidence=0.0,   # WATCHLIST ONLY — never a trade confidence
            signal_taken=False,
            degraded_reason="",
            data={
                "watchlist_summary": summary,
                "top_pairs": enriched[:5],
                "available_platforms": adapters_ok,
                "platform_market_counts": {
                    "kalshi": len(kalshi_markets),
                    "polymarket": len(poly_markets),
                    "oddsapi": len(oddsapi_events),
                },
                "truth_note": _WATCHLIST_NOTE,
            },
        )


def _find_market(
    market_id: str,
    kalshi: list[dict],
    poly: list[dict],
    oddsapi: list[dict],
    platform: str,
) -> dict[str, Any] | None:
    """Find a market dict by ID from the appropriate list."""
    if platform == "kalshi_public":
        for m in kalshi:
            if m.get("ticker") == market_id or m.get("id") == market_id:
                return m
    elif platform == "polymarket_public":
        for m in poly:
            if m.get("conditionId") == market_id or m.get("id") == market_id:
                return m
    elif platform == "oddsapi":
        for m in oddsapi:
            if m.get("id") == market_id:
                return m
    return None
