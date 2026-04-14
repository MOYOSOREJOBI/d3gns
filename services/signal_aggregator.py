"""
Ensemble signal aggregator — weighted voting across all bots and adapters.

Architecture:
  - Each source emits a signal: "bullish" | "bearish" | "neutral" | "skip"
  - Sources have configurable weights (default equal-weight)
  - Weighted majority vote → composite signal
  - Confidence = margin / total_weight
  - Optional: Optuna-based weight optimisation (if optuna installed)
  - Returns a full audit trail: per-source votes, weights, final signal
"""
from __future__ import annotations

import statistics
from typing import Any


# ── Signal type aliases ───────────────────────────────────────────────────────
BULLISH = "bullish"
BEARISH = "bearish"
NEUTRAL = "neutral"
SKIP    = "skip"      # source skipped / no data — excluded from vote


# ── Default source weights ────────────────────────────────────────────────────
# Tune these based on backtested accuracy. Higher = more influence.
DEFAULT_WEIGHTS: dict[str, float] = {
    # Market data
    "crypto_momentum":     1.5,
    "fear_greed":          1.2,
    "technical_indicators": 1.5,
    "coingecko_trend":     1.0,
    "coincap_trend":       0.8,
    "coinpaprika":         0.8,
    "defillama_tvl":       1.0,
    "defi_yields":         0.9,

    # Macro / finance
    "macro_fred":          1.3,
    "wsb_sentiment":       0.9,
    "alpha_vantage":       1.0,
    "coinmarketcap":       1.0,

    # News sentiment
    "news_vader":          1.1,
    "crypto_news":         1.0,
    "hackernews":          0.7,
    "gnews":               0.9,
    "newsapi":             1.0,
    "currents":            0.8,

    # Social sentiment
    "reddit_crypto":       1.0,
    "reddit_finance":      0.9,
    "reddit_politics":     0.6,

    # Prediction markets
    "metaculus":           1.3,
    "polymarket_signal":   1.2,
    "kalshi_signal":       1.1,

    # Sports / alternative
    "sports_signal":       0.5,
    "spaceflight_news":    0.3,
    "weather_extreme":     0.4,
}


# ── Core aggregator ───────────────────────────────────────────────────────────

class SignalAggregator:
    """
    Weighted ensemble voter for multi-source signals.

    Usage:
        agg = SignalAggregator()
        agg.add("fear_greed", "bullish", confidence=0.8, data={...})
        agg.add("macro_fred", "bearish", confidence=0.6, data={...})
        result = agg.aggregate()
    """

    def __init__(self, weights: dict[str, float] | None = None) -> None:
        self._weights: dict[str, float] = weights or dict(DEFAULT_WEIGHTS)
        self._votes: list[dict[str, Any]] = []

    def set_weight(self, source: str, weight: float) -> None:
        self._weights[source] = weight

    def add(
        self,
        source: str,
        signal: str,
        *,
        confidence: float = 1.0,
        data: dict[str, Any] | None = None,
        reason: str = "",
    ) -> None:
        """
        Register a signal from a source.

        Args:
            source:     Unique name for the signal source.
            signal:     "bullish" | "bearish" | "neutral" | "skip"
            confidence: How confident the source is (0–1). Multiplied into weight.
            data:       Optional raw payload for audit trail.
            reason:     Optional human-readable reason.
        """
        sig_norm = signal.lower().strip()
        if sig_norm not in (BULLISH, BEARISH, NEUTRAL, SKIP):
            sig_norm = NEUTRAL
        w = self._weights.get(source, 1.0) * max(0.0, min(confidence, 1.0))
        self._votes.append({
            "source":     source,
            "signal":     sig_norm,
            "weight":     w,
            "confidence": confidence,
            "data":       data or {},
            "reason":     reason,
        })

    def aggregate(self, min_sources: int = 2) -> dict[str, Any]:
        """
        Compute weighted-vote composite signal.

        Args:
            min_sources: Minimum non-skip sources required for a non-neutral result.
        """
        active = [v for v in self._votes if v["signal"] != SKIP]
        if len(active) < min_sources:
            return {
                "composite_signal": NEUTRAL,
                "confidence":       0.0,
                "reason":           f"insufficient_sources ({len(active)}/{min_sources} required)",
                "bull_weight":      0.0,
                "bear_weight":      0.0,
                "neutral_weight":   0.0,
                "total_weight":     0.0,
                "source_count":     len(active),
                "votes":            self._votes,
            }

        bull_w = sum(v["weight"] for v in active if v["signal"] == BULLISH)
        bear_w = sum(v["weight"] for v in active if v["signal"] == BEARISH)
        neut_w = sum(v["weight"] for v in active if v["signal"] == NEUTRAL)
        total  = bull_w + bear_w + neut_w

        if total == 0:
            return self._neutral_result("zero_total_weight", active)

        bull_pct = bull_w / total
        bear_pct = bear_w / total
        neut_pct = neut_w / total
        margin   = abs(bull_pct - bear_pct)

        if bull_pct > bear_pct and bull_pct > neut_pct:
            composite = BULLISH
        elif bear_pct > bull_pct and bear_pct > neut_pct:
            composite = BEARISH
        else:
            composite = NEUTRAL

        # Confidence: margin normalised; high if one side dominates clearly
        confidence = round(margin, 4) if composite != NEUTRAL else round(neut_pct, 4)

        # Conviction tier
        conviction = (
            "high"   if confidence >= 0.4 else
            "medium" if confidence >= 0.2 else
            "low"
        )

        # Per-source breakdown
        breakdown = [
            {
                "source":   v["source"],
                "signal":   v["signal"],
                "weight":   round(v["weight"], 4),
                "confidence": v["confidence"],
                "reason":   v["reason"],
            }
            for v in self._votes
        ]

        return {
            "composite_signal": composite,
            "confidence":       confidence,
            "conviction":       conviction,
            "bull_weight":      round(bull_w, 4),
            "bear_weight":      round(bear_w, 4),
            "neutral_weight":   round(neut_w, 4),
            "total_weight":     round(total, 4),
            "bull_pct":         round(bull_pct * 100, 1),
            "bear_pct":         round(bear_pct * 100, 1),
            "neutral_pct":      round(neut_pct * 100, 1),
            "source_count":     len(active),
            "total_sources":    len(self._votes),
            "breakdown":        breakdown,
        }

    def reset(self) -> None:
        self._votes.clear()

    def _neutral_result(self, reason: str, active: list) -> dict[str, Any]:
        return {
            "composite_signal": NEUTRAL,
            "confidence":       0.0,
            "reason":           reason,
            "source_count":     len(active),
            "votes":            self._votes,
        }


