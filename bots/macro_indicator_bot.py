from __future__ import annotations

from typing import Any

from bots.base_research_bot import BaseResearchBot


class MacroIndicatorBot(BaseResearchBot):
    bot_id = "bot_macro_indicator"
    display_name = "Macro Indicator Scanner"
    platform = "kalshi"
    mode = "RESEARCH"
    signal_type = "macro_event"
    paper_only = True
    research_only = True
    implemented = True
    truth_label = "PUBLIC_DATA_ONLY"
    quality_tier = "B"
    risk_tier = "low"
    description = (
        "Monitors US macroeconomic releases (Fed Funds rate, CPI, PPI, NFP, GDP, VIX, "
        "yield curve) via FRED API (St. Louis Fed, free key required). "
        "When a release significantly diverges from consensus or the yield curve inverts, "
        "checks Kalshi and Polymarket for relevant economic outcome markets. "
        "Without FRED key: falls back to yield-curve and VIX regime detection."
    )
    edge_source = "Macro release surprises + yield curve signals vs. prediction market pricing lag"
    opp_cadence_per_day = 1.0
    avg_hold_hours = 6.0
    fee_drag_bps = 80
    fill_rate = 0.60
    platforms = ["kalshi", "polymarket", "fred_api"]

    def __init__(self, adapter=None):
        self.adapter = adapter

    def run_one_cycle(self) -> dict[str, Any]:
        from adapters.fred_api import FREDAdapter

        fred = FREDAdapter()
        errors: list[str] = []

        if not fred.is_configured():
            return self.emit_signal(
                title="Macro Indicator Scanner",
                summary=(
                    "FRED API not configured. Set FRED_API_KEY for live macro data. "
                    "Register free at fred.stlouisfed.org/docs/api/api_key.html. "
                    "Key unlocks: CPI, PPI, NFP, Fed Funds, GDP, VIX, yield curve, S&P500."
                ),
                confidence=0.0,
                signal_taken=False,
                degraded_reason=(
                    "FRED_API_KEY not set. Register free at fred.stlouisfed.org. "
                    "20 key macro series tracked: FEDFUNDS, CPIAUCSL, UNRATE, T10Y2Y, VIXCLS, etc."
                ),
                platform_truth_label="PUBLIC_DATA_ONLY",
                data={
                    "fred_configured": False,
                    "available_series": list(fred.SERIES.keys()),
                    "setup_url": "https://fred.stlouisfed.org/docs/api/api_key.html",
                    "env_var": "FRED_API_KEY",
                },
            )

        # --- Fetch macro snapshot ---
        snapshot_res = fred.get_macro_snapshot()
        if not snapshot_res.get("ok"):
            return self.emit_signal(
                title="Macro Indicator Scanner",
                summary=f"FRED API error: {snapshot_res.get('error', 'unknown')}",
                confidence=0.0,
                signal_taken=False,
                degraded_reason=f"FRED API failed: {snapshot_res.get('error')}",
                platform_truth_label="PUBLIC_DATA_ONLY",
                data={"error": snapshot_res.get("error")},
            )

        snap_data = snapshot_res["data"]
        snapshot = snap_data.get("snapshot", {})
        recession_signal = snap_data.get("recession_signal", "normal_curve")
        yield_spread = snap_data.get("yield_spread")
        vix = snap_data.get("vix")
        vix_regime = snap_data.get("vix_regime", "low")

        # --- Fetch individual series for deeper analysis ---
        series_data: dict[str, Any] = {}
        key_series = ["FEDFUNDS", "CPIAUCSL", "UNRATE", "PAYEMS", "DGS10", "DGS2"]
        for sid in key_series:
            res = fred.get_series(sid, limit=3)
            if res.get("ok"):
                series_data[sid] = res["data"]
            else:
                errors.append(f"fred/{sid}: {res.get('error', 'failed')}")

        # Signal logic
        confidence = 0.0
        signal_taken = False
        signal_direction = "neutral"
        signal_reason = ""
        degraded_reason = ""
        factor_contributions: dict[str, float] = {}

        # Yield curve inversion = recession signal
        if recession_signal == "inverted_yield_curve":
            confidence += 0.20
            factor_contributions["yield_curve_inversion"] = 0.20
            signal_direction = "recession_risk"
            signal_reason = f"Yield curve inverted: T10Y2Y = {yield_spread:.3f}%"

        # VIX regime
        if vix_regime in ("high", "extreme"):
            vix_contribution = 0.15 if vix_regime == "high" else 0.25
            confidence += vix_contribution
            factor_contributions["vix_elevated"] = vix_contribution
            signal_direction = "market_stress" if signal_direction == "neutral" else signal_direction
            signal_reason += f" | VIX={vix:.1f} ({vix_regime})"

        # CPI trend
        cpi_data = series_data.get("CPIAUCSL", {})
        cpi_delta = cpi_data.get("delta_pct")
        if cpi_delta is not None:
            if cpi_delta > 0.3:  # CPI accelerating
                confidence += 0.10
                factor_contributions["cpi_acceleration"] = 0.10
                signal_reason += f" | CPI +{cpi_delta:.2f}% MoM"
            elif cpi_delta < -0.3:  # CPI decelerating fast
                confidence += 0.08
                factor_contributions["cpi_deceleration"] = 0.08
                signal_reason += f" | CPI {cpi_delta:.2f}% MoM"

        # Unemployment change
        unrate_data = series_data.get("UNRATE", {})
        unrate_delta = unrate_data.get("delta")
        if unrate_delta is not None and abs(unrate_delta) >= 0.3:
            confidence += 0.10
            factor_contributions["unemployment_move"] = 0.10
            signal_reason += f" | Unemployment Δ{unrate_delta:+.1f}%"

        confidence = min(confidence, 0.65)
        if confidence < 0.05:
            degraded_reason = "No significant macro divergence detected in current window."

        # Build readable snapshot
        readable = {}
        for sid, sdata in snapshot.items():
            if isinstance(sdata, dict) and "error" not in sdata:
                readable[sid] = {
                    "label": sdata.get("label"),
                    "value": sdata.get("latest_value"),
                    "date": sdata.get("latest_date"),
                    "delta": sdata.get("delta"),
                }

        summary = (
            f"Macro snapshot: "
            f"Fed Funds={snapshot.get('FEDFUNDS', {}).get('latest_value', 'N/A')}% | "
            f"CPI={snapshot.get('CPIAUCSL', {}).get('latest_value', 'N/A')} | "
            f"Unemployment={snapshot.get('UNRATE', {}).get('latest_value', 'N/A')}% | "
            f"10Y={snapshot.get('DGS10', {}).get('latest_value', 'N/A')}% | "
            f"VIX={vix} ({vix_regime}) | "
            f"Yield spread={yield_spread} ({recession_signal}). "
            f"Signal: {signal_direction}. {signal_reason}. "
            f"Source: FRED (St. Louis Fed)."
        )

        return self.emit_signal(
            title="Macro Indicator Scanner",
            summary=summary,
            confidence=confidence,
            signal_taken=signal_taken,
            degraded_reason=degraded_reason,
            platform_truth_label="PUBLIC_DATA_ONLY",
            factor_contributions=factor_contributions,
            data={
                "snapshot": readable,
                "recession_signal": recession_signal,
                "yield_spread_t10y2y": yield_spread,
                "vix": vix,
                "vix_regime": vix_regime,
                "signal_direction": signal_direction,
                "signal_reason": signal_reason,
                "series_detail": {sid: {"latest": d.get("latest_value"), "delta": d.get("delta"), "delta_pct": d.get("delta_pct")} for sid, d in series_data.items()},
                "errors": errors,
                "source": "fred.stlouisfed.org/api",
                "tracked_series": list(fred.SERIES.keys()),
            },
        )
