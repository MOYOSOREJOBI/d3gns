"""
FastAPI server – 6-bot command center backend.

Features:
  • Password auth (token-based, 24h expiry)
  • SQLite persistence (trades, equity, events, notes, wallet, notifications)
  • SMS alerts via Twilio (milestones, halts, targets)
  • Monte Carlo projections endpoint
  • Full CRUD for notes
  • Wallet tracking with simulated deposits
  • Settings management

"""

import os, hashlib, secrets, time, logging, threading, collections, json, hmac, contextvars
import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from typing import Any, Set

import bcrypt
from fastapi import FastAPI, HTTPException, Request, Body, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, FileResponse

# ── Stake client: save original real functions before any patching ────────────
import paper_stake, stake_client as _sc

# Stash real functions under private names so we can restore them later
_sc._real_dice_roll   = _sc.dice_roll
_sc._real_limbo_game  = _sc.limbo_game
_sc._real_mines_play  = _sc.mines_play
_sc._real_get_balance = _sc.get_balance

import database as db
db.init_db()   # must run before any logging handler touches the DB
import notifier
from bots.catalog       import build_catalog, instantiate_bot, load_catalog_registry
from config           import BOT_INITIAL_BANK, BET_DELAY_SECONDS
import config as _cfg
from risk_manager     import RiskManager
from services import execution_authority as execution_authority
from services import quota_budgeter
from services.market_registry import build_default_market_registry
from services.home_content import build_home_payload
from services.platform_health_service import PlatformHealthService
from services.runtime_health import RuntimeHealthTracker
from services.signal_logger import persist_signal
from stake_strategies import make_strategy
from services.truth_labels import (
    build_system_truth,
    network_routing_truth,
    reconciliation_state,
    storage_mode_from_db_path,
    venue_auth_truth,
)
from paper_polymarket    import PaperPolymarketBot
from btc_momentum        import BtcMomentumBot
from intra_arb           import IntraArbBot
from resolution_sniper   import ResolutionSniperBot
from volume_spike        import VolumeSpikeBot

_POLY_STRATEGY_MAP = {
    "edge_scanner"       : PaperPolymarketBot,
    "btc_momentum"       : BtcMomentumBot,
    "intra_arb"          : IntraArbBot,
    "resolution_sniper"  : ResolutionSniperBot,
    "volume_spike"       : VolumeSpikeBot,
}

_cfg.BET_DELAY_SECONDS = max(BET_DELAY_SECONDS / 10.0, 0.05)

# ── Logging / request context ────────────────────────────────────────────────
_request_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


class _RequestContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id_ctx.get("-")
        return True


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "level": record.levelname,
            "module": record.name,
            "request_id": getattr(record, "request_id", "-"),
            "message": record.getMessage(),
        }
        for attr in ("bot_id", "platform", "path", "method", "status_code", "duration_ms"):
            value = getattr(record, attr, None)
            if value not in (None, ""):
                payload[attr] = value
        if record.exc_info:
            payload["traceback"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=True)


def _set_request_id(request_id: str) -> contextvars.Token:
    return _request_id_ctx.set(request_id or "-")


def _clear_request_id(token: contextvars.Token | None) -> None:
    if token is None:
        return
    try:
        _request_id_ctx.reset(token)
    except Exception:
        pass


def _current_request_id() -> str:
    return _request_id_ctx.get("-")


def _configure_logging() -> logging.Logger:
    log_dir = os.environ.get("LOG_DIR", ".")
    try:
        os.makedirs(log_dir, exist_ok=True)
    except Exception:
        log_dir = "."
    log_file = os.path.join(log_dir, "server.log")
    formatter = _JsonFormatter()
    request_filter = _RequestContextFilter()

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    stream_handler.addFilter(request_filter)

    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.addFilter(request_filter)

    logging.basicConfig(
        level=logging.INFO,
        handlers=[stream_handler, file_handler],
        force=True,
    )
    return logging.getLogger(__name__)


logger = _configure_logging()


# ── Auth ──────────────────────────────────────────────────────────────────────
try:
    from passlib.context import CryptContext as _CryptContext
    _pwd_ctx = _CryptContext(
        schemes=["argon2", "bcrypt_sha256", "bcrypt"],
        deprecated="auto",
        argon2__type="ID",
    )
    _PASSLIB_AVAILABLE = True
except ImportError:
    _PASSLIB_AVAILABLE = False


def _pbkdf2_hash(pwd: str, salt_hex: str | None = None) -> str:
    salt = bytes.fromhex(salt_hex) if salt_hex else secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", pwd.encode(), salt, 200_000)
    return f"pbkdf2_sha256${salt.hex()}${digest.hex()}"


def _pbkdf2_verify(pwd: str, hashed: str) -> bool:
    try:
        _, salt_hex, _ = hashed.split("$", 2)
    except ValueError:
        return False
    expected = _pbkdf2_hash(pwd, salt_hex=salt_hex)
    return hmac.compare_digest(expected, hashed)

