from __future__ import annotations

from fastapi import APIRouter

import server as runtime


router = APIRouter(tags=["diagnostics"])

for method, path, handler in (
    ("GET", "/api/diagnostics/phase-transitions", runtime.get_phase_transitions_log),
    ("GET", "/api/diagnostics/bots", runtime.get_bots_diagnostics),
    ("GET", "/api/diagnostics/bots/{bot_id}", runtime.get_bot_diagnostic),
    ("GET", "/api/diagnostics/strategies", runtime.get_strategy_diagnostics_route),
    ("GET", "/api/diagnostics/platforms", runtime.get_platform_diagnostics_route),
    ("GET", "/api/diagnostics/platforms/{platform}", runtime.get_platform_diagnostic_route),
    ("GET", "/api/diagnostics/summary", runtime.get_diagnostics_summary),
    ("GET", "/api/diagnostics/calibration", runtime.get_calibration_summary),
    ("GET", "/api/diagnostics/calibration/{bot_id}", runtime.get_calibration_summary),
    ("GET", "/api/diagnostics/simulator/{run_id}", runtime.get_simulator_diagnostic_route),
    ("GET", "/api/diagnostics/signals", runtime.get_signal_analysis),
    ("GET", "/api/diagnostics/circuit-breakers", runtime.get_cb_analysis),
    ("GET", "/api/backtests", runtime.list_backtests),
):
    router.add_api_route(path, handler, methods=[method])


@router.post("/api/backtests/{bot_id}/run")
@runtime.limiter.limit("30/minute")
async def run_backtest_endpoint(bot_id: str, request):
    return await runtime.run_backtest_endpoint(request, bot_id)
