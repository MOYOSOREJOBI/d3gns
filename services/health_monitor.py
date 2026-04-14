"""
Live Health Monitor — current-state checker for all adapters, bots, and services.

Runs healthchecks concurrently (ThreadPoolExecutor) and returns a unified
status report with latency, truth labels, and degraded reasons.

Usage:
    from services.health_monitor import run_full_health_check, quick_status
    report = run_full_health_check()
    status = quick_status()   # fast summary — green/amber/red
"""
from __future__ import annotations

import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeout
from typing import Any


# ── Adapter registry ──────────────────────────────────────────────────────────
# Each entry: (adapter_class_path, display_name, tier, is_free)
ADAPTER_REGISTRY: list[tuple[str, str, str, bool]] = [
    # Free / no-auth
    ("adapters.coingecko.CoinGeckoAdapter",          "CoinGecko",          "data",     True),
    ("adapters.coincap.CoinCapAdapter",               "CoinCap",            "data",     True),
    ("adapters.fear_greed.FearGreedAdapter",          "Fear & Greed",       "signal",   True),
    ("adapters.coinpaprika.CoinPaprikaAdapter",       "CoinPaprika",        "data",     True),
    ("adapters.defillama.DeFiLlamaAdapter",           "DeFiLlama",          "defi",     True),
    ("adapters.metaculus.MetaculusAdapter",           "Metaculus",          "predict",  True),
    ("adapters.free_crypto_news.FreeCryptoNewsAdapter", "Free Crypto News", "news",     True),
    ("adapters.hackernews.HackerNewsAdapter",         "HackerNews",         "news",     True),
    ("adapters.reddit_public.RedditPublicAdapter",    "Reddit Public",      "social",   True),
    ("adapters.wsb_sentiment.WSBSentimentAdapter",    "WSB Sentiment",      "social",   True),
    ("adapters.spaceflight_news.SpaceflightNewsAdapter", "Spaceflight News","news",     True),
    ("adapters.sec_edgar.SECEdgarAdapter",            "SEC EDGAR",          "macro",    True),
    ("adapters.thesportsdb.TheSportsDBAdapter",       "TheSportsDB",        "sports",   True),
    ("adapters.balldontlie.BallDontLieAdapter",       "BallDontLie",        "sports",   True),
    ("adapters.ergast_f1.ErgastF1Adapter",            "Ergast F1",          "sports",   True),
    ("adapters.open_meteo.OpenMeteoAdapter",          "Open-Meteo",         "weather",  True),
    ("adapters.noaa_nws.NOAANWSAdapter",              "NOAA/NWS",           "weather",  True),
    # Key-optional (degraded without, fully functional with key)
    ("adapters.newsapi.NewsAPIAdapter",               "NewsAPI",            "news",     False),
    ("adapters.gnews.GNewsAdapter",                   "GNews",              "news",     False),
    ("adapters.currents_api.CurrentsAPIAdapter",      "Currents API",       "news",     False),
    ("adapters.fred_api.FREDAdapter",                 "FRED (Fed)",         "macro",    False),
    ("adapters.alpha_vantage.AlphaVantageAdapter",    "Alpha Vantage",      "finance",  False),
    ("adapters.coinmarketcap.CoinMarketCapAdapter",   "CoinMarketCap",      "data",     False),
    # Platform adapters
    ("adapters.polymarket_public.PolymarketPublicAdapter", "Polymarket",    "platform", False),
    ("adapters.kalshi_public.KalshiPublicAdapter",    "Kalshi",             "platform", False),
    # Wave 3 — no auth
    ("adapters.binance_public.BinancePublicAdapter",  "Binance Public",     "data",     True),
    ("adapters.coinglass.CoinGlassAdapter",           "CoinGlass",          "defi",     True),
    ("adapters.forex_rates.ForexRatesAdapter",        "Forex Rates",        "finance",  True),
    ("adapters.world_markets.WorldMarketsAdapter",    "World Markets",      "finance",  True),
]


# ── Services registry ─────────────────────────────────────────────────────────
SERVICE_REGISTRY: list[tuple[str, str]] = [
    ("services.vader_sentiment",       "VADER Sentiment"),
    ("services.kelly_sizer",           "Kelly Sizer"),
    ("services.technical_indicators",  "Technical Indicators"),
    ("services.signal_aggregator",     "Signal Aggregator"),
    ("services.backtester",            "Backtester"),
]


# ── Core check functions ──────────────────────────────────────────────────────

