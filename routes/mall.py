from __future__ import annotations

from collections import Counter

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse

import database as db
import server as runtime
from services.mall_playbooks import build_mall_item_playbook
from services.notification_center import notify_mall_pipeline_stage


router = APIRouter(tags=["mall"])

STAGE_ALIASES = {
    "review": "reviewed",
    "qualified": "quoted",
}
VALID_MALL_STAGES = (
    "new",
    "discovered",
    "reviewed",
    "contacted",
    "quoted",
    "booked",
    "paid",
    "cleared",
    "vaulted",
    "rejected",
    "archived",
)
ACTIONABLE_STAGES = {"discovered", "reviewed", "contacted", "quoted", "booked"}
CLOSED_STAGES = {"paid", "cleared", "vaulted"}


def _normalize_stage(value: str | None) -> str:
    stage = str(value or "").strip().lower().replace(" ", "_")
    return STAGE_ALIASES.get(stage, stage)


def _canonicalize_item(item: dict | None) -> dict | None:
    if item is None:
        return None
    payload = dict(item)
    payload["stage"] = _normalize_stage(payload.get("stage"))
    return payload


def _build_summary(items: list[dict]) -> dict:
    stage_counts = Counter(str(item.get("stage", "unknown") or "unknown") for item in items)
    lane_counts = Counter(str(item.get("lane", "unknown") or "unknown") for item in items)
    total_value = sum(float(item.get("value_estimate", 0) or 0) for item in items)
    actionable_value = sum(
        float(item.get("value_estimate", 0) or 0)
        for item in items
        if str(item.get("stage", "") or "") in ACTIONABLE_STAGES
    )
    closed_value = sum(
        float(item.get("value_estimate", 0) or 0)
        for item in items
        if str(item.get("stage", "") or "") in CLOSED_STAGES
    )
    latest_ts = items[0].get("ts") if items else None
    return {
        "total": len(items),
        "total_value_estimate": round(total_value, 2),
        "actionable_count": sum(stage_counts.get(stage, 0) for stage in ACTIONABLE_STAGES),
        "actionable_value_estimate": round(actionable_value, 2),
        "closed_count": sum(stage_counts.get(stage, 0) for stage in CLOSED_STAGES),
        "closed_value_estimate": round(closed_value, 2),
        "by_stage": dict(stage_counts),
        "by_lane": dict(lane_counts),
        "latest_ts": latest_ts,
    }


@router.get("/api/mall/pipeline")
async def get_mall_pipeline(
    request: Request,
    lane: str | None = None,
    stage: str | None = None,
    bot_id: str | None = None,
    limit: int = 100,
):
    runtime._check_token(request)
    normalized_stage = _normalize_stage(stage) if stage else None
    limit = max(1, min(int(limit), 200))
    fetch_limit = max(limit, 500)
    items = db.get_mall_pipeline(lane=lane, stage=None, limit=fetch_limit)
    items = [_canonicalize_item(item) for item in items]
    if normalized_stage:
        items = [item for item in items if str(item.get("stage", "")) == normalized_stage]
    if bot_id:
        items = [item for item in items if str(item.get("bot_id", "") or "") == str(bot_id)]
    items = items[:limit]
    return JSONResponse(
        {
            "ok": True,
            "items": items,
            "summary": _build_summary(items),
            "available_stages": list(VALID_MALL_STAGES),
        }
    )


