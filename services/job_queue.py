from __future__ import annotations

import time
import uuid
from typing import Any

import config as _cfg


def _now_ms() -> int:
    return int(time.time() * 1000)


def enqueue(
    task_name: str,
    *,
    payload: dict[str, Any] | None = None,
    queue_name: str | None = None,
) -> dict[str, Any]:
    backend = str(getattr(_cfg, "QUEUE_BACKEND", "inline") or "inline").lower()
    queue = queue_name or getattr(_cfg, "QUEUE_NAME_DEFAULT", "degens-runtime")
    payload = payload or {}
    job_id = f"job_{uuid.uuid4().hex[:12]}"

    if backend == "inline":
        return {
            "ok": True,
            "backend": "inline",
            "queue": queue,
            "job_id": job_id,
            "enqueued_at_ms": _now_ms(),
            "payload": payload,
        }

    redis_url = str(getattr(_cfg, "REDIS_URL", "") or "").strip()
    if not redis_url:
        return {"ok": False, "backend": backend, "queue": queue, "error": "redis_url_missing"}

    try:
        import redis
        conn = redis.from_url(redis_url)
        if backend == "rq":
            from rq import Queue
            q = Queue(name=queue, connection=conn)
            job = q.enqueue(task_name, kwargs=payload, job_id=job_id)
            return {
                "ok": True,
                "backend": "rq",
                "queue": queue,
                "job_id": job.id,
                "enqueued_at_ms": _now_ms(),
            }
        return {"ok": False, "backend": backend, "queue": queue, "error": "unsupported_queue_backend"}
    except Exception as exc:
        return {"ok": False, "backend": backend, "queue": queue, "error": str(exc)}

