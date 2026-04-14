from __future__ import annotations

from collections import Counter
from statistics import mean
from typing import Any

from services.calibration import summarize_bot as summarize_calibration


def _latest_signal_rows(db: Any, bot_id: str | None = None, limit: int = 200) -> list[dict]:
    if db is None or not hasattr(db, "get_research_signals"):
        return []
    return db.get_research_signals(bot_id=bot_id, limit=limit)


def _performance_class(signals: list[dict]) -> tuple[str, float]:
    if len(signals) < 5:
        return "no_data", 0.0
    if len(signals) < 30:
        return "insufficient_data", min(0.3, len(signals) / 100)
    truth_labels = {str((signal.get("payload") or {}).get("platform_truth_label", signal.get("mode", ""))).upper() for signal in signals}
    if any(label in {"DELAYED", "TRIAL", "SCRAMBLED DATA"} for label in truth_labels):
        return "delayed_artifact", 0.45
    confidences = [float(signal.get("confidence", 0) or 0) for signal in signals]
    avg_confidence = mean(confidences) if confidences else 0.0
    signal_taken_ratio = sum(1 for signal in signals if (signal.get("payload") or {}).get("signal_taken")) / max(1, len(signals))
    if avg_confidence >= 0.62 and signal_taken_ratio >= 0.35:
        return "edge", min(0.92, 0.45 + avg_confidence / 2)
    if avg_confidence <= 0.45:
        return "variance", 0.55
    return "mixed", 0.6


def _factor_summary(signals: list[dict]) -> tuple[dict[str, float], dict[str, int]]:
    factors: Counter[str] = Counter()
    skip_reasons: Counter[str] = Counter()
    for signal in signals:
        payload = signal.get("payload") or {}
        factor_contributions = payload.get("factor_contributions") or {}
        for key, value in factor_contributions.items():
            try:
                factors[key] += float(value)
            except Exception:
                continue
        skip_reason = payload.get("skip_reason") or {}
        if isinstance(skip_reason, dict):
            reason_key = skip_reason.get("code") or skip_reason.get("reason")
            if reason_key:
                skip_reasons[str(reason_key)] += 1
        degraded_reason = signal.get("degraded_reason")
        if degraded_reason:
            skip_reasons[str(degraded_reason)] += 1
    return dict(factors), dict(skip_reasons)


