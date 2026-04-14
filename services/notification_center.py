from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import notifier
from notifier_telegram import send_telegram_many


def _db(db_module: Any = None):
    if db_module is not None:
        return db_module
    import database as db

    return db


def _settings(db_module: Any) -> dict[str, Any]:
    try:
        return db_module.get_all_settings()
    except Exception:
        return {}


def _send_telegram_message(settings: dict[str, Any], message: str) -> dict[str, Any]:
    return send_telegram_many(
        message,
        bot_token=settings.get("telegram_bot_token", ""),
        settings=settings,
    )


def send_notification(
    kind: str,
    title: str,
    body: str,
    *,
    critical: bool = False,
    bot_id: str = "",
    platform: str = "",
    db_module: Any = None,
) -> dict[str, Any]:
    db = _db(db_module)
    settings = _settings(db)
    message = (
        f"<b>DeG£N$ — {title}</b>\n\n"
        f"{body}\n\n"
        f"Bot: <code>{bot_id or 'n/a'}</code>\n"
        f"Platform: <code>{platform or 'n/a'}</code>\n"
        f"Time: <code>{datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%SZ')}</code>"
    )
    telegram = _send_telegram_message(settings, message)
    sent = bool(telegram.get("ok"))
    fallback_sms = False
    if critical and not sent:
        try:
            fallback_sms = notifier.send_sms(f"{title}\n{body}\n{bot_id}".strip(), real_money=True)
            sent = sent or fallback_sms
        except Exception:
            fallback_sms = False
    try:
        db.save_notification(
            f"{kind}:{title}:{body}",
            ntype=kind,
            sent=sent,
        )
    except Exception:
        pass
    return {
        "ok": sent,
        "kind": kind,
        "telegram_ok": bool(telegram.get("ok")),
        "sms_fallback_ok": fallback_sms,
    }


def notify_runtime_state(*, running: bool, source: str = "api", db_module: Any = None) -> dict[str, Any]:
    title = "Runtime Started" if running else "Runtime Stopped"
    body = (
        f"Runtime control accepted via {source}. Bot loops are {'running' if running else 'idle'}."
    )
    return send_notification("runtime_control", title, body, db_module=db_module)


def notify_bot_state(bot_id: str, *, paused: bool, source: str = "api", db_module: Any = None) -> dict[str, Any]:
    title = "Bot Paused" if paused else "Bot Resumed"
    body = (
        f"Bot <code>{bot_id}</code> was {'paused' if paused else 'resumed'} via {source}."
    )
    return send_notification("bot_control", title, body, bot_id=bot_id, db_module=db_module)


def notify_live_control_change(
    controls: dict[str, Any],
    *,
    source: str = "api",
    db_module: Any = None,
) -> dict[str, Any]:
    title = "Live Control Updated"
    body = (
        f"Live execution controls changed via {source}.\n"
        f"Global armed: <b>{'yes' if controls.get('live_execution_enabled') else 'no'}</b>\n"
        f"Stake armed: <b>{'yes' if controls.get('stake_live_enabled') else 'no'}</b>\n"
        f"Polymarket armed: <b>{'yes' if controls.get('polymarket_live_enabled') else 'no'}</b>\n"
        "Runtime start is still required before any live-capable loop runs."
    )
    return send_notification("live_control", title, body, db_module=db_module)


def notify_credential_validation_result(
    summary: dict[str, Any],
    *,
    source: str = "operator_refresh",
    db_module: Any = None,
) -> dict[str, Any]:
    platforms = summary.get("platforms") or []
    valid = sum(1 for row in platforms if row.get("credentials_valid"))
    failed = sum(1 for row in platforms if row.get("validation_performed") and not row.get("credentials_valid"))
    loaded = sum(1 for row in platforms if row.get("credentials_present"))
    title = "Credential Validation Completed"
    body = (
        f"Credential refresh ran via {source}.\n"
        f"Loaded: <b>{loaded}</b>\n"
        f"Validated: <b>{valid}</b>\n"
        f"Failed validation: <b>{failed}</b>\n"
        "No raw credentials were returned in this check."
    )
    return send_notification("credential_validation", title, body, db_module=db_module)


