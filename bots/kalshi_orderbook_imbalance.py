from __future__ import annotations

import uuid
from typing import Any

from bots.base_research_bot import BaseResearchBot
from models.proposal import Proposal


def _extract_levels(payload: dict[str, Any], candidates: list[str]) -> list[dict[str, float]]:
    for key in candidates:
        side = payload.get(key)
        if isinstance(side, list):
            levels = []
            for row in side[:5]:
                price = row.get("price") if isinstance(row, dict) else None
                size = row.get("quantity") if isinstance(row, dict) else None
                if size is None and isinstance(row, dict):
                    size = row.get("size") or row.get("qty") or row.get("volume")
                try:
                    levels.append({"price": float(price or 0), "size": float(size or 0)})
                except Exception:
                    continue
            if levels:
                return levels
    return []


class KalshiOrderbookImbalanceBot(BaseResearchBot):
    bot_id = "bot_kalshi_orderbook_imbalance_paper"
    display_name = "Kalshi Orderbook Imbalance"
    platform = "kalshi_public"
    mode = "PAPER"
    signal_type = "orderbook_imbalance"
    paper_only = True
    implemented = True

    def __init__(self, adapter):
        self.adapter = adapter

    def run_one_cycle(self) -> dict[str, Any]:
        health = self.adapter.healthcheck()
        if not health.get("ok"):
            return self.disabled_result(health.get("degraded_reason"))

        markets_resp = self.adapter.list_markets(limit=10)
        markets = (markets_resp.get("data") or {}).get("markets", [])
        best_result = None

        for market in markets[:10]:
            market_id = (
                market.get("ticker")
                or market.get("market_ticker")
                or market.get("id")
                or market.get("symbol")
            )
            if not market_id:
                continue
            orderbook_resp = self.adapter.get_orderbook(market_id)
            payload = (orderbook_resp.get("data") or {}).get("orderbook", {})
            if not isinstance(payload, dict):
                payload = {}
            yes_levels = _extract_levels(payload, ["yes", "yes_levels", "buy_yes"])
            no_levels = _extract_levels(payload, ["no", "no_levels", "buy_no"])
            if not yes_levels or not no_levels:
                continue

            yes_top = sum(level["size"] for level in yes_levels[:3])
            no_top = sum(level["size"] for level in no_levels[:3])
            total = max(yes_top + no_top, 1.0)
            imbalance = (yes_top - no_top) / total
            best_yes = max(level["price"] for level in yes_levels[:3]) / 100.0
            best_no = max(level["price"] for level in no_levels[:3]) / 100.0
            spread_width = max(0.0, 1.0 - (best_yes + best_no))
            score = abs(imbalance) * max(0.01, 1.0 - min(spread_width, 0.6))

            result = self.emit_signal(
                title=f"{market.get('title') or market.get('subtitle') or market_id}",
                summary=(
                    f"YES top-of-book {yes_top:.0f} vs NO {no_top:.0f}. "
                    f"Imbalance {imbalance:+.2f}, spread width {spread_width:.3f}."
                ),
                confidence=min(0.99, score),
                signal_taken=abs(imbalance) >= 0.25 and spread_width <= 0.15,
                degraded_reason="" if abs(imbalance) >= 0.25 else "Orderbook imbalance did not clear the signal threshold.",
                data={
                    "market_id": market_id,
                    "best_bid_yes_implied": round(best_yes, 4),
                    "best_bid_no_implied": round(best_no, 4),
                    "imbalance_score": round(imbalance, 4),
                    "spread_width": round(spread_width, 4),
                    "signal_side": "yes" if imbalance > 0 else "no",
                },
            )
            if not best_result or result["confidence"] > best_result["confidence"]:
                best_result = result

        return best_result or self.disabled_result("Kalshi orderbook data was unavailable for the current market sample.")

    def generate_proposal(self, context: dict[str, Any] | None = None) -> Proposal | None:
        result = self.run_one_cycle()
        if not result.get("signal_taken"):
            return None
        data = result.get("data", {}) or {}
        imbalance = abs(float(data.get("imbalance_score", 0) or 0))
        spread_width = float(data.get("spread_width", 0) or 0)
        edge_bps = imbalance * 1000
        edge_post_fee = edge_bps - (spread_width * 10000) - 45.0
        if edge_post_fee <= 0:
            return None
        ctx = context or {}
        signal_side = str(data.get("signal_side", "yes") or "yes").upper()
        return Proposal(
            proposal_id=f"p_{uuid.uuid4().hex[:12]}",
            bot_id=self.bot_id,
            platform="kalshi_public",
            market_id=str(data.get("market_id", "")),
            side="BUY_YES" if signal_side == "YES" else "BUY_NO",
            confidence=round(float(result.get("confidence", 0) or 0), 4),
            edge_bps=round(edge_bps, 2),
            edge_post_fee_bps=round(edge_post_fee, 2),
            expected_hold_s=1800,
            max_slippage_bps=max(25.0, spread_width * 10000),
            correlation_key="kalshi_orderbook",
            reason_code="orderbook_imbalance",
            runtime_mode=str(ctx.get("runtime_mode", "paper")).lower(),
            truth_label="PAPER — NO REAL ORDER",
            metadata={
                "imbalance_score": data.get("imbalance_score"),
                "spread_width": spread_width,
                "best_bid_yes_implied": data.get("best_bid_yes_implied"),
                "best_bid_no_implied": data.get("best_bid_no_implied"),
            },
        )
