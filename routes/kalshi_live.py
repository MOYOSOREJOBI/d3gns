from __future__ import annotations

from fastapi import APIRouter, Request

import server as runtime
from models import KalshiLiveOrderRequest


router = APIRouter(tags=["kalshi-live"])

for method, path, handler in (
    ("GET", "/api/kalshi/live/health", runtime.kalshi_live_health),
    ("GET", "/api/kalshi/live/markets", runtime.kalshi_live_markets),
    ("GET", "/api/kalshi/live/balance", runtime.kalshi_live_balance),
    ("GET", "/api/kalshi/live/positions", runtime.kalshi_live_positions),
    ("DELETE", "/api/kalshi/live/order/{order_id}", runtime.kalshi_live_cancel_order),
    ("GET", "/api/kalshi/live/order/{order_id}", runtime.kalshi_live_get_order),
):
    router.add_api_route(path, handler, methods=[method])


@router.post("/api/kalshi/live/order")
@runtime.limiter.limit("30/minute")
async def kalshi_live_place_order(request: Request, body: KalshiLiveOrderRequest):
    return await runtime.kalshi_live_place_order(request, body.to_body())
