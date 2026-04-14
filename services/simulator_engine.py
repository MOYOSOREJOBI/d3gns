from __future__ import annotations

import random
import time
import uuid
from dataclasses import asdict, dataclass, field
from statistics import mean
from typing import Any

import config as _cfg


_TRUTH_MODE_SCORE = {
    "LIVE": 1.0,
    "DEMO": 0.7,
    "PAPER": 0.6,
    "DELAYED": 0.4,
    "TRIAL": 0.2,
    "SCRAMBLED": 0.1,
    "RESEARCH": 0.1,
    "WATCHLIST ONLY": 0.1,
    "PUBLIC DATA ONLY": 0.2,
    "LIVE DISABLED": 0.3,
}

_CREDENTIAL_SCORE = {
    "valid": 1.0,
    "validated": 1.0,
    "unchecked": 0.7,
    "present": 0.7,
    "partial": 0.4,
    "missing": 0.1,
    "not_configured": 0.1,
    "invalid": 0.0,
    "failed": 0.0,
}

_DATA_QUALITY_SCORE = {
    "live_reconciled": 1.0,
    "live": 1.0,
    "paper": 0.7,
    "public": 0.5,
    "delayed": 0.4,
    "trial": 0.2,
    "scrambled": 0.1,
    "research": 0.2,
}

_BASE_STRATEGY = {
    "dice": {"edge": 0.028, "volatility": 0.95, "actions": 1.2},
    "limbo": {"edge": 0.034, "volatility": 1.15, "actions": 1.1},
    "mines": {"edge": 0.031, "volatility": 1.35, "actions": 0.95},
    "polymarket": {"edge": 0.024, "volatility": 0.7, "actions": 0.65},
    "edge_scanner": {"edge": 0.018, "volatility": 0.5, "actions": 0.45},
    "btc_momentum": {"edge": 0.021, "volatility": 0.85, "actions": 0.7},
    "resolution_sniper": {"edge": 0.026, "volatility": 0.8, "actions": 0.55},
    "volume_spike": {"edge": 0.019, "volatility": 0.75, "actions": 0.5},
}

_PHASE_RISK = {
    "floor": 0.25,
    "ultra_safe": 0.35,
    "safe": 0.5,
    "careful": 0.7,
    "normal": 1.0,
    "aggressive": 1.25,
    "turbo": 1.5,
    "milestone": 0.9,
}


@dataclass
class SimulatorConfig:
    mode: str = "quick"
    bot_id: str = ""
    strategy_id: str = "dice"
    strategy: str = "dice"
    phase: str = "normal"
    run_count: int = 200
    n_rounds: int = 120
    bankroll: float = 100.0
    platform_truth_mode: str = "PAPER"
    credential_state: str = "missing"
    data_quality: str = "paper"
    quota_headroom_pct: float = 1.0
    latency_realism: bool = False
    has_historical_evidence: bool = False
    reconciliation_ok: bool = False
    initial_state: dict[str, Any] = field(default_factory=dict)
    signal_sequence: list[dict[str, Any]] = field(default_factory=list)
    params_override: dict[str, Any] = field(default_factory=dict)
    cadence_assumptions: dict[str, Any] = field(default_factory=dict)
    label: str = ""
    tags: list[str] = field(default_factory=list)
    seed: int | None = None

    @classmethod
    def from_request(cls, body: dict[str, Any]) -> "SimulatorConfig":
        return cls(
            mode=body.get("mode", body.get("simulation_mode", "quick")),
            bot_id=body.get("bot_id", ""),
            strategy_id=body.get("strategy_id", body.get("strategy", "dice")),
            strategy=body.get("strategy", body.get("strategy_id", "dice")),
            phase=body.get("phase", "normal"),
            run_count=int(body.get("run_count", body.get("runs", 200))),
            n_rounds=int(body.get("n_rounds", body.get("steps", 120))),
            bankroll=float(body.get("bankroll", body.get("starting_bankroll", 100.0))),
            platform_truth_mode=body.get("platform_truth_mode", "PAPER"),
            credential_state=body.get("credential_state", "missing"),
            data_quality=body.get("data_quality", "paper"),
            quota_headroom_pct=float(body.get("quota_headroom_pct", 1.0)),
            latency_realism=bool(body.get("latency_realism", False)),
            has_historical_evidence=bool(body.get("has_historical_evidence", False)),
            reconciliation_ok=bool(body.get("reconciliation_ok", False)),
            initial_state=body.get("initial_state") or {},
            signal_sequence=body.get("signal_sequence") or [],
            params_override=body.get("params_override") or body.get("params") or {},
            cadence_assumptions=body.get("cadence_assumptions") or {},
            label=body.get("label", ""),
            tags=body.get("tags") or [],
            seed=body.get("seed"),
        )


