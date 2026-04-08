"""
Vault — immutable profit-lock ledger.

Once money is locked into the vault it NEVER comes back out.
Vault total only ever increases.
"""

import logging
import time

logger = logging.getLogger(__name__)


class Vault:
    """
    Thin wrapper around the DB vault table.
    Keeps an in-process running total so server code can read it
    without a DB round-trip every time.
    """

    def __init__(self, db_module):
        """
        Args:
            db_module: the database module (import database as db → pass db here).
        """
        self._db   = db_module
        self._total: float = 0.0
        self._load_total()

    # ── Write ──────────────────────────────────────────────────────────────────

    def lock(self, bot_id: str, amount: float, reason: str = "ratchet") -> float:
        """
        Irreversibly lock `amount` to vault.
        Returns the amount actually locked (≥ 0).
        """
        if amount <= 0:
            return 0.0

        self._total += amount
        try:
            self._db.save_vault_lock(bot_id, amount, reason, self._total)
        except Exception as exc:
            logger.error(f"[Vault] DB write failed for {bot_id}: {exc}")

        logger.info(
            f"[Vault] LOCKED ${amount:.4f} from {bot_id} ({reason}) "
            f"| vault_total=${self._total:.2f}"
        )
        return amount

    # ── Read ───────────────────────────────────────────────────────────────────

    def total(self) -> float:
        """Sum of all locked amounts."""
        return self._total

    def history(self, limit: int = 50) -> list[dict]:
        """Last `limit` vault transactions, most recent first."""
        try:
            return self._db.get_vault_history(limit=limit)
        except Exception as exc:
            logger.error(f"[Vault] history fetch failed: {exc}")
            return []

    def refresh_total(self) -> float:
        """Re-fetch total from DB (use if multiple processes write to vault)."""
        self._load_total()
        return self._total

    # ── Internal ───────────────────────────────────────────────────────────────

    def _load_total(self) -> None:
        try:
            rows = self._db.get_vault_history(limit=10_000)
            self._total = sum(r.get("amount", 0) for r in rows)
        except Exception:
            self._total = 0.0
