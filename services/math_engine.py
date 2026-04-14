"""
Enhanced Math Engine — advanced position sizing and risk mathematics.

Builds on kelly_sizer.py with:
  - Optimal-F (Ralph Vince)
  - Kelly bands (confidence intervals via bootstrapping)
  - Dynamic Kelly with drawdown adjustment
  - Multi-outcome Kelly (non-binary bets)
  - Expected value with probability distributions
  - Sharpe-adjusted position sizing
  - Compound growth optimisation
  - Risk of ruin calculator (precise)
  - Bet sequence analysis (CLT confidence)
"""
from __future__ import annotations

import math
import random
import statistics
from typing import Any


# ── Optimal-F (Ralph Vince) ───────────────────────────────────────────────────

def optimal_f(
    trade_pnls: list[float],
    *,
    fraction_steps: int = 100,
) -> dict[str, Any]:
    """
    Find the Optimal-F fraction that maximises terminal wealth (TWR).
    Vince's formula: maximises product of (1 + f * HPR[i]).
    Warning: Optimal-F is very aggressive — use fractional multiplier.

    Args:
        trade_pnls: List of historical P&L outcomes (in dollars or percentages).
        fraction_steps: Number of f values to evaluate (resolution).
    """
    if not trade_pnls:
        return {"error": "no_trade_pnls"}

    worst_loss = abs(min(trade_pnls))
    if worst_loss == 0:
        return {"error": "no_losing_trades — optimal_f undefined"}

    best_f   = 0.0
    best_twr = 1.0

    for i in range(1, fraction_steps + 1):
        f = i / fraction_steps
        twr = 1.0
        valid = True
        for pnl in trade_pnls:
            hpr = 1.0 + f * (pnl / worst_loss)
            if hpr <= 0:
                valid = False
                break
            twr *= hpr
        if valid and twr > best_twr:
            best_twr = twr
            best_f   = f

    # Translate f → fraction of bankroll
    # f is expressed as a ratio of worst_loss so: position_size = f * bankroll / worst_loss
    quarter_f = round(best_f * 0.25, 6)
    half_f    = round(best_f * 0.50, 6)

    return {
        "optimal_f":         round(best_f, 6),
        "quarter_optimal_f": quarter_f,
        "half_optimal_f":    half_f,
        "best_twr":          round(best_twr, 4),
        "worst_loss":        worst_loss,
        "n_trades":          len(trade_pnls),
        "recommended":       quarter_f,   # safer fraction
    }


# ── Kelly confidence bands via bootstrapping ──────────────────────────────────

def kelly_confidence_bands(
    win_prob: float,
    win_payoff: float,
    loss_payoff: float = 1.0,
    *,
    n_bootstrap: int = 1000,
    n_sample: int = 100,
    fraction: float = 0.25,
    seed: int | None = 42,
) -> dict[str, Any]:
    """
    Bootstrap confidence intervals for Kelly fraction estimates.
    Simulates uncertainty in win_prob by resampling trade outcomes.

    Returns 5th/25th/50th/75th/95th percentile Kelly fractions.
    """
    rng = random.Random(seed)
    kelly_samples: list[float] = []

    for _ in range(n_bootstrap):
        wins   = sum(1 for _ in range(n_sample) if rng.random() < win_prob)
        losses = n_sample - wins
        p_hat  = wins / n_sample
        if p_hat <= 0 or p_hat >= 1:
            kelly_samples.append(0.0)
            continue
        # Kelly formula with sampled p
        b = win_payoff / loss_payoff
        k = (b * p_hat - (1 - p_hat)) / b
        kelly_samples.append(max(0.0, k * fraction))

    kelly_samples.sort()
    n = len(kelly_samples)
    pct = lambda p: kelly_samples[int(p * n)]

    return {
        "p5":    round(pct(0.05), 6),
        "p25":   round(pct(0.25), 6),
        "p50":   round(pct(0.50), 6),
        "p75":   round(pct(0.75), 6),
        "p95":   round(pct(0.95), 6),
        "mean":  round(statistics.mean(kelly_samples), 6),
        "stdev": round(statistics.stdev(kelly_samples), 6) if n > 1 else 0.0,
        "conservative_recommendation": round(pct(0.25), 6),  # 25th percentile — safe
        "n_bootstrap": n_bootstrap,
        "n_sample":    n_sample,
    }


# ── Dynamic Kelly with drawdown adjustment ────────────────────────────────────

