"""
Target timing module.

Given a set of MC run results, computes:
  - P(hit Nx by horizon H) for all ForceField milestone multiples
  - Median time-to-target (in hours and bets)
  - Percentile distribution of first-hit times

This is the analytical layer above quick_mc / proposal_replay / bootstrap_replay.
All three simulators produce compatible output shapes.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any


TARGET_MULTIPLES = [1.2, 3.0, 10.0, 20.0]
TARGET_NAMES     = {1.2: "+20%", 3.0: "3×", 10.0: "10×", 20.0: "20×"}

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


@dataclass
class TargetTimingResult:
    starting_capital: float
    target_multiple: float
    target_capital: float

    # Core stats
    hit_probability: float      # 0-1
    hit_probability_pct: float  # 0-100

    # Timing (when target IS hit)
    median_bets_to_hit: int | None
    p10_bets_to_hit: int | None
    p90_bets_to_hit: int | None
    median_hours_to_hit: float | None

    # Multi-target breakdown
    by_multiple: dict[float, dict[str, Any]] = field(default_factory=dict)


def compute_target_timing(
    runs: list[dict[str, Any]],
    *,
    starting_capital: float,
    target_multiple: float,
    bets_per_hour: float = 1.5,
    horizon_hours: float | None = None,
) -> TargetTimingResult:
    """
    Compute timing statistics from a list of run result dicts.
    Each run must have: {"final": float, "hit_target": bool, "n_taken": int}
    Optional per-path curve for timing: {"curve": [float, ...]}
    """
    target = starting_capital * target_multiple
    n = max(len(runs), 1)

    hit_runs  = [r for r in runs if r.get("hit_target")]
    hit_bets  = sorted(r.get("n_taken", 0) for r in hit_runs)
    hit_prob  = len(hit_runs) / n

    def _median(lst: list) -> int | None:
        if not lst:
            return None
        return lst[len(lst) // 2]

    def _pct(lst: list, p: float) -> int | None:
        if not lst:
            return None
        return lst[max(0, int(p * len(lst)) - 1)]

    median_bets = _median(hit_bets)
    p10_bets    = _pct(hit_bets, 0.10)
    p90_bets    = _pct(hit_bets, 0.90)
    median_hours = (
        round(median_bets / max(bets_per_hour, 0.001), 1)
        if median_bets is not None else None
    )

    # Multi-target breakdown using final capitals
    finals = [r.get("final", starting_capital) for r in runs]
    by_multiple: dict[float, dict[str, Any]] = {}
    for mult in TARGET_MULTIPLES:
        t_cap = starting_capital * mult
        hits  = sum(1 for f in finals if f >= t_cap)
        prob  = hits / n
        hit_r = [r for r in runs if r.get("final", 0) >= t_cap]
        hit_b = sorted(r.get("n_taken", 0) for r in hit_r)
        by_multiple[mult] = {
            "target": round(t_cap, 2),
            "label": TARGET_NAMES.get(mult, f"{mult}×"),
            "hit_probability": round(prob, 4),
            "hit_probability_pct": round(prob * 100, 1),
            "median_bets_to_hit": _median(hit_b),
            "median_hours_to_hit": (
                round(_median(hit_b) / max(bets_per_hour, 0.001), 1)
                if _median(hit_b) is not None else None
            ),
        }

    return TargetTimingResult(
        starting_capital=starting_capital,
        target_multiple=target_multiple,
        target_capital=round(target, 2),
        hit_probability=round(hit_prob, 4),
        hit_probability_pct=round(hit_prob * 100, 1),
        median_bets_to_hit=median_bets,
        p10_bets_to_hit=p10_bets,
        p90_bets_to_hit=p90_bets,
        median_hours_to_hit=median_hours,
        by_multiple=by_multiple,
    )


def timing_for_horizon(
    timing: TargetTimingResult,
    horizon: str,
    *,
    bets_per_hour: float = 1.5,
) -> dict[str, Any]:
    """
    Given a TargetTimingResult, compute horizon-specific probability estimates.
    E.g. "given we run for 1w, what is P(hit 3×)?"
    """
    hours = HORIZON_HOURS.get(horizon, 168)
    max_bets = int(hours * bets_per_hour)

    result: dict[str, Any] = {
        "horizon": horizon,
        "horizon_hours": hours,
        "max_bets_in_horizon": max_bets,
    }

    # For each target: check if median_bets_to_hit is within horizon
    for mult, data in timing.by_multiple.items():
        median_b = data.get("median_bets_to_hit")
        achievable = (
            median_b is not None and median_b <= max_bets
        )
        result[f"target_{mult}x"] = {
            **data,
            "within_horizon": achievable,
            "horizon_note": (
                f"Median path hits {data['label']} in {data.get('median_hours_to_hit', '?')}h"
                if achievable else
                f"{data['label']} target likely beyond {horizon} horizon"
            ),
        }

    return result


def format_timing_for_api(timing: TargetTimingResult) -> dict[str, Any]:
    """Serialize TargetTimingResult into a JSON-safe dict for the API."""
    return {
        "starting_capital": timing.starting_capital,
        "target_multiple": timing.target_multiple,
        "target_capital": timing.target_capital,
        "hit_probability": timing.hit_probability,
        "hit_probability_pct": timing.hit_probability_pct,
        "median_bets_to_hit": timing.median_bets_to_hit,
        "p10_bets_to_hit": timing.p10_bets_to_hit,
        "p90_bets_to_hit": timing.p90_bets_to_hit,
        "median_hours_to_hit": timing.median_hours_to_hit,
        "by_multiple": {
            str(k): v for k, v in timing.by_multiple.items()
        },
    }
