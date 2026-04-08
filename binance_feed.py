"""
BinanceFeed — async real-time BTC price via Binance WebSocket.

Usage:
    feed = BinanceFeed()
    await feed.start()          # runs WS loop in background task
    price = feed.current_price  # always up-to-date float

Falls back to REST poll on WS failure.
"""

import asyncio
import json
import logging
import time

import aiohttp

logger = logging.getLogger(__name__)

_WS_URL   = "wss://stream.binance.com:9443/ws/btcusdt@ticker"
_REST_URL = "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT"
_RECONNECT_DELAY = 5   # seconds between reconnect attempts
_REST_FALLBACK_INTERVAL = 10   # seconds between REST polls when WS down


class BinanceFeed:
    """
    Async Binance BTC price feed.
    Maintains a background task that keeps `current_price` updated.
    """

    def __init__(self):
        self._price: float | None = None
        self._last_update: float  = 0.0
        self._ws_active: bool     = False
        self._task: asyncio.Task | None = None

    # ── Properties ─────────────────────────────────────────────────────────────

    @property
    def current_price(self) -> float | None:
        return self._price

    @property
    def last_update_ts(self) -> float:
        return self._last_update

    @property
    def is_live(self) -> bool:
        return self._ws_active and (time.time() - self._last_update < 30)

    # ── Start ──────────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Launch the background feed loop. Safe to call multiple times."""
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._run_forever())
        logger.info("[BinanceFeed] Started background price feed")

    # ── Internal loops ─────────────────────────────────────────────────────────

    async def _run_forever(self) -> None:
        while True:
            try:
                await self._ws_loop()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning(f"[BinanceFeed] WS error: {exc} — falling back to REST")
                self._ws_active = False
                await self._rest_poll_once()
                await asyncio.sleep(_RECONNECT_DELAY)

    async def _ws_loop(self) -> None:
        """Connect to Binance WS and update price on every ticker event."""
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(_WS_URL, heartbeat=20) as ws:
                self._ws_active = True
                logger.info("[BinanceFeed] WebSocket connected")
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        data = json.loads(msg.data)
                        price = data.get("c") or data.get("price")
                        if price:
                            self._price       = float(price)
                            self._last_update = time.time()
                    elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        break

    async def _rest_poll_once(self) -> None:
        """Single REST fetch — used when WS is down."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(_REST_URL, timeout=aiohttp.ClientTimeout(total=5)) as r:
                    data  = await r.json()
                    price = data.get("price")
                    if price:
                        self._price       = float(price)
                        self._last_update = time.time()
                        logger.debug(f"[BinanceFeed] REST fallback price: {self._price}")
        except Exception as exc:
            logger.debug(f"[BinanceFeed] REST fallback failed: {exc}")


# ── Module-level singleton (convenience for sync code) ─────────────────────────

_feed: BinanceFeed | None = None


def get_feed() -> BinanceFeed:
    """Return the module-level BinanceFeed singleton."""
    global _feed
    if _feed is None:
        _feed = BinanceFeed()
    return _feed


def get_price_sync() -> float | None:
    """
    Synchronous best-effort price getter.
    Returns cached price if feed is running, else None.
    """
    if _feed is not None:
        return _feed.current_price
    # Fallback: synchronous REST request
    try:
        import requests
        r = requests.get(_REST_URL, timeout=4)
        return float(r.json()["price"])
    except Exception:
        return None
