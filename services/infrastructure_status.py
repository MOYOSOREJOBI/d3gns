from __future__ import annotations

from typing import Any

import config as _cfg


def _postgres_status() -> dict[str, Any]:
    url = str(getattr(_cfg, "DATABASE_URL", "") or "").strip()
    status = {
        "configured": bool(url),
        "backend": "postgres",
        "dsn_redacted": (url.split("@")[-1] if "@" in url else url[:24]) if url else "",
        "ok": False,
        "detail": "not_configured",
    }
    if not url:
        return status
    try:
        import psycopg
        with psycopg.connect(url, connect_timeout=3) as conn:
            with conn.cursor() as cur:
                cur.execute("select current_database(), current_user")
                db_name, user_name = cur.fetchone()
        status.update({"ok": True, "detail": "connected", "database": db_name, "user": user_name})
    except ImportError:
        status["detail"] = "psycopg_not_installed"
    except Exception as exc:
        status["detail"] = str(exc)
    return status


def _queue_status() -> dict[str, Any]:
    backend = str(getattr(_cfg, "QUEUE_BACKEND", "inline") or "inline").lower()
    redis_url = str(getattr(_cfg, "REDIS_URL", "") or "").strip()
    status = {
        "backend": backend,
        "configured": backend == "inline" or bool(redis_url),
        "ok": backend == "inline",
        "detail": "inline_only" if backend == "inline" else "not_configured",
    }
    if backend == "inline":
        return status
    if not redis_url:
        return status
    try:
        import redis
        client = redis.from_url(redis_url, socket_timeout=3)
        status["redis_ping"] = bool(client.ping())
        status["ok"] = bool(status["redis_ping"])
        status["detail"] = "connected" if status["ok"] else "unreachable"
        if backend == "rq":
            try:
                import rq  # noqa: F401
                status["rq_installed"] = True
            except ImportError:
                status["rq_installed"] = False
                status["ok"] = False
                status["detail"] = "rq_not_installed"
    except ImportError:
        status["detail"] = "redis_client_not_installed"
    except Exception as exc:
        status["detail"] = str(exc)
    return status


def get_infrastructure_status() -> dict[str, Any]:
    postgres = _postgres_status()
    queue = _queue_status()
    sqlite_active = not bool(getattr(_cfg, "DATABASE_URL", ""))
    return {
        "ok": bool(postgres.get("ok") or sqlite_active) and bool(queue.get("ok") or queue.get("backend") == "inline"),
        "storage": {
            "sqlite_active": sqlite_active,
            "postgres": postgres,
        },
        "queue": queue,
        "recommendation": (
            "ready_for_durable_runtime" if postgres.get("ok") and queue.get("ok")
            else "needs_postgres_and_queue"
        ),
    }