def notify_reset_to_zero(*, source: str = "api", db_module: Any = None) -> dict[str, Any]:
    return send_notification(
        "runtime_reset",
        "Reset To Zero Completed",
        f"Runtime histories and ledger runtime balances were reset via {source}. Catalog, bot registry, and settings were preserved.",
        critical=True,
        db_module=db_module,
    )


def notify_withdraw_request_recorded(amount: float, *, note: str = "", db_module: Any = None) -> dict[str, Any]:
    body = (
        f"Withdrawal request recorded for <b>${amount:.2f}</b>."
        f"\nLedger only. No external funds were moved."
    )
    if note:
        body += f"\nNote: {note}"
    return send_notification("withdraw_request", "Withdrawal Request Recorded", body, db_module=db_module)


def notify_progress_milestone(bot_id: str, *, progress: float, bankroll: float, db_module: Any = None) -> dict[str, Any]:
    body = (
        f"Bot <code>{bot_id}</code> hit a milestone gate.\n"
        f"Progress: <b>${progress:.2f}</b>\n"
        f"Bankroll: <b>${bankroll:.2f}</b>\n"
        "Operator review is recommended before increasing ambition."
    )
    return send_notification("milestone", "Milestone Hit", body, bot_id=bot_id, db_module=db_module)


def notify_target_reached(bot_id: str, *, progress: float, bankroll: float, db_module: Any = None) -> dict[str, Any]:
    body = (
        f"Bot <code>{bot_id}</code> reached its configured target.\n"
        f"Progress: <b>${progress:.2f}</b>\n"
        f"Bankroll: <b>${bankroll:.2f}</b>\n"
        "Review withdrawal and restart decisions explicitly."
    )
    return send_notification("target_reached", "Target Reached", body, bot_id=bot_id, db_module=db_module)


def notify_floor_warning(
    bot_id: str,
    *,
    bankroll: float,
    floor: float,
    reason: str = "",
    db_module: Any = None,
) -> dict[str, Any]:
    body = (
        f"Bot <code>{bot_id}</code> dropped into the floor-protection state.\n"
        f"Bankroll: <b>${bankroll:.2f}</b>\n"
        f"Configured floor: <b>${floor:.2f}</b>"
    )
    if reason:
        body += f"\nReason: {reason}"
    body += "\nThis is a degraded protective state, not a live win signal."
    return send_notification("floor_warning", "ForceField Floor Warning", body, critical=True, bot_id=bot_id, db_module=db_module)


def notify_circuit_breaker_event(
    bot_id: str,
    *,
    reason: str,
    cooldown_s: float,
    db_module: Any = None,
) -> dict[str, Any]:
    body = (
        f"Circuit breaker tripped for bot <code>{bot_id}</code>.\n"
        f"Reason: {reason or 'unknown'}\n"
        f"Cooldown: <b>{cooldown_s:.0f}s</b>\n"
        "Review before resuming."
    )
    return send_notification("circuit_breaker", "Circuit Breaker Triggered", body, critical=True, bot_id=bot_id, db_module=db_module)


def send_daily_summary(*, db_module: Any = None) -> dict[str, Any]:
    db = _db(db_module)
    key = "notification_daily_last_sent"
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    if db.get_setting(key, "") == today:
        return {"ok": True, "skipped": True, "reason": "already_sent_today"}

    _tr = db.get_trades(limit=200) if hasattr(db, "get_trades") else ([], 0)
    trades = _tr[0] if isinstance(_tr, tuple) else _tr
    best = max(trades, key=lambda row: float(row.get("net", 0) or 0), default=None)
    worst = min(trades, key=lambda row: float(row.get("net", 0) or 0), default=None)
    total_pnl = round(sum(float(row.get("net", 0) or 0) for row in trades), 2)
    message = (
        f"Total PnL: ${total_pnl:.2f}\n"
        f"Best: {(best or {}).get('bot_id', 'n/a')} ${float((best or {}).get('net', 0) or 0):.2f}\n"
        f"Worst: {(worst or {}).get('bot_id', 'n/a')} ${float((worst or {}).get('net', 0) or 0):.2f}\n"
        f"Trades counted: {len(trades)}"
    )
    result = send_notification("daily_summary", "Daily Summary", message, db_module=db)
    if result.get("ok"):
        db.set_setting(key, today)
    return result


