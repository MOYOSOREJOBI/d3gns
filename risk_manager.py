"""
RiskManager — 5-phase strategy engine with circuit breakers and vault ratchet.

Phases (bot NEVER permanently halts — only adjusts aggression):
  TURBO      win streak >= 3, zero drawdown, last 20 bets positive → 4% bets, Paroli x5
  NORMAL     default operation → 2% bets, Paroli x3
  SAFE       3–5% drawdown → 0.5% bets, no pressing
  ULTRA_SAFE 5–10% drawdown → 0.2% bets, flat bets only
  FLOOR      at/below floor → 0.1% dust bets, grind back
  MILESTONE  3x hit, waiting for user decision → normal sizing continues

Auto-withdraw:
  Every time bankroll hits start × 1.20 → withdraw excess → reset to start

Profit lock ratchet:
  Every time bankroll gains +10% of start from the peak → lock 50% of that gain to vault
  Vault funds are NEVER re-risked.

Circuit breakers (pause, not stop):
  3 consecutive losses → 2 min cooldown
  5 consecutive losses → 10 min cooldown
  -3% in 5 min → 10 min cooldown
  -5% in 30 min → 30 min cooldown
  Bot resumes automatically after cooldown at one phase level lower.

TURBO rules:
  Enters: win streak >= 3 AND drawdown == 0 AND last 20 bets net positive
  Exits: any single loss → drop to NORMAL (not SAFE)
"""

import logging
import time
import collections

logger = logging.getLogger(__name__)

# ── Phase constants (7 phases) ───────────────────────────────────────────────
PHASE_TURBO      = "turbo"        # streak>=3, zero DD, last 20 positive → 4% Paroli×5
PHASE_AGGRESSIVE = "aggressive"   # streak>=2, zero DD → 3% Paroli×4
PHASE_NORMAL     = "normal"       # default → 1.5% Paroli×3
PHASE_CAREFUL    = "careful"      # 3-5% DD → 0.8% Paroli×2
PHASE_SAFE       = "safe"         # 5-7% DD → 0.5% no pressing
PHASE_ULTRA_SAFE = "ultra_safe"   # 7-10% DD → 0.2% flat
PHASE_FLOOR      = "floor"        # >=10% DD → 0.1% dust, safest only
PHASE_MILESTONE  = "milestone"    # 3x hit, waiting user decision

_PHASE_ORDER = [PHASE_FLOOR, PHASE_ULTRA_SAFE, PHASE_SAFE, PHASE_CAREFUL,
                PHASE_NORMAL, PHASE_AGGRESSIVE, PHASE_TURBO]

# Drawdown thresholds (bankroll / start_amount)
FLOOR_THRESHOLD      = 0.90   # 10% drawdown → FLOOR
ULTRA_SAFE_THRESHOLD = 0.93   # 7%  drawdown → ULTRA_SAFE
SAFE_THRESHOLD       = 0.95   # 5%  drawdown → SAFE
CAREFUL_THRESHOLD    = 0.97   # 3%  drawdown → CAREFUL
SAFE_EXIT_THRESHOLD  = 0.98   # recover to 2% before exiting CAREFUL

# Auto-withdraw
AUTO_WITHDRAW_PCT = 1.20

# TURBO criteria
TURBO_MIN_STREAK = 3
TURBO_N_BETS     = 20   # last N bets must be net positive

# Bet sizing (7 phases)
BET_PCT = {
    "turbo"     : 0.040,
    "aggressive": 0.030,
    "normal"    : 0.020,
    "careful"   : 0.008,
    "safe"      : 0.005,
    "ultra_safe": 0.002,
    "floor"     : 0.001,
    "milestone" : 0.020,
}

# Paroli press limits
PAROLI_LIMIT = {
    "turbo"     : 5,
    "aggressive": 4,
    "normal"    : 3,
    "careful"   : 2,
    "safe"      : 1,
    "ultra_safe": 0,
    "floor"     : 0,
    "milestone" : 3,
}

# Soft Martingale
MARTI_STEPS = 4
MARTI_MULT  = 1.4

# Profit lock ratchet
PROFIT_LOCK_PCT   = 0.10  # lock profits every +10% of start gained
PROFIT_LOCK_RATIO = 0.50  # lock 50% of each gain tranche

