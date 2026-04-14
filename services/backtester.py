"""
Historical strategy backtester — pure Python/stdlib, no pandas required.

Supports:
  - Binary signal backtesting (buy/sell on signal)
  - Kelly-sized position backtesting
  - Walk-forward validation (rolling windows)
  - Monte Carlo permutation test for significance
  - Sharpe, Sortino, Calmar, max drawdown metrics
  - Multi-strategy comparison
"""
from __future__ import annotations

import math
import random
import statistics
from typing import Any


# ── Core data structures ──────────────────────────────────────────────────────

class Trade:
    __slots__ = ("entry_price", "exit_price", "size", "side", "pnl", "pnl_pct", "bars_held", "label")

    def __init__(
        self,
        entry_price: float,
        exit_price: float,
        size: float = 1.0,
        side: str = "long",
        bars_held: int = 1,
        label: str = "",
    ) -> None:
        self.entry_price = entry_price
        self.exit_price  = exit_price
        self.size        = size
        self.side        = side
        self.bars_held   = bars_held
        self.label       = label

        if side == "long":
            self.pnl = (exit_price - entry_price) * size
            self.pnl_pct = (exit_price - entry_price) / entry_price
        else:  # short
            self.pnl = (entry_price - exit_price) * size
            self.pnl_pct = (entry_price - exit_price) / entry_price


# ── Metrics ───────────────────────────────────────────────────────────────────

def _equity_curve(trades: list[Trade], starting_equity: float = 1000.0) -> list[float]:
    equity = starting_equity
    curve  = [equity]
    for t in trades:
        equity += t.pnl
        curve.append(equity)
    return curve


def max_drawdown(equity_curve: list[float]) -> float:
    """Maximum peak-to-trough drawdown as a fraction (0–1)."""
    peak = equity_curve[0]
    mdd  = 0.0
    for v in equity_curve:
        if v > peak:
            peak = v
        dd = (peak - v) / peak if peak > 0 else 0.0
        if dd > mdd:
            mdd = dd
    return round(mdd, 6)


def sharpe_ratio(returns: list[float], risk_free: float = 0.0) -> float:
    """Annualised Sharpe (assumes daily returns if len ≈ 252)."""
    if len(returns) < 2:
        return 0.0
    excess = [r - risk_free for r in returns]
    mean_e = statistics.mean(excess)
    std_e  = statistics.stdev(excess)
    if std_e == 0:
        return 0.0
    # Annualise assuming ~252 trading periods
    return round(mean_e / std_e * math.sqrt(252), 4)


def sortino_ratio(returns: list[float], risk_free: float = 0.0) -> float:
    """Sortino ratio — only penalises downside deviation."""
    if len(returns) < 2:
        return 0.0
    excess       = [r - risk_free for r in returns]
    mean_e       = statistics.mean(excess)
    downside     = [r for r in excess if r < 0]
    if not downside:
        return float("inf")
    downside_dev = math.sqrt(sum(r ** 2 for r in downside) / len(downside))
    if downside_dev == 0:
        return 0.0
    return round(mean_e / downside_dev * math.sqrt(252), 4)


def calmar_ratio(returns: list[float], equity_curve: list[float]) -> float:
    """Calmar = annualised return / max drawdown."""
    mdd = max_drawdown(equity_curve)
    if mdd == 0:
        return float("inf")
    annualised_return = sum(returns) * (252 / max(len(returns), 1))
    return round(annualised_return / mdd, 4)


