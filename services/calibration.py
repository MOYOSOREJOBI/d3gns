from __future__ import annotations

from statistics import mean
from typing import Any


def _coerce_probability(value: Any) -> float | None:
    try:
        prob = float(value)
    except Exception:
        return None
    if prob > 1:
        prob /= 100.0
    return max(0.0, min(1.0, prob))


def _coerce_actual(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    try:
        actual = float(value)
    except Exception:
        return None
    return 1.0 if actual >= 1 else 0.0 if actual <= 0 else actual


def record_signal_observation(db: Any, signal: dict[str, Any]) -> int | None:
    if db is None or not hasattr(db, "save_calibration_observation"):
        return None
    data = signal.get("data") or {}
    probability = _coerce_probability(
        signal.get("predicted_probability", signal.get("confidence", data.get("predicted_probability")))
    )
    if probability is None:
        return None
    actual = _coerce_actual(
        signal.get("actual_outcome", data.get("actual_outcome", signal.get("outcome")))
    )
    return db.save_calibration_observation(
        bot_id=signal.get("bot_id", ""),
        signal_type=signal.get("signal_type", ""),
        predicted_probability=probability,
        actual_outcome=actual,
        truth_label=str(signal.get("platform_truth_label", signal.get("mode", "RESEARCH"))),
        payload={
            "title": signal.get("title", ""),
            "summary": signal.get("summary", ""),
            "mode": signal.get("mode", ""),
            "degraded_reason": signal.get("degraded_reason", ""),
            "data": data,
        },
    )


def _brier_score(rows: list[dict[str, Any]]) -> float | None:
    complete = [row for row in rows if row.get("actual_outcome") is not None]
    if not complete:
        return None
    return mean(
        (float(row.get("predicted_probability", 0) or 0) - float(row.get("actual_outcome", 0) or 0)) ** 2
        for row in complete
    )


def _bucketize(rows: list[dict[str, Any]], buckets: int = 10) -> list[dict[str, Any]]:
    complete = [row for row in rows if row.get("actual_outcome") is not None]
    if not complete:
        return []
    bucket_rows: list[list[dict[str, Any]]] = [[] for _ in range(buckets)]
    for row in complete:
        prob = max(0.0, min(0.9999, float(row.get("predicted_probability", 0) or 0)))
        index = min(buckets - 1, int(prob * buckets))
        bucket_rows[index].append(row)
    result = []
    for idx, items in enumerate(bucket_rows):
        if not items:
            continue
        predicted = mean(float(item.get("predicted_probability", 0) or 0) for item in items)
        actual = mean(float(item.get("actual_outcome", 0) or 0) for item in items)
        result.append(
            {
                "bucket": idx,
                "predicted": round(predicted, 4),
                "actual": round(actual, 4),
                "count": len(items),
            }
        )
    return result


def summarize_bot(db: Any, bot_id: str, limit: int = 500) -> dict[str, Any]:
    rows = db.get_calibration_observations(bot_id=bot_id, limit=limit) if db and hasattr(db, "get_calibration_observations") else []
    complete = [row for row in rows if row.get("actual_outcome") is not None]
    if len(complete) < 10:
        return {
            "bot_id": bot_id,
            "state": "no_data",
            "sample_size": len(complete),
            "message": "Not enough realized outcomes to score calibration yet.",
            "truth_label": "CALIBRATION PENDING",
            "realized_pnl": None,
        }
    recent = complete[:100]
    historical_brier = _brier_score(complete)
    recent_brier = _brier_score(recent)
    avg_pred = mean(float(row.get("predicted_probability", 0) or 0) for row in recent)
    actual_freq = mean(float(row.get("actual_outcome", 0) or 0) for row in recent)
    drift = bool(
        historical_brier is not None
        and recent_brier is not None
        and recent_brier > historical_brier + 0.05
    )
    overconfidence_pct = max(0.0, (avg_pred - actual_freq) * 100)
    underconfidence_pct = max(0.0, (actual_freq - avg_pred) * 100)
    return {
        "bot_id": bot_id,
        "state": "ready",
        "sample_size": len(complete),
        "historical_brier": round(historical_brier or 0.0, 4),
        "recent_brier": round(recent_brier or 0.0, 4),
        "avg_predicted": round(avg_pred, 4),
        "actual_frequency": round(actual_freq, 4),
        "drift_flag": drift,
        "overconfidence_pct": round(overconfidence_pct, 2),
        "underconfidence_pct": round(underconfidence_pct, 2),
        "message": (
            f"{bot_id} is overconfident by {overconfidence_pct:.1f}%."
            if overconfidence_pct >= 2
            else f"{bot_id} is underconfident by {underconfidence_pct:.1f}%."
            if underconfidence_pct >= 2
            else f"{bot_id} is reasonably calibrated."
        ),
        "points": _bucketize(complete),
        "truth_label": "CALIBRATION ANALYSIS",
        "realized_pnl": None,
    }


def summarize_all(db: Any, limit: int = 500) -> dict[str, Any]:
    bot_ids = set()
    if db and hasattr(db, "get_bot_catalog"):
        bot_ids.update(row["bot_id"] for row in db.get_bot_catalog())
    rows = [summarize_bot(db, bot_id, limit=limit) for bot_id in sorted(bot_ids)]
    return {"bots": rows, "truth_label": "CALIBRATION ANALYSIS", "realized_pnl": None}
