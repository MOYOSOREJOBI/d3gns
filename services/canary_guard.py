from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse

import config as _cfg


def _json_body(request: Any) -> dict[str, Any]:
    return getattr(request.state, "typed_body", None) or {}


def _challenge_is_approved(record: dict[str, Any] | None, *, bot_id: str) -> bool:
    if not record:
        return False
    if str(record.get("bot_id") or "") != str(bot_id):
        return False
    if str(record.get("status") or "").lower() != "resolved":
        return False
    return str(record.get("response") or "").lower() == "yes"


def ensure_canary_approval(
    *,
    bot_id: str,
    request: Any,
    db_module: Any,
) -> JSONResponse | None:
    if not getattr(_cfg, "REQUIRE_CANARY_APPROVAL", True):
        return None

    try:
        body = _json_body(request)
        challenge_id = (
            request.headers.get("X-Approval-Challenge-Id", "").strip()
            or str(body.get("approval_challenge_id", "") or "").strip()
        )
    except Exception:
        challenge_id = ""

    if challenge_id and hasattr(db_module, "get_human_relay_request"):
        record = db_module.get_human_relay_request(challenge_id)
        if _challenge_is_approved(record, bot_id=bot_id):
            return None

    from services.human_relay import open_challenge

    challenge = open_challenge(
        bot_id=bot_id,
        platform="live_canary",
        prompt=f"Approve canary start for {bot_id}?",
        description=(
            "DeG£N$ is blocking automatic canary activation until an operator approves the dust-live session. "
            "Reply yes/no through the human relay flow, then retry with the returned challenge id."
        ),
        timeout_s=int(getattr(_cfg, "CANARY_APPROVAL_TTL_S", 600) or 600),
        db_module=db_module,
    )
    return JSONResponse(
        {
            "ok": False,
            "error": "approval_required",
            "bot_id": bot_id,
            "challenge": challenge,
        },
        status_code=409,
    )

