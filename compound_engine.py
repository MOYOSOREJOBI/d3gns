"""
CompoundEngine — bet sizing calculator used by all strategies.

Centralises:
  • Drawdown-adjusted Kelly
  • Paroli doubling up to phase limit
  • Win velocity boost
  • Floor value enforcement (bot physically cannot breach 10%)
"""

import logging

logger = logging.getLogger(__name__)

# Phase bet percentages
_BET_PCT = {
    "floor"      : 0.001,
    "ultra_safe" : 0.002,
    "safe"       : 0.005,
    "careful"    : 0.008,
    "normal"     : 0.015,
    "aggressive" : 0.030,
    "turbo"      : 0.040,
    "milestone"  : 0.015,
}

# Paroli press limits
_PAROLI = {
    "floor"      : 0,
    "ultra_safe" : 0,
    "safe"       : 0,
    "careful"    : 2,
    "normal"     : 3,
    "aggressive" : 4,
    "turbo"      : 5,
    "milestone"  : 3,
}

# Max bankroll cap per press level (prevents single bet blowing floor)
_CAP_PCT_BY_PHASE = {
    "floor"      : 0.02,
    "ultra_safe" : 0.03,
    "safe"       : 0.04,
    "careful"    : 0.05,
    "normal"     : 0.06,
    "aggressive" : 0.08,
    "turbo"      : 0.10,
    "milestone"  : 0.06,
}


class CompoundEngine:
    """
    Stateless bet-sizing calculator.
    All methods are pure functions of inputs — no side effects.
    """

    def __init__(self, initial_bankroll: float):
        self.initial_bankroll = initial_bankroll
        self.floor_value      = initial_bankroll * 0.90   # 10% floor (never breach)

    # ── Main entry point ───────────────────────────────────────────────────────

    def calculate_bet_size(
        self,
        bankroll: float,
        phase: str,
        kelly_fraction: float = 0.25,
        paroli_streak: int = 0,
        win_velocity_active: bool = False,
        win_velocity_boost: float = 1.5,
        user_scale: float = 1.0,
        min_bet: float = 0.000001,
    ) -> float:
        """
        Compute the final bet size, fully floor-enforced.

        Args:
            bankroll:             current bankroll
            phase:                current phase string
            kelly_fraction:       fraction of full Kelly to use (0.25 = quarter Kelly)
            paroli_streak:        consecutive wins in current Paroli run (0 = no press)
            win_velocity_active:  True if win velocity boost is in effect
            win_velocity_boost:   multiplier when velocity active (default 1.5)
            user_scale:           user-set bet scale multiplier (from UI slider)
            min_bet:              absolute minimum bet floor

        Returns:
            Bet size in dollars, guaranteed not to push bankroll below floor_value.
        """
        # Drawdown-adjusted Kelly
        dd_adj = self._drawdown_adj(bankroll)

        # Base bet from phase pct
        pct  = _BET_PCT.get(phase, _BET_PCT["normal"])
        base = bankroll * pct * dd_adj

        # User scale (cap at 1.0 in loss phases to prevent amplification)
        if phase in ("floor", "ultra_safe", "safe"):
            scale = min(max(user_scale, 0.1), 1.0)
            boost = 1.0
        else:
            scale = max(user_scale, 0.1)
            boost = win_velocity_boost if win_velocity_active else 1.0

        base = base * scale * boost

        # Paroli doubling
        if paroli_streak > 0:
            base = self._apply_paroli(base, phase, paroli_streak, bankroll)

        # Kelly fraction adjustment
        base = base * kelly_fraction / 0.25   # normalise: 0.25 = reference

        # Floor enforcement
        available = max(bankroll - self.floor_value, 0.0)
        base      = min(base, available)

        return max(base, min_bet)

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _drawdown_adj(self, bankroll: float) -> float:
        """
        Continuous Kelly size shrink as drawdown grows.
        kelly_fraction = base * (1 - drawdown_pct / 0.10)
        Clamped to [0.1, 1.0].
        """
        dd_pct = max(0.0, (self.initial_bankroll - bankroll) / max(self.initial_bankroll, 0.0001))
        adj    = max(0.1, 1.0 - dd_pct / 0.10)
        return adj

    def _apply_paroli(
        self,
        base_bet: float,
        phase: str,
        streak: int,
        bankroll: float,
    ) -> float:
        """
        Anti-Martingale: double on each win, capped by phase paroli limit and cap%.
        """
        max_press = _PAROLI.get(phase, 0)
        if max_press == 0:
            return base_bet

        presses = min(streak, max_press)
        bet     = base_bet * (2 ** presses)

        cap_pct = _CAP_PCT_BY_PHASE.get(phase, 0.06)
        cap     = bankroll * cap_pct

        return min(bet, cap)

    def enforce_floor(self, proposed_bet: float, bankroll: float) -> float:
        """
        Hard floor enforcement — bet cannot push bankroll below floor_value.
        Returns capped bet (may be 0 if already at or below floor).
        """
        available = max(bankroll - self.floor_value, 0.0)
        return min(proposed_bet, available)

    def kelly_bet(
        self,
        bankroll: float,
        win_prob: float,
        payout_ratio: float,
        fraction: float = 0.25,
    ) -> float:
        """
        Standard fractional Kelly formula.
        payout_ratio: net odds (e.g. 1.0 for even money, 0.95 for a market at price 0.51)
        fraction:     Kelly fraction to use (0.25 = quarter Kelly)
        """
        b = payout_ratio
        p = win_prob
        q = 1.0 - p
        kelly_full = (b * p - q) / max(b, 0.0001)
        kelly_full = max(kelly_full, 0.0)
        return bankroll * kelly_full * fraction