@dataclass
class SimulatorStep:
    step: int
    action: str
    outcome: str
    equity: float
    phase: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class SimulationResult:
    run_id: str
    mode: str
    config: SimulatorConfig
    replication_probability: float
    realism_score: float
    realism_label: str
    estimated_real_elapsed_s: float
    pnl_p10: float
    pnl_p50: float
    pnl_p90: float
    max_drawdown_p50: float
    hit_rate_estimate: float
    steps: list[SimulatorStep]
    terminal_state: dict[str, Any]
    assumptions: dict[str, Any]
    caveats: list[str]
    truth_label: str = "SIMULATED — NOT REAL"
    warning: str = "Simulation only. No result implies live profitability."
    realized_pnl: None = None

    @property
    def simulation_id(self) -> str:
        return self.run_id

    @property
    def n_rounds(self) -> int:
        return self.config.n_rounds

    @property
    def final_bankroll(self) -> float:
        return round(self.terminal_state.get("bankroll", self.config.bankroll), 4)

    @property
    def win_rate_pct(self) -> float:
        return round(self.hit_rate_estimate * 100, 2)

    @property
    def max_drawdown_pct(self) -> float:
        return round(self.max_drawdown_p50 * 100, 2)

    @property
    def roi_pct(self) -> float:
        start = max(self.config.bankroll, 1e-6)
        return round(((self.final_bankroll - start) / start) * 100, 2)

    @property
    def equity_curve(self) -> list[float]:
        return [round(step.equity, 4) for step in self.steps]


def _realism_label(score: float) -> str:
    if score >= 0.8:
        return "HIGH"
    if score >= 0.6:
        return "MODERATE"
    if score >= 0.4:
        return "LOW"
    return "VERY_LOW"


def _quantile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * q))))
    return float(ordered[index])


def _replication_probability(cfg: SimulatorConfig, evidence_count: int) -> tuple[float, dict[str, float]]:
    weights = getattr(_cfg, "SIMULATOR_REALISM_WEIGHTS", {})
    truth_score = _TRUTH_MODE_SCORE.get(cfg.platform_truth_mode.upper(), 0.2)
    credential_score = _CREDENTIAL_SCORE.get(cfg.credential_state.lower(), 0.2)
    data_score = _DATA_QUALITY_SCORE.get(cfg.data_quality.lower(), 0.2)
    if cfg.quota_headroom_pct >= 0.5:
        quota_score = 1.0
    elif cfg.quota_headroom_pct >= 0.1:
        quota_score = 0.7
    elif cfg.quota_headroom_pct > 0:
        quota_score = 0.4
    else:
        quota_score = 0.0
    latency_score = 1.0 if cfg.mode in {"realistic", "continuation", "replay"} or cfg.latency_realism else 0.5
    if evidence_count > 200:
        history_score = 1.0
    elif evidence_count > 50:
        history_score = 0.7
    elif evidence_count > 10:
        history_score = 0.4
    else:
        history_score = 0.2
    reconciliation_score = 1.0 if cfg.reconciliation_ok else 0.5
    components = {
        "platform_truth_mode": truth_score,
        "credential_state": credential_score,
        "data_quality": data_score,
        "quota_headroom": quota_score,
        "latency_realism": latency_score,
        "historical_evidence": history_score,
        "live_reconciliation": reconciliation_score,
    }
    total = 0.0
    for key, value in components.items():
        total += value * float(weights.get(key, 0))
    return round(max(0.0, min(1.0, total)), 4), components


