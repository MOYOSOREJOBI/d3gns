from __future__ import annotations

from statistics import mean
from typing import Any

from services.simulator_engine import SimulationResult, result_to_dict


def report_from_result(result: SimulationResult) -> dict[str, Any]:
    strengths: list[str] = []
    weaknesses: list[str] = []
    caveats = list(result.caveats)
    fragilities: list[str] = []
    optimism_flags: list[str] = []
    unknowns: list[str] = []
    components = (result.assumptions or {}).get("replication_components", {})
    source_state = (result.assumptions or {}).get("source_state", "unknown")
    data_provenance = (result.assumptions or {}).get("data_provenance", "synthetic")
    decision_usefulness = (result.assumptions or {}).get("decision_usefulness", "exploratory")

    if result.hit_rate_estimate >= 0.55:
        strengths.append("Signal win-rate estimate stayed above a random baseline.")
    else:
        weaknesses.append("Signal hit-rate estimate is too close to variance to trust yet.")

    if result.max_drawdown_p50 <= 0.12:
        strengths.append("Median drawdown stayed contained under the current phase sizing.")
    else:
        weaknesses.append("Median drawdown is large enough to threaten operator confidence.")

    if result.replication_probability < 0.35:
        weaknesses.append("Replication probability is low because truth mode, credentials, or data quality are weak.")
        caveats.append("This report is exploratory rather than execution-adjacent.")
    elif result.replication_probability >= 0.65:
        strengths.append("Replication probability cleared the conservative confidence threshold.")
    else:
        fragilities.append("Replication probability is middling, so mild assumption drift could materially change results.")

    if result.mode == "quick":
        caveats.append("Quick mode skips some latency and quota realism assumptions.")
        optimism_flags.append("Quick mode compresses time and may understate operational drag.")
    if result.mode == "replay":
        strengths.append("Replay mode anchored the run to observed historical signals when available.")
    if float(components.get("quota_headroom", 1.0)) < 0.7:
        fragilities.append("Quota headroom is limited enough to reduce real-world repeatability.")
    if float(components.get("live_reconciliation", 1.0)) < 1.0:
        unknowns.append("Live reconciliation is incomplete, so settlement realism is capped.")
    if float(components.get("credential_state", 1.0)) < 1.0:
        unknowns.append("Credential validity is not fully established, so auth degradation risk remains.")
    if result.estimated_real_elapsed_s > 6 * 3600:
        fragilities.append("The median workflow would take many hours in real life, which increases exposure to drift and interruption.")

    why_winning = (
        "Wins are driven by a positive modeled edge and phase-aware sizing."
        if result.pnl_p50 >= 0
        else "There is no credible evidence of edge in the median path."
    )
    why_losing = (
        "Losses cluster when drawdown compounds faster than the modeled edge can recover."
        if result.max_drawdown_p50 > 0.1
        else "Losses appear limited, but sample uncertainty still dominates."
    )

    return {
        "run_id": result.run_id,
        "mode": result.mode,
        "replication_probability": result.replication_probability,
        "realism_score": result.realism_score,
        "truth_label": result.truth_label,
        "source_state": source_state,
        "data_provenance": data_provenance,
        "decision_usefulness": decision_usefulness,
        "execution_assumptions": (result.assumptions or {}).get("execution_assumptions", {}),
        "time_basis": (result.assumptions or {}).get("time_basis", {}),
        "pnl_p10": result.pnl_p10,
        "pnl_p50": result.pnl_p50,
        "pnl_p90": result.pnl_p90,
        "max_drawdown_p50": result.max_drawdown_p50,
        "hit_rate_estimate": result.hit_rate_estimate,
        "why_winning": why_winning,
        "why_losing": why_losing,
        "strengths": strengths,
        "weaknesses": weaknesses,
        "fragilities": fragilities,
        "optimism_flags": optimism_flags,
        "unknowns": unknowns,
        "caveats": caveats,
        "assumptions": result.assumptions,
        "terminal_state": result.terminal_state,
        "summary": result_to_dict(result),
        "realized_pnl": None,
    }


def build_report(results: list[SimulationResult], label: str = "") -> dict[str, Any]:
    if not results:
        return {
            "label": label,
            "truth_label": "SIMULATED — NOT REAL",
            "runs": [],
            "comparison": {"count": 0},
            "realized_pnl": None,
        }
    reports = [report_from_result(result) for result in results]
    rp = [report["replication_probability"] for report in reports]
    pnl = [report["pnl_p50"] for report in reports]
    dd = [report["max_drawdown_p50"] for report in reports]
    ranked = sorted(reports, key=lambda report: (report["replication_probability"], report["pnl_p50"]), reverse=True)
    return {
        "label": label,
        "truth_label": "COMPARATIVE SIMULATION — NOT REAL",
        "runs": ranked,
        "comparison": {
            "count": len(ranked),
            "replication_probability_mean": round(mean(rp), 4),
            "pnl_p50_mean": round(mean(pnl), 4),
            "max_drawdown_p50_mean": round(mean(dd), 4),
            "best_run_id": ranked[0]["run_id"],
        },
        "realized_pnl": None,
    }


def strategy_leaderboard_from_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        key = row.get("source_strategy") or row.get("params", {}).get("strategy_id") or row.get("params", {}).get("strategy") or "unknown"
        grouped.setdefault(key, []).append(row)
    leaderboard = []
    for strategy, items in grouped.items():
        payloads = [row.get("payload", {}) for row in items]
        pnl = [float(payload.get("pnl_p50", 0) or 0) for payload in payloads]
        rp = [float(payload.get("replication_probability", 0) or 0) for payload in payloads]
        leaderboard.append({
            "strategy": strategy,
            "n_runs": len(items),
            "mean_pnl_p50": round(mean(pnl), 4) if pnl else 0.0,
            "mean_replication_probability": round(mean(rp), 4) if rp else 0.0,
            "truth_label": "SIMULATED — NOT REAL",
        })
    leaderboard.sort(key=lambda row: (row["mean_replication_probability"], row["mean_pnl_p50"]), reverse=True)
    return leaderboard
