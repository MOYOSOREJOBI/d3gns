from __future__ import annotations

from collections import Counter
from typing import Any

from services.home_content import LAB_HOME_STACK, MALL_HOME_STACK, build_manual_library


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or 0)
    except Exception:
        return default


def _action(
    title: str,
    *,
    priority: str,
    lane: str,
    detail: str,
    route: str = "",
    eta: str = "",
    command: str = "",
) -> dict[str, str]:
    return {
        "title": title,
        "priority": priority,
        "lane": lane,
        "detail": detail,
        "route": route,
        "eta": eta,
        "command": command,
    }


def _priority_score(level: str) -> int:
    return {"critical": 0, "high": 1, "medium": 2, "low": 3}.get(str(level or "").lower(), 9)


def _trim_actions(actions: list[dict[str, str]], limit: int = 6) -> list[dict[str, str]]:
    ranked = sorted(actions, key=lambda row: (_priority_score(row.get("priority", "")), row.get("lane", "")))
    return ranked[:limit]


def build_operator_brief(
    *,
    db_module: Any,
    running: bool = False,
    runtime_bot_count: int = 0,
    active_runtime_count: int = 0,
) -> dict[str, Any]:
    settings = db_module.get_all_settings() if hasattr(db_module, "get_all_settings") else {}
    wallet = db_module.get_wallet_summary() if hasattr(db_module, "get_wallet_summary") else {}
    mall_items = db_module.get_mall_pipeline(limit=200) if hasattr(db_module, "get_mall_pipeline") else []
    mall_stage_counts = Counter(str(item.get("stage", "unknown") or "unknown") for item in mall_items)
    rec_events = db_module.get_reconciliation_events(limit=200) if hasattr(db_module, "get_reconciliation_events") else []
    rec_event_counts = Counter(str(item.get("event_type", "unknown") or "unknown") for item in rec_events)
    notifications = db_module.get_notifications(50) if hasattr(db_module, "get_notifications") else []
    note_types = Counter(str(item.get("type", "info") or "info") for item in notifications)
    signals = db_module.get_research_signals(limit=50) if hasattr(db_module, "get_research_signals") else []
    pending_relay = len(db_module.list_human_relay_requests(status="pending", limit=50)) if hasattr(db_module, "list_human_relay_requests") else 0
    open_positions = db_module.get_open_position_count() if hasattr(db_module, "get_open_position_count") else 0

    quoted_count = mall_stage_counts.get("quoted", 0)
    booked_count = mall_stage_counts.get("booked", 0)
    paid_count = mall_stage_counts.get("paid", 0)
    cleared_count = mall_stage_counts.get("cleared", 0)
    vaulted_count = mall_stage_counts.get("vaulted", 0)
    proposal_count = rec_event_counts.get("proposal_decision", 0)
    routed_count = rec_event_counts.get("proposal_routed", 0)
    execution_count = (
        rec_event_counts.get("shadow_execution", 0)
        + rec_event_counts.get("demo_execution", 0)
        + rec_event_counts.get("live_execution", 0)
    )
    mismatch_count = rec_event_counts.get("reconciliation_mismatch", 0)
    floor_warning_count = note_types.get("floor_warning", 0)
    telegram_configured = bool(settings.get("telegram_bot_token")) and bool(
        settings.get("telegram_chat_id") or settings.get("telegram_operator_chat_ids")
    )

    primary_focus = "MALL"
    if paid_count > 0 or cleared_count > 0 or vaulted_count > 0:
        primary_focus = "MALL + LAB"
    elif proposal_count >= 3 and execution_count >= 1:
        primary_focus = "LAB + MALL"

    actions: list[dict[str, str]] = []
    if not telegram_configured:
        actions.append(
            _action(
                "Configure Telegram operator alerts",
                priority="critical",
                lane="Shared",
                detail="Telegram is still not armed, so runtime errors and queue prompts can die silently.",
                route="Settings",
                eta="5m",
                command="/help",
            )
        )
    if not mall_items:
        actions.append(
            _action(
                "Seed the Mall queue with reviewed leads",
                priority="critical",
                lane="MALL",
                detail="No revenue pipeline exists yet. Start with local website rescue, GBP fixes, and booking funnel reviews.",
                route="Mall",
                eta="30m",
                command="/mall",
            )
        )
    elif quoted_count == 0:
        actions.append(
            _action(
                "Convert reviewed leads into first quotes",
                priority="high",
                lane="MALL",
                detail="The queue exists, but no quoted items are on record. Focus on same-day quoting to create conversion pressure.",
                route="Mall",
                eta="20m",
                command="/mall",
            )
        )
    if quoted_count > 0 and paid_count == 0 and cleared_count == 0 and vaulted_count == 0:
        actions.append(
            _action(
                "Follow up on quoted and booked leads",
                priority="high",
                lane="MALL",
                detail="You have revenue pressure building in the queue. Prioritize follow-ups and deposits before adding more discovery volume.",
                route="Mall",
                eta="25m",
                command="/mall",
            )
        )
    if cleared_count > 0 and vaulted_count == 0:
        actions.append(
            _action(
                "Review cleared revenue and vault outcome",
                priority="high",
                lane="MALL",
                detail="Cleared revenue exists but has not been fully vaulted through the operator flow. Confirm the vault trail and reconciliation.",
                route="Mall",
                eta="10m",
                command="/mall",
            )
        )
    if proposal_count == 0:
        actions.append(
            _action(
                "Generate first LAB proposal evidence",
                priority="high",
                lane="LAB",
                detail="No proposal decisions are in the evidence trail yet. Run paper/demo proposal cycles before risking live capital.",
                route="Lab",
                eta="20m",
                command="/lab",
            )
        )
    elif execution_count == 0:
        actions.append(
            _action(
                "Route first executable proposal through demo/shadow",
                priority="high",
                lane="LAB",
                detail="Proposal evidence exists, but there is no execution evidence. Prove route -> adapter -> reconciliation before scaling.",
                route="Lab",
                eta="20m",
                command="/lab",
            )
        )
    if mismatch_count > 0:
        actions.append(
            _action(
                "Investigate reconciliation drift",
                priority="critical",
                lane="Shared",
                detail=f"{mismatch_count} reconciliation mismatch event(s) were recorded. Do not scale live routing until they are understood.",
                route="Diagnostics",
                eta="15m",
                command="/brief",
            )
        )
    if pending_relay > 0:
        actions.append(
            _action(
                "Clear pending human relay approvals",
                priority="high",
                lane="Shared",
                detail=f"{pending_relay} human relay challenge(s) are pending. These block operator-reviewed actions and can stall the system.",
                route="Diagnostics",
                eta="10m",
                command="/brief",
            )
        )
    if running and active_runtime_count == 0:
        actions.append(
            _action(
                "Runtime is on, but no bots are active",
                priority="medium",
                lane="Shared",
                detail="The runtime is marked running while all bots appear inactive or paused. Verify runtime controls and pause flags.",
                route="Overview",
                eta="5m",
                command="/status",
            )
        )
    if floor_warning_count > 0:
        actions.append(
            _action(
                "ForceField warnings need review",
                priority="high",
                lane="LAB",
                detail=f"{floor_warning_count} floor warning notification(s) were raised. Keep LAB in preservation mode until the pressure normalizes.",
                route="Lab",
                eta="10m",
                command="/lab",
            )
        )
    if not actions:
        actions.append(
            _action(
                "Operate steady and harvest evidence",
                priority="low",
                lane="Shared",
                detail="Core surfaces look healthy. Keep MALL conversion moving, keep LAB conservative, and avoid broadening scope prematurely.",
                route="Home",
                eta="10m",
                command="/brief",
            )
        )

    mall_focus = [
        {
            "bot_id": item["bot_id"],
            "title": item["prototype_label"],
            "lane": item["lane"],
            "goal": "same-day quote and fast close",
        }
        for item in MALL_HOME_STACK
        if item["bot_id"] in {"bot_google_biz_profile", "bot_booking_funnel", "bot_local_biz_website", "bot_ai_intake", "bot_seo_audit"}
    ]
    lab_focus = [
        {
            "bot_id": item["bot_id"],
            "title": item["prototype_label"],
            "tier": item["tier"],
            "goal": "paper/demo proof before live scale",
        }
        for item in LAB_HOME_STACK
        if item["bot_id"] in {"bot_poly_kalshi_crossvenue_spread", "bot_polymarket_microstructure_paper", "bot_funding_rate_arb_paper", "bot_kalshi_orderbook_imbalance_paper"}
    ]

    return {
        "summary": {
            "primary_focus": primary_focus,
            "runtime_running": bool(running),
            "runtime_bot_count": int(runtime_bot_count),
            "active_runtime_count": int(active_runtime_count),
            "proposal_evidence_count": int(proposal_count),
            "execution_evidence_count": int(execution_count),
            "mall_queue_count": int(len(mall_items)),
            "quoted_count": int(quoted_count),
            "paid_count": int(paid_count),
            "cleared_count": int(cleared_count),
            "vaulted_count": int(vaulted_count),
            "open_positions": int(open_positions),
            "pending_human_relay": int(pending_relay),
            "working_capital": round(_safe_float(wallet.get("working_capital", 0)), 2),
            "vault_total": round(_safe_float(wallet.get("vault_total", 0)), 2),
            "signal_count": int(len(signals)),
        },
        "capital_plan": {
            "mall_ops_usd": 50,
            "lab_live_usd": 100,
            "reserve_usd": 50,
            "extra_reserve_usd": 300,
            "rule": "Use MALL as the primary engine, LAB as controlled upside, and do not unlock extra reserve until proof exists.",
        },
        "mall_playbook": {
            "thesis": "High-quality, operator-reviewed local service offers beat fantasy scale. Quote fast, follow up fast, vault cleared revenue.",
            "daily_targets": {
                "reviewed_leads": "10-20",
                "approved_outreach": "5-10",
                "quotes_sent": "2-4",
                "real_conversations": "1+",
            },
            "focus_bots": mall_focus,
        },
        "lab_playbook": {
            "thesis": "Keep LAB conservative until route, execution, and reconciliation evidence are real. Demo/shadow proof comes before scale.",
            "risk_rules": {
                "max_loss_per_trade_pct": "0.5%-1.0%",
                "max_daily_loss_pct": "2%",
                "max_weekly_loss_pct": "5%",
                "max_live_bots": "1-2",
            },
            "focus_bots": lab_focus,
        },
        "top_actions": _trim_actions(actions),
        "telegram_commands": [
            {"command": "/brief", "description": "Show the operator brief and top actions."},
            {"command": "/mall", "description": "Show MALL priorities, queue pressure, and close-focused tasks."},
            {"command": "/lab", "description": "Show LAB proof status, route evidence, and risk posture."},
            {"command": "/status", "description": "Show the narrow runtime state summary."},
        ],
        "manuals": build_manual_library(),
    }


