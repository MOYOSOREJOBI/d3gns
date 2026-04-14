from __future__ import annotations

import uuid
import requests

from bots.base_research_bot import BaseResearchBot
from models.proposal import Proposal


class FundingRateArbBot(BaseResearchBot):
    bot_id = "bot_funding_rate_arb_paper"
    display_name = "Funding Rate Arb"
    platform = "binance"
    mode = "PAPER"
    signal_type = "funding_rate_arb"
    paper_only = True
    implemented = True

    def run_one_cycle(self) -> dict:
        try:
            funding = requests.get("https://fapi.binance.com/fapi/v1/premiumIndex", timeout=8).json()
            if not isinstance(funding, list):
                return self.disabled_result("Funding rate feed did not return a market list.")
            candidates = []
            for row in funding:
                try:
                    rate = float(row.get("lastFundingRate", 0) or 0)
                except Exception:
                    continue
                if abs(rate) >= 0.0003:
                    candidates.append((abs(rate), row))
            if not candidates:
                return self.disabled_result("No perpetual funding rate exceeded the 0.03% threshold.")
            _, best = max(candidates, key=lambda item: item[0])
            symbol = best.get("symbol", "BTCUSDT")
            spot = requests.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={"ids": "bitcoin", "vs_currencies": "usd"},
                timeout=8,
            ).json()
            spot_price = ((spot.get("bitcoin") or {}).get("usd"))
            rate = float(best.get("lastFundingRate", 0) or 0)
            carry_pct = rate * 100
            return self.emit_signal(
                title=f"{symbol} funding spread",
                summary=f"{symbol} funding rate is {carry_pct:.3f}% — candidate long spot / short perp carry trade.",
                confidence=min(0.95, abs(rate) * 2500),
                signal_taken=abs(rate) >= 0.0003,
                data={
                    "symbol": symbol,
                    "funding_rate": rate,
                    "mark_price": best.get("markPrice"),
                    "index_price": best.get("indexPrice"),
                    "spot_price_usd": spot_price,
                    "strategy": "long_spot_short_perp",
                },
                factor_contributions={"funding_rate": round(abs(rate) * 100, 4)},
                platform_truth_label="PAPER",
            )
        except Exception as exc:
            return self.disabled_result(f"Funding rate arb data fetch failed: {exc}")

    def generate_proposal(self, context: dict[str, object] | None = None) -> Proposal | None:
        result = self.run_one_cycle()
        if not result.get("signal_taken"):
            return None
        data = result.get("data", {}) or {}
        rate = abs(float(data.get("funding_rate", 0) or 0))
        edge_bps = rate * 10000
        edge_post_fee = edge_bps - 18.0
        if edge_post_fee <= 0:
            return None
        side = "SELL" if float(data.get("funding_rate", 0) or 0) > 0 else "BUY"
        ctx = context or {}
        return Proposal(
            proposal_id=f"p_{uuid.uuid4().hex[:12]}",
            bot_id=self.bot_id,
            platform="binance",
            market_id=str(data.get("symbol", "")),
            side=side,
            confidence=round(float(result.get("confidence", 0) or 0), 4),
            edge_bps=round(edge_bps, 2),
            edge_post_fee_bps=round(edge_post_fee, 2),
            expected_hold_s=8 * 3600,
            max_slippage_bps=25,
            correlation_key=str(data.get("symbol", "")),
            reason_code="funding_rate_arb",
            runtime_mode=str(ctx.get("runtime_mode", "paper")).lower(),
            truth_label="PAPER — NO REAL ORDER",
            metadata={
                "symbol": data.get("symbol", ""),
                "funding_rate": data.get("funding_rate", 0),
                "mark_price": data.get("mark_price"),
                "index_price": data.get("index_price"),
                "spot_price_usd": data.get("spot_price_usd"),
            },
        )