# Circuit breaker cooldowns (seconds)
CB_3_LOSS  = 120   # 2 min
CB_5_LOSS  = 600   # 10 min
CB_3PCT_5M = 600   # 10 min
CB_5PCT_30 = 1800  # 30 min

# Velocity windows
VEL_LOSS_WINDOW  = 300   # 5 minutes in seconds
VEL_LOSS_30_WIND = 1800  # 30 minutes
WIN_VEL_WINDOW   = 600   # 10 minutes
WIN_VEL_BOOST    = 1.50
WIN_VEL_BETS     = 10    # boosted bets
WIN_VEL_TRIGGER  = 0.05  # +5% in 10 min


class RiskManager:
    def __init__(
        self,
        bot_id: str,
        start_amount: float,
        target_amount: float | None = None,
        floor_amount:  float | None = None,
    ):
        self.bot_id       = bot_id
        self.start_amount = start_amount
        self.bankroll     = start_amount
        self.target       = target_amount or start_amount * 5.0
        self.floor        = floor_amount  or start_amount * 0.40

        # Compatibility aliases
        self.initial_bankroll  = start_amount
        self.current_bankroll  = start_amount

        # Financials
        self.total_withdrawn   = 0.0
        self.vault             = 0.0
        self.peak_bankroll     = start_amount
        self._last_lock_level  = 0   # how many PROFIT_LOCK_PCT tranches locked

        # Counters
        self.bet_count   = 0
        self.win_count   = 0
        self.loss_count  = 0
        self.streak      = 0   # positive=wins, negative=losses

        # Recent bets circular buffer (for TURBO detection)
        self._recent_pnl: collections.deque = collections.deque(maxlen=TURBO_N_BETS)

        # Velocity tracking: list of (timestamp, bankroll) snapshots
        self._vel_samples: list[tuple[float, float]] = []

        # Circuit breaker state
        self._cooldown_until: float = 0.0     # epoch timestamp
        self._cb_reason      : str  = ""

        # Win velocity boost
        self._vel_boost_remaining: int = 0
        self._vel_boost_factor   : float = 1.0

        # State flags
        self.is_halted      = False   # never True under normal operation
        self.is_target_hit  = False
        self.milestone_hit  = False
        self.continue_to    = None
        self.bet_scale      = 1.0     # user-set multiplier (UI slider)

        self.start_time = time.time()
        self.phase      = PHASE_NORMAL
        self._recalculate_phase()

    # ── Properties ───────────────────────────────────────────────────────────

    @property
    def is_cooling_down(self) -> bool:
        return time.time() < self._cooldown_until

    @property
    def cooldown_remaining(self) -> float:
        """Seconds left in current cooldown."""
        return max(0.0, self._cooldown_until - time.time())

    @property
    def withdraw_trigger(self) -> float:
        return self.start_amount * AUTO_WITHDRAW_PCT

    @property
    def caution_threshold(self) -> float:      # compat alias
        return self.start_amount * SAFE_THRESHOLD

    @property
    def recovery_threshold(self) -> float:     # compat alias
        return self.start_amount * ULTRA_SAFE_THRESHOLD

    @property
    def hard_stop_amount(self) -> float:
        return self.floor

    @property
    def target_amount(self) -> float:
        return self.target

    @property
    def floor_amount(self) -> float:
        return self.floor

    @property
    def withdraw_at(self) -> float:
        return self.withdraw_trigger

    @property
    def caution_at(self) -> float:
        return self.caution_threshold

    @property
    def recovery_at(self) -> float:
        return self.recovery_threshold

    @property
    def progress(self) -> float:
        return self.bankroll + self.total_withdrawn + self.vault

    @property
    def progress_multiplier(self) -> float:
        return self.progress / self.start_amount

    @property
    def roi_pct(self) -> float:
        return (self.bankroll - self.start_amount) / self.start_amount * 100

    @property
    def drawdown_pct(self) -> float:
        """Current drawdown from peak as fraction (0.0 = at peak)."""
        if self.peak_bankroll <= 0:
            return 0.0
        return max(0.0, (self.peak_bankroll - self.bankroll) / self.peak_bankroll)

    @property
    def danger(self) -> bool:
        return self.bankroll <= self.floor

    @property
    def milestoneHit(self) -> bool:
        return self.milestone_hit

    @property
    def progress_pct(self) -> float:
        goal_range = self.target - self.start_amount
        if goal_range <= 0:
            return 100.0
        return min(100.0, max(0.0, (self.progress - self.start_amount) / goal_range * 100))

    # ── Core bet result handler ───────────────────────────────────────────────

    def record_bet_result(self, profit_or_loss: float) -> dict:
        """
        Called after every settled bet.
        Returns action dict — never "HALTED" under normal operation.
        Handles: phase recalc, auto-withdraw, profit locking, circuit breakers,
                 TURBO entry/exit, velocity detection.
        """
        now = time.time()

        self.bankroll        += profit_or_loss
        self.current_bankroll = self.bankroll
        self.bet_count       += 1
        self._recent_pnl.append(profit_or_loss)
        self._vel_samples.append((now, self.bankroll))

        if profit_or_loss > 0:
            self.win_count += 1
            self.streak     = max(self.streak, 0) + 1
        else:
            self.loss_count += 1
            self.streak      = min(self.streak, 0) - 1

        if self.bankroll > self.peak_bankroll:
            self.peak_bankroll = self.bankroll

        # ── Velocity monitoring ───────────────────────────────────────────────
        self._check_loss_velocity(now)
        self._check_win_velocity(now)

        # ── Circuit breaker: consecutive losses ───────────────────────────────
        consec_loss = abs(self.streak) if self.streak < 0 else 0
        if consec_loss >= 5 and not self.is_cooling_down:
            self._trigger_cb(CB_5_LOSS, "5 consecutive losses")
        elif consec_loss == 3 and not self.is_cooling_down:
            self._trigger_cb(CB_3_LOSS, "3 consecutive losses")

        # ── Auto-withdraw ─────────────────────────────────────────────────────
        withdrawn_now = 0.0
        if self.bankroll >= self.withdraw_trigger:
            withdrawn_now          = self.bankroll - self.start_amount
            self.total_withdrawn  += withdrawn_now
            self.bankroll          = self.start_amount
            self.current_bankroll  = self.bankroll
            logger.info(
                f"[{self.bot_id}] AUTO-WITHDRAW ${withdrawn_now:.4f} | "
                f"vault_total={self.vault:.2f} withdrawn={self.total_withdrawn:.2f}"
            )
            # Reset win velocity boost to avoid inflating bets after reset
            self._vel_boost_remaining = 0

        # ── Profit lock ratchet ───────────────────────────────────────────────
        lock_now = self._check_profit_lock()

        # ── Floor danger ──────────────────────────────────────────────────────
        if self.bankroll <= self.floor:
            logger.warning(
                f"[{self.bot_id}] DANGER at/below floor=${self.floor:.4f} "
                f"bankroll=${self.bankroll:.4f} — micro-bets engaged."
            )

        self._recalculate_phase()

        # Trim old velocity samples
        cutoff = now - max(VEL_LOSS_30_WIND, WIN_VEL_WINDOW) - 10
        self._vel_samples = [(t, b) for t, b in self._vel_samples if t >= cutoff]

        # ── Win velocity boost countdown ──────────────────────────────────────
        if self._vel_boost_remaining > 0:
            self._vel_boost_remaining -= 1
            if self._vel_boost_remaining == 0:
                self._vel_boost_factor = 1.0
                logger.info(f"[{self.bot_id}] Win velocity boost expired.")

        # ── Milestone check ───────────────────────────────────────────────────
        if not self.milestone_hit and self.progress >= self.start_amount * 3.0:
            self.milestone_hit = True
            elapsed = (time.time() - self.start_time) / 3600
            logger.info(
                f"[{self.bot_id}] 3x MILESTONE! progress=${self.progress:.2f} "
                f"in {elapsed:.1f}h"
            )
            return self._result(withdrawn_now, lock_now, action="MILESTONE")

        # ── Goal check ────────────────────────────────────────────────────────
        if not self.is_target_hit and self.progress >= self.target:
            self.is_target_hit = True
            logger.info(f"[{self.bot_id}] GOAL HIT! progress=${self.progress:.2f}")
            return self._result(withdrawn_now, lock_now, action="GOAL_HIT")

        return self._result(withdrawn_now, lock_now, action="CONTINUE")

    # ── Profit lock ratchet ───────────────────────────────────────────────────

    def _check_profit_lock(self) -> float:
        """
        Lock 50% of every +10% gain tranche to vault.
        E.g. start=$20: lock fires at $22, $24, $26...
        Returns amount locked this call.
        """
        gains   = self.bankroll - self.start_amount
        tranches_earned = int(gains / (self.start_amount * PROFIT_LOCK_PCT))
        if tranches_earned > self._last_lock_level:
            new_tranches = tranches_earned - self._last_lock_level
            lock_amount  = new_tranches * self.start_amount * PROFIT_LOCK_PCT * PROFIT_LOCK_RATIO
            lock_amount  = min(lock_amount, self.bankroll - self.floor)  # never lock below floor
            if lock_amount > 0:
                self.bankroll        -= lock_amount
                self.current_bankroll = self.bankroll
                self.vault           += lock_amount
                self._last_lock_level = tranches_earned
                logger.info(
                    f"[{self.bot_id}] PROFIT LOCK ${lock_amount:.4f} to vault "
                    f"(vault=${self.vault:.2f}) — tranche {tranches_earned}"
                )
                return lock_amount
        return 0.0

    # ── Circuit breaker ───────────────────────────────────────────────────────

    def _trigger_cb(self, duration_sec: float, reason: str):
        self._cooldown_until = time.time() + duration_sec
        self._cb_reason      = reason
        # Drop one phase level on resume (implemented by recalculate after cooldown)
        logger.warning(
            f"[{self.bot_id}] CIRCUIT BREAKER: {reason} — "
            f"cooling down {duration_sec/60:.0f} min"
        )

    def _check_loss_velocity(self, now: float):
        """Check if loss velocity triggers a circuit breaker."""
        if self.is_cooling_down:
            return
        # -3% in 5 minutes
        cutoff_5m = now - VEL_LOSS_WINDOW
        older_5m  = [b for t, b in self._vel_samples if t <= cutoff_5m]
        if older_5m:
            ref_bank = older_5m[-1]
            if ref_bank > 0:
                change_pct = (self.bankroll - ref_bank) / ref_bank
                if change_pct <= -0.03:
                    self._trigger_cb(CB_3PCT_5M, f"-3% in 5min (was ${ref_bank:.2f} now ${self.bankroll:.2f})")
                    return
        # -5% in 30 minutes
        cutoff_30m = now - VEL_LOSS_30_WIND
        older_30m  = [b for t, b in self._vel_samples if t <= cutoff_30m]
        if older_30m:
            ref_bank = older_30m[-1]
            if ref_bank > 0:
                change_pct = (self.bankroll - ref_bank) / ref_bank
                if change_pct <= -0.05:
                    self._trigger_cb(CB_5PCT_30, f"-5% in 30min (was ${ref_bank:.2f} now ${self.bankroll:.2f})")

    def _check_win_velocity(self, now: float):
        """Detect win velocity and boost bet sizing."""
        if self._vel_boost_remaining > 0:
            return  # already boosting
        cutoff = now - WIN_VEL_WINDOW
        older  = [b for t, b in self._vel_samples if t <= cutoff]
        if older:
            ref_bank   = older[-1]
            change_pct = (self.bankroll - ref_bank) / ref_bank if ref_bank > 0 else 0
            if change_pct >= WIN_VEL_TRIGGER:
                self._vel_boost_remaining = WIN_VEL_BETS
                self._vel_boost_factor    = WIN_VEL_BOOST
                logger.info(
                    f"[{self.bot_id}] WIN VELOCITY +{change_pct:.1%} in 10min — "
                    f"bet boost {WIN_VEL_BOOST}x for {WIN_VEL_BETS} bets"
                )

    # ── Phase calculation ─────────────────────────────────────────────────────

    def _recalculate_phase(self):
        prev = self.phase

        if self.milestone_hit and self.continue_to is None:
            new = PHASE_MILESTONE
        elif self.bankroll <= self.start_amount * FLOOR_THRESHOLD:
            # FLOOR phase at ≥10% drawdown; if at/below hard floor, still FLOOR
            new = PHASE_FLOOR
        elif self.bankroll <= self.start_amount * ULTRA_SAFE_THRESHOLD:
            new = PHASE_ULTRA_SAFE
        elif self.bankroll <= self.start_amount * SAFE_THRESHOLD:
            new = PHASE_SAFE
        elif self.bankroll <= self.start_amount * CAREFUL_THRESHOLD:
            new = PHASE_CAREFUL
        elif self._turbo_eligible():
            new = PHASE_TURBO
        elif self._aggressive_eligible():
            new = PHASE_AGGRESSIVE
        else:
            new = PHASE_NORMAL

        # TURBO exit: any loss drops to AGGRESSIVE, not SAFE
        if prev == PHASE_TURBO and self.streak < 0:
            new = PHASE_AGGRESSIVE

        # AGGRESSIVE exit: any loss drops to NORMAL
        if prev == PHASE_AGGRESSIVE and self.streak < 0:
            new = PHASE_NORMAL

        # SAFE/CAREFUL exit: only when recovered past SAFE_EXIT_THRESHOLD
        if prev in (PHASE_SAFE, PHASE_ULTRA_SAFE, PHASE_CAREFUL) and new == PHASE_NORMAL:
            if self.bankroll < self.start_amount * SAFE_EXIT_THRESHOLD:
                new = PHASE_CAREFUL   # step to CAREFUL before NORMAL

        if new != prev:
            self.phase = new
            logger.info(
                f"[{self.bot_id}] Phase {prev} → {new} "
                f"(bankroll=${self.bankroll:.4f} streak={self.streak})"
            )
        else:
            self.phase = new

    def _turbo_eligible(self) -> bool:
        """Returns True when TURBO activation conditions are met."""
        if self.streak < TURBO_MIN_STREAK:
            return False
        if self.bankroll < self.start_amount:   # must be at or above start
            return False
        if self.drawdown_pct > 0.005:           # allow tiny rounding noise
            return False
        if len(self._recent_pnl) < TURBO_N_BETS:
            return False
        return sum(self._recent_pnl) > 0

    def _aggressive_eligible(self) -> bool:
        """AGGRESSIVE: win streak >= 2, bankroll at or above start, low drawdown."""
        if self.streak < 2:
            return False
        if self.bankroll < self.start_amount:
            return False
        if self.drawdown_pct > 0.01:
            return False
        return True

    # ── Bet sizing ────────────────────────────────────────────────────────────

    def get_bet_size(self, min_bet: float = 0.000001) -> float:
        """
        Base bet size from phase percentage, scaled by user bet_scale and velocity boost.
        In SAFE/ULTRA_SAFE/FLOOR: scale capped at 1.0 (never amplify when losing).
        """
        pct   = BET_PCT.get(self.phase, BET_PCT[PHASE_NORMAL])
        scale = max(0.1, min(float(getattr(self, "bet_scale", 1.0)), 5.0))
        boost = self._vel_boost_factor if self._vel_boost_remaining > 0 else 1.0

        # Cap scale + boost in losing phases
        if self.phase in (PHASE_SAFE, PHASE_ULTRA_SAFE, PHASE_FLOOR):
            scale = min(scale, 1.0)
            boost = 1.0

        # Drawdown-adjusted Kelly: naturally reduce size as drawdown grows
        dd_adj = max(0.3, 1.0 - self.drawdown_pct * 3.0)

        size = self.bankroll * pct * scale * boost * dd_adj
        return max(size, min_bet)

    def get_paroli_bet(self, base_bet: float, press_count: int) -> float:
        """Anti-Martingale: double on each win, capped by phase limit and 8% bankroll."""
        max_press = PAROLI_LIMIT.get(self.phase, 0)
        if max_press == 0:
            return base_bet
        if self.phase in (PHASE_SAFE, PHASE_ULTRA_SAFE, PHASE_FLOOR, PHASE_CAREFUL):
            return base_bet   # flat bets in recovery phases
        presses = min(press_count, max_press)
        bet     = base_bet * (2 ** presses)
        cap_pct = 0.08 if self.phase == PHASE_TURBO else 0.05
        return min(max(bet, 0.000001), self.bankroll * cap_pct)

    def get_martingale_bet(self, base_bet: float, loss_step: int) -> float:
        """Soft loss-recovery: 1.4× per step, max 4 steps."""
        step    = min(loss_step, MARTI_STEPS)
        bet     = base_bet * (MARTI_MULT ** step)
        cap_pct = 0.02 if self.phase in (PHASE_FLOOR, PHASE_ULTRA_SAFE) else 0.05
        return min(max(bet, 0.000001), self.bankroll * cap_pct)

    # ── Result dict ───────────────────────────────────────────────────────────

    def _result(self, withdrawn: float, locked: float, action: str = "CONTINUE") -> dict:
        return {
            "action"          : action,
            "phase"           : self.phase,
            "withdraw"        : round(withdrawn, 6),
            "locked"          : round(locked, 6),
            "bankroll"        : round(self.bankroll, 6),
            "total_withdrawn" : round(self.total_withdrawn, 6),
            "vault"           : round(self.vault, 6),
            "progress_x"      : round(self.progress_multiplier, 3),
            "danger"          : self.danger,
            "milestone"       : self.milestone_hit,
            "cooling_down"    : self.is_cooling_down,
            "cb_reason"       : self._cb_reason if self.is_cooling_down else "",
        }

    # ── User actions ──────────────────────────────────────────────────────────

    def continue_after_milestone(self, new_target_multiplier: float):
        self.continue_to   = f"{new_target_multiplier}x"
        self.milestone_hit = False
        self.target        = self.start_amount * new_target_multiplier
        self._recalculate_phase()
        logger.info(f"[{self.bot_id}] Continuing to {new_target_multiplier}x")

    def send_to_vault(self, amount: float) -> float:
        sendable              = min(amount, self.total_withdrawn)
        self.total_withdrawn -= sendable
        self.vault           += sendable
        logger.info(f"[{self.bot_id}] Vault +${sendable:.2f} (total=${self.vault:.2f})")
        return sendable

    def reconfigure(self, start_amount: float, target_amount: float,
                    floor_amount: float | None = None):
        self.start_amount     = start_amount
        self.initial_bankroll = start_amount
        self.bankroll         = start_amount
        self.current_bankroll = start_amount
        self.target           = target_amount
        self.floor            = floor_amount or start_amount * 0.40
        self.total_withdrawn  = 0.0
        self.vault            = 0.0
        self.milestone_hit    = False
        self.continue_to      = None
        self.is_target_hit    = False
        self._last_lock_level = 0
        self._recent_pnl.clear()
        self._vel_samples.clear()
        self._cooldown_until  = 0.0
        self._recalculate_phase()
        logger.info(
            f"[{self.bot_id}] Reconfigured: start=${start_amount} "
            f"target=${target_amount} floor=${self.floor}"
        )

    # ── Status snapshot ───────────────────────────────────────────────────────

    def status(self) -> dict:
        elapsed = time.time() - self.start_time
        return {
            "bot_id"          : self.bot_id,
            "phase"           : self.phase,
            "bankroll"        : round(self.bankroll, 4),
            "initial"         : self.start_amount,
            "start_amount"    : self.start_amount,
            "target_amount"   : self.target,
            "floor_amount"    : round(self.floor, 4),
            "total_withdrawn" : round(self.total_withdrawn, 4),
            "vault"           : round(self.vault, 4),
            "progress_x"      : round(self.progress_multiplier, 3),
            "progress_pct"    : round(self.progress_pct, 1),
            "peak"            : round(self.peak_bankroll, 4),
            "roi_pct"         : round(self.roi_pct, 2),
            "drawdown_pct"    : round(self.drawdown_pct * 100, 2),
            "to_target"       : round(max(self.target - self.progress, 0), 4),
            "bets"            : self.bet_count,
            "wins"            : self.win_count,
            "losses"          : self.loss_count,
            "win_rate_pct"    : round(self.win_count / max(self.bet_count, 1) * 100, 1),
            "elapsed_min"     : round(elapsed / 60, 1),
            "halted"          : False,
            "cooling_down"    : self.is_cooling_down,
            "cooldown_sec"    : round(self.cooldown_remaining, 0),
            "cb_reason"       : self._cb_reason if self.is_cooling_down else "",
            "danger"          : self.danger,
            "milestone_hit"   : self.milestone_hit,
            "milestoneHit"    : self.milestone_hit,
            "target_hit"      : self.is_target_hit,
            "streak"          : self.streak,
            "withdraw_at"     : round(self.withdraw_trigger, 4),
            "caution_at"      : round(self.caution_threshold, 4),
            "recovery_at"     : round(self.recovery_threshold, 4),
            "turbo_eligible"  : self._turbo_eligible(),
            "vel_boost"       : self._vel_boost_remaining > 0,
        }
