"""
services/simulation — modular simulation suite for DeG£N$.

Three modes:
  quick_mc        — fast synthetic Monte Carlo (no external data)
  bootstrap_replay— historical bootstrap from CSV / odds snapshots
  proposal_replay — driven by real proposal/signal logs from the DB

Supporting modules:
  target_timing   — P(hit Nx target by horizon)
  bankroll_paths  — P10 / P50 / P90 equity series for the frontend
  calibration     — Brier calibration and outcome drift per bot
"""
from services.simulation.quick_mc import run_quick_mc, QuickMCConfig
from services.simulation.target_timing import compute_target_timing, TargetTimingResult
from services.simulation.bankroll_paths import build_percentile_paths, PercentilePaths

__all__ = [
    "run_quick_mc",
    "QuickMCConfig",
    "compute_target_timing",
    "TargetTimingResult",
    "build_percentile_paths",
    "PercentilePaths",
]
