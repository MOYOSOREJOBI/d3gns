from __future__ import annotations

import config as _cfg
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import database as db
import server as runtime
from models import SimulatorCompareRequest, SimulatorRunRequest


router = APIRouter(tags=["simulator"])

for method, path, handler in (
    ("GET", "/api/simulator/status", runtime.simulator_status),
    ("GET", "/api/simulator/capabilities", runtime.simulator_capabilities),
    ("GET", "/api/simulator/history", runtime.simulator_history),
    ("GET", "/api/simulator/{run_id}", runtime.simulator_run_detail),
    ("GET", "/api/simulator/leaderboard", runtime.simulator_leaderboard),
):
    router.add_api_route(path, handler, methods=[method])


@router.post("/api/simulator/run")
@runtime.limiter.limit("30/minute")
async def simulator_run(request: Request, body: SimulatorRunRequest):
    request.state.typed_body = body.to_body()
    return await runtime.simulator_run(request)


@router.post("/api/simulator/continue")
@runtime.limiter.limit("30/minute")
async def simulator_continue(request: Request, body: SimulatorRunRequest):
    request.state.typed_body = body.to_body()
    return await runtime.simulator_continue(request)


@router.post("/api/simulator/compare")
@runtime.limiter.limit("30/minute")
async def simulator_compare(request: Request, body: SimulatorCompareRequest):
    request.state.typed_body = body.to_body()
    return await runtime.simulator_compare(request)


@router.post("/api/simulator/realistic")
async def simulator_realistic(request: Request):
    runtime._check_token(request)
    try:
        body = await request.json()
    except Exception:
        body = {}

    from services.realistic_simulator import SimConfig, run_batch_simulation, run_realistic_simulation

    config = SimConfig(
        starting_capital=float(body.get("starting_capital", 100)),
        target_multiple=float(body.get("target_multiple", 3.0)),
        duration_hours=float(body.get("duration_hours", 96)),
        time_step_hours=float(body.get("time_step_hours", 0.1)),
        num_bots=int(body.get("num_bots", 5)),
        bot_edge_bps=float(body.get("bot_edge_bps", 45)),
        bot_win_rate=float(body.get("win_rate", 0.58)),
        fee_drag_bps=float(body.get("fee_drag_bps", 50)),
        slippage_bps=float(body.get("slippage_bps", 15)),
        fill_rate=float(body.get("fill_rate", 0.85)),
        opportunities_per_hour=float(body.get("opportunities_per_hour", 2.0)),
        seed=body.get("seed"),
    )

    mode = str(body.get("mode", "single")).lower()
    if mode == "batch":
        result = run_batch_simulation(config, n_runs=int(body.get("n_runs", 200)))
    else:
        result = run_realistic_simulation(config)

    return JSONResponse({"ok": True, **result})