def notify_degraded_health(
    bot_id: str = "",
    *,
    reason: str = "",
    source: str = "system",
    db_module: Any = None,
) -> dict[str, Any]:
    title = "Degraded System Health"
    body = f"Health degradation detected via {source}."
    if bot_id:
        body += f"\nBot: <code>{bot_id}</code>"
    if reason:
        body += f"\nReason: {reason}"
    return send_notification("degraded_health", title, body, critical=True, bot_id=bot_id, db_module=db_module)


def notify_reconciliation_mismatch(
    bot_id: str = "",
    *,
    detail: str = "",
    source: str = "reconciliation",
    db_module: Any = None,
) -> dict[str, Any]:
    title = "Reconciliation Mismatch"
    body = f"Reconciliation drift detected via {source}."
    if bot_id:
        body += f"\nBot: <code>{bot_id}</code>"
    if detail:
        body += f"\nDetail: {detail}"
    body += "\nManual review recommended before continuing live execution."
    return send_notification("reconciliation_mismatch", title, body, critical=True, bot_id=bot_id, db_module=db_module)


def notify_canary_breach(
    bot_id: str,
    *,
    breach_type: str = "drawdown",
    detail: str = "",
    source: str = "canary",
    db_module: Any = None,
) -> dict[str, Any]:
    title = "Canary Breach — Stop Condition Hit"
    body = (
        f"Canary session breach: <b>{breach_type}</b> for bot <code>{bot_id}</code>.\n"
        f"Bot was stopped automatically.\n"
        f"This is a controlled stop, not a system fault."
    )
    if detail:
        body += f"\nDetail: {detail}"
    body += "\nReview canary session log before restarting."
    return send_notification("canary_breach", title, body, critical=True, bot_id=bot_id, db_module=db_module)


def notify_proposal_routed(
    proposal: dict[str, Any],
    result: dict[str, Any],
    *,
    source: str = "scheduler",
    db_module: Any = None,
) -> dict[str, Any]:
    risk = result.get("risk_decision") or {}
    decision = str(risk.get("decision", result.get("decision", "")) or "").strip().lower()
    if decision == "reject":
        return {"ok": True, "skipped": True, "reason": "proposal_rejected"}

    proposal_id = str(proposal.get("proposal_id", "n/a") or "n/a")
    order_ref = str(
        result.get("order_id")
        or result.get("shadow_order_id")
        or proposal_id
    )
    size_usd = float(risk.get("size_usd", 0) or 0)
    edge_post_fee = float(
        proposal.get("edge_post_fee_bps", proposal.get("edge_bps", 0)) or 0
    )
    truth_label = str(result.get("truth_label", proposal.get("truth_label", "unknown")) or "unknown")
    body = (
        f"Proposal <code>{proposal_id}</code> routed via {source}.\n"
        f"Decision: <b>{decision or 'accepted'}</b>\n"
        f"Mode: <b>{truth_label}</b>\n"
        f"Edge post-fee: <b>{edge_post_fee:.2f} bps</b>\n"
        f"Size: <b>${size_usd:.2f}</b>\n"
        f"Order ref: <code>{order_ref}</code>"
    )
    degraded_reason = str(result.get("degraded_reason", "") or "").strip()
    if degraded_reason:
        body += f"\nDetail: {degraded_reason}"
    return send_notification(
        "proposal_routed",
        "Proposal Routed",
        body,
        bot_id=str(proposal.get("bot_id", "") or ""),
        platform=str(proposal.get("platform", "") or ""),
        db_module=db_module,
    )


