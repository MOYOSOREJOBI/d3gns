"""
Stake GraphQL client – Dice, Limbo, Mines.

Authentication: pass your x-access-token from Stake (grab it from
browser DevTools → Network → any GraphQL request → Request Headers).
"""

import uuid
import logging
import requests
from typing import Optional

from config import STAKE_API_TOKEN, STAKE_CURRENCY, STAKE_API_URL

logger = logging.getLogger(__name__)

HEADERS = {
    "Content-Type" : "application/json",
    "x-access-token": STAKE_API_TOKEN,
    "User-Agent"   : (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
}


def _gql(query: str, variables: dict) -> dict:
    # Always pull latest token from config (hot-loaded from DB at startup)
    import config as _cfg
    if _cfg.STAKE_API_TOKEN:
        HEADERS["x-access-token"] = _cfg.STAKE_API_TOKEN
    resp = requests.post(
        STAKE_API_URL,
        json    = {"query": query, "variables": variables},
        headers = HEADERS,
        timeout = 15,
    )
    resp.raise_for_status()
    data = resp.json()
    if "errors" in data:
        raise RuntimeError(f"GraphQL error: {data['errors']}")
    return data["data"]


# ═══════════════════════════════════════════════════════════════
#  DICE
# ═══════════════════════════════════════════════════════════════

DICE_MUTATION = """
mutation DiceRoll(
    $amount: Float!
    $target: Float!
    $condition: CasinoGameDiceConditionEnum!
    $currency: CurrencyEnum!
    $identifier: String!
) {
    diceRoll(
        amount: $amount
        target: $target
        condition: $condition
        currency: $currency
        identifier: $identifier
    ) {
        id
        active
        payoutMultiplier
        amountMultiplier
        amount
        payout
        updatedAt
        currency
        game {
            ... on CasinoGameDice {
                result
                target
                condition
            }
        }
        user {
            id
            balances { available { amount currency } }
        }
    }
}
"""


def dice_roll(amount: float, target: float, condition: str = "below",
              currency: str = STAKE_CURRENCY) -> Optional[dict]:
    """
    condition: "below" (bet that result < target) or "above"
    target range: 0–100
    For a 49% win chance (near 50/50): target=49.5, condition=below
    """
    try:
        data = _gql(DICE_MUTATION, {
            "amount"    : amount,
            "target"    : target,
            "condition" : condition,
            "currency"  : currency,
            "identifier": str(uuid.uuid4()),
        })
        bet   = data["diceRoll"]
        won   = bet["payout"] > 0
        logger.debug(
            f"DICE | bet={amount} target={target} {condition} | "
            f"result={bet['game']['result']:.2f} | "
            f"{'WIN' if won else 'LOSS'} payout={bet['payout']}"
        )
        return {
            "won"       : won,
            "payout"    : float(bet["payout"]),
            "amount"    : float(bet["amount"]),
            "multiplier": float(bet["payoutMultiplier"]),
            "result"    : float(bet["game"]["result"]),
            "balance"   : _parse_balance(bet["user"]["balances"], currency),
        }
    except Exception as exc:
        logger.error(f"dice_roll error: {exc}")
        return None


# ═══════════════════════════════════════════════════════════════
#  LIMBO
# ═══════════════════════════════════════════════════════════════

LIMBO_MUTATION = """
mutation LimboGame(
    $amount: Float!
    $multiplierTarget: Float!
    $currency: CurrencyEnum!
    $identifier: String!
) {
    limboGame(
        amount: $amount
        multiplierTarget: $multiplierTarget
        currency: $currency
        identifier: $identifier
    ) {
        id
        active
        payoutMultiplier
        amountMultiplier
        amount
        payout
        updatedAt
        currency
        game {
            ... on CasinoGameLimbo {
                result
            }
        }
        user {
            id
            balances { available { amount currency } }
        }
    }
}
"""


def limbo_game(amount: float, multiplier_target: float,
               currency: str = STAKE_CURRENCY) -> Optional[dict]:
    """
    Win if the random multiplier ≥ multiplier_target.
    Win probability ≈ 1 / multiplier_target  (with 1% house edge applied).
    """
    try:
        data = _gql(LIMBO_MUTATION, {
            "amount"          : amount,
            "multiplierTarget": multiplier_target,
            "currency"        : currency,
            "identifier"      : str(uuid.uuid4()),
        })
        bet = data["limboGame"]
        won = float(bet["game"]["result"]) >= multiplier_target
        logger.debug(
            f"LIMBO | bet={amount} target={multiplier_target}x | "
            f"result={bet['game']['result']}x | "
            f"{'WIN' if won else 'LOSS'} payout={bet['payout']}"
        )
        return {
            "won"       : won,
            "payout"    : float(bet["payout"]),
            "amount"    : float(bet["amount"]),
            "multiplier": float(bet["payoutMultiplier"]),
            "result"    : float(bet["game"]["result"]),
            "balance"   : _parse_balance(bet["user"]["balances"], currency),
        }
    except Exception as exc:
        logger.error(f"limbo_game error: {exc}")
        return None


# ═══════════════════════════════════════════════════════════════
#  MINES
# ═══════════════════════════════════════════════════════════════

MINES_CREATE_MUTATION = """
mutation MinesCreate(
    $amount: Float!
    $minesCount: Int!
    $currency: CurrencyEnum!
) {
    minesCreate(amount: $amount minesCount: $minesCount currency: $currency) {
        id
        active
        currency
        amount
        payoutMultiplier
        game {
            ... on CasinoGameMines {
                minesCount
                fieldSize
            }
        }
        user {
            id
            balances { available { amount currency } }
        }
    }
}
"""

MINES_PICK_MUTATION = """
mutation MinesPick($field: Int! $gameId: ID!) {
    minesPick(field: $field gameId: $gameId) {
        id
        active
        payoutMultiplier
        payout
        game {
            ... on CasinoGameMines {
                minesCount
                fieldSize
                revealedSquares { field isMine }
            }
        }
        user {
            id
            balances { available { amount currency } }
        }
    }
}
"""

MINES_CASHOUT_MUTATION = """
mutation MinesCashout($gameId: ID!) {
    minesCashout(gameId: $gameId) {
        id
        active
        payoutMultiplier
        payout
        currency
        game {
            ... on CasinoGameMines {
                minesCount
                fieldSize
                revealedSquares { field isMine }
            }
        }
        user {
            id
            balances { available { amount currency } }
        }
    }
}
"""


def mines_play(amount: float, mines_count: int, num_picks: int,
               currency: str = STAKE_CURRENCY) -> Optional[dict]:
    """
    Full mines round: create → pick num_picks gems → cashout.
    Returns won=True if no mine was hit and cashout succeeded.
    Grid is 5×5 = 25 fields (0-indexed 0..24).
    """
    import random

    try:
        # Create game
        create_data = _gql(MINES_CREATE_MUTATION, {
            "amount"    : amount,
            "minesCount": mines_count,
            "currency"  : currency,
        })
        game_id    = create_data["minesCreate"]["id"]
        field_size = create_data["minesCreate"]["game"]["fieldSize"]

        available  = list(range(field_size))
        random.shuffle(available)
        picks      = available[:num_picks]

        hit_mine  = False
        last_pick = None

        for field in picks:
            pick_data = _gql(MINES_PICK_MUTATION, {
                "field" : field,
                "gameId": game_id,
            })
            last_pick = pick_data["minesPick"]

            # Check if this pick hit a mine
            revealed = last_pick["game"]["revealedSquares"]
            if any(sq["isMine"] for sq in revealed if sq["field"] == field):
                hit_mine = True
                break

            if not last_pick["active"]:
                break

        if hit_mine or last_pick is None:
            won     = False
            payout  = 0.0
            balance = None
            if last_pick:
                balance = _parse_balance(
                    last_pick["user"]["balances"], currency
                )
        else:
            # Cashout
            cashout_data = _gql(MINES_CASHOUT_MUTATION, {"gameId": game_id})
            cashout      = cashout_data["minesCashout"]
            won          = True
            payout       = float(cashout["payout"])
            balance      = _parse_balance(cashout["user"]["balances"], currency)

        logger.debug(
            f"MINES | bet={amount} mines={mines_count} picks={num_picks} | "
            f"{'WIN' if won else 'HIT MINE'} payout={payout:.4f}"
        )
        return {
            "won"    : won,
            "payout" : payout,
            "amount" : amount,
            "balance": balance,
        }

    except Exception as exc:
        logger.error(f"mines_play error: {exc}")
        return None


# ═══════════════════════════════════════════════════════════════
#  BALANCE
# ═══════════════════════════════════════════════════════════════

BALANCE_QUERY = """
query UserBalances {
    user {
        id
        balances {
            available { amount currency }
        }
    }
}
"""


def get_balance(currency: str = STAKE_CURRENCY) -> Optional[float]:
    try:
        data = _gql(BALANCE_QUERY, {})
        return _parse_balance(data["user"]["balances"], currency)
    except Exception as exc:
        logger.error(f"get_balance error: {exc}")
        return None


def _parse_balance(balances: list, currency: str) -> Optional[float]:
    for b in balances:
        if b["available"]["currency"].lower() == currency.lower():
            return float(b["available"]["amount"])
    return None
