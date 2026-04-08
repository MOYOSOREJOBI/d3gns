"""
Configuration for the DeG£N$ multi-bot system.
7-phase risk model: FLOOR → ULTRA_SAFE → SAFE → CAREFUL → NORMAL → AGGRESSIVE → TURBO
Secrets are loaded from environment variables (set in .env locally, Render env vars in prod).
"""
import os
from pathlib import Path

# Load .env file if present (local dev only — Render/prod uses real env vars)
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    for _line in _env_path.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip())

# ─── PLATFORM CREDENTIALS ────────────────────────────────────────────────────
STAKE_API_TOKEN     = os.getenv("STAKE_API_TOKEN", "")
STAKE_CURRENCY      = "usdt"
STAKE_API_URL       = "https://stake.com/_api/graphql"

POLY_PRIVATE_KEY    = os.getenv("POLY_PRIVATE_KEY", "")
POLY_API_KEY        = os.getenv("POLY_API_KEY", "")
POLY_API_SECRET     = os.getenv("POLY_API_SECRET", "")
POLY_API_PASSPHRASE = os.getenv("POLY_API_PASSPHRASE", "")
POLY_CHAIN_ID       = 137
POLY_HOST           = "https://clob.polymarket.com"

# ─── TWILIO SMS ALERTS ────────────────────────────────────────────────────────
TWILIO_ACCOUNT_SID  = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN   = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM_NUMBER  = os.getenv("TWILIO_FROM_NUMBER", "")
NOTIFY_PHONE        = os.getenv("NOTIFY_PHONE", "")

# ─── BOT LAYOUT ──────────────────────────────────────────────────────────────
NUM_BOTS         = 10
BOT_INITIAL_BANK = 20.0       # $20 per bot for testing
BOT_PLATFORMS    = [
    "stake_dice",
    "stake_limbo",
    "stake_mines",
    "polymarket",
    "polymarket",
    "polymarket",
    "polymarket",
    "polymarket",
    "polymarket",
    "polymarket",
]

# ─── 7-PHASE CONSTANTS ───────────────────────────────────────────────────────
# Phase names (used throughout system)
PHASE_FLOOR      = "floor"
PHASE_ULTRA_SAFE = "ultra_safe"
PHASE_SAFE       = "safe"
PHASE_CAREFUL    = "careful"
PHASE_NORMAL     = "normal"
PHASE_AGGRESSIVE = "aggressive"
PHASE_TURBO      = "turbo"
PHASE_MILESTONE  = "milestone"

# Drawdown thresholds (as fraction of start_amount — distance below start)
FLOOR_DRAWDOWN_PCT   = 0.10   # >= 10% drawdown → FLOOR (bounce floor, NEVER stops)
ULTRA_SAFE_DRAWDOWN  = 0.07   # 7–10% drawdown → ULTRA_SAFE
SAFE_DRAWDOWN        = 0.05   # 5–7%  drawdown → SAFE
CAREFUL_DRAWDOWN     = 0.03   # 3–5%  drawdown → CAREFUL

# Legacy compat names for code that uses the old naming
FLOOR_THRESHOLD      = 1.0 - FLOOR_DRAWDOWN_PCT     # 0.90
ULTRA_SAFE_THRESHOLD = 1.0 - ULTRA_SAFE_DRAWDOWN    # 0.93
SAFE_THRESHOLD       = 1.0 - SAFE_DRAWDOWN          # 0.95
CAREFUL_THRESHOLD    = 1.0 - CAREFUL_DRAWDOWN        # 0.97
SAFE_EXIT_THRESHOLD  = 0.98   # Must recover to 2% drawdown to exit CAREFUL

# Global safe-mode trigger: if ANY bot hits this drawdown, all bots drop to safe phase
GLOBAL_SAFE_TRIGGER  = 0.05   # 5% drawdown on any single bot

# TURBO activation criteria
TURBO_MIN_STREAK      = 3    # Consecutive wins needed
TURBO_LAST_N_BETS     = 20   # Window for momentum check
TURBO_REQUIRE_ZERO_DD = True # Must have zero drawdown (peak == start or above)

# ─── BET SIZING PER PHASE ────────────────────────────────────────────────────
BET_PCT_BY_PHASE = {
    "floor"      : 0.001,   # 0.1 % — dust bets, bounce floor
    "ultra_safe" : 0.002,   # 0.2 %
    "safe"       : 0.005,   # 0.5 %
    "careful"    : 0.008,   # 0.8 %
    "normal"     : 0.015,   # 1.5 %
    "aggressive" : 0.030,   # 3.0 %
    "turbo"      : 0.040,   # 4.0 %
    "milestone"  : 0.015,   # 1.5 % — keep running at normal rate
}

# Paroli press limit per phase (how many consecutive wins to double-up)
PAROLI_BY_PHASE = {
    "floor"      : 0,   # flat bets only
    "ultra_safe" : 0,   # flat bets only
    "safe"       : 0,   # no pressing in safe
    "careful"    : 2,   # max 2 presses
    "normal"     : 3,   # 2^3 = 8x
    "aggressive" : 4,   # 2^4 = 16x
    "turbo"      : 5,   # 2^5 = 32x base bet potential
    "milestone"  : 3,
}

# ─── CIRCUIT BREAKERS ────────────────────────────────────────────────────────
CB_CONSEC_LOSS_3_PAUSE  = 120    # 2 min
CB_CONSEC_LOSS_5_PAUSE  = 600    # 10 min
CB_VELOCITY_3PCT_PAUSE  = 600    # 10 min
CB_VELOCITY_5PCT_PAUSE  = 1800   # 30 min
CB_PHASE_DROP_ON_RESUME = 1      # drop 1 phase level when resuming after CB

