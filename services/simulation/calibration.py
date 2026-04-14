"""
Brier calibration and outcome-drift tracking per bot.

Brier score = mean((predicted_probability - actual_outcome)^2)
  0.0 = perfect calibration
  0.25 = equivalent to random guessing (50/50)
  >0.25 = worse than guessing

Also tracks:
  - Outcome drift: is the bot's observed win rate drifting from its predicted win rate?
  - Calibration curve: binned predicted probability vs observed frequency
  - Edge erosion: is edge shrinking over time?
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CalibrationResult:
    bot_id: str
    n: int                       # number of observations
    brier_score: float           # 0-1, lower is better
    mean_predicted: float        # average predicted probability
    observed_win_rate: float     # actual win rate
    calibration_error: float     # abs(mean_predicted - observed_win_rate)
    edge_bps: float              # (observed_wr - 0.5) * 10000
    is_calibrated: bool          # brier < 0.20 and calibration_error < 0.05
    is_positive_ev: bool         # edge_bps > 0
    drift_detected: bool         # recent win rate diverging from historic
    calibration_curve: list[dict[str, float]] = field(default_factory=list)
    recommendation: str = ""


def _bin_calibration(
    observations: list[dict[str, Any]],
    n_bins: int = 10,
) -> list[dict[str, float]]:
    """Compute calibration curve: binned predicted prob vs observed frequency."""
    bins: dict[int, list[float]] = {i: [] for i in range(n_bins)}

    for obs in observations:
        pred = float(obs.get("confidence") or obs.get("predicted_probability") or 0.5)
        raw_out = obs.get("outcome") or obs.get("actual_outcome")
        if raw_out is None:
            continue
        o_str = str(raw_out).lower()
        outcome = 1.0 if o_str in ("1", "true", "win", "yes", "correct", "1.0") else 0.0
        bin_idx = min(int(pred * n_bins), n_bins - 1)
        bins[bin_idx].append(outcome)

    result = []
    for i in range(n_bins):
        outcomes = bins[i]
        mid = (i + 0.5) / n_bins
        freq = sum(outcomes) / len(outcomes) if outcomes else None
        result.append({
            "predicted_midpoint": round(mid, 3),
            "observed_frequency": round(freq, 3) if freq is not None else None,
            "n": len(outcomes),
        })
    return result


def _detect_drift(observations: list[dict[str, Any]], window: int = 20) -> bool:
    """
    Detect if recent win rate is drifting significantly from historic.
    Returns True if drift is likely.
    """
    if len(observations) < window * 2:
        return False

    def wr(obs_list: list[dict]) -> float:
        wins = sum(
            1 for o in obs_list
            if str(o.get("outcome") or o.get("actual_outcome") or "").lower()
            in ("1", "true", "win", "yes", "correct", "1.0")
        )
        return wins / len(obs_list) if obs_list else 0.5

    sorted_obs = sorted(observations, key=lambda o: str(o.get("ts") or o.get("created_at") or ""))
    historic = sorted_obs[:-window]
    recent   = sorted_obs[-window:]

    historic_wr = wr(historic)
    recent_wr   = wr(recent)

    return abs(recent_wr - historic_wr) > 0.08  # 8pp drift threshold


def compute_calibration(
    bot_id: str,
    observations: list[dict[str, Any]],
) -> CalibrationResult:
    """
    Compute Brier calibration and edge statistics for a bot.
    Each observation must have: confidence/predicted_probability, outcome/actual_outcome.
    """
    valid = []
    for obs in observations:
        pred = obs.get("confidence") or obs.get("predicted_probability")
        outcome = obs.get("outcome") or obs.get("actual_outcome")
        if pred is None or outcome is None:
            continue
        p = float(pred)
        o_str = str(outcome).lower()
        o = 1.0 if o_str in ("1", "true", "win", "yes", "correct", "1.0") else 0.0
        if 0.0 <= p <= 1.0:
            valid.append((p, o))

    if not valid:
        return CalibrationResult(
            bot_id=bot_id,
            n=0,
            brier_score=0.25,
            mean_predicted=0.5,
            observed_win_rate=0.0,
            calibration_error=0.5,
            edge_bps=0.0,
            is_calibrated=False,
            is_positive_ev=False,
            drift_detected=False,
            recommendation="No calibration data available. Run more paper trades.",
        )

    brier = sum((p - o) ** 2 for p, o in valid) / len(valid)
    mean_pred = sum(p for p, _ in valid) / len(valid)
    obs_wr    = sum(o for _, o in valid) / len(valid)
    cal_err   = abs(mean_pred - obs_wr)
    edge_bps  = (obs_wr - 0.5) * 10000

    is_cal = brier < 0.20 and cal_err < 0.05 and len(valid) >= 30
    is_pos_ev = edge_bps > 0
    drift = _detect_drift(observations)
    cal_curve = _bin_calibration(observations)

    if brier > 0.25:
        rec = "Model is worse than random. Predicted probabilities are unreliable."
    elif not is_cal and len(valid) < 30:
        rec = f"Only {len(valid)} observations — need 30+ for calibration assessment."
    elif cal_err > 0.10:
        rec = f"High calibration error ({cal_err:.2%}): model predicts {mean_pred:.2%} but observes {obs_wr:.2%}."
    elif drift:
        rec = "Drift detected: recent win rate diverging from historic. Review signal quality."
    elif is_pos_ev and is_cal:
        rec = f"Positive edge ({edge_bps:.0f}bps) with well-calibrated predictions. Strategy viable."
    elif is_pos_ev:
        rec = f"Positive edge ({edge_bps:.0f}bps) but more data needed for calibration confidence."
    else:
        rec = f"Negative edge ({edge_bps:.0f}bps). Strategy not profitable at current win rate."

    return CalibrationResult(
        bot_id=bot_id,
        n=len(valid),
        brier_score=round(brier, 4),
        mean_predicted=round(mean_pred, 4),
        observed_win_rate=round(obs_wr, 4),
        calibration_error=round(cal_err, 4),
        edge_bps=round(edge_bps, 1),
        is_calibrated=is_cal,
        is_positive_ev=is_pos_ev,
        drift_detected=drift,
        calibration_curve=cal_curve,
        recommendation=rec,
    )


def calibration_to_dict(result: CalibrationResult) -> dict[str, Any]:
    return {
        "bot_id": result.bot_id,
        "n": result.n,
        "brier_score": result.brier_score,
        "mean_predicted": result.mean_predicted,
        "observed_win_rate": result.observed_win_rate,
        "calibration_error": result.calibration_error,
        "edge_bps": result.edge_bps,
        "is_calibrated": result.is_calibrated,
        "is_positive_ev": result.is_positive_ev,
        "drift_detected": result.drift_detected,
        "calibration_curve": result.calibration_curve,
        "recommendation": result.recommendation,
    }
