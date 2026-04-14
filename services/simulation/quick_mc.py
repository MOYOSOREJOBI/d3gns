"""
Quick synthetic Monte Carlo simulator.

Fast path: no DB reads, no external data, pure random walk.
Used for UI exploration and quick "what if" checks.
Produces ForceField-aware equity paths with floor repeller.
"""
from __future__ import annotations

import math
import random
import uuid
from dataclasses import dataclass, field
from typing import Any

from services.risk_kernel import MAX_DRAWDOWN_PCT, REPEL_START_PCT, compute_dynamic_repel
from services.simulation.target_timing import compute_target_timing, format_timing_for_api

HORIZON_HOURS: dict[str, float] = {
    "3h":  3,
    "12h": 12,
    "24h": 24,
    "3d":  72,
    "1w":  168,
    "1m":  720,
    "3m":  2160,
    "6m":  4320,
    "1y":  8760,
}

TARGET_MULTIPLES = [1.2, 3.0, 10.0, 20.0]


@dataclass
class QuickMCConfig:
    starting_capital: float = 100.0
    target_multiple:  float = 3.0
    floor_pct:        float = MAX_DRAWDOWN_PCT   # 0.199
    horizon:          str   = "1w"               # key in HORIZON_HOURS
    win_rate:         float = 0.58
    avg_win_mult:     float = 1.05               # avg win = size * 1.05
    avg_loss_mult:    float = 0.97               # avg loss = size * 0.97
    bet_size_pct:     float = 0.03               # 3% of capital per bet
    bets_per_hour:    float = 1.5
    fee_drag_pct:     float = 0.005              # 0.5% fee per bet
    n_runs:           int   = 300
    seed:             int | None = None

    @property
    def duration_hours(self) -> float:
        return HORIZON_HOURS.get(self.horizon, 168)

    @property
    def n_bets(self) -> int:
        return max(1, int(self.duration_hours * self.bets_per_hour))

    @property
    def target(self) -> float:
        return self.starting_capital * self.target_multiple

    @property
    def floor(self) -> float:
        return self.starting_capital * (1 - self.floor_pct)


@dataclass
class MCRunResult:
    final_capital: float
    hit_target: bool
    hit_floor:  bool
    bets_taken: int
    peak:       float
    trough:     float
    max_drawdown_pct: float
    outcome_reason: str
    curve:      list[float] = field(default_factory=list)


