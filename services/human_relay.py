from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Any


def _db(db_module: Any = None):
    if db_module is not None:
        return db_module
    import database as db

    return db


def _iso_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except Exception:
        return None


def open_challenge(
    *,
    bot_id: str,
    platform: str,
    prompt: str,
    description: str = "",
    screenshot_path: str = "",
    challenge_type: str = "human_check",
    timeout_s: int = 300,
    chat_id: str = "",
    bot_token: str = "",
    payload: dict[str, Any] | None = None,
    db_module: Any = None,
) -> dict[str, Any]:
    from notifier_telegram import send_human_verification_prompt

    db = _db(db_module)
    challenge_id = f"relay_{secrets.token_hex(8)}"
    now = datetime.now(UTC)
    expires_at = (now + timedelta(seconds=max(60, int(timeout_s or 300)))).strftime("%Y-%m-%dT%H:%M:%SZ")
    db.save_human_relay_request(
        challenge_id,
        bot_id=bot_id,
        platform=platform,
        challenge_type=challenge_type,
        status="pending",
        prompt=prompt,
        description=description,
        screenshot_path=screenshot_path,
        expires_at=expires_at,
        last_sent_at=_iso_now(),
        payload=payload or {},
    )

    message = prompt
    if description:
        message = f"{prompt}\n\n{description}"
    telegram_result = send_human_verification_prompt(
        prompt=message,
        chat_id=chat_id,
        bot_token=bot_token,
        context=challenge_id,
        settings=db.get_all_settings() if hasattr(db, "get_all_settings") else None,
    )
    db.save_notification(
        f"HUMAN RELAY {challenge_id} [{platform}/{bot_id}] {prompt}",
        ntype="human_relay",
        sent=bool(telegram_result.get("ok")),
    )
    return {
        "ok": True,
        "challenge_id": challenge_id,
        "status": "pending",
        "bot_id": bot_id,
        "platform": platform,
        "expires_at": expires_at,
        "telegram_sent": bool(telegram_result.get("ok")),
        "telegram_result": telegram_result,
    }


def get_challenge(challenge_id: str, *, db_module: Any = None) -> dict[str, Any] | None:
    return _db(db_module).get_human_relay_request(challenge_id)


def list_challenges(*, status: str | None = None, limit: int = 50, db_module: Any = None) -> list[dict[str, Any]]:
    return _db(db_module).list_human_relay_requests(status=status, limit=limit)


def respond_to_challenge(
    challenge_id: str,
    decision: str,
    *,
    source: str = "api",
    payload: dict[str, Any] | None = None,
    db_module: Any = None,
) -> dict[str, Any]:
    db = _db(db_module)
    record = db.get_human_relay_request(challenge_id)
    if not record:
        return {"ok": False, "error": "not_found", "challenge_id": challenge_id}
    normalized = str(decision or "").strip().lower()
    if normalized not in {"yes", "no", "skip"}:
        return {"ok": False, "error": "invalid_decision", "challenge_id": challenge_id}
    db.update_human_relay_request(
        challenge_id,
        status="resolved",
        response=normalized,
        response_source=source,
        payload=payload or {},
    )
    db.save_notification(
        f"HUMAN RELAY {challenge_id} resolved via {source}: {normalized}",
        ntype="human_relay",
        sent=False,
    )
    updated = db.get_human_relay_request(challenge_id)
    return {"ok": True, "challenge": updated}


def send_due_reminders(*, db_module: Any = None) -> dict[str, Any]:
    from notifier_telegram import configured_operator_chat_ids, send_telegram, send_telegram_many

    db = _db(db_module)
    settings = db.get_all_settings()
    token = settings.get("telegram_bot_token", "")
    chat_id = settings.get("telegram_chat_id", "")
    chat_ids = configured_operator_chat_ids(settings)
    now = datetime.now(UTC)
    reminded = 0
    expired = 0
    for row in db.list_human_relay_requests(status="pending", limit=100):
        ts = _parse_iso(row.get("ts"))
        last_sent = _parse_iso(row.get("last_sent_at"))
        expires_at = _parse_iso(row.get("expires_at"))
        if expires_at and now >= expires_at:
            if last_sent is None or (now - last_sent).total_seconds() >= 300:
                message = (
                    f"<b>DeG£N$ — Reminder</b>\n\n"
                    f"Challenge <code>{row['id']}</code> is still pending.\n"
                    f"Bot: <code>{row.get('bot_id','')}</code>\n"
                    f"Platform: <code>{row.get('platform','')}</code>\n\n"
                    f"{row.get('prompt','Human action required')}"
                )
                result = (
                    send_telegram(message, chat_id=chat_id, bot_token=token)
                    if chat_id
                    else send_telegram_many(message, chat_ids=chat_ids, bot_token=token, settings=settings)
                )
                db.update_human_relay_request(
                    row["id"],
                    reminder_count=int(row.get("reminder_count", 0)) + 1,
                    last_sent_at=_iso_now(),
                    payload={"last_reminder_ok": bool(result.get("ok"))},
                )
                reminded += 1
            expired += 1
            continue
        if ts and (now - ts).total_seconds() >= 300 and (last_sent is None or (now - last_sent).total_seconds() >= 300):
            reminder_message = (
                f"<b>DeG£N$ — Human Relay Pending</b>\n\n"
                f"Challenge <code>{row['id']}</code> still awaits a response.\n"
                f"Bot: <code>{row.get('bot_id','')}</code>\n"
                f"Platform: <code>{row.get('platform','')}</code>"
            )
            result = (
                send_telegram(reminder_message, chat_id=chat_id, bot_token=token)
                if chat_id
                else send_telegram_many(reminder_message, chat_ids=chat_ids, bot_token=token, settings=settings)
            )
            db.update_human_relay_request(
                row["id"],
                reminder_count=int(row.get("reminder_count", 0)) + 1,
                last_sent_at=_iso_now(),
                payload={"last_reminder_ok": bool(result.get("ok"))},
            )
            reminded += 1
    return {"ok": True, "pending": len(db.list_human_relay_requests(status="pending", limit=100)), "expired": expired, "reminded": reminded}