@router.patch("/api/mall/pipeline/{item_id}/stage")
@runtime.limiter.limit("30/minute")
async def patch_mall_pipeline_stage(item_id: int, request: Request, body: dict = Body(...)):
    runtime._check_token(request)
    new_stage = _normalize_stage(body.get("stage"))
    if new_stage not in VALID_MALL_STAGES:
        return JSONResponse(
            {
                "ok": False,
                "error": "invalid_stage",
                "available_stages": list(VALID_MALL_STAGES),
            },
            status_code=400,
        )

    item = _canonicalize_item(db.get_mall_pipeline_item(item_id)) if hasattr(db, "get_mall_pipeline_item") else None
    if item is None:
        return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)

    previous_stage = _normalize_stage(item.get("stage"))
    if previous_stage == new_stage:
        return JSONResponse({"ok": True, "item": item, "unchanged": True})

    source = str(body.get("source", "dashboard") or "dashboard")
    reason = str(body.get("reason", "") or "").strip()
    action = runtime._begin_runtime_action(
        "mall_pipeline_stage",
        bot_id=str(item.get("bot_id", "") or ""),
        source=source,
        payload={
            "item_id": item_id,
            "previous_stage": previous_stage,
            "new_stage": new_stage,
        },
    )

    try:
        db.update_mall_pipeline_stage(item_id, new_stage)
        updated = _canonicalize_item(db.get_mall_pipeline_item(item_id)) if hasattr(db, "get_mall_pipeline_item") else None
        db.save_reconciliation_event(
            "mall",
            str(item_id),
            "pipeline_stage",
            new_stage,
            reason=reason or f"{previous_stage}->{new_stage}",
            payload={
                "bot_id": item.get("bot_id", ""),
                "title": item.get("title", ""),
                "contact_ref": item.get("contact_ref", ""),
                "previous_stage": previous_stage,
                "new_stage": new_stage,
                "source": source,
            },
        )
        notify_mall_pipeline_stage(
            updated or item,
            previous_stage=previous_stage,
            new_stage=new_stage,
            source=source,
            db_module=db,
        )
        runtime._finish_runtime_action(
            action,
            "reconciled",
            payload={
                "item_id": item_id,
                "previous_stage": previous_stage,
                "new_stage": new_stage,
            },
        )
        return JSONResponse({"ok": True, "item": updated or item})
    except Exception as exc:
        runtime._finish_runtime_action(
            action,
            "failed",
            reason=str(exc),
            payload={"item_id": item_id, "new_stage": new_stage},
        )
        return JSONResponse(
            {"ok": False, "error": "stage_update_failed", "detail": str(exc)},
            status_code=500,
        )


# ── History ───────────────────────────────────────────────────────────────────

@router.get("/api/mall/pipeline/{item_id}/history")
async def get_mall_item_history(item_id: int, request: Request, limit: int = 50):
    runtime._check_token(request)
    if not hasattr(db, "get_mall_pipeline_item"):
        return JSONResponse({"ok": False, "error": "not_supported"}, status_code=501)
    item = _canonicalize_item(db.get_mall_pipeline_item(item_id))
    if item is None:
        return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
    history = db.get_mall_pipeline_history(item_id, limit=min(int(limit), 200)) if hasattr(db, "get_mall_pipeline_history") else []
    return JSONResponse({"ok": True, "item_id": item_id, "history": history})


@router.get("/api/mall/pipeline/{item_id}/playbook")
async def get_mall_item_playbook(item_id: int, request: Request):
    runtime._check_token(request)
    if not hasattr(db, "get_mall_pipeline_item"):
        return JSONResponse({"ok": False, "error": "not_supported"}, status_code=501)
    item = _canonicalize_item(db.get_mall_pipeline_item(item_id))
    if item is None:
        return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
    return JSONResponse({"ok": True, "item_id": item_id, "playbook": build_mall_item_playbook(item)})


# ── Quote ─────────────────────────────────────────────────────────────────────

