from __future__ import annotations

import math
import random
import uuid
from statistics import mean, pstdev
from typing import Any


def _fractional_kelly(probability: float, odds_b: float = 1.0, fraction: float = 0.25) -> float:
    p = max(0.001, min(0.999, float(probability)))
    b = max(0.05, float(odds_b))
    q = 1.0 - p
    edge = (b * p - q) / b
    return max(0.0, min(0.10, edge * fraction))


def _max_drawdown(equity_curve: list[float]) -> float:
    peak = equity_curve[0] if equity_curve else 0.0
    worst = 0.0
    for value in equity_curve:
        peak = max(peak, value)
        if peak > 0:
            worst = max(worst, (peak - value) / peak)
    return worst


def _sharpe(returns: list[float]) -> float:
    if len(returns) < 2:
        return 0.0
    sigma = pstdev(returns)
    if sigma == 0:
        return 0.0
    return mean(returns) / sigma * math.sqrt(len(returns))


def _load_history(db: Any, bot_id: str, limit: int = 1000) -> list[dict[str, Any]]:
    rows = db.get_calibration_observations(bot_id=bot_id, limit=limit) if hasattr(db, "get_calibration_observations") else []
    complete = [row for row in rows if row.get("actual_outcome") is not None]
    if complete:
        return list(reversed(complete))
    raw = db.get_trades(bot_id=bot_id, limit=limit) if hasattr(db, "get_trades") else []
    # get_trades returns (list, count) tuple or plain list — normalise
    trades = raw[0] if isinstance(raw, tuple) else (raw if isinstance(raw, list) else [])
    result = []
    for trade in reversed(trades):
        amount = float(trade.get("amount", 0) or 0)
        if amount <= 0:
            continue
        won = bool(trade.get("won"))
        net = float(trade.get("net", 0) or 0)
        implied_prob = 0.55 if won else 0.45
        result.append(
            {
                "ts": trade.get("ts"),
                "predicted_probability": implied_prob,
                "actual_outcome": 1.0 if won else 0.0,
                "payload": {"amount": amount, "net": net, "source": "trades"},
            }
        )
    return result


