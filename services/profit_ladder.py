"""
Profit Lock Ladder — Tiered automatic profit extraction.

Tier 0: +20% profit, lock excess, repeat 5 times
Tier 1: +300% profit, lock excess, repeat 5 times
Tier 2: +1000% profit, lock excess, repeat 5 times
Tier 3: +2000% profit, lock excess, then stop (terminal)
"""

from __future__ import annotations

import logging
from typing import Any


logger = logging.getLogger(__name__)

TIER_MULTIPLIERS = [1.20, 4.00, 11.00, 21.00]
CYCLES_PER_TIER = 5
TIER_NAMES = ["+20%", "+300%", "+1000%", "+2000%"]


class ProfitLadder:
    def __init__(self, db_module: Any, vault: Any):
        self._db = db_module
        self._vault = vault
        self._state = self._load_state()

    def _load_state(self) -> dict[str, Any]:
        try:
            rows = self._db.get_settings_by_prefix("ladder_")
            state = {row["key"]: row["value"] for row in rows}
            return {
                "tier_index": int(state.get("ladder_tier_index", 0) or 0),
                "cycle_count": int(state.get("ladder_cycle_count", 0) or 0),
                "active_base": float(state.get("ladder_active_base", 0) or 0),
                "terminal": str(state.get("ladder_terminal", "false")).lower() == "true",
            }
        except Exception:
            return {"tier_index": 0, "cycle_count": 0, "active_base": 0.0, "terminal": False}

    def _save_state(self) -> None:
        for key, value in (
            ("ladder_tier_index", str(self._state["tier_index"])),
            ("ladder_cycle_count", str(self._state["cycle_count"])),
            ("ladder_active_base", str(self._state["active_base"])),
            ("ladder_terminal", str(self._state["terminal"]).lower()),
        ):
            self._db.set_setting(key, value)

    def initialize(self, starting_capital: float) -> None:
        if self._state["active_base"] <= 0 and starting_capital > 0:
            self._state["active_base"] = float(starting_capital)
            self._save_state()

    def check_and_lock(self, working_capital: float, bot_id: str = "system") -> dict[str, Any]:
        if self._state["terminal"]:
            return {"action": "terminal_complete", "locked": 0.0, "tier": "DONE", "terminal": True}

        tier_index = int(self._state["tier_index"])
        if tier_index >= len(TIER_MULTIPLIERS):
            self._state["terminal"] = True
            self._save_state()
            return {"action": "terminal_complete", "locked": 0.0, "tier": "DONE", "terminal": True}

        base = float(self._state["active_base"] or 0.0)
        if base <= 0:
            return {"action": "no_base", "locked": 0.0, "tier": TIER_NAMES[tier_index], "terminal": False}

        multiplier = TIER_MULTIPLIERS[tier_index]
        target = base * multiplier
        if float(working_capital or 0.0) < target:
            progress = 0.0
            if target > base:
                progress = (float(working_capital or 0.0) - base) / (target - base)
            return {
                "action": "waiting",
                "locked": 0.0,
                "tier": TIER_NAMES[tier_index],
                "tier_index": tier_index,
                "cycle": int(self._state["cycle_count"]),
                "target": round(target, 2),
                "progress": round(max(0.0, min(1.0, progress)), 4),
                "terminal": False,
            }

        excess = max(0.0, float(working_capital or 0.0) - base)
        locked = float(self._vault.lock(bot_id, excess, reason=f"ladder_tier{tier_index}_cycle{self._state['cycle_count']}"))
        self._state["cycle_count"] = int(self._state["cycle_count"]) + 1
        self._state["active_base"] = max(0.0, float(working_capital or 0.0) - excess)

        if self._state["cycle_count"] >= CYCLES_PER_TIER:
            self._state["tier_index"] = tier_index + 1
            self._state["cycle_count"] = 0
            if self._state["tier_index"] >= len(TIER_MULTIPLIERS):
                self._state["terminal"] = True

        self._save_state()
        logger.info(
            "[ProfitLadder] locked $%.2f | tier=%s cycle=%s/%s",
            locked,
            TIER_NAMES[min(tier_index, len(TIER_NAMES) - 1)],
            self._state["cycle_count"],
            CYCLES_PER_TIER,
        )
        return {
            "action": "locked",
            "locked": round(locked, 2),
            "tier": TIER_NAMES[min(tier_index, len(TIER_NAMES) - 1)],
            "tier_index": tier_index,
            "cycle": int(self._state["cycle_count"]),
            "cycles_per_tier": CYCLES_PER_TIER,
            "terminal": bool(self._state["terminal"]),
        }

    def status(self) -> dict[str, Any]:
        if self._state["terminal"]:
            tier_name = "DONE"
            multiplier = None
        else:
            tier_name = TIER_NAMES[min(int(self._state["tier_index"]), len(TIER_NAMES) - 1)]
            multiplier = TIER_MULTIPLIERS[min(int(self._state["tier_index"]), len(TIER_MULTIPLIERS) - 1)]
        return {
            **self._state,
            "tier_name": tier_name,
            "multiplier": multiplier,
            "cycles_per_tier": CYCLES_PER_TIER,
        }
