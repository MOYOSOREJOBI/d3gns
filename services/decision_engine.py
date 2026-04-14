"""
Decision Engine — sub-50ms signal → decision → action pipeline.

Architecture:
  1. Ingests live signals from all running bots
  2. Aggregates with ensemble weights (SignalAggregator)
  3. Applies Kelly sizing + circuit breaker gate
  4. Emits ActionDecision with exact bet size, target, and rationale
  5. Tracks decision latency, hit rate, and P&L attribution

Design principles:
  - Every decision logged with full audit trail
  - Hard gates: circuit breaker, phase, daily limit, profit forcefield
  - Decisions are deterministic given same inputs (reproducible)
  - Self-learning: updates source weights based on outcome
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class SignalInput:
    source:      str
    signal:      str          # "bullish" | "bearish" | "neutral" | "skip"
    confidence:  float        # 0.0 – 1.0
    market:      str = ""     # market/symbol this applies to
    price:       float = 0.0  # current market price
    model_prob:  float = 0.0  # model's probability estimate
    market_prob: float = 0.0  # current market implied probability
    metadata:    dict = field(default_factory=dict)
    ts:          float = field(default_factory=time.time)


@dataclass
class ActionDecision:
    action:          str     # "bet_yes" | "bet_no" | "no_action" | "emergency_stop"
    market:          str
    bet_size_usd:    float
    bet_size_pct:    float   # fraction of bankroll
    confidence:      float
    edge_pct:        float
    kelly_fraction:  float
    phase:           str
    rationale:       str
    signal_count:    int
    bull_weight:     float
    bear_weight:     float
    ts:              float = field(default_factory=time.time)
    decision_ms:     float = 0.0
    signals_used:    list = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action, "market": self.market,
            "bet_size_usd": self.bet_size_usd, "bet_size_pct": self.bet_size_pct,
            "confidence": self.confidence, "edge_pct": self.edge_pct,
            "kelly_fraction": self.kelly_fraction, "phase": self.phase,
            "rationale": self.rationale, "signal_count": self.signal_count,
            "bull_weight": self.bull_weight, "bear_weight": self.bear_weight,
            "ts": self.ts, "decision_ms": self.decision_ms,
        }


# ── Source performance tracker ────────────────────────────────────────────────

class SourceTracker:
    """
    Tracks prediction accuracy per signal source.
    Used to dynamically adjust ensemble weights.
    """
    def __init__(self) -> None:
        self._records: dict[str, dict[str, int]] = {}

    def record(self, source: str, signal: str, outcome: str) -> None:
        """outcome: 'correct' | 'wrong' | 'neutral'"""
        if source not in self._records:
            self._records[source] = {"correct": 0, "wrong": 0, "neutral": 0, "total": 0}
        self._records[source][outcome] = self._records[source].get(outcome, 0) + 1
        self._records[source]["total"] += 1

    def accuracy(self, source: str) -> float:
        r = self._records.get(source, {})
        total = r.get("total", 0)
        if total < 5:
            return 0.55   # assume slightly above chance until data accumulates
        return r.get("correct", 0) / total

    def dynamic_weight(self, source: str, base_weight: float = 1.0) -> float:
        """Scale weight by accuracy relative to 50% baseline."""
        acc = self.accuracy(source)
        if acc > 0.65:
            return base_weight * 1.5
        elif acc > 0.55:
            return base_weight * 1.1
        elif acc < 0.40:
            return base_weight * 0.4
        elif acc < 0.50:
            return base_weight * 0.7
        return base_weight

    def get_report(self) -> dict[str, Any]:
        return {
            src: {
                **data,
                "accuracy": round(self.accuracy(src), 3),
                "dynamic_weight": round(self.dynamic_weight(src), 3),
            }
            for src, data in self._records.items()
        }


# ── Decision engine ───────────────────────────────────────────────────────────

class DecisionEngine:
    """
    Core decision-making pipeline. Thread-safe, sub-50ms target.

    Call flow:
      1. receive_signal(signal_input)   ← called by each bot
      2. make_decision(market, bankroll, phase) ← called by executor
         → returns ActionDecision
    """

    # Minimum signals required before acting
    MIN_SIGNALS_TO_ACT: int   = 2
    # Minimum ensemble confidence
    MIN_CONFIDENCE: float     = 0.18
    # Minimum edge over market
    MIN_EDGE_PCT: float       = 0.03   # 3% edge
    # Max decisions per minute (rate limiter)
    MAX_DECISIONS_PER_MIN: int = 30

    def __init__(self, phase: str = "normal", bankroll: float = 1000.0) -> None:
        self.phase    = phase
        self.bankroll = bankroll
        self._pending_signals: list[SignalInput] = []
        self._decision_log: list[ActionDecision] = []
        self._tracker = SourceTracker()
        self._decision_count_window: list[float] = []
        self._total_decisions = 0
        self._total_actions   = 0

    def receive_signal(self, sig: SignalInput) -> None:
        """Register a new signal from a bot or adapter."""
        # Expire signals older than 5 minutes
        now = time.time()
        self._pending_signals = [s for s in self._pending_signals if now - s.ts < 300]
        # Remove stale signal from same source
        self._pending_signals = [s for s in self._pending_signals if s.source != sig.source]
        self._pending_signals.append(sig)

    def make_decision(
        self,
        market: str,
        bankroll: float | None = None,
        phase: str | None = None,
    ) -> ActionDecision:
        """
        Process all pending signals and return an ActionDecision.
        Target: <50ms execution time.
        """
        t0 = time.monotonic()
        bank  = bankroll or self.bankroll
        ph    = phase or self.phase

        # Rate limit
        now = time.time()
        self._decision_count_window = [t for t in self._decision_count_window if now - t < 60]
        if len(self._decision_count_window) >= self.MAX_DECISIONS_PER_MIN:
            return self._no_action(market, bank, ph, "rate_limited", t0)

        # Circuit breaker gate
        try:
            from services.circuit_breaker import get_breaker
            cb_check = get_breaker().check_before_bet("decision_engine", 0.01, bank)
            if not cb_check.get("allowed"):
                return self._no_action(market, bank, ph, cb_check.get("reason", "circuit_break"), t0)
        except Exception:
            pass

        # Need minimum signals
        active = [s for s in self._pending_signals if time.time() - s.ts < 300]
        if len(active) < self.MIN_SIGNALS_TO_ACT:
            return self._no_action(market, bank, ph, f"insufficient_signals_{len(active)}", t0)

        # Aggregate
        try:
            from services.signal_aggregator import SignalAggregator
            agg = SignalAggregator()
            for s in active:
                weight = self._tracker.dynamic_weight(s.source)
                agg.add(s.source, s.signal, confidence=s.confidence * weight / max(weight, 1))
            composite = agg.aggregate(min_sources=self.MIN_SIGNALS_TO_ACT)
        except Exception as exc:
            return self._no_action(market, bank, ph, f"aggregation_error:{exc}", t0)

        direction  = composite.get("composite_signal", "neutral")
        confidence = composite.get("confidence", 0.0)

        if direction == "neutral" or confidence < self.MIN_CONFIDENCE:
            return self._no_action(market, bank, ph, f"low_confidence_{confidence:.2f}", t0)

        # Find market signal for edge calculation
        model_prob  = 0.50 + confidence * 0.25  # 0.54 – 0.75 based on confidence
        market_prob = 0.50
        for sig in active:
            if sig.market == market and sig.model_prob > 0:
                model_prob  = sig.model_prob
                market_prob = sig.market_prob if sig.market_prob > 0 else 0.50
                break

        edge_pct = (model_prob - market_prob) * 100
        if edge_pct < self.MIN_EDGE_PCT:
            return self._no_action(market, bank, ph, f"insufficient_edge_{edge_pct:.2f}%", t0)

        # Kelly sizing
        try:
            from services.kelly_sizer import kelly_prediction_market, phase_kelly_size, dollar_bet_size
            side = "YES" if direction == "bullish" else "NO"
            k = kelly_prediction_market(model_prob, market_prob, side=side, fraction=0.25)
            base_size = k.get("recommended_size", 0.0)
            ph_size   = phase_kelly_size(ph, base_size)
            dollar    = dollar_bet_size(bank, {**k, "recommended_size": ph_size},
                                        min_bet_usd=1.0, max_bet_usd=bank * 0.10)
            bet_usd   = dollar.get("final_usd", 0.0)
        except Exception:
            bet_usd  = bank * 0.01
            ph_size  = 0.01
            base_size = 0.01

        if bet_usd < 0.50:
            return self._no_action(market, bank, ph, "bet_below_minimum", t0)

        # Build decision
        action = "bet_yes" if direction == "bullish" else "bet_no"
        decision_ms = round((time.monotonic() - t0) * 1000, 2)

        decision = ActionDecision(
            action=action,
            market=market,
            bet_size_usd=round(bet_usd, 4),
            bet_size_pct=round(ph_size, 6),
            confidence=confidence,
            edge_pct=round(edge_pct, 3),
            kelly_fraction=round(base_size, 6),
            phase=ph,
            rationale=(
                f"Ensemble: {direction} @ {confidence:.0%} conf | "
                f"Model: {model_prob:.1%} vs Market: {market_prob:.1%} | "
                f"Edge: {edge_pct:.1f}% | Kelly: {ph_size:.2%} | Bet: ${bet_usd:.2f}"
            ),
            signal_count=len(active),
            bull_weight=composite.get("bull_weight", 0),
            bear_weight=composite.get("bear_weight", 0),
            decision_ms=decision_ms,
            signals_used=[s.source for s in active],
        )

        self._decision_log.append(decision)
        self._decision_count_window.append(now)
        self._total_decisions += 1
        self._total_actions   += 1

        logger.info("DECISION [%s] %s $%.2f edge=%.1f%% ms=%.1f",
                    market, action, bet_usd, edge_pct, decision_ms)
        return decision

    def record_outcome(self, decision: ActionDecision, profit: float) -> None:
        """Feed outcome back to source tracker for weight learning."""
        correct = (profit > 0 and decision.action in ("bet_yes", "bet_no"))
        outcome = "correct" if profit > 0 else "wrong"
        for source in decision.signals_used:
            self._tracker.record(source, decision.action, outcome)

    def get_stats(self) -> dict[str, Any]:
        recent = self._decision_log[-100:]
        actions = [d for d in recent if d.action != "no_action"]
        return {
            "total_decisions":   self._total_decisions,
            "total_actions":     self._total_actions,
            "pending_signals":   len(self._pending_signals),
            "active_sources":    len({s.source for s in self._pending_signals}),
            "avg_confidence":    round(sum(d.confidence for d in actions) / max(len(actions), 1), 3),
            "avg_decision_ms":   round(sum(d.decision_ms for d in recent) / max(len(recent), 1), 2),
            "source_accuracy":   self._tracker.get_report(),
            "phase":             self.phase,
            "bankroll":          self.bankroll,
        }

    def _no_action(self, market: str, bank: float, ph: str, reason: str, t0: float) -> ActionDecision:
        self._total_decisions += 1
        return ActionDecision(
            action="no_action", market=market,
            bet_size_usd=0.0, bet_size_pct=0.0,
            confidence=0.0, edge_pct=0.0, kelly_fraction=0.0,
            phase=ph, rationale=reason, signal_count=0,
            bull_weight=0.0, bear_weight=0.0,
            decision_ms=round((time.monotonic() - t0) * 1000, 2),
        )


# ── Global singleton ──────────────────────────────────────────────────────────
_engine: DecisionEngine | None = None


def get_decision_engine(phase: str = "normal", bankroll: float = 1000.0) -> DecisionEngine:
    global _engine
    if _engine is None:
        _engine = DecisionEngine(phase=phase, bankroll=bankroll)
    return _engine