def compute_metrics(trades: list[Trade], starting_equity: float = 1000.0) -> dict[str, Any]:
    """Full performance metrics from a list of Trade objects."""
    if not trades:
        return {"error": "no_trades", "trade_count": 0}

    equity_curve = _equity_curve(trades, starting_equity)
    final_equity = equity_curve[-1]
    total_return = (final_equity - starting_equity) / starting_equity

    pnls     = [t.pnl     for t in trades]
    pnl_pcts = [t.pnl_pct for t in trades]
    winners  = [t for t in trades if t.pnl > 0]
    losers   = [t for t in trades if t.pnl <= 0]

    win_rate    = len(winners) / len(trades)
    avg_win     = statistics.mean([t.pnl for t in winners]) if winners else 0.0
    avg_loss    = statistics.mean([t.pnl for t in losers])  if losers  else 0.0
    profit_factor = (
        sum(t.pnl for t in winners) / abs(sum(t.pnl for t in losers))
        if losers and sum(t.pnl for t in losers) != 0 else float("inf")
    )
    expectancy = statistics.mean(pnls) if pnls else 0.0
    mdd        = max_drawdown(equity_curve)
    sharpe     = sharpe_ratio(pnl_pcts)
    sortino    = sortino_ratio(pnl_pcts)
    calmar     = calmar_ratio(pnl_pcts, equity_curve)

    # Consecutive stats
    max_consec_wins  = _max_consecutive(trades, win=True)
    max_consec_loss  = _max_consecutive(trades, win=False)

    return {
        "trade_count":       len(trades),
        "win_count":         len(winners),
        "loss_count":        len(losers),
        "win_rate":          round(win_rate, 4),
        "avg_win":           round(avg_win, 4),
        "avg_loss":          round(avg_loss, 4),
        "profit_factor":     round(profit_factor, 4) if profit_factor != float("inf") else "inf",
        "expectancy":        round(expectancy, 4),
        "total_pnl":         round(sum(pnls), 4),
        "total_return_pct":  round(total_return * 100, 4),
        "starting_equity":   starting_equity,
        "final_equity":      round(final_equity, 4),
        "max_drawdown_pct":  round(mdd * 100, 4),
        "sharpe_ratio":      sharpe,
        "sortino_ratio":     sortino,
        "calmar_ratio":      calmar,
        "max_consec_wins":   max_consec_wins,
        "max_consec_losses": max_consec_loss,
        "equity_curve":      [round(v, 4) for v in equity_curve],
    }


def _max_consecutive(trades: list[Trade], win: bool) -> int:
    best = streak = 0
    for t in trades:
        is_win = t.pnl > 0
        if is_win == win:
            streak += 1
            best = max(best, streak)
        else:
            streak = 0
    return best


# ── Signal backtester ─────────────────────────────────────────────────────────

def backtest_signals(
    closes: list[float],
    signals: list[str],   # "buy", "sell", "hold" aligned with closes
    *,
    hold_bars: int = 1,
    position_size: float = 100.0,
    starting_equity: float = 1000.0,
    allow_short: bool = False,
) -> dict[str, Any]:
    """
    Backtest a sequence of signals against OHLC closes.

    Args:
        closes:         Price at each bar.
        signals:        Parallel list of 'buy'/'sell'/'hold' per bar.
        hold_bars:      Number of bars to hold each position.
        position_size:  Dollar amount per trade (not Kelly-adjusted).
        starting_equity: Starting bankroll.
        allow_short:    Whether sell signals open short positions.
    """
    if len(closes) != len(signals):
        return {"error": "closes and signals length mismatch"}

    trades: list[Trade] = []
    i = 0
    while i < len(signals) - hold_bars:
        sig = signals[i].lower()
        entry_price = closes[i]
        exit_price  = closes[i + hold_bars]
        if sig == "buy":
            trades.append(Trade(entry_price, exit_price, position_size, "long", hold_bars, f"bar_{i}"))
        elif sig == "sell" and allow_short:
            trades.append(Trade(entry_price, exit_price, position_size, "short", hold_bars, f"bar_{i}"))
        i += 1

    metrics = compute_metrics(trades, starting_equity)
    metrics["hold_bars"]       = hold_bars
    metrics["position_size"]   = position_size
    metrics["bars_total"]      = len(closes)
    metrics["signal_coverage"] = round(len(trades) / max(len(signals), 1), 4)
    return metrics


# ── Kelly-sized backtester ────────────────────────────────────────────────────

