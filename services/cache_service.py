"""
API Response Cache Service — TTL-based in-memory cache with optional Redis backend.

Reduces redundant API calls, improves response time, and respects rate limits.
Uses a two-tier architecture:
  L1 — In-memory dict (always available, fastest)
  L2 — Redis (optional, shared across workers, survives restarts)

TTL defaults are tuned for trading data freshness requirements:
  - Price data:     30s  (frequent updates needed)
  - News/headlines: 5min
  - Market metrics: 2min
  - Heavy reports:  15min
  - Static lookups: 1h
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any


# ── Default TTLs by data category (seconds) ──────────────────────────────────
DEFAULT_TTLS: dict[str, float] = {
    "price":           30.0,
    "ticker_24h":      60.0,
    "orderbook":       10.0,
    "klines":          60.0,
    "funding_rate":    60.0,
    "open_interest":   120.0,
    "fear_greed":      300.0,
    "news":            300.0,
    "sentiment":       300.0,
    "macro":           600.0,
    "tvl":             300.0,
    "yields":          300.0,
    "defi":            300.0,
    "sports":          600.0,
    "weather":         600.0,
    "forex":           120.0,
    "world_markets":   120.0,
    "health":          60.0,
    "static":          3600.0,
    "default":         120.0,
}


class CacheEntry:
    __slots__ = ("value", "expires_at", "created_at", "hits")

    def __init__(self, value: Any, ttl: float) -> None:
        now = time.monotonic()
        self.value      = value
        self.created_at = now
        self.expires_at = now + ttl
        self.hits       = 0

    @property
    def is_expired(self) -> bool:
        return time.monotonic() > self.expires_at

    @property
    def age_s(self) -> float:
        return time.monotonic() - self.created_at

    @property
    def ttl_remaining_s(self) -> float:
        return max(0.0, self.expires_at - time.monotonic())


class InMemoryCache:
    """
    Fast in-memory TTL cache.
    Auto-evicts expired entries on access and periodic cleanup.
    """

    def __init__(self, max_size: int = 2048) -> None:
        self._store: dict[str, CacheEntry] = {}
        self._max_size = max_size
        self._hits   = 0
        self._misses = 0
        self._last_cleanup = time.monotonic()

    def get(self, key: str) -> tuple[bool, Any]:
        """Returns (hit: bool, value: Any)."""
        entry = self._store.get(key)
        if entry is None:
            self._misses += 1
            return False, None
        if entry.is_expired:
            del self._store[key]
            self._misses += 1
            return False, None
        entry.hits += 1
        self._hits  += 1
        return True, entry.value

    def set(self, key: str, value: Any, ttl: float) -> None:
        if len(self._store) >= self._max_size:
            self._evict_expired()
            # If still too large, evict LRU
            if len(self._store) >= self._max_size:
                oldest = min(self._store, key=lambda k: self._store[k].created_at)
                del self._store[oldest]
        self._store[key] = CacheEntry(value, ttl)

        # Periodic cleanup (every 5 min)
        if time.monotonic() - self._last_cleanup > 300:
            self._evict_expired()

    def invalidate(self, key: str) -> bool:
        return bool(self._store.pop(key, None))

    def invalidate_prefix(self, prefix: str) -> int:
        keys = [k for k in self._store if k.startswith(prefix)]
        for k in keys:
            del self._store[k]
        return len(keys)

    def clear(self) -> None:
        self._store.clear()

    def _evict_expired(self) -> int:
        expired = [k for k, v in self._store.items() if v.is_expired]
        for k in expired:
            del self._store[k]
        self._last_cleanup = time.monotonic()
        return len(expired)

    @property
    def stats(self) -> dict[str, Any]:
        total = self._hits + self._misses
        return {
            "size":      len(self._store),
            "max_size":  self._max_size,
            "hits":      self._hits,
            "misses":    self._misses,
            "hit_rate":  round(self._hits / total, 4) if total else 0.0,
            "total_reqs": total,
        }

    def get_keys(self) -> list[str]:
        return list(self._store.keys())


class CacheService:
    """
    Two-tier cache: L1 (in-memory) + optional L2 (Redis).
    Provides typed helper methods for common data categories.
    """

    def __init__(self, redis_url: str | None = None, max_memory_entries: int = 2048) -> None:
        self._l1    = InMemoryCache(max_size=max_memory_entries)
        self._redis = None
        if redis_url:
            try:
                import redis
                self._redis = redis.from_url(redis_url, socket_connect_timeout=2, decode_responses=True)
                self._redis.ping()
            except Exception:
                self._redis = None   # Redis unavailable — fall back to L1 only

    def get(self, key: str) -> tuple[bool, Any]:
        # L1 first
        hit, val = self._l1.get(key)
        if hit:
            return True, val
        # L2 (Redis)
        if self._redis:
            try:
                raw = self._redis.get(key)
                if raw is not None:
                    val = json.loads(raw)
                    # Warm L1
                    self._l1.set(key, val, ttl=30.0)
                    return True, val
            except Exception:
                pass
        return False, None

    def set(self, key: str, value: Any, ttl: float) -> None:
        self._l1.set(key, value, ttl)
        if self._redis:
            try:
                self._redis.setex(key, int(ttl), json.dumps(value, default=str))
            except Exception:
                pass

    def invalidate(self, key: str) -> None:
        self._l1.invalidate(key)
        if self._redis:
            try:
                self._redis.delete(key)
            except Exception:
                pass

    def invalidate_prefix(self, prefix: str) -> None:
        self._l1.invalidate_prefix(prefix)
        if self._redis:
            try:
                keys = self._redis.keys(f"{prefix}*")
                if keys:
                    self._redis.delete(*keys)
            except Exception:
                pass

    def cached(self, key: str, ttl: float, fn, *args, **kwargs) -> Any:
        """
        Cache-aside pattern: get from cache or compute and store.
        Usage: result = cache.cached("key", 60.0, my_func, arg1, arg2)
        """
        hit, val = self.get(key)
        if hit:
            return val
        val = fn(*args, **kwargs)
        self.set(key, val, ttl)
        return val

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "l1":     self._l1.stats,
            "redis":  "connected" if self._redis else "not_configured",
        }

    # ── Typed helpers ─────────────────────────────────────────────────────────

    def get_price(self, symbol: str) -> tuple[bool, Any]:
        return self.get(f"price:{symbol}")

    def set_price(self, symbol: str, data: Any) -> None:
        self.set(f"price:{symbol}", data, DEFAULT_TTLS["price"])

    def get_news(self, source: str) -> tuple[bool, Any]:
        return self.get(f"news:{source}")

    def set_news(self, source: str, data: Any) -> None:
        self.set(f"news:{source}", data, DEFAULT_TTLS["news"])

    def get_fear_greed(self) -> tuple[bool, Any]:
        return self.get("fear_greed:current")

    def set_fear_greed(self, data: Any) -> None:
        self.set("fear_greed:current", data, DEFAULT_TTLS["fear_greed"])

    def get_health(self) -> tuple[bool, Any]:
        return self.get("health:full_report")

    def set_health(self, data: Any) -> None:
        self.set("health:full_report", data, DEFAULT_TTLS["health"])


def make_cache_key(*parts: Any) -> str:
    """Create a deterministic cache key from multiple parts."""
    raw = ":".join(str(p) for p in parts)
    if len(raw) > 200:
        return hashlib.md5(raw.encode()).hexdigest()
    return raw


# ── Global singleton ──────────────────────────────────────────────────────────
_cache: CacheService | None = None


def get_cache() -> CacheService:
    """Get or create the global cache service instance."""
    global _cache
    if _cache is None:
        import os
        redis_url = os.getenv("REDIS_URL", "")
        _cache = CacheService(redis_url or None)
    return _cache
