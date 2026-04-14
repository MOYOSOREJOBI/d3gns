"""
Proposal-driven realistic simulator.

Uses actual proposal/signal logs from the DB as the signal source instead of
synthetic random edge. Falls back to synthetic MC when no history is available.

Signal quality labeling:
  - Real proposal logs available → truth_label = "PAPER" (or bot's label)
  - Insufficient history (<30 signals) → truth_label = "SYNTHETIC" + warning
  - Mix of real + synthetic fill-ins → truth_label = "DELAYED_DATA"
"""
from __future__ import annotations

import math
import random
import uuid
from dataclasses import dataclass
from typing import Any

import config as _cfg
from services.risk_kernel import MAX_DRAWDOWN_PCT, REPEL_START_PCT, compute_dynamic_repel
from services.simulation.target_timing import compute_target_timing, format_timing_for_api

MIN_SIGNALS_FOR_REAL = int(getattr(_cfg, "REPLAY_MIN_OBSERVATIONS", 30) or 30)
BOOTSTRAP_FILL_THRESHOLD = 0.5  # if <50% real signals, label as SYNTHETIC


@dataclass
class ProposalReplayConfig:
    bot_id: str | None = None
    platform: str | None = None
    starting_capital: float = 100.0
    target_multiple: float = 3.0
    floor_pct: float = MAX_DRAWDOWN_PCT
    n_runs: int = 200
    fee_drag_bps: float = 50.0
    slippage_bps: float = 15.0
    fill_rate: float = 0.85
    seed: int | None = None
    horizon_hours: float | None = None   # if set, cap simulation at this many hours


def _load_signals(db_module: Any, cfg: ProposalReplayConfig) -> list[dict[str, Any]]:
    """Load real proposal/signal observations from the DB."""
    if db_module is None:
        return []

    signals: list[dict[str, Any]] = []

    # Try research_signals table (signal_logger persists here)
    if hasattr(db_module, "get_research_signals"):
        raw = db_module.get_research_signals(bot_id=cfg.bot_id, limit=500)
        signals.extend(raw or [])

    # Try signal_observations from calibration (Brier data)
    if hasattr(db_module, "get_signal_observations"):
        obs = db_module.get_signal_observations(bot_id=cfg.bot_id, limit=500)
        signals.extend(obs or [])

    return signals


def _extract_edge(signal: dict[str, Any]) -> tuple[float, float] | None:
    """
    Extract (confidence/probability, outcome) from a signal dict.
    Returns None if insufficient data.
    """
    # confidence is the predicted probability (0-1)
    conf = signal.get("confidence") or signal.get("predicted_probability")
    # outcome: 1.0 = win, 0.0 = loss
    outcome = signal.get("outcome") or signal.get("actual_outcome")
    if conf is None:
        return None
    conf_f = float(conf)
    if conf_f < 0.0 or conf_f > 1.0:
        return None
    if outcome is not None:
        out_f = 1.0 if str(outcome).lower() in ("1", "true", "win", "yes", "correct") else 0.0
        return (conf_f, out_f)
    # No outcome yet — treat confidence as implied win probability only
    return (conf_f, None)


def _signals_to_trades(
    signals: list[dict[str, Any]],
    cfg: ProposalReplayConfig,
    rng: random.Random,
) -> list[dict[str, Any]]:
    """
    Convert signal observations into a sequence of simulated trades.
    Uses real outcomes where available, synthetic fill for gaps.
    """
    trades = []
    real_count = 0
    for sig in signals:
        edge_data = _extract_edge(sig)
        if edge_data is None:
            continue
        conf, outcome = edge_data

        # Fill rate gate
        if rng.random() > cfg.fill_rate:
            continue

        # Compute edge after fees/slippage
        fee_pct = (cfg.fee_drag_bps + cfg.slippage_bps) / 10000.0
        raw_edge = conf - 0.5  # edge vs coin flip

        if outcome is not None:
            won = bool(outcome)
            real_count += 1
        else:
            # Synthetic: use confidence as win probability
            won = rng.random() < conf

        # PnL as fraction of bet size (1x payout minus 1x risk for binary)
        pnl_mult = (1.0 - fee_pct) if won else -(1.0 - fee_pct * 0.1)

        trades.append({
            "won": won,
            "pnl_mult": pnl_mult,
            "confidence": conf,
            "raw_edge": raw_edge,
            "is_real": outcome is not None,
        })

    return trades, real_count


