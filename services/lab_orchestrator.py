"""
Lab Orchestrator — coordinates all 30 LAB bots in the trading/betting/prediction lane.

Architecture:
  - Runs all lab bots concurrently via thread pool
  - Aggregates their signals into the DecisionEngine
  - Applies Kelly sizing + phase multiplier + circuit breaker gate
  - Executes approved decisions via the execution layer
  - Tracks per-bot P&L attribution and feeds back into PortfolioAllocator
  - Manages the signal → decision → execution → settlement pipeline

Bot categories (run in priority order):
  Priority 1 — Prediction market bots (Kalshi, Polymarket, Metaculus)
  Priority 2 — Crypto/DeFi bots (momentum, funding, vol regime)
  Priority 3 — Macro/event bots (FRED, news, sentiment, congress)
  Priority 4 — Alternative (sports, F1, soccer)

Cycle timing:
  Fast cycle (every 5s):  prediction market bots + crypto
  Slow cycle (every 60s): macro + alternative
  Rebalance (every 5min): portfolio allocator
  Daily reset (every 24h): vault + performance archive
"""
from __future__ import annotations

import logging
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeout
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ── Bot registry ──────────────────────────────────────────────────────────────

# (bot_module, bot_class, priority, cycle_seconds)
LAB_BOT_REGISTRY: list[tuple[str, str, int, float]] = [
    # Priority 1 — Prediction markets
    ("bots.kalshi_pair_spread",          "KalshiPairSpreadBot",         1, 10.0),
    ("bots.kalshi_resolution_decay",     "KalshiResolutionDecayBot",    1, 10.0),
    ("bots.kalshi_orderbook_imbalance",  "KalshiOrderbookImbalanceBot", 1,  5.0),
    ("bots.poly_adaptive_trend_bot",     "PolyAdaptiveTrendBot",        1, 10.0),
    ("bots.polymarket_microstructure",   "PolymarketMicrostructureBot", 1,  5.0),
    ("bots.poly_kalshi_crossvenue",      "PolyKalshiCrossvenueBot",     1, 10.0),
    ("bots.crossvenue_arb_watchlist",    "CrossvenueArbWatchlistBot",   1, 15.0),
    ("bots.prediction_consensus_bot",    "PredictionConsensusBot",      1, 30.0),
    # Priority 2 — Crypto/DeFi
    ("bots.crypto_momentum_bot",         "CryptoMomentumBot",           2,  5.0),
    ("bots.defi_yield_arb_bot",          "DefiYieldArbBot",             2, 20.0),
    ("bots.crypto_funding_rate_bot",     "CryptoFundingRateBot",        2, 10.0),
    ("bots.volatility_regime_bot",       "VolatilityRegimeBot",         2, 30.0),
    ("bots.tech_signal_bot",             "TechSignalBot",               2, 30.0),
    ("bots.sp500_momentum_tracker_bot",  "SP500MomentumTrackerBot",     2, 60.0),
    ("bots.gold_price_momentum_bot",     "GoldPriceMomentumBot",        2, 60.0),
    ("bots.gold_funding_basis_bot",      "GoldFundingBasisBot",         2, 60.0),
    # Priority 3 — Macro/event
    ("bots.macro_indicator_bot",         "MacroIndicatorBot",           3, 120.0),
    ("bots.news_sentiment_bot",          "NewsSentimentBot",            3,  60.0),
    ("bots.social_sentiment_bot",        "SocialSentimentBot",          3,  60.0),
    ("bots.geopolitical_risk_bot",       "GeopoliticalRiskBot",         3, 120.0),
    ("bots.oil_inventory_shock_bot",     "OilInventoryShockBot",        3, 120.0),
    ("bots.earnings_surprise_bot",       "EarningsSurpriseBot",         3, 120.0),
    ("bots.insider_filing_bot",          "InsiderFilingBot",            3, 120.0),
    ("bots.congress_trades_bot",         "CongressTradesBot",           3, 300.0),
    ("bots.kalshi_macro_shock_sniper_bot","KalshiMacroShockSniperBot",  3,  15.0),
    # Priority 4 — Alternative
    ("bots.sports_momentum_bot",         "SportsMomentumBot",           4, 120.0),
    ("bots.f1_odds_latency_bot",         "F1OddsLatencyBot",            4, 120.0),
    ("bots.soccer_consensus_latency_bot","SoccerConsensusLatencyBot",   4,  60.0),
    ("bots.oddsapi_clv_tracker",         "OddsapiCLVTrackerBot",        4,  60.0),
]


