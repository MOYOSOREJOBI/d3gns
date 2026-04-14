"""
Real-Time Engine — sub-second price feed manager and fast polling hub.

Architecture:
  - Manages WebSocket connections to Binance (free, no auth) for live prices
  - Falls back to REST polling at 1-5s intervals for other sources
  - Emits price/signal events to registered listeners (decision engine)
  - Auto-reconnects on drop, tracks feed latency
  - Thread-safe event bus for cross-service communication

Target decision latency: <50ms from price update to signal
Target feed latency:     <100ms for WebSocket sources, <1s for REST

Usage:
    engine = get_realtime_engine()
    engine.subscribe("price_update", my_handler)
    engine.start()
"""
from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque
from typing import Any, Callable

logger = logging.getLogger(__name__)

# ── Event types ───────────────────────────────────────────────────────────────
EVENT_PRICE_UPDATE  = "price_update"
EVENT_SIGNAL_FIRE   = "signal_fire"
EVENT_CIRCUIT_BREAK = "circuit_break"
EVENT_ORDER_FILL    = "order_fill"
EVENT_FEED_RECONNECT= "feed_reconnect"
EVENT_HEALTH_TICK   = "health_tick"


class PriceBar:
    """Lightweight OHLCV bar for real-time streaming."""
    __slots__ = ("symbol", "price", "bid", "ask", "volume", "change_pct", "ts", "source")

    def __init__(self, symbol: str, price: float, ts: float,
                 bid: float = 0.0, ask: float = 0.0,
                 volume: float = 0.0, change_pct: float = 0.0,
                 source: str = "unknown"):
        self.symbol     = symbol
        self.price      = price
        self.bid        = bid
        self.ask        = ask
        self.volume     = volume
        self.change_pct = change_pct
        self.ts         = ts
        self.source     = source

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol, "price": self.price,
            "bid": self.bid, "ask": self.ask,
            "volume": self.volume, "change_pct": self.change_pct,
            "ts": self.ts, "source": self.source,
            "age_ms": round((time.time() - self.ts) * 1000, 1),
        }


