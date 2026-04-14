"""
Ensemble Coordinator Bot — meta-bot that runs all research bots in a single
cycle, aggregates their signals using the WeightedSignalAggregator, applies
Kelly sizing, and emits a final composite trade recommendation.

This is the "brain" — it wires together:
  • Crypto momentum (CoinGecko, CoinCap, Fear & Greed)
  • DeFi yield arb (DeFiLlama)
  • Prediction consensus (Metaculus)
  • Sports momentum (BallDontLie, TheSportsDB, Ergast)
  • Macro indicator (FRED)
  • News sentiment (CryptoCompare, CryptoPanic)
  • Technical indicators (SMA, RSI, MACD on recent price data)
  • Signal aggregator (weighted ensemble vote)
  • Kelly sizer (phase-aware bet sizing)
"""
from __future__ import annotations

from typing import Any
from bots.base_research_bot import BaseResearchBot


class EnsembleCoordinatorBot(BaseResearchBot):
    bot_id = "bot_ensemble_coordinator"
    display_name = "Ensemble Coordinator"
    platform = "polymarket"
    mode = "RESEARCH"
    signal_type = "ensemble_composite"
    paper_only = True
    research_only = True
    implemented = True
    truth_label = "PUBLIC_DATA_ONLY"
    quality_tier = "S"
    risk_tier = "medium"
    description = (
        "Meta-bot that polls all research bots, aggregates signals with "
        "weighted voting, applies Kelly criterion sizing, and emits a single "
        "composite recommendation. The ensemble approach reduces individual "
        "bot noise and improves signal reliability."
    )
    edge_source = "Multi-source ensemble voting with confidence-weighted Kelly sizing"
    opp_cadence_per_day = 4.0
    avg_hold_hours = 2.0
    fee_drag_bps = 80
    fill_rate = 0.60
    platforms = ["polymarket", "kalshi"]

    # Which sub-bots to include in the ensemble
    _BOT_REGISTRY: dict[str, str] = {
        "crypto_momentum":      "bots.crypto_momentum_bot.CryptoMomentumBot",
        "defi_yield_arb":       "bots.defi_yield_arb_bot.DefiYieldArbBot",
        "prediction_consensus": "bots.prediction_consensus_bot.PredictionConsensusBot",
        "sports_momentum":      "bots.sports_momentum_bot.SportsMomentumBot",
        "macro_indicator":      "bots.macro_indicator_bot.MacroIndicatorBot",
        "news_sentiment":       "bots.news_sentiment_bot.NewsSentimentBot",
        "social_sentiment":     "bots.social_sentiment_bot.SocialSentimentBot",
    }

    def __init__(self, adapter=None, phase: str = "normal", bankroll: float = 1000.0):
        self.adapter  = adapter
        self.phase    = phase
        self.bankroll = bankroll

    def run_one_cycle(self) -> dict[str, Any]:
        from services.signal_aggregator import SignalAggregator, cross_source_agreement
        from services.kelly_sizer import kelly_prediction_market, phase_kelly_size, dollar_bet_size

        bot_results: list[dict[str, Any]] = []
        run_errors: list[str] = []

        # ── Run each sub-bot ──────────────────────────────────────────────────
        for name, class_path in self._BOT_REGISTRY.items():
            try:
                module_path, class_name = class_path.rsplit(".", 1)
                import importlib
                mod = importlib.import_module(module_path)
                BotClass = getattr(mod, class_name)
                bot = BotClass()
                result = bot.run_one_cycle()
                result["_bot_name"] = name
                bot_results.append(result)
            except Exception as exc:
                run_errors.append(f"{name}: {exc}")
                bot_results.append({
                    "_bot_name": name,
                    "signal_taken": "neutral",
                    "confidence": 0.0,
                    "degraded": True,
                    "title": name,
                    "summary": str(exc),
                })

        # ── Fetch live BTC price for technical indicators ─────────────────────
        closes: list[float] = []
        try:
            from adapters.coingecko import CoinGeckoAdapter
            cg = CoinGeckoAdapter()
            ohlc_res = cg.get_ohlc("bitcoin", days=30)
            if ohlc_res.get("ok"):
                candles = ohlc_res["data"].get("ohlc", [])
                closes = [c.get("close", 0) for c in candles if c.get("close")]
        except Exception as exc:
            run_errors.append(f"coingecko_ohlc: {exc}")

        technical_signal = "neutral"
        technical_confidence = 0.0
        ta_snapshot: dict[str, Any] = {}
        if len(closes) >= 20:
            try:
                from services.technical_indicators import compute_signal_snapshot
                ta_snapshot = compute_signal_snapshot(closes)
                technical_signal = ta_snapshot.get("composite_signal", "neutral")
                score = abs(ta_snapshot.get("composite_score", 0.0))
                technical_confidence = min(score * 1.5, 0.75)
            except Exception as exc:
                run_errors.append(f"technical_indicators: {exc}")

        # ── Assemble votes ────────────────────────────────────────────────────
        agg = SignalAggregator()

        for r in bot_results:
            source = r.get("_bot_name", r.get("title", "unknown"))
            raw_signal = r.get("signal_taken", "neutral")
            # Normalise: True/False → bullish/bearish
            if raw_signal is True:
                signal = "bullish"
            elif raw_signal is False or raw_signal is None:
                signal = "neutral"
            else:
                signal = str(raw_signal).lower()
                if signal not in ("bullish", "bearish", "neutral", "skip"):
                    signal = "neutral"
            conf = float(r.get("confidence", 0.0))
            degraded = bool(r.get("degraded", False))
            if degraded:
                conf *= 0.5
            agg.add(source, signal, confidence=conf, reason=r.get("summary", "")[:120])

        # Add technical indicator signal
        if ta_snapshot:
            agg.add("technical_indicators", technical_signal,
                    confidence=technical_confidence,
                    data={"rsi": ta_snapshot.get("rsi"), "trend": ta_snapshot.get("trend")})

        composite = agg.aggregate(min_sources=2)
        agreement = cross_source_agreement(agg._votes)

        # ── Kelly sizing ──────────────────────────────────────────────────────
        final_signal = composite.get("composite_signal", "neutral")
        composite_confidence = composite.get("confidence", 0.0)
        conviction = composite.get("conviction", "low")

        kelly_result: dict[str, Any] = {}
        dollar_result: dict[str, Any] = {}
        bet_size_usd = 0.0

        if final_signal in ("bullish", "bearish") and composite_confidence > 0.15:
            # Map confidence to model probability: confidence 0.15-0.75 → prob 0.55-0.75
            model_prob = 0.50 + composite_confidence * 0.35
            market_prob = 0.50  # conservative assumption
            side = "YES" if final_signal == "bullish" else "NO"

            kelly_result = kelly_prediction_market(
                model_prob, market_prob, side=side, fraction=0.25
            )
            base_kelly = kelly_result.get("recommended_size", 0.0)
            phase_kelly = phase_kelly_size(self.phase, base_kelly)
            kelly_result["phase_adjusted_size"] = round(phase_kelly, 6)

            dollar_result = dollar_bet_size(
                self.bankroll,
                {**kelly_result, "recommended_size": phase_kelly},
                min_bet_usd=1.0,
                max_bet_usd=self.bankroll * 0.10,
            )
            bet_size_usd = dollar_result.get("final_usd", 0.0)

        # ── Build output ──────────────────────────────────────────────────────
        bull_pct   = composite.get("bull_pct", 0)
        bear_pct   = composite.get("bear_pct", 0)
        source_count = composite.get("source_count", 0)
        agree_score  = agreement.get("agreement_score", 0.0)

        summary = (
            f"Ensemble scan: {source_count} sources. "
            f"Bull {bull_pct:.0f}% / Bear {bear_pct:.0f}% / "
            f"Agreement {agree_score:.0%}. "
            f"Signal: {final_signal} ({conviction} conviction). "
            f"Phase: {self.phase}. "
            f"Kelly bet: ${bet_size_usd:.2f} on bankroll ${self.bankroll:.0f}."
        )

        return self.emit_signal(
            title="Ensemble Coordinator",
            summary=summary,
            confidence=composite_confidence,
            signal_taken=final_signal,
            degraded_reason="" if final_signal != "neutral" else "Insufficient consensus — no trade",
            platform_truth_label="PUBLIC_DATA_ONLY",
            data={
                "composite": composite,
                "agreement": agreement,
                "technical_snapshot": ta_snapshot,
                "kelly_result": kelly_result,
                "dollar_bet": dollar_result,
                "bet_size_usd": bet_size_usd,
                "phase": self.phase,
                "bankroll": self.bankroll,
                "sub_bot_results": [
                    {
                        "bot": r.get("_bot_name"),
                        "signal": r.get("signal_taken"),
                        "confidence": r.get("confidence", 0),
                        "degraded": r.get("degraded", False),
                    }
                    for r in bot_results
                ],
                "run_errors": run_errors,
                "sources_count": source_count,
            },
        )