def run_backtest(
    db: Any,
    bot_id: str,
    *,
    date_range: tuple[str | None, str | None] | None = None,
    strategy_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    history = _load_history(db, bot_id)
    if len(history) < 10:
        return {
            "bot_id": bot_id,
            "state": "no_data",
            "message": "Not enough realized history to run a meaningful backtest.",
            "truth_label": "BACKTEST LIMITED",
            "realized_pnl": None,
        }
    params = strategy_params or {}
    runtime = db.get_bot_runtime_state(bot_id) if hasattr(db, "get_bot_runtime_state") else None
    starting_equity = float(params.get("starting_equity") or (runtime or {}).get("bankroll") or 100.0)
    train_size = max(1, int(len(history) * 0.7))
    train_rows = history[:train_size]
    test_rows = history[train_size:]
    baseline_prob = mean(float(row.get("actual_outcome", 0) or 0) for row in train_rows)

    equity = starting_equity
    equity_curve = [equity]
    trade_returns: list[float] = []
    wins = 0
    brier_terms: list[float] = []
    for row in test_rows:
        probability = float(row.get("predicted_probability", baseline_prob) or baseline_prob)
        actual = float(row.get("actual_outcome", 0) or 0)
        payload = row.get("payload") or {}
        odds_b = float(payload.get("odds_b", payload.get("odds_multiplier", 2.0) - 1.0) or 1.0)
        stake_fraction = _fractional_kelly(probability, odds_b=odds_b)
        stake = equity * stake_fraction
        pnl = stake * odds_b if actual >= 1 else -stake
        equity = max(0.0, equity + pnl)
        equity_curve.append(equity)
        trade_returns.append(0.0 if equity_curve[-2] <= 0 else pnl / max(equity_curve[-2], 1e-6))
        if actual >= 1:
            wins += 1
        brier_terms.append((probability - actual) ** 2)

    total_return = 0.0 if starting_equity <= 0 else (equity - starting_equity) / starting_equity
    result = {
        "run_id": uuid.uuid4().hex,
        "bot_id": bot_id,
        "state": "ready",
        "train_count": len(train_rows),
        "test_count": len(test_rows),
        "total_return": round(total_return, 4),
        "sharpe_ratio": round(_sharpe(trade_returns), 4),
        "max_drawdown": round(_max_drawdown(equity_curve), 4),
        "win_rate": round(wins / max(1, len(test_rows)), 4),
        "average_trade": round(mean(trade_returns), 6) if trade_returns else 0.0,
        "brier_score": round(mean(brier_terms), 4) if brier_terms else None,
        "baseline_probability": round(baseline_prob, 4),
        "equity_curve": [round(v, 4) for v in equity_curve],
        "truth_label": "HISTORICAL REPLAY BACKTEST",
        "realized_pnl": None,
    }
    if hasattr(db, "save_backtest_run"):
        db.save_backtest_run(
            result["run_id"],
            bot_id,
            start_ts=date_range[0] if date_range else None,
            end_ts=date_range[1] if date_range else None,
            train_count=result["train_count"],
            test_count=result["test_count"],
            total_return=result["total_return"],
            sharpe_ratio=result["sharpe_ratio"],
            max_drawdown=result["max_drawdown"],
            win_rate=result["win_rate"],
            average_trade=result["average_trade"],
            brier_score=result["brier_score"],
            params=params,
            payload=result,
        )
    return result


def project_bot_from_history(
    db: Any,
    bot_id: str,
    *,
    current_bankroll: float,
    target_amount: float,
    horizon: int = 150,
    runs: int = 300,
) -> dict[str, Any]:
    history = _load_history(db, bot_id, limit=1000)
    if len(history) < 10:
        return {
            "current": round(current_bankroll, 2),
            "p10_final": round(current_bankroll, 2),
            "p50_final": round(current_bankroll, 2),
            "p90_final": round(current_bankroll, 2),
            "time_to_target": "unknown",
            "win_rate": 0.0,
            "expected_value": 0.0,
            "kelly_fraction": 0.0,
            "paths": {"p10": [], "p50": [], "p90": []},
            "truth_label": "HISTORICAL DATA INSUFFICIENT",
        }

    realized = []
    for row in history:
        p = float(row.get("predicted_probability", 0.5) or 0.5)
        actual = float(row.get("actual_outcome", 0) or 0)
        payload = row.get("payload") or {}
        odds_b = float(payload.get("odds_b", payload.get("odds_multiplier", 2.0) - 1.0) or 1.0)
        kelly_fraction = _fractional_kelly(p, odds_b=odds_b)
        trade_return = kelly_fraction * odds_b if actual >= 1 else -kelly_fraction
        realized.append((trade_return, p, kelly_fraction))

    rng = random.Random(1337)
    paths: list[list[float]] = []
    target_hits: list[int] = []
    for _ in range(max(50, min(runs, 500))):
        equity = current_bankroll
        path = [equity]
        hit_step = 0
        for step in range(1, max(10, horizon) + 1):
            trade_return, _, _ = realized[rng.randrange(len(realized))]
            equity = max(0.0, equity * (1.0 + trade_return))
            path.append(round(equity, 4))
            if not hit_step and equity >= target_amount:
                hit_step = step
        target_hits.append(hit_step)
        paths.append(path)

    ordered = sorted(paths, key=lambda row: row[-1])
    def _subsample(path: list[float], n: int = 30) -> list[float]:
        step = max(1, len(path) // n)
        return [round(path[i], 2) for i in range(0, len(path), step)][:n]

    p10 = ordered[int(len(ordered) * 0.1)]
    p50 = ordered[int(len(ordered) * 0.5)]
    p90 = ordered[int(len(ordered) * 0.9)]
    non_zero_hits = [step for step in target_hits if step > 0]
    avg_hit_step = mean(non_zero_hits) if non_zero_hits else None
    avg_prob = mean(prob for _, prob, _ in realized)
    avg_kelly = mean(kelly for _, _, kelly in realized)
    avg_trade = mean(ret for ret, _, _ in realized)
    bets_per_day = 86400 / max(0.05, float(getattr(__import__("config"), "BET_DELAY_SECONDS", 1.2)))
    days_to_target = (avg_hit_step / bets_per_day) if avg_hit_step else None
    return {
        "current": round(current_bankroll, 2),
        "p10_final": round(p10[-1], 2),
        "p50_final": round(p50[-1], 2),
        "p90_final": round(p90[-1], 2),
        "time_to_target": f"{days_to_target:.1f}d" if days_to_target is not None else "target_not_hit",
        "win_rate": round(avg_prob * 100, 1),
        "expected_value": round(avg_trade * 100, 3),
        "kelly_fraction": round(avg_kelly, 4),
        "paths": {"p10": _subsample(p10), "p50": _subsample(p50), "p90": _subsample(p90)},
        "truth_label": "HISTORICAL REPLAY PROJECTION",
        "confidence_interval": [round(p10[-1], 2), round(p90[-1], 2)],
    }