def dynamic_kelly(
    base_kelly: float,
    current_bankroll: float,
    peak_bankroll: float,
    *,
    drawdown_scale_factor: float = 2.0,
) -> dict[str, Any]:
    """
    Reduce Kelly fraction proportionally to current drawdown.
    Under drawdown, the real edge may be lower than estimated — this adjusts.

    Formula: adjusted_kelly = base_kelly * (1 - drawdown_pct * scale_factor)
    """
    if peak_bankroll <= 0:
        return {"adjusted_kelly": base_kelly, "drawdown_pct": 0.0}

    drawdown_pct = max(0.0, (peak_bankroll - current_bankroll) / peak_bankroll)
    adjustment   = max(0.0, 1.0 - drawdown_pct * drawdown_scale_factor)
    adjusted     = round(base_kelly * adjustment, 6)

    return {
        "base_kelly":     base_kelly,
        "adjusted_kelly": adjusted,
        "drawdown_pct":   round(drawdown_pct * 100, 3),
        "adjustment_factor": round(adjustment, 4),
        "recommendation": adjusted,
    }


# ── Multi-outcome Kelly ───────────────────────────────────────────────────────

def multi_outcome_kelly(
    outcomes: list[dict[str, float]],
    *,
    fraction: float = 0.25,
) -> dict[str, Any]:
    """
    Kelly for non-binary bets (multiple possible outcomes).

    Args:
        outcomes: List of {"probability": p, "payoff": b} dicts.
                  payoff = net profit per unit staked (0 means lose stake).
        fraction: Kelly multiplier.

    Uses numerical optimisation over 0–1 range.
    """
    if not outcomes:
        return {"error": "no_outcomes"}
    if abs(sum(o["probability"] for o in outcomes) - 1.0) > 0.01:
        return {"error": "probabilities must sum to 1"}

    best_f   = 0.0
    best_ev  = 0.0
    best_log = -float("inf")

    for i in range(1, 200):
        f = i / 200.0
        log_growth = 0.0
        valid = True
        for o in outcomes:
            p = o["probability"]
            b = o["payoff"]  # net profit per unit
            wealth_factor = 1.0 + f * b
            if wealth_factor <= 0:
                valid = False
                break
            log_growth += p * math.log(wealth_factor)
        if valid and log_growth > best_log:
            best_log = log_growth
            best_f   = f

    ev = sum(o["probability"] * o["payoff"] for o in outcomes)
    kelly_frac = best_f * fraction

    return {
        "full_kelly":          round(best_f, 6),
        "fractional_kelly":    round(kelly_frac, 6),
        "expected_log_growth": round(best_log, 6),
        "expected_value":      round(ev, 6),
        "recommended_size":    round(kelly_frac, 6),
        "n_outcomes":          len(outcomes),
        "fraction_used":       fraction,
    }


# ── Precise risk of ruin ──────────────────────────────────────────────────────

def risk_of_ruin(
    win_prob: float,
    win_payoff: float,
    loss_fraction: float,
    starting_units: int,
    *,
    n_simulations: int = 5000,
    ruin_threshold: float = 0.10,
    seed: int | None = 42,
) -> dict[str, Any]:
    """
    Monte Carlo risk of ruin simulation.

    Args:
        win_prob:       Probability of winning each bet.
        win_payoff:     Net profit per unit on win.
        loss_fraction:  Fraction of bankroll risked per bet.
        starting_units: Initial bankroll in units.
        ruin_threshold: Fraction of starting bankroll below which = ruin.
        n_simulations:  Number of paths to simulate.
    """
    rng      = random.Random(seed)
    n_ruined = 0
    path_ends: list[float] = []

    ruin_level = starting_units * ruin_threshold

    for _ in range(n_simulations):
        bank = float(starting_units)
        for _ in range(200):  # 200 bets per path
            bet = bank * loss_fraction
            if rng.random() < win_prob:
                bank += bet * win_payoff
            else:
                bank -= bet
            if bank <= ruin_level:
                n_ruined += 1
                break
        path_ends.append(bank)

    ror = round(n_ruined / n_simulations, 4)
    median_final = statistics.median(path_ends)
    mean_final   = statistics.mean(path_ends)

    return {
        "risk_of_ruin_pct":   round(ror * 100, 2),
        "ruin_probability":   ror,
        "median_final_bank":  round(median_final, 2),
        "mean_final_bank":    round(mean_final, 2),
        "median_growth_pct":  round((median_final / starting_units - 1) * 100, 2),
        "n_simulations":      n_simulations,
        "ruin_threshold_pct": round(ruin_threshold * 100, 1),
        "assessment":         "acceptable" if ror < 0.05 else "elevated" if ror < 0.20 else "high",
    }


