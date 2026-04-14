from __future__ import annotations

import requests

from bots.base_research_bot import BaseResearchBot


_ANCHORS: dict[str, float] = {}


class GridTraderBot(BaseResearchBot):
    bot_id = "bot_grid_trader_paper"
    display_name = "Grid Trader"
    platform = "crypto"
    mode = "PAPER"
    signal_type = "grid_trade"
    paper_only = True
    implemented = True

    def run_one_cycle(self) -> dict:
        try:
            payload = requests.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={"ids": "bitcoin", "vs_currencies": "usd"},
                timeout=8,
            ).json()
            price = float((payload.get("bitcoin") or {}).get("usd") or 0)
            if price <= 0:
                return self.disabled_result("Spot price feed returned no usable BTC price.")
            anchor = _ANCHORS.setdefault("bitcoin", price)
            grid_step_pct = 0.01
            delta_pct = (price - anchor) / anchor if anchor else 0.0
            action = "hold"
            if delta_pct >= grid_step_pct:
                action = "sell_grid"
                _ANCHORS["bitcoin"] = price
            elif delta_pct <= -grid_step_pct:
                action = "buy_grid"
                _ANCHORS["bitcoin"] = price
            return self.emit_signal(
                title="BTC grid monitor",
                summary=f"BTC price {price:.2f} vs anchor {anchor:.2f} — {action.replace('_', ' ')}",
                confidence=min(0.9, abs(delta_pct) * 30),
                signal_taken=action != "hold",
                degraded_reason="" if action != "hold" else "Price remains inside the active grid band.",
                data={
                    "asset": "bitcoin",
                    "price_usd": price,
                    "anchor_price_usd": anchor,
                    "delta_pct": round(delta_pct, 4),
                    "grid_step_pct": grid_step_pct,
                    "action": action,
                },
                factor_contributions={"grid_distance": round(abs(delta_pct), 4)},
                platform_truth_label="PAPER",
            )
        except Exception as exc:
            return self.disabled_result(f"Grid trader price fetch failed: {exc}")