def _resolve_strategy(cfg: SimulatorConfig) -> dict[str, float]:
    base = _BASE_STRATEGY.get(cfg.strategy_id or cfg.strategy or "dice", _BASE_STRATEGY["dice"]).copy()
    phase = str(cfg.phase or "normal").lower()
    bet_pcts = getattr(_cfg, "BET_PCT_BY_PHASE", {})
    if phase in bet_pcts:
        base["bet_pct"] = float(bet_pcts[phase])
    if cfg.initial_state:
        phase = str(cfg.initial_state.get("phase", phase) or phase).lower()
        if phase in bet_pcts:
            base["bet_pct"] = float(bet_pcts[phase])
        recent_pnl = cfg.initial_state.get("recent_pnl") or []
        if recent_pnl:
            pnl_avg = sum(float(item or 0) for item in recent_pnl) / max(1, len(recent_pnl))
            base["edge"] = max(-0.04, min(0.08, float(base.get("edge", 0.02)) + pnl_avg / max(1.0, float(cfg.initial_state.get("start_amount", cfg.bankroll) or cfg.bankroll))))
    if cfg.signal_sequence:
        confidences = [float(signal.get("confidence", 0.5) or 0.5) for signal in cfg.signal_sequence]
        taken_ratio = sum(1 for signal in cfg.signal_sequence if (signal.get("payload") or {}).get("signal_taken")) / max(1, len(cfg.signal_sequence))
        confidence_avg = sum(confidences) / max(1, len(confidences))
        base["edge"] = max(-0.05, min(0.09, (confidence_avg - 0.5) * (0.25 + taken_ratio)))
        base["actions"] = max(0.2, min(4.0, len(cfg.signal_sequence) / max(1, cfg.n_rounds)))
    if (cfg.strategy_id or cfg.strategy) == "dice":
        chance_map = getattr(_cfg, "DICE_CHANCE", {})
        if phase in chance_map:
            # Convert chance % to a small modeled edge around 50%.
            base["edge"] = max(-0.04, min(0.08, (float(chance_map[phase]) / 100.0) - 0.5))
    base.setdefault("bet_pct", 0.01)
    base.update({k: v for k, v in cfg.params_override.items() if isinstance(v, (int, float))})
    return base