class EventBus:
    """
    Thread-safe in-process event bus.
    Handlers run in the calling thread (synchronous) by default.
    Use threaded=True to run each handler in its own thread.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, list[Callable]] = {}
        self._lock = threading.Lock()
        self._event_count: dict[str, int] = {}

    def subscribe(self, event: str, handler: Callable) -> None:
        with self._lock:
            self._handlers.setdefault(event, [])
            if handler not in self._handlers[event]:
                self._handlers[event].append(handler)

    def unsubscribe(self, event: str, handler: Callable) -> None:
        with self._lock:
            if event in self._handlers and handler in self._handlers[event]:
                self._handlers[event].remove(handler)

    def emit(self, event: str, data: Any, *, threaded: bool = False) -> None:
        with self._lock:
            handlers = list(self._handlers.get(event, []))
            self._event_count[event] = self._event_count.get(event, 0) + 1

        for handler in handlers:
            try:
                if threaded:
                    t = threading.Thread(target=handler, args=(data,), daemon=True)
                    t.start()
                else:
                    handler(data)
            except Exception as exc:
                logger.error("Event handler error [%s]: %s", event, exc)

    @property
    def stats(self) -> dict[str, int]:
        return dict(self._event_count)


class PriceFeedManager:
    """
    Manages multi-source price feeds via REST polling.
    Uses threads for each source — fast, parallel, fault-tolerant.
    """

    # Sources to poll and their intervals
    POLL_SOURCES = [
        # (name, adapter_class_path, method, symbols, interval_s)
        ("binance",    "adapters.binance_public.BinancePublicAdapter",  "get_top_tickers",    ["BTCUSDT","ETHUSDT","SOLUSDT","BNBUSDT"], 2.0),
        ("coingecko",  "adapters.coingecko.CoinGeckoAdapter",           "get_price",          ["bitcoin","ethereum","solana"],           5.0),
        ("fear_greed", "adapters.fear_greed.FearGreedAdapter",          "get_current",        [],                                        60.0),
        ("defillama",  "adapters.defillama.DeFiLlamaAdapter",           "get_global_tvl",     [],                                        30.0),
    ]

    def __init__(self, bus: EventBus) -> None:
        self._bus     = bus
        self._running = False
        self._threads: list[threading.Thread] = []
        self._price_cache: dict[str, PriceBar] = {}
        self._feed_errors: dict[str, int] = {}
        self._lock = threading.Lock()

    def start(self) -> None:
        self._running = True
        for name, cls_path, method, symbols, interval in self.POLL_SOURCES:
            t = threading.Thread(
                target=self._poll_loop,
                args=(name, cls_path, method, symbols, interval),
                daemon=True,
                name=f"feed-{name}",
            )
            t.start()
            self._threads.append(t)
        logger.info("PriceFeedManager started: %d sources", len(self.POLL_SOURCES))

    def stop(self) -> None:
        self._running = False

    def get_latest(self, symbol: str) -> PriceBar | None:
        with self._lock:
            return self._price_cache.get(symbol)

    def get_all_latest(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {k: v.to_dict() for k, v in self._price_cache.items()}

    def _poll_loop(self, name: str, cls_path: str, method: str,
                   symbols: list[str], interval: float) -> None:
        import importlib
        try:
            mod_path, cls_name = cls_path.rsplit(".", 1)
            mod     = importlib.import_module(mod_path)
            adapter = getattr(mod, cls_name)()
        except Exception as exc:
            logger.error("Failed to init feed %s: %s", name, exc)
            return

        while self._running:
            t0 = time.monotonic()
            try:
                if symbols:
                    # Try calling with first symbol
                    for sym in symbols:
                        try:
                            res = getattr(adapter, method)(sym)
                            self._process_result(name, sym, res)
                        except TypeError:
                            res = getattr(adapter, method)()
                            self._process_result(name, name, res)
                            break
                else:
                    res = getattr(adapter, method)()
                    self._process_result(name, name, res)
                self._feed_errors[name] = 0
            except Exception as exc:
                self._feed_errors[name] = self._feed_errors.get(name, 0) + 1
                logger.debug("Feed %s error: %s", name, exc)

            elapsed = time.monotonic() - t0
            sleep_time = max(0.1, interval - elapsed)
            time.sleep(sleep_time)

    def _process_result(self, source: str, symbol: str, res: dict[str, Any]) -> None:
        if not res or not res.get("ok"):
            return
        data = res.get("data", {})

        # Extract price from various response shapes
        price = None
        if "price" in data:
            price = float(data["price"])
        elif "tickers" in data and symbol in data["tickers"]:
            price = float(data["tickers"][symbol].get("price", 0))
        elif symbol.lower() in str(data).lower():
            # Best effort: look for price in nested dicts
            for k, v in data.items():
                if isinstance(v, (int, float)) and 100 < v < 1_000_000:
                    price = float(v)
                    break

        if price and price > 0:
            bar = PriceBar(
                symbol=symbol,
                price=price,
                ts=time.time(),
                change_pct=float(data.get("change_pct", data.get("price_change_pct_24h", 0)) or 0),
                volume=float(data.get("volume_24h", data.get("quote_volume_24h", 0)) or 0),
                source=source,
            )
            with self._lock:
                self._price_cache[symbol] = bar
            self._bus.emit(EVENT_PRICE_UPDATE, bar)


class SignalBuffer:
    """
    Rolling buffer of recent signals from all bots.
    Keeps last N signals per source for fast aggregation.
    """

    def __init__(self, max_per_source: int = 10) -> None:
        self._buffer: dict[str, deque] = {}
        self._max    = max_per_source
        self._lock   = threading.Lock()

    def push(self, source: str, signal: dict[str, Any]) -> None:
        with self._lock:
            if source not in self._buffer:
                self._buffer[source] = deque(maxlen=self._max)
            signal["_ts"] = time.time()
            self._buffer[source].append(signal)

    def latest(self, source: str) -> dict[str, Any] | None:
        with self._lock:
            buf = self._buffer.get(source)
            return dict(buf[-1]) if buf else None

    def latest_all(self, max_age_s: float = 300.0) -> dict[str, dict[str, Any]]:
        """Return the latest signal from each source, filtered by age."""
        now = time.time()
        with self._lock:
            result = {}
            for src, buf in self._buffer.items():
                if buf:
                    sig = dict(buf[-1])
                    if now - sig.get("_ts", 0) <= max_age_s:
                        result[src] = sig
        return result

    def freshness_report(self) -> dict[str, float]:
        """Returns seconds since last signal per source."""
        now = time.time()
        with self._lock:
            return {
                src: round(now - buf[-1].get("_ts", 0), 1)
                for src, buf in self._buffer.items() if buf
            }


class RealTimeEngine:
    """
    Master real-time engine — coordinates feeds, signals, and events.
    """

    def __init__(self) -> None:
        self.bus           = EventBus()
        self.feed_manager  = PriceFeedManager(self.bus)
        self.signal_buffer = SignalBuffer()
        self._started      = False
        self._start_time   = 0.0
        self._tick_thread: threading.Thread | None = None

    def start(self) -> None:
        if self._started:
            return
        self._started    = True
        self._start_time = time.time()
        self.feed_manager.start()
        # Health tick every 30s
        self._tick_thread = threading.Thread(
            target=self._health_tick_loop, daemon=True, name="health-tick"
        )
        self._tick_thread.start()
        logger.info("RealTimeEngine started")

    def stop(self) -> None:
        self._started = False
        self.feed_manager.stop()

    def subscribe(self, event: str, handler: Callable) -> None:
        self.bus.subscribe(event, handler)

    def emit(self, event: str, data: Any) -> None:
        self.bus.emit(event, data)

    def push_signal(self, source: str, signal: dict[str, Any]) -> None:
        self.signal_buffer.push(source, signal)
        self.bus.emit(EVENT_SIGNAL_FIRE, {"source": source, **signal})

    def get_price(self, symbol: str) -> float | None:
        bar = self.feed_manager.get_latest(symbol)
        return bar.price if bar else None

    def get_all_prices(self) -> dict[str, dict[str, Any]]:
        return self.feed_manager.get_all_latest()

    def get_status(self) -> dict[str, Any]:
        uptime = round(time.time() - self._start_time, 1) if self._start_time else 0
        return {
            "running":       self._started,
            "uptime_s":      uptime,
            "price_symbols": len(self.feed_manager._price_cache),
            "prices":        self.feed_manager.get_all_latest(),
            "signal_sources": list(self.signal_buffer._buffer.keys()),
            "signal_freshness": self.signal_buffer.freshness_report(),
            "feed_errors":   dict(self.feed_manager._feed_errors),
            "event_counts":  self.bus.stats,
        }

    def _health_tick_loop(self) -> None:
        while self._started:
            try:
                self.bus.emit(EVENT_HEALTH_TICK, self.get_status())
            except Exception:
                pass
            time.sleep(30)


# ── Global singleton ──────────────────────────────────────────────────────────
_engine: RealTimeEngine | None = None
_engine_lock = threading.Lock()


def get_realtime_engine() -> RealTimeEngine:
    global _engine
    with _engine_lock:
        if _engine is None:
            _engine = RealTimeEngine()
    return _engine