def backtest_kelly(
    closes: list[float],
    signals: list[str],
    probabilities: list[float],     # model probability per bar
    market_probs: list[float],      # implied market probability per bar
    *,
    hold_bars: int = 1,
    bankroll: float = 1000.0,
    kelly_fraction: float = 0.25,
    max_bet_pct: float = 0.10,
) -> dict[str, Any]:
    """
    Kelly-criterion-sized backtester for prediction market signals.
    Each trade uses a fraction of the current bankroll proportional to edge.
    """
    from services.kelly_sizer import kelly_prediction_market

    if not (len(closes) == len(signals) == len(probabilities) == len(market_probs)):
        return {"error": "all input lists must be the same length"}

    trades: list[Trade] = []
    equity = bankroll

    i = 0
    while i < len(signals) - hold_bars:
        sig = signals[i].lower()
        if sig not in ("buy", "sell"):
            i += 1
            continue

        side       = "YES" if sig == "buy" else "NO"
        model_p    = probabilities[i]
        market_p   = market_probs[i]
        entry      = closes[i]
        exit_price = closes[i + hold_bars]

        k = kelly_prediction_market(
            model_p, market_p, side=side,
            fraction=kelly_fraction, max_position_pct=max_bet_pct,
        )
        size = equity * k.get("recommended_size", 0.0)

        if size > 0:
            trade_side = "long" if sig == "buy" else "short"
            trades.append(Trade(entry, exit_price, size, trade_side, hold_bars, f"bar_{i}"))
            equity += trades[-1].pnl

        i += 1

    metrics = compute_metrics(trades, bankroll)
    metrics["kelly_fraction"] = kelly_fraction
    metrics["max_bet_pct"]    = max_bet_pct
    return metrics


# ── Walk-forward validation ───────────────────────────────────────────────────

def walk_forward(
    closes: list[float],
    signal_fn,           # callable(closes) -> list[str]
    *,
    train_pct: float = 0.70,
    n_folds: int = 5,
    hold_bars: int = 1,
    position_size: float = 100.0,
) -> dict[str, Any]:
    """
    Rolling walk-forward: train on train_pct, test on remainder, slide window.
    signal_fn receives a closes list and should return a parallel signals list.
    Returns per-fold metrics and aggregate stats.
    """
    n = len(closes)
    fold_size = n // n_folds
    fold_results = []

    for fold in range(n_folds):
        start = fold * fold_size
        end   = start + fold_size if fold < n_folds - 1 else n
        train_end = start + int((end - start) * train_pct)

        train_closes = closes[start:train_end]
        test_closes  = closes[train_end:end]

        if len(test_closes) < hold_bars + 1:
            continue

        try:
            all_signals = signal_fn(train_closes + test_closes)
            test_signals = all_signals[len(train_closes):]
        except Exception as exc:
            fold_results.append({"fold": fold, "error": str(exc)})
            continue

        result = backtest_signals(
            test_closes, test_signals,
            hold_bars=hold_bars,
            position_size=position_size,
        )
        result["fold"] = fold
        result["train_bars"] = len(train_closes)
        result["test_bars"]  = len(test_closes)
        fold_results.append(result)

    valid = [f for f in fold_results if "error" not in f and f.get("trade_count", 0) > 0]
    if not valid:
        return {"error": "no valid folds", "folds": fold_results}

    avg_win_rate    = statistics.mean([f["win_rate"]         for f in valid])
    avg_sharpe      = statistics.mean([f["sharpe_ratio"]     for f in valid])
    avg_return      = statistics.mean([f["total_return_pct"] for f in valid])
    avg_mdd         = statistics.mean([f["max_drawdown_pct"] for f in valid])

    return {
        "n_folds":           n_folds,
        "valid_folds":       len(valid),
        "avg_win_rate":      round(avg_win_rate, 4),
        "avg_sharpe_ratio":  round(avg_sharpe, 4),
        "avg_total_return_pct": round(avg_return, 4),
        "avg_max_drawdown_pct": round(avg_mdd, 4),
        "folds":             fold_results,
    }


