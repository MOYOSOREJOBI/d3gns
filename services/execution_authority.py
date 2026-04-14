from __future__ import annotations

import os
import socket
import time
import uuid
from typing import Any

import config as _cfg


LOCK_NAME = "primary_executor"
LEASE_SECONDS = 45


def build_owner(process_name: str, purpose: str) -> dict[str, Any]:
    return {
        "owner_id": f"{process_name}-{os.getpid()}-{uuid.uuid4().hex[:8]}",
        "process_name": process_name,
        "purpose": purpose,
        "pid": os.getpid(),
        "hostname": socket.gethostname(),
        "started_at": time.time(),
    }


def claim(db_module: Any, owner: dict[str, Any]) -> tuple[bool, dict[str, Any] | None]:
    claimed, row = db_module.claim_execution_owner(
        lock_name=LOCK_NAME,
        owner_id=owner["owner_id"],
        process_name=owner["process_name"],
        pid=owner["pid"],
        hostname=owner["hostname"],
        purpose=owner["purpose"],
        lease_seconds=LEASE_SECONDS,
    )
    return claimed, row


def heartbeat(db_module: Any, owner_id: str) -> dict[str, Any] | None:
    return db_module.heartbeat_execution_owner(lock_name=LOCK_NAME, owner_id=owner_id)


def release(db_module: Any, owner_id: str):
    db_module.release_execution_owner(lock_name=LOCK_NAME, owner_id=owner_id)


def current(db_module: Any) -> dict[str, Any] | None:
    return db_module.get_execution_owner(lock_name=LOCK_NAME)


def describe(row: dict[str, Any] | None, owner_id: str | None = None, *, stale_threshold_s: float | None = None) -> dict[str, Any]:
    stale_threshold = stale_threshold_s or _cfg.HEALTHCHECK_THRESHOLDS.get("heartbeat_stale_s", LEASE_SECONDS * 2)
    if not row:
        return {
            "lock_name": LOCK_NAME,
            "owner_present": False,
            "owned_by_self": False,
            "is_stale": False,
            "degraded_reason": "No execution owner is registered.",
        }
    last_heartbeat = float(row.get("last_heartbeat_epoch") or 0)
    age_s = max(0.0, time.time() - last_heartbeat) if last_heartbeat else None
    is_stale_value = bool(last_heartbeat <= 0 or (age_s is not None and age_s > stale_threshold))
    return {
        **row,
        "lock_name": LOCK_NAME,
        "owner_present": True,
        "owned_by_self": bool(owner_id and row.get("owner_id") == owner_id),
        "heartbeat_age_s": round(age_s, 2) if age_s is not None else None,
        "is_stale": is_stale_value,
        "stale_threshold_s": stale_threshold,
        "degraded_reason": "Execution owner heartbeat is stale." if is_stale_value else "",
    }


def is_stale(db_module: Any, stale_threshold_s: float | None = None) -> bool:
    row = db_module.get_execution_owner(lock_name=LOCK_NAME)
    if not row:
        return False
    threshold = stale_threshold_s or _cfg.HEALTHCHECK_THRESHOLDS.get("heartbeat_stale_s", LEASE_SECONDS * 2)
    last_hb = float(row.get("last_heartbeat_epoch") or 0)
    if last_hb <= 0:
        return True
    return (time.time() - last_hb) > threshold


def force_takeover(db_module: Any, new_owner: dict[str, Any], reason: str = "manual_takeover") -> tuple[bool, dict[str, Any] | None]:
    try:
        previous = current(db_module)
        db_module.force_execution_owner(
            lock_name=LOCK_NAME,
            owner_id=new_owner["owner_id"],
            process_name=new_owner["process_name"],
            pid=new_owner["pid"],
            hostname=new_owner["hostname"],
            purpose=new_owner["purpose"],
            lease_seconds=LEASE_SECONDS,
        )
        if hasattr(db_module, "save_executor_lock_audit"):
            db_module.save_executor_lock_audit(
                "force_takeover",
                new_owner["owner_id"],
                prev_owner=(previous or {}).get("owner_id", ""),
                pid=new_owner["pid"],
                hostname=new_owner["hostname"],
                reason=reason,
                payload={"lock_name": LOCK_NAME, "process_name": new_owner["process_name"]},
            )
        row = db_module.get_execution_owner(lock_name=LOCK_NAME)
        return True, row
    except Exception:
        return False, None


def get_history(db_module: Any, limit: int = 20) -> list[dict[str, Any]]:
    try:
        if hasattr(db_module, "get_executor_lock_audit"):
            return db_module.get_executor_lock_audit(limit=limit)
        return db_module.get_execution_owner_history(lock_name=LOCK_NAME, limit=limit)
    except Exception:
        return []


def get_state(db_module: Any, owner_id: str | None = None, limit: int = 10) -> dict[str, Any]:
    row = current(db_module)
    description = describe(row, owner_id=owner_id)
    description["history"] = get_history(db_module, limit=limit)
    return description
