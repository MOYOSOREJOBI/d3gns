from __future__ import annotations

import os
import time
from typing import Any


def _prefer_env_first() -> bool:
    return bool(os.getenv("FLY_APP_NAME"))


def _redact_secret(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    if len(value) <= 4:
        return "*" * len(value)
    return f"{'*' * max(4, len(value) - 4)}{value[-4:]}"


def _result(
    platform: str,
    *,
    state: str,
    auth_truth: str,
    configured: bool,
    credentials_present: bool | None = None,
    credentials_valid: bool = False,
    validation_performed: bool = False,
    live_capable: bool = False,
    readiness: str = "ready_for_paper_only",
    display_state: str = "",
    reason: str = "",
    failure_type: str = "",
    redacted_hint: str = "",
    mode: str = "UNKNOWN",
    payload: dict | None = None,
) -> dict[str, Any]:
    present = configured if credentials_present is None else bool(credentials_present)
    if not display_state:
        if state in {"missing", "not_configured"}:
            display_state = "missing"
        elif validation_performed and not credentials_valid:
            display_state = "validation_failed"
        elif live_capable:
            display_state = "ready_for_live"
        elif present:
            display_state = "loaded"
        else:
            display_state = "ready_for_paper_only"
    return {
        "platform": platform,
        "state": state,
        "status": state,
        "auth_truth": auth_truth,
        "configured": configured,
        "credentials_present": present,
        "credentials_valid": bool(credentials_valid),
        "validation_performed": bool(validation_performed),
        "live_capable": bool(live_capable),
        "readiness": readiness,
        "display_state": display_state,
        "failure_type": failure_type,
        "redacted_hint": redacted_hint,
        "reason": reason,
        "mode": mode,
        "payload": payload or {},
    }


def _persist(db_module: Any, result: dict[str, Any]) -> dict[str, Any]:
    if db_module and hasattr(db_module, "upsert_credential_health"):
        db_module.upsert_credential_health(
            platform=result["platform"],
            state=result["state"],
            failure_type=result.get("failure_type", ""),
            redacted_hint=result.get("redacted_hint", ""),
            payload={k: v for k, v in result.items() if k not in {"platform", "state", "failure_type", "redacted_hint"}},
            valid=result["state"] == "valid",
        )
    if db_module and hasattr(db_module, "save_auth_health_event"):
        db_module.save_auth_health_event(
            result["platform"],
            "validated" if result["state"] == "valid" else "failed",
            result.get("failure_type", ""),
            result,
        )
    return result


def _read_setting(
    name: str,
    *,
    db_module: Any = None,
    settings_getter: Any = None,
    default: str = "",
) -> str:
    if settings_getter:
        try:
            value = settings_getter(name, default)
        except TypeError:
            value = settings_getter(name)
        if value not in (None, ""):
            return str(value)

    def _env_value() -> str:
        for key in (name, name.upper(), name.lower()):
            value = os.getenv(key)
            if value not in (None, ""):
                return str(value)
        return ""

    def _db_value() -> str:
        if db_module and hasattr(db_module, "get_setting"):
            for key in (name.lower(), name):
                value = db_module.get_setting(key, "")
                if value not in (None, ""):
                    return str(value)
        return ""

    if _prefer_env_first():
        return _env_value() or _db_value() or default
    return _db_value() or _env_value() or default


def _bool_setting(
    name: str,
    *,
    db_module: Any = None,
    settings_getter: Any = None,
    default: bool = False,
) -> bool:
    truthy = {"1", "true", "yes", "on"}
    falsy = {"0", "false", "no", "off", ""}
    if settings_getter:
        try:
            raw = str(settings_getter(name, "") or "").strip().lower()
        except TypeError:
            raw = str(settings_getter(name) or "").strip().lower()
        if raw in truthy:
            return True
        if raw not in falsy:
            return bool(default)

    def _env_values() -> list[str]:
        return [
            str(os.getenv(name, "") or "").strip().lower(),
            str(os.getenv(name.upper(), "") or "").strip().lower(),
            str(os.getenv(name.lower(), "") or "").strip().lower(),
        ]

    def _db_values() -> list[str]:
        if not db_module or not hasattr(db_module, "get_setting"):
            return []
        return [
            str(db_module.get_setting(name.lower(), "") or "").strip().lower(),
            str(db_module.get_setting(name, "") or "").strip().lower(),
        ]

    env_values = _env_values()
    db_values = _db_values()
    ordered = env_values + db_values if _prefer_env_first() else db_values + env_values
    if any(value in truthy for value in ordered):
        return True
    if any(value not in falsy for value in ordered):
        return bool(default)
    return False

def _cached_result(platform: str, *, db_module: Any = None) -> dict[str, Any] | None:
    if not db_module or not hasattr(db_module, "get_credential_health"):
        return None
    row = db_module.get_credential_health(platform)
    if not row:
        return None
    payload = dict(row.get("payload") or {})
    result = {
        "platform": platform,
        "state": row.get("state", "unknown"),
        "failure_type": row.get("failure_type", ""),
        "redacted_hint": row.get("redacted_hint", ""),
        **payload,
    }
    result.setdefault("status", result["state"])
    result.setdefault("credentials_present", bool(result.get("configured")))
    result.setdefault("credentials_valid", result["state"] == "valid")
    result.setdefault("validation_performed", result["state"] in {"valid", "invalid"})
    result.setdefault("live_capable", bool(result.get("credentials_valid")))
    result.setdefault("readiness", "ready_for_live" if result.get("live_capable") else "ready_for_paper_only")
    result.setdefault("display_state", "ready_for_live" if result.get("live_capable") else "loaded")
    result.setdefault("payload", payload.get("payload") if isinstance(payload.get("payload"), dict) else payload.get("payload", {}))
    return result


def validate_platform(
    platform: str,
    db_module: Any = None,
    settings_getter: Any = None,
    *,
    perform_network_check: bool = False,
    use_cached: bool = True,
) -> dict[str, Any]:
    platform = platform.lower().strip()
    validators = {
        "stake": lambda: _validate_stake(
            db_module=db_module,
            settings_getter=settings_getter,
            perform_network_check=perform_network_check,
            use_cached=use_cached,
        ),
        "polymarket": lambda: _validate_polymarket(
            db_module=db_module,
            settings_getter=settings_getter,
            perform_network_check=perform_network_check,
            use_cached=use_cached,
        ),
        "kalshi": lambda: _validate_kalshi(db_module=db_module, settings_getter=settings_getter),
        "oddsapi": lambda: _validate_oddsapi(db_module=db_module, settings_getter=settings_getter),
        "betfair": lambda: _validate_betfair(db_module=db_module, settings_getter=settings_getter),
        "sportsdataio": lambda: _validate_sportsdataio(db_module=db_module, settings_getter=settings_getter),
        "kalshi_public": lambda: _validate_kalshi_public(db_module=db_module, settings_getter=settings_getter),
        "kalshi_demo": lambda: _validate_kalshi_demo(db_module=db_module, settings_getter=settings_getter),
        "kalshi_live": lambda: _validate_kalshi_live(db_module=db_module, settings_getter=settings_getter),
        "polymarket_public": _validate_polymarket_public,
        "betfair_delayed": lambda: _validate_betfair_delayed(db_module=db_module, settings_getter=settings_getter),
        "sportsdataio_trial": lambda: _validate_sportsdataio_trial(db_module=db_module, settings_getter=settings_getter),
        "matchbook": lambda: _validate_stub("matchbook"),
        "betdaq": lambda: _validate_stub("betdaq"),
        "smarkets": lambda: _validate_stub("smarkets"),
    }
    validator = validators.get(platform)
    if validator is None:
        return _persist(
            db_module,
            _result(
                platform,
                state="unknown",
                auth_truth="missing",
                configured=False,
                reason=f"No validator registered for '{platform}'",
                failure_type="unknown_platform",
            ),
        )
    return _persist(db_module, validator())


def validate_all(
    db_module: Any = None,
    settings_getter: Any = None,
    *,
    perform_network_check: bool = False,
    use_cached: bool = True,
) -> dict[str, dict[str, Any]]:
    platforms = [
        "stake",
        "polymarket",
        "kalshi",
        "kalshi_public",
        "kalshi_demo",
        "kalshi_live",
        "oddsapi",
        "betfair",
        "betfair_delayed",
        "sportsdataio",
        "sportsdataio_trial",
        "polymarket_public",
        "matchbook",
        "betdaq",
        "smarkets",
    ]
    return {
        platform: validate_platform(
            platform,
            db_module=db_module,
            settings_getter=settings_getter,
            perform_network_check=perform_network_check,
            use_cached=use_cached,
        )
        for platform in platforms
    }


def credential_summary(results: dict[str, dict[str, Any]] | None = None, db_module: Any = None) -> dict[str, Any]:
    if results is None:
        results = validate_all(db_module=db_module)
    ordered = list(results.values())
    valid = sum(1 for result in ordered if result["state"] == "valid")
    missing = sum(1 for result in ordered if result["state"] in {"missing", "not_configured"})
    invalid = sum(1 for result in ordered if result["state"] == "invalid")
    unchecked = sum(1 for result in ordered if result["state"] in {"unchecked", "partial", "loaded"})
    loaded = sum(1 for result in ordered if result["state"] == "loaded")
    return {
        "total": len(ordered),
        "valid": valid,
        "missing": missing,
        "invalid": invalid,
        "unchecked": unchecked,
        "loaded": loaded,
        "platforms": ordered,
    }


def _validate_stake(
    *,
    db_module: Any = None,
    settings_getter: Any = None,
    perform_network_check: bool = False,
    use_cached: bool = True,
) -> dict[str, Any]:
    token = _read_setting("STAKE_API_TOKEN", db_module=db_module, settings_getter=settings_getter).strip()
    if not token:
        return _result(
            "stake",
            state="missing",
            auth_truth="missing",
            configured=False,
            credentials_present=False,
            credentials_valid=False,
            validation_performed=False,
            live_capable=False,
            readiness="ready_for_paper_only",
            display_state="missing",
            reason="STAKE_API_TOKEN not set; Stake stays in paper mode.",
            failure_type="missing_key",
            mode="PAPER",
        )
    redacted_hint = _redact_secret(token)
    if use_cached and not perform_network_check:
        cached = _cached_result("stake", db_module=db_module)
        if cached and cached.get("redacted_hint") == redacted_hint:
            cached["configured"] = True
            cached["credentials_present"] = True
            return cached
    if not perform_network_check:
        return _result(
            "stake",
            state="loaded",
            auth_truth="present",
            configured=True,
            credentials_present=True,
            credentials_valid=False,
            validation_performed=False,
            live_capable=False,
            readiness="ready_for_paper_only",
            display_state="loaded",
            reason="Stake token is loaded. Run validation before arming live execution.",
            redacted_hint=redacted_hint,
            mode="PAPER",
        )
    try:
        import config as runtime_config
        import stake_client

        previous_token = runtime_config.STAKE_API_TOKEN
        previous_header = stake_client.HEADERS.get("x-access-token", "")
        runtime_config.STAKE_API_TOKEN = token
        stake_client.HEADERS["x-access-token"] = token
        check = stake_client.token_health_check()
    except Exception as exc:
        check = {
            "configured": True,
            "valid": False,
            "failure_kind_label": "validator_error",
            "reason": str(exc),
            "latency_ms": None,
        }
    finally:
        try:
            runtime_config.STAKE_API_TOKEN = previous_token
            stake_client.HEADERS["x-access-token"] = previous_header
        except Exception:
            pass
    is_valid = bool(check.get("valid"))
    return _result(
        "stake",
        state="valid" if is_valid else "invalid",
        auth_truth="validated" if is_valid else "failed",
        configured=True,
        credentials_present=True,
        credentials_valid=is_valid,
        validation_performed=True,
        live_capable=is_valid,
        readiness="ready_for_live" if is_valid else "ready_for_paper_only",
        display_state="ready_for_live" if is_valid else "validation_failed",
        reason=str(check.get("reason") or ("Token accepted" if is_valid else "Stake token validation failed.")),
        failure_type="" if is_valid else str(check.get("failure_kind_label") or "validation_failed"),
        redacted_hint=redacted_hint,
        mode="LIVE-CAPABLE" if is_valid else "DEGRADED",
        payload={
            "latency_ms": check.get("latency_ms"),
            "token_health_truth": check.get("token_health_truth", "validated" if is_valid else "failed"),
        },
    )


def _validate_polymarket(
    *,
    db_module: Any = None,
    settings_getter: Any = None,
    perform_network_check: bool = False,
    use_cached: bool = True,
) -> dict[str, Any]:
    key = _read_setting("POLY_PRIVATE_KEY", db_module=db_module, settings_getter=settings_getter).strip()
    api_key = _read_setting("POLY_API_KEY", db_module=db_module, settings_getter=settings_getter).strip()
    api_secret = _read_setting("POLY_API_SECRET", db_module=db_module, settings_getter=settings_getter).strip()
    passphrase = _read_setting("POLY_API_PASSPHRASE", db_module=db_module, settings_getter=settings_getter).strip()
    if not any([key, api_key, api_secret, passphrase]):
        return _result(
            "polymarket",
            state="missing",
            auth_truth="missing",
            configured=False,
            credentials_present=False,
            credentials_valid=False,
            validation_performed=False,
            live_capable=False,
            readiness="ready_for_paper_only",
            display_state="missing",
            reason="Polymarket live credentials are absent; paper/public workflows only.",
            failure_type="missing_key",
            mode="PAPER",
        )
    redacted_hint = _redact_secret(key or api_key)
    if not key.startswith("0x") or len(key) < 20:
        return _result(
            "polymarket",
            state="invalid",
            auth_truth="failed",
            configured=False,
            credentials_present=True,
            credentials_valid=False,
            validation_performed=True,
            live_capable=False,
            readiness="ready_for_paper_only",
            display_state="validation_failed",
            reason="POLY_PRIVATE_KEY appears malformed.",
            failure_type="invalid_format",
            redacted_hint=redacted_hint,
            mode="DEGRADED",
        )
    required_fields_present = all([key, api_key, api_secret, passphrase])
    if use_cached and not perform_network_check:
        cached = _cached_result("polymarket", db_module=db_module)
        if cached and cached.get("redacted_hint") == redacted_hint:
            cached["configured"] = required_fields_present
            cached["credentials_present"] = True
            return cached
    if not required_fields_present:
        return _result(
            "polymarket",
            state="invalid",
            auth_truth="failed",
            configured=False,
            credentials_present=True,
            credentials_valid=False,
            validation_performed=True,
            live_capable=False,
            readiness="ready_for_paper_only",
            display_state="validation_failed",
            reason="Polymarket live requires POLY_PRIVATE_KEY, POLY_API_KEY, POLY_API_SECRET, and POLY_API_PASSPHRASE.",
            failure_type="missing_key",
            redacted_hint=redacted_hint,
            mode="DEGRADED",
        )
    if not perform_network_check:
        return _result(
            "polymarket",
            state="loaded",
            auth_truth="present",
            configured=True,
            credentials_present=True,
            credentials_valid=False,
            validation_performed=False,
            live_capable=False,
            readiness="ready_for_paper_only",
            display_state="loaded",
            reason="Polymarket credentials are loaded. Run validation before arming live execution.",
            redacted_hint=redacted_hint,
            mode="PAPER",
        )
    started = time.perf_counter()
    try:
        import config as runtime_config
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import ApiCreds

        creds = ApiCreds(
            api_key=api_key,
            api_secret=api_secret,
            api_passphrase=passphrase,
        )
        client = ClobClient(
            host=runtime_config.POLY_HOST,
            chain_id=runtime_config.POLY_CHAIN_ID,
            key=key,
            creds=creds,
        )
        response = client.get_api_keys()
        latency_ms = round((time.perf_counter() - started) * 1000, 1)
        response_text = str(response or "")
        is_valid = bool(response) and ("api" in response_text.lower() or "key" in response_text.lower() or isinstance(response, (dict, list)))
        reason = "Authenticated Polymarket connectivity confirmed." if is_valid else "Polymarket returned an unexpected validation response."
        failure_type = "" if is_valid else "validation_failed"
    except Exception as exc:
        latency_ms = round((time.perf_counter() - started) * 1000, 1)
        is_valid = False
        reason = str(exc)
        failure_type = "validation_failed"
    return _result(
        "polymarket",
        state="valid" if is_valid else "invalid",
        auth_truth="validated" if is_valid else "failed",
        configured=True,
        credentials_present=True,
        credentials_valid=is_valid,
        validation_performed=True,
        live_capable=is_valid,
        readiness="ready_for_live" if is_valid else "ready_for_paper_only",
        display_state="ready_for_live" if is_valid else "validation_failed",
        reason=reason,
        failure_type=failure_type,
        redacted_hint=redacted_hint,
        mode="LIVE-CAPABLE" if is_valid else "DEGRADED",
        payload={"latency_ms": latency_ms},
    )


def _validate_kalshi(*, db_module: Any = None, settings_getter: Any = None) -> dict[str, Any]:
    api_key = _read_setting("KALSHI_API_KEY", db_module=db_module, settings_getter=settings_getter).strip()
    private_key = _read_setting("KALSHI_PRIVATE_KEY", db_module=db_module, settings_getter=settings_getter).strip()
    use_demo = _bool_setting("KALSHI_USE_DEMO", db_module=db_module, settings_getter=settings_getter, default=False)
    if not api_key and not private_key:
        return _result(
            "kalshi",
            state="not_configured",
            auth_truth="missing",
            configured=False,
            reason="Kalshi credentials missing; public data and demo remain disabled until configured.",
            failure_type="missing_key",
            mode="DEMO" if use_demo else "PUBLIC DATA ONLY",
        )
    if api_key and len(api_key) < 8:
        return _result(
            "kalshi",
            state="invalid",
            auth_truth="failed",
            configured=False,
            reason="KALSHI_API_KEY looks too short.",
            failure_type="invalid_format",
            redacted_hint=_redact_secret(api_key),
            mode="DEMO" if use_demo else "LIVE DISABLED",
        )
    return _result(
        "kalshi",
        state="unchecked",
        auth_truth="present",
        configured=True,
        reason="Kalshi credentials are present; demo/live validation is deferred and live stays disabled.",
        redacted_hint=_redact_secret(api_key or private_key),
        mode="DEMO" if use_demo else "LIVE DISABLED",
    )


def _validate_kalshi_public(*, db_module: Any = None, settings_getter: Any = None) -> dict[str, Any]:
    enabled = _bool_setting("ENABLE_KALSHI", db_module=db_module, settings_getter=settings_getter, default=False)
    return _result(
        "kalshi_public",
        state="valid" if enabled else "not_configured",
        auth_truth="validated" if enabled else "missing",
        configured=enabled,
        reason="Kalshi public adapter uses unauthenticated market data." if enabled else "ENABLE_KALSHI=false",
        mode="PUBLIC DATA ONLY",
    )


def _validate_kalshi_demo(*, db_module: Any = None, settings_getter: Any = None) -> dict[str, Any]:
    api_key = _read_setting("KALSHI_API_KEY", db_module=db_module, settings_getter=settings_getter).strip()
    private_key = _read_setting("KALSHI_PRIVATE_KEY", db_module=db_module, settings_getter=settings_getter).strip()
    demo_keys_present = bool(api_key and private_key)
    use_demo = _bool_setting("KALSHI_USE_DEMO", db_module=db_module, settings_getter=settings_getter, default=False)
    if not use_demo:
        return _result(
            "kalshi_demo",
            state="not_configured",
            auth_truth="missing",
            configured=False,
            reason="KALSHI_USE_DEMO=false",
            mode="DEMO",
        )
    return _result(
        "kalshi_demo",
        state="unchecked" if demo_keys_present else "partial",
        auth_truth="present" if demo_keys_present else "missing",
        configured=demo_keys_present,
        reason="Kalshi demo is isolated from production and requires separate validation.",
        redacted_hint=_redact_secret(api_key),
        failure_type="" if demo_keys_present else "missing_key",
        mode="DEMO",
    )


def _validate_kalshi_live(*, db_module: Any = None, settings_getter: Any = None) -> dict[str, Any]:
    enabled = _bool_setting("ENABLE_KALSHI", db_module=db_module, settings_getter=settings_getter, default=False)
    api_key = _read_setting("KALSHI_API_KEY", db_module=db_module, settings_getter=settings_getter).strip()
    private_key = _read_setting("KALSHI_PRIVATE_KEY", db_module=db_module, settings_getter=settings_getter).strip()
    if not enabled:
        return _result(
            "kalshi_live",
            state="not_configured",
            auth_truth="missing",
            configured=False,
            reason="ENABLE_KALSHI=false",
            mode="LIVE DISABLED",
        )
    if not api_key or not private_key:
        return _result(
            "kalshi_live",
            state="missing",
            auth_truth="missing",
            configured=False,
            reason="Kalshi live requires KALSHI_API_KEY and KALSHI_PRIVATE_KEY; execution remains disabled.",
            failure_type="missing_key",
            mode="LIVE DISABLED",
        )
    return _result(
        "kalshi_live",
        state="unchecked",
        auth_truth="present",
        configured=True,
        reason="Kalshi live credentials are present, but the live adapter remains disabled by default.",
        redacted_hint=_redact_secret(api_key),
        mode="LIVE DISABLED",
    )


def _validate_oddsapi(*, db_module: Any = None, settings_getter: Any = None) -> dict[str, Any]:
    key = _read_setting("ODDS_API_KEY", db_module=db_module, settings_getter=settings_getter).strip()
    enabled = _bool_setting("ENABLE_ODDSAPI", db_module=db_module, settings_getter=settings_getter, default=False)
    if not enabled:
        return _result(
            "oddsapi",
            state="not_configured",
            auth_truth="missing",
            configured=False,
            reason="ENABLE_ODDSAPI=false",
            mode="PUBLIC DATA ONLY",
        )
    if not key:
        return _result(
            "oddsapi",
            state="missing",
            auth_truth="missing",
            configured=False,
            reason="ODDS_API_KEY missing.",
            failure_type="missing_key",
            mode="PUBLIC DATA ONLY",
        )
    return _result(
        "oddsapi",
        state="unchecked",
        auth_truth="present",
        configured=True,
        reason="Odds API key is present; quota-aware public-data polling only.",
        redacted_hint=_redact_secret(key),
        mode="PUBLIC DATA ONLY",
        payload={"historical_access": "paid_only"},
    )


def _validate_betfair(*, db_module: Any = None, settings_getter: Any = None) -> dict[str, Any]:
    app_key = _read_setting("BETFAIR_APP_KEY", db_module=db_module, settings_getter=settings_getter).strip()
    username = _read_setting("BETFAIR_USERNAME", db_module=db_module, settings_getter=settings_getter).strip()
    password = _read_setting("BETFAIR_PASSWORD", db_module=db_module, settings_getter=settings_getter).strip()
    if not any([app_key, username, password]):
        return _result(
            "betfair",
            state="missing",
            auth_truth="missing",
            configured=False,
            reason="Betfair live access is absent and remains disabled.",
            failure_type="missing_key",
            mode="LIVE DISABLED",
        )
    return _result(
        "betfair",
        state="unchecked",
        auth_truth="present",
        configured=True,
        reason="Betfair credentials are present but live trading is intentionally disabled.",
        redacted_hint=_redact_secret(app_key),
        mode="LIVE DISABLED",
    )


def _validate_betfair_delayed(*, db_module: Any = None, settings_getter: Any = None) -> dict[str, Any]:
    enabled = _bool_setting("ENABLE_BETFAIR_DELAYED", db_module=db_module, settings_getter=settings_getter, default=False)
    app_key = _read_setting("BETFAIR_APP_KEY", db_module=db_module, settings_getter=settings_getter).strip()
    if not enabled:
        return _result(
            "betfair_delayed",
            state="not_configured",
            auth_truth="missing",
            configured=False,
            reason="ENABLE_BETFAIR_DELAYED=false",
            mode="DELAYED",
        )
    return _result(
        "betfair_delayed",
        state="unchecked" if app_key else "partial",
        auth_truth="present" if app_key else "missing",
        configured=bool(app_key),
        reason="Betfair delayed mode is for research/dev only.",
        redacted_hint=_redact_secret(app_key),
        failure_type="" if app_key else "missing_key",
        mode="DELAYED",
    )


def _validate_sportsdataio(*, db_module: Any = None, settings_getter: Any = None) -> dict[str, Any]:
    key = _read_setting("SPORTSDATAIO_API_KEY", db_module=db_module, settings_getter=settings_getter).strip()
    if not key:
        return _result(
            "sportsdataio",
            state="missing",
            auth_truth="missing",
            configured=False,
            reason="SPORTSDATAIO_API_KEY not set.",
            failure_type="missing_key",
            mode="TRIAL",
        )
    return _result(
        "sportsdataio",
        state="unchecked",
        auth_truth="present",
        configured=True,
        reason="SportsDataIO key present; trial data must remain research-only.",
        redacted_hint=_redact_secret(key),
        mode="TRIAL",
    )


def _validate_sportsdataio_trial(*, db_module: Any = None, settings_getter: Any = None) -> dict[str, Any]:
    enabled = _bool_setting("ENABLE_SPORTSDATAIO_TRIAL", db_module=db_module, settings_getter=settings_getter, default=False)
    key = _read_setting("SPORTSDATAIO_API_KEY", db_module=db_module, settings_getter=settings_getter).strip()
    if not enabled:
        return _result(
            "sportsdataio_trial",
            state="not_configured",
            auth_truth="missing",
            configured=False,
            reason="ENABLE_SPORTSDATAIO_TRIAL=false",
            mode="TRIAL",
        )
    return _result(
        "sportsdataio_trial",
        state="unchecked" if key else "partial",
        auth_truth="present" if key else "missing",
        configured=bool(key),
        reason="SportsDataIO trial remains research-only and may be scrambled.",
        redacted_hint=_redact_secret(key),
        failure_type="" if key else "missing_key",
        mode="TRIAL",
    )


def _validate_polymarket_public() -> dict[str, Any]:
    return _result(
        "polymarket_public",
        state="valid",
        auth_truth="validated",
        configured=True,
        reason="Polymarket public data adapter uses unauthenticated market data.",
        mode="PUBLIC DATA ONLY",
    )


def _validate_stub(platform: str) -> dict[str, Any]:
    return _result(
        platform,
        state="not_configured",
        auth_truth="missing",
        configured=False,
        reason="Stub adapter registered for catalog visibility only.",
        mode="NOT CONFIGURED",
    )
