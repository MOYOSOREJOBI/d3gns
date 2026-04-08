"""
Stake betting strategies – Dice, Limbo, Mines.

7-phase system: FLOOR → ULTRA_SAFE → SAFE → CAREFUL → NORMAL → AGGRESSIVE → TURBO

Core mechanic: Paroli (Anti-Martingale)
  • Press wins: after each win, parlay up to phase-based limit times
  • Bank profit after limit consecutive wins → reset to base
  • On any loss: immediately reset to base bet (no chasing)

Soft-grind fallback (FLOOR/ULTRA_SAFE phases only):
  • Soft Martingale 1.4× multiplier, max 4 steps — grind back to surface
"""

import time
import logging
from risk_manager import RiskManager
import stake_client as sc
from config import (
    BET_DELAY_SECONDS, PAROLI_BY_PHASE,
    DICE_CHANCE, LIMBO_TARGET,
    LIMBO_BIGSHOT_MULTIPLIER, LIMBO_BIGSHOT_PCT, LIMBO_BIGSHOT_FREQ,
    MINES_PARAMS,
)

logger = logging.getLogger(__name__)

# Phases where we grind cautiously (soft martingale on losses allowed)
_GRIND_PHASES = {"floor", "ultra_safe"}
# Phases where big-shot limbo fires
_BIGSHOT_PHASES = {"turbo", "aggressive"}
# Phases where mines extra-pick on streak is allowed
_EXTRA_PICK_PHASES = {"turbo", "aggressive", "normal"}


def _sleep():
    time.sleep(BET_DELAY_SECONDS)


# ═══════════════════════════════════════════════════════════════
#  DICE  –  Paroli press system
# ═══════════════════════════════════════════════════════════════

class DiceStrategy:
    """
    Uses a Paroli (anti-Martingale) system.

    Win streak → double bet up to phase Paroli limit, then bank & reset.
    Loss       → immediately reset to base bet.

    In FLOOR/ULTRA_SAFE phases: soft martingale on losses to grind back.
    """

    def __init__(self, rm: RiskManager):
        self.rm        = rm
        self.press     = 0     # consecutive wins in current paroli run
        self.loss_step = 0     # martingale step (only in grind phases)
        self.base_bet  = None  # locked at start of each paroli run

    def _win_chance(self) -> float:
        return DICE_CHANCE.get(self.rm.phase, DICE_CHANCE["normal"])

    def _paroli_limit(self) -> int:
        return PAROLI_BY_PHASE.get(self.rm.phase, 3)

    def run_one_bet(self) -> float:
        if self.rm.is_halted:
            return 0.0

        win_chance   = self._win_chance()
        paroli_limit = self._paroli_limit()

        if self.base_bet is None:
            self.base_bet = self.rm.get_bet_size()

        if self.rm.phase in _GRIND_PHASES:
            bet = self.rm.get_martingale_bet(self.base_bet, self.loss_step)
        else:
            bet = self.rm.get_paroli_bet(self.base_bet, self.press)

        result = sc.dice_roll(bet, win_chance, "below")
        _sleep()

        if result is None:
            logger.warning(f"[{self.rm.bot_id}] DICE: API returned None, skipping")
            return 0.0

        won = result["won"]
        net = result["payout"] - bet if won else -bet

        if won:
            self.loss_step = 0
            self.press    += 1
            if self.press >= paroli_limit:
                logger.info(
                    f"[{self.rm.bot_id}] DICE 🔥 PAROLI {paroli_limit}-WIN STREAK BANKED"
                    f" | net={net:+.6f}"
                )
                self.press    = 0
                self.base_bet = None
        else:
            if self.rm.phase in _GRIND_PHASES:
                self.loss_step += 1
                if self.loss_step > 4:
                    self.loss_step = 0
                    self.base_bet  = None
            else:
                self.loss_step = 0
            self.press    = 0
            self.base_bet = None

        logger.info(
            f"[{self.rm.bot_id}] DICE | {self.rm.phase} | "
            f"bet={bet:.6f} chance={win_chance}% | "
            f"{'WIN' if won else 'LOSS'} net={net:+.6f} | "
            f"press={self.press} bank=${self.rm.current_bankroll:.4f}"
        )
        return net


# ═══════════════════════════════════════════════════════════════
#  LIMBO  –  Paroli + periodic big-shot bets in TURBO/AGGRESSIVE
# ═══════════════════════════════════════════════════════════════

