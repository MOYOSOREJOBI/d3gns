"""
CircuitBreaker — per-bot circuit breaker with velocity and consecutive-loss triggers.

Triggers:
  3 consecutive losses → 2 min pause
  5 consecutive losses → 10 min pause
  -3% in 5 min        → 10 min pause
  -5% in 30 min       → 30 min pause
"""

import time
import logging

logger = logging.getLogger(__name__)

# Pause durations in seconds
_CB_CONSEC_3  = 120    # 2 min
_CB_CONSEC_5  = 600    # 10 min
_CB_VEL_3PCT  = 600    # 10 min
_CB_VEL_5PCT  = 1800   # 30 min

# Velocity windows in seconds
_WIN_5M  = 300
_WIN_30M = 1800


class CircuitBreaker:
    """
    Records every bet result and exposes check() to determine if a pause should fire.
    Maintains its own state; does NOT interact with RiskManager directly.
    """

    def __init__(self, bot_id: str):
        self.bot_id = bot_id

        # Consecutive loss counter
        self._consec_loss: int = 0

        # Bankroll samples for velocity: list of (timestamp, bankroll)
        self._samples: list[tuple[float, float]] = []

        # Active pause state
        self._pause_until: float = 0.0
        self._pause_reason: str  = ""
        self._pause_seconds: int = 0

    # ── Public API ─────────────────────────────────────────────────────────────

    def record_bet(self, net: float, bankroll: float, timestamp: float | None = None) -> None:
        """Call after every bet is settled."""
        now = timestamp or time.time()
        self._samples.append((now, bankroll))
        self._trim_samples(now)

        if net > 0:
            self._consec_loss = 0
        else:
            self._consec_loss += 1

    def check(self) -> tuple[bool, str, int]:
        """
        Returns (triggered, reason, pause_seconds).
        Only fires if not already in a pause.
        """
        if self.is_active:
            return False, "", 0   # already paused

        # Consecutive loss check
        if self._consec_loss >= 5:
            return self._trigger(_CB_CONSEC_5, "5 consecutive losses")
        if self._consec_loss >= 3:
            return self._trigger(_CB_CONSEC_3, "3 consecutive losses")

        # Velocity checks
        now = time.time()
        vel_3 = self._loss_pct_in_window(now, _WIN_5M)
        if vel_3 is not None and vel_3 <= -0.03:
            return self._trigger(_CB_VEL_3PCT, f"-3% loss in 5min ({vel_3:.1%})")

        vel_5 = self._loss_pct_in_window(now, _WIN_30M)
        if vel_5 is not None and vel_5 <= -0.05:
            return self._trigger(_CB_VEL_5PCT, f"-5% loss in 30min ({vel_5:.1%})")

        return False, "", 0

    @property
    def is_active(self) -> bool:
        return time.time() < self._pause_until

    @property
    def remaining_seconds(self) -> float:
        return max(0.0, self._pause_until - time.time())

    @property
    def reason(self) -> str:
        return self._pause_reason if self.is_active else ""

    @property
    def last_pause_seconds(self) -> int:
        return self._pause_seconds

    def clear(self) -> None:
        """Manually clear the circuit breaker (admin action)."""
        self._pause_until  = 0.0
        self._pause_reason = ""
        self._consec_loss  = 0

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _trigger(self, duration: int, reason: str) -> tuple[bool, str, int]:
        self._pause_until  = time.time() + duration
        self._pause_reason = reason
        self._pause_seconds = duration
        logger.warning(
            f"[{self.bot_id}] CIRCUIT BREAKER triggered: {reason} "
            f"— pausing {duration // 60}m {duration % 60}s"
        )
        return True, reason, duration

    def _loss_pct_in_window(self, now: float, window: float) -> float | None:
        """Return % change in bankroll from (now - window) to now. None if no data."""
        cutoff = now - window
        older  = [b for t, b in self._samples if t <= cutoff]
        if not older or not self._samples:
            return None
        ref   = older[-1]
        cur   = self._samples[-1][1]
        if ref <= 0:
            return None
        return (cur - ref) / ref

    def _trim_samples(self, now: float) -> None:
        cutoff = now - _WIN_30M - 10
        self._samples = [(t, b) for t, b in self._samples if t >= cutoff]
