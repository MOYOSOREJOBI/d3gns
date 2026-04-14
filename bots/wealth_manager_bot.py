"""
Wealth Manager Bot — portfolio-level overseer for the entire DeG£N$ fleet.

Responsibilities:
  1. Monitor overall portfolio P&L, drawdown, and daily targets
  2. Coordinate phase transitions (FLOOR → TURBO) based on performance
  3. Trigger portfolio rebalance when drift exceeds threshold
  4. Deploy/recall reserve capital to top-performing lab bots
  5. Enforce profit forcefield and auto-vault rules
  6. Send daily Telegram summary and alerts
  7. Generate the $500 → $10k growth playbook progress report

$500 → $10k Milestones:
  Week 1: $500 → $1,000   (+100%)  Phase: SAFE/CAREFUL
  Week 2: $1k  → $2,500   (+150%)  Phase: CAREFUL/NORMAL
  Week 3: $2.5k→ $5,000   (+100%)  Phase: NORMAL/AGGRESSIVE
  Week 4: $5k  → $10,000  (+100%)  Phase: AGGRESSIVE/TURBO

Daily targets for 20-day run (compounding):
  Day 1–5:   +8% / day  (doubles in ~9 days at 8%)
  Day 6–10:  +7% / day
  Day 11–15: +6% / day
  Day 16–20: +5% / day
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from bots.base_research_bot import BaseResearchBot

logger = logging.getLogger(__name__)

# ── Milestone table ───────────────────────────────────────────────────────────

MILESTONES = [
    {"label": "Week 1 target", "value": 1_000.0,  "days": 7},
    {"label": "Week 2 target", "value": 2_500.0,  "days": 14},
    {"label": "Week 3 target", "value": 5_000.0,  "days": 21},
    {"label": "Week 4 target", "value": 10_000.0, "days": 28},
]

# Phase thresholds — promote phase when bankroll crosses these
PHASE_THRESHOLDS: list[tuple[float, str]] = [
    (0.0,     "floor"),
    (100.0,   "ultra_safe"),
    (200.0,   "safe"),
    (350.0,   "careful"),
    (600.0,   "normal"),
    (1_500.0, "aggressive"),
    (4_000.0, "turbo"),
]

# Daily gain targets by week
DAILY_TARGETS_BY_WEEK = {1: 0.08, 2: 0.07, 3: 0.06, 4: 0.05}


@dataclass
class DailySnapshot:
    date_str:         str
    start_value:      float
    end_value:        float
    pnl:              float
    pnl_pct:          float
    phase:            str
    bets_taken:       int
    wins:             int
    losses:           int
    alerts_sent:      int = 0


class WealthManagerBot(BaseResearchBot):
    bot_id        = "bot_wealth_manager"
    display_name  = "Wealth Manager"
    platform      = "internal"
    mode          = "WEALTH_MANAGEMENT"
    quality_tier  = "S"
    implemented   = True
    default_enabled = True
    description   = "Portfolio overseer: phase management, rebalance, vault, daily reporting"

    # ── Config ────────────────────────────────────────────────────────────────

    REBALANCE_EVERY_N_CYCLES = 50       # rebalance every N run_one_cycle calls
    VAULT_TRIGGER_GAIN_PCT   = 0.10     # vault 50% of gains when day +10%
    EMERGENCY_STOP_PCT       = 0.20     # hard halt if daily loss ≥ 20%
    RESERVE_DEPLOY_THRESHOLD = 0.70     # deploy reserve when top bot score > 0.70

    def __init__(self, starting_capital: float = 500.0) -> None:
        self.starting_capital = starting_capital
        self._bankroll        = starting_capital
        self._daily_start     = starting_capital
        self._peak_value      = starting_capital
        self._session_start   = time.time()
        self._cycle_count     = 0
        self._phase           = "safe"
        self._history:        list[DailySnapshot] = []
        self._vault_balance   = 0.0
        self._total_bets      = 0
        self._total_wins      = 0
        self._emergency_stop  = False
        self._last_rebalance  = 0.0
        self._milestone_idx   = 0

    # ── Core cycle ────────────────────────────────────────────────────────────

    def run_one_cycle(self) -> dict[str, Any]:
        self._cycle_count += 1
        now = time.time()

        # 1. Read live portfolio state
        portfolio = self._get_portfolio_state()
        if portfolio is None:
            return self.emit_signal(
                title="Wealth Manager — no data",
                summary="Portfolio state unavailable (allocator not initialised)",
                confidence=0.0, signal_taken=False, data={},
            )

        bankroll = portfolio["total_value"]
        self._bankroll = bankroll

        # 2. Drawdown protection — emergency stop
        daily_dd = (self._daily_start - bankroll) / max(self._daily_start, 1) * 100
        if daily_dd >= self.EMERGENCY_STOP_PCT * 100:
            self._emergency_stop = True
            self._trigger_emergency_stop(daily_dd)
            return self.emit_signal(
                title="EMERGENCY STOP — daily loss limit hit",
                summary=f"Lost {daily_dd:.1f}% today. All bots halted.",
                confidence=1.0, signal_taken=True,
                data={"emergency_stop": True, "daily_dd_pct": daily_dd},
            )

        # 3. Phase upgrade/downgrade
        new_phase = self._compute_phase(bankroll)
        if new_phase != self._phase:
            self._update_phase(new_phase, bankroll)
            self._phase = new_phase

        # 4. Periodic rebalance
        rebalance_result = None
        if self._cycle_count % self.REBALANCE_EVERY_N_CYCLES == 0:
            rebalance_result = self._do_rebalance()

        # 5. Reserve deployment to hot bots
        reserve_deployed = self._maybe_deploy_reserve(portfolio)

        # 6. Profit vault check
        vault_result = self._maybe_vault(bankroll)

        # 7. Milestone check
        milestone = self._check_milestone(bankroll)

        # 8. Performance metrics
        metrics = self._compute_metrics(bankroll)

        summary_parts = [
            f"Bankroll: ${bankroll:,.2f} | Phase: {self._phase.upper()}",
            f"Daily P&L: {metrics['daily_pnl_pct']:+.1f}% | Peak: ${self._peak_value:,.2f}",
            f"Progress: ${self.starting_capital:,.0f} → $10k = {metrics['progress_pct']:.1f}%",
        ]
        if milestone:
            summary_parts.append(f"MILESTONE: {milestone['label']} reached!")
        if vault_result and vault_result.get("vault_amount", 0) > 0:
            summary_parts.append(f"Vaulted ${vault_result['vault_amount']:.2f}")

        return self.emit_signal(
            title=f"Wealth Manager — ${bankroll:,.0f} | {self._phase.upper()}",
            summary=" | ".join(summary_parts),
            confidence=0.85,
            signal_taken=True,
            data={
                "bankroll":          round(bankroll, 2),
                "daily_start":       round(self._daily_start, 2),
                "peak_value":        round(self._peak_value, 2),
                "vault_balance":     round(self._vault_balance, 2),
                "phase":             self._phase,
                "daily_pnl":         round(metrics["daily_pnl"], 2),
                "daily_pnl_pct":     round(metrics["daily_pnl_pct"], 3),
                "daily_dd_pct":      round(daily_dd, 3),
                "progress_pct":      round(metrics["progress_pct"], 2),
                "total_bets":        self._total_bets,
                "total_wins":        self._total_wins,
                "win_rate":          round(metrics["win_rate"], 3),
                "rebalance":         rebalance_result,
                "reserve_deployed":  reserve_deployed,
                "vault":             vault_result,
                "milestone":         milestone,
                "cycle":             self._cycle_count,
                "uptime_h":          round((time.time() - self._session_start) / 3600, 2),
                "portfolio_detail":  portfolio,
                "emergency_stop":    self._emergency_stop,
            },
        )

    # ── Portfolio state readers ────────────────────────────────────────────────

    def _get_portfolio_state(self) -> dict[str, Any] | None:
        try:
            from services.portfolio_allocator import get_allocator
            allocator = get_allocator(self.starting_capital * 0.80)
            status    = allocator.get_status()

            # Add vault + lab/mall split
            lab_total  = status.get("lab_allocated", 0)
            mall_total = status.get("mall_allocated", 0)
            total      = lab_total + mall_total + status.get("reserve", 0) + self._vault_balance

            return {
                "total_value":   total,
                "lab_capital":   lab_total,
                "mall_capital":  mall_total,
                "reserve":       status.get("reserve", 0),
                "vault":         self._vault_balance,
                "lab_bots":      status.get("lab_bots", 0),
                "mall_bots":     status.get("mall_bots", 0),
                "hot_bots":      status.get("hot_bots", []),
                "cold_bots":     status.get("cold_bots", []),
                "top_performers": status.get("top_performers", []),
            }
        except Exception as exc:
            logger.debug("WealthManager get_portfolio_state: %s", exc)
            return {"total_value": self._bankroll, "lab_capital": 0, "mall_capital": 0,
                    "reserve": 0, "vault": 0, "lab_bots": 0, "mall_bots": 0,
                    "hot_bots": [], "cold_bots": [], "top_performers": []}

    # ── Phase management ──────────────────────────────────────────────────────

    def _compute_phase(self, bankroll: float) -> str:
        phase = "floor"
        for threshold, name in PHASE_THRESHOLDS:
            if bankroll >= threshold:
                phase = name
        return phase

    def _update_phase(self, new_phase: str, bankroll: float) -> None:
        logger.info("Phase change: %s → %s (bankroll $%.2f)", self._phase, new_phase, bankroll)
        try:
            from notifier_telegram import notify_phase_change
            notify_phase_change(self._phase, new_phase, bankroll)
        except Exception:
            pass

    # ── Rebalance ─────────────────────────────────────────────────────────────

    def _do_rebalance(self) -> dict[str, Any] | None:
        try:
            from services.portfolio_allocator import get_allocator
            result = get_allocator().rebalance()
            self._last_rebalance = time.time()
            return result
        except Exception as exc:
            logger.debug("Rebalance error: %s", exc)
            return None

    # ── Reserve deployment ────────────────────────────────────────────────────

    def _maybe_deploy_reserve(self, portfolio: dict) -> dict | None:
        hot_bots = portfolio.get("hot_bots", [])
        if not hot_bots:
            return None
        try:
            from services.portfolio_allocator import get_allocator
            allocator = get_allocator()
            reserve = allocator._reserve
            if reserve < 5.0:
                return None
            # Deploy to first hot bot
            target = hot_bots[0]
            amount = min(reserve * 0.30, 20.0)   # deploy up to 30% of reserve, max $20
            result = allocator.deploy_reserve(target, amount)
            if result.get("deployed"):
                logger.info("Reserve deployed $%.2f to %s", amount, target)
            return result
        except Exception:
            return None

    # ── Vault ─────────────────────────────────────────────────────────────────

    def _maybe_vault(self, bankroll: float) -> dict[str, Any] | None:
        gain = bankroll - self._daily_start
        if gain < self._daily_start * self.VAULT_TRIGGER_GAIN_PCT:
            return None
        vault_amount = gain * 0.50
        self._vault_balance += vault_amount
        # Update peak
        if bankroll > self._peak_value:
            self._peak_value = bankroll
        logger.info("Auto-vault: $%.2f locked (daily gain $%.2f)", vault_amount, gain)
        try:
            from notifier_telegram import send_message
            send_message(
                f"Vault lock: ${vault_amount:.2f} secured "
                f"(daily gain: +${gain:.2f} / +{gain/self._daily_start*100:.1f}%)"
            )
        except Exception:
            pass
        return {"vault_amount": round(vault_amount, 2), "total_vault": round(self._vault_balance, 2)}

    # ── Emergency stop ────────────────────────────────────────────────────────

    def _trigger_emergency_stop(self, dd_pct: float) -> None:
        logger.critical("EMERGENCY STOP triggered — daily drawdown %.1f%%", dd_pct)
        try:
            from services.circuit_breaker import get_breaker
            get_breaker().emergency_halt(reason=f"WealthManager: -{dd_pct:.1f}% daily")
        except Exception:
            pass
        try:
            from notifier_telegram import notify_emergency_stop
            notify_emergency_stop(dd_pct, self._bankroll, f"-{dd_pct:.1f}% daily floor hit")
        except Exception:
            pass

    # ── Metrics ───────────────────────────────────────────────────────────────

    def _compute_metrics(self, bankroll: float) -> dict[str, Any]:
        if bankroll > self._peak_value:
            self._peak_value = bankroll
        daily_pnl     = bankroll - self._daily_start
        daily_pnl_pct = daily_pnl / max(self._daily_start, 1) * 100
        total_gain    = bankroll - self.starting_capital
        progress_pct  = (bankroll - self.starting_capital) / (10_000 - self.starting_capital) * 100
        win_rate      = self._total_wins / max(self._total_bets, 1)
        return {
            "daily_pnl":     daily_pnl,
            "daily_pnl_pct": daily_pnl_pct,
            "total_gain":    total_gain,
            "progress_pct":  min(progress_pct, 100.0),
            "win_rate":      win_rate,
        }

    # ── Milestone tracking ────────────────────────────────────────────────────

    def _check_milestone(self, bankroll: float) -> dict | None:
        while self._milestone_idx < len(MILESTONES):
            m = MILESTONES[self._milestone_idx]
            if bankroll >= m["value"]:
                self._milestone_idx += 1
                logger.info("MILESTONE reached: %s (bankroll $%.2f)", m["label"], bankroll)
                try:
                    from notifier_telegram import send_message
                    send_message(
                        f"MILESTONE REACHED: {m['label']} — "
                        f"${bankroll:,.0f} | {(bankroll/self.starting_capital - 1)*100:.0f}% total gain"
                    )
                except Exception:
                    pass
                return m
            break
        return None

    # ── Daily reset ───────────────────────────────────────────────────────────

    def reset_daily(self, new_day_start: float | None = None) -> None:
        """Call at start of each day. Resets daily floor and sends summary."""
        val = new_day_start or self._bankroll
        snapshot = DailySnapshot(
            date_str=time.strftime("%Y-%m-%d"),
            start_value=self._daily_start,
            end_value=val,
            pnl=val - self._daily_start,
            pnl_pct=(val - self._daily_start) / max(self._daily_start, 1) * 100,
            phase=self._phase,
            bets_taken=self._total_bets,
            wins=self._total_wins,
            losses=self._total_bets - self._total_wins,
        )
        self._history.append(snapshot)
        self._daily_start = val

        # Try to send daily summary
        try:
            from notifier_telegram import send_daily_summary
            send_daily_summary({
                "date":          snapshot.date_str,
                "end_value":     round(val, 2),
                "daily_pnl":     round(snapshot.pnl, 2),
                "daily_pnl_pct": round(snapshot.pnl_pct, 2),
                "phase":         self._phase,
                "total_bets":    self._total_bets,
                "win_rate":      round(self._total_wins / max(self._total_bets, 1), 3),
                "vault":         round(self._vault_balance, 2),
                "progress_pct":  round((val - self.starting_capital) / (10_000 - self.starting_capital) * 100, 1),
            })
        except Exception:
            pass

        # Reset forcefield daily
        try:
            from services.profit_forcefield import get_forcefield
            get_forcefield().reset_daily(val)
        except Exception:
            pass

    def get_growth_playbook(self) -> dict[str, Any]:
        """Return the $500 → $10k growth playbook with current progress."""
        days_elapsed = (time.time() - self._session_start) / 86400
        week = min(int(days_elapsed / 7) + 1, 4)
        daily_target = DAILY_TARGETS_BY_WEEK.get(week, 0.05)

        return {
            "starting_capital":  self.starting_capital,
            "target":            10_000.0,
            "current_value":     round(self._bankroll, 2),
            "vault_locked":      round(self._vault_balance, 2),
            "total_gain_pct":    round((self._bankroll - self.starting_capital) / self.starting_capital * 100, 1),
            "progress_to_10k":   round(self._bankroll / 10_000 * 100, 1),
            "days_elapsed":      round(days_elapsed, 1),
            "current_week":      week,
            "daily_target_pct":  daily_target * 100,
            "phase":             self._phase,
            "milestones":        MILESTONES,
            "next_milestone":    MILESTONES[self._milestone_idx] if self._milestone_idx < len(MILESTONES) else None,
            "history_days":      len(self._history),
        }

    def record_trade(self, won: bool) -> None:
        """Feed trade outcomes into win rate tracking."""
        self._total_bets += 1
        if won:
            self._total_wins += 1


# ── Global singleton ──────────────────────────────────────────────────────────
_wealth_manager: WealthManagerBot | None = None


def get_wealth_manager(starting_capital: float = 500.0) -> WealthManagerBot:
    global _wealth_manager
    if _wealth_manager is None:
        _wealth_manager = WealthManagerBot(starting_capital=starting_capital)
    return _wealth_manager
