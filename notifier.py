"""
SMS notifications via Twilio.
DeG£N$ — short, rotating greetings. No long words.
"""

import os, random, logging, time
import database as db

logger    = logging.getLogger(__name__)
NOTIFY_TO = "+18257365656"

_GREETS = ["How di bodi", "Wesh", "Salafia", "E ai", "Wagwan", "Hey", "Yo"]

# One greeting per session — only rotates after 4+ hrs of silence
_session_greet    = random.choice(_GREETS)
_last_sms_time    = 0.0
_GREET_ROTATE_SEC = 4 * 60 * 60   # 4 hours

def _g() -> str:
    global _session_greet, _last_sms_time
    now = time.time()
    if now - _last_sms_time >= _GREET_ROTATE_SEC:
        _session_greet = random.choice(_GREETS)
    _last_sms_time = now
    return _session_greet

def _get_creds():
    sid   = os.environ.get("TWILIO_ACCOUNT_SID",  "") or db.get_setting("twilio_account_sid")
    token = os.environ.get("TWILIO_AUTH_TOKEN",   "") or db.get_setting("twilio_auth_token")
    frm   = os.environ.get("TWILIO_FROM_NUMBER",  "") or db.get_setting("twilio_from_number")
    phone = db.get_setting("notify_phone") or NOTIFY_TO
    return sid, token, frm, phone

def send_sms(message: str, real_money: bool = False) -> bool:
    """Only sends if real_money=True. Paper/sim events are logged only, never texted."""
    sid, token, frm, phone = _get_creds()
    db.save_notification(message, sent=False)
    if not real_money:
        logger.info(f"[SMS-BLOCKED paper] {message[:80]}")
        return False
    if not all([sid, token, frm]):
        logger.info(f"[SMS-STUB → {phone}] {message}")
        return False
    try:
        from twilio.rest import Client
        Client(sid, token).messages.create(body=message[:1600], from_=frm, to=phone)
        logger.info(f"[SMS SENT → {phone}] {message[:80]}")
        return True
    except Exception as e:
        logger.error(f"[SMS FAIL] {e}")
        return False

_last_milestone = 0.0

def _is_live() -> bool:
    """True only when real credentials are set and paper mode is off."""
    return bool(
        db.get_setting("stake_api_token") or db.get_setting("poly_private_key")
    ) and db.get_setting("execution_mode", "paper") != "paper"

def check_milestone(total_portfolio: float, initial: float, real_money: bool = False):
    global _last_milestone
    if not real_money:
        return
    inc    = float(db.get_setting("milestone_increment") or 100)
    gained = total_portfolio - initial
    if gained <= 0:
        return
    milestone = int(gained // inc) * inc
    if milestone > _last_milestone:
        _last_milestone = milestone
        send_sms(f"{_g()} — DeG£N$\n+${milestone:.0f} up\n${total_portfolio:.2f} total", real_money=True)

def notify_halt(bot_id: str, bankroll: float, real_money: bool = False):
    send_sms(f"{_g()} — DeG£N$\n{bot_id} hit floor\n${bankroll:.2f} left", real_money=real_money)

def notify_target(bot_id: str, progress: float, real_money: bool = False):
    send_sms(f"{_g()} — DeG£N$\n{bot_id} 10x'd\n${progress:.2f}", real_money=real_money)

def notify_circuit_breaker(bot_id: str, reason: str, pause_min: int, real_money: bool = False):
    send_sms(f"{_g()} — DeG£N$\n{bot_id} paused {pause_min}m\n{reason}", real_money=real_money)

def notify_vault_lock(amount: float, vault_total: float, real_money: bool = False):
    send_sms(f"{_g()} — DeG£N$\n${amount:.2f} locked\nvault ${vault_total:.2f}", real_money=real_money)

def notify_captcha(bot_id: str, detail: str = ""):
    # Always send — captcha needs human action regardless of mode
    send_sms(f"{_g()} — DeG£N$\n{bot_id} needs you\n{detail[:40]}", real_money=True)

def notify_startup():
    # Never text on startup — it's always simulated at boot
    logger.info("[SMS-BLOCKED] startup — paper mode")

def notify_shutdown():
    logger.info("[SMS-BLOCKED] shutdown — paper mode")
