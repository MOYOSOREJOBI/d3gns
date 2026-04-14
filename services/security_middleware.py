"""
Security Middleware Service — hardened API protection layer.

Implements:
  1. Request rate limiting (per-IP, per-endpoint, per-token)
  2. Input sanitisation and injection prevention
  3. Suspicious pattern detection (SQL injection, XSS, path traversal)
  4. Secret redaction for API responses
  5. HMAC request signing for internal calls
  6. Auth token rotation utilities
  7. Audit log entry formatting

Use with FastAPI via middleware or dependency injection.
"""
from __future__ import annotations

import hashlib
import hmac
import html
import re
import secrets
import time
from collections import defaultdict
from typing import Any


# ── Suspicious input patterns ─────────────────────────────────────────────────
_SQL_INJECTION = re.compile(
    r"(?i)(\bunion\b.*\bselect\b|\bdrop\s+table\b|\bdelete\s+from\b|"
    r"\binsert\s+into\b|\bupdate\s+\w+\s+set\b|'.*--|\bor\b.*=.*|"
    r"\bexec\s*\(|\bxp_cmdshell\b)"
)
_XSS_PATTERN = re.compile(
    r"(?i)(<script[^>]*>|javascript:|on\w+\s*=|data:text/html|vbscript:)"
)
_PATH_TRAVERSAL = re.compile(r"\.\./|\.\.\\|%2e%2e|%252e%252e")
_COMMAND_INJECT = re.compile(r"[;&|`$]|\\n|%0a|%7c")

# Keys that should NEVER appear in API responses
_SECRET_KEY_PATTERNS = re.compile(
    r"(?i)(api_key|api_secret|passphrase|password|token|private_key|secret|"
    r"stake_api|poly_private|twilio|auth_token|encryption_key|hmac_secret)"
)

# ── Rate limiter ──────────────────────────────────────────────────────────────

class RateLimiter:
    """
    Sliding-window rate limiter. Thread-safe via GIL.
    Supports per-key limits with multiple windows.
    """

    def __init__(self) -> None:
        # {key: [(timestamp, count), ...]}
        self._windows: dict[str, list[tuple[float, int]]] = defaultdict(list)

    def check(
        self,
        key: str,
        *,
        limit: int,
        window_s: float = 60.0,
    ) -> dict[str, Any]:
        """
        Check if key is within rate limit.
        Returns {"allowed": bool, "count": int, "limit": int, "retry_after_s": float}
        """
        now    = time.time()
        cutoff = now - window_s
        entries = [(t, c) for t, c in self._windows[key] if t >= cutoff]
        count   = sum(c for _, c in entries)

        if count >= limit:
            oldest    = min(t for t, _ in entries) if entries else now
            retry_s   = round(window_s - (now - oldest), 1)
            return {"allowed": False, "count": count, "limit": limit,
                    "retry_after_s": max(retry_s, 0.0)}

        entries.append((now, 1))
        self._windows[key] = entries
        return {"allowed": True, "count": count + 1, "limit": limit, "retry_after_s": 0.0}

    def reset(self, key: str) -> None:
        self._windows.pop(key, None)

    def cleanup_stale(self, max_window_s: float = 3600.0) -> int:
        """Remove stale entries to prevent memory leak. Returns entries removed."""
        now = time.time()
        cutoff = now - max_window_s
        removed = 0
        to_delete = []
        for k, entries in self._windows.items():
            fresh = [(t, c) for t, c in entries if t >= cutoff]
            if not fresh:
                to_delete.append(k)
            else:
                removed += len(entries) - len(fresh)
                self._windows[k] = fresh
        for k in to_delete:
            del self._windows[k]
            removed += 1
        return removed


# ── Input sanitiser ───────────────────────────────────────────────────────────

class InputSanitiser:
    """
    Validates and sanitises user input before processing.
    """

    @staticmethod
    def is_safe(value: str) -> tuple[bool, str]:
        """
        Check if a string is safe (no injection patterns).
        Returns (safe: bool, threat_type: str).
        """
        if _SQL_INJECTION.search(value):
            return False, "sql_injection"
        if _XSS_PATTERN.search(value):
            return False, "xss"
        if _PATH_TRAVERSAL.search(value):
            return False, "path_traversal"
        if _COMMAND_INJECT.search(value):
            return False, "command_injection"
        return True, ""

    @staticmethod
    def sanitise_html(value: str) -> str:
        """HTML-escape a string for safe display."""
        return html.escape(value)

    @staticmethod
    def sanitise_numeric(value: Any, *, min_val: float | None = None, max_val: float | None = None) -> float | None:
        """Parse and bound-check a numeric value."""
        try:
            v = float(value)
            if min_val is not None and v < min_val:
                return min_val
            if max_val is not None and v > max_val:
                return max_val
            return v
        except (TypeError, ValueError):
            return None

    @staticmethod
    def sanitise_symbol(symbol: str) -> str:
        """Clean a trading symbol/ticker to alphanumeric + dash/underscore only."""
        return re.sub(r"[^A-Za-z0-9\-_/]", "", symbol)[:20].upper()

    @staticmethod
    def validate_dict(data: dict[str, Any], schema: dict[str, type]) -> tuple[bool, list[str]]:
        """Validate required fields and types. Returns (valid, [errors])."""
        errors = []
        for field, expected_type in schema.items():
            if field not in data:
                errors.append(f"missing_field:{field}")
                continue
            if not isinstance(data[field], expected_type):
                errors.append(f"wrong_type:{field}={type(data[field]).__name__}!={expected_type.__name__}")
        return len(errors) == 0, errors


