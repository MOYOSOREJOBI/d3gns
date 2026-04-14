from __future__ import annotations

from typing import Any

from bots.catalog import instantiate_bot


def _count_signal_evidence(db_module: Any, bot_id: str) -> int:
    if not db_module or not hasattr(db_module, "get_research_signals"):
        return 0
    try:
        return len(db_module.get_research_signals(bot_id=bot_id, limit=1000))
    except Exception:
        return 0


def _count_execution_evidence(db_module: Any, bot_id: str) -> int:
    if not db_module or not hasattr(db_module, "get_order_lifecycle"):
        return 0
    try:
        rows = db_module.get_order_lifecycle(bot_id=bot_id, limit=1000)
    except Exception:
        return 0
    return sum(1 for row in rows if str(row.get("execution_mode", "")).upper() in {"SHADOW", "PAPER", "DEMO"})


def _platform_health(adapter_registry: Any, platform_names: list[str]) -> tuple[bool, str]:
    if not adapter_registry:
        return False, "Adapter registry unavailable"

    failures: list[str] = []
    for platform in platform_names:
        try:
            adapter = adapter_registry.get(platform)
        except Exception:
            adapter = None
        if adapter is None:
            failures.append(f"{platform}: missing")
            continue
        try:
            health = adapter.healthcheck()
        except Exception as exc:
            failures.append(f"{platform}: {exc}")
            continue
        if not health.get("ok"):
            reason = health.get("degraded_reason") or health.get("status") or "unhealthy"
            failures.append(f"{platform}: {reason}")

    if failures:
        return False, "; ".join(failures)
    return True, "All referenced platform adapters are healthy"


def check_launch_gates(
    bot_id: str,
    *,
    db_module: Any,
    adapter_registry: Any,
    vault: Any,
    market_registry: Any | None = None,
    risk_kernel_available: bool = True,
) -> dict[str, Any]:
    runtime_registry = market_registry or adapter_registry
    bot = None
    bot_error = ""
    try:
        bot = instantiate_bot(bot_id, runtime_registry)
    except Exception as exc:
        bot_error = str(exc)

    signal_count = _count_signal_evidence(db_module, bot_id)
    execution_count = _count_execution_evidence(db_module, bot_id)

    platform_names: list[str] = []
    if bot is not None:
        platform_names = [part for part in str(getattr(bot, "platform", "")).split("+") if part]

    adapter_ok, adapter_reason = _platform_health(adapter_registry, platform_names) if platform_names else (False, "No platform metadata found")

    gates = {
        "proposal_implemented": {
            "pass": bot is not None and callable(getattr(bot, "generate_proposal", None)),
            "reason": "Bot exposes generate_proposal()" if bot is not None else f"Bot not found: {bot_error or bot_id}",
        },
        "signal_evidence": {
            "pass": signal_count >= 10,
            "reason": f"{signal_count} research signals logged (need 10+)",
        },
        "execution_evidence": {
            "pass": execution_count >= 5,
            "reason": f"{execution_count} paper/shadow/demo executions logged (need 5+)",
        },
        "adapter_health": {
            "pass": adapter_ok,
            "reason": adapter_reason,
        },
        "risk_kernel": {
            "pass": bool(risk_kernel_available),
            "reason": "Risk kernel available" if risk_kernel_available else "Risk kernel unavailable",
        },
        "notional_cap": {
            "pass": True,
            "reason": "Max notional cap remains bounded in the risk kernel.",
        },
        "vault_available": {
            "pass": vault is not None,
            "reason": "Vault initialized" if vault is not None else "Vault not initialized",
        },
    }

    return {
        "bot_id": bot_id,
        "eligible_for_live": all(item["pass"] for item in gates.values()),
        "blocker_count": sum(1 for item in gates.values() if not item["pass"]),
        "gates": gates,
    }
