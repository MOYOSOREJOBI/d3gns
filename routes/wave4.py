"""
Wave 4 routes — lab orchestrator, mall engine, wealth manager, portfolio, prices.

All bots start DISABLED (paper_mode=True, running=False).
User must explicitly POST /api/lab/start or /api/mall/start to activate.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["wave4"])

# ── Lazy singletons — loaded on first request ─────────────────────────────────

def _lab():
    from services.lab_orchestrator import get_lab_orchestrator
    return get_lab_orchestrator()

def _mall():
    from services.mall_production_engine import get_mall_engine
    return get_mall_engine()

def _wealth():
    from bots.wealth_manager_bot import get_wealth_manager
    return get_wealth_manager()

def _portfolio():
    from services.portfolio_allocator import get_allocator
    return get_allocator()

def _rt():
    from services.realtime_engine import get_realtime_engine
    return get_realtime_engine()

def _cb():
    from services.circuit_breaker import get_breaker
    return get_breaker()

def _ff():
    from services.profit_forcefield import get_forcefield
    return get_forcefield()


# ── Health ─────────────────────────────────────────────────────────────────────

@router.get("/wave4/health")
async def wave4_health():
    return {"ok": True, "services": ["lab_orchestrator", "mall_production_engine",
                                      "wealth_manager", "portfolio_allocator",
                                      "realtime_engine", "profit_forcefield",
                                      "circuit_breaker"]}


# ── Lab orchestrator ───────────────────────────────────────────────────────────

@router.get("/lab/status")
async def lab_status():
    return JSONResponse(_lab().get_status())

@router.get("/lab/last-cycle")
async def lab_last_cycle():
    result = _lab().get_last_cycle()
    if result is None:
        return JSONResponse({"message": "No cycles run yet — POST /api/lab/start first"})
    return JSONResponse(result)

@router.post("/lab/start")
async def lab_start():
    lab = _lab()
    if lab._running:
        return {"already_running": True, "status": lab.get_status()}
    lab.start()
    return {"started": True, "status": lab.get_status()}

@router.post("/lab/stop")
async def lab_stop():
    lab = _lab()
    lab.stop()
    return {"stopped": True}

@router.post("/lab/run-once")
async def lab_run_once():
    """Run one full cycle synchronously (for testing / manual trigger)."""
    try:
        result = _lab().run_once()
        return {
            "cycle_id":  result.cycle_id,
            "signals":   len(result.signals),
            "decisions": result.decisions_made,
            "bets":      result.bets_placed,
            "risked":    round(result.total_risked, 2),
            "errors":    result.errors,
            "ms":        result.cycle_ms,
        }
    except Exception as exc:
        logger.error("lab_run_once error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

@router.post("/lab/set-phase")
async def lab_set_phase(body: dict):
    phase = str(body.get("phase", "safe")).lower()
    valid = {"floor", "ultra_safe", "safe", "careful", "normal", "aggressive", "turbo"}
    if phase not in valid:
        raise HTTPException(status_code=400, detail=f"Invalid phase. Valid: {sorted(valid)}")
    _lab().phase = phase
    return {"phase_set": phase}

@router.post("/lab/set-paper-mode")
async def lab_set_paper_mode(body: dict):
    mode = bool(body.get("paper_mode", True))
    _lab().paper_mode = mode
    return {"paper_mode": mode, "warning": "Set paper_mode=false for live trading"}


# ── Mall engine ────────────────────────────────────────────────────────────────

@router.get("/mall/status")
async def mall_status():
    return JSONResponse(_mall().get_status())

@router.get("/mall/daily-stats")
async def mall_daily_stats():
    return JSONResponse(_mall().get_daily_stats())

@router.get("/mall/leaderboard")
async def mall_leaderboard():
    return JSONResponse({"bots": _mall().get_bot_leaderboard()})

@router.post("/mall/start")
async def mall_start():
    mall = _mall()
    if mall._running:
        return {"already_running": True, "status": mall.get_status()}
    mall.start()
    return {"started": True, "status": mall.get_status()}

@router.post("/mall/stop")
async def mall_stop():
    _mall().stop()
    return {"stopped": True}

@router.post("/mall/reset-daily")
async def mall_reset_daily():
    _mall().reset_daily()
    return {"reset": True}


# ── Wealth manager ─────────────────────────────────────────────────────────────

@router.get("/wealth/status")
async def wealth_status():
    try:
        result = _wealth().run_one_cycle()
        return JSONResponse({"ok": True, "data": result.get("data", {}), "summary": result.get("summary", "")})
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

@router.get("/wealth/playbook")
async def wealth_playbook():
    return JSONResponse(_wealth().get_growth_playbook())

@router.post("/wealth/reset-daily")
async def wealth_reset_daily(body: dict = {}):
    new_value = body.get("value")
    _wealth().reset_daily(new_value)
    return {"reset": True, "new_daily_start": _wealth()._daily_start}

@router.post("/wealth/set-bankroll")
async def wealth_set_bankroll(body: dict):
    val = float(body.get("bankroll", 500.0))
    _wealth()._bankroll = val
    _wealth()._daily_start = val
    return {"bankroll_set": val}


# ── Portfolio allocator ────────────────────────────────────────────────────────

@router.get("/portfolio/status")
async def portfolio_status():
    return JSONResponse(_portfolio().get_status())

@router.post("/portfolio/rebalance")
async def portfolio_rebalance():
    return JSONResponse(_portfolio().rebalance())

@router.get("/portfolio/top-performers")
async def portfolio_top_performers(n: int = 5, bot_type: str = "lab"):
    return JSONResponse({"performers": _portfolio().get_top_performers(n=n, bot_type=bot_type)})

@router.post("/portfolio/deploy-reserve")
async def portfolio_deploy_reserve(body: dict):
    bot_id = str(body.get("bot_id", ""))
    amount = float(body.get("amount", 0))
    if not bot_id or amount <= 0:
        raise HTTPException(status_code=400, detail="bot_id and amount required")
    return JSONResponse(_portfolio().deploy_reserve(bot_id, amount))


# ── Real-time price feeds ──────────────────────────────────────────────────────

@router.get("/prices")
async def get_all_prices():
    try:
        rt = _rt()
        if not rt._started:
            return JSONResponse({"running": False, "message": "Real-time engine not started. POST /api/realtime/start"})
        return JSONResponse({"running": True, "prices": rt.get_all_prices()})
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

@router.get("/prices/{symbol}")
async def get_price(symbol: str):
    try:
        rt = _rt()
        p = rt.get_price(symbol.upper())
        if p is None:
            raise HTTPException(status_code=404, detail=f"No price data for {symbol}")
        return {"symbol": symbol.upper(), "price": p}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

@router.post("/realtime/start")
async def realtime_start():
    rt = _rt()
    if rt._started:
        return {"already_running": True, "status": rt.get_status()}
    rt.start()
    return {"started": True}

@router.get("/realtime/status")
async def realtime_status():
    return JSONResponse(_rt().get_status())

@router.get("/forex")
async def get_forex(base: str = "USD"):
    try:
        from adapters.forex_rates import ForexRatesAdapter
        result = ForexRatesAdapter().get_key_pairs(base_currency=base.upper())
        return JSONResponse(result)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

@router.get("/markets")
async def get_global_markets():
    try:
        from adapters.world_markets import WorldMarketsAdapter
        result = WorldMarketsAdapter().get_global_snapshot()
        return JSONResponse(result)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

@router.get("/crypto/fear-greed")
async def get_fear_greed():
    try:
        from adapters.fear_greed import FearGreedAdapter
        return JSONResponse(FearGreedAdapter().get_current())
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── Circuit breaker ────────────────────────────────────────────────────────────

@router.get("/circuit-breaker/status")
async def cb_status():
    try:
        return JSONResponse(_cb().get_status())
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

@router.post("/circuit-breaker/reset")
async def cb_reset():
    try:
        _cb().reset_all()
        return {"reset": True}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── Profit forcefield ──────────────────────────────────────────────────────────

@router.get("/forcefield/status")
async def ff_status():
    try:
        return JSONResponse(_ff().get_status())
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

@router.post("/forcefield/open-position")
async def ff_open_position(body: dict):
    try:
        pos = _ff().open_position(
            pos_id=str(body["pos_id"]),
            market=str(body["market"]),
            entry_price=float(body["entry_price"]),
            entry_size=float(body["entry_size"]),
            side=str(body.get("side", "long")),
        )
        return {"opened": True, "pos_id": pos.pos_id}
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=f"Missing field: {exc}")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── Decision engine stats ──────────────────────────────────────────────────────

@router.get("/decisions/stats")
async def decision_stats():
    try:
        from services.decision_engine import get_decision_engine
        return JSONResponse(get_decision_engine().get_stats())
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── System overview ────────────────────────────────────────────────────────────

@router.get("/system/overview")
async def system_overview():
    """Single endpoint that returns the full system state."""
    overview: dict[str, Any] = {"ok": True}

    def safe(key: str, fn):
        try:
            overview[key] = fn()
        except Exception as exc:
            overview[key] = {"error": str(exc)}

    safe("lab",         lambda: _lab().get_status())
    safe("mall",        lambda: _mall().get_status())
    safe("portfolio",   lambda: _portfolio().get_status())
    safe("playbook",    lambda: _wealth().get_growth_playbook())
    safe("realtime",    lambda: _rt().get_status())

    return JSONResponse(overview)