# ── Secret redactor ───────────────────────────────────────────────────────────

def redact_secrets(obj: Any, depth: int = 0) -> Any:
    """
    Recursively redact sensitive values in dicts/lists before serialisation.
    Keys matching _SECRET_KEY_PATTERNS have their values replaced with '[REDACTED]'.
    """
    if depth > 8:
        return obj  # prevent infinite recursion
    if isinstance(obj, dict):
        return {
            k: "[REDACTED]" if _SECRET_KEY_PATTERNS.search(str(k)) else redact_secrets(v, depth + 1)
            for k, v in obj.items()
        }
    if isinstance(obj, (list, tuple)):
        return [redact_secrets(item, depth + 1) for item in obj]
    return obj


def redact_env_vars(env_dict: dict[str, str]) -> dict[str, str]:
    """Redact secret env var values, show only that they're set."""
    return {
        k: "[SET]" if _SECRET_KEY_PATTERNS.search(k) and v else v
        for k, v in env_dict.items()
    }


# ── HMAC signing ─────────────────────────────────────────────────────────────

class HMACHelper:
    """
    HMAC-SHA256 request signing for internal service-to-service calls.
    """

    def __init__(self, secret: str) -> None:
        self._secret = secret.encode()

    def sign(self, payload: str, timestamp: float | None = None) -> dict[str, str]:
        ts  = str(int(timestamp or time.time()))
        msg = f"{ts}.{payload}".encode()
        sig = hmac.new(self._secret, msg, hashlib.sha256).hexdigest()
        return {"X-Timestamp": ts, "X-Signature": sig}

    def verify(self, payload: str, timestamp: str, signature: str, *, max_age_s: float = 300.0) -> bool:
        """Verify HMAC signature and timestamp freshness."""
        try:
            ts_int = int(timestamp)
        except ValueError:
            return False
        if abs(time.time() - ts_int) > max_age_s:
            return False
        msg      = f"{timestamp}.{payload}".encode()
        expected = hmac.new(self._secret, msg, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)


# ── Token manager ─────────────────────────────────────────────────────────────

class TokenManager:
    """
    Secure token generation, validation, and rotation.
    """

    def __init__(self, ttl_seconds: float = 86400.0) -> None:
        self._tokens: dict[str, dict[str, Any]] = {}
        self._ttl = ttl_seconds

    def create(self, metadata: dict[str, Any] | None = None) -> str:
        token = secrets.token_urlsafe(32)
        self._tokens[token] = {
            "created_at": time.time(),
            "last_used":  time.time(),
            "metadata":   metadata or {},
        }
        return token

    def validate(self, token: str, *, idle_ttl_s: float | None = None) -> bool:
        entry = self._tokens.get(token)
        if not entry:
            return False
        age    = time.time() - entry["created_at"]
        idle   = time.time() - entry["last_used"]
        if age > self._ttl:
            del self._tokens[token]
            return False
        if idle_ttl_s and idle > idle_ttl_s:
            del self._tokens[token]
            return False
        entry["last_used"] = time.time()
        return True

    def revoke(self, token: str) -> bool:
        return self._tokens.pop(token, None) is not None

    def cleanup_expired(self) -> int:
        now     = time.time()
        expired = [t for t, e in self._tokens.items() if now - e["created_at"] > self._ttl]
        for t in expired:
            del self._tokens[t]
        return len(expired)

    @property
    def active_count(self) -> int:
        return len(self._tokens)


# ── Audit logger ─────────────────────────────────────────────────────────────

def audit_event(
    event: str,
    *,
    ip: str = "unknown",
    user: str = "unknown",
    resource: str = "",
    success: bool = True,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Format an audit log entry.
    Call this before writing to your audit log table.
    """
    return {
        "ts":       time.time(),
        "event":    event,
        "ip":       ip,
        "user":     user,
        "resource": resource,
        "success":  success,
        **(extra or {}),
    }


# ── FastAPI middleware ────────────────────────────────────────────────────────

def get_security_headers() -> dict[str, str]:
    """Return strict security headers for all API responses."""
    return {
        "X-Content-Type-Options":       "nosniff",
        "X-Frame-Options":              "DENY",
        "X-XSS-Protection":             "1; mode=block",
        "Strict-Transport-Security":    "max-age=31536000; includeSubDomains",
        "Content-Security-Policy":      "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'",
        "Referrer-Policy":              "strict-origin-when-cross-origin",
        "Permissions-Policy":           "geolocation=(), microphone=(), camera=()",
        "Cache-Control":                "no-store",
        "Pragma":                       "no-cache",
    }


# ── Global singletons ─────────────────────────────────────────────────────────
_rate_limiter: RateLimiter | None = None
_sanitiser = InputSanitiser()


def get_rate_limiter() -> RateLimiter:
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = RateLimiter()
    return _rate_limiter


def get_sanitiser() -> InputSanitiser:
    return _sanitiser
