"""
Paper Stake engine – identical math to real Stake, zero API calls.

Stake uses Provably Fair RNG. We replicate the outcome distribution exactly:
  Dice  : result = uniform(0, 100),  win if result < target (condition=below)
  Limbo : result = 0.99 / uniform(0, 1), win if result ≥ target
            (house edge baked into 0.99 factor)
  Mines : randomly place `mines_count` mines on a 5×5 grid,
            win if none of the `num_picks` chosen tiles are mines

All functions mirror the same interface as stake_client.py so strategies
work identically in paper and live mode.
"""

import random
import math
import logging
from config import STAKE_CURRENCY

logger = logging.getLogger(__name__)

_balance: float = 0.0   # simulated wallet balance


def set_balance(amount: float):
    global _balance
    _balance = amount


def get_balance(currency: str = STAKE_CURRENCY) -> float:
    return _balance


# ── Dice ──────────────────────────────────────────────────────────────────────

def dice_roll(amount: float, target: float,
              condition: str = "below",
              currency: str  = STAKE_CURRENCY) -> dict:
    global _balance

    result = random.uniform(0.0, 99.99)

    if condition == "below":
        win_chance = target
        won        = result < target
    else:                        # above
        win_chance = 100.0 - target
        won        = result > target

    # Stake multiplier: (100 / win_chance) × 0.99
    multiplier = (100.0 / win_chance) * 0.99 if win_chance > 0 else 0.0
    payout     = amount * multiplier if won else 0.0

    if won:
        _balance += payout - amount
    else:
        _balance -= amount

    return {
        "won"       : won,
        "payout"    : payout,
        "amount"    : amount,
        "multiplier": multiplier,
        "result"    : result,
        "balance"   : _balance,
    }


# ── Limbo ─────────────────────────────────────────────────────────────────────

def limbo_game(amount: float, multiplier_target: float,
               currency: str = STAKE_CURRENCY) -> dict:
    global _balance

    # Stake's Limbo: result = 0.99 / U[0,1]  →  right-skewed distribution
    # P(result ≥ t) = 0.99 / t   for t ≥ 0.99
    u      = random.uniform(1e-6, 1.0)
    result = 0.99 / u
    result = max(1.0, result)    # floor at 1x

    won    = result >= multiplier_target
    payout = amount * multiplier_target if won else 0.0

    if won:
        _balance += payout - amount
    else:
        _balance -= amount

    return {
        "won"       : won,
        "payout"    : payout,
        "amount"    : amount,
        "multiplier": multiplier_target if won else 0.0,
        "result"    : round(result, 4),
        "balance"   : _balance,
    }


# ── Mines ─────────────────────────────────────────────────────────────────────

def mines_play(amount: float, mines_count: int, num_picks: int,
               currency: str = STAKE_CURRENCY) -> dict:
    global _balance

    field_size = 25
    all_tiles  = list(range(field_size))

    # Place mines randomly
    mine_tiles = set(random.sample(all_tiles, mines_count))

    # Bot always picks the first num_picks non-ordered tiles
    # (order doesn't matter for correctness – it's a random placement)
    picks = random.sample(all_tiles, num_picks)

    hit_mine = any(p in mine_tiles for p in picks)

    if hit_mine:
        payout = 0.0
        _balance -= amount
    else:
        # Stake mines payout formula:
        # multiplier = ∏_{k=0}^{n-1} (field - mines - k) / (field - k)
        # then divide by house edge (×0.99)
        p = 1.0
        gems = field_size - mines_count
        for k in range(num_picks):
            p *= (gems - k) / (field_size - k)
        multiplier = 0.99 / p
        payout     = amount * multiplier
        _balance  += payout - amount

    return {
        "won"    : not hit_mine,
        "payout" : payout,
        "amount" : amount,
        "balance": _balance,
    }