def _check_adapter(class_path: str, display_name: str, tier: str, is_free: bool) -> dict[str, Any]:
    t0 = time.monotonic()
    result: dict[str, Any] = {
        "name":        display_name,
        "class_path":  class_path,
        "tier":        tier,
        "is_free":     is_free,
        "status":      "unknown",
        "configured":  False,
        "latency_ms":  None,
        "degraded_reason": "",
        "auth_truth":  "missing",
        "error":       None,
    }
    try:
        module_path, cls_name = class_path.rsplit(".", 1)
        import importlib
        mod = importlib.import_module(module_path)
        cls = getattr(mod, cls_name)
        adapter = cls()
        result["configured"] = adapter.is_configured()
        hc = adapter.healthcheck()
        latency_ms = round((time.monotonic() - t0) * 1000, 1)
        result["latency_ms"] = latency_ms
        result["auth_truth"] = hc.get("truth_labels", {}).get("auth_truth", "unknown")

        if hc.get("ok"):
            result["status"] = "ok" if not hc.get("degraded_reason") else "degraded"
            result["degraded_reason"] = hc.get("degraded_reason", "")
        else:
            result["status"] = "error"
            result["error"]  = hc.get("error", "healthcheck returned not-ok")
    except Exception as exc:
        result["latency_ms"] = round((time.monotonic() - t0) * 1000, 1)
        result["status"] = "import_error"
        result["error"]  = str(exc)
    return result


def _check_service(module_path: str, display_name: str) -> dict[str, Any]:
    t0 = time.monotonic()
    try:
        import importlib
        importlib.import_module(module_path)
        latency_ms = round((time.monotonic() - t0) * 1000, 1)
        return {"name": display_name, "status": "ok", "latency_ms": latency_ms, "error": None}
    except Exception as exc:
        return {"name": display_name, "status": "import_error",
                "latency_ms": round((time.monotonic() - t0) * 1000, 1), "error": str(exc)}


def _check_database() -> dict[str, Any]:
    """Check database connectivity."""
    try:
        import os
        db_url = os.getenv("DATABASE_URL", "")
        db_path = os.getenv("DB_PATH", "./bots.db")
        if db_url:
            # PostgreSQL
            import psycopg
            t0 = time.monotonic()
            with psycopg.connect(db_url, connect_timeout=3) as conn:
                conn.execute("SELECT 1")
            return {"name": "PostgreSQL", "status": "ok",
                    "latency_ms": round((time.monotonic() - t0) * 1000, 1), "type": "postgres"}
        elif db_path:
            import sqlite3
            t0 = time.monotonic()
            conn = sqlite3.connect(db_path, timeout=3)
            conn.execute("SELECT 1")
            conn.close()
            return {"name": "SQLite", "status": "ok",
                    "latency_ms": round((time.monotonic() - t0) * 1000, 1), "type": "sqlite",
                    "path": db_path}
        return {"name": "Database", "status": "not_configured", "latency_ms": None}
    except Exception as exc:
        return {"name": "Database", "status": "error", "error": str(exc), "latency_ms": None}


def _check_redis() -> dict[str, Any]:
    """Check Redis connectivity."""
    import os
    redis_url = os.getenv("REDIS_URL", "")
    if not redis_url:
        return {"name": "Redis", "status": "not_configured", "latency_ms": None}
    try:
        import redis as redis_lib
        t0 = time.monotonic()
        r = redis_lib.from_url(redis_url, socket_connect_timeout=2)
        r.ping()
        return {"name": "Redis", "status": "ok",
                "latency_ms": round((time.monotonic() - t0) * 1000, 1)}
    except Exception as exc:
        return {"name": "Redis", "status": "error", "error": str(exc), "latency_ms": None}


# ── Main health check ─────────────────────────────────────────────────────────