@dataclass
class BotRunResult:
    bot_module:  str
    bot_class:   str
    priority:    int
    signal:      str          # bullish / bearish / neutral / error
    confidence:  float
    market:      str
    raw:         dict         = field(default_factory=dict)
    latency_ms:  float        = 0.0
    error:       str          = ""


@dataclass
class LabCycleResult:
    cycle_id:       int
    ts:             float = field(default_factory=time.time)
    signals:        list[BotRunResult] = field(default_factory=list)
    decisions_made: int   = 0
    bets_placed:    int   = 0
    total_risked:   float = 0.0
    cycle_ms:       float = 0.0
    phase:          str   = "normal"
    errors:         int   = 0


# ── Orchestrator ──────────────────────────────────────────────────────────────

class LabOrchestrator:
    """
    Main lab orchestrator — runs all lab bots and wires their output
    into the decision + execution pipeline.
    """

    FAST_CYCLE_S  = 5.0      # run priority-1/2 bots
    SLOW_CYCLE_S  = 60.0     # run priority-3/4 bots
    REBALANCE_S   = 300.0    # rebalance portfolio allocator
    BOT_TIMEOUT_S = 12.0     # per-bot execution timeout
    MAX_WORKERS   = 12       # thread pool size

    def __init__(
        self,
        bankroll:     float = 400.0,   # lab bot capital
        phase:        str   = "safe",
        paper_mode:   bool  = True,    # flip to False for live trading
    ) -> None:
        self.bankroll    = bankroll
        self.phase       = phase
        self.paper_mode  = paper_mode
        self._running    = False
        self._cycle_n    = 0
        self._last_slow  = 0.0
        self._last_rebal = 0.0
        self._pool       = ThreadPoolExecutor(max_workers=self.MAX_WORKERS, thread_name_prefix="lab")
        self._lock       = threading.Lock()
        self._history:   list[LabCycleResult] = []
        self._bot_cache: dict[str, Any] = {}   # module → bot instance
        self._pnl_map:   dict[str, float] = {} # bot_class → cumulative P&L

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        t = threading.Thread(target=self._main_loop, daemon=True, name="lab-orch")
        t.start()
        logger.info("LabOrchestrator started — phase=%s paper=%s", self.phase, self.paper_mode)

    def stop(self) -> None:
        self._running = False
        self._pool.shutdown(wait=False, cancel_futures=True)

    # ── Main loop ─────────────────────────────────────────────────────────────

    def _main_loop(self) -> None:
        while self._running:
            t0 = time.monotonic()
            try:
                self._cycle_n += 1
                now = time.time()
                run_slow = (now - self._last_slow) >= self.SLOW_CYCLE_S

                # Select bots for this cycle
                if run_slow:
                    bots_to_run = LAB_BOT_REGISTRY          # all
                    self._last_slow = now
                else:
                    bots_to_run = [b for b in LAB_BOT_REGISTRY if b[2] <= 2]  # fast only

                # Run cycle
                result = self._run_cycle(bots_to_run)

                # Periodic rebalance
                if (now - self._last_rebal) >= self.REBALANCE_S:
                    self._rebalance_portfolio()
                    self._last_rebal = now

                # Store
                with self._lock:
                    self._history.append(result)
                    if len(self._history) > 1000:
                        self._history.pop(0)

            except Exception as exc:
                logger.error("LabOrchestrator main loop error: %s", exc)

            elapsed = time.monotonic() - t0
            sleep_for = max(0.5, self.FAST_CYCLE_S - elapsed)
            time.sleep(sleep_for)

    def _run_cycle(self, bots: list[tuple]) -> LabCycleResult:
        t0 = time.monotonic()
        cycle = LabCycleResult(cycle_id=self._cycle_n, phase=self.phase)

        # Run all bots in parallel
        futures = {}
        for mod, cls, pri, _ in bots:
            f = self._pool.submit(self._run_bot_safe, mod, cls, pri)
            futures[f] = (mod, cls, pri)

        for fut in as_completed(futures, timeout=self.BOT_TIMEOUT_S + 2):
            mod, cls, pri = futures[fut]
            try:
                result = fut.result(timeout=0.1)
                cycle.signals.append(result)
                if result.error:
                    cycle.errors += 1
            except Exception as exc:
                cycle.errors += 1
                cycle.signals.append(BotRunResult(
                    bot_module=mod, bot_class=cls, priority=pri,
                    signal="error", confidence=0.0, market="",
                    error=str(exc),
                ))

        # Feed signals into DecisionEngine
        self._process_signals(cycle)

        cycle.cycle_ms = round((time.monotonic() - t0) * 1000, 1)
        logger.debug("LabCycle #%d: %d signals, %d decisions, %.0fms",
                     self._cycle_n, len(cycle.signals), cycle.decisions_made, cycle.cycle_ms)
        return cycle

    def _run_bot_safe(self, mod_path: str, cls_name: str, priority: int) -> BotRunResult:
        t0 = time.monotonic()
        try:
            import importlib
            key = f"{mod_path}.{cls_name}"
            if key not in self._bot_cache:
                mod = importlib.import_module(mod_path)
                self._bot_cache[key] = getattr(mod, cls_name)()
            bot = self._bot_cache[key]
            raw = bot.run_one_cycle()

            # Extract signal
            data       = raw.get("data", {}) or {}
            signal_str = str(data.get("signal", data.get("direction", "neutral"))).lower()
            confidence = float(raw.get("confidence", 0.0))
            market     = str(data.get("market_id", data.get("ticker", data.get("symbol", ""))))

            return BotRunResult(
                bot_module=mod_path, bot_class=cls_name, priority=priority,
                signal=signal_str, confidence=confidence, market=market,
                raw=raw, latency_ms=round((time.monotonic() - t0) * 1000, 1),
            )
        except Exception as exc:
            return BotRunResult(
                bot_module=mod_path, bot_class=cls_name, priority=priority,
                signal="error", confidence=0.0, market="", error=str(exc),
                latency_ms=round((time.monotonic() - t0) * 1000, 1),
            )

    # ── Signal → Decision pipeline ────────────────────────────────────────────

    def _process_signals(self, cycle: LabCycleResult) -> None:
        """
        Send valid signals to DecisionEngine and execute approved decisions.
        """
        try:
            from services.decision_engine import get_decision_engine, SignalInput
            engine = get_decision_engine(phase=self.phase, bankroll=self.bankroll)
        except Exception as exc:
            logger.debug("DecisionEngine unavailable: %s", exc)
            return

        # Push all valid signals
        market_set: set[str] = set()
        for r in cycle.signals:
            if r.signal in ("bullish", "bearish") and r.confidence > 0.10:
                try:
                    sig = SignalInput(
                        source=r.bot_class,
                        signal=r.signal,
                        confidence=r.confidence,
                        market=r.market,
                        price=float(r.raw.get("data", {}).get("price", 0) or 0),
                        model_prob=float(r.raw.get("data", {}).get("model_prob", 0) or 0),
                        market_prob=float(r.raw.get("data", {}).get("market_prob", 0) or 0),
                    )
                    engine.receive_signal(sig)
                    if r.market:
                        market_set.add(r.market)
                except Exception:
                    pass

        # Make decisions for each unique market
        for market in list(market_set)[:5]:   # cap at 5 markets per cycle
            try:
                decision = engine.make_decision(market, bankroll=self.bankroll, phase=self.phase)
                if decision.action != "no_action":
                    cycle.decisions_made += 1
                    placed = self._execute_decision(decision)
                    if placed:
                        cycle.bets_placed    += 1
                        cycle.total_risked   += decision.bet_size_usd
            except Exception as exc:
                logger.debug("Decision error for %s: %s", market, exc)

    # ── Execution ─────────────────────────────────────────────────────────────

    def _execute_decision(self, decision) -> bool:
        """
        Execute a decision. In paper mode: log only.
        In live mode: send to execution adapter.
        """
        if self.paper_mode:
            logger.info(
                "[PAPER] %s %s $%.2f edge=%.1f%% conf=%.0f%%",
                decision.action, decision.market, decision.bet_size_usd,
                decision.edge_pct, decision.confidence * 100,
            )
            # Simulate outcome for paper P&L tracking
            import random
            win_prob = 0.50 + decision.edge_pct / 200  # edge_pct → slight win bias
            won = random.random() < win_prob
            pnl = decision.bet_size_usd * (decision.edge_pct / 100) if won else -decision.bet_size_usd * 0.05
            self._record_pnl(decision.action, pnl)
            # Feed back to allocator
            try:
                from services.portfolio_allocator import get_allocator
                bot_id = decision.signals_used[0] if decision.signals_used else "unknown"
                # Convert class name to bot_id format
                bot_id_fmt = "bot_" + bot_id.lower().replace("bot", "").strip("_")
                get_allocator().record_result(bot_id_fmt, pnl)
            except Exception:
                pass
            return True

        # Live execution — wire to actual execution adapter
        try:
            from services.execution_router import route_decision
            result = route_decision(decision)
            return result.get("placed", False)
        except Exception as exc:
            logger.error("Execution failed: %s", exc)
            return False

    def _record_pnl(self, action: str, pnl: float) -> None:
        with self._lock:
            self._pnl_map[action] = self._pnl_map.get(action, 0.0) + pnl

    # ── Rebalance ─────────────────────────────────────────────────────────────

    def _rebalance_portfolio(self) -> None:
        try:
            from services.portfolio_allocator import get_allocator
            result = get_allocator().rebalance()
            logger.info("Portfolio rebalanced: %d changes", result.get("change_count", 0))
        except Exception as exc:
            logger.debug("Rebalance failed: %s", exc)

    # ── Status / reporting ────────────────────────────────────────────────────

    def get_status(self) -> dict[str, Any]:
        with self._lock:
            recent = self._history[-20:] if self._history else []

        total_bets    = sum(c.bets_placed    for c in recent)
        total_risked  = sum(c.total_risked   for c in recent)
        total_decisions = sum(c.decisions_made for c in recent)
        avg_ms        = (sum(c.cycle_ms for c in recent) / max(len(recent), 1))
        total_pnl     = sum(self._pnl_map.values())

        return {
            "running":          self._running,
            "phase":            self.phase,
            "paper_mode":       self.paper_mode,
            "bankroll":         round(self.bankroll, 2),
            "cycle_count":      self._cycle_n,
            "registered_bots":  len(LAB_BOT_REGISTRY),
            "cached_bots":      len(self._bot_cache),
            "recent_cycles":    len(recent),
            "recent_bets":      total_bets,
            "recent_risked_usd": round(total_risked, 2),
            "recent_decisions": total_decisions,
            "avg_cycle_ms":     round(avg_ms, 1),
            "total_paper_pnl":  round(total_pnl, 2),
            "last_slow_age_s":  round(time.time() - self._last_slow, 0),
            "last_rebal_age_s": round(time.time() - self._last_rebal, 0),
        }

    def get_last_cycle(self) -> dict[str, Any] | None:
        with self._lock:
            if not self._history:
                return None
            c = self._history[-1]
        return {
            "cycle_id":  c.cycle_id,
            "ts":        c.ts,
            "signals":   len(c.signals),
            "decisions": c.decisions_made,
            "bets":      c.bets_placed,
            "risked":    round(c.total_risked, 2),
            "errors":    c.errors,
            "ms":        c.cycle_ms,
            "phase":     c.phase,
            "top_signals": [
                {
                    "bot": r.bot_class,
                    "signal": r.signal,
                    "conf": round(r.confidence, 3),
                    "market": r.market,
                    "ms": r.latency_ms,
                }
                for r in sorted(c.signals, key=lambda x: x.confidence, reverse=True)[:5]
                if r.signal in ("bullish", "bearish")
            ],
        }

    def run_once(self) -> LabCycleResult:
        """Run a single full cycle synchronously (for testing / manual trigger)."""
        return self._run_cycle(LAB_BOT_REGISTRY)


# ── Global singleton ──────────────────────────────────────────────────────────
_lab_orch: LabOrchestrator | None = None


def get_lab_orchestrator(
    bankroll: float = 400.0,
    phase: str = "safe",
    paper_mode: bool = True,
) -> LabOrchestrator:
    global _lab_orch
    if _lab_orch is None:
        _lab_orch = LabOrchestrator(bankroll=bankroll, phase=phase, paper_mode=paper_mode)
    return _lab_orch
