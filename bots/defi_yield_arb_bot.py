from __future__ import annotations
from typing import Any
from bots.base_research_bot import BaseResearchBot


class DefiYieldArbBot(BaseResearchBot):
    bot_id = "bot_defi_yield_arb"
    display_name = "DeFi Yield Arbitrage"
    platform = "polymarket"
    mode = "RESEARCH"
    signal_type = "yield_dislocation"
    paper_only = True
    research_only = True
    implemented = True
    truth_label = "PUBLIC_DATA_ONLY"
    quality_tier = "C"
    risk_tier = "high"
    description = (
        "Monitors DeFi protocol yields via DeFiLlama public API (no auth). "
        "When TVL shifts sharply or stablecoins depeg, flags prediction market "
        "opportunities on DeFi/crypto topics. Also tracks top yield opportunities "
        "and DEX volume momentum. Research signal only — no on-chain execution."
    )
    edge_source = "DeFi yield anomalies and TVL shifts as early signals for prediction markets"
    opp_cadence_per_day = 1.0
    avg_hold_hours = 3.0
    fee_drag_bps = 120
    fill_rate = 0.40
    platforms = ["polymarket", "defillama"]

    def __init__(self, adapter=None):
        self.adapter = adapter

    def run_one_cycle(self) -> dict[str, Any]:
        from adapters.defillama import DeFiLlamaAdapter

        llama = DeFiLlamaAdapter()
        errors: list[str] = []
        source_status: dict[str, str] = {}

        # --- Global TVL ---
        global_tvl = None
        tvl_change_24h = None
        global_res = llama.get_global_tvl()
        if global_res.get("ok"):
            gd = global_res["data"]
            global_tvl = gd.get("total_tvl_usd")
            tvl_change_24h = gd.get("change_24h_pct")
            source_status["global_tvl"] = "live"
        else:
            errors.append(f"global_tvl: {global_res.get('error')}")
            source_status["global_tvl"] = "error"

        # --- Top Protocols ---
        top_protocols = None
        protocols_res = llama.get_top_protocols(limit=15)
        if protocols_res.get("ok"):
            top_protocols = protocols_res["data"].get("protocols", [])
            source_status["top_protocols"] = "live"
        else:
            errors.append(f"top_protocols: {protocols_res.get('error')}")
            source_status["top_protocols"] = "error"

        # --- Top Yields ---
        top_yields = None
        yields_res = llama.get_top_yields(min_apy=5.0, limit=10)
        if yields_res.get("ok"):
            top_yields = yields_res["data"].get("pools", [])
            source_status["top_yields"] = "live"
        else:
            errors.append(f"top_yields: {yields_res.get('error')}")
            source_status["top_yields"] = "error"

        # --- Stablecoins (depeg detection) ---
        depeg_flags = []
        stablecoins_res = llama.get_stablecoins()
        if stablecoins_res.get("ok"):
            depeg_flags = stablecoins_res["data"].get("depeg_alerts", [])
            source_status["stablecoins"] = "live"
        else:
            errors.append(f"stablecoins: {stablecoins_res.get('error')}")
            source_status["stablecoins"] = "error"

        # --- DEX Volumes ---
        dex_24h_volume = None
        dex_res = llama.get_dex_overview()
        if dex_res.get("ok"):
            dex_24h_volume = dex_res["data"].get("total_24h_volume_usd")
            source_status["dex_volumes"] = "live"
        else:
            errors.append(f"dex: {dex_res.get('error')}")
            source_status["dex_volumes"] = "error"

        # --- Market Signal ---
        market_signal = None
        signal_res = llama.get_market_signal()
        if signal_res.get("ok"):
            market_signal = signal_res["data"]
            source_status["market_signal"] = "live"
        else:
            source_status["market_signal"] = "error"

        live_count = sum(1 for v in source_status.values() if v == "live")

        # ── Signal logic ──────────────────────────────────────────────────────
        confidence = 0.0
        signal_taken = "neutral"
        degraded_reason = ""
        anomalies: list[str] = []

        if live_count == 0:
            degraded_reason = "All DeFiLlama sources failed."
        else:
            # TVL shock: >5% drop in 24h = bearish for DeFi markets
            if tvl_change_24h is not None:
                if tvl_change_24h < -5.0:
                    anomalies.append(f"TVL_CRASH:{tvl_change_24h:.1f}% 24h")
                    confidence += 0.25
                    signal_taken = "bearish"
                elif tvl_change_24h > 5.0:
                    anomalies.append(f"TVL_SURGE:{tvl_change_24h:.1f}% 24h")
                    confidence += 0.20
                    signal_taken = "bullish"

            # Stablecoin depeg = systemic risk
            if depeg_flags:
                for d in depeg_flags:
                    anomalies.append(f"DEPEG:{d.get('symbol')}@{d.get('price')}")
                confidence += 0.30 * min(len(depeg_flags), 2)
                signal_taken = "bearish"

            # Market signal from composite
            if market_signal:
                risk = market_signal.get("risk_signal", "neutral")
                composite = market_signal.get("composite_risk_score", 0)
                if risk == "elevated_risk":
                    confidence += 0.15
                    signal_taken = "bearish" if signal_taken == "neutral" else signal_taken
                elif risk == "healthy_defi":
                    confidence += 0.10
                    signal_taken = "bullish" if signal_taken == "neutral" else signal_taken

            confidence = min(confidence, 0.75)
            if not anomalies:
                degraded_reason = "No significant DeFi anomaly — TVL stable, no stablecoin depegs."

        # Build summary
        tvl_str = f"${global_tvl / 1e9:.1f}B" if global_tvl else "N/A"
        change_str = f"{tvl_change_24h:+.1f}%" if tvl_change_24h is not None else "N/A"
        dex_str = f"${dex_24h_volume / 1e9:.1f}B" if dex_24h_volume else "N/A"
        top_yield_str = ""
        if top_yields:
            ty = top_yields[0]
            top_yield_str = f"Top yield: {ty.get('symbol', '')} on {ty.get('project', '')} {ty.get('apy', 0):.1f}% APY"

        depeg_str = f"DEPEG ALERTS: {[d.get('symbol') for d in depeg_flags]}" if depeg_flags else "No depegs"

        summary = (
            f"DeFiLlama scan. Total TVL: {tvl_str} ({change_str} 24h). "
            f"DEX volume 24h: {dex_str}. {depeg_str}. "
            f"{top_yield_str}. "
            f"Anomalies: {anomalies or 'none'}. "
            f"Signal: {signal_taken}."
        )

        return self.emit_signal(
            title="DeFi Yield Arbitrage Signal",
            summary=summary,
            confidence=confidence,
            signal_taken=signal_taken,
            degraded_reason=degraded_reason,
            platform_truth_label="PUBLIC_DATA_ONLY",
            data={
                "global_tvl_usd": global_tvl,
                "tvl_change_24h_pct": tvl_change_24h,
                "dex_24h_volume_usd": dex_24h_volume,
                "top_protocols": top_protocols,
                "top_yields": top_yields,
                "depeg_alerts": depeg_flags,
                "market_signal": market_signal,
                "anomalies": anomalies,
                "source_status": source_status,
                "errors": errors,
                "sources": [
                    "api.llama.fi (no auth)",
                    "yields.llama.fi (no auth)",
                    "stablecoins.llama.fi (no auth)",
                ],
            },
        )