def run_full_health_check(
    timeout_per_adapter: float = 12.0,
    max_workers: int = 12,
) -> dict[str, Any]:
    """
    Run all healthchecks concurrently.
    Returns a full status report with per-adapter and aggregate metrics.
    """
    started_at = time.monotonic()

    adapter_results: list[dict[str, Any]] = []
    service_results: list[dict[str, Any]] = []

    # Adapters — concurrent
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(_check_adapter, cp, dn, tier, free): (cp, dn)
            for cp, dn, tier, free in ADAPTER_REGISTRY
        }
        for future in as_completed(futures, timeout=timeout_per_adapter * 2):
            try:
                adapter_results.append(future.result(timeout=timeout_per_adapter))
            except (FuturesTimeout, Exception) as exc:
                cp, dn = futures[future]
                adapter_results.append({
                    "name": dn, "class_path": cp, "status": "timeout",
                    "error": str(exc), "latency_ms": None,
                })

    # Services — fast, sequential
    for mod_path, name in SERVICE_REGISTRY:
        service_results.append(_check_service(mod_path, name))

    # Infrastructure
    db_result    = _check_database()
    redis_result = _check_redis()

    # Aggregate counts
    ok_count       = sum(1 for r in adapter_results if r.get("status") == "ok")
    degraded_count = sum(1 for r in adapter_results if r.get("status") == "degraded")
    error_count    = sum(1 for r in adapter_results if r.get("status") in ("error", "timeout", "import_error"))
    total          = len(adapter_results)
    configured_count = sum(1 for r in adapter_results if r.get("configured"))
    free_count     = sum(1 for r in adapter_results if r.get("is_free") and r.get("status") == "ok")

    # Overall system health
    if ok_count + degraded_count >= total * 0.7:
        system_status = "healthy"
    elif ok_count + degraded_count >= total * 0.4:
        system_status = "degraded"
    else:
        system_status = "critical"

    # Tier breakdown
    tier_summary: dict[str, dict[str, int]] = {}
    for r in adapter_results:
        t = r.get("tier", "other")
        if t not in tier_summary:
            tier_summary[t] = {"ok": 0, "degraded": 0, "error": 0}
        s = r.get("status", "error")
        if s == "ok":
            tier_summary[t]["ok"] += 1
        elif s == "degraded":
            tier_summary[t]["degraded"] += 1
        else:
            tier_summary[t]["error"] += 1

    elapsed_ms = round((time.monotonic() - started_at) * 1000, 1)

    return {
        "system_status":    system_status,
        "elapsed_ms":       elapsed_ms,
        "timestamp":        time.time(),
        "adapters": {
            "total":      total,
            "ok":         ok_count,
            "degraded":   degraded_count,
            "error":      error_count,
            "configured": configured_count,
            "free_live":  free_count,
            "results":    sorted(adapter_results, key=lambda x: x.get("name", "")),
        },
        "services": {
            "total":   len(service_results),
            "ok":      sum(1 for s in service_results if s.get("status") == "ok"),
            "results": service_results,
        },
        "infrastructure": {
            "database": db_result,
            "redis":    redis_result,
        },
        "tier_summary": tier_summary,
    }


def quick_status() -> dict[str, Any]:
    """
    Fast summary check — returns green/amber/red without making API calls.
    Checks imports and configuration only.
    """
    started = time.monotonic()
    statuses: dict[str, str] = {}
    errors: list[str] = []

    for mod_path, name in SERVICE_REGISTRY:
        try:
            import importlib
            importlib.import_module(mod_path)
            statuses[name] = "ok"
        except Exception as exc:
            statuses[name] = "error"
            errors.append(f"{name}: {exc}")

    for class_path, display_name, tier, is_free in ADAPTER_REGISTRY:
        try:
            module_path, cls_name = class_path.rsplit(".", 1)
            import importlib
            mod = importlib.import_module(module_path)
            cls = getattr(mod, cls_name)
            adapter = cls()
            statuses[display_name] = "configured" if adapter.is_configured() else ("degraded" if is_free else "needs_key")
        except Exception as exc:
            statuses[display_name] = "error"
            errors.append(f"{display_name}: {exc}")

    ok_count      = sum(1 for v in statuses.values() if v in ("ok", "configured"))
    degraded_count = sum(1 for v in statuses.values() if v in ("degraded", "needs_key"))
    error_count   = sum(1 for v in statuses.values() if v == "error")

    colour = "green" if error_count == 0 else "amber" if error_count < 5 else "red"
    elapsed_ms = round((time.monotonic() - started) * 1000, 1)

    return {
        "colour":          colour,
        "ok_count":        ok_count,
        "degraded_count":  degraded_count,
        "error_count":     error_count,
        "total":           len(statuses),
        "errors":          errors[:10],
        "elapsed_ms":      elapsed_ms,
    }


def get_adapter_status_grid() -> list[dict[str, Any]]:
    """
    Returns a flat list of adapter status rows for the frontend health grid.
    Does NOT make live API calls — just checks import and config.
    """
    rows = []
    for class_path, display_name, tier, is_free in ADAPTER_REGISTRY:
        try:
            module_path, cls_name = class_path.rsplit(".", 1)
            import importlib
            mod = importlib.import_module(module_path)
            cls = getattr(mod, cls_name)
            adapter = cls()
            configured = adapter.is_configured()
            rows.append({
                "name":       display_name,
                "tier":       tier,
                "is_free":    is_free,
                "configured": configured,
                "status":     "ready" if configured else ("no_key" if not is_free else "ready"),
                "auth_required": not is_free,
            })
        except Exception as exc:
            rows.append({
                "name": display_name, "tier": tier, "is_free": is_free,
                "configured": False, "status": "import_error",
                "error": str(exc)[:80], "auth_required": not is_free,
            })
    return rows