def _run_replay(
    trades: list[dict[str, Any]],
    cfg: ProposalReplayConfig,
    rng: random.Random,
) -> dict[str, Any]:
    capital = cfg.starting_capital
    floor   = cfg.starting_capital * (1 - cfg.floor_pct)
    target  = cfg.starting_capital * cfg.target_multiple
    peak    = capital
    max_drawdown_pct = 0.0
    n_taken = 0
    n_won   = 0
    curve   = [capital]

    shuffled = list(trades)
    rng.shuffle(shuffled)

    for trade in shuffled:
        if capital <= floor or capital >= target:
            break

        repel = compute_dynamic_repel(capital, cfg.starting_capital, cfg.floor_pct, REPEL_START_PCT)
        if repel["zone"] == "FLOOR":
            break

        # Position size: 2% base * edge scalar * repel multiplier
        edge_scalar = max(0.5, min(2.0, 1.0 + trade["raw_edge"] * 5))
        size = capital * 0.02 * edge_scalar * repel["multiplier"]
        if size < 0.01:
            continue

        pnl = size * trade["pnl_mult"]
        capital = max(floor, capital + pnl)
        n_taken += 1
        if trade["won"]:
            n_won += 1
        if capital > peak:
            peak = capital
        if peak > 0:
            max_drawdown_pct = max(max_drawdown_pct, (peak - capital) / peak * 100.0)

        if n_taken % max(1, len(shuffled) // 60) == 0:
            curve.append(round(capital, 2))

    if not curve or curve[-1] != round(capital, 2):
        curve.append(round(capital, 2))

    return {
        "final": round(capital, 2),
        "hit_target": capital >= target,
        "hit_floor": capital <= floor + 0.01,
        "n_taken": n_taken,
        "n_won": n_won,
        "peak": round(peak, 2),
        "max_drawdown_pct": round(max_drawdown_pct, 2),
        "outcome_reason": (
            "target_hit" if capital >= target else
            "floor_hover" if capital <= floor + 0.01 else
            "signal_exhausted"
        ),
        "curve": curve,
    }


def run_proposal_replay(
    cfg: ProposalReplayConfig,
    db_module: Any = None,
) -> dict[str, Any]:
    """
    Run proposal-driven simulation.
    Returns statistics + percentile paths compatible with bankroll_paths.
    """
    rng = random.Random(cfg.seed)
    signals = _load_signals(db_module, cfg)

    # Need at least some signals to build trades
    if len(signals) < 5:
        # Fall back to synthetic
        from services.simulation.quick_mc import run_quick_mc, QuickMCConfig
        qcfg = QuickMCConfig(
            starting_capital=cfg.starting_capital,
            target_multiple=cfg.target_multiple,
            floor_pct=cfg.floor_pct,
            n_runs=cfg.n_runs,
            seed=cfg.seed,
        )
        result = run_quick_mc(qcfg)
        result["mode"] = "proposal_replay_fallback"
        result["warning"] = (
            f"Insufficient signal history ({len(signals)} signals found). "
            "Fell back to synthetic MC. Run more paper trades to unlock real replay."
        )
        result["signal_count"] = len(signals)
        return result

    trades, real_count = _signals_to_trades(signals, cfg, rng)
    real_fraction = real_count / max(len(trades), 1)

    # Determine truth label
    if real_fraction >= 0.8:
        truth_label = "PAPER"
        decision_useful = True
    elif real_fraction >= BOOTSTRAP_FILL_THRESHOLD:
        truth_label = "DELAYED_DATA"
        decision_useful = True
    else:
        truth_label = "SYNTHETIC"
        decision_useful = False

    if len(trades) < MIN_SIGNALS_FOR_REAL:
        truth_label = "SYNTHETIC"
        decision_useful = False

    # Run N replays with shuffled trade sequences
    results = [_run_replay(trades, cfg, rng) for _ in range(cfg.n_runs)]

    finals = sorted(r["final"] for r in results)
    hit_target = sum(1 for r in results if r["hit_target"])
    hit_floor  = sum(1 for r in results if r["hit_floor"])
    drawdowns = sorted(float(r.get("max_drawdown_pct", 0) or 0) for r in results)
    failure_breakdown = {
        "target_hit": sum(1 for r in results if r.get("outcome_reason") == "target_hit"),
        "floor_hover": sum(1 for r in results if r.get("outcome_reason") == "floor_hover"),
        "signal_exhausted": sum(1 for r in results if r.get("outcome_reason") == "signal_exhausted"),
    }

    def pct_val(p: float) -> float:
        return finals[int(p * len(finals))]

    p10, p50, p90 = pct_val(0.10), pct_val(0.50), pct_val(0.90)

    # Build percentile paths
    max_len = max(len(r["curve"]) for r in results) if results else 1
    padded  = [r["curve"] + [r["curve"][-1]] * (max_len - len(r["curve"])) for r in results]
    step    = max(1, max_len // 80)
    s_p10, s_p50, s_p90 = [], [], []
    for i in range(0, max_len, step):
        col = sorted(row[i] for row in padded)
        s_p10.append(round(col[int(0.10 * len(col))], 2))
        s_p50.append(round(col[int(0.50 * len(col))], 2))
        s_p90.append(round(col[int(0.90 * len(col))], 2))

    avg_bets = sum(r["n_taken"] for r in results) / max(len(results), 1)
    timing = compute_target_timing(
        [{"final": r["final"], "hit_target": r["hit_target"], "n_taken": r["n_taken"]} for r in results],
        starting_capital=cfg.starting_capital,
        target_multiple=cfg.target_multiple,
        bets_per_hour=max((cfg.horizon_hours or 24.0) / max(avg_bets, 1), 0.25),
    )
    assumption_summary = (
        f"Proposal replay from {len(signals)} recorded signals "
        f"({real_count} with observed outcomes), fill rate {cfg.fill_rate:.0%}, "
        f"fees {cfg.fee_drag_bps:.0f}bps, slippage {cfg.slippage_bps:.0f}bps."
    )
    warnings = []
    if truth_label == "SYNTHETIC":
        warnings.append("Replay is still evidence-light. Run more paper cycles to unlock stronger replay fidelity.")
    if real_fraction < BOOTSTRAP_FILL_THRESHOLD:
        warnings.append("A large fraction of trades were synthetically inferred from confidence, not observed outcomes.")
    warnings.append("ForceField floor remains a sizing repeller, not a live guarantee against venue behavior.")

    return {
        "sim_id": f"pr_{uuid.uuid4().hex[:8]}",
        "mode": "proposal_replay",
        "config": {
            "bot_id": cfg.bot_id,
            "starting_capital": cfg.starting_capital,
            "target": cfg.starting_capital * cfg.target_multiple,
            "floor": cfg.starting_capital * (1 - cfg.floor_pct),
            "n_runs": cfg.n_runs,
            "fee_drag_bps": cfg.fee_drag_bps,
            "slippage_bps": cfg.slippage_bps,
            "fill_rate": cfg.fill_rate,
        },
        "summary": {
            "p10": round(p10, 2),
            "p50": round(p50, 2),
            "p90": round(p90, 2),
            "hit_target_pct": round(hit_target / max(len(results), 1) * 100, 1),
            "hit_floor_pct":  round(hit_floor  / max(len(results), 1) * 100, 1),
            "avg_bets": round(avg_bets, 1),
            "roi_p50": round((p50 - cfg.starting_capital) / cfg.starting_capital * 100, 1),
            "expected_trade_count": round(avg_bets, 1),
            "expected_hold_hours": round((cfg.horizon_hours or 24.0) / max(avg_bets, 1), 3),
        },
        "paths": {
            "p10": s_p10,
            "p50": s_p50,
            "p90": s_p90,
        },
        "signal_count": len(signals),
        "real_signal_count": real_count,
        "real_fraction": round(real_fraction, 3),
        "targets": format_timing_for_api(timing),
        "max_drawdown_distribution": {
            "p10": round(drawdowns[int(0.10 * len(drawdowns))], 2),
            "p50": round(drawdowns[int(0.50 * len(drawdowns))], 2),
            "p90": round(drawdowns[int(0.90 * len(drawdowns))], 2),
            "worst": round(drawdowns[-1], 2),
        },
        "failure_breakdown": failure_breakdown,
        "assumptions": {
            "signal_count": len(signals),
            "real_signal_count": real_count,
            "real_fraction": round(real_fraction, 3),
            "fee_drag_bps": cfg.fee_drag_bps,
            "slippage_bps": cfg.slippage_bps,
            "fill_rate": cfg.fill_rate,
            "floor_pct": cfg.floor_pct,
        },
        "assumption_summary": assumption_summary,
        "truth_label": truth_label,
        "decision_useful": decision_useful,
        "replication_probability": round(min(0.95, real_fraction * 0.9 + 0.05), 3),
        "warnings": warnings,
    }
