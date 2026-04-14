"""
Daily/periodic summary sent via Telegram.
Call start_daily_summary(rms) after orchestrator launches.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)


def start_daily_summary(rms: list[Any], interval_hours: float = 6) -> threading.Thread:
    """Send a periodic P&L report via Telegram every `interval_hours` hours."""

    def _loop() -> None:
        while True:
            time.sleep(interval_hours * 3600)
            try:
                from notifier_telegram import send_telegram

                total_bank    = sum(r.current_bankroll for r in rms)
                total_locked  = sum(r.total_withdrawn   for r in rms)
                total_bets    = sum(r.bet_count          for r in rms)
                total_initial = sum(r.initial_bankroll   for r in rms)
                overall_pnl   = (total_bank + total_locked) - total_initial
                pnl_pct       = (overall_pnl / total_initial * 100) if total_initial else 0

                lines = [
                    f"📊 DeG£N$ {interval_hours:.0f}h Report",
                    "=" * 28,
                    f"💰 Active:  ${total_bank:.2f}",
                    f"🔒 Vault:   ${total_locked:.2f}",
                    f"📈 Total:   ${total_bank + total_locked:.2f}",
                    f"{'🟢' if overall_pnl >= 0 else '🔴'} P&L: ${overall_pnl:+.2f} ({pnl_pct:+.1f}%)",
                    f"🎰 Bets:    {total_bets}",
                    "=" * 28,
                ]

                for r in rms:
                    s       = r.status()
                    bot_pnl = r.current_bankroll + r.total_withdrawn - r.initial_bankroll
                    emoji   = "🟢" if bot_pnl >= 0 else "🔴"
                    lines.append(
                        f"{emoji} {r.bot_id}: ${r.current_bankroll:.2f} "
                        f"({s['phase']}) P&L:${bot_pnl:+.2f}"
                    )

                send_telegram("\n".join(lines))
            except Exception as exc:
                logger.error(f"Daily summary error: {exc}")

    t = threading.Thread(target=_loop, daemon=True, name="daily_summary")
    t.start()
    return t
