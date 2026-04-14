from __future__ import annotations

from fastapi import APIRouter, Request

import server as runtime
from models import (
    BotConfigSaveRequest,
    BotConfigureRequest,
    FundRequest,
    GoalsRequest,
    MilestoneContinueRequest,
    ResetToZeroRequest,
    ScaleRequest,
    SchedulerIntervalRequest,
    StrategyModeRequest,
)


router = APIRouter(tags=["bots"])

for method, path, handler in (
    ("GET", "/api/status", runtime.get_status),
    ("GET", "/api/equity", runtime.get_equity),
    ("GET", "/api/logs", runtime.get_logs),
    ("GET", "/api/info", runtime.get_info),
    ("GET", "/api/history", runtime.get_history),
    ("GET", "/api/history/summary", runtime.history_summary),
    ("GET", "/api/projections", runtime.get_projections),
    ("GET", "/api/notifications", runtime.get_notifs),
    ("GET", "/api/bots/config", runtime.get_bot_config),
    ("GET", "/api/bots/registry", runtime.get_bot_registry),
    ("GET", "/api/bots/{bot_id}/launch-gates", runtime.get_bot_launch_gates),
    ("GET", "/api/phases", runtime.get_phases),
    ("GET", "/api/goals", runtime.get_goals),
    ("GET", "/api/timeline", runtime.get_timeline),
    ("POST", "/api/start", runtime.start_bots),
    ("POST", "/api/stop", runtime.stop_bots),
    ("GET", "/api/circuit-breakers", runtime.get_circuit_breakers),
    ("GET", "/api/phase-transitions", runtime.get_phase_transitions),
    ("GET", "/api/volume-spikes", runtime.get_volume_spikes),
    ("GET", "/api/strategy-runtime", runtime.get_strategy_runtime),
):
    router.add_api_route(path, handler, methods=[method])

router.add_api_websocket_route("/api/ws", runtime.websocket_endpoint)


@router.post("/api/bots/config")
@runtime.limiter.limit("30/minute")
async def save_bot_config(request: Request, body: BotConfigSaveRequest):
    return await runtime.save_bot_config(request, body.to_body())


@router.post("/api/bots/registry")
@runtime.limiter.limit("30/minute")
async def save_bot_registry(request: Request, body: BotConfigSaveRequest):
    return await runtime.save_bot_registry(request, body.to_body())


@router.post("/api/bots/strategy")
@runtime.limiter.limit("30/minute")
async def set_all_strategy(request: Request, body: StrategyModeRequest):
    return await runtime.set_all_strategy(request, body.to_body())


@router.post("/api/bots/{bot_id}/strategy")
@runtime.limiter.limit("30/minute")
async def set_bot_strategy(bot_id: str, request: Request, body: StrategyModeRequest):
    return await runtime.set_bot_strategy(bot_id, request, body.to_body())


@router.post("/api/bots/{bot_id}/scale")
@runtime.limiter.limit("30/minute")
async def scale_bot(bot_id: str, request: Request, body: ScaleRequest):
    return await runtime.scale_bot(bot_id, request, body.to_body())


@router.post("/api/bots/{bot_id}/pause")
@runtime.limiter.limit("30/minute")
async def pause_bot(bot_id: str, request: Request):
    return await runtime.pause_bot(bot_id, request)


@router.post("/api/bots/{bot_id}/resume")
@runtime.limiter.limit("30/minute")
async def resume_bot(bot_id: str, request: Request):
    return await runtime.resume_bot(bot_id, request)


@router.post("/api/bots/{bot_id}/fund")
@runtime.limiter.limit("30/minute")
async def fund_bot(bot_id: str, request: Request, body: FundRequest):
    return await runtime.fund_bot(bot_id, request, body.to_body())


@router.post("/api/goals")
@runtime.limiter.limit("30/minute")
async def set_goals(request: Request, body: GoalsRequest):
    return await runtime.set_goals(request, body.to_body())


@router.post("/api/reset-to-zero")
@runtime.limiter.limit("10/minute")
async def reset_to_zero(request: Request, body: ResetToZeroRequest):
    return await runtime.reset_to_zero(request, body.to_body())


@router.post("/api/bots/{bot_id}/configure")
@runtime.limiter.limit("30/minute")
async def configure_bot(bot_id: str, request: Request, body: BotConfigureRequest):
    return await runtime.configure_bot(bot_id, request, body.to_body())


@router.post("/api/bots/{bot_id}/milestone/continue")
@runtime.limiter.limit("30/minute")
async def milestone_continue(bot_id: str, request: Request, body: MilestoneContinueRequest):
    return await runtime.milestone_continue(bot_id, request, body.to_body())


@router.get("/api/bots/scheduler")
async def get_bot_scheduler_status(request: Request):
    return await runtime.get_bot_scheduler_status(request)


@router.post("/api/bots/{bot_id}/scheduler/enable")
@runtime.limiter.limit("30/minute")
async def enable_bot_scheduler(bot_id: str, request: Request):
    return await runtime.enable_bot_scheduler(request, bot_id)


@router.post("/api/bots/{bot_id}/scheduler/disable")
@runtime.limiter.limit("30/minute")
async def disable_bot_scheduler(bot_id: str, request: Request):
    return await runtime.disable_bot_scheduler(request, bot_id)


@router.patch("/api/bots/{bot_id}/scheduler/interval")
@runtime.limiter.limit("30/minute")
async def patch_bot_scheduler_interval(bot_id: str, request: Request, body: SchedulerIntervalRequest):
    return await runtime.patch_bot_scheduler_interval(request, bot_id, body.to_body())


@router.post("/api/bots/{bot_id}/canary/start")
@runtime.limiter.limit("10/minute")
async def canary_start(bot_id: str, request: Request):
    try:
        request.state.typed_body = await request.json()
    except Exception:
        request.state.typed_body = {}
    from services.canary_guard import ensure_canary_approval
    import database as db

    blocked = ensure_canary_approval(bot_id=bot_id, request=request, db_module=db)
    if blocked is not None:
        return blocked
    return await runtime.start_canary_session(bot_id, request)


@router.post("/api/bots/{bot_id}/canary/stop")
@runtime.limiter.limit("10/minute")
async def canary_stop(bot_id: str, request: Request):
    return await runtime.stop_canary_session(bot_id, request)


@router.get("/api/canary/status")
async def canary_status(request: Request):
    return await runtime.get_canary_status(request)


@router.post("/api/bots/{bot_id}/growth")
@runtime.limiter.limit("30/minute")
async def set_growth_mode(bot_id: str, request: Request):
    return await runtime.set_bot_growth_mode(bot_id, request)


@router.get("/api/bots/{bot_id}/growth")
async def get_growth_mode(bot_id: str, request: Request):
    return await runtime.get_bot_growth_mode(bot_id, request)
