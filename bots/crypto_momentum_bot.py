from __future__ import annotations

from typing import Any

from bots.base_research_bot import BaseResearchBot


class CryptoMomentumBot(BaseResearchBot):
    bot_id = "bot_crypto_momentum"
    display_name = "Crypto Momentum Signal"
    platform = "polymarket"
    mode = "RESEARCH"
    signal_type = "momentum"
    paper_only = True
    research_only = True
    implemented = True
    truth_label = "PUBLIC_DATA_ONLY"
    quality_tier = "B"
    risk_tier = "high"
    description = (
        "Tracks BTC/ETH/SOL price momentum via public CoinGecko, CoinCap, and Coinpaprika "
        "REST endpoints (no auth required). Combines price momentum with Alternative.me "
        "Fear & Greed Index and global market data. Flags open crypto prediction markets "
        "on Polymarket or Kalshi that may not have repriced."
    )
    edge_source = "Crypto price momentum + Fear & Greed divergence vs. prediction market pricing lag"
    opp_cadence_per_day = 3.0
    avg_hold_hours = 1.0
    fee_drag_bps = 150
    fill_rate = 0.50
    platforms = ["polymarket", "kalshi", "coingecko", "coincap", "coinpaprika", "fear_greed"]

    def __init__(self, adapter=None):
        self.adapter = adapter

    def run_one_cycle(self) -> dict[str, Any]:
        from adapters.coingecko import CoinGeckoAdapter
        from adapters.coincap import CoinCapAdapter
        from adapters.fear_greed import FearGreedAdapter
        from adapters.coinpaprika import CoinpaprikaAdapter

        cg = CoinGeckoAdapter()
        cc = CoinCapAdapter()
        fg = FearGreedAdapter()
        cp = CoinpaprikaAdapter()

        errors = []

        # --- CoinGecko: price + global + trending ---
        cg_prices = cg.get_price(coins=["BTC", "ETH", "SOL"])
        cg_global = cg.get_global()
        cg_trending = cg.get_trending()

        # --- CoinCap: top assets for cross-validation ---
        cc_top = cc.get_top_assets(limit=10)

        # --- Fear & Greed ---
        fg_signal = fg.get_sentiment_signal()

        # --- Coinpaprika: global ---
        cp_global = cp.get_global()

        # Build price snapshot
        prices: dict[str, dict] = {}
        if cg_prices.get("ok"):
            raw = cg_prices["data"]
            for coin_id, vals in raw.items():
                sym = {"bitcoin": "BTC", "ethereum": "ETH", "solana": "SOL"}.get(coin_id, coin_id.upper())
                prices[sym] = {
                    "price_usd": vals.get("usd"),
                    "change_24h_pct": vals.get("usd_24h_change"),
                    "volume_24h_usd": vals.get("usd_24h_vol"),
                    "market_cap_usd": vals.get("usd_market_cap"),
                    "source": "coingecko",
                }
        else:
            errors.append(f"coingecko_prices: {cg_prices.get('error', 'failed')}")

        # CoinCap cross-check prices
        cc_prices: dict[str, float] = {}
        if cc_top.get("ok"):
            for asset in cc_top["data"].get("assets", []):
                sym = asset.get("symbol", "")
                if sym in ("BTC", "ETH", "SOL"):
                    cc_prices[sym] = float(asset.get("price_usd") or 0)

        # Cross-source divergence check
        divergences = []
        for sym, cg_data in prices.items():
            cc_price = cc_prices.get(sym)
            cg_price = cg_data.get("price_usd") or 0
            if cc_price and cg_price and cg_price > 0:
                div_pct = abs(cc_price - cg_price) / cg_price * 100
                if div_pct > 0.5:
                    divergences.append({"symbol": sym, "coingecko": cg_price, "coincap": cc_price, "div_pct": round(div_pct, 3)})

        # Fear & Greed
        fg_data = fg_signal.get("data", {}) if fg_signal.get("ok") else {}
        fg_value = fg_data.get("current", {}).get("value") if fg_data.get("current") else None
        fg_direction = fg_data.get("signal_direction", "neutral")
        fg_strength = float(fg_data.get("signal_strength", 0.0) or 0)

        # Global market metrics
        global_data = {}
        if cg_global.get("ok"):
            global_data = cg_global["data"]
        elif cp_global.get("ok"):
            global_data = cp_global["data"]

        # Momentum calculation
        momentum_scores = [d.get("change_24h_pct") or 0 for d in prices.values()]
        avg_momentum = sum(momentum_scores) / len(momentum_scores) if momentum_scores else 0

        # Signal logic
        confidence = 0.0
        signal_direction = "neutral"
        signal_taken = False
        degraded_reason = ""

        if not prices:
            degraded_reason = "Could not fetch crypto prices from any source."
        else:
            if avg_momentum > 3.0 and fg_direction in ("neutral", "bearish_contrarian"):
                signal_direction = "bullish_momentum"
                confidence = min(0.40 + fg_strength * 0.1 + min(avg_momentum / 20, 0.1), 0.60)
            elif avg_momentum < -3.0 and fg_direction in ("neutral", "bullish_contrarian"):
                signal_direction = "bearish_momentum"
                confidence = min(0.40 + fg_strength * 0.1 + min(abs(avg_momentum) / 20, 0.1), 0.60)
            elif fg_direction == "bullish_contrarian" and fg_strength > 0.5:
                signal_direction = "contrarian_buy"
                confidence = 0.28 + fg_strength * 0.12
            elif fg_direction == "bearish_contrarian" and fg_strength > 0.5:
                signal_direction = "contrarian_sell"
                confidence = 0.28 + fg_strength * 0.12

            if not degraded_reason and confidence == 0.0:
                degraded_reason = "No significant momentum or F&G divergence detected in this scan window."

        trending_coins = []
        if cg_trending.get("ok"):
            trending_coins = [c.get("symbol") for c in cg_trending["data"].get("trending", [])[:5]]

        btc_chg = prices.get("BTC", {}).get("change_24h_pct")
        eth_chg = prices.get("ETH", {}).get("change_24h_pct")
        sol_chg = prices.get("SOL", {}).get("change_24h_pct")

        def fmt(v: Any) -> str:
            return f"{v:.2f}%" if isinstance(v, (int, float)) else "N/A"

        summary = (
            f"Crypto momentum scan complete. "
            f"BTC 24h: {fmt(btc_chg)} | ETH: {fmt(eth_chg)} | SOL: {fmt(sol_chg)}. "
            f"Fear & Greed: {fg_value} ({fg_data.get('current', {}).get('classification', 'N/A') if fg_data.get('current') else 'N/A'}). "
            f"Trending: {', '.join(str(t) for t in trending_coins)}. "
            f"Signal: {signal_direction}. "
            f"Sources: CoinGecko, CoinCap, Coinpaprika, Alternative.me F&G."
        ) if prices else "Failed to fetch crypto price data from all sources."

        return self.emit_signal(
            title="Crypto Momentum Signal",
            summary=summary,
            confidence=confidence,
            signal_taken=signal_taken,
            degraded_reason=degraded_reason,
            platform_truth_label="PUBLIC_DATA_ONLY",
            data={
                "prices": prices,
                "fear_greed_value": fg_value,
                "fear_greed_classification": (fg_data.get("current", {}).get("classification") if fg_data.get("current") else None),
                "fear_greed_direction": fg_direction,
                "fear_greed_strength": fg_strength,
                "avg_momentum_24h_pct": round(avg_momentum, 3),
                "signal_direction": signal_direction,
                "trending_coins": trending_coins,
                "btc_dominance_pct": global_data.get("btc_dominance_pct"),
                "total_market_cap_usd": global_data.get("total_market_cap_usd"),
                "market_cap_change_24h_pct": global_data.get("market_cap_change_24h_pct"),
                "price_divergences": divergences,
                "sources": [
                    "api.coingecko.com/api/v3",
                    "api.coincap.io/v2",
                    "api.coinpaprika.com/v1",
                    "api.alternative.me/fng",
                ],
                "errors": errors,
            },
        )
