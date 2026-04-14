"""
Hard Circuit Breaker — capital protection service.

Implements multi-tier safety stops:
  1. Consecutive loss breaker   (N losses in a row → pause K minutes)
  2. Velocity drawdown breaker  (X% loss in Y minutes → pause longer)
  3. Daily loss limit           (Z% of starting bankroll lost today → halt all)
  4. Single bet size limiter    (never risk > MAX_BET_PCT per bet)
  5. Global portfolio stop      (any bot hits hard floor → all bots pause)
  6. Rapid recovery check       (must wait for cooldown after circuit fires)

All state is in-memory (single process) — persisted to DB separately.
Thread-safe via simple counters (no locks needed for reads in CPython GIL).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


# ── Configuration ─────────────────────────────────────────────────────────────

@dataclass
class CircuitBreakerConfig:
    # Consecutive loss breaker
    consec_loss_pause_3:  float = 120.0   # 2 min pause after 3 consecutive losses
    consec_loss_pause_5:  float = 600.0   # 10 min pause after 5 consecutive losses
    consec_loss_pause_7:  float = 3600.0  # 60 min pause after 7 consecutive losses

    # Velocity breaker (loss % in rolling window)
    velocity_window_s:    float = 300.0   # 5-minute window
    velocity_halt_3pct:   float = 600.0   # pause 10 min if -3% in 5 min
    velocity_halt_5pct:   float = 1800.0  # pause 30 min if -5% in 5 min
    velocity_halt_10pct:  float = 7200.0  # pause 2h   if -10% in 5 min

    # Daily loss limit
    daily_loss_limit_pct: float = 0.20    # halt all if daily loss >= 20% of start
    daily_reset_hour_utc: int   = 0       # UTC hour at which daily limit resets

    # Single bet hard cap
    max_bet_pct:          float = 0.10    # never risk > 10% per single bet

    # Portfolio stop (global floor)
    global_floor_pct:     float = 0.90    # if any bot falls below 90% of start → all pause
    global_floor_pause_s: float = 1800.0  # 30 min pause when global floor hit

    # Phase drop on resume
    phase_drop_on_resume: int   = 1       # drop 1 phase level when resuming


# ── Per-bot state ──────────────────────────────────────────────────────────────

@dataclass
class BotBreaker:
    bot_id:              str
    start_bankroll:      float
    current_bankroll:    float = 0.0
    consecutive_losses:  int   = 0
    paused_until:        float = 0.0    # epoch seconds
    pause_reason:        str   = ""
    daily_start_bankroll: float = 0.0
    daily_loss_halt:     bool  = False
    recent_bets:         list  = field(default_factory=list)  # (timestamp, pnl)
    total_bets:          int   = 0
    total_wins:          int   = 0
    total_losses:        int   = 0

    def __post_init__(self):
        if self.current_bankroll == 0.0:
            self.current_bankroll = self.start_bankroll
        if self.daily_start_bankroll == 0.0:
            self.daily_start_bankroll = self.start_bankroll

    @property
    def is_paused(self) -> bool:
        return time.time() < self.paused_until

    @property
    def pause_remaining_s(self) -> float:
        return max(0.0, self.paused_until - time.time())

    @property
    def drawdown_pct(self) -> float:
        if self.start_bankroll <= 0:
            return 0.0
        return (self.start_bankroll - self.current_bankroll) / self.start_bankroll

    @property
    def daily_loss_pct(self) -> float:
        if self.daily_start_bankroll <= 0:
            return 0.0
        return (self.daily_start_bankroll - self.current_bankroll) / self.daily_start_bankroll


class CircuitBreaker:
    """
    Thread-safe (GIL-protected) circuit breaker service.
    One instance per process, shared across all bots.
    """

    def __init__(self, config: CircuitBreakerConfig | None = None):
        self.config = config or CircuitBreakerConfig()
        self._bots: dict[str, BotBreaker] = {}
        self._global_halt_until: float = 0.0
        self._global_halt_reason: str  = ""

    # ── Bot registration ──────────────────────────────────────────────────────

    def register(self, bot_id: str, start_bankroll: float) -> None:
        if bot_id not in self._bots:
            self._bots[bot_id] = BotBreaker(
                bot_id=bot_id,
                start_bankroll=start_bankroll,
                current_bankroll=start_bankroll,
                daily_start_bankroll=start_bankroll,
            )

    # ── Main entry point ──────────────────────────────────────────────────────

    def check_before_bet(
        self,
        bot_id: str,
        bet_size: float,
        bankroll: float,
    ) -> dict[str, Any]:
        """
        Must be called before placing any bet.
        Returns {"allowed": bool, "reason": str, "capped_size": float}
        """
        bot = self._get_or_create(bot_id, bankroll)

        # Global halt
        if self._global_halt_until > time.time():
            return self._block(f"GLOBAL_HALT: {self._global_halt_reason}", 0.0)

        # Bot-level pause
        if bot.is_paused:
            return self._block(
                f"PAUSED: {bot.pause_reason} (resumes in {bot.pause_remaining_s:.0f}s)", 0.0
            )

        # Daily loss limit
        if bot.daily_loss_halt:
            return self._block("DAILY_LOSS_LIMIT: reset at midnight UTC", 0.0)

        # Hard single-bet size cap
        max_bet = bankroll * self.config.max_bet_pct
        capped  = min(bet_size, max_bet)
        if capped < bet_size:
            return {
                "allowed": True,
                "capped":  True,
                "capped_size": round(capped, 6),
                "original_size": bet_size,
                "reason": f"BET_CAPPED: >{self.config.max_bet_pct*100:.0f}% bankroll",
            }

        return {"allowed": True, "capped": False, "capped_size": bet_size, "reason": "ok"}

    def record_result(
        self,
        bot_id: str,
        win: bool,
        pnl: float,
        new_bankroll: float,
    ) -> dict[str, Any]:
        """
        Call after every bet result. Returns triggered breaker info if any fired.
        """
        bot = self._get_or_create(bot_id, new_bankroll)
        bot.current_bankroll = new_bankroll
        bot.total_bets += 1
        now = time.time()
        bot.recent_bets.append((now, pnl))

        # Trim old entries outside velocity window
        cutoff = now - self.config.velocity_window_s
        bot.recent_bets = [(t, p) for t, p in bot.recent_bets if t >= cutoff]

        if win:
            bot.total_wins += 1
            bot.consecutive_losses = 0
        else:
            bot.total_losses += 1
            bot.consecutive_losses += 1

        triggered = []

        # ── Consecutive loss breaker ──────────────────────────────────────────
        if bot.consecutive_losses >= 7:
            self._pause_bot(bot, self.config.consec_loss_pause_7, "7_CONSEC_LOSSES")
            triggered.append("7_consec_loss_breaker")
        elif bot.consecutive_losses >= 5:
            self._pause_bot(bot, self.config.consec_loss_pause_5, "5_CONSEC_LOSSES")
            triggered.append("5_consec_loss_breaker")
        elif bot.consecutive_losses >= 3:
            self._pause_bot(bot, self.config.consec_loss_pause_3, "3_CONSEC_LOSSES")
            triggered.append("3_consec_loss_breaker")

        # ── Velocity breaker ─────────────────────────────────────────────────
        window_pnl = sum(p for _, p in bot.recent_bets)
        if new_bankroll > 0:
            window_pnl_pct = abs(window_pnl) / new_bankroll if window_pnl < 0 else 0
        else:
            window_pnl_pct = 0

        if window_pnl_pct >= 0.10 and window_pnl < 0:
            self._pause_bot(bot, self.config.velocity_halt_10pct, "VELOCITY_-10%_5min")
            triggered.append("velocity_10pct_breaker")
        elif window_pnl_pct >= 0.05 and window_pnl < 0:
            self._pause_bot(bot, self.config.velocity_halt_5pct, "VELOCITY_-5%_5min")
            triggered.append("velocity_5pct_breaker")
        elif window_pnl_pct >= 0.03 and window_pnl < 0:
            self._pause_bot(bot, self.config.velocity_halt_3pct, "VELOCITY_-3%_5min")
            triggered.append("velocity_3pct_breaker")

        # ── Daily loss limit ─────────────────────────────────────────────────
        if bot.daily_loss_pct >= self.config.daily_loss_limit_pct:
            bot.daily_loss_halt = True
            triggered.append(f"daily_loss_limit_{self.config.daily_loss_limit_pct*100:.0f}pct")

        # ── Global floor trigger ─────────────────────────────────────────────
        if bot.drawdown_pct >= (1.0 - self.config.global_floor_pct):
            self._trigger_global_halt(f"BOT_{bot_id}_HIT_FLOOR")
            triggered.append("global_floor_halt")

        return {
            "bot_id":             bot_id,
            "consecutive_losses": bot.consecutive_losses,
            "is_paused":          bot.is_paused,
            "pause_remaining_s":  bot.pause_remaining_s,
            "daily_loss_halt":    bot.daily_loss_halt,
            "drawdown_pct":       round(bot.drawdown_pct * 100, 3),
            "daily_loss_pct":     round(bot.daily_loss_pct * 100, 3),
            "triggered_breakers": triggered,
            "global_halt":        self._global_halt_until > time.time(),
        }

    def reset_daily(self) -> None:
        """Call at midnight UTC to reset daily loss counters."""
        for bot in self._bots.values():
            bot.daily_start_bankroll = bot.current_bankroll
            bot.daily_loss_halt = False

    def emergency_stop_all(self, duration_s: float = 3600.0, reason: str = "MANUAL_EMERGENCY") -> None:
        """Operator command: halt all bots immediately."""
        self._global_halt_until  = time.time() + duration_s
        self._global_halt_reason = reason

    def resume_bot(self, bot_id: str) -> bool:
        """Manually resume a paused bot (operator override)."""
        if bot_id in self._bots:
            self._bots[bot_id].paused_until = 0.0
            self._bots[bot_id].pause_reason = ""
            self._bots[bot_id].consecutive_losses = 0
            return True
        return False

    def resume_all(self) -> None:
        """Manually resume all bots and clear global halt."""
        self._global_halt_until = 0.0
        for bot in self._bots.values():
            bot.paused_until = 0.0
            bot.pause_reason = ""

    def get_status(self) -> dict[str, Any]:
        """Return full circuit breaker status for all bots."""
        global_halt = self._global_halt_until > time.time()
        return {
            "global_halt":         global_halt,
            "global_halt_until":   self._global_halt_until if global_halt else None,
            "global_halt_reason":  self._global_halt_reason if global_halt else "",
            "global_halt_remaining_s": max(0.0, self._global_halt_until - time.time()),
            "bots": {
                bid: {
                    "is_paused":          b.is_paused,
                    "pause_remaining_s":  round(b.pause_remaining_s, 1),
                    "pause_reason":       b.pause_reason,
                    "consecutive_losses": b.consecutive_losses,
                    "drawdown_pct":       round(b.drawdown_pct * 100, 3),
                    "daily_loss_pct":     round(b.daily_loss_pct * 100, 3),
                    "daily_loss_halt":    b.daily_loss_halt,
                    "current_bankroll":   b.current_bankroll,
                    "start_bankroll":     b.start_bankroll,
                    "total_bets":         b.total_bets,
                    "total_wins":         b.total_wins,
                    "total_losses":       b.total_losses,
                    "win_rate":           round(b.total_wins / b.total_bets, 4) if b.total_bets else 0,
                }
                for bid, b in self._bots.items()
            },
        }

    def validate_bet_size(
        self, bankroll: float, proposed_size: float, phase: str = "normal"
    ) -> dict[str, Any]:
        """
        Validate and optionally adjust a proposed bet size against all safety rules.
        Independent of per-bot state — pure math safety check.
        """
        from config import BET_PCT_BY_PHASE
        phase_max = bankroll * BET_PCT_BY_PHASE.get(phase, 0.015)
        hard_max  = bankroll * self.config.max_bet_pct
        final_max = min(phase_max, hard_max)

        if proposed_size <= 0:
            return {"valid": False, "reason": "zero_or_negative_size", "adjusted_size": 0.0}

        if proposed_size > final_max:
            return {
                "valid":         True,
                "capped":        True,
                "adjusted_size": round(final_max, 6),
                "original_size": proposed_size,
                "reason":        f"capped_to_{final_max:.4f} (phase={phase})",
            }
        return {"valid": True, "capped": False, "adjusted_size": proposed_size, "reason": "ok"}

    # ── Private helpers ───────────────────────────────────────────────────────

    def _get_or_create(self, bot_id: str, bankroll: float) -> BotBreaker:
        if bot_id not in self._bots:
            self.register(bot_id, bankroll)
        return self._bots[bot_id]

    def _pause_bot(self, bot: BotBreaker, duration_s: float, reason: str) -> None:
        new_until = time.time() + duration_s
        if new_until > bot.paused_until:
            bot.paused_until = new_until
            bot.pause_reason = reason

    def _trigger_global_halt(self, reason: str) -> None:
        new_until = time.time() + self.config.global_floor_pause_s
        if new_until > self._global_halt_until:
            self._global_halt_until = new_until
            self._global_halt_reason = reason

    def _block(self, reason: str, size: float) -> dict[str, Any]:
        return {"allowed": False, "capped": False, "capped_size": size, "reason": reason}


# ── Global singleton ──────────────────────────────────────────────────────────

_breaker: CircuitBreaker | None = None


def get_breaker(config: CircuitBreakerConfig | None = None) -> CircuitBreaker:
    """Get or create the global circuit breaker instance."""
    global _breaker
    if _breaker is None:
        _breaker = CircuitBreaker(config)
    return _breaker
