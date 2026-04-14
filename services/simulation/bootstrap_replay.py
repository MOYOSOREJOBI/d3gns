"""
Historical bootstrap / replay simulation.

Accepts either:
  - A list of dicts with {price_or_prob, outcome, timestamp} rows (from CSV upload or DB)
  - Raw OHLCV candle data (venue-exported)
  - Odds snapshot data from OddsAPI

Resamples with replacement (bootstrap) across N runs to produce
percentile paths, target timing, and Brier score.
"""
from __future__ import annotations

import csv
import io
import math
import random
import uuid
from dataclasses import dataclass, field
from typing import Any

from services.risk_kernel import MAX_DRAWDOWN_PCT, REPEL_START_PCT, compute_dynamic_repel
from services.simulation.target_timing import compute_target_timing, format_timing_for_api


@dataclass
class BootstrapConfig:
    starting_capital: float = 100.0
    target_multiple:  float = 3.0
    floor_pct:        float = MAX_DRAWDOWN_PCT
    n_runs:           int   = 300
    fee_drag_bps:     float = 50.0
    fill_rate:        float = 0.85
    bets_per_hour:    float = 1.5
    seed:             int | None = None


@dataclass
class TradeRecord:
    probability: float   # predicted probability (0-1)
    outcome: float       # 1.0 = win, 0.0 = loss
    payout_mult: float   # e.g. 1.9 for binary NO-VIG, 2.0 for evens
    ts: str = ""

    @property
    def pnl_per_unit(self) -> float:
        """PnL per unit stake."""
        return (self.payout_mult - 1) if self.outcome >= 0.5 else -1.0


def parse_csv_trades(csv_text: str) -> list[TradeRecord]:
    """
    Parse a CSV into TradeRecord list.
    Expected columns (flexible): probability/confidence, outcome/result, payout/odds, ts/date
    """
    reader = csv.DictReader(io.StringIO(csv_text.strip()))
    records = []
    for row in reader:
        # Probability
        prob = None
        for k in ("probability", "confidence", "prob", "edge", "win_prob"):
            if k in row and row[k]:
                try:
                    prob = float(row[k])
                    break
                except (ValueError, TypeError):
                    pass
        if prob is None:
            continue
        if prob > 1.0:
            prob /= 100.0  # handle percentage form

        # Outcome
        outcome = None
        for k in ("outcome", "result", "won", "win"):
            if k in row and row[k] is not None:
                v = str(row[k]).lower().strip()
                if v in ("1", "true", "win", "won", "yes", "correct"):
                    outcome = 1.0
                elif v in ("0", "false", "loss", "lost", "no", "incorrect"):
                    outcome = 0.0
                break
        if outcome is None:
            continue

        # Payout multiple
        payout = 1.9
        for k in ("payout", "odds", "mult", "multiplier"):
            if k in row and row[k]:
                try:
                    raw = float(row[k])
                    # Detect decimal odds > 1 (e.g. 1.9) vs American (e.g. -110)
                    if raw > 1.0:
                        payout = raw
                    break
                except (ValueError, TypeError):
                    pass

        ts = row.get("ts") or row.get("date") or row.get("timestamp") or ""
        records.append(TradeRecord(probability=prob, outcome=outcome, payout_mult=payout, ts=ts))

    return records


def parse_odds_snapshot(rows: list[dict[str, Any]]) -> list[TradeRecord]:
    """
    Convert OddsAPI-style rows into TradeRecords.
    Expects rows with: price (0-1), outcome_result (0/1), market (h2h/spreads)
    """
    records = []
    for row in rows:
        prob = float(row.get("price") or row.get("probability") or 0.5)
        outcome_raw = row.get("outcome_result") or row.get("outcome")
        if outcome_raw is None:
            continue
        outcome = 1.0 if str(outcome_raw) in ("1", "true", "win") else 0.0
        # American to decimal odds if needed
        american = row.get("american_odds")
        if american:
            a = float(american)
            payout = (100 / abs(a) + 1) if a < 0 else (a / 100 + 1)
        else:
            payout = row.get("decimal_odds") or (1 / max(prob, 0.01))
            payout = float(payout)
        records.append(TradeRecord(probability=prob, outcome=outcome, payout_mult=payout))
    return records


def _run_bootstrap(
    records: list[TradeRecord],
    cfg: BootstrapConfig,
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

    fee_pct = cfg.fee_drag_bps / 10000.0

    # Bootstrap: sample with replacement from the records
    sample = [rng.choice(records) for _ in range(len(records) * 3)]

    for trade in sample:
        if capital <= floor or capital >= target:
            break

        if rng.random() > cfg.fill_rate:
            continue

        repel = compute_dynamic_repel(capital, cfg.starting_capital, cfg.floor_pct, REPEL_START_PCT)
        if repel["zone"] == "FLOOR":
            break

        size = capital * 0.025 * repel["multiplier"]
        if size < 0.01:
            continue

        pnl = size * trade.pnl_per_unit - size * fee_pct
        capital = max(floor, capital + pnl)
        n_taken += 1
        if trade.outcome >= 0.5:
            n_won += 1
        if capital > peak:
            peak = capital
        if peak > 0:
            max_drawdown_pct = max(max_drawdown_pct, (peak - capital) / peak * 100.0)

        if n_taken % max(1, len(sample) // 60) == 0:
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
            "sample_exhausted"
        ),
        "curve": curve,
    }


