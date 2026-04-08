"""
Multi-bot orchestrator – 6 bots in parallel (3 Stake + 3 Polymarket).

Each bot gets its own RiskManager (RM). The RM is created HERE and passed
into both the strategy and the bot object – no duplicate RMs, no tracking bugs.

Bot layout:
  bot1_dice    – Stake Dice
  bot2_limbo   – Stake Limbo
  bot3_mines   – Stake Mines
  bot4_poly    – Polymarket
  bot5_poly    – Polymarket
  bot6_poly    – Polymarket

Status is printed every STATUS_LOG_INTERVAL seconds showing each bot's
phase, bankroll, progress toward 10x, and total locked profits.
"""

import time
import logging
import threading

from config import (
    BOT_INITIAL_BANK, BOT_PLATFORMS, NUM_BOTS,
    STATUS_LOG_INTERVAL, BET_DELAY_SECONDS,
    LOG_FILE, LOG_LEVEL,
    TARGET_MULTIPLIER,
)
from risk_manager import RiskManager
from stake_strategies import make_strategy

# ── Logging setup ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level    = getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format   = "%(asctime)s | %(levelname)-7s | %(message)s",
    handlers = [
        logging.StreamHandler(),
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
#  INDIVIDUAL BOT LOOPS
# ═══════════════════════════════════════════════════════════════

def _stake_loop(bot_id: str, game: str, rm: RiskManager,
                stop_event: threading.Event):
    """Main loop for one Stake game bot."""
    strategy = make_strategy(game, rm)
    logger.info(
        f"[{bot_id}] Stake/{game} started | "
        f"bank=${rm.initial_bankroll} | target=${rm.target_amount:.0f}"
    )

    while not stop_event.is_set():
        # Auto-resume from circuit-breaker cooldown
        if rm.is_cooling_down:
            time.sleep(0.5)
            continue
        try:
            net    = strategy.run_one_bet()
            result = rm.record_bet_result(net)

            if result["action"] == "TARGET_HIT":
                logger.info(
                    f"[{bot_id}] 🚀 10x TARGET HIT! "
                    f"progress={result['progress_x']}x | "
                    f"withdrawn=${rm.total_withdrawn:.2f}"
                )
                # Keep running to grow further – don't stop on target

        except Exception as exc:
            logger.error(f"[{bot_id}] error: {exc}", exc_info=True)
            time.sleep(5)


def _poly_loop(bot_id: str, rm: RiskManager, stop_event: threading.Event):
    """Main loop for one Polymarket bot."""
    from polymarket_bot import PolymarketBot

    bot = PolymarketBot(rm)   # RM passed in – no duplicate created

    if bot.client is None:
        logger.warning(
            f"[{bot_id}] Polymarket unavailable (no credentials or import error). "
            "Thread exiting."
        )
        return

    logger.info(
        f"[{bot_id}] Polymarket started | "
        f"bank=${rm.initial_bankroll} | target=${rm.target_amount:.0f}"
    )

    while not stop_event.is_set():
        if rm.is_cooling_down:
            time.sleep(0.5)
            continue
        try:
            net = bot.run_one_cycle()

            if net != 0.0:
                result = rm.record_bet_result(net)
                if result["action"] == "TARGET_HIT":
                    logger.info(f"[{bot_id}] 🚀 10x TARGET HIT!")

        except Exception as exc:
            logger.error(f"[{bot_id}] error: {exc}", exc_info=True)

        # Scan pause (interruptible)
        for _ in range(int(25 / max(BET_DELAY_SECONDS, 0.5))):
            if stop_event.is_set():
                break
            time.sleep(min(BET_DELAY_SECONDS, 1.0))


# ═══════════════════════════════════════════════════════════════
#  STATUS PRINTER
# ═══════════════════════════════════════════════════════════════

def _status_printer(rms: list[RiskManager], stop_event: threading.Event):
    while not stop_event.is_set():
        time.sleep(STATUS_LOG_INTERVAL)
        if stop_event.is_set():
            break

        total_in       = sum(rm.initial_bankroll  for rm in rms)
        total_active   = sum(rm.current_bankroll  for rm in rms)
        total_locked   = sum(rm.total_withdrawn   for rm in rms)
        total_progress = total_active + total_locked
        overall_x      = total_progress / total_in if total_in else 0

        sep   = "═" * 72
        lines = [
            "",
            sep,
            f"  6-BOT PORTFOLIO  |  {overall_x:.2f}x  |  "
            f"target={TARGET_MULTIPLIER:.0f}x per bot",
            f"  Total deployed : ${total_in:.2f}",
            f"  Active (in play): ${total_active:.4f}",
            f"  Locked profits  : ${total_locked:.4f}",
            f"  Combined value  : ${total_progress:.4f}",
            "─" * 72,
            f"  {'Bot':<14} {'Phase':<10} {'Bank':>10} {'Progress':>10} "
            f"{'Locked':>10} {'Bets':>6} {'W%':>5} {'Status'}",
            "─" * 72,
        ]

        for rm in rms:
            s    = rm.status()
            flag = ""
            if s.get("cooling_down"):
                flag = f"⏸  CB ({s.get('cb_reason','')})"
            elif s["target_hit"]:
                flag = "🚀 10x!"
            elif s["phase"] in ("floor", "ultra_safe"):
                flag = f"⚠️  {s['phase']}"
            lines.append(
                f"  {s['bot_id']:<14} {s['phase']:<10} "
                f"${s['bankroll']:>9.4f} "
                f"{s['progress_x']:>9.2f}x "
                f"${s['total_withdrawn']:>9.4f} "
                f"{s['bets']:>6} "
                f"{s['win_rate_pct']:>4.0f}% "
                f"{flag}"
            )

        lines.append(sep)
        logger.info("\n".join(lines))


# ═══════════════════════════════════════════════════════════════
#  ORCHESTRATOR
# ═══════════════════════════════════════════════════════════════

class BotOrchestrator:

    # Named bot layout (Stake core + expanded Polymarket suite)
    BOT_SPECS = [
        ("bot1_dice",  "stake_dice"),
        ("bot2_limbo", "stake_limbo"),
        ("bot3_mines", "stake_mines"),
        ("bot4_poly",  "polymarket"),
        ("bot5_poly",  "polymarket"),
        ("bot6_poly",  "polymarket"),
        ("bot7_poly",  "polymarket"),
        ("bot8_poly",  "polymarket"),
        ("bot9_poly",  "polymarket"),
    ]

    def __init__(self):
        self.stop_event = threading.Event()
        self.threads    = []
        self.rms        = []

    def start(self):
        # Create all RMs up front so status printer can see them immediately
        rm_map = {
            bot_id: RiskManager(bot_id, BOT_INITIAL_BANK)
            for bot_id, _ in self.BOT_SPECS
        }
        self.rms = list(rm_map.values())

        # Spawn bot threads
        for bot_id, platform in self.BOT_SPECS:
            rm = rm_map[bot_id]
            if platform == "polymarket":
                target = lambda b=bot_id, r=rm: _poly_loop(b, r, self.stop_event)
            else:
                game   = platform.replace("stake_", "")
                target = lambda b=bot_id, g=game, r=rm: _stake_loop(
                    b, g, r, self.stop_event
                )

            t = threading.Thread(target=target, name=bot_id, daemon=True)
            self.threads.append(t)

        # Spawn status printer
        status_t = threading.Thread(
            target = _status_printer,
            args   = (self.rms, self.stop_event),
            daemon = True,
            name   = "status_printer",
        )

        # Start everything
        for t in self.threads:
            t.start()
        status_t.start()

        self._print_launch_banner()

        try:
            while any(t.is_alive() for t in self.threads):
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("\nCtrl+C received – stopping all bots…")
            self.stop()

    def stop(self):
        self.stop_event.set()
        for t in self.threads:
            t.join(timeout=10)
        self._print_final_report()

    def _print_launch_banner(self):
        total = len(self.BOT_SPECS) * BOT_INITIAL_BANK
        floor = BOT_INITIAL_BANK * 0.80
        goal  = BOT_INITIAL_BANK * TARGET_MULTIPLIER
        logger.info(
            f"\n{'╔' + '═'*60 + '╗'}\n"
            f"║  6-BOT SYSTEM LAUNCHED{'':37}║\n"
            f"║  Bots 1-3 : Stake  Dice / Limbo / Mines{'':19}║\n"
            f"║  Bots 4-6 : Polymarket (3 parallel hunters){'':15}║\n"
            f"║{'':60}║\n"
            f"║  Per-bot bankroll : ${BOT_INITIAL_BANK:<39.2f}║\n"
            f"║  Total deployed   : ${total:<39.2f}║\n"
            f"║  Hard floor/bot   : ${floor:<39.2f}║\n"
            f"║  10x target/bot   : ${goal:<39.2f}║\n"
            f"╚{'═'*60}╝"
        )

    def _print_final_report(self):
        sep   = "═" * 60
        lines = ["\n" + sep, "  FINAL REPORT", "─" * 60]
        grand_locked = 0.0
        grand_active = 0.0
        for rm in self.rms:
            s = rm.status()
            grand_locked += s["total_withdrawn"]
            grand_active += s["bankroll"]
            lines.append(
                f"  {s['bot_id']:<14} | "
                f"bank=${s['bankroll']:.4f} | "
                f"locked=${s['total_withdrawn']:.4f} | "
                f"progress={s['progress_x']:.2f}x | "
                f"bets={s['bets']} | "
                f"{'HALTED' if s['halted'] else ('10x!' if s['target_hit'] else 'ok')}"
            )
        lines += [
            "─" * 60,
            f"  Grand total value : ${grand_active + grand_locked:.4f}",
            f"  Total profits locked: ${grand_locked:.4f}",
            sep,
        ]
        logger.info("\n".join(lines))


if __name__ == "__main__":
    BotOrchestrator().start()