def _simulate_one_path(cfg: SimulatorConfig, rng: random.Random, strategy: dict[str, float], signal_sequence: list[dict[str, Any]]) -> tuple[list[SimulatorStep], float, float]:
    bankroll = float(cfg.initial_state.get("bankroll", cfg.bankroll))
    phase = str(cfg.initial_state.get("phase", cfg.phase))
    start_amount = float(cfg.initial_state.get("start_amount", cfg.bankroll) or cfg.bankroll)
    peak = bankroll
    max_drawdown = 0.0
    wins = 0
    steps: list[SimulatorStep] = []
    signal_count = len(signal_sequence)
    for step_idx in range(max(1, cfg.n_rounds)):
        phase_risk = _PHASE_RISK.get(phase, 1.0)
        base_edge = float(strategy.get("edge", 0.02))
        volatility = float(strategy.get("volatility", 1.0))
        if cfg.mode == "replay" and signal_count:
            sample = signal_sequence[min(step_idx, signal_count - 1)]
            confidence = float(sample.get("confidence", 0.5) or 0.5)
            sampled_edge = (confidence - 0.5) * 0.18
            base_edge = max(-0.08, min(0.08, sampled_edge))
        truth_penalty = _TRUTH_MODE_SCORE.get(cfg.platform_truth_mode.upper(), 0.2)
        data_penalty = _DATA_QUALITY_SCORE.get(cfg.data_quality.lower(), 0.2)
        effective_edge = base_edge * phase_risk * truth_penalty * data_penalty
        win_prob = max(0.08, min(0.92, 0.5 + effective_edge))
        configured_bet_pct = float(strategy.get("bet_pct", 0.01))
        wager_pct = max(0.001, min(0.08, configured_bet_pct * max(0.4, phase_risk)))
        wager = bankroll * wager_pct
        outcome = "win" if rng.random() < win_prob else "loss"
        slippage_pct = rng.uniform(0.005, 0.02) if cfg.mode in {"realistic", "continuation", "replay"} else 0.0
        partial_fill_pct = rng.uniform(0.65, 1.0) if cfg.mode in {"realistic", "continuation", "replay"} else 1.0
        effective_wager = wager * partial_fill_pct
        if outcome == "win":
            delta = effective_wager * (0.65 + effective_edge * 8)
            wins += 1
        else:
            delta = -effective_wager * (0.8 + (1 - truth_penalty) * 0.5)
        delta -= effective_wager * slippage_pct
        noise = rng.uniform(-0.3, 0.3) * effective_wager * (volatility - 0.3)
        bankroll = max(0.0, bankroll + delta + noise)
        peak = max(peak, bankroll)
        drawdown = 0.0 if peak <= 0 else max(0.0, (peak - bankroll) / peak)
        max_drawdown = max(max_drawdown, drawdown)
        if drawdown >= float(getattr(_cfg, "FLOOR_DRAWDOWN_PCT", 0.10)):
            phase = "floor"
        elif drawdown >= float(getattr(_cfg, "ULTRA_SAFE_DRAWDOWN", 0.07)):
            phase = "ultra_safe"
        elif drawdown >= float(getattr(_cfg, "SAFE_DRAWDOWN", 0.05)):
            phase = "safe"
        elif drawdown >= float(getattr(_cfg, "CAREFUL_DRAWDOWN", 0.03)):
            phase = "careful"
        elif bankroll > peak * 0.98 and outcome == "win":
            phase = "aggressive" if wins >= 3 else "normal"
        elif bankroll >= start_amount:
            phase = "normal"
        steps.append(
            SimulatorStep(
                step=step_idx,
                action="simulate_signal",
                outcome=outcome,
                equity=round(bankroll, 4),
                phase=phase,
                payload={
                    "wager": round(wager, 4),
                    "effective_wager": round(effective_wager, 4),
                    "effective_edge": round(effective_edge, 4),
                    "drawdown": round(drawdown, 4),
                    "slippage_pct": round(slippage_pct, 4),
                    "partial_fill_pct": round(partial_fill_pct, 4),
                },
            )
        )
        if bankroll <= 0:
            break
    return steps, bankroll, max_drawdown if steps else 0.0


def _estimated_real_elapsed_s(cfg: SimulatorConfig, avg_actions: float) -> float:
    cadence = cfg.cadence_assumptions or _cfg.VENUE_CADENCE_ASSUMPTIONS.get((cfg.strategy_id or cfg.strategy or "").lower(), {})
    request_latency_ms = float(cadence.get("request_latency_ms", 750))
    poll_interval_s = float(cadence.get("poll_interval_s", 30))
    quota_throttle_delay_s = float(cadence.get("quota_throttle_delay_s", 5))
    breaker_cooldown_s = float(cadence.get("breaker_cooldown_s", 60))
    quota_fraction = 0.15 if cfg.quota_headroom_pct < 0.2 else 0.05
    breaker_fraction = 0.1 if cfg.mode in {"continuation", "realistic"} else 0.03
    if cfg.mode == "quick":
        return float(max(1.0, cfg.run_count * avg_actions * 0.02))
    return float(
        cfg.run_count
        * avg_actions
        * (
            (request_latency_ms / 1000.0)
            + min(poll_interval_s, 10.0)
            + quota_fraction * quota_throttle_delay_s
            + breaker_fraction * breaker_cooldown_s
        )
    )


