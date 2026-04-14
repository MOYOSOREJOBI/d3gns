from __future__ import annotations

import json
import logging
import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

import server as runtime

logger = logging.getLogger(__name__)

router = APIRouter(tags=["mrkrabs"])

_SYSTEM_PROMPT = """You are Mr. Krabs, the AI trading-bot assistant embedded in the DeG£N$ dashboard.
Your job is to help the operator understand their bots' performance, give sharp actionable insights,
and answer questions about the live trading system.

Personality: direct, confident, slightly salty like the cartoon character — but professional when it counts.
You do NOT place bets, delete bots, or perform any destructive action.
You DO explain P&L, strategies, risk, projections, simulator results, and give concise recommendations.
Never invent numbers — only reference the dashboard snapshot provided in the system context.
Keep answers under 120 words unless a detailed breakdown is explicitly asked for.
"""


class MrKrabsChatRequest(BaseModel):
    message: str
    context: dict[str, Any] | None = None
    history: list[dict[str, str]] | None = None


def _build_context_block(ctx: dict[str, Any] | None) -> str:
    if not ctx:
        return ""
    try:
        return "\n\n<dashboard_snapshot>\n" + json.dumps(ctx, default=str, indent=2) + "\n</dashboard_snapshot>"
    except Exception:
        return ""


def _openai_client():
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise HTTPException(status_code=503, detail="OPENAI_API_KEY not configured — add it to .env and restart.")
    try:
        from openai import OpenAI
        return OpenAI(api_key=api_key)
    except ImportError:
        raise HTTPException(status_code=503, detail="openai package not installed — run: pip install 'openai>=1.30.0'")


@router.post("/api/mrkrabs/chat")
@runtime.limiter.limit("30/minute")
async def mrkrabs_chat(request: Request, body: MrKrabsChatRequest):
    """Send a message to Mr. Krabs (GPT-4o) with live dashboard context."""
    client = _openai_client()

    ctx_block = _build_context_block(body.context)
    system_content = _SYSTEM_PROMPT + ctx_block

    messages: list[dict[str, str]] = [{"role": "system", "content": system_content}]

    # Include prior conversation turns (max 10 to control token usage)
    for turn in (body.history or [])[-10:]:
        role = turn.get("role", "user")
        text = turn.get("text") or turn.get("content") or ""
        if role in ("user", "assistant") and text:
            messages.append({"role": role, "content": text})

    messages.append({"role": "user", "content": body.message})

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            max_tokens=300,
            temperature=0.7,
        )
        reply = response.choices[0].message.content.strip()
        return {"ok": True, "reply": reply}
    except Exception as exc:
        logger.error(f"[mrkrabs] OpenAI error: {exc}")
        raise HTTPException(status_code=502, detail=f"OpenAI request failed: {exc}")