# ── Bankroll growth projection ────────────────────────────────────────────────

def growth_projection(
    bankroll: float,
    win_prob: float,
    win_payoff: float,
    bet_fraction: float,
    n_bets: int,
    *,
    n_simulations: int = 1000,
    seed: int | None = 42,
) -> dict[str, Any]:
    """
    Monte Carlo projection of bankroll growth over N bets.
    Returns percentile distribution of outcomes.
    """
    rng       = random.Random(seed)
    endpoints: list[float] = []

    for _ in range(n_simulations):
        bank = bankroll
        for _ in range(n_bets):
            bet = bank * bet_fraction
            if rng.random() < win_prob:
                bank += bet * win_payoff
            else:
                bank -= bet
            if bank <= 0:
                bank = 0
                break
        endpoints.append(bank)

    endpoints.sort()
    n = len(endpoints)
    pct = lambda p: endpoints[int(p * n)]

    return {
        "starting_bankroll": bankroll,
        "n_bets":            n_bets,
        "n_simulations":     n_simulations,
        "p5":                round(pct(0.05), 2),
        "p25":               round(pct(0.25), 2),
        "p50":               round(pct(0.50), 2),
        "p75":               round(pct(0.75), 2),
        "p95":               round(pct(0.95), 2),
        "p50_growth_pct":    round((pct(0.50) / bankroll - 1) * 100, 2),
        "ruin_rate_pct":     round(sum(1 for e in endpoints if e <= 0) / n * 100, 2),
        "mean_final":        round(statistics.mean(endpoints), 2),
    }


# ── Bet sequence CLT confidence ───────────────────────────────────────────────

def sequence_confidence(
    n_bets: int,
    win_prob: float,
    win_payoff: float,
    loss_payoff: float = 1.0,
    confidence: float = 0.95,
) -> dict[str, Any]:
    """
    Uses CLT to estimate confidence interval for total P&L after N bets.
    EV per bet: E = p*win - q*loss
    Var per bet: V = p*(win - E)^2 + q*(-loss - E)^2
    """
    p = win_prob
    q = 1 - p
    ev_per_bet = p * win_payoff - q * loss_payoff
    var_per_bet = p * (win_payoff - ev_per_bet)**2 + q * (-loss_payoff - ev_per_bet)**2

    total_ev  = ev_per_bet * n_bets
    total_std = math.sqrt(var_per_bet * n_bets)

    # Z for confidence
    z = 1.645 if confidence == 0.90 else 1.96 if confidence == 0.95 else 2.576

    ci_low  = total_ev - z * total_std
    ci_high = total_ev + z * total_std
    prob_positive = 0.5 + 0.5 * math.erf(total_ev / (total_std * math.sqrt(2))) if total_std > 0 else 0.5

    return {
        "n_bets":           n_bets,
        "ev_per_bet":       round(ev_per_bet, 6),
        "total_ev":         round(total_ev, 4),
        "total_std":        round(total_std, 4),
        "ci_low":           round(ci_low, 4),
        "ci_high":          round(ci_high, 4),
        "confidence":       confidence,
        "prob_positive_pnl": round(prob_positive, 4),
        "edge_per_bet_pct": round(ev_per_bet / loss_payoff * 100, 4),
    }


# ── Sharpe-adjusted position size ────────────────────────────────────────────

def sharpe_adjusted_kelly(
    kelly_fraction: float,
    historical_sharpe: float,
    target_sharpe: float = 1.0,
) -> dict[str, Any]:
    """
    Reduce Kelly fraction if historical Sharpe is below target.
    Rationale: lower Sharpe = noisier edge = should bet less.
    """
    if historical_sharpe <= 0:
        return {"adjusted_kelly": 0.0, "reason": "negative_sharpe", "multiplier": 0.0}

    multiplier = min(historical_sharpe / target_sharpe, 1.0)
    adjusted   = round(kelly_fraction * multiplier, 6)
    return {
        "original_kelly":   kelly_fraction,
        "adjusted_kelly":   adjusted,
        "multiplier":       round(multiplier, 4),
        "historical_sharpe": historical_sharpe,
        "target_sharpe":    target_sharpe,
    }
