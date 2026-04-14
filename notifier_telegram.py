"""
notifier_telegram.py — Telegram notification support for DeG£N$.

Usage:
    from notifier_telegram import send_telegram, send_verification_challenge

Configuration (in DB settings or env):
    telegram_bot_token   — Bot token from @BotFather
    telegram_chat_id     — Your personal chat ID (get from @userinfobot)
"""

import os
import hashlib
import time
import requests
import logging
import re

logger = logging.getLogger(__name__)

_BASE = "https://api.telegram.org/bot{token}"


def _prefer_env_telegram_settings() -> bool:
    return bool(os.getenv("FLY_APP_NAME"))


def _resolve_setting_value(
    settings: dict | None,
    key: str,
    env_key: str,
    *,
    explicit: str = "",
) -> str:
    explicit_value = str(explicit or "").strip()
    payload_value = str((settings or {}).get(key, "") or "").strip()
    env_value = str(os.getenv(env_key, "") or "").strip()
    if _prefer_env_telegram_settings():
        return env_value or explicit_value or payload_value
    return explicit_value or payload_value or env_value


def parse_chat_ids(value: str | None = "") -> list[str]:
    raw = str(value or "").strip()
    if not raw:
        return []
    parts = [part.strip() for part in re.split(r"[\s,;]+", raw) if part.strip()]
    seen: set[str] = set()
    ordered: list[str] = []
    for part in parts:
        if part in seen:
            continue
        seen.add(part)
        ordered.append(part)
    return ordered


def configured_operator_chat_ids(settings: dict | None = None) -> list[str]:
    payload = settings or {}
    combined: list[str] = []
    combined.extend(parse_chat_ids(_resolve_setting_value(payload, "telegram_chat_id", "TELEGRAM_CHAT_ID")))
    combined.extend(
        parse_chat_ids(
            _resolve_setting_value(payload, "telegram_operator_chat_ids", "TELEGRAM_OPERATOR_CHAT_IDS")
        )
    )
    seen: set[str] = set()
    ordered: list[str] = []
    for chat_id in combined:
        if chat_id in seen:
            continue
        seen.add(chat_id)
        ordered.append(chat_id)
    return ordered

# ── Core send ─────────────────────────────────────────────────────────────────

def send_telegram(
    message: str,
    chat_id: str = "",
    bot_token: str = "",
    *,
    parse_mode: str = "HTML",
    reply_markup: dict | None = None,
    timeout: float = 10.0,
) -> dict:
    """
    Send a Telegram message.  Returns the API response dict.
    Falls back to env vars if chat_id / bot_token not provided.
    """
    token = _resolve_setting_value({}, "telegram_bot_token", "TELEGRAM_BOT_TOKEN", explicit=bot_token)
    chat = _resolve_setting_value({}, "telegram_chat_id", "TELEGRAM_CHAT_ID", explicit=chat_id)

    if not token or not chat:
        logger.warning("Telegram not configured (missing token or chat_id)")
        return {"ok": False, "error": "not_configured"}

    url  = f"{_BASE.format(token=token)}/sendMessage"
    body: dict = {
        "chat_id":    chat,
        "text":       message,
        "parse_mode": parse_mode,
    }
    if reply_markup:
        body["reply_markup"] = reply_markup

    try:
        resp = requests.post(url, json=body, timeout=timeout)
        data = resp.json()
        if not data.get("ok"):
            logger.warning("Telegram send failed: %s", data)
        return data
    except Exception as exc:
        logger.error("Telegram request error: %s", exc)
        return {"ok": False, "error": str(exc)}


def send_telegram_many(
    message: str,
    *,
    chat_ids: list[str] | None = None,
    bot_token: str = "",
    settings: dict | None = None,
    parse_mode: str = "HTML",
    reply_markup: dict | None = None,
    timeout: float = 10.0,
) -> dict:
    resolved_chat_ids = chat_ids or configured_operator_chat_ids(settings)
    if not resolved_chat_ids:
        return {"ok": False, "error": "not_configured", "results": []}

    results = [
        send_telegram(
            message,
            chat_id=chat_id,
            bot_token=bot_token,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
            timeout=timeout,
        )
        for chat_id in resolved_chat_ids
    ]
    ok = any(bool(result.get("ok")) for result in results)
    return {
        "ok": ok,
        "results": results,
        "sent_count": sum(1 for result in results if result.get("ok")),
        "attempted_count": len(results),
    }