def notify_profit_ladder_lock(
    bot_id: str,
    ladder_result: dict[str, Any],
    *,
    db_module: Any = None,
) -> dict[str, Any]:
    if str(ladder_result.get("action", "")).strip().lower() != "locked":
        return {"ok": True, "skipped": True, "reason": "no_ladder_lock"}
    locked = float(ladder_result.get("locked", 0) or 0)
    tier = str(ladder_result.get("tier", "unknown") or "unknown")
    cycle = int(ladder_result.get("cycle", 0) or 0)
    cycles_per_tier = int(ladder_result.get("cycles_per_tier", 0) or 0)
    terminal = bool(ladder_result.get("terminal"))
    body = (
        f"Locked <b>${locked:.2f}</b> into the vault.\n"
        f"Tier: <b>{tier}</b>\n"
        f"Cycle: <b>{cycle}</b> / <b>{cycles_per_tier}</b>\n"
        f"Terminal: <b>{'yes' if terminal else 'no'}</b>"
    )
    return send_notification(
        "profit_ladder",
        "Profit Ladder Lock",
        body,
        bot_id=bot_id,
        platform="vault",
        db_module=db_module,
    )


def notify_mall_pipeline_stage(
    item: dict[str, Any],
    *,
    previous_stage: str,
    new_stage: str,
    source: str = "operator",
    db_module: Any = None,
) -> dict[str, Any]:
    title_text = str(item.get("title", "Mall queue item") or "Mall queue item")
    value_estimate = float(item.get("value_estimate", 0) or 0)
    contact_ref = str(item.get("contact_ref", "") or "").strip()
    body = (
        f"{title_text}\n"
        f"Stage: <b>{previous_stage or 'unknown'}</b> → <b>{new_stage}</b>\n"
        f"Source: <b>{source}</b>"
    )
    if value_estimate > 0:
        body += f"\nEstimated value: <b>${value_estimate:.2f}</b>"
    if contact_ref:
        body += f"\nContact: <code>{contact_ref}</code>"
    return send_notification(
        "mall_pipeline",
        "Mall Queue Updated",
        body,
        bot_id=str(item.get("bot_id", "") or ""),
        platform="mall",
        db_module=db_module,
    )


def notify_alert_delivery_failure(
    failed_kind: str,
    *,
    reason: str = "",
    db_module: Any = None,
) -> dict[str, Any]:
    """Persists a record of an alert that could not be delivered (no outbound attempt — just audit log)."""
    db = _db(db_module)
    msg = f"alert_delivery_failure:{failed_kind}:{reason}"
    try:
        db.save_notification(msg, ntype="alert_delivery_failure", sent=False)
    except Exception:
        pass
    return {"ok": False, "kind": "alert_delivery_failure", "failed_kind": failed_kind}


def notify_proposal_executed(
    proposal_data: dict,
    risk_decision: dict,
    fill_result: dict,
    *,
    db_module: Any = None,
) -> dict[str, Any]:
    """Send notification when a proposal is executed through the order router."""
    bot_id = str(proposal_data.get("bot_id", "") or "")
    side = proposal_data.get("side", "?")
    market = proposal_data.get("market_id", "?")
    size = float(risk_decision.get("size_usd", 0) or 0)
    mode = proposal_data.get("runtime_mode", "paper")
    filled = bool(fill_result.get("filled", False))
    emoji = "✅" if filled else "⏳"
    body = (
        f"{emoji} Proposal executed\n"
        f"Side: {side} | Market: {market}\n"
        f"Size: ${size:.2f} | Mode: {mode.upper()}\n"
        f"Filled: {filled}"
    )
    return send_notification(
        "proposal_executed",
        f"Execution — {bot_id}",
        body,
        bot_id=bot_id,
        platform=str(proposal_data.get("platform", "") or ""),
        db_module=db_module,
    )