@router.post("/api/simulator/quick-mc")
async def simulator_quick_mc(request: Request):
    """Fast synthetic Monte Carlo — no external data, returns immediately."""
    runtime._check_token(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    from services.simulation.quick_mc import QuickMCConfig, run_quick_mc
    cfg = QuickMCConfig(
        starting_capital=float(body.get("starting_capital", 100)),
        target_multiple=float(body.get("target_multiple", 3.0)),
        floor_pct=float(body.get("floor_pct", 0.199)),
        horizon=str(body.get("horizon", "1w")),
        win_rate=float(body.get("win_rate", 0.58)),
        bet_size_pct=float(body.get("bet_size_pct", 0.03)),
        bets_per_hour=float(body.get("bets_per_hour", 1.5)),
        fee_drag_pct=float(body.get("fee_drag_pct", 0.005)),
        n_runs=min(int(body.get("n_runs", 300)), 1000),
        seed=body.get("seed"),
    )
    result = run_quick_mc(cfg)
    return JSONResponse({"ok": True, **result})


@router.post("/api/simulator/proposal-replay")
async def simulator_proposal_replay(request: Request):
    """Proposal-driven replay using real signal logs from DB."""
    runtime._check_token(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    from services.simulation.proposal_replay import ProposalReplayConfig, run_proposal_replay
    cfg = ProposalReplayConfig(
        bot_id=body.get("bot_id"),
        platform=body.get("platform"),
        starting_capital=float(body.get("starting_capital", 100)),
        target_multiple=float(body.get("target_multiple", 3.0)),
        floor_pct=float(body.get("floor_pct", 0.199)),
        n_runs=min(int(body.get("n_runs", 200)), 500),
        fee_drag_bps=float(body.get("fee_drag_bps", 50)),
        slippage_bps=float(body.get("slippage_bps", 15)),
        fill_rate=float(body.get("fill_rate", 0.85)),
        horizon_hours=(
            float(body.get("horizon_hours"))
            if body.get("horizon_hours") is not None
            else None
        ),
        seed=body.get("seed"),
    )
    result = run_proposal_replay(cfg, db_module=db)
    return JSONResponse({"ok": True, **result})


@router.post("/api/simulator/bootstrap-replay")
async def simulator_bootstrap_replay(request: Request):
    """Bootstrap replay from uploaded CSV trade records."""
    runtime._check_token(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    from services.simulation.bootstrap_replay import (
        BootstrapConfig, parse_csv_trades, parse_odds_snapshot, run_bootstrap_replay,
    )
    cfg = BootstrapConfig(
        starting_capital=float(body.get("starting_capital", 100)),
        target_multiple=float(body.get("target_multiple", 3.0)),
        floor_pct=float(body.get("floor_pct", 0.199)),
        n_runs=min(int(body.get("n_runs", 300)), 500),
        fee_drag_bps=float(body.get("fee_drag_bps", 50)),
        fill_rate=float(body.get("fill_rate", 0.85)),
        bets_per_hour=float(body.get("bets_per_hour", 1.5)),
        seed=body.get("seed"),
    )
    csv_text = body.get("csv")
    rows = body.get("rows")  # pre-parsed rows
    if csv_text:
        records = parse_csv_trades(str(csv_text))
    elif rows:
        records = parse_odds_snapshot(rows)
    else:
        return JSONResponse({"ok": False, "error": "Provide 'csv' text or 'rows' list"}, status_code=400)
    result = run_bootstrap_replay(records, cfg)
    return JSONResponse({"ok": True, **result})


@router.get("/api/simulator/replay-datasets")
async def simulator_replay_datasets(request: Request):
    runtime._check_token(request)
    platform = request.query_params.get("platform")
    limit = min(int(request.query_params.get("limit", "50") or 50), 200)
    from services.simulation.replay_dataset_builder import list_replay_datasets

    datasets = list_replay_datasets(db, platform=platform, limit=limit)
    return JSONResponse({"ok": True, "datasets": datasets, "count": len(datasets)})


@router.post("/api/simulator/replay-datasets/build")
async def simulator_build_replay_dataset(request: Request):
    runtime._check_token(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    from services.simulation.replay_dataset_builder import build_replay_dataset

    dataset = build_replay_dataset(
        db,
        bot_id=body.get("bot_id"),
        platform=body.get("platform"),
        market_id=body.get("market_id"),
        limit=min(int(body.get("limit", 500) or 500), 2000),
        persist=True,
    )
    return JSONResponse({"ok": True, "dataset": dataset})


@router.post("/api/simulator/replay-smart")
async def simulator_replay_smart(request: Request):
    """
    Prefer evidence-backed replay, then proposal replay, then synthetic MC.
    """
    runtime._check_token(request)
    try:
        body = await request.json()
    except Exception:
        body = {}

    from services.simulation.bootstrap_replay import BootstrapConfig, TradeRecord, run_bootstrap_replay
    from services.simulation.proposal_replay import ProposalReplayConfig, run_proposal_replay
    from services.simulation.quick_mc import QuickMCConfig, run_quick_mc
    from services.simulation.replay_dataset_builder import build_replay_dataset

    dataset = build_replay_dataset(
        db,
        bot_id=body.get("bot_id"),
        platform=body.get("platform"),
        market_id=body.get("market_id"),
        limit=min(int(body.get("limit", 500) or 500), 2000),
        persist=True,
    )
    min_obs = int(getattr(_cfg, "REPLAY_MIN_OBSERVATIONS", 30) or 30)
    records = [
        TradeRecord(
            probability=float(row.get("probability", 0.5) or 0.5),
            outcome=float(row.get("outcome", 0.0) or 0.0),
            payout_mult=float(row.get("payout_mult", 2.0) or 2.0),
            ts=str(row.get("ts") or ""),
        )
        for row in dataset.get("records", [])
        if row.get("outcome") is not None
    ]

    if len(records) >= min_obs:
        cfg = BootstrapConfig(
            starting_capital=float(body.get("starting_capital", 100)),
            target_multiple=float(body.get("target_multiple", 3.0)),
            floor_pct=float(body.get("floor_pct", 0.199)),
            n_runs=min(int(body.get("n_runs", 300) or 300), 1000),
            fee_drag_bps=float(body.get("fee_drag_bps", 50)),
            fill_rate=float(body.get("fill_rate", 0.85)),
            bets_per_hour=float(body.get("bets_per_hour", 1.5)),
            seed=body.get("seed"),
        )
        result = run_bootstrap_replay(records, cfg)
        return JSONResponse({"ok": True, "selected_mode": "bootstrap_replay", "evidence": dataset, **result})

    proposal_cfg = ProposalReplayConfig(
        bot_id=body.get("bot_id"),
        platform=body.get("platform"),
        starting_capital=float(body.get("starting_capital", 100)),
        target_multiple=float(body.get("target_multiple", 3.0)),
        floor_pct=float(body.get("floor_pct", 0.199)),
        n_runs=min(int(body.get("n_runs", 200) or 200), 500),
        fee_drag_bps=float(body.get("fee_drag_bps", 50)),
        slippage_bps=float(body.get("slippage_bps", 15)),
        fill_rate=float(body.get("fill_rate", 0.85)),
        horizon_hours=(
            float(body.get("horizon_hours"))
            if body.get("horizon_hours") is not None
            else None
        ),
        seed=body.get("seed"),
    )
    proposal_result = run_proposal_replay(proposal_cfg, db_module=db)
    if proposal_result.get("mode") != "proposal_replay_fallback":
        return JSONResponse({"ok": True, "selected_mode": "proposal_replay", "evidence": dataset, **proposal_result})

    quick_cfg = QuickMCConfig(
        starting_capital=float(body.get("starting_capital", 100)),
        target_multiple=float(body.get("target_multiple", 3.0)),
        floor_pct=float(body.get("floor_pct", 0.199)),
        horizon=str(body.get("horizon", "1w")),
        win_rate=float(body.get("win_rate", 0.58)),
        bet_size_pct=float(body.get("bet_size_pct", 0.03)),
        bets_per_hour=float(body.get("bets_per_hour", 1.5)),
        fee_drag_pct=float(body.get("fee_drag_pct", 0.005)),
        n_runs=min(int(body.get("n_runs", 300) or 300), 1000),
        seed=body.get("seed"),
    )
    quick_result = run_quick_mc(quick_cfg)
    return JSONResponse({"ok": True, "selected_mode": "quick_mc", "evidence": dataset, **quick_result})


@router.post("/api/simulator/command-center")
async def simulator_command_center(request: Request):
    """
    Unified simulator entry point for the current DeG£N$ command-center shell.

    Supported modes:
      - quick
      - proposal
      - bootstrap
      - smart
    """
    runtime._check_token(request)
    try:
        body = await request.json()
    except Exception:
        body = {}

    default_mode = "smart" if getattr(_cfg, "PREFERRED_SIMULATION_MODE", "replay_first") == "replay_first" else "quick"
    mode = str(body.get("mode", default_mode)).lower().strip()
    if mode == "quick":
        from services.simulation.quick_mc import QuickMCConfig, run_quick_mc

        cfg = QuickMCConfig(
            starting_capital=float(body.get("starting_capital", 100)),
            target_multiple=float(body.get("target_multiple", 3.0)),
            floor_pct=float(body.get("floor_pct", 0.199)),
            horizon=str(body.get("horizon", "1w")),
            win_rate=float(body.get("win_rate", 0.58)),
            bet_size_pct=float(body.get("bet_size_pct", 0.03)),
            bets_per_hour=float(body.get("bets_per_hour", 1.5)),
            fee_drag_pct=float(body.get("fee_drag_pct", 0.005)),
            n_runs=min(int(body.get("n_runs", 300)), 1000),
            seed=body.get("seed"),
        )
        return JSONResponse({"ok": True, "command_center_mode": mode, **run_quick_mc(cfg)})

    if mode == "proposal":
        from services.simulation.proposal_replay import ProposalReplayConfig, run_proposal_replay

        cfg = ProposalReplayConfig(
            bot_id=body.get("bot_id"),
            platform=body.get("platform"),
            starting_capital=float(body.get("starting_capital", 100)),
            target_multiple=float(body.get("target_multiple", 3.0)),
            floor_pct=float(body.get("floor_pct", 0.199)),
            n_runs=min(int(body.get("n_runs", 200)), 500),
            fee_drag_bps=float(body.get("fee_drag_bps", 50)),
            slippage_bps=float(body.get("slippage_bps", 15)),
            fill_rate=float(body.get("fill_rate", 0.85)),
            horizon_hours=(
                float(body.get("horizon_hours"))
                if body.get("horizon_hours") is not None
                else None
            ),
            seed=body.get("seed"),
        )
        return JSONResponse({"ok": True, "command_center_mode": mode, **run_proposal_replay(cfg, db_module=db)})

    if mode == "bootstrap":
        from services.simulation.bootstrap_replay import (
            BootstrapConfig, parse_csv_trades, parse_odds_snapshot, run_bootstrap_replay,
        )

        cfg = BootstrapConfig(
            starting_capital=float(body.get("starting_capital", 100)),
            target_multiple=float(body.get("target_multiple", 3.0)),
            floor_pct=float(body.get("floor_pct", 0.199)),
            n_runs=min(int(body.get("n_runs", 300)), 500),
            fee_drag_bps=float(body.get("fee_drag_bps", 50)),
            fill_rate=float(body.get("fill_rate", 0.85)),
            bets_per_hour=float(body.get("bets_per_hour", 1.5)),
            seed=body.get("seed"),
        )
        csv_text = body.get("csv")
        rows = body.get("rows")
        if csv_text:
            records = parse_csv_trades(str(csv_text))
        elif rows:
            records = parse_odds_snapshot(rows)
        else:
            return JSONResponse({"ok": False, "error": "Provide 'csv' text or 'rows' list"}, status_code=400)
        return JSONResponse({"ok": True, "command_center_mode": mode, **run_bootstrap_replay(records, cfg)})

    if mode == "smart":
        return await simulator_replay_smart(request)

    return JSONResponse(
        {"ok": False, "error": "unsupported_mode", "supported_modes": ["quick", "proposal", "bootstrap", "smart"]},
        status_code=400,
    )


@router.post("/api/simulator/target-timing")
async def simulator_target_timing(request: Request):
    """Compute target timing from an existing simulation result."""
    runtime._check_token(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    runs = body.get("runs")
    if not runs:
        return JSONResponse({"ok": False, "error": "Provide 'runs' list from a prior simulation"}, status_code=400)
    from services.simulation.target_timing import compute_target_timing, format_timing_for_api, timing_for_horizon
    timing = compute_target_timing(
        runs,
        starting_capital=float(body.get("starting_capital", 100)),
        target_multiple=float(body.get("target_multiple", 3.0)),
        bets_per_hour=float(body.get("bets_per_hour", 1.5)),
    )
    horizon = body.get("horizon", "1w")
    horizon_result = timing_for_horizon(timing, horizon)
    return JSONResponse({
        "ok": True,
        "timing": format_timing_for_api(timing),
        "horizon": horizon_result,
    })


@router.get("/api/simulator/calibration")
async def simulator_calibration(request: Request, bot_id: str | None = None):
    """Return Brier calibration stats for a bot."""
    runtime._check_token(request)
    from services.simulation.calibration import calibration_to_dict, compute_calibration
    observations = []
    if hasattr(db, "get_signal_observations"):
        observations = db.get_signal_observations(bot_id=bot_id, limit=500) or []
    if not observations and hasattr(db, "get_research_signals"):
        observations = db.get_research_signals(bot_id=bot_id, limit=500) or []
    result = compute_calibration(bot_id or "all", observations)
    return JSONResponse({"ok": True, "calibration": calibration_to_dict(result)})