def _run_single(cfg: QuickMCConfig, rng: random.Random) -> MCRunResult:
    capital = cfg.starting_capital
    floor   = cfg.floor
    target  = cfg.target
    peak    = capital
    trough  = capital
    max_drawdown_pct = 0.0
    bets    = 0
    curve   = [capital]

    for _ in range(cfg.n_bets):
        if capital <= floor:
            break
        if capital >= target:
            break

        repel = compute_dynamic_repel(capital, cfg.starting_capital, cfg.floor_pct, REPEL_START_PCT)
        if repel["zone"] == "FLOOR":
            break

        size = capital * cfg.bet_size_pct * repel["multiplier"]
        if size < 0.01:
            continue

        won = rng.random() < cfg.win_rate
        if won:
            pnl = size * (cfg.avg_win_mult - 1)
        else:
            pnl = -size * (1 - cfg.avg_loss_mult)

        pnl -= size * cfg.fee_drag_pct
        capital = max(floor, capital + pnl)
        bets += 1

        if capital > peak:
            peak = capital
        if capital < trough:
            trough = capital
        if peak > 0:
            max_drawdown_pct = max(max_drawdown_pct, (peak - capital) / peak * 100.0)

        # Sample curve every ~20 bets to keep it small
        if bets % max(1, cfg.n_bets // 60) == 0:
            curve.append(round(capital, 2))

    if curve[-1] != round(capital, 2):
        curve.append(round(capital, 2))

    return MCRunResult(
        final_capital=round(capital, 2),
        hit_target=capital >= target,
        hit_floor=capital <= floor + 0.01,
        bets_taken=bets,
        peak=round(peak, 2),
        trough=round(trough, 2),
        max_drawdown_pct=round(max_drawdown_pct, 2),
        outcome_reason=(
            "target_hit" if capital >= target else
            "floor_hover" if capital <= floor + 0.01 else
            "horizon_exhausted"
        ),
        curve=curve,
    )


def run_quick_mc(cfg: QuickMCConfig) -> dict[str, Any]:
    """
    Run N Monte Carlo paths and return statistics + percentile paths.
    Output matches the shape expected by bankroll_paths.build_percentile_paths.
    """
    rng = random.Random(cfg.seed)
    runs: list[MCRunResult] = [_run_single(cfg, rng) for _ in range(cfg.n_runs)]

    finals = sorted(r.final_capital for r in runs)
    hit_target = sum(1 for r in runs if r.hit_target)
    hit_floor  = sum(1 for r in runs if r.hit_floor)
    avg_bets   = sum(r.bets_taken for r in runs) / max(len(runs), 1)
    drawdowns = sorted(r.max_drawdown_pct for r in runs)
    failure_breakdown = {
        "target_hit": sum(1 for r in runs if r.outcome_reason == "target_hit"),
        "floor_hover": sum(1 for r in runs if r.outcome_reason == "floor_hover"),
        "horizon_exhausted": sum(1 for r in runs if r.outcome_reason == "horizon_exhausted"),
    }

    def pct(p: float) -> float:
        idx = int(p * len(finals))
        return finals[min(idx, len(finals) - 1)]

    p10, p50, p90 = pct(0.10), pct(0.50), pct(0.90)

    # Align all curves to the same length by padding with final value
    max_len = max(len(r.curve) for r in runs) if runs else 1
    padded = [
        r.curve + [r.curve[-1]] * (max_len - len(r.curve))
        for r in runs
    ]

    # Extract percentile series
    def col(i: int) -> list[float]:
        vals = sorted(row[i] for row in padded)
        return [
            round(vals[int(0.10 * len(vals))], 2),
            round(vals[int(0.50 * len(vals))], 2),
            round(vals[int(0.90 * len(vals))], 2),
        ]

    step = max(1, max_len // 80)
    series_p10, series_p50, series_p90 = [], [], []
    for i in range(0, max_len, step):
        c = col(i)
        series_p10.append(c[0])
        series_p50.append(c[1])
        series_p90.append(c[2])

    # Target hit timing (median bets to hit, if hit)
    hit_runs = [r for r in runs if r.hit_target]
    median_bets_to_hit = (
        sorted(r.bets_taken for r in hit_runs)[len(hit_runs) // 2]
        if hit_runs else None
    )
    hours_to_target = (
        round(median_bets_to_hit / max(cfg.bets_per_hour, 0.001), 1)
        if median_bets_to_hit else None
    )
    timing = compute_target_timing(
        [
            {"final": r.final_capital, "hit_target": r.hit_target, "n_taken": r.bets_taken}
            for r in runs
        ],
        starting_capital=cfg.starting_capital,
        target_multiple=cfg.target_multiple,
        bets_per_hour=cfg.bets_per_hour,
    )
    assumption_summary = (
        f"Synthetic Monte Carlo using {cfg.n_runs} paths across {cfg.horizon} "
        f"({cfg.duration_hours:.0f}h), win rate {cfg.win_rate:.1%}, "
        f"bet size {cfg.bet_size_pct:.1%}, fee drag {cfg.fee_drag_pct:.2%}, "
        f"ForceField floor at ${cfg.floor:.2f}."
    )
    warnings = [
        "Synthetic quick mode is exploratory only." if True else "",
        "Floor behavior is a sizing repeller, not a live-market guarantee.",
    ]

    return {
        "sim_id": f"qmc_{uuid.uuid4().hex[:8]}",
        "mode": "quick_mc",
        "config": {
            "starting_capital": cfg.starting_capital,
            "target": cfg.target,
            "floor": cfg.floor,
            "horizon": cfg.horizon,
            "duration_hours": cfg.duration_hours,
            "win_rate": cfg.win_rate,
            "bet_size_pct": cfg.bet_size_pct,
            "bets_per_hour": cfg.bets_per_hour,
            "n_runs": cfg.n_runs,
        },
        "summary": {
            "p10": round(p10, 2),
            "p50": round(p50, 2),
            "p90": round(p90, 2),
            "hit_target_pct": round(hit_target / max(len(runs), 1) * 100, 1),
            "hit_floor_pct":  round(hit_floor  / max(len(runs), 1) * 100, 1),
            "avg_bets": round(avg_bets, 1),
            "median_bets_to_hit": median_bets_to_hit,
            "hours_to_target": hours_to_target,
            "roi_p50": round((p50 - cfg.starting_capital) / cfg.starting_capital * 100, 1),
            "expected_trade_count": round(avg_bets, 1),
            "expected_hold_hours": round(cfg.duration_hours / max(avg_bets, 1), 3),
        },
        "paths": {
            "p10": series_p10,
            "p50": series_p50,
            "p90": series_p90,
        },
        "targets": format_timing_for_api(timing),
        "max_drawdown_distribution": {
            "p10": round(drawdowns[int(0.10 * len(drawdowns))], 2),
            "p50": round(drawdowns[int(0.50 * len(drawdowns))], 2),
            "p90": round(drawdowns[int(0.90 * len(drawdowns))], 2),
            "worst": round(drawdowns[-1], 2),
        },
        "failure_breakdown": failure_breakdown,
        "assumptions": {
            "synthetic": True,
            "horizon": cfg.horizon,
            "duration_hours": cfg.duration_hours,
            "win_rate": cfg.win_rate,
            "bet_size_pct": cfg.bet_size_pct,
            "fee_drag_pct": cfg.fee_drag_pct,
            "bets_per_hour": cfg.bets_per_hour,
            "floor_pct": cfg.floor_pct,
        },
        "assumption_summary": assumption_summary,
        "runs": [
            {
                "final": r.final_capital,
                "hit_target": r.hit_target,
                "n_taken": r.bets_taken,
                "max_drawdown_pct": r.max_drawdown_pct,
                "outcome_reason": r.outcome_reason,
            }
            for r in runs
        ],
        "truth_label": "SYNTHETIC",
        "decision_useful": False,
        "warnings": warnings,
        "warning": "Quick MC uses synthetic random walk. Not decision-grade evidence.",
    }
