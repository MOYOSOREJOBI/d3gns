from __future__ import annotations

"""
Centralized rate/quota budgeting for all free-tier APIs.

Design:
- In-process singleton — shared across all bots and adapters in the same server process.
- Thread-safe per-platform counters with daily + per-minute caps.
- Accepts real remaining-credit values pushed from API response headers.
- Provides backoff helpers: can_request(), wait_if_needed().
- Never crashes: if a platform is unknown it silently passes.

Free-tier defaults:
  oddsapi            450 req/day  (The Odds API free tier)
  kalshi_public      2 000 req/day  (generous estimate, no published hard limit)
  polymarket_public  5 000 req/day  (CLOB public, generous estimate)
  sportsdataio_trial   200 req/day  (trial key)
  betfair_delayed      500 req/day  (delayed dev key)

Per-minute caps prevent burst hammering regardless of daily headroom.
"""

import threading
import time
from typing import Any


# ── Per-platform caps ─────────────────────────────────────────────────────────
_DEFAULT_CAPS: dict[str, dict[str, int]] = {
    "oddsapi":            {"daily": 450,  "per_minute": 8},
    "kalshi_public":      {"daily": 2000, "per_minute": 20},
    "kalshi_demo":        {"daily": 500,  "per_minute": 10},
    "polymarket_public":  {"daily": 5000, "per_minute": 40},
    "betfair_delayed":    {"daily": 500,  "per_minute": 10},
    "sportsdataio_trial": {"daily": 200,  "per_minute": 4},
}


class _PlatformBudget:
    """Thread-safe per-platform budget entry."""

    def __init__(self, daily_cap: int, per_minute_cap: int):
        self.daily_cap = daily_cap
        self.per_minute_cap = per_minute_cap
        self._lock = threading.RLock()
        self._day_used: int = 0
        self._minute_used: int = 0
        self._day_reset_at: float = time.time() + 86400.0
        self._minute_reset_at: float = time.time() + 60.0
        # Remote remaining from API headers (None = unknown)
        self._remote_remaining: int | None = None
        self._remote_used: int | None = None
        self._last_flushed_at: float | None = None

    # ── Public interface ─────────────────────────────────────────────────────

    def can_request(self) -> bool:
        with self._lock:
            self._tick()
            if self._remote_remaining is not None and self._remote_remaining <= 0:
                return False
            return self._day_used < self.daily_cap and self._minute_used < self.per_minute_cap

    def record(self, remote_remaining: int | None = None, remote_used: int | None = None) -> None:
        with self._lock:
            self._tick()
            self._day_used += 1
            self._minute_used += 1
            if remote_remaining is not None:
                self._remote_remaining = int(remote_remaining)
            if remote_used is not None:
                self._remote_used = int(remote_used)

    def status(self) -> dict[str, Any]:
        with self._lock:
            self._tick()
            local_day_remaining = max(0, self.daily_cap - self._day_used)
            local_minute_remaining = max(0, self.per_minute_cap - self._minute_used)
            exhausted = not (
                (self._remote_remaining is None or self._remote_remaining > 0)
                and local_day_remaining > 0
                and local_minute_remaining > 0
            )
            return {
                "daily_cap": self.daily_cap,
                # Canonical names
                "daily_used": self._day_used,
                "daily_remaining": local_day_remaining,
                "minute_cap": self.per_minute_cap,
                "minute_used": self._minute_used,
                "minute_remaining": local_minute_remaining,
                # Legacy aliases kept for compatibility
                "daily_used_local": self._day_used,
                "daily_remaining_local": local_day_remaining,
                "per_minute_cap": self.per_minute_cap,
                "minute_used_local": self._minute_used,
                "minute_remaining_local": local_minute_remaining,
                "remote_remaining": self._remote_remaining,
                "remote_used": self._remote_used,
                "exhausted": exhausted,
                "last_flushed_at": self._last_flushed_at,
                "day_reset_in_s": max(0.0, round(self._day_reset_at - time.time(), 1)),
                "minute_reset_in_s": max(0.0, round(self._minute_reset_at - time.time(), 1)),
            }

    def set_day_used(self, value: int) -> None:
        with self._lock:
            self._day_used = max(0, int(value))

    def set_minute_used(self, value: int) -> None:
        with self._lock:
            self._minute_used = max(0, int(value))

    def mark_flushed(self) -> None:
        with self._lock:
            self._last_flushed_at = time.time()

    def restore_snapshot(
        self,
        *,
        day_used: int,
        minute_used: int,
        remote_remaining: int | None,
        remote_used: int | None,
        same_day: bool,
    ) -> None:
        with self._lock:
            if remote_remaining is not None:
                self._remote_remaining = int(remote_remaining)
            if remote_used is not None:
                self._remote_used = int(remote_used)
            if same_day:
                self._day_used = max(0, int(day_used))
                self._minute_used = max(0, int(minute_used))

    # ── Internal ─────────────────────────────────────────────────────────────

    def _tick(self) -> None:
        """Reset counters if time windows have elapsed. Call under lock."""
        now = time.time()
        if now >= self._day_reset_at:
            self._day_used = 0
            self._day_reset_at = now + 86400.0
        if now >= self._minute_reset_at:
            self._minute_used = 0
            self._minute_reset_at = now + 60.0