# ── Convenience: aggregate from bot emit_signal payloads ─────────────────────

def aggregate_bot_signals(
    bot_results: list[dict[str, Any]],
    *,
    weights: dict[str, float] | None = None,
    min_sources: int = 2,
) -> dict[str, Any]:
    """
    Aggregate the output of multiple research bots.

    Each bot result should have:
      - "signal_taken": "bullish" | "bearish" | "neutral" | None
      - "confidence":   0.0–1.0
      - "title":        source identifier (used as key)
      - "degraded":     bool — if True, weight halved
      - "data":         optional raw payload

    Returns the same dict structure as SignalAggregator.aggregate().
    """
    agg = SignalAggregator(weights=weights)
    for bot in bot_results:
        source    = bot.get("title", bot.get("source", "unknown"))
        signal    = bot.get("signal_taken") or NEUTRAL
        conf      = float(bot.get("confidence", 1.0))
        degraded  = bool(bot.get("degraded", False))
        if degraded:
            conf *= 0.5
        agg.add(source, signal, confidence=conf, data=bot.get("data", {}), reason=bot.get("summary", ""))
    return agg.aggregate(min_sources=min_sources)


# ── Weight optimiser (Optuna, optional) ──────────────────────────────────────

def optimise_weights(
    historical_snapshots: list[dict[str, Any]],
    actual_outcomes: list[str],   # "bullish" | "bearish" | "neutral" per snapshot
    n_trials: int = 200,
    source_names: list[str] | None = None,
) -> dict[str, Any]:
    """
    Use Optuna to find optimal source weights that maximise directional accuracy.
    Each snapshot is a dict of {source: signal} for one time period.
    Requires: pip install optuna
    """
    try:
        import optuna
        optuna.logging.set_verbosity(optuna.logging.WARNING)
    except ImportError:
        return {"error": "optuna not installed. pip install optuna", "optimised": False}

    if len(historical_snapshots) != len(actual_outcomes):
        return {"error": "snapshots and outcomes must be same length"}

    sources = source_names or sorted({k for snap in historical_snapshots for k in snap})

    def objective(trial: Any) -> float:
        weights = {s: trial.suggest_float(s, 0.1, 3.0) for s in sources}
        correct = 0
        for snap, actual in zip(historical_snapshots, actual_outcomes):
            agg = SignalAggregator(weights=weights)
            for src, sig in snap.items():
                agg.add(src, sig)
            result = agg.aggregate(min_sources=1)
            if result["composite_signal"] == actual:
                correct += 1
        return correct / len(actual_outcomes)

    study = optuna.create_study(direction="maximize")
    study.optimize(objective, n_trials=n_trials, show_progress_bar=False)

    best = study.best_params
    best_score = study.best_value
    return {
        "optimised": True,
        "best_weights": {k: round(v, 4) for k, v in best.items()},
        "accuracy": round(best_score, 4),
        "n_trials": n_trials,
        "n_samples": len(historical_snapshots),
    }


# ── Signal smoothing (temporal) ───────────────────────────────────────────────

def smooth_signals(
    signals: list[str],
    window: int = 3,
) -> list[str]:
    """
    Majority-vote smoothing over a rolling window.
    Reduces flip-flopping in noisy signal streams.
    """
    out: list[str] = []
    for i, sig in enumerate(signals):
        if i < window - 1:
            out.append(sig)
            continue
        window_sigs = signals[i - window + 1: i + 1]
        bull = window_sigs.count(BULLISH)
        bear = window_sigs.count(BEARISH)
        if bull > bear:
            out.append(BULLISH)
        elif bear > bull:
            out.append(BEARISH)
        else:
            out.append(NEUTRAL)
    return out


# ── Agreement score (cross-source consistency) ────────────────────────────────

def cross_source_agreement(votes: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Compute how much sources agree with each other.
    High agreement = higher reliability; low agreement = uncertain environment.
    """
    active_signals = [v["signal"] for v in votes if v["signal"] != SKIP]
    if not active_signals:
        return {"agreement_score": 0.0, "dominant_signal": NEUTRAL}

    counts = {
        BULLISH: active_signals.count(BULLISH),
        BEARISH: active_signals.count(BEARISH),
        NEUTRAL: active_signals.count(NEUTRAL),
    }
    dominant = max(counts, key=lambda k: counts[k])
    agreement_score = counts[dominant] / len(active_signals)

    return {
        "agreement_score": round(agreement_score, 4),
        "dominant_signal": dominant,
        "bull_count":  counts[BULLISH],
        "bear_count":  counts[BEARISH],
        "neutral_count": counts[NEUTRAL],
        "total":       len(active_signals),
    }
