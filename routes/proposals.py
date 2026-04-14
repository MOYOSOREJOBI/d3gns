from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(tags=["proposals"])


@router.post("/api/proposals/submit")
async def submit_proposal(request: Request):
    """
    Generate a proposal from a bot and route it through the full risk + order pipeline.

    Body fields:
      bot_id          str   required
      runtime_mode    str   optional (default: "paper")
      working_capital float optional (default: 100)
      floor           float optional (default: 80)
      repel_zone      float optional (default: 15)
      price_limit     float optional
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid_json"}, status_code=400)

    bot_id = str(body.get("bot_id", "")).strip()
    if not bot_id:
        return JSONResponse({"ok": False, "error": "bot_id_required"}, status_code=400)

    import server as runtime
    runtime._check_token(request)

    # Instantiate bot (same pattern as _run_expansion_bot)
    try:
        runtime._init_platform_services()
        bot = runtime.instantiate_bot(bot_id, runtime._market_registry)
    except Exception as exc:
        return JSONResponse(
            {"ok": False, "error": "bot_not_found", "detail": str(exc)},
            status_code=404,
        )

    context = {"runtime_mode": str(body.get("runtime_mode", "paper")).lower()}

    try:
        proposal = bot.generate_proposal(context)
    except Exception as exc:
        return JSONResponse(
            {"ok": False, "error": "proposal_generation_failed", "detail": str(exc)},
            status_code=500,
        )

    if proposal is None:
        return JSONResponse({"ok": True, "proposal": None, "reason": "no_actionable_edge"})

    working_capital = float(body.get("working_capital", 100))
    floor = float(body.get("floor", working_capital * 0.801))
    repel_zone = float(body.get("repel_zone", working_capital * 0.15))

    from services.order_router import route_proposal
    import database as db

    try:
        result = route_proposal(
            proposal,
            db_module=db,
            registry=runtime._market_registry,
            execution_mode=context["runtime_mode"],
            price_limit=body.get("price_limit"),
            executor_owner_id=body.get("executor_owner_id"),
            risk_context={
                "working_capital": working_capital,
                "floor": floor,
                "repel_zone": repel_zone,
            },
        )
    except Exception as exc:
        return JSONResponse(
            {"ok": False, "error": "routing_failed", "detail": str(exc)},
            status_code=500,
        )

    try:
        from services.notification_center import notify_proposal_routed

        notify_proposal_routed(proposal.model_dump(), result, source="api_submit", db_module=db)
    except Exception:
        pass

    return JSONResponse({"ok": True, **result})
