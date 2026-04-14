from __future__ import annotations

from fastapi import APIRouter, Request

import server as runtime
from models import AuthRequest, AuthResponse, ChangePasswordRequest


router = APIRouter(tags=["auth"])


@router.post("/api/auth", response_model=AuthResponse)
@runtime.limiter.limit("5/minute")
async def auth(request: Request, body: AuthRequest):
    return await runtime.auth(request, body.to_body())


@router.post("/api/auth/change-password")
@runtime.limiter.limit("5/minute")
async def change_password(request: Request, body: ChangePasswordRequest):
    return await runtime.change_password(request, body.to_body())