class LimboStrategy:
    """
    Phase-based multiplier targets with Paroli press system.

    In TURBO/AGGRESSIVE: every LIMBO_BIGSHOT_FREQ bets, fire one 10x big-shot
    with a tiny (0.5 %) bankroll stake for spike potential.
    """

    def __init__(self, rm: RiskManager):
        self.rm        = rm
        self.press     = 0
        self.loss_step = 0
        self.base_bet  = None
        self.bet_count = 0

    def _multiplier(self) -> float:
        return LIMBO_TARGET.get(self.rm.phase, LIMBO_TARGET["normal"])

    def _paroli_limit(self) -> int:
        return PAROLI_BY_PHASE.get(self.rm.phase, 3)

    def run_one_bet(self) -> float:
        if self.rm.is_halted:
            return 0.0

        self.bet_count += 1

        # ── Big-shot bet in TURBO/AGGRESSIVE ─────────────────────
        if (self.rm.phase in _BIGSHOT_PHASES
                and self.bet_count % LIMBO_BIGSHOT_FREQ == 0):
            big_bet = max(
                self.rm.current_bankroll * LIMBO_BIGSHOT_PCT,
                0.000001
            )
            result = sc.limbo_game(big_bet, LIMBO_BIGSHOT_MULTIPLIER)
            _sleep()
            if result is not None:
                won = result["won"]
                net = result["payout"] - big_bet if won else -big_bet
                logger.info(
                    f"[{self.rm.bot_id}] LIMBO 🎯 BIG-SHOT "
                    f"bet={big_bet:.6f} target={LIMBO_BIGSHOT_MULTIPLIER}x | "
                    f"result={result['result']:.2f}x | "
                    f"{'🔥 WIN' if won else 'miss'} net={net:+.6f}"
                )
                return net

        # ── Standard Paroli grind ─────────────────────────────────
        multiplier   = self._multiplier()
        paroli_limit = self._paroli_limit()

        if self.base_bet is None:
            self.base_bet = self.rm.get_bet_size()

        if self.rm.phase in _GRIND_PHASES:
            bet = self.rm.get_martingale_bet(self.base_bet, self.loss_step)
        else:
            bet = self.rm.get_paroli_bet(self.base_bet, self.press)

        result = sc.limbo_game(bet, multiplier)
        _sleep()

        if result is None:
            logger.warning(f"[{self.rm.bot_id}] LIMBO: API returned None, skipping")
            return 0.0

        won = result["won"]
        net = result["payout"] - bet if won else -bet

        if won:
            self.loss_step = 0
            self.press    += 1
            if self.press >= paroli_limit:
                logger.info(
                    f"[{self.rm.bot_id}] LIMBO 🔥 PAROLI {paroli_limit}-WIN STREAK"
                    f" | net={net:+.6f}"
                )
                self.press    = 0
                self.base_bet = None
        else:
            if self.rm.phase in _GRIND_PHASES:
                self.loss_step += 1
                if self.loss_step > 4:
                    self.loss_step = 0
                    self.base_bet  = None
            else:
                self.loss_step = 0
            self.press    = 0
            self.base_bet = None

        logger.info(
            f"[{self.rm.bot_id}] LIMBO | {self.rm.phase} | "
            f"bet={bet:.6f} target={multiplier}x result={result['result']:.2f}x | "
            f"{'WIN' if won else 'LOSS'} net={net:+.6f} | press={self.press}"
        )
        return net


# ═══════════════════════════════════════════════════════════════
#  MINES  –  Progressive picks on win streak, always cashout
# ═══════════════════════════════════════════════════════════════

class MinesStrategy:
    """
    Picks a fixed, pre-calculated number of gems (always cashes out).
    On a winning streak ≥ 3 in TURBO/AGGRESSIVE/NORMAL: adds one extra pick.
    On any loss: resets to base params.

    Probability formula (5×5 grid):
      P = ∏_{k=0}^{n-1}  (25 - m - k) / (25 - k)
    """

    def __init__(self, rm: RiskManager):
        self.rm         = rm
        self.win_streak = 0
        self.loss_step  = 0
        self.base_bet   = None

    def _params(self) -> tuple[int, int]:
        p     = MINES_PARAMS.get(self.rm.phase, MINES_PARAMS["normal"])
        mines = p["mines"]
        picks = p["picks"]
        # Hot-streak bonus pick in higher phases
        if self.win_streak >= 3 and self.rm.phase in _EXTRA_PICK_PHASES:
            picks = min(picks + 1, 25 - mines - 1)
        return mines, picks

    def _win_prob(self, mines: int, picks: int) -> float:
        p, field = 1.0, 25
        gems = field - mines
        for k in range(picks):
            p *= (gems - k) / (field - k)
        return p

    def run_one_bet(self) -> float:
        if self.rm.is_halted:
            return 0.0

        mines, picks = self._params()
        paroli_press = min(self.win_streak // 3, PAROLI_BY_PHASE.get(self.rm.phase, 3))

        if self.base_bet is None:
            self.base_bet = self.rm.get_bet_size()

        if self.rm.phase in _GRIND_PHASES:
            bet = self.rm.get_martingale_bet(self.base_bet, self.loss_step)
        else:
            bet = self.rm.get_paroli_bet(self.base_bet, paroli_press)

        win_prob = self._win_prob(mines, picks)
        result   = sc.mines_play(bet, mines, picks)
        _sleep()

        if result is None:
            logger.warning(f"[{self.rm.bot_id}] MINES: API returned None, skipping")
            return 0.0

        won = result["won"]
        net = result["payout"] - bet if won else -bet

        if won:
            self.win_streak += 1
            self.loss_step   = 0
        else:
            if self.rm.phase in _GRIND_PHASES:
                self.loss_step += 1
                if self.loss_step > 4:
                    self.loss_step = 0
                    self.base_bet  = None
            else:
                self.loss_step = 0
            self.win_streak = 0
            self.base_bet   = None

        logger.info(
            f"[{self.rm.bot_id}] MINES | {self.rm.phase} | "
            f"bet={bet:.6f} mines={mines} picks={picks} P={win_prob:.1%} | "
            f"{'WIN' if won else 'HIT MINE'} net={net:+.6f} | "
            f"streak={self.win_streak}"
        )
        return net


# ─── Factory ──────────────────────────────────────────────────────────────────

def make_strategy(game: str, rm: RiskManager):
    return {
        "dice" : DiceStrategy,
        "limbo": LimboStrategy,
        "mines": MinesStrategy,
    }[game.lower()](rm)
