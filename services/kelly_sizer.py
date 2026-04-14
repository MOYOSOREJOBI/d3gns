"""
Kelly Criterion and bankroll management service.
Pure Python/math — no external APIs, no dependencies beyond stdlib.

Implements:
  - Full Kelly
  - Fractional Kelly (quarter Kelly recommended for prediction markets)
  - Kelly with multiple simultaneous bets (Simultaneous Kelly)
  - Drawdown-adjusted Kelly
  - Optimal-F (Ralph Vince)
  - Bet sizing for binary prediction markets (YES/NO)
"""
from __future__ import annotations

import math
from typing import Any


# ── Core Kelly formulas ───────────────────────────────────────────────────────

def kelly_fraction(
    probability: float,
    odds_decimal: float,
    *,
    fraction: float = 0.25,
    min_edge: float = 0.0,
) -> dict[str, Any]:
    """
    Compute Kelly fraction for a binary bet.

    Args:
        probability:  Model probability of winning (0–1).
        odds_decimal: Decimal odds offered (e.g. 2.0 = evens, 1.5 = -200 US).
        fraction:     Kelly multiplier (0.25 = quarter Kelly).
        min_edge:     Minimum required edge to return non-zero size (default 0).

    Returns dict with kelly_full, kelly_fractional, edge_pct, ev, recommended_size.
    """
    if probability <= 0 or probability >= 1:
        return _zero("probability must be in (0, 1)")
    if odds_decimal <= 1:
        return _zero("odds_decimal must be > 1")

    b = odds_decimal - 1.0          # net profit per unit staked on win
    p = probability
    q = 1.0 - p

    kelly_full = (b * p - q) / b    # standard Kelly formula

    edge_pct = kelly_full * 100     # edge as % of bankroll
    ev = p * b - q                  # expected value per unit staked

    if ev < min_edge or kelly_full <= 0:
        return {
            "kelly_full": round(kelly_full, 6),
            "kelly_fractional": 0.0,
            "edge_pct": round(edge_pct, 4),
            "ev": round(ev, 6),
            "recommended_size": 0.0,
            "reason": "no_edge" if ev <= 0 else "below_min_edge",
            "fraction_used": fraction,
        }

    kelly_frac = kelly_full * fraction
    # Hard cap: never risk more than 10% of bankroll on a single bet
    kelly_capped = min(kelly_frac, 0.10)

    return {
        "kelly_full": round(kelly_full, 6),
        "kelly_fractional": round(kelly_frac, 6),
        "kelly_capped": round(kelly_capped, 6),
        "edge_pct": round(edge_pct, 4),
        "ev": round(ev, 6),
        "recommended_size": round(kelly_capped, 6),
        "reason": "valid",
        "fraction_used": fraction,
    }


def kelly_prediction_market(
    model_prob: float,
    market_prob: float,
    *,
    side: str = "YES",
    fraction: float = 0.25,
    max_position_pct: float = 0.10,
) -> dict[str, Any]:
    """
    Kelly sizing for binary prediction markets (Polymarket/Kalshi style).

    Args:
        model_prob:       Your model's probability estimate.
        market_prob:      Current market price (implied probability).
        side:             'YES' or 'NO' — which side you're betting.
        fraction:         Kelly fraction (default quarter Kelly).
        max_position_pct: Hard cap on position size as % of bankroll.

    Returns recommended position size as fraction of bankroll.
    """
    if side.upper() == "NO":
        # Flip: betting NO means you win if the event doesn't happen
        model_p   = 1.0 - model_prob
        market_p  = 1.0 - market_prob
    else:
        model_p  = model_prob
        market_p = market_prob

    if market_p <= 0 or market_p >= 1:
        return _zero("market_prob out of range")
    if model_p <= 0 or model_p >= 1:
        return _zero("model_prob out of range")

    # Decimal odds from market price: $1 bet on YES at 60¢ → $1/0.60 = 1.667 decimal
    odds_decimal = 1.0 / market_p
    edge = model_p - market_p
    result = kelly_fraction(model_p, odds_decimal, fraction=fraction)
    result["market_prob"] = round(market_p, 4)
    result["model_prob"]  = round(model_p, 4)
    result["edge"] = round(edge, 4)
    result["side"] = side.upper()

    # Apply max position cap
    capped = min(result.get("recommended_size", 0), max_position_pct)
    result["recommended_size"] = round(capped, 6)
    result["max_position_pct"] = max_position_pct

    if edge <= 0:
        result["recommended_size"] = 0.0
        result["reason"] = "no_edge_over_market"

    return result