def _source_state(cfg: SimulatorConfig, effective_mode: str, signal_sequence: list[dict[str, Any]]) -> tuple[str, str]:
    if effective_mode == "replay" and signal_sequence:
        return "historical_replay", "historical"
    if effective_mode == "continuation" and cfg.initial_state:
        return "bot_snapshot", "partial"
    if cfg.initial_state and cfg.bot_id:
        return "bot_snapshot", "partial"
    if effective_mode == "quick":
        return "synthetic_fallback", "synthetic"
    return "model_only", "synthetic"


def _truth_label(cfg: SimulatorConfig, effective_mode: str, source_state: str, data_provenance: str) -> str:
    if effective_mode == "replay" and source_state == "historical_replay":
        return "HISTORICAL REPLAY — SIMULATED"
    if effective_mode == "continuation" and source_state == "bot_snapshot":
        return "BOT SNAPSHOT CONTINUATION — SIMULATED"
    if effective_mode == "quick":
        return "SYNTHETIC SANDBOX — EXPLORATORY"
    if data_provenance == "partial":
        return "PARTIAL STATE SIMULATION"
    return "SIMULATED WITH REALISM DISCOUNTS"


def simulate(cfg: SimulatorConfig) -> SimulationResult:
    started = time.perf_counter()
    rng = random.Random(cfg.seed if cfg.seed is not None else random.randint(1, 10_000_000))
    strategy = _resolve_strategy(cfg)
    signal_sequence = cfg.signal_sequence or []
    effective_mode = cfg.mode
    caveats: list[str] = []
    if cfg.mode == "replay" and len(signal_sequence) < 5:
        effective_mode = "realistic"
        caveats.append("Replay fell back to realistic mode because signal history was insufficient.")
    if cfg.mode == "continuation" and not cfg.initial_state:
        effective_mode = "realistic"
        caveats.append("Continuation state unavailable, so the run fell back to realistic mode.")

    representative_steps: list[SimulatorStep] = []
    finals: list[float] = []
    drawdowns: list[float] = []
    hit_rates: list[float] = []
    actual_runs = max(20, min(int(cfg.run_count), 500))
    for run_index in range(actual_runs):
        steps, final_bankroll, max_drawdown = _simulate_one_path(cfg, rng, strategy, signal_sequence)
        finals.append(final_bankroll - cfg.bankroll)
        drawdowns.append(max_drawdown)
        wins = sum(1 for step in steps if step.outcome == "win")
        hit_rates.append(wins / max(1, len(steps)))
        if run_index == 0:
            representative_steps = steps

    replication_probability, rp_components = _replication_probability(cfg, len(signal_sequence) if signal_sequence else (actual_runs if cfg.has_historical_evidence else 0))
    realism_score = replication_probability
    source_state, data_provenance = _source_state(cfg, effective_mode, signal_sequence)
    truth_label = _truth_label(cfg, effective_mode, source_state, data_provenance)
    decision_usefulness = "decision_useful" if replication_probability >= 0.55 and data_provenance != "synthetic" else "exploratory"
    terminal_bankroll = cfg.bankroll + _quantile(finals, 0.5)
    terminal_state = {
        "bankroll": round(terminal_bankroll, 4),
        "phase": representative_steps[-1].phase if representative_steps else cfg.phase,
        "source_mode": effective_mode,
        "source_state": source_state,
    }
    assumptions = {
        "mode": effective_mode,
        "runs": actual_runs,
        "steps_per_run": cfg.n_rounds,
        "strategy": cfg.strategy_id or cfg.strategy,
        "source_state": source_state,
        "data_provenance": data_provenance,
        "decision_usefulness": decision_usefulness,
        "time_basis": {
            "market_time": "simulated_market_time" if effective_mode != "quick" else "synthetic_step_time",
            "wall_clock": "estimated_real_world_elapsed_time" if effective_mode != "quick" else "compressed_local_runtime",
        },
        "execution_assumptions": {
            "fill_model": "partial_fill_discount" if effective_mode in {"realistic", "continuation", "replay"} else "synthetic_full_fill",
            "slippage_model": "active" if effective_mode in {"realistic", "continuation", "replay"} else "minimal",
            "settlement_model": "simulated_outcome_settlement",
        },
        "fee_assumptions": {
            "model": "rake_fraction",
            "house_edge_pct": round(strategy.get("edge", 0.02) * 100, 3),
            "applied": True,
            "note": "House edge is deducted from each simulated outcome. Actual fees vary by venue and contract.",
        },
        "slippage_assumptions": {
            "model": "uniform_random_pct",
            "range_pct": "0.50% – 2.00%" if effective_mode in {"realistic", "continuation", "replay"} else "0.00% (minimal)",
            "applied": effective_mode in {"realistic", "continuation", "replay"},
        },
        "latency_assumptions": {
            "model": "request_latency_ms",
            "applied": cfg.latency_realism or effective_mode in {"realistic", "continuation", "replay"},
            "note": "Latency slows simulated throughput vs wall-clock time when applied.",
        },
        "replication_components": rp_components,
        "latency_realism": cfg.latency_realism or effective_mode in {"realistic", "continuation", "replay"},
    }
    avg_actions = float(strategy.get("actions", 1.0)) * cfg.n_rounds
    elapsed = time.perf_counter() - started
    if effective_mode == "quick" and elapsed > 6.0:
        caveats.append("Quick mode exceeded the target wall time and should be treated as exploratory.")
    return SimulationResult(
        run_id=uuid.uuid4().hex,
        mode=effective_mode,
        config=cfg,
        replication_probability=replication_probability,
        realism_score=realism_score,
        realism_label=_realism_label(realism_score),
        estimated_real_elapsed_s=_estimated_real_elapsed_s(cfg, avg_actions),
        pnl_p10=round(_quantile(finals, 0.1), 4),
        pnl_p50=round(_quantile(finals, 0.5), 4),
        pnl_p90=round(_quantile(finals, 0.9), 4),
        max_drawdown_p50=round(_quantile(drawdowns, 0.5), 4),
        hit_rate_estimate=round(mean(hit_rates), 4),
        steps=representative_steps,
        terminal_state=terminal_state,
        assumptions=assumptions,
        caveats=caveats,
        truth_label=truth_label,
        warning=(
            "Exploratory synthetic output only."
            if decision_usefulness == "exploratory"
            else "Simulation only. Use as decision support with explicit truth labels and caveats."
        ),
    )


