"""
Percentile path builder for frontend charts.

Takes raw MC run outputs and produces P10/P50/P90 series
in the exact shape needed by EquityComparison and the simulator charts.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PercentilePaths:
    """P10/P50/P90 equity series plus key statistics."""
    p10: list[float]
    p50: list[float]
    p90: list[float]
    floor_line: list[float]
    target_line: list[float]
    x_labels: list[str]        # time labels matching series length

    # Scalar summaries
    starting: float
    floor:    float
    target:   float
    p50_final: float
    p10_final: float
    p90_final: float

    def to_chart_series(self) -> list[dict[str, Any]]:
        """
        Produce the recharts-compatible series format used by EquityComparison.
        Each element: {"t": label, "p10": v, "p50": v, "p90": v, "floor": v, "target": v}
        """
        length = len(self.p50)
        result = []
        for i in range(length):
            result.append({
                "t": self.x_labels[i] if i < len(self.x_labels) else str(i),
                "p10":    self.p10[i]    if i < len(self.p10) else self.p10[-1],
                "p50":    self.p50[i]    if i < len(self.p50) else self.p50[-1],
                "p90":    self.p90[i]    if i < len(self.p90) else self.p90[-1],
                "floor":  self.floor_line[i] if i < len(self.floor_line) else self.floor,
                "target": self.target_line[i] if i < len(self.target_line) else self.target,
            })
        return result


def build_percentile_paths(
    sim_result: dict[str, Any],
    *,
    x_unit: str = "bet",       # "bet" | "hour" | "day"
    max_points: int = 120,
) -> PercentilePaths:
    """
    Build PercentilePaths from a simulator result dict.
    Handles output from quick_mc, proposal_replay, bootstrap_replay, or realistic_simulator.
    """
    paths = sim_result.get("paths") or {}
    p10_raw = paths.get("p10") or sim_result.get("equity_curve_p10") or []
    p50_raw = paths.get("p50") or sim_result.get("equity_curve_p50") or sim_result.get("equity_curve") or []
    p90_raw = paths.get("p90") or sim_result.get("equity_curve_p90") or []

    cfg = sim_result.get("config") or {}
    starting = float(cfg.get("starting_capital") or 100.0)
    target   = float(cfg.get("target") or starting * 3.0)
    floor    = float(cfg.get("floor") or starting * 0.801)

    # If only p50 exists, mirror it for p10/p90 with ±10% offset
    if not p10_raw and p50_raw:
        p10_raw = [round(v * 0.88, 2) for v in p50_raw]
    if not p90_raw and p50_raw:
        p90_raw = [round(v * 1.14, 2) for v in p50_raw]

    # If nothing at all, produce a flat line
    if not p50_raw:
        p50_raw = [starting]
        p10_raw = [starting]
        p90_raw = [starting]

    # Downsample to max_points
    def downsample(series: list[float]) -> list[float]:
        if len(series) <= max_points:
            return [round(v, 2) for v in series]
        step = len(series) / max_points
        return [round(series[int(i * step)], 2) for i in range(max_points)]

    p10 = downsample(p10_raw)
    p50 = downsample(p50_raw)
    p90 = downsample(p90_raw)
    length = len(p50)

    # Build x-axis labels
    summary = sim_result.get("summary") or {}
    avg_bets = float(summary.get("avg_bets") or length)
    horizon  = cfg.get("horizon") or "1w"

    if x_unit == "bet":
        x_labels = [str(i) for i in range(length)]
    elif x_unit == "hour":
        from services.simulation.quick_mc import HORIZON_HOURS
        total_h = HORIZON_HOURS.get(horizon, avg_bets / 1.5)
        x_labels = [f"{round(i * total_h / max(length - 1, 1), 1)}h" for i in range(length)]
    else:
        x_labels = [str(i) for i in range(length)]

    floor_line  = [round(floor, 2)]  * length
    target_line = [round(target, 2)] * length

    return PercentilePaths(
        p10=p10,
        p50=p50,
        p90=p90,
        floor_line=floor_line,
        target_line=target_line,
        x_labels=x_labels,
        starting=starting,
        floor=floor,
        target=target,
        p50_final=p50[-1] if p50 else starting,
        p10_final=p10[-1] if p10 else starting,
        p90_final=p90[-1] if p90 else starting,
    )


def merge_paths(*path_sets: PercentilePaths, labels: list[str] | None = None) -> dict[str, Any]:
    """
    Merge multiple PercentilePaths into a comparison overlay.
    Used by the Compare tab.
    """
    result = {"series": [], "x_labels": path_sets[0].x_labels if path_sets else []}
    for i, ps in enumerate(path_sets):
        label = (labels[i] if labels and i < len(labels) else f"Run {i + 1}")
        chart = ps.to_chart_series()
        result["series"].append({
            "label": label,
            "p50": [pt["p50"] for pt in chart],
            "p10": [pt["p10"] for pt in chart],
            "p90": [pt["p90"] for pt in chart],
        })
    return result