@router.post("/api/mall/pipeline/{item_id}/quote")
@runtime.limiter.limit("20/minute")
async def mall_quote(item_id: int, request: Request, body: dict = Body(...)):
    runtime._check_token(request)
    item = _canonicalize_item(db.get_mall_pipeline_item(item_id)) if hasattr(db, "get_mall_pipeline_item") else None
    if item is None:
        return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)

    quoted_amount = float(body.get("quoted_amount") or 0)
    if quoted_amount <= 0:
        return JSONResponse({"ok": False, "error": "quoted_amount must be > 0"}, status_code=400)

    compliance_notes = str(body.get("compliance_notes") or "").strip()
    actor = str(body.get("actor") or "operator").strip()
    previous_stage = _normalize_stage(item.get("stage"))
    if previous_stage not in {"discovered", "reviewed", "contacted", "quoted", "booked"}:
        return JSONResponse(
            {"ok": False, "error": "Item must be in discovery/contact/quote flow before quoting"},
            status_code=400,
        )

    try:
        if hasattr(db, "mall_set_quote"):
            db.mall_set_quote(item_id, quoted_amount, compliance_notes)
        else:
            db.update_mall_pipeline_stage(item_id, "quoted")

        if hasattr(db, "mall_add_history_event"):
            db.mall_add_history_event(
                item_id,
                "quoted",
                actor=actor,
                previous_stage=previous_stage,
                new_stage="quoted",
                amount=quoted_amount,
                notes=compliance_notes,
                payload={"source": "quote_endpoint"},
            )

        db.save_reconciliation_event(
            "mall", str(item_id), "quoted", "quoted",
            reason=f"Quote ${quoted_amount:.2f} issued",
            payload={"quoted_amount": quoted_amount, "compliance_notes": compliance_notes, "actor": actor},
        )
        updated = _canonicalize_item(db.get_mall_pipeline_item(item_id)) if hasattr(db, "get_mall_pipeline_item") else None
        return JSONResponse({"ok": True, "item": updated})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


# ── Pay ───────────────────────────────────────────────────────────────────────

@router.post("/api/mall/pipeline/{item_id}/pay")
@runtime.limiter.limit("20/minute")
async def mall_pay(item_id: int, request: Request, body: dict = Body(...)):
    runtime._check_token(request)
    item = _canonicalize_item(db.get_mall_pipeline_item(item_id)) if hasattr(db, "get_mall_pipeline_item") else None
    if item is None:
        return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)

    paid_amount = float(body.get("paid_amount") or 0)
    if paid_amount <= 0:
        return JSONResponse({"ok": False, "error": "paid_amount must be > 0"}, status_code=400)

    proof_ref = str(body.get("proof_ref") or "").strip()
    actor = str(body.get("actor") or "operator").strip()
    previous_stage = _normalize_stage(item.get("stage"))
    if previous_stage not in {"quoted", "booked", "paid"}:
        return JSONResponse(
            {"ok": False, "error": "Item must be booked before recording payment"},
            status_code=400,
        )

    try:
        if hasattr(db, "mall_record_payment"):
            db.mall_record_payment(item_id, paid_amount, proof_ref)
        else:
            db.update_mall_pipeline_stage(item_id, "paid")

        if hasattr(db, "mall_add_history_event"):
            db.mall_add_history_event(
                item_id,
                "paid",
                actor=actor,
                previous_stage=previous_stage,
                new_stage="paid",
                amount=paid_amount,
                notes=proof_ref,
                payload={"proof_ref": proof_ref},
            )

        db.save_reconciliation_event(
            "mall", str(item_id), "paid", "paid",
            reason=f"Payment ${paid_amount:.2f} recorded",
            payload={"paid_amount": paid_amount, "proof_ref": proof_ref, "actor": actor},
        )
        updated = _canonicalize_item(db.get_mall_pipeline_item(item_id)) if hasattr(db, "get_mall_pipeline_item") else None
        return JSONResponse({"ok": True, "item": updated})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


# ── Clear ─────────────────────────────────────────────────────────────────────