# Legacy names for backwards compat
CB_CONSEC_LOSS_3_MIN    = CB_CONSEC_LOSS_3_PAUSE // 60
CB_CONSEC_LOSS_5_MIN    = CB_CONSEC_LOSS_5_PAUSE // 60
CB_VELOCITY_3PCT_5MIN   = CB_VELOCITY_3PCT_PAUSE // 60
CB_VELOCITY_5PCT_30MIN  = CB_VELOCITY_5PCT_PAUSE // 60

# ─── PROFIT LOCKING (VAULT RATCHET) ─────────────────────────────────────────
PROFIT_LOCK_TRIGGER_PCT = 0.10   # Lock profits every time bankroll gains +10% of start
PROFIT_LOCK_RATIO       = 0.50   # Lock 50% of the gain amount

# Auto-withdraw (existing mechanic): lock 100% of gains above +20%
AUTO_WITHDRAW_PCT       = 1.20

# ─── WIN/LOSS VELOCITY ───────────────────────────────────────────────────────
WIN_VELOCITY_PCT        = 0.05   # +5% gain in 10 minutes → boost sizing
WIN_VELOCITY_WINDOW_MIN = 10     # minutes window
WIN_VELOCITY_BOOST      = 1.50   # 1.5x size for next N bets
WIN_VELOCITY_BOOST_BETS = 10     # number of boosted bets

LOSS_VELOCITY_PCT        = 0.03  # -3% loss in 5 minutes → circuit breaker
LOSS_VELOCITY_WINDOW_MIN = 5     # minutes window

# ─── STAKE GAME PARAMS PER PHASE ─────────────────────────────────────────────
DICE_CHANCE = {
    "turbo"     : 49.5,   # ~49.5% win, 2.0x payout — maximum compound potential
    "normal"    : 55.0,   # ~55%  win, 1.8x
    "safe"      : 80.0,   # ~80%  win, 1.24x
    "ultra_safe": 92.0,   # ~92%  win, 1.08x — volume farming
    "floor"     : 98.0,   # ~98%  win, 1.02x — dust bets, just grind
    "milestone" : 55.0,
    # Legacy names
    "recovery"  : 92.0,
    "phase1"    : 55.0,
    "phase2"    : 49.5,
    "phase3"    : 35.0,
    "active"    : 55.0,
    "caution"   : 80.0,
}

LIMBO_TARGET = {
    "turbo"     : 2.00,
    "normal"    : 1.50,
    "safe"      : 1.10,
    "ultra_safe": 1.03,
    "floor"     : 1.01,
    "milestone" : 1.50,
    # Legacy
    "recovery"  : 1.05,
    "phase1"    : 1.50,
    "phase2"    : 2.00,
    "phase3"    : 3.00,
    "active"    : 1.50,
    "caution"   : 1.10,
}

MINES_PARAMS = {
    "turbo"     : {"mines": 5, "picks": 4},   # ~34% win, ~3.2x
    "normal"    : {"mines": 3, "picks": 3},   # ~68% win, ~1.5x
    "safe"      : {"mines": 1, "picks": 2},   # ~88% win, ~1.09x
    "ultra_safe": {"mines": 1, "picks": 1},   # ~96% win, ~1.04x
    "floor"     : {"mines": 1, "picks": 1},
    "milestone" : {"mines": 3, "picks": 3},
    # Legacy
    "recovery"  : {"mines": 1, "picks": 2},
    "phase1"    : {"mines": 3, "picks": 3},
    "phase2"    : {"mines": 5, "picks": 4},
    "phase3"    : {"mines": 5, "picks": 6},
    "active"    : {"mines": 3, "picks": 3},
    "caution"   : {"mines": 1, "picks": 2},
}

LIMBO_BIGSHOT_MULTIPLIER = 10.0
LIMBO_BIGSHOT_PCT        = 0.005
LIMBO_BIGSHOT_FREQ       = 20   # every N bets, only in TURBO

# ─── POLYMARKET ───────────────────────────────────────────────────────────────
POLY_MAX_SPREAD       = 0.04
POLY_MIN_VOLUME       = 500
POLY_MIN_EDGE         = 0.05    # raised from 0.025 — require real edge
POLY_KELLY_FRACTION   = 0.25    # quarter Kelly (conservative)
POLY_MAX_POSITION_PCT = 0.10    # max 10% per position
POLY_SCAN_INTERVAL    = 10.0

# ─── GROWTH TARGET ───────────────────────────────────────────────────────────
TARGET_MULTIPLIER   = 10.0
TARGET_HOURS        = 12

# ─── LEGACY COMPAT ───────────────────────────────────────────────────────────
HARD_STOP_PCT        = FLOOR_THRESHOLD
RECOVERY_MODE_PCT    = ULTRA_SAFE_THRESHOLD
WITHDRAW_TRIGGER_PCT = AUTO_WITHDRAW_PCT
PAROLI_PRESS_MAX     = 3
MAX_MARTINGALE_STEPS  = 4
MARTINGALE_MULTIPLIER = 1.4
BET_PCT_RECOVERY     = BET_PCT_BY_PHASE["floor"]
BET_PCT_PHASE1       = BET_PCT_BY_PHASE["normal"]
BET_PCT_PHASE2       = BET_PCT_BY_PHASE["normal"]
BET_PCT_PHASE3       = BET_PCT_BY_PHASE["turbo"]
PHASE1_MAX           = 2.0
PHASE2_MAX           = 5.0

# ─── TIMING ──────────────────────────────────────────────────────────────────
BET_DELAY_SECONDS   = 1.2
STATUS_LOG_INTERVAL = 60

# ─── LOGGING ─────────────────────────────────────────────────────────────────
LOG_FILE  = "bots.log"
LOG_LEVEL = "INFO"
