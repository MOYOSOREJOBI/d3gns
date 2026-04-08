"""
PhaseManager — centralised 7-phase logic for the DeG£N$ system.

Consumed by RiskManager and by server.py for phase-summary endpoints.
"""

import logging
from typing import Sequence

logger = logging.getLogger(__name__)

# ── Phase constants ────────────────────────────────────────────────────────────
PHASE_FLOOR      = "floor"
PHASE_ULTRA_SAFE = "ultra_safe"
PHASE_SAFE       = "safe"
PHASE_CAREFUL    = "careful"
PHASE_NORMAL     = "normal"
PHASE_AGGRESSIVE = "aggressive"
PHASE_TURBO      = "turbo"
PHASE_MILESTONE  = "milestone"

_PHASE_ORDER = [
    PHASE_FLOOR, PHASE_ULTRA_SAFE, PHASE_SAFE, PHASE_CAREFUL,
    PHASE_NORMAL, PHASE_AGGRESSIVE, PHASE_TURBO,
]

# Drawdown thresholds (fraction from peak)
_DD_FLOOR      = 0.10   # >= 10% drawdown → FLOOR
_DD_ULTRA_SAFE = 0.07   # 7–10% → ULTRA_SAFE
_DD_SAFE       = 0.05   # 5–7%  → SAFE
_DD_CAREFUL    = 0.03   # 3–5%  → CAREFUL

# Bet pct by phase
_BET_PCT = {
    PHASE_FLOOR      : 0.001,
    PHASE_ULTRA_SAFE : 0.002,
    PHASE_SAFE       : 0.005,
    PHASE_CAREFUL    : 0.008,
    PHASE_NORMAL     : 0.015,
    PHASE_AGGRESSIVE : 0.030,
    PHASE_TURBO      : 0.040,
    PHASE_MILESTONE  : 0.015,
}

# Paroli limit by phase
_PAROLI = {
    PHASE_FLOOR      : 0,
    PHASE_ULTRA_SAFE : 0,
    PHASE_SAFE       : 0,
    PHASE_CAREFUL    : 2,
    PHASE_NORMAL     : 3,
    PHASE_AGGRESSIVE : 4,
    PHASE_TURBO      : 5,
    PHASE_MILESTONE  : 3,
}

# Hex colour by phase (matches frontend PHASE_COLORS)
_PHASE_COLOR = {
    PHASE_FLOOR      : "#ef5f57",
    PHASE_ULTRA_SAFE : "#ff8f5a",
    PHASE_SAFE       : "#d6af41",
    PHASE_CAREFUL    : "#c089ff",
    PHASE_NORMAL     : "#5ea1ff",
    PHASE_AGGRESSIVE : "#59d47a",
    PHASE_TURBO      : "#00ff88",
    PHASE_MILESTONE  : "#f0c060",
}

_TURBO_MIN_STREAK = 3
_TURBO_N_BETS     = 20


class PhaseManager:
    """
    Stateless helper that computes the correct phase given financial state.
    Instantiated once with the initial bankroll; call compute_phase() repeatedly.
    """

    def __init__(self, initial_bankroll: float):
        self.initial_bankroll = initial_bankroll

    # ── Core computation ──────────────────────────────────────────────────────

    def compute_phase(
        self,
        bankroll: float,
        peak_bankroll: float,
        recent_bets: Sequence[float],
        streak: int,
        milestone_hit: bool = False,
        continue_to: object = None,
    ) -> str:
        """
        Returns the correct phase string given the current state.

        Args:
            bankroll:      current bankroll value
            peak_bankroll: highest bankroll ever reached
            recent_bets:   last N bet P&Ls (positive = win)
            streak:        consecutive wins (positive) or losses (negative)
            milestone_hit: True if 3x milestone was hit and not yet continued
            continue_to:   non-None if user chose to continue past milestone
        """
        if milestone_hit and continue_to is None:
            return PHASE_MILESTONE

        # Drawdown from peak
        dd = max(0.0, (peak_bankroll - bankroll) / max(peak_bankroll, 0.0001))

        if dd >= _DD_FLOOR:
            return PHASE_FLOOR
        if dd >= _DD_ULTRA_SAFE:
            return PHASE_ULTRA_SAFE
        if dd >= _DD_SAFE:
            return PHASE_SAFE
        if dd >= _DD_CAREFUL:
            return PHASE_CAREFUL

        # Above CAREFUL threshold — check for TURBO / AGGRESSIVE / NORMAL
        if self._turbo_eligible(bankroll, dd, recent_bets, streak):
            return PHASE_TURBO
        if self._aggressive_eligible(bankroll, dd, streak):
            return PHASE_AGGRESSIVE
        return PHASE_NORMAL

    def apply_transition_rules(
        self,
        prev_phase: str,
        new_phase: str,
        streak: int,
    ) -> str:
        """
        Apply one-step transition rules on top of raw phase calculation:
        - TURBO → first loss → AGGRESSIVE (not CAREFUL)
        - AGGRESSIVE → 2 consec losses → CAREFUL
        Returns (possibly adjusted) new phase.
        """
        consec_loss = abs(streak) if streak < 0 else 0

        if prev_phase == PHASE_TURBO and streak < 0:
            return PHASE_AGGRESSIVE

        if prev_phase == PHASE_AGGRESSIVE and consec_loss >= 2:
            return PHASE_CAREFUL

        return new_phase

    # ── Eligibility checks ────────────────────────────────────────────────────

    def _turbo_eligible(
        self,
        bankroll: float,
        drawdown_pct: float,
        recent_bets: Sequence[float],
        streak: int,
    ) -> bool:
        if streak < _TURBO_MIN_STREAK:
            return False
        if bankroll < self.initial_bankroll:
            return False
        if drawdown_pct > 0.005:   # allow tiny rounding noise
            return False
        if len(recent_bets) < _TURBO_N_BETS:
            return False
        return sum(recent_bets) > 0

    def _aggressive_eligible(
        self,
        bankroll: float,
        drawdown_pct: float,
        streak: int,
    ) -> bool:
        if streak < 2:
            return False
        if bankroll < self.initial_bankroll:
            return False
        if drawdown_pct > 0.01:
            return False
        return True

    # ── Lookup helpers ────────────────────────────────────────────────────────

    @staticmethod
    def bet_pct_for_phase(phase: str) -> float:
        return _BET_PCT.get(phase, _BET_PCT[PHASE_NORMAL])

    @staticmethod
    def paroli_limit_for_phase(phase: str) -> int:
        return _PAROLI.get(phase, 0)

    @staticmethod
    def phase_color(phase: str) -> str:
        return _PHASE_COLOR.get(phase, "#5ea1ff")

    @staticmethod
    def phase_index(phase: str) -> int:
        """Lower = more conservative."""
        try:
            return _PHASE_ORDER.index(phase)
        except ValueError:
            return 4   # default to NORMAL

    @staticmethod
    def all_phases() -> list[str]:
        return list(_PHASE_ORDER)