def notify_ladder_lock(ladder_result: dict, *, db_module: Any = None) -> dict[str, Any]:
    """Send notification when the profit ladder locks (thin wrapper over notify_profit_ladder_lock)."""
    bot_id = str(ladder_result.get("bot_id", "system") or "system")
    return notify_profit_ladder_lock(bot_id, ladder_result, db_module=db_module)


def notify_mall_revenue(result: dict, *, db_module: Any = None) -> dict[str, Any]:
    """Send notification when MALL revenue is auto-vaulted or auto-withdrawn."""
    if result.get("action") not in ("vaulted", "withdrawal_requested"):
        return {"ok": True, "skipped": True}
    amount = float(result.get("amount", 0) or 0)
    items = int(result.get("items", 0) or 0)
    body = (
        f"MALL revenue processed\n"
        f"Action: {result['action']}\n"
        f"Amount: ${amount:.2f}\n"
        f"Items: {items}"
    )
    return send_notification("mall_revenue", "MALL Revenue", body, platform="mall", db_module=db_module)


def notify_forcefield_milestone(status: dict[str, Any], *, db_module: Any = None) -> dict[str, Any]:
    portfolio = status.get("portfolio") or {}
    milestones = status.get("milestones") or {}
    if not milestones.get("milestone_pending"):
        return {"ok": True, "skipped": True, "reason": "no_pending_milestone"}
    multiple = float(portfolio.get("current_multiple", 0) or 0)
    next_target = float(milestones.get("next_target_multiple", 0) or 0)
    body = (
        f"Portfolio milestone gate is active.\n"
        f"Current multiple: <b>{multiple:.2f}x</b>\n"
        f"Next target: <b>{next_target:.2f}x</b>\n"
        "Manual continue is required before new executable reservations will be approved."
    )
    return send_notification("forcefield_milestone", "ForceField Milestone Gate", body, platform="portfolio", db_module=db_module)


def notify_forcefield_sweep(result: dict[str, Any], *, db_module: Any = None) -> dict[str, Any]:
    if result.get("action") != "swept":
        return {"ok": True, "skipped": True, "reason": "no_sweep"}
    amount = float(result.get("amount", 0) or 0)
    portfolio = result.get("portfolio") or {}
    body = (
        f"ForceField swept <b>${amount:.2f}</b> into the vault.\n"
        f"Execution equity: <b>${float(portfolio.get('equity', 0) or 0):.2f}</b>\n"
        f"Vault balance: <b>${float(portfolio.get('vault_balance', 0) or 0):.2f}</b>\n"
        f"Total value: <b>${float(portfolio.get('total_value', 0) or 0):.2f}</b>"
    )
    return send_notification("forcefield_sweep", "ForceField Sweep", body, platform="portfolio", db_module=db_module)


def send_weekly_report(*, db_module: Any = None) -> dict[str, Any]:
    db = _db(db_module)
    key = "notification_weekly_last_sent"
    week_id = datetime.now(UTC).strftime("%G-W%V")
    if db.get_setting(key, "") == week_id:
        return {"ok": True, "skipped": True, "reason": "already_sent_this_week"}

    _tr = db.get_trades(limit=500) if hasattr(db, "get_trades") else ([], 0)
    trades = _tr[0] if isinstance(_tr, tuple) else _tr
    by_bot: dict[str, float] = {}
    for row in trades:
        by_bot[row.get("bot_id", "unknown")] = by_bot.get(row.get("bot_id", "unknown"), 0.0) + float(row.get("net", 0) or 0)
    lines = [f"{bot}: ${pnl:.2f}" for bot, pnl in sorted(by_bot.items(), key=lambda item: item[1], reverse=True)[:5]]
    message = "Top weekly bot PnL\n" + ("\n".join(lines) if lines else "No trade evidence yet.")
    result = send_notification("weekly_report", "Weekly Report", message, db_module=db)
    if result.get("ok"):
        db.set_setting(key, week_id)
    return result
