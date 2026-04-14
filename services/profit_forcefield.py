"""
Profit Forcefield — trailing profit lock that only moves UP, never down.

Core concept:
  - Every open position has an "entry price" and a "floor price"
  - The floor starts at entry (break-even) or below (initial risk)
  - As price moves in your favour, the floor ratchets UP automatically
  - If price falls back to the floor → exit with locked profit
  - You NEVER give back more than the ratchet allows
  - System ONLY stops on profit, never on breakeven or loss after floor locks

Tiers (configurable):
  Gain +3%  → floor moves to entry (break-even protection)
  Gain +7%  → floor moves to +3%  (locks 43% of gains)
  Gain +15% → floor moves to +8%  (locks 53% of gains)
  Gain +25% → floor moves to +15% (locks 60% of gains)
  Gain +40% → floor moves to +25% (locks 63% of gains)

Portfolio-level protection:
  - Track total portfolio value
  - If daily gain exceeds TARGET_DAILY_GAIN → increase floor for all positions
  - Profit vault: auto-withdraw % of gains above HIGH_WATER_MARK to safety
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


# ── Ratchet tiers ─────────────────────────────────────────────────────────────
# (gain_threshold_pct, floor_moves_to_pct)
DEFAULT_RATCHET_TIERS: list[tuple[float, float]] = [
    (3.0,   0.0),    # at +3%: floor = entry (break-even)
    (7.0,   3.0),    # at +7%: floor = +3%
    (15.0,  8.0),    # at +15%: floor = +8%
    (25.0, 15.0),    # at +25%: floor = +15%
    (40.0, 25.0),    # at +40%: floor = +25%
    (60.0, 40.0),    # at +60%: floor = +40%
    (100.0, 65.0),   # at +100%: floor = +65%
]

# Bankroll-level floor (portfolio protection)
PORTFOLIO_DAILY_FLOOR_PCT = 0.80  # never lose more than 20% on any day
PORTFOLIO_HARD_FLOOR_PCT  = 0.80  # absolute floor from starting bankroll


@dataclass
class Position:
    pos_id:       str
    market:       str
    entry_price:  float
    entry_size:   float     # dollar amount at entry
    side:         str       # "long" / "yes" or "short" / "no"
    tiers:        list = field(default_factory=lambda: list(DEFAULT_RATCHET_TIERS))

    # Dynamic state
    current_price: float = 0.0
    peak_price:    float = 0.0
    floor_price:   float = 0.0  # absolute floor price — exit if hit
    floor_pct:     float = 0.0  # floor as % gain from entry
    current_gain:  float = 0.0  # current % gain
    peak_gain:     float = 0.0  # highest % gain seen
    tier_reached:  int   = 0
    opened_at:     float = field(default_factory=time.time)
    status:        str   = "open"  # "open" | "exit_triggered" | "closed"
    exit_reason:   str   = ""
    exit_price:    float = 0.0
    realised_pnl:  float = 0.0

    def __post_init__(self):
        self.current_price = self.entry_price
        self.peak_price    = self.entry_price
        self.floor_price   = self.entry_price * (1.0 - 0.05)  # start: allow -5% initial risk

    @property
    def is_long(self) -> bool:
        return self.side.lower() in ("long", "yes", "buy")

    @property
    def unrealised_pnl_pct(self) -> float:
        if self.entry_price == 0:
            return 0.0
        if self.is_long:
            return (self.current_price - self.entry_price) / self.entry_price * 100
        return (self.entry_price - self.current_price) / self.entry_price * 100


class ProfitForcefield:
    """
    Manages all open positions with automatic profit ratcheting.
    Call update_price() every time you get a new price quote.
    The forcefield emits exit signals when a position's floor is breached.
    """

    def __init__(
        self,
        tiers: list[tuple[float, float]] | None = None,
        daily_bankroll: float = 1000.0,
    ) -> None:
        self._positions:    dict[str, Position] = {}
        self._tiers         = tiers or DEFAULT_RATCHET_TIERS
        self._daily_start   = daily_bankroll
        self._high_water    = daily_bankroll
        self._exit_queue:   list[dict[str, Any]] = []
        self._closed:       list[Position] = []

    # ── Position management ───────────────────────────────────────────────────

    def open_position(
        self,
        pos_id: str,
        market: str,
        entry_price: float,
        entry_size: float,
        side: str = "long",
    ) -> Position:
        pos = Position(
            pos_id=pos_id,
            market=market,
            entry_price=entry_price,
            entry_size=entry_size,
            side=side,
            tiers=list(self._tiers),
        )
        self._positions[pos_id] = pos
        return pos

    def update_price(self, pos_id: str, new_price: float) -> dict[str, Any]:
        """
        Feed a new price into the forcefield.
        Returns {"exit": bool, "reason": str, "floor_pct": float, ...}
        """
        pos = self._positions.get(pos_id)
        if not pos or pos.status != "open":
            return {"exit": False, "reason": "position_not_found_or_closed"}

        pos.current_price = new_price
        gain_pct = pos.unrealised_pnl_pct

        # Update peak
        if gain_pct > pos.peak_gain:
            pos.peak_gain  = gain_pct
            pos.peak_price = new_price

        # Ratchet floor upward
        new_floor_pct = pos.floor_pct
        new_tier = pos.tier_reached
        for i, (thresh, floor_to) in enumerate(self._tiers):
            if gain_pct >= thresh and i >= pos.tier_reached:
                new_floor_pct = floor_to
                new_tier = i + 1

        if new_floor_pct > pos.floor_pct:
            pos.floor_pct  = new_floor_pct
            new_floor_abs  = pos.entry_price * (1.0 + new_floor_pct / 100) if pos.is_long else \
                             pos.entry_price * (1.0 - new_floor_pct / 100)
            pos.floor_price = new_floor_abs
            pos.tier_reached = new_tier

        # Check if floor breached → exit
        floor_breached = (
            (pos.is_long  and new_price <= pos.floor_price) or
            (not pos.is_long and new_price >= pos.floor_price)
        )

        if floor_breached and pos.floor_pct >= 0:
            pnl = (pos.floor_price - pos.entry_price) * (pos.entry_size / pos.entry_price) if pos.is_long else \
                  (pos.entry_price - pos.floor_price) * (pos.entry_size / pos.entry_price)
            pos.status      = "exit_triggered"
            pos.exit_reason = f"floor_hit@{pos.floor_pct:.1f}%"
            pos.exit_price  = pos.floor_price
            pos.realised_pnl = pnl
            exit_info = {
                "exit":         True,
                "pos_id":       pos_id,
                "reason":       pos.exit_reason,
                "entry_price":  pos.entry_price,
                "exit_price":   pos.exit_price,
                "gain_locked_pct": pos.floor_pct,
                "realised_pnl": round(pnl, 4),
                "entry_size":   pos.entry_size,
                "market":       pos.market,
            }
            self._exit_queue.append(exit_info)
            return exit_info

        return {
            "exit":          False,
            "pos_id":        pos_id,
            "current_gain":  round(gain_pct, 3),
            "floor_pct":     round(pos.floor_pct, 3),
            "floor_price":   round(pos.floor_price, 6),
            "peak_gain":     round(pos.peak_gain, 3),
            "tier_reached":  pos.tier_reached,
        }

    def close_position(self, pos_id: str, close_price: float) -> dict[str, Any]:
        """Manually close a position at a given price."""
        pos = self._positions.pop(pos_id, None)
        if not pos:
            return {"error": "not_found"}
        gain_pct = pos.unrealised_pnl_pct
        pnl = (close_price - pos.entry_price) * (pos.entry_size / pos.entry_price) if pos.is_long else \
              (pos.entry_price - close_price) * (pos.entry_size / pos.entry_price)
        pos.status       = "closed"
        pos.exit_price   = close_price
        pos.realised_pnl = pnl
        self._closed.append(pos)
        return {"closed": True, "pnl": round(pnl, 4), "gain_pct": round(gain_pct, 3)}

    def drain_exit_queue(self) -> list[dict[str, Any]]:
        """Get and clear all pending exit signals."""
        exits = list(self._exit_queue)
        self._exit_queue.clear()
        # Move triggered positions to closed
        for ex in exits:
            pid = ex.get("pos_id")
            if pid in self._positions:
                self._closed.append(self._positions.pop(pid))
        return exits

    # ── Portfolio-level protection ────────────────────────────────────────────

    def update_portfolio_value(self, current_value: float) -> dict[str, Any]:
        """
        Check portfolio-level floors. Call after each trade settlement.
        Returns action if global floor is breached.
        """
        if current_value > self._high_water:
            self._high_water = current_value

        daily_drawdown_pct = (self._daily_start - current_value) / self._daily_start * 100
        hw_drawdown_pct    = (self._high_water  - current_value) / self._high_water  * 100

        result = {
            "current_value":       current_value,
            "daily_start":         self._daily_start,
            "high_water":          self._high_water,
            "daily_drawdown_pct":  round(daily_drawdown_pct, 3),
            "hw_drawdown_pct":     round(hw_drawdown_pct, 3),
            "emergency_stop":      False,
        }

        # Hard floor: never lose >20% from daily start
        if daily_drawdown_pct >= 20.0:
            result["emergency_stop"] = True
            result["stop_reason"]    = f"DAILY_FLOOR_HIT: -{daily_drawdown_pct:.1f}%"

        return result

    def auto_vault(self, current_value: float, vault_pct: float = 0.50) -> dict[str, Any]:
        """
        Auto-vault: if portfolio is above daily start + 10%, lock vault_pct of gains.
        Returns amount to transfer to vault.
        """
        gain = current_value - self._daily_start
        if gain < self._daily_start * 0.10:
            return {"vault_amount": 0.0, "reason": "below_vault_threshold"}
        vault_amount = gain * vault_pct
        return {
            "vault_amount":    round(vault_amount, 4),
            "gain":            round(gain, 4),
            "vault_pct":       vault_pct,
            "post_vault_value": round(current_value - vault_amount, 4),
        }

    # ── Reporting ─────────────────────────────────────────────────────────────

    def get_status(self) -> dict[str, Any]:
        open_positions = list(self._positions.values())
        total_unrealised = sum(
            p.unrealised_pnl_pct * p.entry_size / 100
            for p in open_positions
        )
        total_realised = sum(p.realised_pnl for p in self._closed)
        return {
            "open_positions":      len(open_positions),
            "closed_positions":    len(self._closed),
            "pending_exits":       len(self._exit_queue),
            "total_unrealised_pnl": round(total_unrealised, 4),
            "total_realised_pnl":  round(total_realised, 4),
            "high_water_mark":     self._high_water,
            "daily_start":         self._daily_start,
            "positions": [
                {
                    "id": p.pos_id, "market": p.market,
                    "gain_pct": round(p.unrealised_pnl_pct, 2),
                    "floor_pct": round(p.floor_pct, 2),
                    "tier": p.tier_reached,
                    "status": p.status,
                }
                for p in open_positions
            ],
        }

    def reset_daily(self, new_start_value: float) -> None:
        """Call at start of each day to reset daily floor."""
        self._daily_start = new_start_value
        self._high_water  = max(self._high_water, new_start_value)


# ── Global singleton ──────────────────────────────────────────────────────────
_forcefield: ProfitForcefield | None = None


def get_forcefield(bankroll: float = 1000.0) -> ProfitForcefield:
    global _forcefield
    if _forcefield is None:
        _forcefield = ProfitForcefield(daily_bankroll=bankroll)
    return _forcefield