# ── Inline keyboard helpers ───────────────────────────────────────────────────

def _inline_keyboard(*rows: list[tuple[str, str]]) -> dict:
    """
    Build a Telegram InlineKeyboardMarkup.
    Each row is a list of (label, callback_data) tuples.
    """
    return {
        "inline_keyboard": [
            [{"text": label, "callback_data": data} for label, data in row]
            for row in rows
        ]
    }


# ── Verification / CAPTCHA challenge ─────────────────────────────────────────

def _make_challenge_token(secret: str = "") -> tuple[str, str]:
    """Return (token, expected_answer) for a simple numeric CAPTCHA."""
    import random
    a, b    = random.randint(1, 9), random.randint(1, 9)
    answer  = str(a + b)
    payload = f"{a}+{b}:{time.time():.0f}:{secret}"
    token   = hashlib.sha256(payload.encode()).hexdigest()[:16]
    return token, answer


def send_verification_challenge(
    chat_id: str = "",
    bot_token: str = "",
    context: str = "login",
    settings: dict | None = None,
) -> dict:
    """
    Send a human verification challenge via inline keyboard buttons.
    Returns dict with challenge token and expected answer for server-side validation.
    """
    token, answer = _make_challenge_token(bot_token)

    import random
    options = sorted({answer, str(random.randint(1,18)), str(random.randint(1,18)), str(random.randint(1,18))})
    # Build two rows of 2 buttons each
    row1 = [(opt, f"verify:{token}:{opt}") for opt in options[:2]]
    row2 = [(opt, f"verify:{token}:{opt}") for opt in options[2:]]

    message = (
        f"<b>DeG\u00a3N\u0024 — Human Verification</b>\n\n"
        f"Context: <code>{context}</code>\n\n"
        f"To confirm it's you, tap the correct answer to:\n"
        f"<b>What is the result of this sum?</b>\n\n"
        f"Tap a button below \u2b07\ufe0f"
    )

    resolved_token = _resolve_setting_value(settings, "telegram_bot_token", "TELEGRAM_BOT_TOKEN", explicit=bot_token)
    if chat_id:
        result = send_telegram(
            message,
            chat_id=chat_id,
            bot_token=resolved_token,
            reply_markup=_inline_keyboard(row1, row2),
        )
    else:
        result = send_telegram_many(
            message,
            chat_ids=configured_operator_chat_ids(settings),
            bot_token=resolved_token,
            settings=settings,
            reply_markup=_inline_keyboard(row1, row2),
        )
    result["challenge_token"] = token
    result["expected_answer"] = answer
    return result


def send_human_verification_prompt(
    *,
    prompt: str = "Human verification required. Are you present and able to handle the challenge?",
    chat_id: str = "",
    bot_token: str = "",
    context: str = "verification",
    settings: dict | None = None,
) -> dict:
    """
    Send a simple operator decision prompt with Yes / No / Skip buttons.
    Useful when an external venue presents a CAPTCHA or other human check.
    """
    message = (
        f"<b>DeG\u00a3N\u0024 — Human Check Needed</b>\n\n"
        f"Context: <code>{context}</code>\n\n"
        f"{prompt}\n\n"
        f"Choose an action below."
    )
    markup = _inline_keyboard(
        [("Yes", f"human:{context}:yes"), ("No", f"human:{context}:no"), ("Skip", f"human:{context}:skip")]
    )
    resolved_token = _resolve_setting_value(settings, "telegram_bot_token", "TELEGRAM_BOT_TOKEN", explicit=bot_token)
    if chat_id:
        return send_telegram(
            message,
            chat_id=chat_id,
            bot_token=resolved_token,
            reply_markup=markup,
        )
    return send_telegram_many(
        message,
        chat_ids=configured_operator_chat_ids(settings),
        bot_token=resolved_token,
        settings=settings,
        reply_markup=markup,
    )


