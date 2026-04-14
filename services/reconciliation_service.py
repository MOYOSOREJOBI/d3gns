from __future__ import annotations

import secrets
import time
from typing import Any

import config as _cfg

ACTION_PLATFORM = "runtime_control"
ACTION_EXECUTION_MODE = "OPERATOR"
ACTION_STATES = {"requested", "accepted", "executed", "noop", "failed", "reconciled"}


def _platform_result(
    platform: str,
    *,
    reconciliation_state: str,
    truth_label: str,
    reason: str,
    order_counts: dict | None = None,
    last_reconciled_at: float | None = None,
    stalled: bool = False,
    payload: dict | None = None,
) -> dict[str, Any]:
    return {
        "platform": platform,
        "reconciliation_state": reconciliation_state,
        "truth_label": truth_label,
        "reason": reason,
        "last_reconciled_at": last_reconciled_at,
        "stalled": stalled,
        "order_counts": order_counts or {"requests": 0, "fills": 0, "settlements": 0},
        "payload": payload or {},
        "realized_pnl": None,
    }


def _db_order_counts(db_module: Any, platform: str) -> dict[str, int]:
    if db_module is None:
        return {"requests": 0, "fills": 0, "settlements": 0}
    try:
        requests = db_module.get_order_requests(platform=platform, limit=500) if hasattr(db_module, "get_order_requests") else []
        fills = db_module.get_order_fills(platform=platform, limit=500) if hasattr(db_module, "get_order_fills") else []
        settlements = db_module.get_order_settlements(platform=platform, limit=500) if hasattr(db_module, "get_order_settlements") else []
        return {
            "requests": len(requests),
            "fills": len(fills),
            "settlements": len(settlements),
        }
    except Exception:
        return {"requests": 0, "fills": 0, "settlements": 0}


def _latest_balance_payload(db_module: Any, platform: str) -> dict[str, Any]:
    if db_module is None or not hasattr(db_module, "get_venue_balances"):
        return {}
    try:
        rows = db_module.get_venue_balances(platform=platform, limit=1)
        return rows[0] if rows else {}
    except Exception:
        return {}


def reconcile_platform(platform: str, db_module: Any = None) -> dict[str, Any]:
    platform = platform.lower().strip()
    order_counts = _db_order_counts(db_module, platform)
    balance_payload = _latest_balance_payload(db_module, platform)
    if platform == "stake":
        live_enabled = bool(_cfg.STAKE_API_TOKEN)
        if not live_enabled:
            return _platform_result(
                "stake",
                reconciliation_state="not_applicable",
                truth_label="LIVE DISABLED",
                reason="Stake live reconciliation is unavailable because live execution is disabled.",
                order_counts=order_counts,
                payload={"latest_balance": balance_payload},
            )
        return _platform_result(
            "stake",
            reconciliation_state="partial",
            truth_label="LIVE DISABLED",
            reason="Stake credentials are present, but full fill/balance settlement reconciliation is not enabled in this batch.",
            order_counts=order_counts,
            stalled=order_counts.get("requests", 0) > order_counts.get("fills", 0),
            payload={"latest_balance": balance_payload},
        )

    mode_map = {
        "polymarket": "PAPER",
        "polymarket_public": "PUBLIC DATA ONLY",
        "kalshi": "DEMO",
        "kalshi_public": "PUBLIC DATA ONLY",
        "kalshi_demo": "DEMO",
        "kalshi_live": "LIVE DISABLED",
        "oddsapi": "PUBLIC DATA ONLY",
        "betfair_delayed": "DELAYED",
        "sportsdataio_trial": "TRIAL",
        "matchbook": "NOT CONFIGURED",
        "betdaq": "NOT CONFIGURED",
        "smarkets": "NOT CONFIGURED",
    }
    truth_label = mode_map.get(platform, "NOT APPLICABLE")
    return _platform_result(
        platform,
        reconciliation_state="not_applicable",
        truth_label=truth_label,
        reason=f"{truth_label} workflows do not provide externally reconciled funds/settlement in this batch.",
        order_counts=order_counts,
        stalled=platform == "kalshi_live" and order_counts.get("requests", 0) > order_counts.get("fills", 0),
        payload={"latest_balance": balance_payload},
    )