# ── Monte Carlo permutation test ──────────────────────────────────────────────

def monte_carlo_significance(
    trades: list[Trade],
    n_simulations: int = 1000,
    seed: int | None = 42,
) -> dict[str, Any]:
    """
    Permutation test: randomly shuffle trade outcomes N times and compare
    to actual strategy. Estimates p-value for strategy edge.
    """
    if not trades:
        return {"error": "no trades"}

    rng = random.Random(seed)
    actual_pnl = sum(t.pnl for t in trades)
    actual_mdd = max_drawdown(_equity_curve(trades))

    outcomes = [t.pnl for t in trades]
    beat_count_pnl = 0
    beat_count_mdd = 0

    for _ in range(n_simulations):
        shuffled = outcomes[:]
        rng.shuffle(shuffled)
        sim_pnl = sum(shuffled)
        # Build fake equity curve for mdd
        eq = [1000.0]
        e  = 1000.0
        for p in shuffled:
            e += p
            eq.append(e)
        sim_mdd = max_drawdown(eq)
        if sim_pnl < actual_pnl:
            beat_count_pnl += 1
        if sim_mdd > actual_mdd:
            beat_count_mdd += 1

    p_value_pnl = 1.0 - beat_count_pnl / n_simulations
    p_value_mdd = 1.0 - beat_count_mdd / n_simulations
    return {
        "n_simulations":   n_simulations,
        "actual_total_pnl": round(actual_pnl, 4),
        "actual_max_dd":    round(actual_mdd, 4),
        "p_value_pnl":      round(p_value_pnl, 4),
        "p_value_mdd":      round(p_value_mdd, 4),
        "significant_pnl":  p_value_pnl < 0.05,
        "significant_mdd":  p_value_mdd < 0.05,
        "beat_pct_pnl":     round(beat_count_pnl / n_simulations * 100, 1),
    }


# ── Multi-strategy comparison ─────────────────────────────────────────────────

def compare_strategies(
    closes: list[float],
    strategies: dict[str, list[str]],   # {name: signals_list}
    *,
    hold_bars: int = 1,
    position_size: float = 100.0,
    starting_equity: float = 1000.0,
) -> dict[str, Any]:
    """
    Compare multiple signal strategies on the same price data.
    strategies: {"strategy_name": [signals_list], ...}
    Returns ranked results by Sharpe ratio.
    """
    results = {}
    for name, signals in strategies.items():
        r = backtest_signals(
            closes, signals,
            hold_bars=hold_bars,
            position_size=position_size,
            starting_equity=starting_equity,
        )
        r["strategy"] = name
        results[name] = r

    ranked = sorted(
        [v for v in results.values() if "error" not in v],
        key=lambda x: x.get("sharpe_ratio", -999),
        reverse=True,
    )

    return {
        "strategies":    results,
        "ranked":        ranked,
        "best_strategy": ranked[0]["strategy"] if ranked else None,
        "count":         len(results),
    }


# ── Quick smoke test ──────────────────────────────────────────────────────────

def _demo() -> None:
    """Quick self-test with synthetic data."""
    import math
    n = 100
    closes = [100 + 10 * math.sin(i * 0.1) + i * 0.05 for i in range(n)]
    signals = ["buy" if closes[i] < closes[i-1] else "sell" if closes[i] > closes[i-1] + 0.5 else "hold"
               for i in range(n)]
    result = backtest_signals(closes, signals, hold_bars=3, position_size=50.0)
    print(f"Trades: {result['trade_count']}, Win rate: {result['win_rate']}, "
          f"Sharpe: {result['sharpe_ratio']}, MDD: {result['max_drawdown_pct']}%")

    mc = monte_carlo_significance(
        [Trade(c, closes[min(i+3, n-1)], 50.0, "long", 3) for i, c in enumerate(closes[:-3])]
    )
    print(f"Monte Carlo p-value (PnL): {mc['p_value_pnl']}, significant: {mc['significant_pnl']}")


if __name__ == "__main__":
    _demo()