def format_operator_brief_markdown(brief: dict[str, Any]) -> str:
    summary = brief.get("summary", {})
    capital = brief.get("capital_plan", {})
    actions = brief.get("top_actions", [])[:4]
    lines = [
        "<b>DeG£N$ — Operator Brief</b>",
        "",
        f"Primary focus: <b>{summary.get('primary_focus', 'MALL')}</b>",
        f"Runtime: {'RUNNING' if summary.get('runtime_running') else 'PAUSED'} · {summary.get('active_runtime_count', 0)}/{summary.get('runtime_bot_count', 0)} active",
        f"Evidence: {summary.get('proposal_evidence_count', 0)} proposals · {summary.get('execution_evidence_count', 0)} executions",
        f"MALL: {summary.get('mall_queue_count', 0)} queue · {summary.get('quoted_count', 0)} quoted · {summary.get('paid_count', 0)} paid · {summary.get('vaulted_count', 0)} vaulted",
        f"Capital: ${capital.get('mall_ops_usd', 0)} MALL · ${capital.get('lab_live_usd', 0)} LAB · ${capital.get('reserve_usd', 0)} reserve",
        "",
        "<b>Top actions</b>",
    ]
    if not actions:
        lines.append("• Hold steady and keep collecting proof.")
    else:
        for action in actions:
            suffix = f" ({action['route']})" if action.get("route") else ""
            lines.append(f"• {action['title']}{suffix} — {action['detail']}")
    return "\n".join(lines)


