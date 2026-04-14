"""
Binance WebSocket Adapter — sub-second real-time price feed.

Connects to Binance public stream (no auth required):
  wss://stream.binance.com:9443/ws/<stream>

Supported streams:
  - Mini-ticker:    <symbol>@miniTicker   — price, 24h stats, ~every 1s
  - Trade stream:   <symbol>@trade        — every trade, ~ms latency
  - Book ticker:    <symbol>@bookTicker   — best bid/ask
  - Kline stream:   <symbol>@kline_<int>  — OHLCV per interval
  - Combined feed:  /stream?streams=...   — multiple in one connection

Architecture:
  - Runs in a background daemon thread
  - Auto-reconnects with exponential back-off (max 30s)
  - Thread-safe price cache + callback registry
  - Feeds directly into RealTimeEngine EventBus on update

Usage:
    ws = get_binance_ws()
    ws.subscribe("BTCUSDT", my_handler)   # optional per-symbol callback
    ws.start(["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"])
    price = ws.get_price("BTCUSDT")
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Callable

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

WS_BASE_URL   = "wss://stream.binance.com:9443"
WS_STREAM_URL = f"{WS_BASE_URL}/stream?streams="
PING_INTERVAL = 20        # seconds between pings
RECONNECT_BASE = 2.0      # initial reconnect delay (doubles on each retry)
RECONNECT_MAX  = 30.0     # cap reconnect delay
MAX_RECONNECTS = 50       # give up after this many consecutive failures

# Symbols to monitor by default
DEFAULT_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "AVAXUSDT", "ADAUSDT"]


# ── Data class ────────────────────────────────────────────────────────────────

class LiveTicker:
    """Snapshot of a live price from WebSocket."""
    __slots__ = ("symbol", "price", "bid", "ask", "volume_24h",
                 "change_pct_24h", "high_24h", "low_24h", "ts", "latency_ms")

    def __init__(
        self,
        symbol: str,
        price: float,
        bid: float = 0.0,
        ask: float = 0.0,
        volume_24h: float = 0.0,
        change_pct_24h: float = 0.0,
        high_24h: float = 0.0,
        low_24h: float = 0.0,
        ts: float = 0.0,
        latency_ms: float = 0.0,
    ) -> None:
        self.symbol        = symbol
        self.price         = price
        self.bid           = bid
        self.ask           = ask
        self.volume_24h    = volume_24h
        self.change_pct_24h = change_pct_24h
        self.high_24h      = high_24h
        self.low_24h       = low_24h
        self.ts            = ts or time.time()
        self.latency_ms    = latency_ms

    @property
    def spread(self) -> float:
        if self.bid > 0 and self.ask > 0:
            return self.ask - self.bid
        return 0.0

    @property
    def spread_pct(self) -> float:
        if self.bid > 0 and self.price > 0:
            return self.spread / self.price * 100
        return 0.0

    @property
    def age_ms(self) -> float:
        return round((time.time() - self.ts) * 1000, 1)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol":         self.symbol,
            "price":          self.price,
            "bid":            self.bid,
            "ask":            self.ask,
            "spread_pct":     round(self.spread_pct, 5),
            "volume_24h":     self.volume_24h,
            "change_pct_24h": round(self.change_pct_24h, 4),
            "high_24h":       self.high_24h,
            "low_24h":        self.low_24h,
            "ts":             self.ts,
            "age_ms":         self.age_ms,
            "latency_ms":     round(self.latency_ms, 2),
        }


# ── WebSocket connection manager ──────────────────────────────────────────────

class BinanceWebSocketAdapter:
    """
    Threading-based WebSocket client for Binance public streams.
    Maintains one combined stream for all subscribed symbols.
    """

    def __init__(self) -> None:
        self._tickers:     dict[str, LiveTicker] = {}
        self._callbacks:   dict[str, list[Callable]] = {}  # symbol → handlers
        self._global_cbs:  list[Callable] = []              # fires on any update
        self._lock         = threading.RLock()
        self._ws           = None
        self._thread:      threading.Thread | None = None
        self._running      = False
        self._symbols:     list[str] = []
        self._reconnect_n  = 0
        self._last_msg_ts  = 0.0
        self._msg_count    = 0
        self._error_count  = 0
        self._started_at   = 0.0

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self, symbols: list[str] | None = None) -> None:
        """Start streaming. Idempotent — call multiple times safely."""
        if self._running:
            return
        self._symbols  = [s.upper() for s in (symbols or DEFAULT_SYMBOLS)]
        self._running  = True
        self._started_at = time.time()
        self._thread = threading.Thread(
            target=self._run_loop,
            daemon=True,
            name="binance-ws",
        )
        self._thread.start()
        logger.info("BinanceWS started for %d symbols: %s", len(self._symbols), self._symbols)

    def stop(self) -> None:
        """Gracefully stop the WebSocket thread."""
        self._running = False
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass

    def subscribe(self, symbol: str, handler: Callable[[LiveTicker], None]) -> None:
        """Register a callback for a specific symbol's price updates."""
        sym = symbol.upper()
        with self._lock:
            self._callbacks.setdefault(sym, [])
            if handler not in self._callbacks[sym]:
                self._callbacks[sym].append(handler)

    def subscribe_all(self, handler: Callable[[LiveTicker], None]) -> None:
        """Register a callback that fires on ANY symbol update."""
        with self._lock:
            if handler not in self._global_cbs:
                self._global_cbs.append(handler)

    def unsubscribe(self, symbol: str, handler: Callable) -> None:
        sym = symbol.upper()
        with self._lock:
            if sym in self._callbacks and handler in self._callbacks[sym]:
                self._callbacks[sym].remove(handler)

    def get_price(self, symbol: str) -> float | None:
        with self._lock:
            t = self._tickers.get(symbol.upper())
            return t.price if t else None

    def get_ticker(self, symbol: str) -> LiveTicker | None:
        with self._lock:
            return self._tickers.get(symbol.upper())

    def get_all_tickers(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {sym: t.to_dict() for sym, t in self._tickers.items()}

    def get_status(self) -> dict[str, Any]:
        uptime = round(time.time() - self._started_at, 1) if self._started_at else 0
        fresh  = {s: round((time.time() - t.ts) * 1000, 0)
                  for s, t in self._tickers.items()}
        return {
            "running":        self._running,
            "symbols":        self._symbols,
            "ticker_count":   len(self._tickers),
            "msg_count":      self._msg_count,
            "error_count":    self._error_count,
            "reconnect_n":    self._reconnect_n,
            "uptime_s":       uptime,
            "last_msg_age_ms": round((time.time() - self._last_msg_ts) * 1000, 0) if self._last_msg_ts else None,
            "freshness_ms":   fresh,
        }

    # ── Internal loop ─────────────────────────────────────────────────────────

    def _run_loop(self) -> None:
        """Main reconnect loop. Runs in daemon thread."""
        delay = RECONNECT_BASE
        while self._running:
            try:
                self._connect_and_stream()
                delay = RECONNECT_BASE  # reset on clean run
            except Exception as exc:
                self._error_count += 1
                self._reconnect_n += 1
                if self._reconnect_n >= MAX_RECONNECTS:
                    logger.error("BinanceWS: max reconnects reached, stopping")
                    self._running = False
                    break
                logger.warning("BinanceWS reconnect #%d in %.0fs: %s", self._reconnect_n, delay, exc)
                time.sleep(delay)
                delay = min(delay * 1.5, RECONNECT_MAX)

    def _build_url(self) -> str:
        """Build combined stream URL for all subscribed symbols."""
        streams = []
        for sym in self._symbols:
            s = sym.lower()
            streams.append(f"{s}@miniTicker")    # price + 24h stats
            streams.append(f"{s}@bookTicker")    # best bid/ask
        return WS_STREAM_URL + "/".join(streams)

    def _connect_and_stream(self) -> None:
        """Open WebSocket and read messages until disconnected."""
        try:
            import websocket
        except ImportError:
            logger.error(
                "websocket-client not installed. Run: pip install websocket-client"
            )
            # Fall back to REST polling mode
            self._rest_fallback_loop()
            return

        url = self._build_url()
        logger.debug("BinanceWS connecting: %s", url[:120])

        ws = websocket.WebSocketApp(
            url,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )
        self._ws = ws
        # ping_interval keeps connection alive; run_forever blocks until close
        ws.run_forever(ping_interval=PING_INTERVAL, ping_timeout=10)

    def _rest_fallback_loop(self) -> None:
        """
        Poll REST API if websocket-client is unavailable.
        Feeds same LiveTicker objects so callers work identically.
        """
        logger.info("BinanceWS: REST fallback mode (2s poll)")
        try:
            from adapters.binance_public import BinancePublicAdapter
            adapter = BinancePublicAdapter()
        except Exception as exc:
            logger.error("BinanceWS REST fallback init failed: %s", exc)
            return

        while self._running:
            t0 = time.monotonic()
            for sym in self._symbols:
                try:
                    res = adapter.get_24h_ticker(sym)
                    if res.get("ok"):
                        d   = res["data"]
                        now = time.time()
                        ticker = LiveTicker(
                            symbol=sym,
                            price=float(d.get("last_price", 0)),
                            bid=float(d.get("bid_price", 0)),
                            ask=float(d.get("ask_price", 0)),
                            volume_24h=float(d.get("quote_volume_24h", 0)),
                            change_pct_24h=float(d.get("price_change_pct_24h", 0)),
                            high_24h=float(d.get("high_24h", 0)),
                            low_24h=float(d.get("low_24h", 0)),
                            ts=now,
                            latency_ms=round((time.monotonic() - t0) * 1000, 1),
                        )
                        self._update_ticker(ticker)
                except Exception:
                    pass
            elapsed = time.monotonic() - t0
            time.sleep(max(0.1, 2.0 - elapsed))

    # ── WebSocket handlers ────────────────────────────────────────────────────

    def _on_open(self, ws) -> None:
        self._reconnect_n = 0
        logger.info("BinanceWS connected — streaming %d symbols", len(self._symbols))

    def _on_message(self, ws, raw: str) -> None:
        t_recv = time.time()
        self._last_msg_ts = t_recv
        self._msg_count  += 1
        try:
            envelope = json.loads(raw)
            # Combined stream wraps data: {"stream": "btcusdt@miniTicker", "data": {...}}
            data   = envelope.get("data", envelope)
            stream = envelope.get("stream", "")

            if "@miniTicker" in stream:
                self._handle_mini_ticker(data, t_recv)
            elif "@bookTicker" in stream:
                self._handle_book_ticker(data, t_recv)
        except Exception as exc:
            logger.debug("BinanceWS parse error: %s | raw=%s", exc, raw[:200])

    def _on_error(self, ws, error) -> None:
        self._error_count += 1
        logger.debug("BinanceWS error: %s", error)

    def _on_close(self, ws, code, msg) -> None:
        logger.debug("BinanceWS closed: code=%s msg=%s", code, msg)

    # ── Message handlers ──────────────────────────────────────────────────────

    def _handle_mini_ticker(self, data: dict, recv_ts: float) -> None:
        """
        miniTicker payload:
          e: event type, E: event time (ms), s: symbol,
          c: close, o: open, h: high, l: low, v: base vol, q: quote vol
        """
        try:
            sym    = data["s"]
            price  = float(data["c"])
            open_p = float(data["o"])
            high   = float(data["h"])
            low    = float(data["l"])
            vol    = float(data.get("q", 0))          # quote volume
            event_ts = data.get("E", 0) / 1000.0      # ms → s
            latency  = (recv_ts - event_ts) * 1000 if event_ts > 0 else 0

            change_pct = ((price - open_p) / open_p * 100) if open_p > 0 else 0.0

            with self._lock:
                existing = self._tickers.get(sym)
                bid = existing.bid if existing else 0.0
                ask = existing.ask if existing else 0.0

            ticker = LiveTicker(
                symbol=sym, price=price, bid=bid, ask=ask,
                volume_24h=vol, change_pct_24h=change_pct,
                high_24h=high, low_24h=low,
                ts=recv_ts, latency_ms=latency,
            )
            self._update_ticker(ticker)
        except (KeyError, ValueError, ZeroDivisionError):
            pass

    def _handle_book_ticker(self, data: dict, recv_ts: float) -> None:
        """
        bookTicker payload:
          u: order book update id, s: symbol, b: best bid, B: bid qty, a: ask, A: ask qty
        """
        try:
            sym = data["s"]
            bid = float(data["b"])
            ask = float(data["a"])
            with self._lock:
                if sym in self._tickers:
                    self._tickers[sym].bid = bid
                    self._tickers[sym].ask = ask
        except (KeyError, ValueError):
            pass

    def _update_ticker(self, ticker: LiveTicker) -> None:
        """Store ticker and fire callbacks."""
        with self._lock:
            self._tickers[ticker.symbol] = ticker
            symbol_cbs = list(self._callbacks.get(ticker.symbol, []))
            global_cbs = list(self._global_cbs)

        # Fire callbacks outside lock to prevent deadlock
        for cb in symbol_cbs + global_cbs:
            try:
                cb(ticker)
            except Exception as exc:
                logger.debug("BinanceWS callback error: %s", exc)

        # Emit into RealTimeEngine if available
        try:
            from services.realtime_engine import get_realtime_engine, EVENT_PRICE_UPDATE
            from services.realtime_engine import PriceBar
            bar = PriceBar(
                symbol=ticker.symbol,
                price=ticker.price,
                bid=ticker.bid,
                ask=ticker.ask,
                volume=ticker.volume_24h,
                change_pct=ticker.change_pct_24h,
                ts=ticker.ts,
                source="binance_ws",
            )
            get_realtime_engine().bus.emit(EVENT_PRICE_UPDATE, bar)
        except Exception:
            pass


# ── Smart subscription helpers ────────────────────────────────────────────────

def build_alert_handler(
    symbol: str,
    threshold_pct: float,
    on_spike: Callable[[LiveTicker, float], None],
) -> Callable:
    """
    Returns a callback that fires `on_spike(ticker, change_since_attach)`
    when price moves more than threshold_pct since the handler was attached.
    """
    state = {"baseline": None}

    def handler(t: LiveTicker) -> None:
        if state["baseline"] is None:
            state["baseline"] = t.price
            return
        if state["baseline"] == 0:
            return
        move = (t.price - state["baseline"]) / state["baseline"] * 100
        if abs(move) >= threshold_pct:
            on_spike(t, move)
            state["baseline"] = t.price   # reset

    return handler


# ── Global singleton ──────────────────────────────────────────────────────────
_ws: BinanceWebSocketAdapter | None = None
_ws_lock = threading.Lock()


def get_binance_ws(symbols: list[str] | None = None) -> BinanceWebSocketAdapter:
    """
    Get (or create + start) the global Binance WebSocket adapter.
    Thread-safe. Starts streaming immediately on first call.
    """
    global _ws
    with _ws_lock:
        if _ws is None:
            _ws = BinanceWebSocketAdapter()
            _ws.start(symbols or DEFAULT_SYMBOLS)
    return _ws