# ── Notification helpers ──────────────────────────────────────────────────────

def notify_milestone(bot_id: str, bankroll: float, **kwargs) -> dict:
    msg = (
        f"\U0001f4b0 <b>Milestone Hit!</b>\n"
        f"Bot: <code>{bot_id}</code>\n"
        f"Bankroll: <b>${bankroll:.2f}</b>"
    )
    return send_telegram(msg, **kwargs)


def notify_circuit_breaker(bot_id: str, reason: str, cooldown_s: float, **kwargs) -> dict:
    msg = (
        f"\u26a0\ufe0f <b>Circuit Breaker</b>\n"
        f"Bot: <code>{bot_id}</code>\n"
        f"Reason: {reason}\n"
        f"Cooling down: <b>{cooldown_s:.0f}s</b>"
    )
    return send_telegram(msg, **kwargs)


def notify_halt(bot_id: str, bankroll: float, floor: float, **kwargs) -> dict:
    msg = (
        f"\U0001f6d1 <b>Bot Halted — Floor Hit</b>\n"
        f"Bot: <code>{bot_id}</code>\n"
        f"Bankroll: <b>${bankroll:.2f}</b> (floor: ${floor:.2f})\n"
        f"Manual intervention required."
    )
    return send_telegram(msg, **kwargs)


def notify_target_reached(bot_id: str, bankroll: float, target: float, **kwargs) -> dict:
    msg = (
        f"\U0001f3af <b>Target Reached!</b>\n"
        f"Bot: <code>{bot_id}</code>\n"
        f"Bankroll: <b>${bankroll:.2f}</b> / target ${target:.2f}\n"
        f"Consider withdrawing profits."
    )
    return send_telegram(msg, **kwargs)


# ── Advanced operator commands ────────────────────────────────────────────────

def notify_emergency_stop(
    reason: str = "OPERATOR_COMMAND",
    duration_minutes: float = 60.0,
    triggered_by: str = "manual",
    **kwargs,
) -> dict:
    """Notify operator that emergency stop was triggered."""
    msg = (
        f"\U0001f6a8 <b>EMERGENCY STOP ACTIVATED</b>\n\n"
        f"Reason: <code>{reason}</code>\n"
        f"Duration: <b>{duration_minutes:.0f} minutes</b>\n"
        f"Triggered by: {triggered_by}\n\n"
        f"All bots are now halted. Use /resume to restart."
    )
    return send_telegram(msg, **kwargs)


def notify_kelly_recommendation(
    market: str,
    model_prob: float,
    market_prob: float,
    kelly_size_pct: float,
    bet_usd: float,
    bankroll: float,
    phase: str = "normal",
    **kwargs,
) -> dict:
    """Send Kelly-sized bet recommendation to operator."""
    edge = round((model_prob - market_prob) * 100, 2)
    msg = (
        f"\U0001f4ca <b>Kelly Signal</b>\n\n"
        f"Market: <code>{market}</code>\n"
        f"Model prob: <b>{model_prob:.1%}</b> | Market: <b>{market_prob:.1%}</b>\n"
        f"Edge: <b>{edge:+.2f}%</b>\n"
        f"Kelly size: <b>{kelly_size_pct:.2%}</b> of bankroll\n"
        f"Bet amount: <b>${bet_usd:.2f}</b> / ${bankroll:.2f}\n"
        f"Phase: <code>{phase}</code>"
    )
    markup = _inline_keyboard(
        [("Approve", f"kelly:approve:{market}"), ("Skip", f"kelly:skip:{market}")],
        [("Emergency Stop", "emergency:stop:manual")],
    )
    return send_telegram(msg, reply_markup=markup, **kwargs)


