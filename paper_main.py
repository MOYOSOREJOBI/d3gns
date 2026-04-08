"""
Paper trading entry point – full 6-bot dry run.

Runs all 6 bots with:
  • Real Stake math (provably-fair RNG replicated locally)
  • Real Polymarket prices fetched from the public Gamma API
  • Simulated order fills (zero real money, zero API tokens needed)
  • Live rich dashboard updating every 2 seconds
  • Adjustable simulation speed (default 10×, so 24h sim = ~2.5h real)

Usage:
    python paper_main.py                  # 10× speed, 24h sim
    python paper_main.py --speed 30       # 30× speed (~48 min for 24h)
    python paper_main.py --speed 1        # real-time (for live feel)
    python paper_main.py --hours 12       # simulate only 12 hours
    python paper_main.py --speed 60 --hours 24   # full 24h in ~24 min
"""

import argparse
import logging
import random
import threading
import time
import sys

# ── CLI args ──────────────────────────────────────────────────────────────────
parser = argparse.ArgumentParser(description="Paper trading dry-run")
parser.add_argument("--speed", type=float, default=10.0,
                    help="Simulation speed multiplier (default 10)")
parser.add_argument("--hours", type=float, default=24.0,
                    help="Hours of trading to simulate (default 24)")
parser.add_argument("--seed",  type=int,   default=None,
                    help="Random seed for reproducibility")
args = parser.parse_args()

SIM_SPEED  = args.speed
SIM_HOURS  = args.hours
SIM_SEED   = args.seed

if SIM_SEED is not None:
    random.seed(SIM_SEED)

# ── Logging (file only during paper run – dashboard handles console) ──────────
logging.basicConfig(
    level    = logging.INFO,
    format   = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    handlers = [logging.FileHandler("paper_run.log", encoding="utf-8")],
)
logger = logging.getLogger("paper_main")

# ── Imports ───────────────────────────────────────────────────────────────────
from config      import BOT_INITIAL_BANK, TARGET_MULTIPLIER, BET_DELAY_SECONDS
from risk_manager import RiskManager

# Monkey-patch stake_client so strategies use our paper engine
import paper_stake
import stake_client
stake_client.dice_roll  = paper_stake.dice_roll
stake_client.limbo_game = paper_stake.limbo_game
stake_client.mines_play = paper_stake.mines_play
stake_client.get_balance= paper_stake.get_balance

from stake_strategies    import make_strategy
from paper_polymarket    import PaperPolymarketBot
from dashboard           import Dashboard, plain_status, RICH_OK

# ── Scale delay by speed ──────────────────────────────────────────────────────
import config as _cfg
_cfg.BET_DELAY_SECONDS = max(BET_DELAY_SECONDS / SIM_SPEED, 0.05)

# ─────────────────────────────────────────────────────────────────────────────
#  Bot specs: Stake core + expanded Polymarket suite
# ─────────────────────────────────────────────────────────────────────────────
BOT_SPECS = [
    ("bot1_dice",  "dice"),
    ("bot2_limbo", "limbo"),
    ("bot3_mines", "mines"),
    ("bot4_poly",  "poly"),
    ("bot5_poly",  "poly"),
    ("bot6_poly",  "poly"),
    ("bot7_poly",  "poly"),
    ("bot8_poly",  "poly"),
    ("bot9_poly",  "poly"),
]

stop_event  = threading.Event()
all_rms: list[RiskManager] = []
dashboard: Dashboard        = None


# ─────────────────────────────────────────────────────────────────────────────
#  Stake bot thread
# ─────────────────────────────────────────────────────────────────────────────

def stake_loop(bot_id: str, game: str, rm: RiskManager):
    # Give each bot its own starting balance in the paper wallet
    paper_stake.set_balance(rm.initial_bankroll)

    strategy = make_strategy(game, rm)
    logger.info(f"[{bot_id}] Stake/{game} paper-started")

    while not stop_event.is_set() and not rm.is_halted:
        try:
            net    = strategy.run_one_bet()
            result = rm.record_bet_result(net)

            if dashboard:
                if result.get("withdraw", 0) > 0:
                    dashboard.log_event(
                        f"[green]{bot_id}[/green] 💰 locked "
                        f"${result['withdraw']:.2f} | "
                        f"total safe ${rm.total_withdrawn:.2f}"
                    )
                if result["action"] == "HALTED":
                    dashboard.log_event(
                        f"[red]{bot_id}[/red] ⛔ HARD STOP "
                        f"bank=${rm.current_bankroll:.4f}"
                    )
                    break
                if result["action"] == "TARGET_HIT":
                    dashboard.log_event(
                        f"[bold green]{bot_id}[/bold green] "
                        f"🚀 10× TARGET HIT! "
                        f"progress={result['progress_x']}×"
                    )

        except Exception as exc:
            logger.error(f"[{bot_id}] error: {exc}", exc_info=True)
            time.sleep(0.5)


# ─────────────────────────────────────────────────────────────────────────────
#  Polymarket bot thread
# ─────────────────────────────────────────────────────────────────────────────

