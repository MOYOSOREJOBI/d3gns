"""
Entry point – validates config then launches the orchestrator.

Before running:
  1. pip install -r requirements.txt
  2. Edit config.py:
     - Set STAKE_API_TOKEN  (get from browser DevTools on stake.com)
     - Set POLY_PRIVATE_KEY + POLY_API_KEY etc. for Polymarket
     - Set NUM_BOTS, BOT_INITIAL_BANK, BOT_PLATFORMS to your preference
  3. python main.py
"""

import sys
import logging
import os

logger = logging.getLogger(__name__)


def validate_config():
    from config import (
        STAKE_API_TOKEN, POLY_PRIVATE_KEY, BOT_PLATFORMS,
        BOT_INITIAL_BANK, HARD_STOP_PCT, WITHDRAW_TRIGGER_PCT,
    )

    errors   = []
    warnings = []

    needs_stake = any("stake" in p for p in BOT_PLATFORMS)
    needs_poly  = any("polymarket" in p for p in BOT_PLATFORMS)

    if needs_stake and not STAKE_API_TOKEN:
        errors.append(
            "STAKE_API_TOKEN is not set.\n"
            "  How to get it:\n"
            "  1. Log in to stake.com\n"
            "  2. Open browser DevTools (F12) → Network tab\n"
            "  3. Place any manual bet\n"
            "  4. Find the GraphQL request → Headers → x-access-token\n"
            "  OR run:  python get_stake_token.py"
        )

    if needs_poly and not POLY_PRIVATE_KEY:
        warnings.append(
            "POLY_PRIVATE_KEY not set – Polymarket bots (4-6) will be disabled.\n"
            "  To enable: python -m py_clob_client create-api-key"
        )

    if BOT_INITIAL_BANK <= 0:
        errors.append(f"BOT_INITIAL_BANK must be positive, got {BOT_INITIAL_BANK}.")

    if HARD_STOP_PCT >= WITHDRAW_TRIGGER_PCT:
        errors.append(
            f"HARD_STOP_PCT ({HARD_STOP_PCT}) must be < "
            f"WITHDRAW_TRIGGER_PCT ({WITHDRAW_TRIGGER_PCT})."
        )

    for w in warnings:
        print(f"  WARNING: {w}")
    for e in errors:
        print(f"  ERROR: {e}")

    return len(errors) == 0


def print_banner():
    from config import BOT_INITIAL_BANK, TARGET_MULTIPLIER

    total  = 6 * BOT_INITIAL_BANK
    floor  = BOT_INITIAL_BANK * 0.80
    target = BOT_INITIAL_BANK * TARGET_MULTIPLIER

    banner = f"""
╔══════════════════════════════════════════════════════════════╗
║          6-BOT  $100 → $1000  (10x)  SYSTEM                 ║
╠══════════════════════════════════════════════════════════════╣
║  Bot 1  Stake Dice     Bot 4  Polymarket                     ║
║  Bot 2  Stake Limbo    Bot 5  Polymarket                     ║
║  Bot 3  Stake Mines    Bot 6  Polymarket                     ║
╠══════════════════════════════════════════════════════════════╣
║  Per-bot bankroll : ${BOT_INITIAL_BANK:<42.2f}║
║  Total deployed   : ${total:<42.2f}║
║  Hard floor/bot   : ${floor:<42.2f}║
║  10x target/bot   : ${target:<42.2f}║
╠══════════════════════════════════════════════════════════════╣
║  RISK RULES:                                                 ║
║  • HARD STOP: bot halts if bankroll ≤ $80 (80% of $100)     ║
║  • Recovery mode: bankroll < $90 → tiny safe bets            ║
║  • Profit lock: every time bankroll hits $115, take $15 out  ║
║  • Growth phases: phase1 → phase2 → phase3 as you grow       ║
║  • 10x achieved: bankroll + locked profits ≥ $1000           ║
╚══════════════════════════════════════════════════════════════╝
"""
    print(banner)


if __name__ == "__main__":
    # Setup basic logging before full config
    logging.basicConfig(
        level  = logging.INFO,
        format = "%(asctime)s | %(levelname)-7s | %(message)s",
    )

    print_banner()

    if not validate_config():
        print("\nFix the errors above and try again.")
        sys.exit(1)

    print("\nConfig OK. Starting bots...\n")

    from orchestrator import BotOrchestrator
    BotOrchestrator().start()  # Always uses the hardcoded 6-bot layout
