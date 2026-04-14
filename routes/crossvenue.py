from __future__ import annotations

from fastapi import APIRouter

import server as runtime


router = APIRouter(tags=["crossvenue"])

for method, path, handler in (
    ("GET", "/api/crossvenue/watchlist", runtime.crossvenue_watchlist),
    ("GET", "/api/arbitrage", runtime.get_arbitrage),
):
    router.add_api_route(path, handler, methods=[method])