def reconcile_all(db_module: Any = None) -> dict[str, Any]:
    platforms = [
        "stake",
        "polymarket",
        "polymarket_public",
        "kalshi_public",
        "kalshi_demo",
        "kalshi_live",
        "oddsapi",
        "betfair_delayed",
        "sportsdataio_trial",
    ]
    results = {platform: reconcile_platform(platform, db_module=db_module) for platform in platforms}
    checked_at = time.time()
    return {
        "checked_at": checked_at,
        "platforms": results,
        "summary": {
            "stalled_platforms": [p for p, row in results.items() if row["stalled"]],
            "applicable_platforms": [p for p, row in results.items() if row["reconciliation_state"] != "not_applicable"],
            "truthful_noop_platforms": [p for p, row in results.items() if row["reconciliation_state"] == "not_applicable"],
        },
        "realized_pnl": None,
    }


def persist_reconciliation(db_module: Any, report: dict[str, Any]) -> None:
    if db_module is None:
        return
    if hasattr(db_module, "save_reconciliation_snapshot"):
        try:
            db_module.save_reconciliation_snapshot(report)
        except Exception:
            pass
    if hasattr(db_module, "set_runtime_truth"):
        try:
            db_module.set_runtime_truth("reconciliation", report)
        except Exception:
            pass


def history(db_module: Any, platform: str | None = None, limit: int = 50) -> list[dict]:
    if db_module is None or not hasattr(db_module, "get_reconciliation_history"):
        return []
    return db_module.get_reconciliation_history(platform=platform, limit=limit)


def _action_payload(
    action_id: str,
    action_type: str,
    state: str,
    *,
    bot_id: str = "",
    source: str = "api",
    reason: str = "",
    payload: dict | None = None,
) -> dict[str, Any]:
    return {
        "action_id": action_id,
        "action_type": action_type,
        "state": state,
        "bot_id": bot_id or "system",
        "source": source,
        "reason": reason,
        "payload": payload or {},
        "ts_epoch": time.time(),
    }


def begin_action(
    action_type: str,
    *,
    bot_id: str = "",
    source: str = "api",
    payload: dict | None = None,
    db_module: Any = None,
) -> dict[str, Any]:
    action_id = f"act_{int(time.time() * 1000)}_{secrets.token_hex(4)}"
    record_action_state(
        action_id,
        action_type,
        "requested",
        bot_id=bot_id,
        source=source,
        payload=payload,
        db_module=db_module,
    )
    return {
        "action_id": action_id,
        "action_type": action_type,
        "bot_id": bot_id or "system",
        "source": source,
    }


def record_action_state(
    action_id: str,
    action_type: str,
    state: str,
    *,
    bot_id: str = "",
    source: str = "api",
    reason: str = "",
    payload: dict | None = None,
    db_module: Any = None,
) -> None:
    if db_module is None or state not in ACTION_STATES:
        return
    record = _action_payload(
        action_id,
        action_type,
        state,
        bot_id=bot_id,
        source=source,
        reason=reason,
        payload=payload,
    )
    if hasattr(db_module, "save_order_lifecycle"):
        try:
            db_module.save_order_lifecycle(
                {
                    "order_id": action_id,
                    "bot_id": bot_id or "system",
                    "platform": ACTION_PLATFORM,
                    "execution_mode": ACTION_EXECUTION_MODE,
                    "side": action_type,
                    "market_id": bot_id or "system",
                    "amount": 0,
                    "price": 0,
                    "status": state,
                    "payload": record,
                }
            )
        except Exception:
            pass
    if hasattr(db_module, "save_reconciliation_event"):
        try:
            db_module.save_reconciliation_event(
                ACTION_PLATFORM,
                action_id,
                action_type,
                state,
                reason=reason,
                payload=record,
            )
        except Exception:
            pass
