from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import server as runtime
from models import ExecutorTakeoverRequest, HumanRelayOpenRequest, HumanRelayRespondRequest, LiveControlRequest


router = APIRouter(tags=["system"])

for method, path, handler in (
    ("GET", "/api/btc-price", runtime.get_btc_price),
    ("GET", "/api/data-sources", runtime.get_data_sources),
    ("GET", "/api/system/truth", runtime.get_system_truth),
    ("GET", "/api/system/home", runtime.get_system_home),
    ("GET", "/api/system/operator-brief", runtime.get_operator_brief),
    ("GET", "/api/forcefield", runtime.get_forcefield),
    ("POST", "/api/forcefield/continue", runtime.continue_forcefield),
    ("POST", "/api/forcefield/pause", runtime.pause_forcefield),
    ("POST", "/api/forcefield/resume", runtime.resume_forcefield),
    ("POST", "/api/forcefield/sweep", runtime.sweep_forcefield),
    ("GET", "/api/positions/open", runtime.get_open_positions),
    ("POST", "/api/positions/{order_id}/settle", runtime.settle_position),
    ("GET", "/api/system/executor", runtime.get_system_executor),
    ("GET", "/api/system/executor/detail", runtime.get_executor_status),
    ("GET", "/api/system/executor/history", runtime.get_executor_history),
    ("GET", "/api/system/storage", runtime.get_system_storage),
    ("GET", "/api/system/storage/integrity", runtime.get_system_storage_integrity),
    ("GET", "/api/system/storage/backups", runtime.get_system_storage_backups),
    ("GET", "/api/system/storage/backups/{basename}/verify", runtime.verify_system_storage_backup),
    ("POST", "/api/system/storage/backup", runtime.create_system_storage_backup),
    ("GET", "/api/system/startup", runtime.get_system_startup),
    ("GET", "/api/system/health", runtime.get_system_health),
    ("GET", "/api/system/health/deep", runtime.get_system_health_deep),
    ("GET", "/api/system/security", runtime.get_system_security),
    ("GET", "/api/system/runtime-history", runtime.get_system_runtime_history),
    ("GET", "/api/system/quota", runtime.system_quota),
    ("GET", "/api/system/credentials", runtime.get_credentials_status),
    ("GET", "/api/system/credentials/{platform}", runtime.get_platform_credential_status),
    ("GET", "/api/system/live-control", runtime.get_live_control),
    ("GET", "/api/system/go-live-readiness", runtime.get_go_live_readiness),
    ("GET", "/api/system/reconciliation", runtime.get_reconciliation),
    ("GET", "/api/system/reconciliation/history", runtime.get_reconciliation_history),
    ("GET", "/api/system/human-relay", runtime.get_human_relay),
    ("GET", "/api/system/human-relay/{challenge_id}", runtime.get_human_relay_detail),
    ("GET", "/api/system/scheduler", runtime.get_scheduler_status),
    ("POST", "/api/system/scheduler/trigger/{job_name}", runtime.trigger_scheduler_job),
    ("GET", "/api/system/stake/health", runtime.get_stake_health),
    ("GET", "/api/version", runtime.version),
    ("GET", "/metrics", runtime.get_metrics),
    ("GET", "/api/orders", runtime.get_orders),
    ("GET", "/api/reconciliation/orders", runtime.get_reconciliation_orders),
    ("GET", "/api/reconciliation/orders/{order_ref}", runtime.get_reconciliation_order_detail),
    ("GET", "/api/system/launch-checklist", runtime.get_launch_checklist),
):
    router.add_api_route(path, handler, methods=[method])


@router.post("/api/system/human-relay/open")
@runtime.limiter.limit("30/minute")
async def open_human_relay(request: Request, body: HumanRelayOpenRequest):
    return await runtime.open_human_relay(request, body.to_body())


@router.post("/api/system/human-relay/{challenge_id}/respond")
@runtime.limiter.limit("30/minute")
async def respond_human_relay(challenge_id: str, request: Request, body: HumanRelayRespondRequest):
    return await runtime.respond_human_relay(request, challenge_id, body.to_body())


@router.post("/api/system/live-control")
@runtime.limiter.limit("30/minute")
async def update_live_control(request: Request, body: LiveControlRequest):
    return await runtime.update_live_control(request, body.to_body())


@router.post("/api/system/executor/takeover")
@runtime.limiter.limit("10/minute")
async def force_executor_takeover(request: Request, body: ExecutorTakeoverRequest):
    return await runtime.force_executor_takeover(request, body.to_body())


@router.get("/api/system/infrastructure")
async def get_infrastructure_status(request: Request):
    runtime._check_token(request)
    from services.infrastructure_status import get_infrastructure_status as _get_infrastructure_status

    return JSONResponse({"ok": True, **_get_infrastructure_status()})
