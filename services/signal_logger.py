from __future__ import annotations

from typing import Any

from services.calibration import record_signal_observation


def normalize_signal(signal: dict[str, Any]) -> dict[str, Any]:
    payload = dict(signal or {})
    payload.setdefault("factor_contributions", {})
    payload.setdefault("skip_reason", {})
    payload.setdefault("platform_truth_label", payload.get("mode", "RESEARCH"))
    payload.setdefault("reason_codes", [])
    return payload


def persist_signal(db_module: Any, signal: dict[str, Any]) -> int | None:
    payload = normalize_signal(signal)
    row_id = db_module.save_research_signal(
        bot_id=payload.get("bot_id", ""),
        platform=payload.get("platform", ""),
        mode=payload.get("mode", ""),
        signal_type=payload.get("signal_type", ""),
        title=payload.get("title", ""),
        summary=payload.get("summary", ""),
        confidence=payload.get("confidence"),
        degraded_reason=payload.get("degraded_reason", ""),
        data=payload,
    )
    try:
        record_signal_observation(db_module, payload)
    except Exception:
        pass
    return row_id


def load_signals(db_module: Any, bot_id: str | None = None, limit: int = 50):
    return db_module.get_research_signals(bot_id=bot_id, limit=limit)