def poly_loop(bot_id: str, rm: RiskManager):
    bot = PaperPolymarketBot(rm)
    logger.info(f"[{bot_id}] Polymarket paper-started (real prices, fake orders)")

    scan_delay = max(_cfg.BET_DELAY_SECONDS * 15, 0.5)

    while not stop_event.is_set() and not rm.is_halted:
        try:
            net = bot.run_one_cycle()

            if net != 0.0:
                result = rm.record_bet_result(net)

                if dashboard:
                    if result.get("withdraw", 0) > 0:
                        dashboard.log_event(
                            f"[green]{bot_id}[/green] 💰 locked "
                            f"${result['withdraw']:.2f}"
                        )
                    if result["action"] == "HALTED":
                        dashboard.log_event(
                            f"[red]{bot_id}[/red] ⛔ HARD STOP"
                        )
                        break
                    if result["action"] == "TARGET_HIT":
                        dashboard.log_event(
                            f"[bold green]{bot_id}[/bold green] 🚀 10× TARGET!"
                        )

        except Exception as exc:
            logger.error(f"[{bot_id}] error: {exc}", exc_info=True)

        for _ in range(int(scan_delay / 0.1)):
            if stop_event.is_set():
                break
            time.sleep(0.1)


# ─────────────────────────────────────────────────────────────────────────────
#  Status fallback (no rich)
# ─────────────────────────────────────────────────────────────────────────────

def _plain_printer(start_wall: float):
    interval = max(10.0 / SIM_SPEED, 2.0)
    while not stop_event.is_set():
        time.sleep(interval)
        plain_status(all_rms, SIM_SPEED, start_wall, SIM_HOURS)


# ─────────────────────────────────────────────────────────────────────────────
#  Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    global dashboard

    # ── Print startup info ────────────────────────────────────────────────────
    print(f"""
╔══════════════════════════════════════════════════════════════╗
║           PAPER TRADING DRY-RUN  (no real money)            ║
╠══════════════════════════════════════════════════════════════╣
║  Bots 1-3 : Stake Dice / Limbo / Mines (simulated RNG)      ║
║  Bots 4-6 : Polymarket (real prices, fake orders)            ║
╠══════════════════════════════════════════════════════════════╣
║  Sim speed  : {SIM_SPEED:<44.0f}×║
║  Sim length : {SIM_HOURS:<43.0f}h║
║  Real time  : ~{SIM_HOURS*60/SIM_SPEED:<40.1f}min║
║  Per-bot $  : ${BOT_INITIAL_BANK:<43.0f}║
║  Target     : ${BOT_INITIAL_BANK*TARGET_MULTIPLIER:<43.0f}║
╚══════════════════════════════════════════════════════════════╝
""")

    if not RICH_OK:
        print("  TIP: pip install rich  for the live dashboard.\n"
              "  Running with plain-text status instead.\n")

    # ── Create RMs ────────────────────────────────────────────────────────────
    rms = {
        bot_id: RiskManager(bot_id, BOT_INITIAL_BANK)
        for bot_id, _ in BOT_SPECS
    }
    all_rms.extend(rms.values())

    # ── Dashboard ─────────────────────────────────────────────────────────────
    if RICH_OK:
        dashboard = Dashboard(all_rms, sim_speed=SIM_SPEED, sim_hours=SIM_HOURS)
        dashboard.start(refresh_seconds=max(2.0 / SIM_SPEED, 0.3))
    else:
        start_wall = time.time()
        plain_t    = threading.Thread(
            target=_plain_printer, args=(start_wall,), daemon=True
        )
        plain_t.start()

    # ── Spawn bot threads ─────────────────────────────────────────────────────
    threads = []
    for bot_id, kind in BOT_SPECS:
        rm = rms[bot_id]
        if kind == "poly":
            t = threading.Thread(
                target=poly_loop, args=(bot_id, rm), daemon=True, name=bot_id
            )
        else:
            t = threading.Thread(
                target=stake_loop, args=(bot_id, kind, rm), daemon=True, name=bot_id
            )
        threads.append(t)

    for t in threads:
        t.start()

    # ── Sim timer ─────────────────────────────────────────────────────────────
    wall_start    = time.time()
    sim_wall_end  = wall_start + (SIM_HOURS * 3600 / SIM_SPEED)

    try:
        while time.time() < sim_wall_end:
            if all(not t.is_alive() for t in threads):
                break
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nStopped by user.")

    # ── Shutdown ──────────────────────────────────────────────────────────────
    stop_event.set()
    for t in threads:
        t.join(timeout=5)
    if dashboard:
        dashboard.stop()

    # ── Final report ──────────────────────────────────────────────────────────
    print(f"\n{'═'*65}")
    print(f"  PAPER RUN COMPLETE — {SIM_HOURS:.0f}h simulated "
          f"in {(time.time()-wall_start)/60:.1f} min real")
    print(f"{'─'*65}")

    grand_active = 0.0
    grand_locked = 0.0
    for rm in all_rms:
        s = rm.status()
        grand_active += s["bankroll"]
        grand_locked += s["total_withdrawn"]
        hit    = "🚀 10×!" if s["target_hit"] else ""
        halted = "⛔" if s["halted"] else ""
        print(
            f"  {s['bot_id']:<14} | "
            f"bank=${s['bankroll']:>8.4f} | "
            f"locked=${s['total_withdrawn']:>8.2f} | "
            f"{s['progress_x']:.2f}× | "
            f"bets={s['bets']:>5} | "
            f"W{s['win_rate_pct']:.0f}% "
            f"{hit}{halted}"
        )

    total_value = grand_active + grand_locked
    total_init  = len(all_rms) * BOT_INITIAL_BANK
    print(f"{'─'*65}")
    print(f"  Grand total value : ${total_value:.2f}  "
          f"({total_value/total_init:.2f}× of ${total_init:.0f} deployed)")
    print(f"  Total locked      : ${grand_locked:.2f}")
    print(f"{'═'*65}\n")
    print(f"  Full log saved to: paper_run.log")


if __name__ == "__main__":
    main()