def notify_phase_change(
    bot_id: str,
    old_phase: str,
    new_phase: str,
    bankroll: float,
    reason: str = "",
    **kwargs,
) -> dict:
    """Notify operator of phase transition."""
    PHASE_EMOJI = {
        "floor": "\U0001f534", "ultra_safe": "\U0001f7e0", "safe": "\U0001f7e1",
        "careful": "\U0001f7e2", "normal": "\U0001f535", "aggressive": "\U0001f7e3",
        "turbo": "\u26a1", "milestone": "\U0001f3c6",
    }
    old_e = PHASE_EMOJI.get(old_phase, "")
    new_e = PHASE_EMOJI.get(new_phase, "")
    msg = (
        f"\U0001f504 <b>Phase Change</b>\n\n"
        f"Bot: <code>{bot_id}</code>\n"
        f"{old_e} <code>{old_phase}</code> → {new_e} <code>{new_phase}</code>\n"
        f"Bankroll: <b>${bankroll:.2f}</b>\n"
        f"Reason: {reason or 'automatic'}"
    )
    return send_telegram(msg, **kwargs)


def send_daily_summary(
    total_pnl: float,
    starting_bankroll: float,
    current_bankroll: float,
    total_bets: int,
    win_rate: float,
    best_bot: str = "",
    phase_summary: dict | None = None,
    **kwargs,
) -> dict:
    """Send daily performance summary."""
    pnl_emoji = "\U0001f4c8" if total_pnl >= 0 else "\U0001f4c9"
    pnl_str = f"${total_pnl:+.2f} ({total_pnl/starting_bankroll*100:+.1f}%)" if starting_bankroll else f"${total_pnl:+.2f}"
    phases_str = ""
    if phase_summary:
        phases_str = "\nPhases: " + ", ".join(f"{k}:{v}" for k, v in phase_summary.items())

    msg = (
        f"{pnl_emoji} <b>Daily Summary</b>\n\n"
        f"P&amp;L: <b>{pnl_str}</b>\n"
        f"Bankroll: <b>${current_bankroll:.2f}</b> (started: ${starting_bankroll:.2f})\n"
        f"Bets: <b>{total_bets}</b> | Win rate: <b>{win_rate:.1%}</b>\n"
        f"Best bot: <code>{best_bot or 'N/A'}</code>"
        f"{phases_str}"
    )
    return send_telegram(msg, **kwargs)


def send_system_health_alert(
    status: str,
    error_count: int,
    adapter_errors: list[str],
    **kwargs,
) -> dict:
    """Send system health status to operator."""
    status_emoji = "\U0001f7e2" if status == "healthy" else "\U0001f7e1" if status == "degraded" else "\U0001f534"
    errors_str = "\n".join(f"  \u2022 {e}" for e in adapter_errors[:5]) if adapter_errors else "  None"
    msg = (
        f"{status_emoji} <b>System Health: {status.upper()}</b>\n\n"
        f"Adapter errors: <b>{error_count}</b>\n"
        f"{errors_str}"
    )
    return send_telegram(msg, **kwargs)


def send_signal_alert(
    signal: str,
    source: str,
    confidence: float,
    details: str = "",
    **kwargs,
) -> dict:
    """Send a research signal alert."""
    SIGNAL_EMOJI = {"bullish": "\U0001f7e2", "bearish": "\U0001f534", "neutral": "\u26aa"}
    emoji = SIGNAL_EMOJI.get(signal.lower(), "\u2139")
    msg = (
        f"{emoji} <b>Signal: {signal.upper()}</b>\n\n"
        f"Source: <code>{source}</code>\n"
        f"Confidence: <b>{confidence:.1%}</b>\n"
        f"{details}"
    )
    return send_telegram(msg, **kwargs)


def send_forex_snapshot(
    base_ccy: str,
    rates: dict,
    **kwargs,
) -> dict:
    """Send currency rates snapshot."""
    lines = [f"<b>Currency Rates (vs {base_ccy})</b>\n"]
    for code, rate in list(rates.items())[:15]:
        if rate is not None:
            lines.append(f"  {code}: <b>{rate:.4f}</b>")
    msg = "\n".join(lines)
    return send_telegram(msg, **kwargs)
