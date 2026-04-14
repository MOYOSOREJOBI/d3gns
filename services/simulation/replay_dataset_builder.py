from __future__ import annotations

import time
from collections import Counter
from typing import Any

import config as _cfg


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalise_calibration_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for row in rows or []:
        actual = row.get("actual_outcome")
        if actual is None:
            continue
        probability = _as_float(row.get("predicted_probability", row.get("confidence", 0.5)), 0.5)
        payload = row.get("payload") or {}
        payout_mult = _as_float(payload.get("odds_multiplier", payload.get("payout_mult", 2.0)), 2.0)
        records.append(
            {
                "source": "calibration",
                "ts": row.get("ts"),
                "probability": max(0.001, min(0.999, probability)),
                "outcome": 1.0 if float(actual) >= 1 else 0.0,
                "payout_mult": max(1.01, payout_mult),
                "payload": payload,
            }
        )
    return records


def _normalise_trade_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for row in rows or []:
        amount = _as_float(row.get("amount"), 0.0)
        if amount <= 0:
            continue
        won = bool(row.get("won"))
        net = _as_float(row.get("net"), 0.0)
        payout_mult = 1.0 + max(0.0, net) / amount if won else 2.0
        records.append(
            {
                "source": "trades",
                "ts": row.get("ts"),
                "probability": 0.55 if won else 0.45,
                "outcome": 1.0 if won else 0.0,
                "payout_mult": max(1.01, payout_mult),
                "payload": {"amount": amount, "net": net},
            }
        )
    return records


def _normalise_odds_snapshot_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for row in rows or []:
        payload = row.get("payload") or {}
        outcome = payload.get("outcome_result", payload.get("outcome"))
        if outcome is None:
            continue
        probability = _as_float(
            payload.get("price", payload.get("probability", payload.get("implied_probability", 0.5))),
            0.5,
        )
        decimal_odds = _as_float(payload.get("decimal_odds", 0), 0)
        if decimal_odds <= 1.0:
            probability = max(0.01, min(0.99, probability))
            decimal_odds = 1.0 / probability
        records.append(
            {
                "source": "odds_snapshot",
                "ts": row.get("ts"),
                "probability": max(0.001, min(0.999, probability)),
                "outcome": 1.0 if str(outcome).lower() in {"1", "true", "win", "won", "yes"} else 0.0,
                "payout_mult": max(1.01, decimal_odds),
                "payload": payload,
            }
        )
    return records


def build_replay_dataset(
    db_module: Any,
    *,
    bot_id: str | None = None,
    platform: str | None = None,
    market_id: str | None = None,
    limit: int = 500,
    persist: bool = True,
) -> dict[str, Any]:
    calibration_rows = []
    if hasattr(db_module, "get_calibration_observations") and bot_id:
        calibration_rows = db_module.get_calibration_observations(bot_id=bot_id, limit=limit) or []

    raw_trades = []
    if hasattr(db_module, "get_trades") and bot_id:
        fetched = db_module.get_trades(bot_id=bot_id, limit=limit)
        raw_trades = fetched[0] if isinstance(fetched, tuple) else (fetched or [])

    odds_rows = []
    if hasattr(db_module, "get_odds_snapshots"):
        odds_rows = db_module.get_odds_snapshots(platform=platform, event_id=market_id, limit=limit) or []

    records = (
        _normalise_calibration_rows(calibration_rows)
        + _normalise_trade_rows(raw_trades)
        + _normalise_odds_snapshot_rows(odds_rows)
    )
    records.sort(key=lambda row: str(row.get("ts") or ""))

    source_counts = Counter(record.get("source", "unknown") for record in records)
    snapshot_count = len(odds_rows)
    record_count = len(records)
    min_obs = int(getattr(_cfg, "REPLAY_MIN_OBSERVATIONS", 30) or 30)
    truth_label = "REPLAY DATA" if record_count >= min_obs else "REPLAY THIN"
    dataset_id = f"replay_{bot_id or platform or 'system'}_{int(time.time())}"
    payload = {
        "bot_id": bot_id,
        "platform": platform,
        "market_id": market_id,
        "record_count": record_count,
        "snapshot_count": snapshot_count,
        "source_counts": dict(source_counts),
        "records": records[-limit:],
    }

    if persist and hasattr(db_module, "save_replay_dataset"):
        first_ts = str(records[0].get("ts") or "") if records else ""
        last_ts = str(records[-1].get("ts") or "") if records else ""
        db_module.save_replay_dataset(
            dataset_id,
            platform or (bot_id or "system"),
            market_id or (bot_id or "all"),
            first_ts,
            last_ts,
            snapshot_count=snapshot_count,
            truth_label=truth_label,
            payload=payload,
        )

    return {
        "dataset_id": dataset_id,
        "bot_id": bot_id,
        "platform": platform,
        "market_id": market_id,
        "record_count": record_count,
        "snapshot_count": snapshot_count,
        "source_counts": dict(source_counts),
        "truth_label": truth_label,
        "records": records[-limit:],
        "decision_useful": record_count >= min_obs,
    }


def list_replay_datasets(db_module: Any, *, platform: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    if not hasattr(db_module, "get_replay_datasets"):
        return []
    return db_module.get_replay_datasets(platform=platform, limit=limit) or []