def run_simulation(cfg: SimulatorConfig) -> SimulationResult:
    return simulate(cfg)


def result_to_dict(result: SimulationResult) -> dict[str, Any]:
    payload = {
        "run_id": result.run_id,
        "simulation_id": result.run_id,
        "mode": result.mode,
        "config": asdict(result.config),
        "replication_probability": result.replication_probability,
        "realism_score": result.realism_score,
        "realism_label": result.realism_label,
        "estimated_real_elapsed_s": round(result.estimated_real_elapsed_s, 3),
        "pnl_p10": result.pnl_p10,
        "pnl_p50": result.pnl_p50,
        "pnl_p90": result.pnl_p90,
        "max_drawdown_p50": result.max_drawdown_p50,
        "hit_rate_estimate": result.hit_rate_estimate,
        "truth_label": result.truth_label,
        "warning": result.warning,
        "steps": [asdict(step) for step in result.steps],
        "terminal_state": result.terminal_state,
        "assumptions": result.assumptions,
        "caveats": result.caveats,
        "realized_pnl": None,
        # Compatibility aliases for older UI/routes
        "n_rounds": result.n_rounds,
        "final_bankroll": result.final_bankroll,
        "roi_pct": result.roi_pct,
        "win_rate_pct": result.win_rate_pct,
        "max_drawdown_pct": result.max_drawdown_pct,
        "equity_curve": result.equity_curve,
        "elapsed_s": round(result.estimated_real_elapsed_s, 3),
        "started_at": time.time(),
    }
    return payload