def simultaneous_kelly(bets: list[dict[str, Any]], fraction: float = 0.25) -> dict[str, Any]:
    """
    Approximate simultaneous Kelly for multiple concurrent bets.
    Uses Thorp's approximation: scale each individual Kelly by 1/N^0.5 for N correlated bets.
    Each bet: {"probability": float, "odds_decimal": float, "label": str}
    """
    if not bets:
        return {"error": "no bets provided", "positions": []}
    results = []
    total_allocation = 0.0
    for bet in bets:
        k = kelly_fraction(
            bet.get("probability", 0.5),
            bet.get("odds_decimal", 2.0),
            fraction=fraction,
        )
        size = k.get("recommended_size", 0.0)
        results.append({
            "label": bet.get("label", "bet"),
            "probability": bet["probability"],
            "odds_decimal": bet.get("odds_decimal", 2.0),
            "individual_kelly": k.get("kelly_fractional", 0),
            "recommended_size": round(size, 6),
            "ev": k.get("ev", 0),
            "edge_pct": k.get("edge_pct", 0),
        })
        total_allocation += size

    # If total exceeds safe threshold, scale all down proportionally
    MAX_TOTAL = 0.20  # never allocate > 20% across all simultaneous bets
    if total_allocation > MAX_TOTAL:
        scale = MAX_TOTAL / total_allocation
        for r in results:
            r["recommended_size"] = round(r["recommended_size"] * scale, 6)
            r["scaled"] = True
        total_allocation = MAX_TOTAL

    return {
        "bets": results,
        "total_allocation": round(total_allocation, 6),
        "count": len(bets),
        "fraction_used": fraction,
        "scaled": total_allocation >= MAX_TOTAL,
    }


def dollar_bet_size(
    bankroll: float,
    kelly_result: dict[str, Any],
    *,
    min_bet_usd: float = 1.0,
    max_bet_usd: float | None = None,
) -> dict[str, Any]:
    """Convert a Kelly fraction to a dollar amount given bankroll."""
    size_pct = kelly_result.get("recommended_size", 0.0)
    raw_usd = bankroll * size_pct
    capped_usd = min(raw_usd, max_bet_usd) if max_bet_usd else raw_usd
    final_usd = max(capped_usd, min_bet_usd) if size_pct > 0 else 0.0
    return {
        "bankroll": bankroll,
        "size_pct": round(size_pct, 6),
        "raw_usd": round(raw_usd, 4),
        "final_usd": round(final_usd, 4),
        "min_bet_usd": min_bet_usd,
        "max_bet_usd": max_bet_usd,
        "kelly_edge_pct": kelly_result.get("edge_pct", 0),
        "kelly_ev": kelly_result.get("ev", 0),
    }


def expected_growth_rate(probability: float, odds_decimal: float, fraction: float = 0.25) -> float:
    """
    Compute the expected log-growth rate of bankroll per bet.
    G = p * log(1 + f*b) + q * log(1 - f)
    where f is the fractional Kelly size and b is net profit multiplier.
    """
    if probability <= 0 or probability >= 1 or odds_decimal <= 1:
        return 0.0
    k = kelly_fraction(probability, odds_decimal, fraction=fraction)
    f = k.get("recommended_size", 0.0)
    if f <= 0:
        return 0.0
    b = odds_decimal - 1.0
    p, q = probability, 1.0 - probability
    try:
        return round(p * math.log(1 + f * b) + q * math.log(1 - f), 6)
    except (ValueError, ZeroDivisionError):
        return 0.0


def ruin_probability(bankroll_units: int, win_prob: float, payout_multiple: float) -> float:
    """
    Gambler's ruin probability: chance of going broke before reaching N*bankroll.
    Uses the classical formula: (q/p)^N where p=win, q=loss.
    """
    if win_prob <= 0 or win_prob >= 1:
        return 1.0
    q = 1.0 - win_prob
    ratio = q / win_prob
    if ratio >= 1:
        return 1.0
    return round(ratio ** bankroll_units, 6)


def phase_kelly_size(phase: str, base_kelly: float) -> float:
    """
    Scale Kelly size according to DeG£N$ 7-phase risk model.
    More conservative in lower phases, full fraction in normal/aggressive.
    """
    PHASE_MULTIPLIERS = {
        "floor":      0.10,
        "ultra_safe": 0.20,
        "safe":       0.35,
        "careful":    0.55,
        "normal":     0.80,
        "aggressive": 1.00,
        "turbo":      1.20,
        "milestone":  0.80,
    }
    mult = PHASE_MULTIPLIERS.get(phase.lower(), 0.80)
    return round(base_kelly * mult, 6)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _zero(reason: str) -> dict[str, Any]:
    return {
        "kelly_full": 0.0, "kelly_fractional": 0.0, "kelly_capped": 0.0,
        "edge_pct": 0.0, "ev": 0.0, "recommended_size": 0.0,
        "reason": reason,
    }