def _brier_score(records: list[TradeRecord]) -> float:
    if not records:
        return 1.0
    return sum((r.probability - r.outcome) ** 2 for r in records) / len(records)


def run_bootstrap_replay(
    records: list[TradeRecord],
    cfg: BootstrapConfig,
) -> dict[str, Any]:
    """
    Run bootstrap replay from a list of TradeRecords.
    Returns statistics + percentile paths.
    """
    if not records:
        return {
            "ok": False,
            "error": "no_records",
            "warning": "No trade records provided. Upload a CSV or connect to a data source.",
        }

    rng = random.Random(cfg.seed)
    brier = _brier_score(records)

    results = [_run_bootstrap(records, cfg, rng) for _ in range(cfg.n_runs)]
    finals  = sorted(r["final"] for r in results)
    hit_target = sum(1 for r in results if r["hit_target"])
    hit_floor  = sum(1 for r in results if r["hit_floor"])
    avg_bets = sum(r["n_taken"] for r in results) / max(len(results), 1)
    drawdowns = sorted(float(r.get("max_drawdown_pct", 0) or 0) for r in results)
    failure_breakdown = {
        "target_hit": sum(1 for r in results if r.get("outcome_reason") == "target_hit"),
        "floor_hover": sum(1 for r in results if r.get("outcome_reason") == "floor_hover"),
        "sample_exhausted": sum(1 for r in results if r.get("outcome_reason") == "sample_exhausted"),
    }

    def pv(p: float) -> float:
        return finals[int(p * len(finals))]

    p10, p50, p90 = pv(0.10), pv(0.50), pv(0.90)

    # Percentile paths
    max_len = max(len(r["curve"]) for r in results) if results else 1
    padded  = [r["curve"] + [r["curve"][-1]] * (max_len - len(r["curve"])) for r in results]
    step    = max(1, max_len // 80)
    s_p10, s_p50, s_p90 = [], [], []
    for i in range(0, max_len, step):
        col = sorted(row[i] for row in padded)
        s_p10.append(round(col[int(0.10 * len(col))], 2))
        s_p50.append(round(col[int(0.50 * len(col))], 2))
        s_p90.append(round(col[int(0.90 * len(col))], 2))

    # Brier score → replication quality
    replication = max(0.0, 1.0 - brier * 4)
    decision_useful = len(records) >= 30 and replication >= 0.55
    timing = compute_target_timing(
        [{"final": r["final"], "hit_target": r["hit_target"], "n_taken": r["n_taken"]} for r in results],
        starting_capital=cfg.starting_capital,
        target_multiple=cfg.target_multiple,
        bets_per_hour=cfg.bets_per_hour,
    )
    assumption_summary = (
        f"Bootstrap replay from {len(records)} records with {cfg.n_runs} resampled paths, "
        f"fill rate {cfg.fill_rate:.0%}, fee drag {cfg.fee_drag_bps:.0f}bps, "
        f"ForceField floor at ${cfg.starting_capital * (1 - cfg.floor_pct):.2f}."
    )
    warnings = [
        "Bootstrap replay samples historical records with replacement.",
        "Replay quality depends on record count, outcome fidelity, and venue similarity.",
    ]

    return {
        "sim_id": f"br_{uuid.uuid4().hex[:8]}",
        "mode": "bootstrap_replay",
        "config": {
            "starting_capital": cfg.starting_capital,
            "target": cfg.starting_capital * cfg.target_multiple,
            "floor": cfg.starting_capital * (1 - cfg.floor_pct),
            "n_runs": cfg.n_runs,
            "records": len(records),
            "bets_per_hour": cfg.bets_per_hour,
        },
        "summary": {
            "p10": round(p10, 2),
            "p50": round(p50, 2),
            "p90": round(p90, 2),
            "hit_target_pct": round(hit_target / max(len(results), 1) * 100, 1),
            "hit_floor_pct":  round(hit_floor  / max(len(results), 1) * 100, 1),
            "avg_bets": round(avg_bets, 1),
            "roi_p50": round((p50 - cfg.starting_capital) / cfg.starting_capital * 100, 1),
            "brier_score": round(brier, 4),
            "expected_trade_count": round(avg_bets, 1),
            "expected_hold_hours": round(len(records) / max(cfg.bets_per_hour, 0.001) / max(avg_bets, 1), 3),
        },
        "paths": {
            "p10": s_p10,
            "p50": s_p50,
            "p90": s_p90,
        },
        "targets": format_timing_for_api(timing),
        "max_drawdown_distribution": {
            "p10": round(drawdowns[int(0.10 * len(drawdowns))], 2),
            "p50": round(drawdowns[int(0.50 * len(drawdowns))], 2),
            "p90": round(drawdowns[int(0.90 * len(drawdowns))], 2),
            "worst": round(drawdowns[-1], 2),
        },
        "failure_breakdown": failure_breakdown,
        "assumptions": {
            "records": len(records),
            "fill_rate": cfg.fill_rate,
            "fee_drag_bps": cfg.fee_drag_bps,
            "bets_per_hour": cfg.bets_per_hour,
            "floor_pct": cfg.floor_pct,
        },
        "assumption_summary": assumption_summary,
        "truth_label": "DELAYED_DATA" if decision_useful else "SYNTHETIC",
        "decision_useful": decision_useful,
        "replication_probability": round(replication, 3),
        "warnings": warnings,
    }