def format_lane_focus_markdown(brief: dict[str, Any], lane: str) -> str:
    normalized = str(lane or "").strip().lower()
    if normalized == "mall":
        playbook = brief.get("mall_playbook", {})
        focus = playbook.get("focus_bots", [])
        lines = [
            "<b>DeG£N$ — MALL Focus</b>",
            "",
            playbook.get("thesis", "Mall-first focus."),
            "",
            "<b>Daily targets</b>",
        ]
        for key, value in (playbook.get("daily_targets", {}) or {}).items():
            lines.append(f"• {key.replace('_', ' ')}: {value}")
        lines.append("")
        lines.append("<b>Focus bots</b>")
        for item in focus[:4]:
            lines.append(f"• {item['title']} — {item['goal']}")
        return "\n".join(lines)

    playbook = brief.get("lab_playbook", {})
    focus = playbook.get("focus_bots", [])
    lines = [
        "<b>DeG£N$ — LAB Focus</b>",
        "",
        playbook.get("thesis", "LAB-first focus."),
        "",
        "<b>Risk rules</b>",
    ]
    for key, value in (playbook.get("risk_rules", {}) or {}).items():
        lines.append(f"• {key.replace('_', ' ')}: {value}")
    lines.append("")
    lines.append("<b>Focus bots</b>")
    for item in focus[:4]:
        lines.append(f"• {item['title']} ({item.get('tier', 'T?')}) — {item['goal']}")
    return "\n".join(lines)
