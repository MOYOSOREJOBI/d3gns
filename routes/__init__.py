from __future__ import annotations

from fastapi import FastAPI

from . import auth, bots, crossvenue, diagnostics, kalshi_live, mall, mrkrabs, platforms, proposals, settings, simulator, system, wallet
from . import wave4


def include_routers(app: FastAPI) -> None:
    for router in (
        auth.router,
        bots.router,
        wallet.router,
        settings.router,
        system.router,
        platforms.router,
        mall.router,
        crossvenue.router,
        kalshi_live.router,
        simulator.router,
        diagnostics.router,
        mrkrabs.router,
        proposals.router,
        wave4.router,
    ):
        app.include_router(router)