@router.post("/api/mall/pipeline/{item_id}/clear")
@runtime.limiter.limit("20/minute")
async def mall_clear(item_id: int, request: Request, body: dict = Body(...)):
    runtime._check_token(request)
    item = _canonicalize_item(db.get_mall_pipeline_item(item_id)) if hasattr(db, "get_mall_pipeline_item") else None
    if item is None:
        return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)

    if _normalize_stage(item.get("stage")) != "paid":
        return JSONResponse(
            {"ok": False, "error": "Item must be in 'paid' stage before clearing"},
            status_code=400,
        )

    notes = str(body.get("notes") or "").strip()
    actor = str(body.get("actor") or "operator").strip()

    try:
        if hasattr(db, "mall_clear_payment"):
            db.mall_clear_payment(item_id, notes)
        else:
            db.update_mall_pipeline_stage(item_id, "cleared")

        if hasattr(db, "mall_add_history_event"):
            db.mall_add_history_event(
                item_id,
                "cleared",
                actor=actor,
                previous_stage="paid",
                new_stage="cleared",
                notes=notes,
                payload={"cleared_by": actor},
            )

        db.save_reconciliation_event(
            "mall", str(item_id), "cleared", "cleared",
            reason="Payment cleared",
            payload={"notes": notes, "actor": actor},
        )
        updated = _canonicalize_item(db.get_mall_pipeline_item(item_id)) if hasattr(db, "get_mall_pipeline_item") else None
        return JSONResponse({"ok": True, "item": updated})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


# ── Suppress / Opt-out ────────────────────────────────────────────────────────

@router.post("/api/mall/pipeline/{item_id}/suppress")
@runtime.limiter.limit("20/minute")
async def mall_suppress(item_id: int, request: Request, body: dict = Body(...)):
    runtime._check_token(request)
    item = _canonicalize_item(db.get_mall_pipeline_item(item_id)) if hasattr(db, "get_mall_pipeline_item") else None
    if item is None:
        return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)

    reason = str(body.get("reason") or "operator_suppressed").strip()
    actor = str(body.get("actor") or "operator").strip()

    try:
        if hasattr(db, "mall_suppress_item"):
            db.mall_suppress_item(item_id, reason)
        if hasattr(db, "mall_add_history_event"):
            db.mall_add_history_event(
                item_id,
                "suppressed",
                actor=actor,
                previous_stage=_normalize_stage(item.get("stage")),
                new_stage=_normalize_stage(item.get("stage")),
                notes=reason,
            )
        db.save_reconciliation_event(
            "mall", str(item_id), "suppressed", "suppressed",
            reason=reason,
            payload={"actor": actor},
        )
        updated = _canonicalize_item(db.get_mall_pipeline_item(item_id)) if hasattr(db, "get_mall_pipeline_item") else None
        return JSONResponse({"ok": True, "item": updated})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


# ── Log outreach ──────────────────────────────────────────────────────────────

@router.post("/api/mall/pipeline/{item_id}/outreach")
@runtime.limiter.limit("20/minute")
async def mall_log_outreach(item_id: int, request: Request, body: dict = Body(...)):
    runtime._check_token(request)
    item = _canonicalize_item(db.get_mall_pipeline_item(item_id)) if hasattr(db, "get_mall_pipeline_item") else None
    if item is None:
        return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)

    channel = str(body.get("channel") or "manual").strip()
    notes = str(body.get("notes") or "").strip()
    actor = str(body.get("actor") or "operator").strip()

    try:
        if hasattr(db, "mall_record_outreach"):
            db.mall_record_outreach(item_id)
        if hasattr(db, "mall_add_history_event"):
            db.mall_add_history_event(
                item_id,
                "outreach",
                actor=actor,
                previous_stage=_normalize_stage(item.get("stage")),
                new_stage=_normalize_stage(item.get("stage")),
                notes=f"[{channel}] {notes}".strip(),
                payload={"channel": channel},
            )
        updated = _canonicalize_item(db.get_mall_pipeline_item(item_id)) if hasattr(db, "get_mall_pipeline_item") else None
        return JSONResponse({"ok": True, "item": updated})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)
