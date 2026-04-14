from __future__ import annotations

from fastapi import APIRouter

import server as runtime


router = APIRouter(tags=["platforms"])

for method, path, handler in (
    ("GET", "/api/platforms", runtime.list_platforms),
    ("GET", "/api/platforms/{platform}/health", runtime.platform_health),
    ("GET", "/api/platforms/{platform}/markets", runtime.platform_markets),
    ("GET", "/api/bots/catalog", runtime.get_bots_catalog),
    ("GET", "/api/bots/{bot_id}/mode", runtime.get_bot_mode),
    ("GET", "/api/bots/{bot_id}/truth", runtime.get_bot_truth),
    ("GET", "/api/research/signals", runtime.get_research_signals),
    ("GET", "/api/research/signals/{bot_id}", runtime.get_research_signals_for_bot),
):
    router.add_api_route(path, handler, methods=[method])