def _truth_mode_counts(signals: list[dict]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for signal in signals:
        payload = signal.get("payload") or {}
        label = str(payload.get("platform_truth_label", signal.get("mode", "UNKNOWN")) or "UNKNOWN").upper()
        counts[label] += 1
    return dict(counts)


def _sorted_factor_keys(factors: dict[str, float], limit: int = 3) -> list[str]:
    return [key for key, _ in sorted(factors.items(), key=lambda item: abs(item[1]), reverse=True)[:limit]]


def _build_findings(
    *,
    performance_class: str,
    confidence: float,
    signal_count: int,
    truth_counts: dict[str, int],
    factors: dict[str, float],
    skip_reasons: dict[str, int],
) -> tuple[str, str, list[str], list[str], list[str], list[str]]:
    top_factors = _sorted_factor_keys(factors, limit=3)
    dominant_truth = max(truth_counts.items(), key=lambda item: item[1])[0] if truth_counts else "UNKNOWN"
    strongest_skip = max(skip_reasons.items(), key=lambda item: item[1])[0] if skip_reasons else ""

    strengths: list[str] = []
    weaknesses: list[str] = []
    blockers: list[str] = []
    suggestions: list[str] = []

    if performance_class == "edge":
        strengths.append("Confidence and take-rate are high enough to suggest repeatable edge under current assumptions.")
    elif performance_class == "variance":
        weaknesses.append("Signal quality is too close to noise to make a durable edge claim.")
    elif performance_class == "insufficient_data":
        weaknesses.append("The evidence window is still too small for strong conclusions.")
    elif performance_class == "delayed_artifact":
        blockers.append("Delayed or trial data is dominating the sample, so execution realism is capped.")
    elif performance_class == "mixed":
        weaknesses.append("Some factors look useful, but the overall signal is inconsistent across the current sample.")

    if dominant_truth in {"DELAYED", "TRIAL", "SCRAMBLED DATA", "WATCHLIST ONLY", "PUBLIC DATA ONLY"}:
        blockers.append(f"Most evidence comes from {dominant_truth}, which lowers real-world execution trust.")
    if top_factors:
        strengths.append(f"Most influential factors so far: {', '.join(top_factors)}.")
    else:
        suggestions.append("Persist factor contributions on every signal so attribution is evidence-based.")
    if strongest_skip:
        blockers.append(f"Most common skip/degrade reason: {strongest_skip}.")
    if signal_count < 30:
        suggestions.append("Accumulate at least 30 high-quality signals before treating diagnostics as directional.")
    if confidence < 0.5:
        suggestions.append("Lower size or move the bot into a stricter research/watchlist posture until confidence improves.")
    if not blockers and performance_class == "edge":
        strengths.append("No dominant platform-mode blocker is overwhelming the current evidence window.")

    why_winning = (
        "Signal selection is outperforming a random baseline and the strongest factors are contributing consistently."
        if performance_class == "edge"
        else "There is not enough clean evidence to say this bot is winning for repeatable reasons yet."
    )
    why_losing = (
        "Weak confidence, degraded truth modes, or repeated skip conditions are preventing the edge from compounding."
        if blockers or performance_class in {"variance", "mixed", "insufficient_data", "delayed_artifact"}
        else "Losses look contained, but execution realism is still limited."
    )
    return why_winning, why_losing, strengths, weaknesses, blockers, suggestions


def get_bot_diagnostic(rm: Any = None, db: Any = None, bot_id: str = "") -> dict[str, Any]:
    signals = _latest_signal_rows(db, bot_id=bot_id, limit=200)
    if not signals:
        return {
            "bot_id": bot_id,
            "state": "no_data",
            "message": "No signal history is available for this bot yet.",
            "truth_label": "NO DATA",
            "realized_pnl": None,
        }
    performance_class, confidence = _performance_class(signals)
    if performance_class == "no_data":
        return {
            "bot_id": bot_id,
            "state": "no_data",
            "message": "Only a small amount of signal history is available; diagnostics are not directional yet.",
            "truth_label": "NO DATA",
            "signal_count": len(signals),
            "last_signals": signals[:20],
            "realized_pnl": None,
        }
    factor_contributions, skip_reasons = _factor_summary(signals)
    truth_counts = _truth_mode_counts(signals)
    why_winning, why_losing, strengths, weaknesses, blockers, suggestions = _build_findings(
        performance_class=performance_class,
        confidence=confidence,
        signal_count=len(signals),
        truth_counts=truth_counts,
        factors=factor_contributions,
        skip_reasons=skip_reasons,
    )
    diag = {
        "bot_id": bot_id,
        "state": "ready",
        "performance_class": performance_class,
        "confidence": round(confidence, 4),
        "signal_count": len(signals),
        "taken_count": sum(1 for signal in signals if (signal.get("payload") or {}).get("signal_taken")),
        "skip_count": sum(skip_reasons.values()),
        "factor_summary": factor_contributions,
        "skip_reasons": skip_reasons,
        "truth_mode_counts": truth_counts,
        "why_winning": why_winning,
        "why_losing": why_losing,
        "strengths": strengths,
        "weaknesses": weaknesses,
        "blockers": blockers,
        "suggestions": suggestions,
        "last_signals": signals[:20],
        "truth_label": "RESEARCH DIAGNOSTIC",
        "realized_pnl": None,
    }
    if db is not None:
        diag["calibration"] = summarize_calibration(db, bot_id)
        if hasattr(db, "get_backtest_runs"):
            diag["backtests"] = db.get_backtest_runs(bot_id=bot_id, limit=3)
    if rm is not None and hasattr(rm, "diagnostic_snapshot"):
        diag["runtime"] = rm.diagnostic_snapshot()
    elif db is not None and hasattr(db, "get_bot_runtime_state"):
        diag["runtime"] = db.get_bot_runtime_state(bot_id)
    if db and hasattr(db, "save_bot_diagnostic"):
        db.save_bot_diagnostic(
            bot_id,
            diag["signal_count"],
            diag["taken_count"],
            skip_reasons,
            factor_contributions,
            diag,
        )
    return diag


def get_all_diagnostics(rms: dict[str, Any], db: Any = None) -> dict[str, Any]:
    bot_ids = set(rms.keys())
    if db and hasattr(db, "get_bot_catalog"):
        bot_ids.update(row["bot_id"] for row in db.get_bot_catalog())
    bots = {
        bot_id: get_bot_diagnostic(rm=rms.get(bot_id), db=db, bot_id=bot_id)
        for bot_id in sorted(bot_ids)
    }
    return {
        "bots": bots,
        "bot_count": len(bots),
        "truth_label": "RESEARCH DIAGNOSTIC",
        "realized_pnl": None,
    }


def get_strategy_diagnostics(db: Any, limit: int = 50) -> dict[str, Any]:
    signals = _latest_signal_rows(db, limit=500)
    grouped: dict[str, list[dict]] = {}
    for signal in signals:
        payload = signal.get("payload") or {}
        strategy_id = payload.get("strategy_id") or signal.get("signal_type") or signal.get("bot_id")
        grouped.setdefault(str(strategy_id), []).append(signal)
    strategies = []
    for strategy_id, rows in grouped.items():
        performance_class, confidence = _performance_class(rows)
        factors, skip_reasons = _factor_summary(rows)
        truth_counts = _truth_mode_counts(rows)
        why_winning, why_losing, strengths, weaknesses, blockers, suggestions = _build_findings(
            performance_class=performance_class,
            confidence=confidence,
            signal_count=len(rows),
            truth_counts=truth_counts,
            factors=factors,
            skip_reasons=skip_reasons,
        )
        strategies.append({
            "strategy_id": strategy_id,
            "performance_class": performance_class,
            "confidence": round(confidence, 4),
            "signal_count": len(rows),
            "why_winning": why_winning,
            "why_losing": why_losing,
            "strengths": strengths,
            "weaknesses": weaknesses,
            "blockers": blockers,
            "suggestions": suggestions,
            "factor_summary": factors,
            "skip_reasons": skip_reasons,
            "truth_mode_counts": truth_counts,
            "truth_label": "RESEARCH DIAGNOSTIC",
            "realized_pnl": None,
        })
        if db and hasattr(db, "save_strategy_diagnostic"):
            db.save_strategy_diagnostic(strategy_id, "snapshot", why_winning or why_losing, strategies[-1])
    strategies.sort(key=lambda row: (row["confidence"], row["signal_count"]), reverse=True)
    return {"strategies": strategies[:limit], "truth_label": "RESEARCH DIAGNOSTIC", "realized_pnl": None}


def analyze_signals(db: Any, bot_id: str | None = None, limit: int = 100) -> dict[str, Any]:
    signals = _latest_signal_rows(db, bot_id=bot_id, limit=limit)
    if not signals:
        return {"state": "no_data", "signals": [], "truth_label": "NO DATA", "realized_pnl": None}
    modes = Counter(str(signal.get("mode", "UNKNOWN")) for signal in signals)
    factors, skip_reasons = _factor_summary(signals)
    confidences = [float(signal.get("confidence", 0) or 0) for signal in signals]
    return {
        "signals": signals[:20],
        "count": len(signals),
        "modes": dict(modes),
        "confidence_avg": round(mean(confidences), 4) if confidences else None,
        "factor_summary": factors,
        "skip_reasons": skip_reasons,
        "truth_label": "RESEARCH SIGNALS",
        "realized_pnl": None,
    }


def analyze_circuit_breakers(db: Any, bot_id: str | None = None) -> dict[str, Any]:
    if db is None or not hasattr(db, "get_circuit_breakers"):
        return {"state": "no_data", "recent": []}
    rows = db.get_circuit_breakers(bot_id=bot_id, limit=100)
    reasons = Counter(row.get("reason", "unknown") for row in rows)
    return {
        "count": len(rows),
        "reason_distribution": dict(reasons),
        "recent": rows[:20],
        "total_pause_s": round(sum(float(row.get("duration_s", 0) or 0) for row in rows), 2),
    }


def get_platform_diagnostics(db: Any, registry: Any = None) -> dict[str, Any]:
    platforms: list[dict] = []
    health_rows = db.get_platform_health(limit=100) if db and hasattr(db, "get_platform_health") else []
    credential_rows = {}
    if db and hasattr(db, "get_credential_health"):
        for row in db.get_credential_health():
            credential_rows[row["platform"]] = row
    quota_rows = {}
    from services.quota_budgeter import get_budget

    budget = get_budget()
    for row in health_rows:
        platform = row["platform"]
        quota_rows[platform] = budget.status(platform)
        platforms.append({
            "platform": platform,
            "status": row.get("status", "unknown"),
            "mode": row.get("mode", "UNKNOWN"),
            "credential_state": (credential_rows.get(platform) or {}).get("state", "unchecked"),
            "quota_state": quota_rows[platform],
            "degraded_reason": row.get("degraded_reason", ""),
            "truth_label": (row.get("payload") or {}).get("truth_labels", {}).get("data_truth_label", row.get("mode", "UNKNOWN")),
        })
    return {"platforms": platforms, "truth_label": "PLATFORM DIAGNOSTIC"}


def get_platform_diagnostic(platform: str, db: Any, registry: Any = None) -> dict[str, Any]:
    summary = get_platform_diagnostics(db, registry=registry)
    for row in summary["platforms"]:
        if row["platform"] == platform:
            row["auth_events"] = db.get_auth_health_events(platform=platform, limit=20) if hasattr(db, "get_auth_health_events") else []
            row["failure_events"] = db.get_failure_events(source=platform, limit=20) if hasattr(db, "get_failure_events") else []
            return row
    return {"platform": platform, "state": "no_data", "message": "No platform diagnostics recorded yet."}