def _hash(pwd: str) -> str:
    if _PASSLIB_AVAILABLE:
        try:
            return _pwd_ctx.hash(pwd)
        except Exception:
            logger.warning("Passlib Argon2 backend unavailable, falling back to bcrypt compatibility hashing.")
    try:
        return bcrypt.hashpw(pwd.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    except Exception:
        pass
    if _PASSLIB_AVAILABLE:
        logger.warning("Passlib hash backend unavailable, falling back to PBKDF2 hash compatibility path.")
    return _pbkdf2_hash(pwd)

def _verify_hash(pwd: str, hashed: str) -> bool:
    if _PASSLIB_AVAILABLE and hashed.startswith("$"):
        try:
            return _pwd_ctx.verify(pwd, hashed)
        except Exception:
            logger.warning("Passlib verify failed for stored hash; falling back to bcrypt/PBKDF2 compatibility verify.")
    if hashed.startswith(("$2a$", "$2b$", "$2y$")):
        try:
            return bcrypt.checkpw(pwd.encode("utf-8"), hashed.encode("utf-8"))
        except Exception:
            logger.warning("Direct bcrypt verify failed; trying compatibility fallback.")
    if hashed.startswith("pbkdf2_sha256$"):
        return _pbkdf2_verify(pwd, hashed)
    return False


def _password_strength_issues(password: str) -> list[str]:
    value = str(password or "")
    lowered = value.lower()
    issues: list[str] = []
    has_symbol = any(not char.isalnum() for char in value)
    is_long_passphrase = len(value) >= 16 and has_symbol
    if len(value) < 12:
        issues.append("Use at least 12 characters.")
    if not is_long_passphrase and (value.lower() == value or value.upper() == value):
        issues.append("Use both upper and lower case letters.")
    if not is_long_passphrase and not any(char.isdigit() for char in value):
        issues.append("Add at least one number.")
    if not has_symbol:
        issues.append("Add at least one symbol.")
    if any(token in lowered for token in ("changeme", "password", "123456", "qwerty", "letmein", "admin")):
        issues.append("Avoid common or predictable passwords.")
    return issues

def _load_stored_hash() -> str:
    """Load hash from DB, fall back to env var default."""
    primary = db.get_setting("auth_password_hash", "")
    if primary:
        return primary
    legacy = db.get_setting("dashboard_password_hash", "")
    if legacy:
        db.set_setting("auth_password_hash", legacy)
        return legacy
    default_pwd = os.getenv("AUTH_PASSWORD", "changeme")
    h = _hash(default_pwd)
    db.set_setting("auth_password_hash", h)
    db.set_setting("dashboard_password_hash", h)
    return h

AUTH_PASSWORD   = os.getenv("AUTH_PASSWORD", "changeme")
_STORED_HASH    = ""  # populated after DB init in lifespan
_VALID_TOKENS   : dict[str, dict[str, float] | float] = {}   # token → auth token metadata
_AUTH_FAILURES  : dict[str, dict[str, object]] = {}
_auth_lock      = threading.Lock()


def _hash_needs_upgrade(hashed: str) -> bool:
    value = str(hashed or "")
    if not value:
        return True
    if value.startswith("$argon2id$"):
        if _PASSLIB_AVAILABLE:
            try:
                return _pwd_ctx.needs_update(value)
            except Exception:
                return False
        return False
    return True


def _is_legacy_sha256_hash(hashed: str) -> bool:
    value = str(hashed or "").strip().lower()
    return len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _maybe_upgrade_legacy_password_hash() -> bool:
    global _STORED_HASH
    stored = _STORED_HASH or _load_stored_hash()
    if not _is_legacy_sha256_hash(stored):
        return False
    env_password = os.getenv("AUTH_PASSWORD", "").strip()
    if not env_password:
        logger.error("Legacy SHA-256 password hash detected, but AUTH_PASSWORD is unavailable for migration.")
        return False
    if not hmac.compare_digest(hashlib.sha256(env_password.encode()).hexdigest(), stored):
        logger.error("Legacy SHA-256 password hash detected, but AUTH_PASSWORD does not match the stored hash.")
        return False
    upgraded = _hash(env_password)
    _persist_password_hash(upgraded, rotated=False)
    try:
        db.save_auth_health_event(
            "auth_password",
            "legacy_hash_upgraded",
            "sha256_legacy",
            {"request_id": _current_request_id(), "upgraded_to": "argon2id"},
        )
    except Exception:
        pass
    logger.warning("Legacy SHA-256 password hash was upgraded to Argon2id during startup migration.")
    return True


def _using_default_password() -> bool:
    hashed = _STORED_HASH or _load_stored_hash()
    return bool(hashed) and _verify_hash("changeme", hashed)


def _persist_password_hash(new_hash: str, *, rotated: bool = True) -> None:
    global _STORED_HASH
    _STORED_HASH = new_hash
    db.set_setting("auth_password_hash", new_hash)
    db.set_setting("dashboard_password_hash", new_hash)
    stamp_key = "auth_password_rotated_at" if rotated else "auth_password_upgraded_at"
    db.set_setting(stamp_key, datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"))


def _token_absolute_ttl_s() -> float:
    return float(max(1, int(getattr(_cfg, "AUTH_TOKEN_TTL_HOURS", 24))) * 3600)


def _token_idle_ttl_s() -> float:
    return float(max(0, int(getattr(_cfg, "AUTH_TOKEN_IDLE_MINUTES", 240))) * 60)


def _new_token_record(*, now: float | None = None) -> dict[str, float]:
    current = float(now if now is not None else time.time())
    return {
        "issued_at": current,
        "last_seen": current,
        "expires_at": current + _token_absolute_ttl_s(),
    }


def _normalize_token_record(record: dict[str, float] | float | int | None, *, now: float) -> dict[str, float]:
    if isinstance(record, dict):
        expires_at = float(record.get("expires_at", 0) or 0)
        issued_at = float(record.get("issued_at", 0) or 0)
        last_seen = float(record.get("last_seen", 0) or 0)
        if not issued_at and expires_at:
            issued_at = max(0.0, expires_at - _token_absolute_ttl_s())
        if not last_seen:
            last_seen = issued_at or now
        return {
            "issued_at": issued_at or now,
            "last_seen": last_seen,
            "expires_at": expires_at,
        }
    expires_at = float(record or 0)
    issued_at = max(0.0, expires_at - _token_absolute_ttl_s()) if expires_at else now
    return {
        "issued_at": issued_at,
        "last_seen": now,
        "expires_at": expires_at,
    }


def _prune_and_resolve_token_locked(now: float, token: str = "", *, touch: bool = False) -> dict[str, float] | None:
    idle_ttl_s = _token_idle_ttl_s()
    resolved: dict[str, float] | None = None
    for candidate, record in list(_VALID_TOKENS.items()):
        normalized = _normalize_token_record(record, now=now)
        expired = normalized["expires_at"] <= now
        idle_expired = idle_ttl_s > 0 and (now - normalized["last_seen"]) >= idle_ttl_s
        if expired or idle_expired:
            _VALID_TOKENS.pop(candidate, None)
            continue
        if touch and candidate == token:
            normalized["last_seen"] = now
        _VALID_TOKENS[candidate] = normalized
        if candidate == token:
            resolved = dict(normalized)
    return resolved


def _cleanup_expired_tokens() -> None:
    now = time.time()
    with _auth_lock:
        _prune_and_resolve_token_locked(now)


def _check_token(req: Request):
    token = req.headers.get("Authorization","").replace("Bearer ","").strip()
    now = time.time()
    with _auth_lock:
        record = _prune_and_resolve_token_locked(now, token, touch=True) if token else None
    if not token or not record:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return token


def _settings_body_items(body: dict) -> list[tuple[str, object]]:
    if "key" in body:
        key = str(body.get("key", "") or "").strip()
        if not key:
            return []
        return [(key, body.get("value", ""))]
    return [
        (str(key or "").strip(), value)
        for key, value in body.items()
        if str(key or "").strip() and str(key or "").strip() not in {"reauth_password"}
    ]


def _audit_reauth_event(event_type: str, failure_type: str, *, reason: str, req: Request) -> None:
    try:
        db.save_auth_health_event(
            "auth_password",
            event_type,
            failure_type,
            {
                "request_id": _current_request_id(),
                "reason": reason,
                "path": str(getattr(getattr(req, "url", None), "path", "") or ""),
            },
        )
    except Exception:
        pass


def _require_reauth_password(req: Request, provided_password: str, *, reason: str) -> None:
    password = str(provided_password or "")
    if not password:
        _audit_reauth_event("reauth_failed", "missing_password", reason=reason, req=req)
        raise HTTPException(401, f"Operator password confirmation required for {reason}.")
    if not _verify_hash(password, _STORED_HASH):
        _audit_reauth_event("reauth_failed", "invalid_password", reason=reason, req=req)
        raise HTTPException(401, "Operator password confirmation is incorrect.")
    if _hash_needs_upgrade(_STORED_HASH):
        _persist_password_hash(_hash(password), rotated=False)
    _audit_reauth_event("reauth_verified", "password_ok", reason=reason, req=req)


def _setting_requires_reauth(key: str) -> bool:
    from utils.crypto import is_sensitive_key

    normalized_key = str(key or "").strip()
    if not normalized_key:
        return False
    return normalized_key in {"dashboard_password", "auth_password"} or is_sensitive_key(normalized_key)


def _client_key(req: Request, device_id: str = "") -> str:
    client = getattr(req, "client", None)
    host = getattr(client, "host", None) or req.headers.get("x-forwarded-for", "").split(",")[0].strip() or "local"
    return f"{host}:{device_id or 'anon'}"


def _auth_guard(req: Request, device_id: str = "") -> None:
    key = _client_key(req, device_id=device_id)
    now = time.time()
    with _auth_lock:
        entry = _AUTH_FAILURES.get(key)
        if not entry:
            return
        locked_until = float(entry.get("locked_until", 0) or 0)
        if locked_until > now:
            raise HTTPException(status_code=429, detail="Too many failed login attempts. Try again later.")


def _record_auth_attempt(req: Request, *, ok: bool, device_id: str = "") -> None:
    key = _client_key(req, device_id=device_id)
    now = time.time()
    with _auth_lock:
        entry = _AUTH_FAILURES.setdefault(key, {"attempts": collections.deque(), "locked_until": 0.0})
        attempts = entry["attempts"]
        assert isinstance(attempts, collections.deque)
        window_s = getattr(_cfg, "AUTH_FAILURE_WINDOW_S", 900)
        while attempts and (now - attempts[0]) > window_s:
            attempts.popleft()
        if ok:
            attempts.clear()
            entry["locked_until"] = 0.0
            return
        attempts.append(now)
        if len(attempts) >= getattr(_cfg, "AUTH_MAX_FAILED_ATTEMPTS", 5):
            entry["locked_until"] = now + getattr(_cfg, "AUTH_LOCKOUT_S", 300)


def _runtime_settings_env_first() -> bool:
    return bool(os.getenv("FLY_APP_NAME"))


def _get_runtime_setting(key: str, default: str = "") -> str:
    def _env_value() -> str:
        for candidate in (key, key.upper(), key.lower()):
            env_value = os.getenv(candidate)
            if env_value not in (None, ""):
                return str(env_value)
        return ""

    def _db_value() -> str:
        for candidate in (key.lower(), key):
            db_value = db.get_setting(candidate, "")
            if db_value not in (None, ""):
                return str(db_value)
        return ""

    if _runtime_settings_env_first():
        resolved = _env_value() or _db_value()
    else:
        resolved = _db_value() or _env_value()
    if resolved not in (None, ""):
        return resolved
    return default


_RUNTIME_SETTING_ENV_KEYS = {
    "stake_api_token": "STAKE_API_TOKEN",
    "poly_private_key": "POLY_PRIVATE_KEY",
    "poly_api_key": "POLY_API_KEY",
    "poly_api_secret": "POLY_API_SECRET",
    "poly_api_passphrase": "POLY_API_PASSPHRASE",
    "telegram_bot_token": "TELEGRAM_BOT_TOKEN",
    "telegram_chat_id": "TELEGRAM_CHAT_ID",
    "telegram_operator_chat_ids": "TELEGRAM_OPERATOR_CHAT_IDS",
    "telegram_webhook_secret": "TELEGRAM_WEBHOOK_SECRET",
    "twilio_account_sid": "TWILIO_ACCOUNT_SID",
    "twilio_auth_token": "TWILIO_AUTH_TOKEN",
    "twilio_from_number": "TWILIO_FROM_NUMBER",
    "notify_phone": "NOTIFY_PHONE",
    "live_execution_enabled": "LIVE_EXECUTION_ENABLED",
    "stake_live_enabled": "STAKE_LIVE_ENABLED",
    "polymarket_live_enabled": "POLYMARKET_LIVE_ENABLED",
}


def _runtime_settings_snapshot() -> dict[str, str]:
    settings = db.get_all_settings()
    env_first = _runtime_settings_env_first()
    for key, env_key in _RUNTIME_SETTING_ENV_KEYS.items():
        env_value = os.getenv(env_key)
        if env_value not in (None, ""):
            current = settings.get(key, "")
            if env_first or current in (None, ""):
                settings[key] = env_value
    return settings


def _boolish(value, default: bool = False) -> bool:
    lowered = str(value or "").strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _live_control_flag(env_key: str, default: bool = False) -> bool:
    return _boolish(_get_runtime_setting(env_key, ""), default=default)


def _live_controls_snapshot() -> dict[str, dict]:
    global_enabled = _live_control_flag("LIVE_EXECUTION_ENABLED", default=False)
    stake_enabled = _live_control_flag("STAKE_LIVE_ENABLED", default=False)
    polymarket_enabled = _live_control_flag("POLYMARKET_LIVE_ENABLED", default=False)
    return {
        "global": {
            "key": "live_execution_enabled",
            "enabled": global_enabled,
        },
        "platforms": {
            "stake": {
                "key": "stake_live_enabled",
                "enabled": stake_enabled,
                "effective_enabled": bool(global_enabled and stake_enabled),
            },
            "polymarket": {
                "key": "polymarket_live_enabled",
                "enabled": polymarket_enabled,
                "effective_enabled": bool(global_enabled and polymarket_enabled),
            },
        },
    }


def _platform_live_enabled(platform: str) -> bool:
    return bool((_live_controls_snapshot()["platforms"].get(platform) or {}).get("effective_enabled"))


def _stake_live_running() -> bool:
    return _sc.dice_roll is not paper_stake.dice_roll

# ── Stake mode switching ──────────────────────────────────────────────────────
import stake_client as _sc
_live_runtime_requested = False

def _patch_stake_paper():
    _sc.dice_roll   = paper_stake.dice_roll
    _sc.limbo_game  = paper_stake.limbo_game
    _sc.mines_play  = paper_stake.mines_play
    _sc.get_balance = paper_stake.get_balance
    logger.info("Stake: PAPER mode")

def _patch_stake_live():
    _sc.dice_roll   = _sc._real_dice_roll
    _sc.limbo_game  = _sc._real_limbo_game
    _sc.mines_play  = _sc._real_mines_play
    _sc.get_balance = _sc._real_get_balance
    logger.info("Stake: LIVE mode — real API calls active")

_patch_stake_paper()   # default until credentials loaded

# Preserve any configured token for health checks, but never auto-enable live
# execution until the operator explicitly starts the runtime.
import config as _cfg_init
if _cfg_init.STAKE_API_TOKEN:
    import stake_client as _sc_init
    _sc_init.HEADERS["x-access-token"] = _cfg_init.STAKE_API_TOKEN
    _patch_stake_paper()
    logger.info("Stake token loaded from config.py — runtime remains idle until /api/start.")


def _sync_stake_client_mode(*, live_requested: bool | None = None) -> bool:
    global _live_runtime_requested
    if live_requested is not None:
        _live_runtime_requested = bool(live_requested)

    import stake_client as stake_client
    import config as runtime_config

    stake_tok = _get_runtime_setting("STAKE_API_TOKEN", "").strip()
    if stake_tok and "*" not in stake_tok and "•" not in stake_tok:
        runtime_config.STAKE_API_TOKEN = stake_tok
        stake_client.HEADERS["x-access-token"] = stake_tok
    else:
        runtime_config.STAKE_API_TOKEN = ""
        stake_client.HEADERS["x-access-token"] = ""
        stake_tok = ""

    live_enabled = bool(_live_runtime_requested and _platform_live_enabled("stake") and stake_tok)
    if live_enabled:
        _patch_stake_live()
        logger.info("Stake live execution armed by explicit operator action.")
    else:
        _patch_stake_paper()
        logger.info("Stake execution remains in paper mode until live is explicitly armed.")
    try:
        db.set_setting("execution_mode", "live" if live_enabled else "paper")
    except Exception:
        pass
    return live_enabled

# ── Tor embedded VPN ─────────────────────────────────────────────────────────
import subprocess, shutil, re as _re

_tor_proc    = None
_tor_status  = "off"        # off | not_installed | starting | ready | failed
_tor_ip      = None
_tor_country = None

def _tor_socks():
    return {"http": "socks5h://127.0.0.1:9050", "https": "socks5h://127.0.0.1:9050"}

def _fetch_tor_ip():
    global _tor_ip, _tor_country
    try:
        import requests as _req
        r = _req.get("https://ipapi.co/json/", proxies=_tor_socks(), timeout=20)
        d = r.json()
        _tor_ip      = d.get("ip", "?")
        _tor_country = d.get("country_name", "?")
        logger.info(f"Tor exit: {_tor_ip} ({_tor_country})")
    except Exception as e:
        logger.warning(f"Tor IP check failed: {e}")

def _find_bin(name):
    found = shutil.which(name)
    if found: return found
    for p in [f"/usr/local/bin/{name}", f"/opt/homebrew/bin/{name}"]:
        if os.path.isfile(p): return p
    return None

def _start_tor():
    global _tor_proc, _tor_status
    if _tor_status == "ready":
        return
    tor_bin = _find_bin("tor")
    if not tor_bin:
        _tor_status = "not_installed"
        logger.info("Tor not installed — run: brew install tor")
        return
    if _tor_proc and _tor_proc.poll() is None:
        _tor_status = "ready"
        return
    _tor_status = "starting"
    try:
        import socket as _sock
        _tor_proc = subprocess.Popen(
            [tor_bin, "--SocksPort", "9050",
             "--ControlPort", "9051",
             "--CookieAuthentication", "0",
             "--DataDirectory", "/tmp/degens_tor",
             "--Log", "notice stderr"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        for _ in range(60):
            time.sleep(1)
            try:
                s = _sock.socket()
                s.settimeout(1)
                s.connect(("127.0.0.1", 9050))
                s.close()
                _tor_status = "ready"
                db.set_setting("proxy_host", "127.0.0.1")
                db.set_setting("proxy_port", "9050")
                logger.info("Tor SOCKS5 ready — all restricted platforms now routed through Tor")
                threading.Thread(target=_fetch_tor_ip, daemon=True).start()
                return
            except Exception:
                pass
        _tor_status = "failed"
        logger.warning("Tor started but SOCKS5 port never opened")
    except Exception as e:
        _tor_status = "failed"
        logger.error(f"Tor start error: {e}")

def _renew_tor_circuit():
    """Send NEWNYM signal to Tor to get a fresh circuit (new exit IP)."""
    global _tor_ip, _tor_country
    try:
        import socket as _sock
        ctl = _sock.socket()
        ctl.connect(("127.0.0.1", 9051))
        ctl.sendall(b"AUTHENTICATE \"\"\r\nSIGNAL NEWNYM\r\nQUIT\r\n")
        ctl.close()
        _tor_ip = None; _tor_country = None
        threading.Thread(target=_fetch_tor_ip, daemon=True).start()
        return True
    except Exception:
        return False

# ── Remote access tunnel (prefer Cloudflare, fallback to localtunnel) ────────
_tunnel_proc     = None
_tunnel_url      = None
_tunnel_status   = "off"   # off | starting | ready | failed | not_installed
_tunnel_provider = "off"
_TUNNEL_SLUG     = "d3gns"   # → https://d3gns.loca.lt


def _stop_tunnel_process():
    global _tunnel_proc, _tunnel_url, _tunnel_status, _tunnel_provider
    if _tunnel_proc and _tunnel_proc.poll() is None:
        try:
            _tunnel_proc.terminate()
            _tunnel_proc.wait(timeout=5)
        except Exception:
            try:
                _tunnel_proc.kill()
            except Exception:
                pass
    _tunnel_proc = None
    _tunnel_url = None
    _tunnel_status = "off"
    _tunnel_provider = "off"

def _start_tunnel():
    global _tunnel_proc, _tunnel_url, _tunnel_status, _tunnel_provider
    if not getattr(_cfg, "ENABLE_REMOTE_ACCESS", False):
        _stop_tunnel_process()
        return
    if _using_default_password():
        _tunnel_status = "failed"
        _tunnel_provider = "blocked"
        logger.warning("Remote tunnel blocked because the operator password is still using the default value.")
        return

    cloudflared = _find_bin("cloudflared")
    npx = _find_bin("npx") if getattr(_cfg, "ENABLE_LOCALTUNNEL", False) else None
    provider = None
    cmd = None
    pattern = None

    if cloudflared:
        provider = "cloudflare"
        cmd = [cloudflared, "tunnel", "--no-autoupdate", "--url", "http://127.0.0.1:8000"]
        pattern = r"https://[^\s]+\.trycloudflare\.com"
    elif npx:
        provider = "localtunnel"
        cmd = [npx, "localtunnel", "--port", "8000", "--subdomain", _TUNNEL_SLUG]
        pattern = r"https://[^\s]+\.loca\.lt"
    else:
        _tunnel_status = "not_installed"
        _tunnel_provider = "missing"
        logger.warning("Remote tunnel requested but neither cloudflared nor localtunnel is available.")
        return

    if _tunnel_proc and _tunnel_proc.poll() is None:
        return
    _tunnel_status = "starting"
    _tunnel_provider = provider
    _tunnel_url = None
    try:
        _tunnel_proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            universal_newlines=True, bufsize=1
        )
        def _read():
            global _tunnel_url, _tunnel_status, _tunnel_provider
            for line in _tunnel_proc.stdout:
                m = _re.search(pattern, line)
                if m:
                    _tunnel_url = m.group(0)
                    _tunnel_status = "ready"
                    db.set_setting("tunnel_url", _tunnel_url)
                    db.set_setting("tunnel_provider", _tunnel_provider)
                    logger.info(f"Remote tunnel ({_tunnel_provider}): {_tunnel_url}")
                    return
            if _tunnel_status == "starting":
                _tunnel_status = "failed"
        threading.Thread(target=_read, daemon=True).start()
    except Exception as e:
        _tunnel_status = "failed"
        _tunnel_provider = provider or "unknown"
        logger.error(f"Tunnel error: {e}")

# ── Shared state ──────────────────────────────────────────────────────────────
_stop_event      = threading.Event()
_rms             : dict[str, RiskManager] = {}
_equity_history  : list[dict]             = []
_log_entries     = collections.deque(maxlen=30)
_start_wall      : float | None           = None
_running         = False
_lock            = threading.Lock()
_tick            = 0
_initial_total   = 0.0
_bet_scales      : dict[str, float]       = {}   # per-bot multiplier
_strategy_modes  : dict[str, str]         = {}   # per-bot preset name
_goal_target     : float                  = 6000.0  # portfolio goal $
_recent_trade_nets: dict[str, collections.deque] = {}
_auto_manage_state: dict[str, dict] = {}

# ── Canary session state ───────────────────────────────────────────────────────
# dust-live / $1 canary sessions: bot_id → session dict
_canary_sessions: dict[str, dict] = {}
_poly_runtime    : dict[str, dict]        = {}
_runtime_health  = RuntimeHealthTracker()
_market_registry = None
_platform_health_service = None
_catalog_cache   : list[dict]             = []
_vault = None
_profit_ladder = None
_executor_owner  = execution_authority.build_owner("server", "fastapi_runtime")
_executor_claimed = False
_executor_state  : dict | None            = None

_STRATEGY_SCALES = {"conservative": 0.5, "balanced": 1.0, "aggressive": 2.0, "turbo": 3.5}
_LEGACY_EXPANSION_BOT_IDS = (
    "bot_kalshi_orderbook_imbalance_paper",
    "bot_kalshi_resolution_decay_paper",
    "bot_kalshi_pair_spread_paper",
    "bot_kalshi_demo_execution",
    "bot_oddsapi_consensus_outlier_paper",
    "bot_oddsapi_stale_line_scanner",
    "bot_oddsapi_clv_tracker",
    "bot_poly_kalshi_crossvenue_spread",
    "bot_polymarket_microstructure_paper",
    "bot_betfair_delayed_mirror",
    "bot_sportsdataio_line_movement_research",
    "bot_crossvenue_arb_watchlist",
)

# ── WebSocket connection manager ─────────────────────────────────────────────
class _WSManager:
    def __init__(self):
        self._clients: Set[WebSocket] = set()
        self._lock = threading.Lock()

    async def connect(self, ws: WebSocket):
        await ws.accept()
        with self._lock:
            self._clients.add(ws)

    def disconnect(self, ws: WebSocket):
        with self._lock:
            self._clients.discard(ws)

    async def broadcast(self, data: dict):
        msg = json.dumps(data)
        dead = set()
        with self._lock:
            clients = set(self._clients)
        for ws in clients:
            try:
                await ws.send_text(msg)
            except Exception:
                dead.add(ws)
        with self._lock:
            self._clients -= dead

    async def close_all(self, code: int = 1001):
        dead = set()
        with self._lock:
            clients = set(self._clients)
        for ws in clients:
            try:
                await ws.close(code=code)
            except Exception:
                dead.add(ws)
        with self._lock:
            self._clients -= clients

    def has_clients(self) -> bool:
        with self._lock:
            return bool(self._clients)

_ws_manager = _WSManager()

def _broadcast_sync(data: dict):
    """Fire-and-forget broadcast from a sync thread."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.run_coroutine_threadsafe(_ws_manager.broadcast(data), loop)
    except Exception:
        pass


def _init_platform_services():
    global _market_registry, _platform_health_service, _vault, _profit_ladder
    if _market_registry is None:
        _market_registry = build_default_market_registry(
            settings_getter=_get_runtime_setting,
            proxy_getter=_get_proxies,
        )
    if _platform_health_service is None:
        _platform_health_service = PlatformHealthService(
            _market_registry,
            db_module=db,
            settings_getter=_get_runtime_setting,
        )
    if _vault is None or _profit_ladder is None:
        from services.profit_ladder import ProfitLadder
        from vault import Vault

        _vault = _vault or Vault(db)
        _profit_ladder = _profit_ladder or ProfitLadder(db, _vault)
        wallet_summary = db.get_wallet_summary() if hasattr(db, "get_wallet_summary") else {}
        starting_capital = float(wallet_summary.get("deposits", 0) or 0) or 100.0
        _profit_ladder.initialize(starting_capital)


def _validate_credentials_on_boot() -> list[dict]:
    _init_platform_services()
    if _market_registry is None:
        return []

    from services.credential_validator import validate_platform

    platforms_checked: list[dict] = []
    for name in _market_registry.list_platforms():
        adapter = _market_registry.get(name)
        try:
            health = adapter.healthcheck()
        except Exception as exc:
            health = {
                "ok": False,
                "status": "error",
                "degraded_reason": str(exc),
            }
        credential = validate_platform(
            name,
            db_module=db,
            settings_getter=_get_runtime_setting,
            perform_network_check=False,
            use_cached=False,
        )
        status = "OK" if health.get("ok") else (health.get("degraded_reason") or credential.get("reason") or health.get("status", "unknown"))
        credential_state = "valid" if credential.get("credentials_valid") or credential.get("state") == "valid" else credential.get("state", "unknown")
        if hasattr(db, "save_credential_health"):
            db.save_credential_health(name, credential_state, reason=str(status))
        row = {
            "platform": name,
            "status": str(status),
            "ok": bool(health.get("ok")),
            "credential_state": credential_state,
        }
        platforms_checked.append(row)
        logger.info("[Boot] %s: %s", name, status)
    return platforms_checked


def _sync_expansion_catalog():
    global _catalog_cache
    _catalog_cache = build_catalog()
    db.sync_bot_catalog(_catalog_cache)
    for entry in _catalog_cache:
        db.set_bot_mode(entry["bot_id"], entry["mode"], entry)
        db.set_bot_config_state(entry["bot_id"], bool(entry.get("default_enabled")), entry)


def _claim_execution_authority() -> bool:
    global _executor_claimed, _executor_state
    claimed, row = execution_authority.claim(db, _executor_owner)
    _executor_state = execution_authority.describe(row, _executor_owner["owner_id"])
    _executor_claimed = bool(claimed and _executor_state.get("owned_by_self"))
    return _executor_claimed


def _refresh_executor_state():
    global _executor_state
    row = execution_authority.current(db)
    _executor_state = execution_authority.describe(row, _executor_owner["owner_id"])
    return _executor_state


def _release_execution_authority():
    global _executor_claimed, _executor_state
    if _executor_claimed:
        try:
            execution_authority.release(db, _executor_owner["owner_id"])
        except Exception:
            logger.exception("Failed to release execution authority.")
    _executor_claimed = False
    _executor_state = execution_authority.describe(None, _executor_owner["owner_id"])
    return _executor_state


def _persist_bot_runtime_snapshots(*, include_history: bool = False):
    for spec in _active_bot_specs():
        bid = spec["id"]
        rm = _rms.get(bid)
        if rm is None:
            continue
        runtime_health = _runtime_health.get(bid)
        payload = rm.serialize_runtime_state()
        db.upsert_bot_runtime_state(
            bid,
            platform=spec["platform"],
            phase=payload.get("phase", ""),
            bankroll=float(payload.get("bankroll", 0) or 0),
            vault=float(payload.get("vault", 0) or 0),
            streak=int(payload.get("streak", 0) or 0),
            cooldown_until=payload.get("cooldown_until"),
            runtime_health=runtime_health,
            payload=payload,
        )
        if include_history:
            db.save_bot_runtime_history(
                bid,
                platform=spec["platform"],
                phase=payload.get("phase", ""),
                bankroll=float(payload.get("bankroll", 0) or 0),
                vault=float(payload.get("vault", 0) or 0),
                streak=int(payload.get("streak", 0) or 0),
                cooldown_until=payload.get("cooldown_until"),
                runtime_health=runtime_health,
                payload=payload,
            )


def _executor_heartbeat_loop():
    global _executor_state
    while True:
        try:
            if _executor_claimed:
                row = execution_authority.heartbeat(db, _executor_owner["owner_id"])
                _executor_state = execution_authority.describe(row, _executor_owner["owner_id"])
            else:
                _refresh_executor_state()
        except Exception as exc:
            logger.warning(f"Executor heartbeat failed: {exc}")
        time.sleep(15)


def _legacy_platform_health() -> list[dict]:
    credentials = {platform: _credential_status_snapshot(platform) for platform in ("stake", "polymarket")}
    rows: list[dict] = []
    for platform in ("stake", "polymarket"):
        credential = credentials.get(platform, {})
        running_live = _platform_currently_running_live(platform)
        live_capable = bool(credential.get("live_capable"))
        validation_failed = bool(credential.get("validation_performed")) and not bool(credential.get("credentials_valid"))
        if running_live:
            status = "live"
            mode = "LIVE"
            data_truth_label = "LIVE"
            degraded_reason = ""
        elif live_capable:
            status = "live_capable"
            mode = "LIVE-CAPABLE"
            data_truth_label = "LIVE-CAPABLE"
            degraded_reason = "Live credentials are validated and idle until the operator explicitly starts the runtime."
        elif validation_failed:
            status = "degraded"
            mode = "DEGRADED"
            data_truth_label = "DEGRADED"
            degraded_reason = credential.get("reason", "Credential validation failed.")
        else:
            status = "paper"
            mode = "PAPER"
            data_truth_label = "PAPER"
            degraded_reason = credential.get("reason", "") if credential.get("credentials_present") else ""
        rows.append(
            {
                "ok": status != "degraded",
                "platform": platform,
                "configured": bool(credential.get("credentials_present")),
                "status": status,
                "degraded_reason": degraded_reason,
                "truth_labels": {
                    "mode": mode,
                    "live_capable": live_capable,
                    "live_enabled": bool(credential.get("live_enabled")),
                    "execution_enabled": running_live,
                    "auth_validated": bool(credential.get("credentials_valid")),
                    "auth_truth": venue_auth_truth(
                        credentials_present=bool(credential.get("credentials_present")),
                        validated=bool(credential.get("credentials_valid")),
                    ),
                    "data_truth_label": data_truth_label,
                },
                "credential_health": credential,
                "data": {"operator_flow_preserved": True, "display_state": credential.get("display_state", "missing")},
            }
        )
    return rows


def _build_system_truth():
    _init_platform_services()
    platform_rows = _platform_health_service.snapshot_all()
    legacy_rows = _legacy_platform_health()
    all_rows = legacy_rows + platform_rows
    storage_mode, storage_reason = storage_mode_from_db_path(db.DB_PATH)
    proxy_truth, proxy_reason = network_routing_truth(
        _get_runtime_setting("proxy_host", ""),
        _get_runtime_setting("proxy_port", ""),
        proxy_verified=bool(_get_runtime_setting("proxy_last_verified_ts", "")),
    )
    any_live_running = any(_platform_currently_running_live(platform) for platform in ("stake", "polymarket"))
    truth_rows = [row.get("truth_labels") or {} for row in all_rows]
    live_capable = any(
        bool(truth.get("live_capable")) and (
            row.get("configured")
            or truth.get("auth_truth") in {"present", "validated", "stale", "failed"}
        )
        for row, truth in zip(all_rows, truth_rows)
    )
    any_execution_enabled = any(bool(truth.get("execution_enabled")) for truth in truth_rows) or any_live_running
    execution_mode = "live" if any_execution_enabled and _executor_claimed else ("live_capable" if live_capable else "paper")
    venue_truth = {
        row["platform"]: ((row.get("truth_labels") or {}).get("auth_truth") or "missing")
        for row in all_rows
    }
    payload = build_system_truth(
        execution_mode=execution_mode,
        reconciliation_state_value=reconciliation_state(enabled_live_execution=any_live_running, externally_reconciled=False),
        storage_mode=storage_mode,
        storage_reason=storage_reason,
        wallet_truth="virtual_ledger",
        network_routing_truth_value=proxy_truth,
        network_routing_reason=proxy_reason,
        venue_auth_truth_map=venue_truth,
    )
    db.upsert_execution_truth(
        "system",
        payload["execution_mode"],
        payload["reconciliation_state"],
        payload["storage_mode"],
        payload["wallet_truth"],
        payload["network_routing_truth"],
        payload,
    )
    return payload, all_rows


def _get_kalshi_live_adapter(enable_execution: bool = False):
    from adapters.kalshi_live import KalshiLiveAdapter

    adapter = KalshiLiveAdapter(settings_getter=_get_runtime_setting, proxy_getter=_get_proxies)
    adapter.execution_enabled = bool(enable_execution)
    return adapter


def _extract_balance_value(payload):
    if isinstance(payload, (int, float)):
        return float(payload)
    if isinstance(payload, dict):
        for key in ("balance", "available_balance", "available", "cash_balance", "usd_balance"):
            value = payload.get(key)
            if isinstance(value, (int, float)):
                return float(value)
            if isinstance(value, str):
                try:
                    return float(value)
                except Exception:
                    continue
    return None


def _persist_kalshi_live_order_snapshot(body: dict, result: dict, *, status_override: str | None = None):
    order_data = (result.get("data") or {}).get("order") or {}
    order_id = str(
        order_data.get("order_id")
        or order_data.get("id")
        or order_data.get("orderId")
        or ""
    )
    ticker = str(body.get("ticker") or order_data.get("ticker") or "")
    side = str(body.get("side") or order_data.get("side") or "")
    amount_usd = float(body.get("amount_usd", 0) or 0)
    price = float(body.get("price_cents", 0) or 0) / 100.0
    status_value = status_override or str(result.get("status") or order_data.get("status") or "pending")
    payload = {
        "request": dict(body),
        "response": result,
        "external_order_id": order_id,
    }
    request_id = db.save_order_request(
        "kalshi_live",
        str(body.get("bot_id", "manual") or "manual"),
        ticker,
        side,
        amount_usd,
        price,
        execution_mode="LIVE",
        state=status_value,
        payload=payload,
    )
    fill_count = order_data.get("filled_count") or order_data.get("fill_count") or order_data.get("fill_size") or 0
    fill_price = order_data.get("avg_fill_price") or order_data.get("fill_price") or price
    try:
        fill_count = float(fill_count or 0)
        fill_price = float(fill_price or 0)
    except Exception:
        fill_count = 0.0
        fill_price = 0.0
    if fill_count > 0:
        db.save_order_fill(
            request_id,
            "kalshi_live",
            fill_price,
            fill_count,
            fill_type="live",
            payload={"external_order_id": order_id, "response": result},
        )
    db.save_order_lifecycle({
        "order_id": order_id,
        "bot_id": str(body.get("bot_id", "manual") or "manual"),
        "platform": "kalshi_live",
        "execution_mode": "LIVE",
        "side": side,
        "market_id": ticker,
        "amount": amount_usd,
        "price": price,
        "status": status_value,
        "fill_price": fill_price if fill_count > 0 else None,
        "fill_amount": fill_count if fill_count > 0 else None,
        "payload": result,
    })
    db.save_reconciliation_event(
        "kalshi_live",
        str(request_id),
        "order_snapshot",
        status_value,
        reason=result.get("degraded_reason", ""),
        payload={
            "external_order_id": order_id,
            "state": status_value,
            "fill_count": fill_count,
            "fill_price": fill_price if fill_count > 0 else None,
        },
    )
    return request_id


def _build_executor_status():
    state = execution_authority.get_state(db, _executor_owner.get("owner_id"), limit=20)
    try:
        db.set_runtime_truth("executor", state)
    except Exception:
        pass
    return state


def _build_storage_status():
    migrations = db.list_schema_migrations() if hasattr(db, "list_schema_migrations") else []
    last_migration = migrations[-1]["name"] if migrations else None
    db_path = getattr(db, "DB_PATH", _get_runtime_setting("DB_PATH", "bots.db"))
    db_basename = os.path.basename(db_path)
    db_dir = os.path.dirname(os.path.abspath(db_path)) or "."
    size_bytes = os.path.getsize(db_path) if os.path.exists(db_path) else 0
    writable = os.access(db_dir, os.W_OK)
    write_test_ok = False
    write_test_error = ""
    probe_path = os.path.join(db_dir, ".degens-write-test")
    try:
        with open(probe_path, "w", encoding="utf-8") as handle:
            handle.write("ok")
        os.remove(probe_path)
        write_test_ok = True
    except Exception as exc:
        write_test_error = str(exc)
    wal_mode = "unknown"
    row_counts = {}
    try:
        cx = db._cx()
        wal_mode = str(cx.execute("PRAGMA journal_mode").fetchone()[0]).lower()
        for table in ("trades", "research_signals", "bot_catalog", "simulator_runs", "credential_health"):
            try:
                row_counts[table] = int(cx.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            except Exception:
                row_counts[table] = 0
        cx.close()
    except Exception:
        pass
    status = {
        "db_path_basename": db_basename,
        "db_path": db_basename if getattr(_cfg, "REDACT_SECRETS", True) else db_path,
        "size_bytes": size_bytes,
        "writable": writable,
        "write_test_ok": write_test_ok,
        "write_test_error": write_test_error,
        "wal_mode": wal_mode,
        "migration_count": len(migrations),
        "last_migration": last_migration,
        "row_counts": row_counts,
    }
    try:
        db.set_runtime_truth("storage", status)
    except Exception:
        pass
    return status


def _platform_currently_running_live(platform: str) -> bool:
    if platform == "stake":
        return _stake_live_running()
    if platform == "polymarket":
        return any(str((runtime or {}).get("execution_mode", "")).lower() == "live" for runtime in _poly_runtime.values())
    return False


def _enrich_credential_results(results: dict[str, dict]) -> dict[str, dict]:
    controls = _live_controls_snapshot()
    enriched: dict[str, dict] = {}
    for platform, result in results.items():
        row = dict(result or {})
        live_flag = bool((controls["platforms"].get(platform) or {}).get("effective_enabled"))
        running_live = _platform_currently_running_live(platform)
        row["live_enabled"] = live_flag
        row["currently_running_live"] = running_live
        row["live_ready"] = bool(row.get("live_capable")) and live_flag
        enriched[platform] = row
    return enriched


def _credential_status_snapshot(platform: str) -> dict:
    if hasattr(db, "get_credential_health"):
        cached = db.get_credential_health(platform)
        if cached:
            payload = dict(cached.get("payload") or {})
            row = {
                "platform": platform,
                "state": cached.get("state", "unknown"),
                "failure_type": cached.get("failure_type", ""),
                "redacted_hint": cached.get("redacted_hint", ""),
                **payload,
            }
            row.setdefault("credentials_present", bool(row.get("configured")))
            row.setdefault("credentials_valid", row.get("state") == "valid")
            row.setdefault("validation_performed", row.get("state") in {"valid", "invalid"})
            row.setdefault("live_capable", bool(row.get("credentials_valid")))
            row.setdefault("readiness", "ready_for_live" if row.get("live_capable") else "ready_for_paper_only")
            row.setdefault("display_state", "ready_for_live" if row.get("live_capable") else ("validation_failed" if row.get("validation_performed") and not row.get("credentials_valid") else "loaded"))
            return _enrich_credential_results({platform: row}).get(platform, row)
    from services.credential_validator import validate_platform

    row = validate_platform(
        platform,
        db_module=db,
        settings_getter=_get_runtime_setting,
        perform_network_check=False,
        use_cached=True,
    )
    return _enrich_credential_results({platform: row}).get(platform, row)


def _build_live_control_payload(results: dict[str, dict] | None = None) -> dict:
    controls = _live_controls_snapshot()
    enriched = _enrich_credential_results(results or {})
    platforms: dict[str, dict] = {}
    for platform in ("stake", "polymarket"):
        credential = dict(enriched.get(platform) or {})
        effective_enabled = bool((controls["platforms"].get(platform) or {}).get("effective_enabled"))
        platforms[platform] = {
            "enabled": bool((controls["platforms"].get(platform) or {}).get("enabled")),
            "effective_enabled": effective_enabled,
            "credentials_present": bool(credential.get("credentials_present")),
            "credentials_valid": bool(credential.get("credentials_valid")),
            "live_capable": bool(credential.get("live_capable")),
            "live_enabled": effective_enabled,
            "currently_running_live": bool(credential.get("currently_running_live")),
            "display_state": credential.get("display_state", "missing"),
            "readiness": credential.get("readiness", "ready_for_paper_only"),
            "reason": credential.get("reason", ""),
        }
    return {
        "global": controls["global"],
        "platforms": platforms,
        "runtime_running": bool(_running),
        "explicit_runtime_start_required": True,
        "auto_start_live_bots": bool(getattr(_cfg, "AUTO_START_LIVE_BOTS", False)),
    }


def _build_credentials_status(*, refresh: bool = False, use_cached: bool = True):
    from services.credential_validator import credential_summary, validate_all

    results = validate_all(
        db_module=db,
        settings_getter=_get_runtime_setting,
        perform_network_check=refresh,
        use_cached=use_cached,
    )
    enriched = _enrich_credential_results(results)
    summary = credential_summary(enriched, db_module=db)
    summary["platforms"] = list(enriched.values())
    summary["live_control"] = _build_live_control_payload(enriched)
    try:
        db.set_runtime_truth("credentials", summary)
    except Exception:
        pass
    return summary


def _build_reconciliation_status():
    from services.reconciliation_service import persist_reconciliation, reconcile_all

    report = reconcile_all(db_module=db)
    persist_reconciliation(db, report)
    return report


def _build_storage_integrity():
    result = db.run_integrity_check() if hasattr(db, "run_integrity_check") else {"ok": False, "result": "unsupported"}
    try:
        db.set_runtime_truth("storage_integrity", result)
    except Exception:
        pass
    return result


def _build_startup_checks(*, persist: bool = False):
    storage = _build_storage_status()
    checks: list[dict] = []

    def _add(level: str, code: str, message: str):
        checks.append({"level": level, "code": code, "message": message})

    if _using_default_password():
        _add("critical", "default_password", "Operator password is still using the default value.")
    if _is_legacy_sha256_hash(_STORED_HASH or _load_stored_hash()):
        _add("critical", "legacy_sha256_password_hash", "Stored password hash is legacy raw SHA-256 and must be upgraded before normal auth use.")
    if not storage.get("write_test_ok"):
        _add("critical", "storage_not_writable", "Database directory failed the write probe.")
    if storage_mode_from_db_path(getattr(db, "DB_PATH", "bots.db"))[0] == "ephemeral":
        _add("warning", "ephemeral_storage", "DB_PATH points to ephemeral storage; history and backups may not survive redeploys.")
    if getattr(_cfg, "ENABLE_REMOTE_ACCESS", False) and getattr(_cfg, "REMOTE_ACCESS_DEFAULTS", {}).get("allow_cors_all", True):
        _add("warning", "cors_wildcard_remote", "Remote access is enabled while CORS still allows all origins.")
    if getattr(_cfg, "AUTH_TOKEN_TTL_HOURS", 24) > 168:
        _add("warning", "long_token_ttl", "Auth token TTL is longer than 7 days.")
    if int(getattr(_cfg, "AUTH_TOKEN_IDLE_MINUTES", 240)) <= 0:
        _add("warning", "token_idle_timeout_disabled", "Auth token inactivity timeout is disabled.")
    if int(getattr(_cfg, "AUTH_TOKEN_IDLE_MINUTES", 240)) > 720:
        _add("warning", "long_token_idle_timeout", "Auth token inactivity timeout is longer than 12 hours.")
    if str(_get_runtime_setting("require_approved_device", "false")).strip().lower() not in {"1", "true", "yes", "on"}:
        _add("warning", "approved_device_guard_disabled", "Approved-device-only login is disabled.")
    if not getattr(_cfg, "STRICT_SECURITY_HEADERS", True):
        _add("warning", "security_headers_disabled", "Strict security headers are disabled.")
    if not getattr(_cfg, "ENABLE_DB_BACKUPS", True):
        _add("warning", "db_backups_disabled", "Database backups are disabled.")
    if os.getenv("FLY_APP_NAME"):
        if not os.getenv("ENCRYPTION_KEY", "").strip():
            _add("critical", "missing_fly_encryption_key", "ENCRYPTION_KEY must be provided via Fly secrets in production.")
        if not os.getenv("AUTH_PASSWORD", "").strip():
            _add("critical", "missing_fly_auth_password", "AUTH_PASSWORD must be provided via Fly secrets in production.")

    payload = {
        "ready": not any(check["level"] == "critical" for check in checks),
        "critical_count": sum(1 for check in checks if check["level"] == "critical"),
        "warning_count": sum(1 for check in checks if check["level"] == "warning"),
        "checks": checks,
    }
    try:
        db.set_runtime_truth("startup_checks", payload)
    except Exception:
        pass
    if persist and checks:
        try:
            db.save_security_findings_snapshot("startup_checks", checks)
        except Exception:
            pass
    return payload


def _build_system_health():
    startup = _build_startup_checks()
    storage = _build_storage_status()
    integrity = _build_storage_integrity()
    executor = _build_executor_status()
    credentials = _build_credentials_status()
    runtime_health = _runtime_health.all()
    heartbeat_values = [
        float(state.get("last_heartbeat_ts", 0) or 0)
        for state in runtime_health.values()
        if state.get("last_heartbeat_ts")
    ]
    last_bot_heartbeat_ts = max(heartbeat_values) if heartbeat_values else None
    encryption_status = _build_encryption_status()
    notifications = _build_notification_status()
    adapters = _build_adapter_health_snapshot(include_details=False)
    health = {
        "ready": bool(startup.get("ready")) and bool(integrity.get("ok")) and bool(storage.get("write_test_ok")),
        "startup": startup,
        "storage": storage,
        "integrity": integrity,
        "db_connectivity": {
            "ok": bool(storage.get("write_test_ok")) and bool(integrity.get("ok")),
            "db_path": storage.get("db_path_basename"),
        },
        "last_bot_heartbeat_ts": last_bot_heartbeat_ts,
        "executor": {
            "owner_present": executor.get("owner_present", False),
            "owned_by_self": executor.get("owned_by_self", False),
            "is_stale": executor.get("is_stale", False),
        },
        "credentials": {
            "valid": credentials.get("valid", 0),
            "invalid": credentials.get("invalid", 0),
            "missing": credentials.get("missing", 0),
            "unchecked": credentials.get("unchecked", 0),
        },
        "platform_adapters": {
            "total": len(adapters.get("platforms", [])),
            "ok": adapters.get("ok_count", 0),
            "degraded": adapters.get("degraded_count", 0),
        },
        "notification_service": notifications,
        "encryption": encryption_status,
    }
    try:
        db.set_runtime_truth("system_health", health)
    except Exception:
        pass
    return health


def _build_security_status():
    startup = _build_startup_checks()
    payload = {
        "single_operator_mode": bool(getattr(_cfg, "SINGLE_OPERATOR_MODE", True)),
        "private_ui_mode": bool(getattr(_cfg, "PRIVATE_UI_MODE", False)),
        "remote_access_enabled": bool(getattr(_cfg, "ENABLE_REMOTE_ACCESS", False)),
        "localtunnel_enabled": bool(getattr(_cfg, "ENABLE_LOCALTUNNEL", False)),
        "redact_secrets": bool(getattr(_cfg, "REDACT_SECRETS", True)),
        "strict_security_headers": bool(getattr(_cfg, "STRICT_SECURITY_HEADERS", True)),
        "approved_device_guard": str(_get_runtime_setting("require_approved_device", "false")).strip().lower() in {"1", "true", "yes", "on"},
        "auth_token_ttl_hours": max(1, int(getattr(_cfg, "AUTH_TOKEN_TTL_HOURS", 24))),
        "auth_token_idle_minutes": max(0, int(getattr(_cfg, "AUTH_TOKEN_IDLE_MINUTES", 240))),
        "startup": startup,
        "recent_security_findings": db.get_security_findings(limit=100),
        "recent_failure_events": db.get_failure_events(limit=50),
    }
    try:
        db.set_runtime_truth("security", payload)
    except Exception:
        pass
    return payload


def _build_encryption_status() -> dict:
    from utils.crypto import ensure_encryption_key

    try:
        key = ensure_encryption_key()
    except RuntimeError as exc:
        return {
            "enabled": False,
            "fernet": False,
            "key_present": False,
            "source": "missing",
            "reason": str(exc),
        }
    return {
        "enabled": True,
        "fernet": True,
        "key_present": bool(key),
        "source": "env" if os.getenv("ENCRYPTION_KEY", "").strip() else "local_env_file",
    }


def _build_notification_status() -> dict:
    settings = db.get_all_settings()
    telegram_targets = bool(_get_runtime_setting("telegram_chat_id")) or bool(_get_runtime_setting("telegram_operator_chat_ids"))
    telegram_configured = bool(_get_runtime_setting("telegram_bot_token")) and telegram_targets
    twilio_configured = bool(_get_runtime_setting("twilio_account_sid")) and bool(_get_runtime_setting("twilio_auth_token"))
    pending_human_relay = 0
    if hasattr(db, "list_human_relay_requests"):
        try:
            pending_human_relay = len(db.list_human_relay_requests(status="pending", limit=100))
        except Exception:
            pending_human_relay = 0
    return {
        "ok": telegram_configured or twilio_configured or not pending_human_relay,
        "telegram_configured": telegram_configured,
        "twilio_configured": twilio_configured,
        "pending_human_relay": pending_human_relay,
        "daily_summary_enabled": bool(settings.get("telegram_bot_token") or settings.get("twilio_account_sid")),
    }


def _build_adapter_health_snapshot(*, include_details: bool = True) -> dict:
    _init_platform_services()
    if _market_registry is None:
        return {"platforms": [], "ok_count": 0, "degraded_count": 0}
    rows = []
    for platform in _market_registry.list_platforms():
        adapter = _market_registry.get(platform)
        started = time.perf_counter()
        try:
            snapshot = adapter.healthcheck()
        except Exception as exc:
            snapshot = {
                "platform": platform,
                "ok": False,
                "status": "error",
                "degraded_reason": str(exc),
            }
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        row = {
            "platform": platform,
            "ok": bool(snapshot.get("ok")),
            "status": snapshot.get("status", "unknown"),
            "mode": snapshot.get("mode") or getattr(adapter, "get_mode", lambda: "UNKNOWN")(),
            "configured": bool(snapshot.get("configured", adapter.is_configured())),
            "degraded_reason": snapshot.get("degraded_reason", ""),
            "response_time_ms": duration_ms,
        }
        if include_details:
            row["health"] = snapshot
        rows.append(row)
    return {
        "platforms": rows,
        "ok_count": sum(1 for row in rows if row["ok"]),
        "degraded_count": sum(1 for row in rows if not row["ok"]),
    }


_HEALTH_DEEP_CACHE: dict = {}
_HEALTH_DEEP_CACHE_TS: float = 0.0
_HEALTH_DEEP_CACHE_TTL: float = 8.0  # seconds


def _build_system_health_deep() -> dict:
    global _HEALTH_DEEP_CACHE, _HEALTH_DEEP_CACHE_TS
    now = time.time()
    if _HEALTH_DEEP_CACHE and (now - _HEALTH_DEEP_CACHE_TS) < _HEALTH_DEEP_CACHE_TTL:
        return dict(_HEALTH_DEEP_CACHE)
    health = _build_system_health()
    schema = db.get_schema_version() if hasattr(db, "get_schema_version") else {"version": 0}
    migrations = db.list_schema_migrations() if hasattr(db, "list_schema_migrations") else []
    pending = max(0, len(migrations) - int(schema.get("version", 0) or 0))
    runtime_health = _runtime_health.all()
    bot_rows = []
    for bot_id, state in runtime_health.items():
        bot_rows.append({
            "bot_id": bot_id,
            **state,
        })
    payload = {
        **health,
        "bot_health": bot_rows,
        "adapter_health": _build_adapter_health_snapshot(include_details=True),
        "schema_version": schema,
        "pending_migrations": pending,
        "integrity": _build_storage_integrity(),
    }
    try:
        db.set_runtime_truth("system_health_deep", payload)
    except Exception:
        pass
    _HEALTH_DEEP_CACHE.clear()
    _HEALTH_DEEP_CACHE.update(payload)
    _HEALTH_DEEP_CACHE_TS = time.time()
    return payload


def _uptime_str() -> str:
    elapsed_wall = (time.time() - _start_wall) if _start_wall else 0.0
    h, rem = divmod(int(max(0.0, elapsed_wall)), 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def public_health_payload() -> dict:
    payload = _build_system_health()
    service_ok = bool((payload.get("db_connectivity") or {}).get("ok"))
    return {
        "ok": service_ok,
        "ready": bool(payload.get("ready", False)),
        "server": {
            "status": "ok" if payload.get("ready") else ("degraded" if service_ok else "down"),
            "uptime": _uptime_str(),
        },
        "db": payload.get("db_connectivity", {}),
        "last_bot_heartbeat_ts": payload.get("last_bot_heartbeat_ts"),
        "platform_adapters": payload.get("platform_adapters", {}),
        "notification_service": payload.get("notification_service", {}),
        "encryption": payload.get("encryption", {}),
    }


def public_health_deep_payload() -> dict:
    payload = _build_system_health_deep()
    service_ok = bool((payload.get("db_connectivity") or {}).get("ok"))
    return {
        "ok": service_ok,
        "ready": bool(payload.get("ready", False)),
        "server": {
            "status": "ok" if payload.get("ready") else ("degraded" if service_ok else "down"),
            "uptime": _uptime_str(),
        },
        "db": {
            **payload.get("db_connectivity", {}),
            "integrity": payload.get("integrity", {}),
            "schema_version": payload.get("schema_version", {}),
            "pending_migrations": payload.get("pending_migrations", 0),
        },
        "bot_health": payload.get("bot_health", []),
        "adapter_health": payload.get("adapter_health", {}),
        "notification_service": payload.get("notification_service", {}),
        "encryption": payload.get("encryption", {}),
    }


def _stake_execution_mode() -> str:
    if _stake_live_running():
        return "live"
    stake_row = _credential_status_snapshot("stake")
    if stake_row.get("live_capable"):
        return "live_capable"
    if stake_row.get("validation_performed") and not stake_row.get("credentials_valid"):
        return "degraded"
    return "paper"


def _bot_truth_labels(bot_id: str, spec: dict, runtime: dict) -> dict:
    credential_rows = {
        "stake": _credential_status_snapshot("stake"),
        "polymarket": _credential_status_snapshot("polymarket"),
    }
    if spec["platform"] == "stake":
        execution_mode = _stake_execution_mode()
        data_truth_label = "LIVE" if execution_mode == "live" else ("LIVE-CAPABLE" if execution_mode == "live_capable" else ("DEGRADED" if execution_mode == "degraded" else "PAPER"))
        auth_ok = execution_mode in {"live", "live_capable"}
    else:
        if str(runtime.get("execution_mode", "")).lower() == "live":
            execution_mode = "live"
            data_truth_label = "LIVE"
        else:
            poly_row = credential_rows.get("polymarket", {})
            if poly_row.get("live_capable"):
                execution_mode = "live_capable"
                data_truth_label = "LIVE-CAPABLE"
            elif poly_row.get("validation_performed") and not poly_row.get("credentials_valid"):
                execution_mode = "degraded"
                data_truth_label = "DEGRADED"
            else:
                execution_mode = "paper"
                data_truth_label = "PAPER"
        auth_ok = execution_mode in {"live", "live_capable", "paper"}
    return {
        "execution_mode": execution_mode,
        "reconciliation_state": "partial" if execution_mode == "live" else "off",
        "wallet_truth": "virtual_ledger",
        "data_truth_label": data_truth_label,
        "auth_ok": auth_ok,
    }


def _compute_window_stats(nets, size: int):
    sample = list(nets)[-size:]
    count = len(sample)
    wins = sum(1 for n in sample if n > 0)
    pnl = sum(sample)
    avg_abs = sum(abs(n) for n in sample) / max(count, 1)
    denom = avg_abs * count
    roi_pct = (pnl / denom * 100) if denom > 0 else 0.0
    return {
        "count": count,
        "wins": wins,
        "losses": count - wins,
        "pnl": round(pnl, 4),
        "win_rate": round((wins / max(count, 1)) * 100, 1),
        "roi_pct": round(roi_pct, 2),
    }


def _rolling_windows(bot_id: str):
    nets = _recent_trade_nets.get(bot_id, collections.deque(maxlen=100))
    return {
        "w25": _compute_window_stats(nets, 25),
        "w100": _compute_window_stats(nets, 100),
    }


def _seed_recent_trade_state():
    _recent_trade_nets.clear()
    _auto_manage_state.clear()
    for spec in _active_bot_specs():
        bid = spec["id"]
        dq = collections.deque(maxlen=100)
        rows, _ = db.get_trades(bot_id=bid, limit=100, offset=0)
        for row in reversed(rows):
            dq.append(float(row.get("net", 0) or 0))
        _recent_trade_nets[bid] = dq
        _auto_manage_state[bid] = {
            "last_downscale_count": 0,
            "downscales": 0,
            "last_reason": "",
        }
        _runtime_health.ensure(bid, enabled=True)


def _maybe_auto_downscale(bot_id: str):
    rm = _rms.get(bot_id)
    if not rm:
        return
    windows = _rolling_windows(bot_id)
    recent = windows["w25"]
    state = _auto_manage_state.setdefault(bot_id, {
        "last_downscale_count": 0,
        "downscales": 0,
        "last_reason": "",
    })
    if recent["count"] < 25:
        return
    if recent["count"] - state["last_downscale_count"] < 25:
        return

    scale = _bet_scales.get(bot_id, 1.0)
    trigger_reason = None
    if recent["roi_pct"] <= -12:
        trigger_reason = f"rolling 25 ROI {recent['roi_pct']}%"
    elif recent["win_rate"] <= 42:
        trigger_reason = f"rolling 25 win rate {recent['win_rate']}%"
    elif windows["w100"]["count"] >= 60 and windows["w100"]["roi_pct"] <= -8:
        trigger_reason = f"rolling 100 ROI {windows['w100']['roi_pct']}%"

    if not trigger_reason or scale <= 0.35:
        return

    new_scale = max(0.35, round(scale * 0.85, 2))
    if new_scale >= scale:
        return

    _apply_scale(bot_id, new_scale)
    state["last_downscale_count"] = recent["count"]
    state["downscales"] += 1
    state["last_reason"] = trigger_reason
    msg = f"{bot_id} auto-downscaled {scale:.2f}x -> {new_scale:.2f}x ({trigger_reason})"
    logger.info(msg)
    db.save_event("warn", msg, bot_id)
    _broadcast_sync({
        "type": "auto_downscale",
        "bot_id": bot_id,
        "from_scale": round(scale, 2),
        "to_scale": round(new_scale, 2),
        "reason": trigger_reason,
    })


def _capture_poly_runtime(bot_id: str, strategy_type: str, bot=None, error: str = ""):
    base = {
        "strategy": strategy_type,
        "execution_mode": "paper",
        "data_mode": "real_market_data",
        "open_positions": 0,
        "opportunity_count": 0,
        "best_edge": None,
        "best_question": "",
        "last_scan_ts": 0.0,
        "opportunities": [],
        "last_cycle_ts": time.time(),
        "last_error": error or "",
    }
    if bot and hasattr(bot, "snapshot"):
        try:
            base.update(bot.snapshot() or {})
        except Exception as exc:
            base["last_error"] = f"snapshot failed: {exc}"
    elif error:
        base["last_error"] = error
    _poly_runtime[bot_id] = base
# Per-bot user configuration: start_amount, target_amount, floor_amount
_bot_configs: dict[str, dict] = {}

BOT_SPECS = [
    # (bot_id, game/kind, platform_label, strategy_type, description)
    ("bot1_dice",      "dice",  "Stake Dice",   "dice",             "Paroli press on wins"),
    ("bot2_limbo",     "limbo", "Stake Limbo",  "limbo",            "Paroli + 10x big-shot"),
    ("bot3_mines",     "mines", "Stake Mines",  "mines",            "Progressive mine picks"),
    ("bot4_poly",      "poly",  "Polymarket",   "edge_scanner",     "Multi-factor Kelly edge"),
    ("bot5_poly",      "poly",  "Polymarket",   "edge_scanner",     "Execution-truthful EV"),
    ("bot6_poly",      "poly",  "Polymarket",   "edge_scanner",     "Ask-aware order flow"),
    ("bot7_momentum",  "poly",  "Polymarket",   "btc_momentum",     "Binance BTC price lag arb"),
    ("bot8_arb",       "poly",  "Polymarket",   "intra_arb",        "Sum-to-one arb + mid dev"),
    ("bot9_sniper",    "poly",  "Polymarket",   "resolution_sniper","Near-expiry convergence"),
    ("bot10_volume",   "poly",  "Polymarket",   "volume_spike",     "Volume surge momentum"),
]
BOT_COLORS = {
    "bot1_dice"    : "#5eb8ff",
    "bot2_limbo"   : "#b87dff",
    "bot3_mines"   : "#4ade80",
    "bot4_poly"    : "#3b82f6",
    "bot5_poly"    : "#f87171",
    "bot6_poly"    : "#fbbf24",
    "bot7_momentum": "#14b8a6",
    "bot8_arb"     : "#a78bfa",
    "bot9_sniper"  : "#fb7185",
    "bot10_volume" : "#f97316",
}
BOT_INDEX = {spec[0]: i + 1 for i, spec in enumerate(BOT_SPECS)}
BOT_SPEC_MAP = {
    bid: {
        "id"          : bid,
        "kind"        : kind,
        "platform"    : "stake" if kind in ("dice", "limbo", "mines") else "poly",
        "strategy"    : strategy_type,
        "description" : desc,
        "display_name": bid,
        "color"       : BOT_COLORS.get(bid, "#94a3b8"),
        "equity_key"  : f"bot{BOT_INDEX[bid]}",
    }
    for bid, kind, _plat, strategy_type, desc in BOT_SPECS
}


def _load_bot_registry():
    try:
        raw = db.get_setting("bot_registry", "[]")
        data = json.loads(raw) if raw else []
    except Exception:
        data = []

    merged = []
    existing = {item.get("id"): item for item in data if isinstance(item, dict) and item.get("id") in BOT_SPEC_MAP}
    for bid, spec in BOT_SPEC_MAP.items():
        row = existing.get(bid, {})
        merged.append({
            **spec,
            "enabled": bool(row.get("enabled", True)),
            "display_name": str(row.get("display_name") or spec["display_name"]),
        })
    return merged


def _save_bot_registry(registry):
    trimmed = [
        {"id": item["id"], "enabled": bool(item.get("enabled", True)), "display_name": item.get("display_name") or item["id"]}
        for item in registry
        if item.get("id") in BOT_SPEC_MAP
    ]
    db.set_setting("bot_registry", json.dumps(trimmed))


def _active_bot_specs():
    return [item for item in _load_bot_registry() if item["enabled"]]

# ── Log capture ───────────────────────────────────────────────────────────────
class _Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self._degens_capture = True

    def emit(self, record):
        msg   = self.format(record)
        clean = msg.split("|",2)[-1].strip() if "|" in msg else msg
        lm    = clean.lower()
        t     = ("halt" if "hard stop" in lm or "halted" in lm
                 else "fill" if any(x in lm for x in ["locked","profit","filled","withdraw","10x","target"])
                 else "warn" if "recovery" in lm or "warn" in lm
                 else "info")
        _log_entries.appendleft({"time": datetime.now().strftime("%H:%M:%S"), "msg": clean[:120], "type": t})
        try:
            db.save_event(t, clean[:200])
        except Exception:
            # Log capture must never block server import/startup.
            pass

_root_logger = logging.getLogger()
for _handler in list(_root_logger.handlers):
    if getattr(_handler, "_degens_capture", False):
        _root_logger.removeHandler(_handler)
_root_logger.addHandler(_Capture())

# ── Bot loops ─────────────────────────────────────────────────────────────────

def _handle_result(bot_id: str, game: str, net: float, result: dict):
    """Common post-bet handling: DB writes, WS broadcast, notifications."""
    rm = _rms[bot_id]
    db.save_trade(bot_id, game, rm.phase, abs(net) if net else 0,
                  net > 0, net, rm.current_bankroll)
    _recent_trade_nets.setdefault(bot_id, collections.deque(maxlen=100)).append(float(net or 0))
    _maybe_auto_downscale(bot_id)
    if result.get("withdraw", 0) > 0:
        db.save_wallet_tx("profit_lock", result["withdraw"],
                          platform=game, note=f"{bot_id} profit lock")
        db.save_vault_lock(bot_id, result["withdraw"], "ratchet", rm.current_bankroll)
    if result.get("circuit_breaker"):
        db.save_circuit_breaker(
            bot_id, result.get("cb_reason", "unknown"),
            result.get("cb_duration_s", 0), rm.current_bankroll, rm.phase
        )
        try:
            from services.notification_center import notify_circuit_breaker_event

            notify_circuit_breaker_event(
                bot_id,
                reason=result.get("cb_reason", "unknown"),
                cooldown_s=float(result.get("cb_duration_s", 0) or 0),
                db_module=db,
            )
        except Exception as exc:
            logger.warning("Circuit breaker notification failed: %s", exc)
    if result.get("phase_changed"):
        db.save_phase_transition(
            bot_id, result.get("prev_phase", "?"), rm.phase,
            rm.current_bankroll, result.get("phase_reason")
        )
        if rm.phase == "floor":
            try:
                from services.notification_center import notify_floor_warning

                notify_floor_warning(
                    bot_id,
                    bankroll=float(rm.current_bankroll or 0),
                    floor=float(getattr(rm, "floor", 0) or 0),
                    reason=str(result.get("phase_reason", "") or ""),
                    db_module=db,
                )
            except Exception as exc:
                logger.warning("Floor warning notification failed: %s", exc)
    if result["action"] == "MILESTONE":
        try:
            from services.notification_center import notify_progress_milestone

            notify_progress_milestone(
                bot_id,
                progress=float(rm.progress or 0),
                bankroll=float(rm.current_bankroll or 0),
                db_module=db,
            )
        except Exception as exc:
            logger.warning("Milestone notification failed: %s", exc)
    if result["action"] == "GOAL_HIT":
        notifier.notify_target(bot_id, rm.progress)
        try:
            from services.notification_center import notify_target_reached

            notify_target_reached(
                bot_id,
                progress=float(rm.progress or 0),
                bankroll=float(rm.current_bankroll or 0),
                db_module=db,
            )
        except Exception as exc:
            logger.warning("Target notification failed: %s", exc)
    # ── Canary breach check ───────────────────────────────────────────────────
    if _canary_sessions.get(bot_id, {}).get("active"):
        breached, breach_type = _canary_check_breach(bot_id)
        session = _canary_sessions[bot_id]
        current_dd = max(0.0, (rm.peak_bankroll - rm.current_bankroll) / max(1e-9, rm.peak_bankroll) * 100)
        session["observed_drawdown_pct"] = round(current_dd, 2)
        if breached:
            session["active"]      = False
            session["stop_ts"]     = datetime.now(UTC).isoformat().replace("+00:00", "Z")
            session["stop_reason"] = breach_type
            _canary_record_event(bot_id, "breach", breach_type)
            session["red_line_events"].append({"ts": session["stop_ts"], "type": breach_type})
            _bot_pause_flags[bot_id] = True
            rm.is_halted = True
            _runtime_health.update(bot_id, execution_ready=False, degraded_reason=f"Canary breach: {breach_type}")
            _broadcast_sync({"type": "canary_breach", "bot_id": bot_id, "breach_type": breach_type})
            try:
                from services.notification_center import notify_canary_breach
                notify_canary_breach(bot_id, breach_type=breach_type, db_module=db)
            except Exception as _e:
                logger.warning(f"[canary] Breach notification failed: {_e}")
            logger.warning(f"[canary] Breach for {bot_id}: {breach_type} — bot paused")
    _broadcast_sync({
        "type"    : "bet",
        "bot_id"  : bot_id,
        "net"     : round(net, 4),
        "bankroll": round(rm.current_bankroll, 4),
        "phase"   : rm.phase,
        "action"  : result["action"],
    })


def _bot_is_paused(bot_id: str) -> bool:
    return bool(_bot_pause_flags.get(bot_id))


def _stake_loop(bot_id: str, game: str):
    rm       = _rms[bot_id]
    strategy = make_strategy(game, rm)
    _runtime_health.ensure(bot_id, enabled=True)
    logger.info(f"[{bot_id}] started game={game}")
    while not _stop_event.is_set():
        if _bot_is_paused(bot_id):
            _runtime_health.heartbeat(
                bot_id,
                market_data_ok=True,
                auth_ok=_stake_execution_mode() == "live",
                execution_ready=False,
                reconciliation_ready=False,
                degraded_reason="Paused by operator.",
                last_error="",
            )
            time.sleep(0.5)
            continue
        # Only run when live Stake credentials are active — never simulate
        if _sc.dice_roll is paper_stake.dice_roll:
            _runtime_health.heartbeat(
                bot_id,
                market_data_ok=False,
                auth_ok=False,
                execution_ready=False,
                reconciliation_ready=False,
                degraded_reason="Stake live execution is disabled because no live token is loaded.",
            )
            time.sleep(2.0)
            continue
        if rm.is_cooling_down:
            _runtime_health.heartbeat(
                bot_id,
                market_data_ok=True,
                auth_ok=True,
                execution_ready=False,
                reconciliation_ready=True,
                degraded_reason=f"Circuit breaker active: {rm.cooldown_remaining:.1f}s remaining.",
            )
            time.sleep(0.5)
            continue
        try:
            _runtime_health.heartbeat(
                bot_id,
                market_data_ok=True,
                auth_ok=True,
                execution_ready=True,
                reconciliation_ready=True,
                degraded_reason="",
                last_error="",
            )
            net    = strategy.run_one_bet()
            if _bot_is_paused(bot_id) or rm.is_halted:
                time.sleep(0.1)
                continue
            result = rm.record_bet_result(net)
            _handle_result(bot_id, game, net, result)
        except Exception as e:
            logger.error(f"[{bot_id}] {e}")
            _runtime_health.update(
                bot_id,
                market_data_ok=False,
                execution_ready=False,
                degraded_reason=str(e),
                last_error=str(e),
            )
            time.sleep(0.5)


def _make_clob_client():
    """Create a live Polymarket CLOB client only when the operator explicitly arms live execution."""
    import config as _c
    if not (_live_runtime_requested and _platform_live_enabled("polymarket")):
        return None
    if not (_c.POLY_PRIVATE_KEY and "••" not in _c.POLY_PRIVATE_KEY):
        return None
    try:
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import ApiCreds
        creds = ApiCreds(
            api_key        = _c.POLY_API_KEY,
            api_secret     = _c.POLY_API_SECRET,
            api_passphrase = _c.POLY_API_PASSPHRASE,
        )
        client = ClobClient(
            host     = _c.POLY_HOST,
            chain_id = _c.POLY_CHAIN_ID,
            key      = _c.POLY_PRIVATE_KEY,
            creds    = creds,
        )
        logger.info("Polymarket CLOB client initialised — live execution armed by operator.")
        return client
    except Exception as e:
        logger.error(f"CLOB client init failed: {e}")
        return None


def _poly_loop(bot_id: str, strategy_type: str = "edge_scanner"):
    rm          = _rms[bot_id]
    live_client = _make_clob_client()
    bot_class   = _POLY_STRATEGY_MAP.get(strategy_type, PaperPolymarketBot)
    bot         = bot_class(rm, live_client=live_client)
    _runtime_health.ensure(bot_id, enabled=True)
    scan_delay = max(_cfg.BET_DELAY_SECONDS * 12, 0.4)
    _capture_poly_runtime(bot_id, strategy_type, bot)
    logger.info(f"[{bot_id}] polymarket started strategy={strategy_type}")
    while not _stop_event.is_set():
        if _bot_is_paused(bot_id):
            _runtime_health.heartbeat(
                bot_id,
                market_data_ok=True,
                auth_ok=True,
                execution_ready=False,
                reconciliation_ready=bool(live_client),
                degraded_reason="Paused by operator.",
                last_error="",
            )
            time.sleep(0.5)
            continue
        if rm.is_cooling_down:
            _runtime_health.heartbeat(
                bot_id,
                market_data_ok=True,
                auth_ok=True,
                execution_ready=False,
                reconciliation_ready=bool(live_client),
                degraded_reason=f"Circuit breaker active: {rm.cooldown_remaining:.1f}s remaining.",
            )
            time.sleep(0.5)
            continue
        try:
            net = bot.run_one_cycle()
            _capture_poly_runtime(bot_id, strategy_type, bot)
            runtime = _poly_runtime.get(bot_id, {})
            _runtime_health.heartbeat(
                bot_id,
                market_data_ok=not bool(runtime.get("last_error")),
                auth_ok=True,
                execution_ready=bool(live_client) or runtime.get("execution_mode") == "paper",
                reconciliation_ready=bool(live_client),
                degraded_reason=runtime.get("last_error", ""),
                last_error=runtime.get("last_error", ""),
            )
            # Record all fills (paper mode tracks PnL even without live credentials)
            if net != 0.0:
                result = rm.record_bet_result(net)
                _handle_result(bot_id, strategy_type, net, result)
        except Exception as e:
            logger.error(f"[{bot_id}] {e}")
            _capture_poly_runtime(bot_id, strategy_type, bot, str(e))
            _runtime_health.update(
                bot_id,
                market_data_ok=False,
                execution_ready=bool(live_client),
                degraded_reason=str(e),
                last_error=str(e),
            )
        for _ in range(int(scan_delay / 0.1)):
            if _stop_event.is_set(): break
            time.sleep(0.1)


def _snapshot_loop():
    global _tick
    while not _stop_event.is_set():
        time.sleep(max(1.0 / 10.0, 0.2))
        now = datetime.now(UTC)
        with _lock:
            progress  = {bid: rm.progress for bid, rm in _rms.items()}
            portfolio = sum(progress.values())
            label     = now.strftime("%H:%M")
            snap      = {bid: round(v, 2) for bid, v in progress.items()}
            snap["portfolio"] = round(portfolio, 2)
            snap["label"]     = label
            snap["ts"]        = now.isoformat().replace("+00:00", "Z")
            if len(_equity_history) >= 120:
                _equity_history.pop(0)
            _equity_history.append(snap)
            _tick += 1
        # Persist snapshot every 5 ticks
        if _tick % 5 == 0:
            db.save_snapshot(label, progress, portfolio)
            _persist_bot_runtime_snapshots(include_history=(_tick % 20 == 0))
        # Check $100 milestone every 10 ticks
        if _tick % 10 == 0:
            notifier.check_milestone(portfolio, _initial_total)
        # Broadcast equity update over WS every tick
        _broadcast_sync({"type": "equity", "snap": snap})


# ── Start / stop ──────────────────────────────────────────────────────────────

def _apply_scale(bid: str, scale: float):
    _bet_scales[bid] = scale
    if _rms.get(bid):
        _rms[bid].bet_scale = scale

def _ensure_bot_runtime_state(*, reset: bool = False):
    global _initial_total
    if reset:
        _rms.clear()
        _poly_runtime.clear()
    if _rms:
        return
    active_specs = _active_bot_specs()
    _seed_recent_trade_state()
    for spec in active_specs:
        bid = spec["id"]
        cfg = _bot_configs.get(bid, {})
        start = float(cfg.get("start_amount", BOT_INITIAL_BANK))
        target = float(cfg.get("target_amount", start * 5.0))
        floor = float(cfg.get("floor_amount", start * 0.40))
        _rms[bid] = RiskManager(bid, start, target, floor)
        scale = _STRATEGY_SCALES.get(_strategy_modes.get(bid, "balanced"), 1.0)
        _rms[bid].bet_scale = scale
        _bet_scales[bid] = scale
        if bid not in _strategy_modes:
            _strategy_modes[bid] = "balanced"
    _initial_total = sum(rm.initial_bankroll for rm in _rms.values())


def _validate_live_enablement(platform: str) -> dict:
    from services.credential_validator import validate_platform

    result = validate_platform(
        platform,
        db_module=db,
        settings_getter=_get_runtime_setting,
        perform_network_check=True,
        use_cached=False,
    )
    if not result.get("credentials_valid"):
        raise HTTPException(
            409,
            {
                "error": "live_validation_failed",
                "platform": platform,
                "reason": result.get("reason", "Credential validation failed."),
                "credential": result,
            },
        )
    return result


def _preflight_live_start() -> dict[str, dict]:
    target_platforms = [platform for platform in ("stake", "polymarket") if _platform_live_enabled(platform)]
    readiness = _build_go_live_readiness(target_platforms=target_platforms, refresh_credentials=False)
    if target_platforms and readiness.get("status") != "ready_for_canary":
        raise HTTPException(
            409,
            {
                "error": "go_live_blocked",
                "detail": readiness.get("message"),
                "readiness": readiness,
            },
        )
    checks: dict[str, dict] = {}
    for platform in target_platforms:
        checks[platform] = _validate_live_enablement(platform)
    return checks

def _start_bots():
    global _start_wall, _running, _initial_total
    if not _claim_execution_authority():
        owner = _refresh_executor_state()
        logger.warning(f"Execution authority unavailable. Current owner: {owner}")
        _running = False
        return False
    try:
        _preflight_live_start()
    except HTTPException:
        _release_execution_authority()
        _running = False
        raise
    _sync_stake_client_mode(live_requested=True)
    active_specs = _active_bot_specs()
    _stop_event.clear()
    _equity_history.clear()
    _ensure_bot_runtime_state(reset=True)
    _bot_pause_flags.clear()

    threads = []
    for spec in active_specs:
        bid          = spec["id"]
        kind         = spec["kind"]
        strategy_type = spec.get("strategy", "edge_scanner")
        if kind == "poly":
            fn = (lambda b=bid, s=strategy_type: _poly_loop(b, s))
        else:
            fn = (lambda b=bid, g=kind: _stake_loop(b, g))
        threads.append(threading.Thread(target=fn, name=bid, daemon=True))
    threads.append(threading.Thread(target=_snapshot_loop, name="snapshot", daemon=True))
    for t in threads: t.start()
    _start_wall = time.time()
    _running    = True
    _broadcast_sync({"type": "runtime_state", "running": True, "ts": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")})
    logger.info("Started %s active bots.", len(active_specs))
    return True

def _stop_bots():
    global _running
    _stop_event.set()
    _running = False
    _sync_stake_client_mode(live_requested=False)
    _broadcast_sync({"type": "runtime_state", "running": False, "ts": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")})
    _release_execution_authority()


# ── FastAPI app ───────────────────────────────────────────────────────────────

def _load_credentials_from_db():
    """Inject runtime credentials into live config + client modules.

    On Fly/prod, deployment secrets win over DB-backed values. Locally, saved
    settings keep working unless the DB value is blank.
    """
    import stake_client, config as _c

    stake_tok = _get_runtime_setting("STAKE_API_TOKEN", "").strip()
    if stake_tok and "••" not in stake_tok and "*" not in stake_tok:
        _c.STAKE_API_TOKEN = stake_tok
        stake_client.HEADERS["x-access-token"] = stake_tok
        logger.info("Stake credentials loaded — paper mode remains active until live is explicitly armed.")
    else:
        _c.STAKE_API_TOKEN = ""
        stake_client.HEADERS["x-access-token"] = ""
        logger.info("No Stake token — running PAPER mode.")
    _sync_stake_client_mode()

    poly_pk  = _get_runtime_setting("POLY_PRIVATE_KEY", "").strip()
    poly_ak  = _get_runtime_setting("POLY_API_KEY", "").strip()
    poly_as  = _get_runtime_setting("POLY_API_SECRET", "").strip()
    poly_app = _get_runtime_setting("POLY_API_PASSPHRASE", "").strip()
    if poly_pk and "••" not in poly_pk:
        _c.POLY_PRIVATE_KEY    = poly_pk
        _c.POLY_API_KEY        = poly_ak
        _c.POLY_API_SECRET     = poly_as
        _c.POLY_API_PASSPHRASE = poly_app
        logger.info("Polymarket credentials loaded — live execution remains idle until explicitly armed.")
    else:
        _c.POLY_PRIVATE_KEY = ""
        _c.POLY_API_KEY = ""
        _c.POLY_API_SECRET = ""
        _c.POLY_API_PASSPHRASE = ""

    twilio_sid   = _get_runtime_setting("TWILIO_ACCOUNT_SID", "").strip()
    twilio_token = _get_runtime_setting("TWILIO_AUTH_TOKEN", "").strip()
    twilio_from  = _get_runtime_setting("TWILIO_FROM_NUMBER", "").strip()
    notify_phone = _get_runtime_setting("NOTIFY_PHONE", "").strip()
    if twilio_sid and "••" not in twilio_token:
        notifier.TWILIO_SID   = twilio_sid
        notifier.TWILIO_TOKEN = twilio_token
        notifier.TWILIO_FROM  = twilio_from
        notifier.NOTIFY_PHONE = notify_phone
        logger.info("Twilio credentials loaded from DB.")
    else:
        notifier.TWILIO_SID = ""
        notifier.TWILIO_TOKEN = ""
        notifier.TWILIO_FROM = ""
        notifier.NOTIFY_PHONE = ""

    runtime_bool_flags = {
        "redact_secrets": "REDACT_SECRETS",
        "private_ui_mode": "PRIVATE_UI_MODE",
        "enable_remote_access": "ENABLE_REMOTE_ACCESS",
        "enable_localtunnel": "ENABLE_LOCALTUNNEL",
    }
    for key, attr in runtime_bool_flags.items():
        raw = _get_runtime_setting(key, "")
        if raw != "":
            setattr(_c, attr, str(raw).strip().lower() == "true")


def _reload_runtime_after_settings_save(changed_keys: set[str] | None = None) -> None:
    global _tunnel_proc, _tunnel_status, _tunnel_url
    changed_keys = {str(key).lower() for key in (changed_keys or set())}
    _load_credentials_from_db()
    if changed_keys & {
        "stake_api_token",
        "poly_private_key",
        "poly_api_key",
        "poly_api_secret",
        "poly_api_passphrase",
        "live_execution_enabled",
        "stake_live_enabled",
        "polymarket_live_enabled",
        "enable_kalshi",
        "enable_oddsapi",
        "kalshi_api_key",
        "kalshi_private_key",
        "kalshi_use_demo",
        "odds_api_key",
        "betfair_app_key",
        "betfair_username",
        "betfair_password",
        "sportsdataio_api_key",
        "enable_betfair_delayed",
        "enable_sportsdataio_trial",
    }:
        try:
            _build_credentials_status(refresh=False, use_cached=False)
        except Exception as exc:
            logger.warning("Credential status refresh failed after settings save: %s", exc)
    if changed_keys & {"enable_remote_access", "enable_localtunnel"}:
        if getattr(_cfg, "ENABLE_REMOTE_ACCESS", False) and (
            _find_bin("cloudflared") is not None or getattr(_cfg, "ENABLE_LOCALTUNNEL", False)
        ):
            if not (_tunnel_proc and _tunnel_proc.poll() is None):
                threading.Thread(target=_start_tunnel, daemon=True).start()
        else:
            _stop_tunnel_process()


def _keep_alive():
    """Ping self every 14 min so Render free tier never sleeps."""
    keepalive_enabled = os.getenv("KEEPALIVE_ENABLED")
    if keepalive_enabled is not None and keepalive_enabled.strip().lower() not in {"1", "true", "yes", "on"}:
        return
    if keepalive_enabled is None and not (os.getenv("RENDER") or os.getenv("RENDER_EXTERNAL_URL")):
        return
    import requests as _req
    while True:
        time.sleep(14 * 60)
        try:
            _req.get("http://localhost:8000/api/ping", timeout=10)
        except Exception:
            pass

def _register_scheduler_bots(sched):
    _sync_expansion_catalog()
    cadence = getattr(_cfg, "VENUE_CADENCE_ASSUMPTIONS", {})
    for entry in _catalog_cache:
        platform = entry.get("platform", "unknown")
        interval = int(cadence.get(platform, {}).get("poll_interval_s", 300))
        sched.register_bot(
            entry["bot_id"],
            lambda bot_id=entry["bot_id"]: _run_expansion_bot(bot_id),
            platform=platform,
            interval_seconds=interval,
            enabled=False,
        )

def _init_scheduler():
    """Register periodic maintenance jobs and start the scheduler."""
    from services.scheduler import get_scheduler
    sched = get_scheduler()
    sched.configure(
        db_module=db,
        owner_state_getter=_refresh_executor_state,
        quota_budget=quota_budgeter.get_budget(),
        proposal_runner=_run_bot_proposal_cycle,
    )

    def _quota_flush():
        try:
            quota_budgeter.get_budget().flush_to_db(db)
        except Exception as e:
            logger.warning(f"Quota flush failed: {e}")

    def _check_auto_withdrawal():
        try:
            if _vault is not None:
                from services.auto_withdraw import check_auto_withdrawal
                from services.notification_center import notify_mall_revenue
                result = check_auto_withdrawal(db, _vault)
                if result.get("action") in ("vaulted", "withdrawal_requested"):
                    try:
                        notify_mall_revenue(result, db_module=db)
                    except Exception:
                        pass
        except Exception as e:
            logger.warning(f"Auto-withdrawal check failed: {e}")

    def _reconcile_periodic():
        try:
            from services.portfolio_forcefield import expire_stale_reservations, maybe_auto_sweep
            from services.reconciliation_service import reconcile_all, persist_reconciliation
            report = reconcile_all(db_module=db)
            persist_reconciliation(db, report)
            if report.get("mismatch_count", 0) > 0:
                from services.notification_center import notify_reconciliation_mismatch
                notify_reconciliation_mismatch(
                    detail=f"{report['mismatch_count']} mismatch(es) detected in periodic reconciliation.",
                    db_module=db,
                )
            # Check profit ladder after reconciliation
            if _profit_ladder is not None and hasattr(db, "get_wallet_summary"):
                wallet = db.get_wallet_summary()
                working = float(wallet.get("working_capital", 0) or 0)
                if working > 0:
                    _profit_ladder.check_and_lock(working, bot_id="reconciliation")
            expire_stale_reservations(db)
            if _vault is not None:
                sweep_result = maybe_auto_sweep(db, _vault)
                if sweep_result.get("action") == "swept":
                    try:
                        from services.notification_center import notify_forcefield_sweep

                        notify_forcefield_sweep(sweep_result, db_module=db)
                    except Exception:
                        pass
            # Settle OPEN reservations where positions have been settled
            try:
                from services.portfolio_forcefield import reconcile_open_positions
                reconcile_open_positions(db)
            except Exception as _e:
                logger.warning(f"Open-position reconciliation failed: {_e}")
            # Auto-withdrawal for MALL revenue
            _check_auto_withdrawal()
        except Exception as e:
            logger.warning(f"Periodic reconciliation failed: {e}")

    def _backup_periodic():
        try:
            storage_mode, _ = storage_mode_from_db_path(db.DB_PATH)
            if not getattr(_cfg, "ENABLE_DB_BACKUPS", True):
                return
            if storage_mode != "durable":
                return
            db.create_database_backup()
            db.prune_database_backups(getattr(_cfg, "DB_BACKUP_RETENTION_COUNT", 7))
        except Exception as e:
            logger.warning(f"Periodic backup failed: {e}")

    def _daily_summary_periodic():
        try:
            from services.notification_center import send_daily_summary
            send_daily_summary(db_module=db)
        except Exception as e:
            logger.warning(f"Daily summary failed: {e}")

    def _weekly_report_periodic():
        try:
            from services.notification_center import send_weekly_report
            send_weekly_report(db_module=db)
        except Exception as e:
            logger.warning(f"Weekly report failed: {e}")

    def _human_relay_reminders():
        try:
            from services.human_relay import send_due_reminders
            send_due_reminders(db_module=db)
        except Exception as e:
            logger.warning(f"Human relay reminders failed: {e}")

    sched.add_job("quota_flush", _quota_flush, interval_s=300, jitter_s=30, replace=True)
    sched.add_job("reconcile",   _reconcile_periodic, interval_s=600, jitter_s=60, replace=True)
    sched.add_job(
        "db_backup",
        _backup_periodic,
        interval_s=max(900, int(getattr(_cfg, "DB_BACKUP_INTERVAL_S", 21600))),
        jitter_s=90,
        replace=True,
    )
    sched.add_job("daily_summary", _daily_summary_periodic, interval_s=1800, jitter_s=120, replace=True)
    sched.add_job("weekly_report", _weekly_report_periodic, interval_s=7200, jitter_s=240, replace=True)
    sched.add_job("human_relay_reminders", _human_relay_reminders, interval_s=120, jitter_s=15, replace=True)
    _register_scheduler_bots(sched)
    sched.start()
    logger.info("Scheduler started with maintenance jobs (quota, reconcile, backups, notifications, human relay); expansion enabled=%s", _cfg.ENABLE_EXPANSION_SCHEDULER)


def _maybe_startup_backup() -> dict | None:
    if not getattr(_cfg, "ENABLE_DB_BACKUPS", True):
        return None
    storage_mode, _ = storage_mode_from_db_path(db.DB_PATH)
    if storage_mode != "durable":
        logger.info("Skipping startup DB backup because storage mode is %s", storage_mode)
        return None
    try:
        backup = db.create_database_backup()
        db.prune_database_backups(getattr(_cfg, "DB_BACKUP_RETENTION_COUNT", 7))
        logger.info("Startup DB backup completed: %s", backup.get("basename"))
        return backup
    except Exception as exc:
        logger.warning("Startup DB backup failed: %s", exc)
        return None


def _graceful_shutdown_runtime(timeout_s: float = 30.0) -> None:
    logger.info("Shutdown sequence started.")
    try:
        _persist_bot_runtime_snapshots(include_history=True)
    except Exception:
        logger.exception("Failed to persist runtime snapshots during shutdown.")
    try:
        quota_budgeter.get_budget().flush_to_db(db)
    except Exception:
        logger.exception("Failed to flush quota budget during shutdown.")
    try:
        _stop_bots()
    except Exception:
        logger.exception("Failed to stop bot loops during shutdown.")
    try:
        from services.scheduler import get_scheduler

        get_scheduler().stop()
    except Exception:
        logger.exception("Failed to stop scheduler during shutdown.")
    try:
        if _ws_manager.has_clients():
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop and loop.is_running():
                loop.create_task(_ws_manager.close_all())
            else:
                asyncio.run(_ws_manager.close_all())
    except Exception:
        logger.debug("WebSocket shutdown cleanup skipped", exc_info=True)
    if _executor_claimed:
        try:
            execution_authority.release(db, _executor_owner["owner_id"])
        except Exception:
            logger.exception("Failed to release executor lock during shutdown.")
    try:
        if _tor_proc:
            _tor_proc.terminate()
    except Exception:
        logger.debug("Tor terminate skipped", exc_info=True)
    try:
        _stop_tunnel_process()
    except Exception:
        logger.debug("Tunnel stop skipped", exc_info=True)
    logger.info("Shutdown sequence completed.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _STORED_HASH
    db.init_db()
    _STORED_HASH = _load_stored_hash()
    _maybe_upgrade_legacy_password_hash()
    _sync_expansion_catalog()
    _init_platform_services()
    _load_credentials_from_db()
    _validate_credentials_on_boot()
    startup = _build_startup_checks(persist=True)
    if os.getenv("FLY_APP_NAME"):
        blocking_codes = {"missing_fly_encryption_key", "missing_fly_auth_password", "default_password", "legacy_sha256_password_hash"}
        blocking = [check for check in startup.get("checks", []) if check.get("level") == "critical" and check.get("code") in blocking_codes]
        if blocking:
            joined = "; ".join(f"{item['code']}: {item['message']}" for item in blocking)
            raise RuntimeError(f"Fly startup blocked by security prerequisites: {joined}")
    _build_storage_integrity()
    _maybe_startup_backup()
    _build_system_health()
    _load_bot_configs()
    logger.info("Bots remain stopped on startup. Use POST /api/start or the PLAY control in the UI to begin execution.")
    _persist_bot_runtime_snapshots(include_history=True)
    quota_budgeter.get_budget().load_from_db(db)
    _init_scheduler()
    threading.Thread(target=_executor_heartbeat_loop, daemon=True).start()
    threading.Thread(target=_start_tor,    daemon=True).start()
    threading.Thread(target=_start_tunnel, daemon=True).start()
    threading.Thread(target=_keep_alive,   daemon=True).start()
    yield
    _graceful_shutdown_runtime()

allow_cors_all = getattr(_cfg, "REMOTE_ACCESS_DEFAULTS", {}).get("allow_cors_all", True)

async def _bypass_tunnel(request, call_next):
    response = await call_next(request)
    response.headers["bypass-tunnel-reminder"] = "true"
    if getattr(_cfg, "STRICT_SECURITY_HEADERS", True):
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        response.headers.setdefault("Origin-Agent-Cluster", "?1")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob: https:; "
            "connect-src 'self' ws: wss: https://ipapi.co; "
            "font-src 'self' data:; "
            "base-uri 'self'; "
            "form-action 'self'; "
            "frame-ancestors 'none'; "
            "object-src 'none'",
        )
        if request.url.scheme == "https":
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
        response.headers.setdefault("Pragma", "no-cache")
    return response

# ── Auth endpoints ─────────────────────────────────────────────────────────────

async def auth(req: Request, body: dict = Body(...)):
    pwd       = body.get("password", "")
    device_id = body.get("device_id", "")
    ua        = req.headers.get("user-agent", "")
    approved_device_guard = str(_get_runtime_setting("require_approved_device", "false")).strip().lower() in {"1", "true", "yes", "on"}
    _auth_guard(req, device_id=device_id)
    if not _verify_hash(pwd, _STORED_HASH):
        _record_auth_attempt(req, ok=False, device_id=device_id)
        raise HTTPException(status_code=401, detail="Invalid password")
    if _hash_needs_upgrade(_STORED_HASH):
        _persist_password_hash(_hash(pwd), rotated=False)
    if device_id:
        device, is_new = db.register_device(device_id, ua)
        approved_devices = [row for row in db.get_devices() if row.get("approved")]
        if approved_device_guard and approved_devices and not device["approved"]:
            _record_auth_attempt(req, ok=False, device_id=device_id)
            raise HTTPException(status_code=403, detail="Device not approved. Use an existing trusted session to approve it.")
        if not approved_device_guard and not device["approved"]:
            db.approve_device(device_id)
    _record_auth_attempt(req, ok=True, device_id=device_id)
    token = secrets.token_hex(32)
    with _auth_lock:
        if getattr(_cfg, "SINGLE_OPERATOR_MODE", True):
            _VALID_TOKENS.clear()
        _VALID_TOKENS[token] = _new_token_record()
    return {"ok": True, "token": token}

async def change_password(req: Request, body: dict = Body(...)):
    _check_token(req)
    current_pwd = str(body.get("current_password", "") or "")
    new_pwd = body.get("new_password", "")
    _require_reauth_password(req, current_pwd, reason="changing the dashboard password")
    if hmac.compare_digest(current_pwd, new_pwd):
        raise HTTPException(400, "New password must be different from the current password")
    issues = _password_strength_issues(new_pwd)
    if issues:
        raise HTTPException(400, {"error": "weak_password", "issues": issues})
    new_hash = _hash(new_pwd)
    _persist_password_hash(new_hash, rotated=True)
    new_token = secrets.token_hex(32)
    with _auth_lock:
        _VALID_TOKENS.clear()
        _VALID_TOKENS[new_token] = _new_token_record()
    return {"ok": True, "token": new_token, "tokens_rotated": True}

# ── Status endpoints (all require auth) ───────────────────────────────────────

async def get_status(req: Request):
    _check_token(req)
    _ensure_bot_runtime_state()
    bots = []
    for spec in _active_bot_specs():
        bid = spec["id"]
        rm = _rms.get(bid)
        if not rm:
            continue
        paused = _bot_is_paused(bid)
        s = rm.status()
        windows = _rolling_windows(bid)
        auto_state = _auto_manage_state.get(bid, {})
        runtime = _poly_runtime.get(bid, {}) if spec["platform"] == "poly" else {}
        runtime_health = _runtime_health.get(bid)
        truth = _bot_truth_labels(bid, spec, runtime)
        status = (
            "idle"     if not _running else
            "paused"   if paused else
            "cooling"  if rm.is_cooling_down else
            "floor"    if rm.phase == "floor" else
            "pushing"  if truth["execution_mode"] == "live" and rm.phase in ("turbo", "aggressive") else
            truth["execution_mode"]
        )
        bots.append({
            "id": bid,
            "name": spec["display_name"],
            "platform": spec["platform"],
            "strategy": spec["strategy"],
            "equityKey": spec["equity_key"],
            "bankroll":     round(rm.current_bankroll, 2),
            "locked":       round(rm.total_withdrawn, 2),
            "vault":        round(getattr(rm, "vault", 0), 2),
            "progress":     round(rm.progress, 2),
            "phase":        rm.phase,
            "status":       status,
            "winRate":      s["win_rate_pct"],
            "color":        spec["color"],
            "bets":         s["bets"],
            "wins":         s["wins"],
            "losses":       s["losses"],
            "avgBet":       round(rm.current_bankroll * 0.015, 2),
            "maxDD":        round(min(0, (rm.current_bankroll - rm.peak_bankroll) / rm.peak_bankroll * 100), 1),
            "peak":         round(rm.peak_bankroll, 2),
            "streak":       getattr(rm, "streak", 0),
            "sharpe":       round((s["roi_pct"] / 100) / max(0.01, 0.25), 2),
            "roi_pct":      s["roi_pct"],
            "halted":        (not _running) or paused,
            "paused":        paused,
            "coolingDown":   rm.is_cooling_down,
            "cooldownSec":   round(s.get("cooldown_sec", 0), 1),
            "cbReason":      s.get("cb_reason", ""),
            "drawdownPct":   round(s.get("drawdown_pct", 0) * 100, 2),
            "turboEligible": s.get("turbo_eligible", False),
            "velBoost":      s.get("vel_boost", False),
            "danger":        s.get("danger", False),
            "milestoneHit":  s.get("milestoneHit", False),
            "targetHit":     rm.is_target_hit,
            "elapsed_min":  s["elapsed_min"],
            "bet_scale":    round(_bet_scales.get(bid, 1.0), 2),
            "strategy_mode":_strategy_modes.get(bid, "balanced"),
            # Goal fields
            "start_amount":   round(s.get("start_amount",  100), 2),
            "target_amount":  round(s.get("target_amount", 500), 2),
            "floor_amount":   round(s.get("floor_amount",   40), 2),
            "withdraw_at":    round(s.get("withdraw_at",   115), 2),
            "caution_at":     round(s.get("caution_at",    95),  2),
            "recovery_at":    round(s.get("recovery_at",   90),  2),
            "progress_pct":   round(min(rm.progress / max(s.get("target_amount", 500), 1) * 100, 100), 1),
            "rolling": windows,
            "autoDownscales": auto_state.get("downscales", 0),
            "lastDownscaleReason": auto_state.get("last_reason", ""),
            "autoManaged": auto_state.get("downscales", 0) > 0,
            "execution_mode": truth["execution_mode"],
            "data_mode": runtime.get("data_mode", "real_market_data" if spec["platform"] == "poly" else "simulated"),
            "scan_opportunity_count": runtime.get("opportunity_count", 0),
            "scan_best_edge": runtime.get("best_edge"),
            "scan_best_question": runtime.get("best_question", ""),
            "scan_last_ts": runtime.get("last_scan_ts", 0.0),
            "open_positions": runtime.get("open_positions", 0),
            "runtime_error": runtime.get("last_error", ""),
            "opportunities": runtime.get("opportunities", []),
            "btc_price": runtime.get("btc_price"),
            # Extended fields
            "total_withdrawn": round(rm.total_withdrawn, 4),
            "drawdown_pct":    round(s.get("drawdown_pct", 0), 4),
            "win_rate_pct":    s["win_rate_pct"],
            "initial_bankroll": round(rm.initial_bankroll, 2),
            "progress_x":      round(rm.progress_multiplier, 3),
            "cooling_down":    rm.is_cooling_down,
            "cb_reason":       s.get("cb_reason", ""),
            "target_hit":      rm.is_target_hit,
            "execution_mode":  truth["execution_mode"],
            "truth_labels": truth,
            "enabled": runtime_health.get("enabled", True),
            "loop_alive": _running and runtime_health.get("loop_alive", False),
            "market_data_ok": runtime_health.get("market_data_ok", spec["platform"] != "stake"),
            "auth_ok": runtime_health.get("auth_ok", truth["auth_ok"]),
            "execution_ready": runtime_health.get("execution_ready", False),
            "reconciliation_ready": runtime_health.get("reconciliation_ready", False),
            "degraded_reason": runtime_health.get("degraded_reason", ""),
            # Last 7 bets as [1/0] for streak meter
            "last_7": [1 if n > 0 else 0
                       for n in list(_recent_trade_nets.get(bid, []))[-7:]],
            # ForceField
            "ff_phase":            s.get("ff_phase", "WITHDRAW_1"),
            "ff_withdrawals":      s.get("ff_withdrawals", 0),
            "ff_withdrawn_total":  round(getattr(rm, "ff_withdrawn_total", 0), 4),
            "ff_floor":            round(getattr(rm, "ff_floor", rm.start_amount * 0.80), 4),
            "ff_headroom":         s.get("ff_headroom", 0),
            "ff_to_next_withdraw": s.get("ff_to_next_withdraw", 0),
            "ff_real_target":      s.get("ff_real_target", rm.start_amount * 3),
        })
    return JSONResponse({
        "bots": bots,
        "tick": _tick,
        "goal_target": _goal_target,
        "running": _running,
        "executor": _refresh_executor_state(),
    })

async def get_equity(req: Request):
    _check_token(req)
    with _lock:
        data = list(_equity_history)
    if not data:
        try:
            rows = db.get_equity_snapshots(limit=240)
            data = []
            for row in rows:
                payload = dict(row.get("data") or {})
                payload["portfolio"] = round(float(row.get("portfolio", 0) or 0), 2)
                payload["label"] = row.get("label") or str(row.get("ts", ""))[11:16]
                payload["ts"] = row.get("ts")
                data.append(payload)
        except Exception:
            logger.exception("Failed to load persisted equity snapshots.")
    return JSONResponse({"equity": data, "empty": len(data) == 0})

async def get_logs(req: Request):
    _check_token(req)
    return JSONResponse({"logs": list(_log_entries)})

async def get_info(req: Request):
    _check_token(req)
    system_truth, _ = _build_system_truth()
    elapsed_wall = (time.time() - _start_wall) if _start_wall else 0
    elapsed_sim  = elapsed_wall * 10.0
    h, rem = divmod(int(elapsed_sim), 3600)
    m, s   = divmod(rem, 60)
    total_init     = sum(rm.initial_bankroll for rm in _rms.values()) if _rms else 0
    total_progress = sum(rm.progress for rm in _rms.values()) if _rms else 0
    c = db._cx()
    trade_count = c.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    c.close()
    return JSONResponse({
        "running":       _running,
        "simSpeed":      10.0,
        "simHours":      24.0,
        "uptimeStr":     f"{h:02d}:{m:02d}:{s:02d}",
        "elapsedSim":    round(elapsed_sim),
        "totalProgress": round(total_progress, 2),
        "totalInit":     total_init,
        "goalPct":       round(total_progress / _goal_target * 100, 1),
        "cycle":         _tick,
        "activeBots":    (sum(1 for rm in _rms.values() if not rm.is_halted) if _running else 0),
        "paperMode":     system_truth["execution_mode"] == "paper",
        "executionMode": system_truth["execution_mode"],
        "marketDataMode": "real_market_data",
        "persistedTrades": trade_count,
        "storageMode": system_truth["storage_mode"],
    })

# ── History ───────────────────────────────────────────────────────────────────

async def get_history(req: Request, bot_id: str = None, won: int = None,
                       game: str = None, limit: int = 100, offset: int = 0):
    _check_token(req)
    won_filter = None if won is None else bool(won)
    rows, total = db.get_trades(bot_id=bot_id, won=won_filter, game=game, limit=limit, offset=offset)
    # Running totals per bot
    return JSONResponse({"trades": rows, "total": total, "limit": limit, "offset": offset})

async def history_summary(req: Request):
    _check_token(req)
    c = db._cx()
    summary = {}
    known_ids = {item["id"] for item in _load_bot_registry()}
    trade_ids = {
        row[0] for row in c.execute("SELECT DISTINCT bot_id FROM trades").fetchall()
        if row[0]
    }
    for bid in sorted(known_ids | trade_ids):
        row = c.execute(
            "SELECT COUNT(*) as bets, SUM(won) as wins, SUM(net) as pnl "
            "FROM trades WHERE bot_id=?", (bid,)
        ).fetchone()
        summary[bid] = {
            "bets": row[0] or 0,
            "wins": row[1] or 0,
            "pnl":  round(row[2] or 0, 2),
            "win_rate": round((row[1] or 0) / max(row[0] or 1, 1) * 100, 1),
        }
    c.close()
    return JSONResponse(summary)

# ── Projections (Monte Carlo) ─────────────────────────────────────────────────

async def get_projections(req: Request):
    _check_token(req)
    from services.backtest_engine import project_bot_from_history, run_backtest

    result = {}

    for spec in _active_bot_specs():
        bid = spec["id"]
        rm = _rms.get(bid)
        if not rm:
            continue
        target = rm.initial_bankroll * 10
        projection = project_bot_from_history(
            db,
            bid,
            current_bankroll=float(rm.current_bankroll),
            target_amount=float(target),
            horizon=150,
            runs=300,
        )
        backtest = run_backtest(db, bid, strategy_params={"starting_equity": rm.current_bankroll})
        result[bid] = {
            **projection,
            "current": round(rm.current_bankroll, 2),
            "color": spec["color"],
            "backtest": backtest,
            "estimated_time_to_target": projection.get("time_to_target", "unknown"),
            "truth_label": projection.get("truth_label", "HISTORICAL REPLAY PROJECTION"),
            "realized_pnl": None,
        }
    return JSONResponse(result)

# ── Notes CRUD ────────────────────────────────────────────────────────────────

async def list_notes(req: Request, search: str = None, tag: str = None):
    _check_token(req)
    return JSONResponse({"notes": db.get_notes(search=search, tag=tag)})

async def add_note(req: Request, body: dict = Body(...)):
    _check_token(req)
    nid = db.create_note(body.get("title","Untitled"), body.get("content",""),
                         body.get("tags",[]), body.get("pinned", False))
    return JSONResponse({"id": nid, "ok": True})

async def edit_note(nid: int, req: Request, body: dict = Body(...)):
    _check_token(req)
    db.update_note(nid, **{k: v for k, v in body.items() if k in ("title","content","tags","pinned")})
    return JSONResponse({"ok": True})

async def remove_note(nid: int, req: Request):
    _check_token(req)
    db.delete_note(nid)
    return JSONResponse({"ok": True})

# ── Wallet ────────────────────────────────────────────────────────────────────

async def get_wallet(req: Request):
    _check_token(req)
    _ensure_bot_runtime_state()
    system_truth, _ = _build_system_truth()
    total_progress = sum(rm.progress for rm in _rms.values()) if _rms else 0
    total_locked   = sum(rm.total_withdrawn for rm in _rms.values()) if _rms else 0
    total_active   = sum(rm.current_bankroll for rm in _rms.values()) if _rms else 0
    txs = db.get_wallet_txs(50)
    total_initial = sum(rm.initial_bankroll for rm in _rms.values()) if _rms else 0
    total_deposited = sum(t["amount"] for t in txs if t["type"] == "deposit")
    total_deposited += total_initial
    return JSONResponse({
        "stake_balance":    round(sum(rm.current_bankroll for bid, rm in _rms.items()
                                      if "dice" in bid or "limbo" in bid or "mines" in bid), 2),
        "poly_balance":     round(sum(rm.current_bankroll for bid, rm in _rms.items()
                                      if "poly" in bid), 2),
        "total_active":     round(total_active, 2),
        "total_locked":     round(total_locked, 2),
        "total_progress":   round(total_progress, 2),
        "total_deposited":  round(total_deposited, 2),
        "unrealized_pnl":   round(total_active - total_initial, 2),
        "realized_pnl":     round(total_locked, 2),
        "transactions":     txs,
        "stake_connected":  bool(_get_runtime_setting("STAKE_API_TOKEN")),
        "poly_connected":   bool(_get_runtime_setting("POLY_PRIVATE_KEY")),
        "wallet_truth": system_truth["wallet_truth"],
        "money_movement_truth": "ledger_only",
        "truth_labels": {
            "wallet_truth": system_truth["wallet_truth"],
            "execution_mode": system_truth["execution_mode"],
        },
        "labels": {
            "withdraw": "Record Ledger Withdrawal",
            "vault": "Virtual Locked Profit Ledger",
        },
        "warning": "Wallet and vault actions are internal accounting records unless an external transfer route is explicitly implemented and confirmed.",
    })


async def get_wallet_transactions(req: Request, limit: int = 100):
    _check_token(req)
    return JSONResponse({
        "transactions": db.get_wallet_txs(limit),
        "wallet_truth": "virtual_ledger",
    })

async def deposit(req: Request, body: dict = Body(...)):
    _check_token(req)
    amount   = float(body.get("amount", 0))
    platform = body.get("platform", "manual")
    if amount <= 0:
        raise HTTPException(400, "Amount must be positive")
    db.save_wallet_tx("deposit", amount, note=f"Manual deposit via {platform}")
    logger.info(f"Deposit recorded: ${amount:.2f} from {platform}")
    return JSONResponse({
        "ok": True,
        "amount": amount,
        "truth_label": "virtual_ledger",
        "message": "Deposit recorded in the internal ledger.",
    })

async def request_withdraw(req: Request, body: dict = Body(...)):
    _check_token(req)
    from services.notification_center import notify_withdraw_request_recorded
    from services.reconciliation_service import begin_action, record_action_state

    amount = float(body.get("amount", 0))
    if amount <= 0:
        raise HTTPException(400, "Amount must be positive")
    note = body.get("note", "Withdrawal request")
    action = begin_action(
        "withdraw_request",
        source="api",
        payload={"amount": amount, "note": note, "money_movement_truth": "ledger_only"},
        db_module=db,
    )
    record_action_state(action["action_id"], "withdraw_request", "accepted", source="api", payload={"amount": amount}, db_module=db)
    db.save_wallet_tx("withdraw_request", amount, note=note)
    record_action_state(
        action["action_id"],
        "withdraw_request",
        "executed",
        source="api",
        reason="Ledger entry recorded; no external transfer route exists on this endpoint.",
        payload={"amount": amount, "note": note, "external_funds_moved": False},
        db_module=db,
    )
    logger.info(f"Withdrawal request: ${amount:.2f}")
    notify_withdraw_request_recorded(amount, note=note, db_module=db)
    return JSONResponse({
        "ok": True,
        "status": "requested",
        "amount": amount,
        "truth_label": "virtual_ledger",
        "money_movement_truth": "ledger_only",
        "external_funds_moved": False,
        "message": "Ledger withdrawal request recorded. No external funds were moved.",
        "degraded_reason": "External transfer execution is not implemented on this route.",
    })

# ── Notifications ─────────────────────────────────────────────────────────────

async def get_notifs(req: Request):
    _check_token(req)
    return JSONResponse({"notifications": db.get_notifications(30)})

# ── Bot registry ──────────────────────────────────────────────────────────────

async def get_bot_config(req: Request):
    _check_token(req)
    registry = _load_bot_registry()
    return JSONResponse({
        "bots": registry,
        "active_count": sum(1 for item in registry if item["enabled"]),
    })


async def get_bot_registry(req: Request):
    return await get_bot_config(req)


async def get_bot_launch_gates(bot_id: str, req: Request):
    _check_token(req)
    _init_platform_services()
    from services.launch_gates import check_launch_gates

    result = check_launch_gates(
        bot_id,
        db_module=db,
        adapter_registry=_market_registry,
        market_registry=_market_registry,
        vault=_vault,
        risk_kernel_available=True,
    )
    return JSONResponse(result)


async def save_bot_config(req: Request, body: dict = Body(...)):
    _check_token(req)
    incoming = body.get("bots", [])
    current = {item["id"]: item for item in _load_bot_registry()}
    for item in incoming:
        bid = item.get("id")
        if bid not in current:
            continue
        current[bid]["enabled"] = bool(item.get("enabled", current[bid]["enabled"]))
        display_name = str(item.get("display_name") or current[bid]["display_name"]).strip()
        current[bid]["display_name"] = display_name or bid
    registry = list(current.values())
    _save_bot_registry(registry)
    if _running:
        _stop_bots()
        _start_bots()
    return JSONResponse({
        "ok": True,
        "bots": registry,
        "active_count": sum(1 for item in registry if item["enabled"]),
        "history_preserved": True,
    })


async def save_bot_registry(req: Request, body: dict = Body(...)):
    return await save_bot_config(req, body)


def _begin_runtime_action(action_type: str, *, bot_id: str = "", source: str = "api", payload: dict | None = None) -> dict:
    from services.reconciliation_service import begin_action, record_action_state

    action = begin_action(action_type, bot_id=bot_id, source=source, payload=payload, db_module=db)
    record_action_state(
        action["action_id"],
        action_type,
        "accepted",
        bot_id=bot_id,
        source=source,
        payload=payload,
        db_module=db,
    )
    return action


def _finish_runtime_action(action: dict, state: str, *, reason: str = "", payload: dict | None = None) -> None:
    from services.reconciliation_service import record_action_state

    record_action_state(
        action["action_id"],
        action["action_type"],
        state,
        bot_id=action.get("bot_id", ""),
        source=action.get("source", "api"),
        reason=reason,
        payload=payload,
        db_module=db,
    )

# ── Bot controls ──────────────────────────────────────────────────────────────

async def set_all_strategy(req: Request, body: dict = Body(...)):
    _check_token(req)
    mode  = body.get("mode", "balanced")
    scale = _STRATEGY_SCALES.get(mode, 1.0)
    for spec in _active_bot_specs():
        bid = spec["id"]
        _strategy_modes[bid] = mode
        _apply_scale(bid, scale)
    logger.info(f"Global strategy → {mode} ({scale}×)")
    return JSONResponse({"ok": True, "mode": mode, "scale": scale})

async def set_bot_strategy(bot_id: str, req: Request, body: dict = Body(...)):
    _check_token(req)
    mode  = body.get("mode", "balanced")
    scale = _STRATEGY_SCALES.get(mode, _bet_scales.get(bot_id, 1.0))
    _strategy_modes[bot_id] = mode
    _apply_scale(bot_id, scale)
    return JSONResponse({"ok": True, "mode": mode, "scale": scale})

async def scale_bot(bot_id: str, req: Request, body: dict = Body(...)):
    _check_token(req)
    scale = max(0.1, min(5.0, float(body.get("scale", 1.0))))
    _apply_scale(bot_id, scale)
    return JSONResponse({"ok": True, "scale": scale})

# ── Per-bot pause / resume / fund ─────────────────────────────────────────────

_bot_pause_flags: dict[str, bool] = {}

async def pause_bot(bot_id: str, req: Request):
    _check_token(req)
    from services.notification_center import notify_bot_state

    if bot_id not in {spec["id"] for spec in _active_bot_specs()} and bot_id not in _rms:
        raise HTTPException(404, "Bot not found")
    action = _begin_runtime_action("pause_bot", bot_id=bot_id, payload={"requested_state": "paused"})
    if _bot_is_paused(bot_id):
        _finish_runtime_action(action, "noop", reason="Bot was already paused.", payload={"paused": True})
        return JSONResponse({"ok": True, "bot_id": bot_id, "paused": True, "state": "noop"})
    _bot_pause_flags[bot_id] = True
    rm = _rms.get(bot_id)
    if rm:
        rm.is_halted = True
    _runtime_health.update(bot_id, execution_ready=False, degraded_reason="Paused by operator.")
    logger.info(f"Bot {bot_id} paused via API")
    _finish_runtime_action(action, "executed", payload={"paused": True})
    notify_bot_state(bot_id, paused=True, db_module=db)
    _broadcast_sync({"type": "bot_paused", "bot_id": bot_id, "paused": True})
    return JSONResponse({"ok": True, "bot_id": bot_id, "paused": True, "state": "executed"})

async def resume_bot(bot_id: str, req: Request):
    _check_token(req)
    from services.notification_center import notify_bot_state

    if bot_id not in {spec["id"] for spec in _active_bot_specs()} and bot_id not in _rms:
        raise HTTPException(404, "Bot not found")
    action = _begin_runtime_action("resume_bot", bot_id=bot_id, payload={"requested_state": "running"})
    if not _bot_is_paused(bot_id):
        _finish_runtime_action(action, "noop", reason="Bot was not paused.", payload={"paused": False})
        return JSONResponse({"ok": True, "bot_id": bot_id, "paused": False, "state": "noop"})
    _bot_pause_flags.pop(bot_id, None)
    rm = _rms.get(bot_id)
    if rm:
        rm.is_halted = False
    _runtime_health.update(bot_id, execution_ready=True, degraded_reason="", last_error="")
    logger.info(f"Bot {bot_id} resumed via API")
    _finish_runtime_action(action, "executed", payload={"paused": False})
    notify_bot_state(bot_id, paused=False, db_module=db)
    _broadcast_sync({"type": "bot_resumed", "bot_id": bot_id, "paused": False})
    return JSONResponse({"ok": True, "bot_id": bot_id, "paused": False, "state": "executed"})

async def set_bot_growth_mode(bot_id: str, req: Request):
    """
    Enable or disable growth mode for a single bot.

    POST /api/bots/{bot_id}/growth
    Body: {
        "enabled": true,
        "kelly_fraction": 0.20,   // 0.01–0.35, default 0.20
        "target_amount": 5000.0   // optional; sets the compounding target
    }
    """
    _check_token(req)
    if bot_id not in _rms:
        raise HTTPException(404, "Bot not found or not running")
    body = await req.json()
    rm   = _rms[bot_id]
    enabled        = bool(body.get("enabled", True))
    kelly_fraction = body.get("kelly_fraction", None)
    target_amount  = body.get("target_amount", None)
    result = rm.set_growth_mode(enabled, kelly_fraction=kelly_fraction,
                                target_amount=target_amount)
    _broadcast_sync({"type": "growth_mode_changed", "bot_id": bot_id, **result})
    return JSONResponse({"ok": True, "bot_id": bot_id, **result})


async def get_bot_growth_mode(bot_id: str, req: Request):
    """GET /api/bots/{bot_id}/growth — current growth mode state."""
    _check_token(req)
    if bot_id not in _rms:
        raise HTTPException(404, "Bot not found or not running")
    rm = _rms[bot_id]
    return JSONResponse({
        "ok": True,
        "bot_id": bot_id,
        "growth_mode": rm.growth_mode,
        "kelly_fraction": rm.kelly_fraction,
        "ff_floor": rm.ff_floor,
        "target": rm.target,
        "ff_phase_label": rm.ff_phase_label,
        "bankroll": rm.bankroll,
        "progress_x": rm.progress_multiplier,
    })


# ── Canary session management ─────────────────────────────────────────────────

def _canary_check_breach(bot_id: str) -> tuple[bool, str]:
    """
    Check if active canary session for bot_id has hit a stop condition.
    Returns (breached: bool, breach_type: str).
    """
    session = _canary_sessions.get(bot_id)
    if not session:
        return False, ""
    rm = _rms.get(bot_id)
    if not rm:
        return False, ""
    max_drawdown_pct = session.get("max_drawdown_pct", 20.0)
    max_capital = session.get("max_capital_at_risk", rm.initial_bankroll)
    # Check drawdown
    current_drawdown_pct = max(0.0, (rm.peak_bankroll - rm.current_bankroll) / max(1e-9, rm.peak_bankroll) * 100)
    if current_drawdown_pct >= max_drawdown_pct:
        return True, "max_drawdown"
    # Check capital at risk
    if rm.current_bankroll <= (max_capital * 0.70):
        return True, "capital_floor"
    # Check red-line conditions set by operator
    red_lines = session.get("red_lines", [])
    for rl in red_lines:
        if rl.get("type") == "bankroll_below" and rm.current_bankroll <= rl.get("value", 0):
            return True, f"red_line:{rl.get('type')}"
    return False, ""


def _canary_record_event(bot_id: str, event_type: str, detail: str = "") -> None:
    session = _canary_sessions.get(bot_id)
    if not session:
        return
    session.setdefault("events", []).append({
        "ts": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "type": event_type,
        "detail": detail,
    })


async def start_canary_session(bot_id: str, req: Request):
    """
    Start a dust-live / canary session for a bot.
    Requires explicit operator enablement.
    """
    _check_token(req)
    try:
        body = await req.json()
    except Exception:
        body = {}
    if bot_id not in {spec["id"] for spec in _active_bot_specs()} and bot_id not in _rms:
        raise HTTPException(404, "Bot not found")
    if _canary_sessions.get(bot_id, {}).get("active"):
        return JSONResponse({"ok": False, "error": "canary_already_active", "bot_id": bot_id})
    max_drawdown_pct = float(body.get("max_drawdown_pct", 20.0))
    max_capital      = float(body.get("max_capital_at_risk", 1.0))
    venues           = body.get("venues", [])
    red_lines        = body.get("red_lines", [])
    session = {
        "active":               True,
        "bot_id":               bot_id,
        "session_id":           f"canary_{bot_id}_{int(time.time())}",
        "start_ts":             datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "stop_ts":              None,
        "venues":               venues,
        "max_capital_at_risk":  max_capital,
        "max_drawdown_pct":     max_drawdown_pct,
        "red_lines":            red_lines,
        "operator_present":     True,
        "observed_drawdown_pct": 0.0,
        "red_line_events":      [],
        "events":               [],
        "stop_reason":          None,
    }
    _canary_sessions[bot_id] = session
    _canary_record_event(bot_id, "session_started", f"max_drawdown={max_drawdown_pct}%, max_capital=${max_capital:.2f}")
    action = _begin_runtime_action("canary_start", bot_id=bot_id, payload={"session_id": session["session_id"]})
    _finish_runtime_action(action, "executed", payload={"session_id": session["session_id"]})
    logger.info(f"[canary] Session started for {bot_id}: {session['session_id']}")
    return JSONResponse({"ok": True, "bot_id": bot_id, "session": session})


async def stop_canary_session(bot_id: str, req: Request):
    """Stop canary session for a bot. Reason may be 'operator', 'breach', or other."""
    _check_token(req)
    try:
        body = await req.json()
    except Exception:
        body = {}
    session = _canary_sessions.get(bot_id)
    if not session or not session.get("active"):
        return JSONResponse({"ok": False, "error": "no_active_canary", "bot_id": bot_id})
    reason = str(body.get("reason", "operator"))
    session["active"]     = False
    session["stop_ts"]    = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    session["stop_reason"] = reason
    _canary_record_event(bot_id, "session_stopped", f"reason={reason}")
    action = _begin_runtime_action("canary_stop", bot_id=bot_id, payload={"reason": reason})
    _finish_runtime_action(action, "executed", payload={"reason": reason})
    if reason == "breach":
        from services.notification_center import notify_canary_breach
        notify_canary_breach(bot_id, breach_type="operator_stop", db_module=db)
    logger.info(f"[canary] Session stopped for {bot_id}: reason={reason}")
    return JSONResponse({"ok": True, "bot_id": bot_id, "session": session})


async def get_canary_status(req: Request):
    """Return all canary sessions (active and completed)."""
    _check_token(req)
    return JSONResponse({
        "sessions": list(_canary_sessions.values()),
        "active_count": sum(1 for s in _canary_sessions.values() if s.get("active")),
    })


async def fund_bot(bot_id: str, req: Request, body: dict = Body(...)):
    """Add funds to a bot's bankroll (simulation — for paper mode)."""
    _check_token(req)
    amount = float(body.get("amount", 0))
    if amount <= 0:
        raise HTTPException(400, "Amount must be positive")
    rm = _rms.get(bot_id)
    if not rm:
        raise HTTPException(404, "Bot not found")
    rm.bankroll        += amount
    rm.current_bankroll = rm.bankroll
    rm.start_amount    += amount   # adjust start so floor scales too
    rm.initial_bankroll = rm.start_amount
    rm.floor            = rm.start_amount * 0.40
    db.save_wallet_tx("fund", amount, note=f"Manual fund: {bot_id}")
    logger.info(f"[{bot_id}] funded +${amount:.2f} via API")
    _broadcast_sync({"type": "bot_funded", "bot_id": bot_id, "amount": amount, "bankroll": round(rm.bankroll, 4)})
    return JSONResponse({"ok": True, "bot_id": bot_id, "amount": amount, "new_bankroll": round(rm.bankroll, 4)})

# ── Phases endpoint ───────────────────────────────────────────────────────────

async def get_phases(req: Request):
    _check_token(req)
    phases = {}
    for bid, rm in _rms.items():
        phases[bid] = {
            "phase":       rm.phase,
            "drawdown_pct": round(rm.drawdown_pct * 100, 2),
            "bankroll":    round(rm.current_bankroll, 4),
            "streak":      getattr(rm, "streak", 0),
        }
    transitions = db.get_phase_transitions(limit=20)
    return JSONResponse({"phases": phases, "recent_transitions": transitions})

# ── Goals ─────────────────────────────────────────────────────────────────────

async def get_goals(req: Request):
    _check_token(req)
    total_progress = sum(rm.progress for rm in _rms.values()) if _rms else 0
    return JSONResponse({
        "target":   _goal_target,
        "progress": round(total_progress, 2),
        "pct":      round(min(total_progress / _goal_target * 100, 100), 1),
    })

async def set_goals(req: Request, body: dict = Body(...)):
    global _goal_target
    _check_token(req)
    _goal_target = max(float(body.get("target", 6000.0)), 100.0)
    return JSONResponse({"ok": True, "target": _goal_target})

# ── Timeline ──────────────────────────────────────────────────────────────────

async def get_timeline(req: Request):
    _check_token(req)
    events = db.get_events(limit=60)
    return JSONResponse({"events": events})

async def test_sms(req: Request):
    _check_token(req)
    sent = notifier.send_sms("6-Bot test SMS — your notifications are working!")
    return JSONResponse({"sent": sent})

async def test_telegram(req: Request, body: dict = Body(default={})):
    _check_token(req)
    from notifier_telegram import send_telegram, send_telegram_many

    s = _runtime_settings_snapshot()
    token   = body.get("bot_token")   or s.get("telegram_bot_token", "")
    chat_id = body.get("chat_id")     or s.get("telegram_chat_id",   "")
    message = "\U0001f916 <b>DeG\u00a3N\u0024</b> — Telegram notifications are working!"
    result = (
        send_telegram(message, chat_id=chat_id, bot_token=token)
        if chat_id
        else send_telegram_many(message, bot_token=token, settings=s)
    )
    return JSONResponse({
        "ok": bool(result.get("ok")),
        "delivery": "accepted" if result.get("ok") else "failed",
        "error": None if result.get("ok") else (result.get("error") or "telegram_delivery_failed"),
        "operator_chat_count": int(result.get("attempted_count", 1 if chat_id else 0)),
        "truth_label": "operator_notification_test",
        "credentials_echoed": False,
    })


_TELEGRAM_RESET_CONFIRMATIONS: dict[str, float] = {}


def _telegram_settings() -> dict:
    return _runtime_settings_snapshot()


def _telegram_allowed_chats(settings: dict | None = None) -> set[str]:
    from notifier_telegram import configured_operator_chat_ids

    return set(configured_operator_chat_ids(settings or _telegram_settings()))


def _telegram_reply(chat_id: str, message: str, *, settings: dict | None = None) -> dict:
    from notifier_telegram import send_telegram

    payload = settings or _telegram_settings()
    return send_telegram(
        message,
        chat_id=chat_id,
        bot_token=payload.get("telegram_bot_token", ""),
    )


def _telegram_runtime_summary() -> str:
    _ensure_bot_runtime_state()
    runtime_bots = []
    for spec in _active_bot_specs():
        rm = _rms.get(spec["id"])
        if rm is None:
            continue
        runtime_bots.append((spec, rm))
    active_count = sum(1 for spec, _rm in runtime_bots if _running and not _bot_is_paused(spec["id"]))
    paused = [spec["id"] for spec, _rm in runtime_bots if _bot_is_paused(spec["id"])]
    total_progress = sum(rm.progress for _spec, rm in runtime_bots)
    total_bankroll = sum(rm.current_bankroll for _spec, rm in runtime_bots)
    return (
        f"<b>DeG£N$ Status</b>\n"
        f"Runtime: <b>{'RUNNING' if _running else 'IDLE'}</b>\n"
        f"Active bots: <b>{active_count}</b> / {len(runtime_bots)}\n"
        f"Paused bots: <code>{', '.join(paused) if paused else 'none'}</code>\n"
        f"Bankroll: <b>${total_bankroll:.2f}</b>\n"
        f"Progress: <b>${total_progress:.2f}</b>\n"
        f"Execution: <code>{_build_system_truth()[0]['execution_mode']}</code>"
    )


def _is_masked_setting_value(key: str, value) -> bool:
    from utils.crypto import is_sensitive_key
    from utils.secrets import mask_value

    text = str(value or "")
    if not text:
        return False
    if "••" in text:
        return True
    if set(text) == {"*"} and len(text) >= 4:
        return True
    if not is_sensitive_key(str(key)):
        return False
    current = str(_runtime_settings_snapshot().get(str(key), "") or "")
    return bool(current) and text == mask_value(str(key), current)


def _save_setting_value(key: str, value, changed_keys: set[str]) -> None:
    normalized_key = str(key or "").strip()
    if not normalized_key:
        return
    if normalized_key in {"dashboard_password", "auth_password"}:
        if _is_masked_setting_value(normalized_key, value):
            return
        password = str(value or "")
        issues = _password_strength_issues(password)
        if issues:
            raise HTTPException(400, {"error": "weak_password", "issues": issues})
        _persist_password_hash(_hash(password), rotated=True)
        changed_keys.update({"auth_password_hash", "dashboard_password_hash"})
        return
    if _is_masked_setting_value(normalized_key, value):
        return
    if normalized_key in {"live_execution_enabled", "stake_live_enabled", "polymarket_live_enabled"}:
        value = "true" if _boolish(value, default=False) else "false"
    db.set_setting(normalized_key, value)
    changed_keys.add(normalized_key)


async def telegram_webhook(req: Request, body: dict = Body(default={})):
    s = _telegram_settings()
    expected_secret = s.get("telegram_webhook_secret", "").strip() or os.getenv("TELEGRAM_WEBHOOK_SECRET", "").strip()
    received_secret = req.headers.get("x-telegram-bot-api-secret-token", "").strip()
    if not expected_secret:
        raise HTTPException(503, "Telegram webhook secret is not configured")
    if not received_secret or not hmac.compare_digest(received_secret, expected_secret):
        raise HTTPException(403, "Invalid Telegram webhook secret")

    allowed_chats = _telegram_allowed_chats(s)
    callback = body.get("callback_query") or {}
    callback_chat_id = str(
        (((callback.get("message") or {}).get("chat") or {}).get("id", ""))
        or ((callback.get("from") or {}).get("id", ""))
        or ""
    )
    if callback and callback_chat_id not in allowed_chats:
        db.save_notification(
            f"Telegram callback ignored from unauthorized chat {callback_chat_id}",
            ntype="telegram_ignored",
            sent=False,
        )
        return JSONResponse({"ok": True, "ignored": True})
    data = str(callback.get("data", "") or "")
    if data.startswith("human:"):
        try:
            _, challenge_id, decision = data.split(":", 2)
        except ValueError:
            return JSONResponse({"ok": False, "error": "invalid_callback"})
        from services.human_relay import respond_to_challenge

        result = respond_to_challenge(
            challenge_id,
            decision,
            source="telegram",
            payload={"callback_query_id": callback.get("id"), "from": callback.get("from")},
            db_module=db,
        )
        return JSONResponse(result)
    message = body.get("message") or body.get("edited_message") or {}
    text = str(message.get("text", "") or "").strip()
    chat_id = str((message.get("chat") or {}).get("id", "") or "")
    if not text.startswith("/"):
        return JSONResponse({"ok": True, "ignored": True})
    if chat_id not in allowed_chats:
        db.save_notification(
            f"Telegram command ignored from unauthorized chat {chat_id}",
            ntype="telegram_ignored",
            sent=False,
        )
        return JSONResponse({"ok": True, "ignored": True})

    parts = text.split()
    command = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""

    if command == "/help":
        _telegram_reply(
            chat_id,
            (
                "<b>DeG£N$ — Telegram Command Console</b>\n\n"
                "<b>Status</b>\n"
                "/status — runtime state + bot summary\n"
                "/summary — same as /status\n"
                "/brief — operator brief + top actions\n"
                "/mall — MALL priorities and queue pressure\n"
                "/lab — LAB proof status and risk posture\n\n"
                "<b>Control</b>\n"
                "/start or /start_all — start all bot loops\n"
                "/stop or /stop_all — stop all bot loops\n"
                "/pause &lt;bot_id&gt; — pause one bot\n"
                "/resume &lt;bot_id&gt; — resume one bot\n"
                "/reset_zero — clear runtime history (requires /reset_zero confirm)\n\n"
                "<b>Limits</b>\n"
                "• Only pre-authorised chat IDs can send commands.\n"
                "• Telegram is a narrow control surface, not an autonomous agent.\n"
                "• stop_all and reset_zero require confirmation.\n"
                "• Wallet withdrawals and credential changes require the web UI.\n"
                "• This bot cannot make trades autonomously via Telegram."
            ),
            settings=s,
        )
        return JSONResponse({"ok": True, "command": command})

    if command in {"/status", "/summary"}:
        _telegram_reply(chat_id, _telegram_runtime_summary(), settings=s)
        return JSONResponse({"ok": True, "command": command})

    if command in {"/brief", "/mall", "/lab"}:
        from services.operator_playbooks import (
            build_operator_brief,
            format_lane_focus_markdown,
            format_operator_brief_markdown,
        )

        brief = build_operator_brief(
            db_module=db,
            running=bool(_running),
            runtime_bot_count=len(_rms),
            active_runtime_count=sum(1 for rm in _rms.values() if not getattr(rm, "is_halted", False)) if _running else 0,
        )
        if command == "/brief":
            message = format_operator_brief_markdown(brief)
        elif command == "/mall":
            message = format_lane_focus_markdown(brief, "mall")
        else:
            message = format_lane_focus_markdown(brief, "lab")
        _telegram_reply(chat_id, message, settings=s)
        return JSONResponse({"ok": True, "command": command})

    if command in {"/start", "/start_all"}:
        action = _begin_runtime_action("start_all", source="telegram", payload={"chat_id": chat_id})
        if _running:
            _finish_runtime_action(action, "noop", reason="Runtime already running.", payload={"running": True})
            _telegram_reply(chat_id, "Runtime is already running.", settings=s)
            return JSONResponse({"ok": True, "command": command, "state": "noop"})
        try:
            started = bool(_start_bots())
            start_reason = ""
        except HTTPException as exc:
            started = False
            start_reason = str(exc.detail)
        _finish_runtime_action(
            action,
            "executed" if started else "failed",
            reason="" if started else (start_reason or "Execution authority unavailable or startup failed."),
            payload={"running": started},
        )
        if started:
            from services.notification_center import notify_runtime_state

            notify_runtime_state(running=True, source="telegram", db_module=db)
        _telegram_reply(chat_id, "Runtime started." if started else f"Runtime start failed. {start_reason or ''}".strip(), settings=s)
        return JSONResponse({"ok": started, "command": command})

    if command in {"/stop", "/stop_all"}:
        action = _begin_runtime_action("stop_all", source="telegram", payload={"chat_id": chat_id})
        if not _running:
            _finish_runtime_action(action, "noop", reason="Runtime already idle.", payload={"running": False})
            _telegram_reply(chat_id, "Runtime is already idle.", settings=s)
            return JSONResponse({"ok": True, "command": command, "state": "noop"})
        _stop_bots()
        _finish_runtime_action(action, "executed", payload={"running": False})
        from services.notification_center import notify_runtime_state

        notify_runtime_state(running=False, source="telegram", db_module=db)
        _telegram_reply(chat_id, "Runtime stopped.", settings=s)
        return JSONResponse({"ok": True, "command": command})

    if command in {"/pause", "/resume"}:
        bot_id = arg.strip()
        if not bot_id:
            _telegram_reply(chat_id, "Provide a bot id, for example /pause bot4_poly", settings=s)
            return JSONResponse({"ok": False, "command": command, "error": "missing_bot_id"})
        if bot_id not in {spec["id"] for spec in _active_bot_specs()} and bot_id not in _rms:
            _telegram_reply(chat_id, f"Unknown bot id: {bot_id}", settings=s)
            return JSONResponse({"ok": False, "command": command, "error": "unknown_bot"})
        paused = command == "/pause"
        action = _begin_runtime_action(
            "pause_bot" if paused else "resume_bot",
            bot_id=bot_id,
            source="telegram",
            payload={"chat_id": chat_id},
        )
        if paused and _bot_is_paused(bot_id):
            _finish_runtime_action(action, "noop", reason="Bot already paused.", payload={"paused": True})
            _telegram_reply(chat_id, f"{bot_id} is already paused.", settings=s)
            return JSONResponse({"ok": True, "command": command, "state": "noop"})
        if (not paused) and (not _bot_is_paused(bot_id)):
            _finish_runtime_action(action, "noop", reason="Bot not paused.", payload={"paused": False})
            _telegram_reply(chat_id, f"{bot_id} is already running.", settings=s)
            return JSONResponse({"ok": True, "command": command, "state": "noop"})
        if paused:
            _bot_pause_flags[bot_id] = True
            if bot_id in _rms:
                _rms[bot_id].is_halted = True
            _runtime_health.update(bot_id, execution_ready=False, degraded_reason="Paused by operator.")
            _broadcast_sync({"type": "bot_paused", "bot_id": bot_id, "paused": True})
        else:
            _bot_pause_flags.pop(bot_id, None)
            if bot_id in _rms:
                _rms[bot_id].is_halted = False
            _runtime_health.update(bot_id, execution_ready=True, degraded_reason="", last_error="")
            _broadcast_sync({"type": "bot_resumed", "bot_id": bot_id, "paused": False})
        _finish_runtime_action(action, "executed", payload={"paused": paused})
        from services.notification_center import notify_bot_state

        notify_bot_state(bot_id, paused=paused, source="telegram", db_module=db)
        _telegram_reply(chat_id, f"{bot_id} {'paused' if paused else 'resumed'}.", settings=s)
        return JSONResponse({"ok": True, "command": command})

    if command == "/reset_zero":
        now = time.time()
        if len(parts) > 1 and parts[1].lower() == "confirm":
            expires_at = _TELEGRAM_RESET_CONFIRMATIONS.get(chat_id, 0.0)
            if expires_at < now:
                _telegram_reply(chat_id, "Reset confirmation expired. Send /reset_zero again.", settings=s)
                return JSONResponse({"ok": False, "command": command, "error": "confirmation_expired"})
            _TELEGRAM_RESET_CONFIRMATIONS.pop(chat_id, None)
            action = _begin_runtime_action("reset_to_zero", source="telegram", payload={"chat_id": chat_id})
            result = _reset_runtime_to_zero_state()
            _finish_runtime_action(action, "executed", payload=result)
            from services.notification_center import notify_reset_to_zero

            notify_reset_to_zero(source="telegram", db_module=db)
            _telegram_reply(chat_id, "Reset to zero completed.", settings=s)
            return JSONResponse({"ok": True, "command": command, "result": result})
        _TELEGRAM_RESET_CONFIRMATIONS[chat_id] = now + 120.0
        _telegram_reply(
            chat_id,
            "Reset to zero is destructive for runtime history and ledger runtime balances. Confirm with /reset_zero confirm within 120 seconds.",
            settings=s,
        )
        return JSONResponse({"ok": True, "command": command, "state": "pending_confirmation"})
    return JSONResponse({"ok": True, "ignored": True})

# ── Settings ──────────────────────────────────────────────────────────────────

async def get_settings(req: Request):
    _check_token(req)
    from utils.secrets import mask_settings

    settings = _runtime_settings_snapshot()
    return JSONResponse(mask_settings(settings))

async def save_settings(req: Request, body: dict = Body(...)):
    _check_token(req)
    from utils.crypto import is_sensitive_key

    settings_items = _settings_body_items(body)
    sensitive_keys = sorted({key for key, _ in settings_items if _setting_requires_reauth(key)})
    if os.getenv("FLY_APP_NAME"):
        blocked = []
        for key, _ in settings_items:
            if key and is_sensitive_key(str(key)):
                blocked.append(str(key))
        if blocked:
            raise HTTPException(400, f"Sensitive settings must come from Fly secrets in production: {', '.join(blocked)}")
    if sensitive_keys:
        _require_reauth_password(
            req,
            str(body.get("reauth_password", "") or ""),
            reason=f"changing sensitive settings ({', '.join(sensitive_keys)})",
        )
    changed_keys: set[str] = set()
    for key, value in settings_items:
        _save_setting_value(key, value, changed_keys)
    if changed_keys:
        _reload_runtime_after_settings_save(changed_keys)
    return JSONResponse({"ok": True})

# ── VPN / Tor endpoints ───────────────────────────────────────────────────────

async def vpn_status(req: Request):
    _check_token(req)
    proxy_host = _get_runtime_setting("proxy_host", "")
    proxy_port = _get_runtime_setting("proxy_port", "")
    last_verified_ts = _get_runtime_setting("proxy_last_verified_ts", "")
    proxy_truth, proxy_reason = network_routing_truth(
        proxy_host,
        proxy_port,
        proxy_verified=bool(last_verified_ts),
    )
    return JSONResponse({
        "status":  _tor_status,
        "ip":      _tor_ip,
        "country": _tor_country,
        "installed": _find_bin("tor") is not None,
        "proxy_configured": bool(proxy_host and proxy_port),
        "proxy_host": proxy_host or None,
        "proxy_port": proxy_port or None,
        "proxy_last_verified_ts": last_verified_ts or None,
        "proxy_last_verified_ip": _get_runtime_setting("proxy_last_verified_ip", "") or None,
        "proxy_last_verified_country": _get_runtime_setting("proxy_last_verified_country", "") or None,
        "truth_label": "Tor circuit test only",
        "network_routing_truth": proxy_truth,
        "degraded_reason": proxy_reason,
    })

async def vpn_start(req: Request):
    _check_token(req)
    threading.Thread(target=_start_tor, daemon=True).start()
    return JSONResponse({"ok": True})

async def vpn_renew(req: Request):
    _check_token(req)
    ok = _renew_tor_circuit()
    return JSONResponse({"ok": ok})

# ── Remote access tunnel endpoints ────────────────────────────────────────────

async def tunnel_status(req: Request):
    _check_token(req)
    return JSONResponse({
        "status":    _tunnel_status,
        "url":       _tunnel_url,
        "provider":  _tunnel_provider,
        "installed": (_find_bin("cloudflared") is not None) or (_find_bin("npx") is not None),
        "providers": {
            "cloudflared": _find_bin("cloudflared") is not None,
            "localtunnel": _find_bin("npx") is not None,
        },
    })

async def tunnel_start(req: Request):
    _check_token(req)
    threading.Thread(target=_start_tunnel, daemon=True).start()
    return JSONResponse({"ok": True})

async def tunnel_stop(req: Request):
    _check_token(req)
    _stop_tunnel_process()
    return JSONResponse({"ok": True})

# ── Device management endpoints ───────────────────────────────────────────────

async def list_devices(req: Request):
    _check_token(req)
    return JSONResponse({"devices": db.get_devices()})

async def approve_device(req: Request, body: dict = Body(...)):
    _check_token(req)
    _require_reauth_password(
        req,
        str(body.get("reauth_password", "") or ""),
        reason="approving trusted devices",
    )
    db.approve_device(body["device_id"], body.get("name", ""))
    return JSONResponse({"ok": True, "devices": db.get_devices()})

async def revoke_device(req: Request, body: dict = Body(...)):
    _check_token(req)
    _require_reauth_password(
        req,
        str(body.get("reauth_password", "") or ""),
        reason="revoking trusted devices",
    )
    db.revoke_device(body["device_id"])
    return JSONResponse({"ok": True, "devices": db.get_devices()})

async def rename_device(req: Request, body: dict = Body(...)):
    _check_token(req)
    db.rename_device(body["device_id"], body["name"])
    return JSONResponse({"ok": True, "devices": db.get_devices()})

# ── Proxy / VPN helpers ───────────────────────────────────────────────────────

def _get_proxies(force: bool = False):
    """Return requests-compatible proxy dict if proxy is configured."""
    host = _get_runtime_setting("proxy_host", "")
    port = _get_runtime_setting("proxy_port", "")
    if host and port:
        url = f"socks5h://{host}:{port}"
        return {"http": url, "https": url}
    return None

async def test_proxy(req: Request):
    _check_token(req)
    proxies = _get_proxies()
    if not proxies:
        return JSONResponse({"ok": False, "error": "No proxy configured"})
    try:
        import requests as _req
        r = _req.get("https://ipapi.co/json/", proxies=proxies, timeout=10)
        d = r.json()
        db.set_setting("proxy_last_verified_ts", str(time.time()))
        db.set_setting("proxy_last_verified_ip", d.get("ip") or "")
        db.set_setting("proxy_last_verified_country", d.get("country_name") or "")
        return JSONResponse({
            "ok": True,
            "ip": d.get("ip"),
            "country": d.get("country_name"),
            "network_routing_truth": "proxy_cosmetic",
            "truth_label": "Proxy test passed for outbound IP verification on this host.",
        })
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})

# ── Polymarket auto-wallet ────────────────────────────────────────────────────

async def get_poly_wallet(req: Request):
    _check_token(req)
    pk = db.get_setting("poly_auto_private_key")
    if pk:
        from eth_account import Account
        acct = Account.from_key(pk)
        return JSONResponse({"address": acct.address, "exists": True})
    return JSONResponse({"address": None, "exists": False})

async def generate_poly_wallet(req: Request):
    _check_token(req)
    existing = db.get_setting("poly_auto_private_key")
    if existing:
        from eth_account import Account
        acct = Account.from_key(existing)
        return JSONResponse({"address": acct.address, "exists": True})
    from eth_account import Account
    acct = Account.create()
    db.set_setting("poly_auto_private_key", acct.key.hex())
    logger.info(f"Polymarket auto-wallet generated: {acct.address}")
    return JSONResponse({"address": acct.address, "exists": True})

# ── Control ───────────────────────────────────────────────────────────────────

async def start_bots(req: Request):
    _check_token(req)
    from services.notification_center import notify_runtime_state

    action = _begin_runtime_action("start_all", payload={"requested_running": True})
    if _running:
        _finish_runtime_action(action, "noop", reason="Runtime already running.", payload={"running": True})
        _ensure_bot_runtime_state()
        return JSONResponse({"ok": True, "state": "noop", "executor": _refresh_executor_state()})
    try:
        started = bool(_start_bots())
        failure_reason = ""
    except HTTPException as exc:
        started = False
        failure_reason = str(exc.detail)
        _finish_runtime_action(
            action,
            "failed",
            reason=failure_reason or "Execution authority unavailable or runtime failed to start.",
            payload={"running": False},
        )
        raise
    _finish_runtime_action(
        action,
        "executed" if started else "failed",
        reason="" if started else (failure_reason or "Execution authority unavailable or runtime failed to start."),
        payload={"running": started},
    )
    if started:
        notify_runtime_state(running=True, db_module=db)
    _ensure_bot_runtime_state()
    return JSONResponse({"ok": started, "state": "executed" if started else "failed", "executor": _refresh_executor_state()})

async def stop_bots(req: Request):
    _check_token(req)
    from services.notification_center import notify_runtime_state

    action = _begin_runtime_action("stop_all", payload={"requested_running": False})
    if not _running:
        _finish_runtime_action(action, "noop", reason="Runtime already idle.", payload={"running": False})
        return JSONResponse({"ok": True, "state": "noop", "executor": _refresh_executor_state()})
    _stop_bots()
    _finish_runtime_action(action, "executed", payload={"running": False})
    notify_runtime_state(running=False, db_module=db)
    return JSONResponse({"ok": True, "state": "executed", "executor": _refresh_executor_state()})


def _reset_runtime_to_zero_state() -> dict:
    global _tick, _start_wall, _runtime_health, _initial_total

    _stop_bots()
    with _lock:
        _equity_history.clear()
        _log_entries.clear()
        _recent_trade_nets.clear()
        _poly_runtime.clear()
        _bot_pause_flags.clear()
        _tick = 0
        _start_wall = None

    quota_budgeter._SINGLETON = quota_budgeter.QuotaBudgeter()

    cx = db._cx()
    try:
        tables = {
            row[0] for row in cx.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        for table in (
            "trades",
            "equity_snapshots",
            "events",
            "notifications",
            "wallet_tx",
            "phase_transitions",
            "bot_runtime_state",
            "bot_runtime_history",
            "order_requests",
            "order_fills",
            "order_settlements",
            "reconciliation_events",
            "api_quota_usage",
            "human_relay_requests",
            "phase_transition_log",
            "runtime_truth_history",
        ):
            if table in tables:
                cx.execute(f"DELETE FROM {table}")
        cx.commit()
    finally:
        cx.close()

    _ensure_bot_runtime_state(reset=True)
    _runtime_health = RuntimeHealthTracker()
    for bid, rm in _rms.items():
        rm.bankroll = 0.0
        rm.current_bankroll = 0.0
        rm.initial_bankroll = 0.0
        rm.total_withdrawn = 0.0
        rm.vault = 0.0
        rm.peak_bankroll = max(rm.start_amount, 0.0)
        rm._last_lock_level = 0
        rm.bet_count = 0
        rm.win_count = 0
        rm.loss_count = 0
        rm.streak = 0
        rm._recent_pnl.clear()
        rm._vel_samples.clear()
        rm._cooldown_until = 0.0
        rm._cb_reason = ""
        rm._vel_boost_remaining = 0
        rm._vel_boost_factor = 1.0
        rm.is_target_hit = False
        rm.milestone_hit = False
        rm.continue_to = None
        rm.ff_withdrawals = 0
        rm.ff_phase_label = "WITHDRAW_1"
        rm.ff_withdrawn_total = 0.0
        rm._recalculate_phase()
        _runtime_health.ensure(bid, enabled=True)

    _initial_total = 0.0
    _persist_bot_runtime_snapshots(include_history=False)
    _broadcast_sync({"type": "reset_to_zero", "running": _running, "ts": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")})
    return {
        "bots_reset": len(_rms),
        "running": _running,
        "executor": _refresh_executor_state(),
    }


async def reset_to_zero(req: Request, body: dict = Body(default={})):
    _check_token(req)
    from services.notification_center import notify_reset_to_zero

    _require_reauth_password(
        req,
        str(body.get("reauth_password", "") or ""),
        reason="resetting runtime state to zero",
    )
    action = _begin_runtime_action("reset_to_zero", payload={"requested": True})
    result = _reset_runtime_to_zero_state()
    _finish_runtime_action(action, "executed", payload=result)
    notify_reset_to_zero(db_module=db)
    return JSONResponse({"ok": True, **result})

# ── Per-bot configuration ─────────────────────────────────────────────────────

async def configure_bot(bot_id: str, req: Request, body: dict = Body(...)):
    """Set start_amount, target_amount, floor_amount for a bot and restart it."""
    _check_token(req)
    start  = float(body.get("start_amount",  100.0))
    target = float(body.get("target_amount", start * 5.0))
    floor  = float(body.get("floor_amount",  start * 0.40))
    if start <= 0 or target <= start or floor >= start:
        raise HTTPException(400, "Invalid config: start > floor, target > start")
    _bot_configs[bot_id] = {"start_amount": start, "target_amount": target, "floor_amount": floor}
    # Live reconfigure if bot is running
    if bot_id in _rms:
        _rms[bot_id].reconfigure(start, target, floor)
    # Persist
    all_cfgs = json.loads(db.get_setting("bot_configs", "{}") or "{}")
    all_cfgs[bot_id] = _bot_configs[bot_id]
    db.set_setting("bot_configs", json.dumps(all_cfgs))
    return JSONResponse({"ok": True, "bot_id": bot_id, "config": _bot_configs[bot_id]})


async def milestone_continue(bot_id: str, req: Request, body: dict = Body(...)):
    """User acknowledges 3x milestone and chooses new target (10x or 20x)."""
    _check_token(req)
    multiplier = float(body.get("multiplier", 10.0))
    if multiplier < 3:
        raise HTTPException(400, "multiplier must be ≥ 3")
    rm = _rms.get(bot_id)
    if not rm:
        raise HTTPException(404, "Bot not found")
    rm.continue_after_milestone(multiplier)
    db.save_event("info", f"{bot_id} continuing to {multiplier}x after milestone", bot_id)
    return JSONResponse({"ok": True, "new_target": round(rm.target, 2)})


# ── Vault ─────────────────────────────────────────────────────────────────────

async def get_vault(req: Request):
    _check_token(req)
    _ensure_bot_runtime_state()
    total_vault = sum(getattr(rm, "vault", 0) for rm in _rms.values())
    total_locked = sum(rm.total_withdrawn for rm in _rms.values())
    per_bot = {
        bid: {"vault": round(getattr(rm, "vault", 0), 4), "locked": round(rm.total_withdrawn, 4)}
        for bid, rm in _rms.items()
    }
    return JSONResponse({
        "total_vault": round(total_vault, 4),
        "total_locked": round(total_locked, 4),
        "per_bot": per_bot,
        "truth_label": "virtual_locked_profit_ledger",
        "warning": "Vault values are internal protected-ledger balances, not external custody transfers.",
    })


async def get_ladder_status(req: Request):
    _check_token(req)
    _init_platform_services()
    if _profit_ladder is None:
        return JSONResponse({"ok": False, "error": "ladder_not_initialized"}, status_code=503)
    return JSONResponse({"ok": True, **_profit_ladder.status()})


async def lock_to_vault(req: Request, body: dict = Body(...)):
    """Move withdrawn profits into the permanent vault for a bot."""
    _check_token(req)
    _ensure_bot_runtime_state()
    bot_id = body.get("bot_id", "")
    amount = float(body.get("amount", 0))
    rm = _rms.get(bot_id)
    if not rm:
        raise HTTPException(404, "Bot not found")
    locked = rm.send_to_vault(amount)
    db.save_wallet_tx("vault_lock", locked, note=f"{bot_id} → vault")
    return JSONResponse({
        "ok": True,
        "locked": round(locked, 4),
        "vault_total": round(rm.vault, 4),
        "truth_label": "virtual_locked_profit_ledger",
        "message": "Protected-ledger lock recorded. No external transfer was sent.",
    })


# ── Load saved bot configs on startup ─────────────────────────────────────────
def _load_bot_configs():
    try:
        stored = json.loads(db.get_setting("bot_configs", "{}") or "{}")
        _bot_configs.update(stored)
    except Exception:
        pass


def _run_expansion_bot(bot_id: str):
    _init_platform_services()
    bot = instantiate_bot(bot_id, _market_registry)
    result = bot.run_one_cycle()
    payload = result.get("data") or {}
    market_id = payload.get("market_id")
    event_id = payload.get("event_id")
    if result.get("platform") == "kalshi_public" and market_id:
        db.save_orderbook_snapshot("kalshi_public", market_id, payload)
    if result.get("platform") == "oddsapi" and event_id:
        db.save_odds_snapshot(
            "oddsapi",
            payload.get("sport_key", "upcoming"),
            event_id,
            payload.get("bookmaker", ""),
            payload.get("market_key", result.get("signal_type", "")),
            payload,
        )
    persist_signal(db, result)
    # Persist crossvenue matches if the bot returned any
    for match in payload.get("crossvenue_matches", []):
        try:
            db.save_crossvenue_match(match)
        except Exception as _exc:
            logger.warning(f"[{bot_id}] crossvenue match persist failed: {_exc}")
    return result


def _run_bot_proposal_cycle(bot_id: str):
    _init_platform_services()
    try:
        bot = instantiate_bot(bot_id, _market_registry)
    except Exception as exc:
        return {"state": "error", "degraded_reason": str(exc), "skipped": True}

    from services.order_router import route_proposal
    from services.portfolio_forcefield import expire_stale_reservations
    from services.risk_kernel import MAX_DRAWDOWN_PCT

    expire_stale_reservations(db)
    context = {"runtime_mode": _get_runtime_setting("runtime_mode", "paper")}
    proposal = bot.generate_proposal(context)
    if proposal is None:
        return {"state": "no_actionable_edge", "bot_id": bot_id, "skipped": True}

    wallet = db.get_wallet_summary() if hasattr(db, "get_wallet_summary") else {"working_capital": 100.0, "deposits": 100.0}
    working = float(wallet.get("working_capital", 100.0) or 100.0)
    starting = float(wallet.get("deposits", working) or working or 100.0)
    floor_val = starting * (1.0 - MAX_DRAWDOWN_PCT)

    corr_count = 0
    if hasattr(db, "count_open_proposals_by_correlation") and proposal.correlation_key:
        corr_count = db.count_open_proposals_by_correlation(proposal.correlation_key)
    venue_conc = db.get_venue_concentration(proposal.platform) if hasattr(db, "get_venue_concentration") else 0.0
    total_notional = db.get_total_open_notional() if hasattr(db, "get_total_open_notional") else 0.0
    event_count = db.count_simultaneous_events() if hasattr(db, "count_simultaneous_events") else 0

    result = route_proposal(
        proposal,
        db_module=db,
        registry=_market_registry,
        execution_mode=context["runtime_mode"],
        risk_context={
            "working_capital": working,
            "floor": floor_val,
            "repel_zone": starting * 0.149,
            "open_proposals_in_correlation_group": corr_count,
            "venue_concentration": venue_conc,
            "current_total_notional": total_notional,
            "simultaneous_event_exposure": event_count,
            "max_total_notional": max(working * 0.5, 50.0),
        },
    )
    try:
        from services.notification_center import notify_profit_ladder_lock, notify_proposal_routed

        notify_proposal_routed(proposal.model_dump(), result, source="scheduler", db_module=db)
    except Exception as exc:
        logger.warning("Proposal notification failed for %s: %s", bot_id, exc)
    if _profit_ladder is not None:
        result["ladder"] = _profit_ladder.check_and_lock(working, bot_id=bot_id)
        try:
            from services.notification_center import notify_profit_ladder_lock

            notify_profit_ladder_lock(bot_id, result["ladder"], db_module=db)
        except Exception as exc:
            logger.warning("Profit ladder notification failed for %s: %s", bot_id, exc)
    result.setdefault("state", result.get("decision") or result.get("risk_decision", {}).get("decision", "proposal_routed"))
    return result


def _snapshot_bot_state(bot_id: str) -> dict:
    rm = _rms.get(bot_id)
    if rm:
        return rm.serialize_runtime_state()
    runtime = _poly_runtime.get(bot_id, {})
    if runtime:
        return {
            "bankroll": float(runtime.get("bankroll", 100.0) or 100.0),
            "phase": runtime.get("phase", "normal"),
            "runtime": runtime,
        }
    persisted = db.get_bot_runtime_state(bot_id)
    if persisted:
        return persisted.get("payload") or {
            "bankroll": float(persisted.get("bankroll", 100.0) or 100.0),
            "phase": persisted.get("phase", "normal"),
            "vault": float(persisted.get("vault", 0.0) or 0.0),
            "streak": int(persisted.get("streak", 0) or 0),
            "runtime_health": persisted.get("runtime_health") or {},
        }
    return {}


def _persist_simulator_result(result, report: dict):
    from services.simulator_engine import result_to_dict

    result_dict = result_to_dict(result)
    db.save_simulator_run(
        run_id=result.run_id,
        mode=result.mode,
        source_bot=result.config.bot_id,
        source_strategy=result.config.strategy_id or result.config.strategy,
        params=result_dict.get("config", {}),
        state="complete",
        wall_time_s=result_dict.get("estimated_real_elapsed_s"),
        truth_label=result.truth_label,
        payload=result_dict,
    )
    db.save_simulator_steps(result.run_id, [step for step in result_dict.get("steps", [])])
    db.save_simulator_report(
        result.run_id,
        replication_probability=report["replication_probability"],
        realism_score=report["realism_score"],
        estimated_real_elapsed_s=result.estimated_real_elapsed_s,
        pnl_p10=report["pnl_p10"],
        pnl_p50=report["pnl_p50"],
        pnl_p90=report["pnl_p90"],
        max_drawdown_p50=report["max_drawdown_p50"],
        hit_rate_estimate=report["hit_rate_estimate"],
        why_winning=report["why_winning"],
        why_losing=report["why_losing"],
        strengths=report["strengths"],
        weaknesses=report["weaknesses"],
        caveats=report["caveats"],
        assumptions=report["assumptions"],
        truth_label=report["truth_label"],
        payload=report,
    )
    return result_dict


# ── WebSocket ─────────────────────────────────────────────────────────────────

async def websocket_endpoint(ws: WebSocket):
    # Token check via query param: /api/ws?token=<token>
    token = ws.query_params.get("token", "")
    if not token:
        await ws.close(code=4401)
        return
    with _auth_lock:
        record = _prune_and_resolve_token_locked(time.time(), token, touch=True)
    if not record:
        await ws.close(code=4401)
        return
    await _ws_manager.connect(ws)
    try:
        while True:
            # Keep alive; client can send ping, we just echo
            msg = await ws.receive_text()
            if msg == "ping":
                with _auth_lock:
                    record = _prune_and_resolve_token_locked(time.time(), token, touch=True)
                if not record:
                    await ws.close(code=4401)
                    return
                await ws.send_text("pong")
    except WebSocketDisconnect:
        pass
    finally:
        _ws_manager.disconnect(ws)


# ── Vault history ─────────────────────────────────────────────────────────────

async def vault_history(req: Request, bot_id: str = None, limit: int = 100):
    _check_token(req)
    rows = db.get_vault_history(bot_id=bot_id, limit=limit)
    total = sum(r["amount"] for r in rows)
    return JSONResponse({"history": rows, "total": round(total, 4)})


# ── Circuit-breaker log ───────────────────────────────────────────────────────

async def get_circuit_breakers(req: Request, bot_id: str = None):
    _check_token(req)
    rows = db.get_circuit_breakers(bot_id=bot_id, limit=100)
    return JSONResponse({"circuit_breakers": rows})


# ── Phase transition log ──────────────────────────────────────────────────────

async def get_phase_transitions(req: Request, bot_id: str = None):
    _check_token(req)
    rows = db.get_phase_transitions(bot_id=bot_id, limit=200)
    return JSONResponse({"transitions": rows})


# ── Volume spikes (Polymarket) ────────────────────────────────────────────────

async def get_volume_spikes(req: Request, min_ratio: float = 3.0):
    _check_token(req)
    rows = db.get_volume_spikes(min_ratio=min_ratio, limit=20)
    return JSONResponse({"spikes": rows})


async def get_btc_price(req: Request):
    _check_token(req)
    runtime = _poly_runtime.get("bot7_momentum", {})
    return JSONResponse({
        "price": runtime.get("btc_price"),
        "ts": runtime.get("last_scan_ts"),
        "execution_mode": "paper",
        "data_mode": "real_market_data",
    })


async def get_arbitrage(req: Request):
    _check_token(req)
    runtime = _poly_runtime.get("bot8_arb", {})
    return JSONResponse({
        "opportunities": runtime.get("opportunities", []),
        "count": runtime.get("opportunity_count", 0),
        "best_edge": runtime.get("best_edge"),
        "execution_mode": "paper",
        "data_mode": "real_market_data",
    })


async def get_strategy_runtime(req: Request):
    _check_token(req)
    return JSONResponse({
        "execution_mode": "paper",
        "paper_execution_only": True,
        "bots": _poly_runtime,
    })


async def get_data_sources(req: Request):
    _check_token(req)
    gamma_ts = max((row.get("last_scan_ts", 0.0) for row in _poly_runtime.values()), default=0.0)
    btc_runtime = _poly_runtime.get("bot7_momentum", {})
    return JSONResponse({
        "execution_mode": "paper",
        "paper_execution_only": True,
        "sources": {
            "gamma": {
                "mode": "public_api",
                "active": gamma_ts > 0,
                "last_scan_ts": gamma_ts,
            },
            "binance": {
                "mode": "public_api",
                "active": btc_runtime.get("btc_price") is not None,
                "last_scan_ts": btc_runtime.get("last_scan_ts", 0.0),
                "price": btc_runtime.get("btc_price"),
            },
            "stake_credentials_present": bool(db.get_setting("stake_api_token")),
            "polymarket_credentials_present": bool(db.get_setting("poly_private_key")),
        },
    })


async def get_system_truth(req: Request):
    _check_token(req)
    system_truth, platforms = _build_system_truth()
    return JSONResponse({
        "truth": system_truth,
        "platforms": platforms,
        "executor": _refresh_executor_state(),
    })


async def get_system_home(req: Request):
    _check_token(req)
    payload = build_home_payload()
    try:
        from services.operator_playbooks import build_operator_brief

        payload["operator_brief"] = build_operator_brief(
            db_module=db,
            running=bool(_running),
            runtime_bot_count=len(_rms),
            active_runtime_count=sum(1 for rm in _rms.values() if not getattr(rm, "is_halted", False)) if _running else 0,
        )
    except Exception as exc:
        payload["operator_brief_error"] = str(exc)
    return JSONResponse(payload)


async def get_operator_brief(req: Request):
    _check_token(req)
    from services.operator_playbooks import build_operator_brief

    brief = build_operator_brief(
        db_module=db,
        running=bool(_running),
        runtime_bot_count=len(_rms),
        active_runtime_count=sum(1 for rm in _rms.values() if not getattr(rm, "is_halted", False)) if _running else 0,
    )
    return JSONResponse({"ok": True, "brief": brief})


async def get_forcefield(req: Request):
    """Dedicated ForceField config endpoint — same data as /api/system/home forcefield key."""
    _check_token(req)
    from services.home_content import FORCEFIELD_SUMMARY
    from services.portfolio_forcefield import FLOOR_PCT, SWEEP_TRIGGER, get_status
    # Aggregate current system ForceField state across all running bots
    ff_phases = {bid: getattr(rm, "phase", "normal") for bid, rm in _rms.items()}
    portfolio_status = get_status(db)
    return JSONResponse({
        "config": FORCEFIELD_SUMMARY,
        "floor_pct": float(FLOOR_PCT),
        "withdraw_trigger_pct": float(SWEEP_TRIGGER),
        "bot_phases": ff_phases,
        "active_bot_count": len(_rms),
        "portfolio": portfolio_status.get("portfolio", {}),
        "milestones": portfolio_status.get("milestones", {}),
        "reservations": portfolio_status.get("reservations", {}),
        "recent_reservations": portfolio_status.get("recent_reservations", []),
        "recent_cash_movements": portfolio_status.get("recent_cash_movements", []),
        "action": portfolio_status.get("action", "CONTINUE"),
        "sweepable_cash": portfolio_status.get("sweepable_cash", 0),
    })


async def continue_forcefield(req: Request):
    _check_token(req)
    from services.portfolio_forcefield import continue_after_milestone, get_status

    result = continue_after_milestone(db)
    return JSONResponse({"ok": True, "result": result, "status": get_status(db)})


async def pause_forcefield(req: Request):
    _check_token(req)
    from services.portfolio_forcefield import get_status, set_manual_pause

    result = set_manual_pause(db, True)
    return JSONResponse({"ok": True, "result": result, "status": get_status(db)})


async def resume_forcefield(req: Request):
    _check_token(req)
    from services.portfolio_forcefield import get_status, set_manual_pause

    result = set_manual_pause(db, False)
    return JSONResponse({"ok": True, "result": result, "status": get_status(db)})


async def sweep_forcefield(req: Request):
    _check_token(req)
    if _vault is None:
        return JSONResponse({"ok": False, "error": "vault_unavailable"}, status_code=503)
    from services.portfolio_forcefield import get_status, maybe_auto_sweep

    result = maybe_auto_sweep(db, _vault)
    return JSONResponse({"ok": bool(result.get("ok", False)), "result": result, "status": get_status(db)})


async def get_open_positions(req: Request):
    """Return all open (filled, unsettled) positions from order_lifecycle."""
    _check_token(req)
    bot_id = req.query_params.get("bot_id")
    platform = req.query_params.get("platform")
    limit = int(req.query_params.get("limit", 100))
    positions = db.get_open_positions(bot_id=bot_id, platform=platform, limit=limit) if hasattr(db, "get_open_positions") else []
    count = db.get_open_position_count() if hasattr(db, "get_open_position_count") else len(positions)
    return JSONResponse({"ok": True, "positions": positions, "count": count})


async def settle_position(req: Request):
    """Mark an order as settled and release its ForceField headroom reservation."""
    _check_token(req)
    order_id = req.path_params.get("order_id", "")
    body = {}
    try:
        body = await req.json()
    except Exception:
        pass
    settlement_price = body.get("settlement_price") or body.get("price")
    pnl = body.get("pnl")

    # Settle the order in order_lifecycle
    settled = db.settle_order(order_id, settlement_price=settlement_price, pnl=pnl) if hasattr(db, "settle_order") else False

    # Release the ForceField headroom reservation
    reservation_result: dict = {"ok": False, "error": "no_open_reservation"}
    try:
        from services.portfolio_forcefield import settle_reservation_by_order_ref
        reservation_result = settle_reservation_by_order_ref(db, order_id, pnl=pnl, settlement_price=settlement_price)
    except Exception as exc:
        reservation_result = {"ok": False, "error": str(exc)}

    return JSONResponse({
        "ok": True,
        "order_id": order_id,
        "order_settled": settled,
        "reservation": reservation_result,
    })


async def get_system_executor(req: Request):
    _check_token(req)
    state = _build_executor_status()
    return JSONResponse({"executor": state, **state})


async def get_system_storage(req: Request):
    _check_token(req)
    return JSONResponse(_build_storage_status())


async def get_system_storage_integrity(req: Request):
    _check_token(req)
    payload = _build_storage_integrity()
    return JSONResponse(payload, status_code=200 if payload.get("ok") else 503)


async def get_system_storage_backups(req: Request, limit: int = 20):
    _check_token(req)
    return JSONResponse({"backups": db.list_database_backups(limit=limit)})


async def create_system_storage_backup(req: Request):
    _check_token(req)
    result = db.create_database_backup()
    removed = db.prune_database_backups(getattr(_cfg, "DB_BACKUP_RETENTION_COUNT", 7))
    storage_mode, _ = storage_mode_from_db_path(db.DB_PATH)
    return JSONResponse({
        "backup": result,
        "removed": removed,
        "storage_mode": storage_mode,
        "truth_note": "Backups on ephemeral storage will not survive redeploys.",
    })


async def verify_system_storage_backup(req: Request, basename: str):
    _check_token(req)
    result = db.verify_database_backup(basename)
    return JSONResponse(result, status_code=200 if result.get("ok") else 404)


async def get_system_startup(req: Request):
    _check_token(req)
    return JSONResponse(_build_startup_checks())


async def get_system_health(req: Request):
    _check_token(req)
    payload = _build_system_health()
    service_ok = bool((payload.get("db_connectivity") or {}).get("ok"))
    return JSONResponse(payload, status_code=200 if service_ok else 503)


async def get_system_health_deep(req: Request):
    _check_token(req)
    payload = _build_system_health_deep()
    service_ok = bool((payload.get("db_connectivity") or {}).get("ok"))
    return JSONResponse(payload, status_code=200 if service_ok else 503)


async def public_health():
    payload = public_health_payload()
    return JSONResponse(payload, status_code=200 if payload.get("ok") else 503)


async def public_health_deep():
    payload = public_health_deep_payload()
    return JSONResponse(payload, status_code=200 if payload.get("ok") else 503)


def _prometheus_metrics_text() -> str:
    """
    Build a minimal Prometheus-compatible text exposition.
    No external library required — hand-rolled to avoid adding dependencies.
    """
    lines: list[str] = []

    def gauge(name: str, value: float, labels: dict | None = None) -> None:
        label_str = ""
        if labels:
            label_str = "{" + ",".join(f'{k}="{v}"' for k, v in labels.items()) + "}"
        lines.append(f"# TYPE {name} gauge")
        lines.append(f"{name}{label_str} {value}")

    running_val = 1.0 if _running else 0.0
    gauge("degens_runtime_running", running_val)
    gauge("degens_active_bots_total", float(sum(1 for rm in _rms.values() if not rm.is_halted) if _running else 0))
    gauge("degens_total_bankroll", round(sum(rm.current_bankroll for rm in _rms.values()), 4) if _rms else 0.0)
    gauge("degens_total_vault", round(sum(getattr(rm, "vault", 0) for rm in _rms.values()), 4) if _rms else 0.0)
    gauge("degens_uptime_seconds", round(time.time() - _start_wall, 1) if _start_wall else 0.0)

    try:
        _trades, trade_count = db.get_trades(limit=1)
    except Exception:
        trade_count = 0
    gauge("degens_trades_total", float(trade_count))

    for spec in _active_bot_specs():
        bid = spec["id"]
        rm = _rms.get(bid)
        if not rm:
            continue
        paused_val = 1.0 if _bot_is_paused(bid) else 0.0
        gauge("degens_bot_paused", paused_val, {"bot_id": bid})
        gauge("degens_bot_bankroll", round(rm.current_bankroll, 4), {"bot_id": bid})
        gauge("degens_bot_drawdown_pct", round(min(0, (rm.current_bankroll - rm.peak_bankroll) / max(1e-9, rm.peak_bankroll) * 100), 2), {"bot_id": bid})
        gauge("degens_bot_phase", float(["floor", "ultra_safe", "safe", "careful", "normal", "aggressive", "turbo", "milestone"].index(rm.phase) if rm.phase in ["floor", "ultra_safe", "safe", "careful", "normal", "aggressive", "turbo", "milestone"] else -1), {"bot_id": bid, "phase": rm.phase})

    lines.append("")
    return "\n".join(lines)


async def get_metrics(req: Request):
    """Prometheus-compatible metrics endpoint — requires bearer token or X-Metrics-Key header."""
    from fastapi.responses import PlainTextResponse
    metrics_key = os.getenv("METRICS_API_KEY", "")
    provided = req.headers.get("X-Metrics-Key", "") or req.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if metrics_key and provided != metrics_key:
        try:
            _check_token(req)
        except Exception:
            raise HTTPException(401, "Unauthorized — set X-Metrics-Key or Bearer token")
    return PlainTextResponse(_prometheus_metrics_text(), media_type="text/plain; version=0.0.4; charset=utf-8")


async def get_system_security(req: Request):
    _check_token(req)
    return JSONResponse(_build_security_status())


async def get_system_runtime_history(req: Request, key: str | None = None, limit: int = 100):
    _check_token(req)
    return JSONResponse({"history": db.get_runtime_truth_history(key=key, limit=limit)})


async def list_platforms(req: Request):
    _check_token(req)
    system_truth, platforms = _build_system_truth()
    return JSONResponse({
        "platforms": platforms,
        "truth": system_truth,
    })


async def platform_health(req: Request, platform: str):
    _check_token(req)
    if platform in {"stake", "polymarket"}:
        rows = {row["platform"]: row for row in _legacy_platform_health()}
        return JSONResponse(rows.get(platform, {"ok": False, "error": "Unknown platform"}))
    _init_platform_services()
    try:
        return JSONResponse(_platform_health_service.health_for(platform))
    except KeyError:
        raise HTTPException(404, "Unknown platform")


async def platform_markets(req: Request, platform: str, limit: int = 25, sport: str = "upcoming",
                           regions: str = "us", markets: str = "h2h"):
    _check_token(req)
    _init_platform_services()
    try:
        adapter = _market_registry.get(platform)
    except KeyError:
        raise HTTPException(404, "Unknown platform")
    if platform == "oddsapi":
        result = adapter.list_markets(limit=limit, sport=sport, regions=regions, markets=markets, oddsFormat="decimal")
    else:
        result = adapter.list_markets(limit=limit)
    if not result.get("ok"):
        db.save_adapter_error(platform, "list_markets", result.get("error") or result.get("degraded_reason", ""), result)
    return JSONResponse(result)


async def get_bots_catalog(req: Request):
    _check_token(req)
    _sync_expansion_catalog()
    catalog = list(_catalog_cache or build_catalog())
    registry = load_catalog_registry(catalog)
    return JSONResponse({
        "ok": True,
        "bots": registry["lab_bots"],
        "phase2_bots": registry["mall_bots"],
        "all_bots": registry["ordered_bots"],
        "lab_bots": registry["lab_bots"],
        "mall_bots": registry["mall_bots"],
        "counts": registry["counts"],
        "tiers": registry["tiers"],
        "runtime_modes": registry["runtime_modes"],
        "registry_version": registry["registry_version"],
        "legacy_bots": _load_bot_registry(),
    })


async def get_bot_mode(req: Request, bot_id: str):
    _check_token(req)
    mode_row = db.get_bot_mode(bot_id)
    if mode_row:
        return JSONResponse(mode_row)
    if bot_id in BOT_SPEC_MAP:
        runtime = _poly_runtime.get(bot_id, {}) if BOT_SPEC_MAP[bot_id]["platform"] == "poly" else {}
        return JSONResponse({"bot_id": bot_id, "mode": _bot_truth_labels(bot_id, BOT_SPEC_MAP[bot_id], runtime)["execution_mode"]})
    raise HTTPException(404, "Unknown bot")


async def get_bot_truth(req: Request, bot_id: str):
    _check_token(req)
    if bot_id in BOT_SPEC_MAP:
        spec = BOT_SPEC_MAP[bot_id]
        runtime = _poly_runtime.get(bot_id, {}) if spec["platform"] == "poly" else {}
        return JSONResponse({
            "bot_id": bot_id,
            "truth": _bot_truth_labels(bot_id, spec, runtime),
            "runtime_health": _runtime_health.get(bot_id),
        })
    catalog = {row["bot_id"]: row for row in db.get_bot_catalog()}
    if bot_id in catalog:
        payload = catalog[bot_id].get("payload", {})
        return JSONResponse({
            "bot_id": bot_id,
            "truth": {
                "execution_mode": payload.get("mode", "RESEARCH"),
                "reconciliation_state": "off",
                "wallet_truth": "virtual_ledger",
                "data_truth_label": payload.get("mode", "RESEARCH"),
            },
            "catalog": payload,
        })
    raise HTTPException(404, "Unknown bot")


async def get_research_signals(req: Request, limit: int = 20, refresh: bool = False):
    _check_token(req)
    if refresh:
        for bot_id in (
            "bot_kalshi_orderbook_imbalance_paper",
            "bot_kalshi_resolution_decay_paper",
            "bot_kalshi_pair_spread_paper",
            "bot_oddsapi_consensus_outlier_paper",
        ):
            try:
                _run_expansion_bot(bot_id)
            except Exception as exc:
                db.save_adapter_error("research", bot_id, str(exc), {"bot_id": bot_id})
    return JSONResponse({"signals": db.get_research_signals(limit=limit)})


async def get_research_signals_for_bot(req: Request, bot_id: str, refresh: bool = False, limit: int = 20):
    _check_token(req)
    if refresh:
        try:
            result = _run_expansion_bot(bot_id)
            return JSONResponse({"signal": result, "signals": db.get_research_signals(bot_id=bot_id, limit=limit)})
        except KeyError:
            raise HTTPException(404, "Unknown bot")
        except Exception as exc:
            return JSONResponse({
                "signal": None,
                "signals": db.get_research_signals(bot_id=bot_id, limit=limit),
                "degraded_reason": str(exc),
            })
    return JSONResponse({"signals": db.get_research_signals(bot_id=bot_id, limit=limit)})


# ── Crossvenue watchlist ──────────────────────────────────────────────────────
async def crossvenue_watchlist(req: Request, verdict: str | None = None, limit: int = 50):
    """
    Return stored crossvenue match pairs from the database.
    Optionally filter by verdict: active_pair | watchlist_only | unmatched.
    Truth label: WATCHLIST ONLY — no PnL implied, all pairs require manual verification.
    """
    _check_token(req)
    matches = db.get_crossvenue_matches(verdict=verdict, limit=limit)
    active = [m for m in matches if m.get("verdict") == "active_pair"]
    watching = [m for m in matches if m.get("verdict") == "watchlist_only"]
    return JSONResponse({
        "total": len(matches),
        "active_pairs": len(active),
        "watchlist_pairs": len(watching),
        "matches": matches,
        "truth_label": "WATCHLIST ONLY — no realized PnL implied",
        "warning": (
            "All crossvenue pairs are for research and monitoring only. "
            "No profit/loss figures are associated with this watchlist. "
            "Active pairs still require manual confirmation before any comparison."
        ),
    })


async def get_human_relay(req: Request, status: str | None = "pending", limit: int = 25):
    _check_token(req)
    from services.human_relay import list_challenges

    rows = list_challenges(status=status, limit=limit, db_module=db)
    return JSONResponse({
        "requests": rows,
        "status": status or "all",
        "count": len(rows),
    })


async def get_human_relay_detail(req: Request, challenge_id: str):
    _check_token(req)
    from services.human_relay import get_challenge

    row = get_challenge(challenge_id, db_module=db)
    if not row:
        raise HTTPException(404, "Unknown human relay challenge")
    return JSONResponse(row)


async def open_human_relay(req: Request, body: dict = Body(default={})):
    _check_token(req)
    from services.human_relay import open_challenge

    result = open_challenge(
        bot_id=str(body.get("bot_id", "") or ""),
        platform=str(body.get("platform", "") or ""),
        prompt=str(body.get("prompt", "Human verification required.") or "Human verification required."),
        description=str(body.get("description", "") or ""),
        screenshot_path=str(body.get("screenshot_path", "") or ""),
        challenge_type=str(body.get("challenge_type", "human_check") or "human_check"),
        timeout_s=int(body.get("timeout_s", 300) or 300),
        chat_id=str(body.get("chat_id", "") or ""),
        bot_token=str(body.get("bot_token", "") or ""),
        payload=body.get("payload") if isinstance(body.get("payload"), dict) else {},
        db_module=db,
    )
    return JSONResponse(result)


async def respond_human_relay(req: Request, challenge_id: str, body: dict = Body(default={})):
    _check_token(req)
    from services.human_relay import respond_to_challenge

    result = respond_to_challenge(
        challenge_id,
        str(body.get("decision", "") or ""),
        source=str(body.get("source", "api") or "api"),
        payload=body.get("payload") if isinstance(body.get("payload"), dict) else {},
        db_module=db,
    )
    if not result.get("ok"):
        if result.get("error") == "not_found":
            raise HTTPException(404, "Unknown human relay challenge")
        raise HTTPException(400, result.get("error", "Invalid human relay response"))
    return JSONResponse(result)


# ── System quota status ───────────────────────────────────────────────────────
async def system_quota(req: Request):
    """
    Return real-time quota/rate-limit status for all quota-managed platforms.
    Also returns recent quota history from the database.
    """
    _check_token(req)
    budget = quota_budgeter.get_budget()
    live_status = budget.status_all()

    # Persist a snapshot for each platform that has been used
    for platform, status in live_status.items():
        if status.get("daily_used", 0) > 0 or status.get("minute_used", 0) > 0:
            try:
                db.save_quota_snapshot(platform, status)
            except Exception:
                pass

    history = db.get_quota_history(limit=24)
    return JSONResponse({
        "live": live_status,
        "history": history,
        "truth_note": "Quota counters are primarily in-process and are periodically snapshotted to SQLite for same-day recovery after restart.",
    })


# ── Kalshi live order lifecycle endpoints ─────────────────────────────────────
async def kalshi_live_health(req: Request):
    """Kalshi live adapter health check."""
    _check_token(req)
    adapter = _get_kalshi_live_adapter(enable_execution=False)
    result = adapter.healthcheck()
    return JSONResponse(result, status_code=200 if result.get("ok") else 503)


async def kalshi_live_markets(req: Request, limit: int = 25):
    """List Kalshi live markets."""
    _check_token(req)
    adapter = _get_kalshi_live_adapter(enable_execution=False)
    result = adapter.list_markets(limit=limit)
    return JSONResponse(result, status_code=200 if result.get("ok") else 409)


async def kalshi_live_balance(req: Request):
    """Kalshi live portfolio balance."""
    _check_token(req)
    adapter = _get_kalshi_live_adapter(enable_execution=False)
    result = adapter.get_balance()
    if result.get("ok"):
        balance_value = _extract_balance_value((result.get("data") or {}).get("balance"))
        if balance_value is not None:
            db.save_venue_balance(
                "kalshi_live",
                balance_value,
                currency="USD",
                balance_type="live_unreconciled",
                payload=result,
            )
    return JSONResponse(result, status_code=200 if result.get("ok") else 409)


async def kalshi_live_positions(req: Request):
    """Kalshi live portfolio positions."""
    _check_token(req)
    adapter = _get_kalshi_live_adapter(enable_execution=False)
    result = adapter.get_positions()
    return JSONResponse(result, status_code=200 if result.get("ok") else 409)


async def kalshi_live_place_order(req: Request):
    """
    Place a Kalshi live order.
    Requires: ENABLE_KALSHI=true, KALSHI_LIVE_EXECUTION=true, valid credentials.
    Body: { ticker, side, amount_usd, price_cents, order_type? }
    """
    _check_token(req)
    body = await req.json()
    required = ["ticker", "side", "amount_usd", "price_cents"]
    missing = [k for k in required if k not in body]
    if missing:
        return JSONResponse({"error": f"Missing fields: {missing}"}, status_code=400)
    adapter = _get_kalshi_live_adapter(enable_execution=bool(body.get("enable_live_execution", False)))
    result = adapter.place_order(
        ticker=body["ticker"],
        side=body["side"],
        amount_usd=float(body["amount_usd"]),
        price_cents=int(body["price_cents"]),
        order_type=body.get("order_type", "limit"),
    )
    if result.get("ok"):
        _persist_kalshi_live_order_snapshot(body, result)
        return JSONResponse(result)
    return JSONResponse(result, status_code=409 if result.get("status") in {"disabled", "blocked", "not_configured"} else 503)


async def kalshi_live_cancel_order(order_id: str, req: Request):
    """Cancel a Kalshi live order."""
    _check_token(req)
    adapter = _get_kalshi_live_adapter(enable_execution=False)
    result = adapter.cancel_order(order_id)
    if result.get("ok"):
        _persist_kalshi_live_order_snapshot(
            {"bot_id": "manual", "ticker": "", "side": "", "amount_usd": 0, "price_cents": 0},
            {"status": "cancelled", "data": {"order": {"order_id": order_id}}, **result},
            status_override="cancelled",
        )
    return JSONResponse(result, status_code=200 if result.get("ok") else 409)


async def kalshi_live_get_order(order_id: str, req: Request):
    """Fetch a Kalshi live order for reconciliation."""
    _check_token(req)
    adapter = _get_kalshi_live_adapter(enable_execution=False)
    result = adapter.get_order(order_id)
    if result.get("ok"):
        order = (result.get("data") or {}).get("order") or {}
        if order.get("status") in {"filled", "settled"}:
            _persist_kalshi_live_order_snapshot(
                {
                    "bot_id": "manual",
                    "ticker": order.get("ticker", ""),
                    "side": order.get("side", ""),
                    "amount_usd": order.get("count", 0),
                    "price_cents": order.get("yes_price") or order.get("no_price") or 0,
                },
                result,
                status_override=str(order.get("status")).lower(),
            )
    return JSONResponse(result, status_code=200 if result.get("ok") else 409)


# ── Execution authority endpoints ────────────────────────────────────────────
async def get_executor_status(req: Request):
    """Return current execution owner info + stale detection."""
    _check_token(req)
    return JSONResponse(_build_executor_status())


async def force_executor_takeover(req: Request, body: dict = Body(default={})):
    """Force-claim the execution lock when the current owner is stale."""
    _check_token(req)
    from services import execution_authority as ea

    _require_reauth_password(
        req,
        str(body.get("reauth_password", "") or ""),
        reason="forcing executor takeover",
    )
    if not ea.is_stale(db):
        return JSONResponse({"ok": False, "reason": "Current owner is not stale — takeover refused"}, status_code=409)
    success, row = ea.force_takeover(db, _executor_owner, reason="api_takeover")
    return JSONResponse({"ok": success, "row": row, "executor": _build_executor_status()})


async def get_executor_history(req: Request):
    """Return recent execution ownership events."""
    _check_token(req)
    from services import execution_authority as ea
    history = ea.get_history(db, limit=30)
    return JSONResponse({"history": history})


# ── Credential validation endpoints ──────────────────────────────────────────
async def get_credentials_status(req: Request):
    """Return credential status for all platforms (no raw keys exposed)."""
    _check_token(req)
    refresh = _boolish(req.query_params.get("refresh", ""), default=False)
    payload = _build_credentials_status(refresh=refresh, use_cached=not refresh)
    if refresh:
        try:
            from services.notification_center import notify_credential_validation_result

            notify_credential_validation_result(payload, source="operator_refresh", db_module=db)
        except Exception as exc:
            logger.warning("Credential validation notification failed: %s", exc)
    return JSONResponse(payload)


async def get_platform_credential_status(platform: str, req: Request):
    """Return credential status for a specific platform."""
    _check_token(req)
    from services.credential_validator import validate_platform
    refresh = _boolish(req.query_params.get("refresh", ""), default=False)
    result = validate_platform(
        platform,
        db_module=db,
        settings_getter=_get_runtime_setting,
        perform_network_check=refresh,
        use_cached=not refresh,
    )
    result = _enrich_credential_results({platform: result}).get(platform, result)
    return JSONResponse(result)


async def get_live_control(req: Request):
    _check_token(req)
    credentials = _build_credentials_status(refresh=False, use_cached=True)
    live_control = credentials.get("live_control", {})
    target_platforms = [
        platform
        for platform, row in (live_control.get("platforms", {}) or {}).items()
        if bool((row or {}).get("enabled"))
    ]
    return JSONResponse(
        {
            "ok": True,
            "live_control": live_control,
            "go_live_readiness": _build_go_live_readiness(target_platforms=target_platforms, refresh_credentials=False),
        }
    )


async def update_live_control(req: Request, body: dict = Body(default={})):
    _check_token(req)
    if _running:
        raise HTTPException(409, "Stop the runtime before changing live execution controls.")

    current = _live_controls_snapshot()
    desired = {
        "live_execution_enabled": bool(current["global"]["enabled"]),
        "stake_live_enabled": bool((current["platforms"].get("stake") or {}).get("enabled")),
        "polymarket_live_enabled": bool((current["platforms"].get("polymarket") or {}).get("enabled")),
    }
    for key in tuple(desired.keys()):
        if key in body:
            desired[key] = _boolish(body.get(key), default=desired[key])

    enabling = any(
        desired[key] and not current_value
        for key, current_value in (
            ("live_execution_enabled", bool(current["global"]["enabled"])),
            ("stake_live_enabled", bool((current["platforms"].get("stake") or {}).get("enabled"))),
            ("polymarket_live_enabled", bool((current["platforms"].get("polymarket") or {}).get("enabled"))),
        )
    )
    changing = any(
        desired[key] != current_value
        for key, current_value in (
            ("live_execution_enabled", bool(current["global"]["enabled"])),
            ("stake_live_enabled", bool((current["platforms"].get("stake") or {}).get("enabled"))),
            ("polymarket_live_enabled", bool((current["platforms"].get("polymarket") or {}).get("enabled"))),
        )
    )
    if changing:
        _require_reauth_password(
            req,
            str(body.get("reauth_password", "") or ""),
            reason="changing live execution controls",
        )
    if enabling and not _boolish(body.get("confirm_live"), default=False):
        raise HTTPException(400, {"error": "live_confirmation_required", "detail": "Set confirm_live=true to arm live execution."})

    target_platforms = [
        platform
        for platform, enabled in (
            ("stake", desired["stake_live_enabled"]),
            ("polymarket", desired["polymarket_live_enabled"]),
        )
        if desired["live_execution_enabled"] and enabled
    ]
    readiness = _build_go_live_readiness(target_platforms=target_platforms, refresh_credentials=False)
    if enabling and readiness.get("status") != "ready_for_canary":
        raise HTTPException(
            409,
            {
                "error": "go_live_blocked",
                "detail": readiness.get("message"),
                "readiness": readiness,
            },
        )
    if enabling and not _boolish(body.get("confirm_canary"), default=False):
        raise HTTPException(
            400,
            {
                "error": "canary_confirmation_required",
                "detail": "Live arming is restricted to a supervised canary first. Set confirm_canary=true and keep first live capital under the returned cap.",
                "readiness": readiness,
            },
        )

    validated: dict[str, dict] = {}
    if desired["live_execution_enabled"] and desired["stake_live_enabled"]:
        validated["stake"] = _validate_live_enablement("stake")
    if desired["live_execution_enabled"] and desired["polymarket_live_enabled"]:
        validated["polymarket"] = _validate_live_enablement("polymarket")

    changed_keys: set[str] = set()
    for key, value in desired.items():
        _save_setting_value(key, value, changed_keys)
    if changed_keys:
        _reload_runtime_after_settings_save(changed_keys)
    credentials = _build_credentials_status(refresh=False, use_cached=True)
    try:
        from services.notification_center import notify_live_control_change

        notify_live_control_change(desired, source="api", db_module=db)
    except Exception as exc:
        logger.warning("Live control notification failed: %s", exc)
    return JSONResponse(
        {
            "ok": True,
            "validated_platforms": validated,
            "live_control": credentials.get("live_control", {}),
            "go_live_readiness": _build_go_live_readiness(target_platforms=target_platforms, refresh_credentials=False),
        }
    )


# ── Reconciliation endpoints ──────────────────────────────────────────────────
async def get_reconciliation(req: Request):
    """Run reconciliation for all platforms and return report."""
    _check_token(req)
    return JSONResponse(_build_reconciliation_status())


async def get_reconciliation_history(req: Request, platform: str | None = None, limit: int = 50):
    """Return past reconciliation snapshots."""
    _check_token(req)
    history = db.get_reconciliation_history(platform=platform, limit=limit)
    return JSONResponse({"history": history})


# ── Scheduler endpoints ───────────────────────────────────────────────────────
async def get_scheduler_status(req: Request):
    """Return scheduler job status."""
    _check_token(req)
    from services.scheduler import get_scheduler
    return JSONResponse(get_scheduler().status())


async def trigger_scheduler_job(job_name: str, req: Request):
    """Manually trigger a scheduler job by name."""
    _check_token(req)
    from services.scheduler import get_scheduler
    ok = get_scheduler().trigger_now(job_name)
    if not ok:
        return JSONResponse({"ok": False, "reason": f"Job '{job_name}' not found"}, status_code=404)
    return JSONResponse({"ok": True, "job": job_name})


def _build_launch_checklist_payload() -> dict[str, Any]:
    checks: list[dict] = []

    def _check(name: str, status: str, detail: str, category: str = "system"):
        checks.append({"name": name, "status": status, "detail": detail, "category": category})

    # ── Credentials ──────────────────────────────────────────────────────────
    try:
        creds = _build_credentials_status(refresh=False, use_cached=True)
        raw_platforms = creds.get("platforms", {})
        # platforms may be a list (from list(enriched.values())) or a dict
        if isinstance(raw_platforms, list):
            all_platforms_list = raw_platforms
        else:
            all_platforms_list = list(raw_platforms.values())
        any_live = any(bool((v or {}).get("live_enabled")) for v in all_platforms_list)
        loaded = [(v or {}).get("platform", v.get("name", "?")) for v in all_platforms_list if (v or {}).get("status") == "loaded"]
        if loaded:
            _check("Credentials", "pass", f"{len(loaded)} platform(s) loaded: {', '.join(loaded)}", "credentials")
        else:
            _check("Credentials", "warn", "No platform credentials loaded — paper/research mode only", "credentials")
        if any_live:
            _check("Live execution flag", "warn", "At least one platform has live execution enabled — confirm intentional", "credentials")
        else:
            _check("Live execution flag", "pass", "Live execution disabled on all platforms (safe default)", "credentials")
    except Exception as exc:
        _check("Credentials", "fail", f"Could not read credentials: {exc}", "credentials")

    # ── Database ──────────────────────────────────────────────────────────────
    try:
        health = _build_system_health()
        db_ok = bool((health.get("db_connectivity") or {}).get("ok"))
        _check("Database", "pass" if db_ok else "fail", "Connected" if db_ok else "DB connectivity failed", "infra")
    except Exception as exc:
        _check("Database", "fail", str(exc), "infra")

    # ── Scheduler ─────────────────────────────────────────────────────────────
    try:
        from services.scheduler import get_scheduler
        sched = get_scheduler().status()
        running_jobs = sched.get("running_jobs", 0)
        _check("Scheduler", "pass", f"{running_jobs} job(s) active", "infra")
    except Exception as exc:
        _check("Scheduler", "warn", f"Scheduler status unavailable: {exc}", "infra")

    # ── Proposal evidence ─────────────────────────────────────────────────────
    try:
        proposal_count = 0
        if hasattr(db, "get_proposal_log"):
            rows = db.get_proposal_log(limit=1)
            proposal_count = len(rows) if rows else 0
        if proposal_count > 0:
            _check("Proposal evidence", "pass", "At least one proposal exists in DB", "execution")
        else:
            _check("Proposal evidence", "warn", "No proposals found — bots have not generated proposals yet", "execution")
    except Exception as exc:
        _check("Proposal evidence", "warn", f"Could not check proposals: {exc}", "execution")

    # ── Execution evidence (order lifecycle) ──────────────────────────────────
    try:
        order_count = 0
        if hasattr(db, "get_order_lifecycle"):
            rows = db.get_order_lifecycle(limit=1)
            order_count = len(rows) if rows else 0
        if order_count > 0:
            _check("Execution evidence", "pass", "Order lifecycle records exist", "execution")
        else:
            _check("Execution evidence", "warn", "No order lifecycle records — execution not yet triggered", "execution")
    except Exception as exc:
        _check("Execution evidence", "warn", f"Could not check orders: {exc}", "execution")

    # ── Reconciliation ────────────────────────────────────────────────────────
    try:
        rec_count = 0
        if hasattr(db, "get_reconciliation_events"):
            events = db.get_reconciliation_events(limit=1)
            rec_count = len(events) if events else 0
        if rec_count > 0:
            _check("Reconciliation", "pass", "Reconciliation events present", "audit")
        else:
            _check("Reconciliation", "warn", "No reconciliation events yet", "audit")
    except Exception as exc:
        _check("Reconciliation", "warn", f"Could not check reconciliation: {exc}", "audit")

    # ── Notification system ───────────────────────────────────────────────────
    try:
        notif_cfg = db.get_setting("telegram_chat_id", "") if hasattr(db, "get_setting") else ""
        if notif_cfg:
            _check("Notifications", "pass", "Telegram chat ID configured", "notifications")
        else:
            _check("Notifications", "warn", "Telegram chat ID not set — notifications silenced", "notifications")
    except Exception as exc:
        _check("Notifications", "warn", f"Could not check notification config: {exc}", "notifications")

    # ── Mall queue ────────────────────────────────────────────────────────────
    try:
        mall_items = db.get_mall_pipeline(limit=1) if hasattr(db, "get_mall_pipeline") else []
        has_mall = len(mall_items) > 0
        _check("Mall queue", "pass" if has_mall else "warn",
               "Mall queue has items" if has_mall else "Mall queue is empty — no leads discovered yet", "mall")
    except Exception as exc:
        _check("Mall queue", "warn", f"Could not check Mall queue: {exc}", "mall")

    # ── Deployment mode ───────────────────────────────────────────────────────
    import os
    is_render = bool(os.environ.get("RENDER"))
    env_name = os.environ.get("DEGEN_ENV", "development")
    _check("Deployment mode", "pass", f"env={env_name}, render={is_render}", "infra")

    # ── Live-danger warnings ──────────────────────────────────────────────────
    live_enabled = db.get_setting("live_execution_enabled", "false") if hasattr(db, "get_setting") else "false"
    if str(live_enabled).lower() in ("true", "1", "yes"):
        _check("LIVE EXECUTION", "warn",
               "Live execution is globally enabled. Ensure all safety gates have been verified before deploying.", "danger")
    else:
        _check("LIVE EXECUTION", "pass", "Live execution globally disabled", "danger")

    status_counts = {"pass": 0, "warn": 0, "fail": 0}
    for c in checks:
        status_counts[c["status"]] = status_counts.get(c["status"], 0) + 1

    ready = status_counts.get("fail", 0) == 0
    return {
        "ok": True,
        "ready": ready,
        "summary": status_counts,
        "checks": checks,
    }


def _build_go_live_readiness(
    *,
    target_platforms: list[str] | None = None,
    refresh_credentials: bool = False,
) -> dict[str, Any]:
    from services.portfolio_forcefield import get_status as get_forcefield_status

    requested_platforms = sorted({str(p) for p in (target_platforms or []) if p})
    launch = _build_launch_checklist_payload()
    credentials = _build_credentials_status(refresh=refresh_credentials, use_cached=not refresh_credentials)
    executor = _build_executor_status()
    forcefield = get_forcefield_status(db)
    startup = _build_startup_checks()
    scheduler_status = {"running_jobs": 0}
    try:
        from services.scheduler import get_scheduler

        scheduler_status = get_scheduler().status()
    except Exception:
        pass

    credential_rows = {
        str(row.get("platform", "")).lower(): row
        for row in (credentials.get("platforms") or [])
        if row.get("platform")
    }
    selected_platforms = requested_platforms or [
        platform
        for platform, row in (credentials.get("live_control", {}).get("platforms", {}) or {}).items()
        if bool((row or {}).get("enabled"))
    ]

    evidence = {
        "proposal_count": next((1 for c in launch["checks"] if c["name"] == "Proposal evidence" and c["status"] == "pass"), 0),
        "execution_count": next((1 for c in launch["checks"] if c["name"] == "Execution evidence" and c["status"] == "pass"), 0),
        "reconciliation_count": next((1 for c in launch["checks"] if c["name"] == "Reconciliation" and c["status"] == "pass"), 0),
        "scheduler_running_jobs": int(scheduler_status.get("running_jobs", 0) or 0),
        "forcefield_action": forcefield.get("action", "CONTINUE"),
        "manual_paused": bool((forcefield.get("portfolio") or {}).get("manual_paused")),
        "milestone_pending": bool((forcefield.get("milestones") or {}).get("milestone_pending")),
        "target_hit": bool((forcefield.get("milestones") or {}).get("target_hit")),
        "executor_owned_by_self": bool(executor.get("owned_by_self")),
        "executor_owner_present": bool(executor.get("owner_present")),
    }

    blockers: list[str] = []
    warnings: list[str] = []

    if not selected_platforms:
        blockers.append("No live-capable platform has been selected for arming.")

    if not startup.get("ready", False):
        blockers.append("Startup checks still contain critical findings.")

    if not evidence["executor_owner_present"] or not evidence["executor_owned_by_self"]:
        blockers.append("Execution authority is not owned by this runtime.")

    if evidence["manual_paused"] or evidence["milestone_pending"] or evidence["target_hit"] or evidence["forcefield_action"] != "CONTINUE":
        blockers.append("ForceField is not in CONTINUE state.")

    launch_by_name = {check["name"]: check for check in launch["checks"]}
    for required_name in ("Proposal evidence", "Execution evidence", "Reconciliation", "Notifications", "Database", "Scheduler"):
        check = launch_by_name.get(required_name)
        if not check:
            continue
        if check["status"] == "fail":
            blockers.append(check["detail"])
        elif check["status"] == "warn":
            blockers.append(check["detail"])

    for platform in selected_platforms:
        row = credential_rows.get(str(platform).lower()) or {}
        if not row:
            blockers.append(f"{platform}: credential status unavailable.")
            continue
        if not bool(row.get("credentials_present")):
            blockers.append(f"{platform}: credentials are missing.")
        elif row.get("state") == "invalid":
            blockers.append(f"{platform}: credentials are invalid.")
        if row.get("reason"):
            warnings.append(f"{platform}: {row['reason']}")

    hosted = bool(os.environ.get("RENDER") or os.environ.get("FLY_APP_NAME"))
    if blockers:
        proof_state = "hosted-staging" if hosted else "local-proof"
        status = "blocked"
    else:
        proof_state = "launchable"
        status = "ready_for_canary"

    return {
        "ok": True,
        "status": status,
        "proof_state": proof_state,
        "target_platforms": selected_platforms,
        "blockers": blockers,
        "warnings": warnings,
        "required_run_mode": "canary_only",
        "recommended_first_live_capital": 5.0 if status == "ready_for_canary" else 0.0,
        "hard_cap_first_live_capital": 20.0 if status == "ready_for_canary" else 0.0,
        "message": (
            "Live arming is blocked until credentials, notifications, proposal evidence, execution evidence, reconciliation, executor ownership, and ForceField continuity are all verified."
            if blockers
            else "Live arming is only approved for canary use. Keep the first supervised live session at $5 recommended / $20 absolute max."
        ),
        "launch_checklist": launch,
        "evidence": evidence,
    }


async def get_go_live_readiness(req: Request):
    _check_token(req)
    refresh = _boolish(req.query_params.get("refresh", ""), default=False)
    requested = req.query_params.get("platforms", "")
    target_platforms = [p.strip() for p in requested.split(",") if p.strip()]
    return JSONResponse(
        _build_go_live_readiness(
            target_platforms=target_platforms,
            refresh_credentials=refresh,
        )
    )


async def get_launch_checklist(req: Request):
    """
    Aggregate system readiness into a single operator launch checklist.
    Returns a structured list of check items with pass/warn/fail status.
    """
    _check_token(req)
    return JSONResponse(_build_launch_checklist_payload())


async def get_bot_scheduler_status(req: Request):
    _check_token(req)
    from services.scheduler import get_scheduler
    return JSONResponse(get_scheduler().status().get("bots", {}))


async def enable_bot_scheduler(req: Request, bot_id: str):
    _check_token(req)
    from services.scheduler import get_scheduler
    ok = get_scheduler().enable_bot(bot_id)
    if not ok:
        raise HTTPException(404, "Unknown bot")
    return JSONResponse({"ok": True, "bot_id": bot_id, "scheduler": get_scheduler().status().get("bots", {}).get(bot_id)})


async def disable_bot_scheduler(req: Request, bot_id: str):
    _check_token(req)
    from services.scheduler import get_scheduler
    ok = get_scheduler().disable_bot(bot_id)
    if not ok:
        raise HTTPException(404, "Unknown bot")
    return JSONResponse({"ok": True, "bot_id": bot_id, "scheduler": get_scheduler().status().get("bots", {}).get(bot_id)})


async def patch_bot_scheduler_interval(req: Request, bot_id: str, body: dict = Body(...)):
    _check_token(req)
    from services.scheduler import get_scheduler
    interval_seconds = int(body.get("interval_seconds", 60))
    ok = get_scheduler().set_interval(bot_id, interval_seconds)
    if not ok:
        raise HTTPException(404, "Unknown bot")
    return JSONResponse({"ok": True, "bot_id": bot_id, "scheduler": get_scheduler().status().get("bots", {}).get(bot_id)})


# ── Order lifecycle endpoints ─────────────────────────────────────────────────
async def get_orders(req: Request, bot_id: str | None = None, platform: str | None = None,
                     status: str | None = None, limit: int = 50):
    """Return order lifecycle records."""
    _check_token(req)
    orders = db.get_order_lifecycle(bot_id=bot_id, platform=platform, status=status, limit=limit)
    order_requests = db.get_order_requests(bot_id=bot_id, platform=platform, limit=limit)
    return JSONResponse({
        "orders": orders,
        "requests": order_requests,
        "truth_note": "All orders in PAPER/DEMO mode are simulated — no real money involved.",
    })


async def get_reconciliation_orders(req: Request, bot_id: str | None = None, platform: str | None = None, limit: int = 50):
    _check_token(req)
    requests = db.get_order_requests(bot_id=bot_id, platform=platform, limit=limit)
    legacy_orders = db.get_order_lifecycle(bot_id=bot_id, platform=platform, limit=limit)
    events = db.get_reconciliation_events(platform=platform, limit=limit * 2)
    return JSONResponse({
        "requests": requests,
        "legacy_orders": legacy_orders,
        "events": events,
        "truth_label": "RECONCILIATION VIEW",
        "truth_note": "Live-disabled, paper, delayed, trial, and research platforms do not imply externally settled profitability.",
    })


async def get_reconciliation_order_detail(req: Request, order_ref: str):
    _check_token(req)
    detail = db.get_order_request_detail(order_ref)
    if detail:
        return JSONResponse({
            "order": detail,
            "truth_label": "ORDER REQUEST DETAIL",
        })
    legacy = db.get_legacy_order_lifecycle_detail(order_ref)
    if legacy:
        return JSONResponse({
            "legacy_order": legacy,
            "reconciliation_events": db.get_reconciliation_events(order_ref=order_ref, limit=100),
            "truth_label": "LEGACY ORDER DETAIL",
        })
    raise HTTPException(404, "Unknown order reference")


# ── Phase transition log endpoint ─────────────────────────────────────────────
async def get_phase_transitions_log(req: Request, bot_id: str | None = None, limit: int = 100):
    """Return rich phase transition log with reason codes."""
    _check_token(req)
    rows = db.get_phase_transition_log(bot_id=bot_id, limit=limit)
    return JSONResponse({"phase_transitions": rows})


# ── Stake token health endpoint ───────────────────────────────────────────────
async def get_stake_health(req: Request):
    """Non-betting Stake token health check."""
    _check_token(req)
    from stake_client import token_health_check
    result = token_health_check()
    return JSONResponse({k: v for k, v in result.items() if k != "token"})


# ── Simulator endpoints ───────────────────────────────────────────────────────
async def simulator_status(req: Request):
    _check_token(req)
    runs = db.get_simulator_runs(limit=1)
    return JSONResponse({
        "state": "idle",
        "supported_modes": ["quick", "realistic", "continuation", "replay"],
        "last_run": runs[0] if runs else None,
    })


async def simulator_capabilities(req: Request):
    _check_token(req)
    runtime_states = db.get_bot_runtime_state(limit=200)
    replay_signals = db.get_research_signals(limit=500)
    continuation_sources = [
        {
            "bot_id": row.get("bot_id"),
            "platform": row.get("platform"),
            "phase": row.get("phase"),
            "bankroll": row.get("bankroll"),
            "ts": row.get("ts"),
        }
        for row in runtime_states
        if row.get("bot_id")
    ]
    modes = ["quick", "realistic", "continuation", "replay"]
    return JSONResponse({
        "supported_modes": modes,
        "modes": modes,
        "continuation_sources": continuation_sources,
        "replay_signal_count": len(replay_signals),
        "replay_available": len(replay_signals) > 0,
        "truth_label": "SIMULATED — NOT REAL",
        "truth_note": "Continuation and replay use local runtime and research history only; missing state degrades safely.",
    })


async def simulator_run(req: Request):
    """Run a conservative truth-labeled simulation."""
    _check_token(req)
    from services.simulator_engine import SimulatorConfig, run_simulation
    from services.simulator_report import report_from_result

    body = getattr(getattr(req, "state", None), "typed_body", None) or await req.json()
    cfg = SimulatorConfig.from_request(body)
    if cfg.bot_id and not cfg.initial_state:
        cfg.initial_state = _snapshot_bot_state(cfg.bot_id)
    result = run_simulation(cfg)
    report = report_from_result(result)
    summary = _persist_simulator_result(result, report)
    return JSONResponse({**summary, "report": report, "run_id": result.run_id})


async def simulator_continue(req: Request):
    _check_token(req)
    from services.simulator_engine import SimulatorConfig, run_simulation
    from services.simulator_report import report_from_result

    body = getattr(getattr(req, "state", None), "typed_body", None) or await req.json()
    source_run_id = body.get("run_id")
    bot_id = body.get("bot_id") or body.get("source_bot") or ""
    initial_state = {}
    strategy_id = body.get("strategy_id", body.get("strategy", "dice"))
    if source_run_id:
        source_run = db.get_simulator_run(source_run_id)
        if not source_run:
            raise HTTPException(404, "Unknown run_id")
        source_payload = source_run.get("payload", {})
        initial_state = source_payload.get("terminal_state", {})
        params = source_run.get("params", {})
        bot_id = bot_id or source_run.get("source_bot", "")
        strategy_id = params.get("strategy_id") or params.get("strategy") or strategy_id
    elif bot_id:
        initial_state = _snapshot_bot_state(bot_id)
    cfg = SimulatorConfig.from_request({
        **body,
        "mode": "continuation",
        "bot_id": bot_id,
        "strategy_id": strategy_id,
        "initial_state": initial_state,
    })
    result = run_simulation(cfg)
    report = report_from_result(result)
    summary = _persist_simulator_result(result, report)
    return JSONResponse({**summary, "report": report, "run_id": result.run_id})


async def simulator_compare(req: Request):
    """Run multiple simulations and return comparative report."""
    _check_token(req)
    from services.simulator_engine import SimulatorConfig, run_simulation
    from services.simulator_report import build_report

    body = getattr(getattr(req, "state", None), "typed_body", None) or await req.json()
    runs_cfg = body.get("runs", [])
    if not runs_cfg or len(runs_cfg) > 20:
        return JSONResponse({"error": "Provide 1–20 run configs in 'runs' array"}, status_code=400)
    results = []
    for rc in runs_cfg:
        cfg = SimulatorConfig.from_request(rc)
        if cfg.bot_id and not cfg.initial_state:
            cfg.initial_state = _snapshot_bot_state(cfg.bot_id)
        results.append(run_simulation(cfg))
    report = build_report(results, label=body.get("label", ""))
    return JSONResponse(report)


async def simulator_history(req: Request, limit: int = 50, offset: int = 0, mode: str | None = None, bot_id: str | None = None):
    """Return past simulation run summaries."""
    _check_token(req)
    runs = db.get_simulator_runs(limit=limit, offset=offset, mode=mode, bot_id=bot_id)
    return JSONResponse({"runs": runs, "truth_label": "SIMULATED — NOT REAL", "limit": limit, "offset": offset})


def _hydrate_simulator_report(report: dict | None) -> dict | None:
    if not report:
        return report
    payload = report.get("payload") or {}
    assumptions = report.get("assumptions") or {}
    normalized = dict(report)
    normalized["source_state"] = normalized.get("source_state") or payload.get("source_state") or assumptions.get("source_state", "unknown")
    normalized["data_provenance"] = normalized.get("data_provenance") or payload.get("data_provenance") or assumptions.get("data_provenance", "unknown")
    normalized["decision_usefulness"] = normalized.get("decision_usefulness") or payload.get("decision_usefulness") or assumptions.get("decision_usefulness", "exploratory")
    normalized["execution_assumptions"] = normalized.get("execution_assumptions") or payload.get("execution_assumptions") or assumptions.get("execution_assumptions", {})
    normalized["time_basis"] = normalized.get("time_basis") or payload.get("time_basis") or assumptions.get("time_basis", {})
    if "terminal_state" not in normalized and payload.get("terminal_state") is not None:
        normalized["terminal_state"] = payload.get("terminal_state")
    if "summary" not in normalized and payload:
        normalized["summary"] = payload.get("summary") or payload
    return normalized


async def simulator_run_detail(req: Request, run_id: str):
    _check_token(req)
    run = db.get_simulator_run(run_id)
    if not run:
        raise HTTPException(404, "Unknown run_id")
    report = _hydrate_simulator_report(db.get_simulator_report(run_id))
    steps = db.get_simulator_steps(run_id)
    return JSONResponse({"run": run, "report": report, "steps": steps})


async def get_bots_diagnostics(req: Request):
    """Return strategy diagnostics for all running bots."""
    _check_token(req)
    from services.strategy_diagnostics import get_all_diagnostics
    result = get_all_diagnostics(rms=_rms, db=db)
    return JSONResponse(result)


async def get_bot_diagnostic(bot_id: str, req: Request):
    """Return strategy diagnostic for a single bot."""
    _check_token(req)
    from services.strategy_diagnostics import get_bot_diagnostic
    rm = _rms.get(bot_id)
    diag = get_bot_diagnostic(rm=rm, db=db, bot_id=bot_id)
    return JSONResponse(diag)


async def get_strategy_diagnostics_route(req: Request, limit: int = 50):
    _check_token(req)
    from services.strategy_diagnostics import get_strategy_diagnostics
    return JSONResponse(get_strategy_diagnostics(db=db, limit=limit))


async def get_platform_diagnostics_route(req: Request):
    _check_token(req)
    from services.strategy_diagnostics import get_platform_diagnostics
    return JSONResponse(get_platform_diagnostics(db=db, registry=_market_registry))


async def get_platform_diagnostic_route(req: Request, platform: str):
    _check_token(req)
    from services.strategy_diagnostics import get_platform_diagnostic
    return JSONResponse(get_platform_diagnostic(platform, db=db, registry=_market_registry))


async def get_diagnostics_summary(req: Request):
    _check_token(req)
    bot_rows = db.get_bot_diagnostics(limit=100)
    strategy_rows = db.get_strategy_diagnostics(limit=100)
    failure_rows = db.get_failure_events(limit=50)
    platform_rows = db.get_platform_health(limit=50)
    top_blockers = []
    for row in strategy_rows[:20]:
        for blocker in row.get("blockers", [])[:3]:
            top_blockers.append({"strategy_id": row.get("strategy_id", row.get("bot_id", "")), "blocker": blocker})
    payload = {
        "bot_count": len(bot_rows),
        "strategy_count": len(strategy_rows),
        "platform_count": len(platform_rows),
        "recent_failure_count": len(failure_rows),
        "top_blockers": top_blockers[:15],
        "recent_failures": failure_rows[:10],
        "truth_label": "RESEARCH DIAGNOSTIC",
        "realized_pnl": None,
    }
    return JSONResponse(payload)


async def get_calibration_summary(req: Request, bot_id: str | None = None):
    _check_token(req)
    from services.calibration import summarize_all, summarize_bot

    if bot_id:
        return JSONResponse(summarize_bot(db, bot_id))
    return JSONResponse(summarize_all(db))


async def run_backtest_endpoint(req: Request, bot_id: str):
    _check_token(req)
    from services.backtest_engine import run_backtest

    body = getattr(getattr(req, "state", None), "typed_body", None) or await req.json()
    start_ts = body.get("start_ts")
    end_ts = body.get("end_ts")
    params = body.get("strategy_params") or body.get("params") or {}
    result = run_backtest(db, bot_id, date_range=(start_ts, end_ts), strategy_params=params)
    return JSONResponse(result)


async def list_backtests(req: Request, bot_id: str | None = None, limit: int = 25):
    _check_token(req)
    return JSONResponse({
        "runs": db.get_backtest_runs(bot_id=bot_id, limit=limit),
        "truth_label": "HISTORICAL REPLAY BACKTEST",
        "realized_pnl": None,
    })


async def get_simulator_diagnostic_route(req: Request, run_id: str):
    _check_token(req)
    run = db.get_simulator_run(run_id)
    if not run:
        raise HTTPException(404, "Unknown run_id")
    return JSONResponse({
        "run": run,
        "report": _hydrate_simulator_report(db.get_simulator_report(run_id)),
        "steps": db.get_simulator_steps(run_id),
    })


async def get_signal_analysis(req: Request, bot_id: str | None = None, limit: int = 100):
    """Analyse recent research signals."""
    _check_token(req)
    from services.strategy_diagnostics import analyze_signals
    result = analyze_signals(db=db, bot_id=bot_id, limit=limit)
    return JSONResponse(result)


async def get_cb_analysis(req: Request, bot_id: str | None = None):
    """Analyse circuit breaker history."""
    _check_token(req)
    from services.strategy_diagnostics import analyze_circuit_breakers
    result = analyze_circuit_breakers(db=db, bot_id=bot_id)
    return JSONResponse(result)


async def simulator_leaderboard(req: Request):
    """Return strategy leaderboard from saved simulation runs."""
    _check_token(req)
    from services.simulator_report import strategy_leaderboard_from_rows

    rows = db.get_simulator_runs(limit=200)
    board = strategy_leaderboard_from_rows(rows)
    return JSONResponse({"leaderboard": board, "truth_label": "SIMULATED — NOT REAL"})


# ── Public health check (no auth) ─────────────────────────────────────────────
async def ping():
    return {"ok": True, "ts": time.time()}

# ── Serve React frontend (must be last — catches all non-API routes) ──────────
_UI_DIST = os.path.join(os.path.dirname(__file__), "ui", "dist")
if os.path.isdir(_UI_DIST):
    # Assets are content-hashed (index-abc123.js) → safe to cache forever

    async def favicon():
        f = os.path.join(_UI_DIST, "favicon.svg")
        return FileResponse(f) if os.path.exists(f) else JSONResponse({}, status_code=404)

    # HTML is NEVER cached — browser always gets the freshest index.html
    # which points to the freshest content-hashed JS bundle
    async def spa_fallback(full_path: str):
        from fastapi.responses import Response
        with open(os.path.join(_UI_DIST, "index.html"), "rb") as f:
            content = f.read()
        return Response(content=content, media_type="text/html", headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma":        "no-cache",
            "Expires":       "0",
        })