# ── Budgeter ─────────────────────────────────────────────────────────────────

class QuotaBudgeter:
    """
    Per-platform rate/quota budget manager.

    Usage:
        from services.quota_budgeter import get_budget
        budget = get_budget()

        if not budget.can_request("oddsapi"):
            return {"error": "quota_exhausted", ...}

        # ... make request ...

        budget.record("oddsapi", remote_remaining=int(resp.headers.get("x-requests-remaining", -1)))
    """

    def __init__(self) -> None:
        self._budgets: dict[str, _PlatformBudget] = {
            platform: _PlatformBudget(caps["daily"], caps["per_minute"])
            for platform, caps in _DEFAULT_CAPS.items()
        }

    def can_request(self, platform: str) -> bool:
        """Return True if this platform has budget remaining for a request."""
        entry = self._budgets.get(platform)
        return entry.can_request() if entry else True

    def record(
        self,
        platform: str,
        cost: int = 1,
        remote_remaining: int | None = None,
        remote_used: int | None = None,
    ) -> None:
        """Record that a request was made. Pass remote header values when available."""
        entry = self._budgets.get(platform)
        if entry is None:
            # Auto-create a generous budget for unknown platforms
            entry = _PlatformBudget(daily_cap=10_000, per_minute_cap=100)
            self._budgets[platform] = entry
        for _ in range(max(1, cost)):
            entry.record(remote_remaining=remote_remaining, remote_used=remote_used)

    def set_caps(self, platform: str, daily: int, per_minute: int) -> None:
        """Override caps for a platform. Useful for testing and runtime tuning."""
        entry = self._budgets.get(platform)
        if entry is None:
            entry = _PlatformBudget(daily_cap=daily, per_minute_cap=per_minute)
            self._budgets[platform] = entry
        else:
            with entry._lock:
                entry.daily_cap = daily
                entry.per_minute_cap = per_minute

    def record_from_oddsapi_headers(self, platform: str, headers: Any) -> None:
        """Convenience: extract The Odds API quota headers and record them."""
        try:
            remaining = headers.get("x-requests-remaining")
            used = headers.get("x-requests-used")
            self.record(
                platform,
                remote_remaining=int(remaining) if remaining is not None else None,
                remote_used=int(used) if used is not None else None,
            )
        except Exception:
            self.record(platform)

    def load_from_db(self, db: Any) -> None:
        """
        Load the most recent quota snapshot per platform from DB and update
        remote_remaining / remote_used counters. Caps are not overwritten.
        Call once at startup to restore last-known API state.
        """
        if db is None:
            return
        try:
            rows = db.get_quota_history(platform=None, limit=500)
            # Keep only the most recent row per platform
            seen: dict[str, dict] = {}
            for row in rows:
                p = row.get("platform", "")
                if p and p not in seen:
                    seen[p] = row
            for platform, row in seen.items():
                entry = self._budgets.get(platform)
                if entry is None:
                    continue
                with entry._lock:
                    payload = {}
                    try:
                        import json
                        payload = json.loads(row.get("payload_json") or "{}")
                    except Exception:
                        pass
                    remote_remaining = payload.get("remote_remaining")
                    remote_used = payload.get("remote_used")
                    ts = str(row.get("ts") or "")
                    today = time.strftime("%Y-%m-%d", time.gmtime())
                    entry.restore_snapshot(
                        day_used=int(row.get("daily_used", 0)),
                        minute_used=int(row.get("minute_used", 0)),
                        remote_remaining=remote_remaining,
                        remote_used=remote_used,
                        same_day=ts.startswith(today),
                    )
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning(f"quota_budgeter.load_from_db failed: {exc}")

    def flush_to_db(self, db: Any) -> None:
        """
        Persist current quota status for all tracked platforms to DB.
        Call periodically (e.g. every 5 minutes via scheduler) to survive restarts.
        """
        if db is None:
            return
        try:
            for platform, entry in self._budgets.items():
                s = entry.status()
                db.save_quota_snapshot(platform, s)
                entry.mark_flushed()
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning(f"quota_budgeter.flush_to_db failed: {exc}")

    def status_all(self) -> dict[str, Any]:
        """Return status dict for all tracked platforms."""
        return {
            platform: entry.status()
            for platform, entry in self._budgets.items()
        }

    def status(self, platform: str) -> dict[str, Any]:
        """Return status for one platform. Returns empty-cap dict if platform unknown."""
        entry = self._budgets.get(platform)
        if entry is None:
            return {
                "daily_cap": 0, "daily_used": 0, "daily_remaining": 0,
                "minute_cap": 0, "minute_used": 0, "minute_remaining": 0,
                "exhausted": False, "remote_remaining": None, "remote_used": None,
            }
        return entry.status()

    def degraded_reason(self, platform: str) -> str:
        """Return a human-readable reason if budget is exhausted, else empty string."""
        entry = self._budgets.get(platform)
        if not entry:
            return ""
        s = entry.status()
        if s.get("remote_remaining") == 0:
            return f"{platform} API quota exhausted (remote reports 0 remaining). Reset in {s['day_reset_in_s']}s."
        if s["daily_remaining_local"] <= 0:
            return f"{platform} daily budget cap ({s['daily_cap']}) reached locally. Reset in {s['day_reset_in_s']}s."
        if s["minute_remaining_local"] <= 0:
            return f"{platform} per-minute cap ({s['per_minute_cap']}) reached. Reset in {s['minute_reset_in_s']}s."
        return ""


# ── Module-level singleton ────────────────────────────────────────────────────

_SINGLETON: QuotaBudgeter | None = None
_singleton_lock = threading.Lock()


def get_budget() -> QuotaBudgeter:
    """Return the process-wide QuotaBudgeter singleton."""
    global _SINGLETON
    if _SINGLETON is None:
        with _singleton_lock:
            if _SINGLETON is None:
                _SINGLETON = QuotaBudgeter()
    return _SINGLETON


# Convenience module-level functions that delegate to the singleton

def can_request(platform: str) -> bool:
    return get_budget().can_request(platform)


def record(platform: str, cost: int = 1, remote_remaining: int | None = None, remote_used: int | None = None) -> None:
    get_budget().record(platform, cost=cost, remote_remaining=remote_remaining, remote_used=remote_used)


def status_all() -> dict[str, Any]:
    return get_budget().status_all()


def status(platform: str) -> dict[str, Any] | None:
    return get_budget().status(platform)
