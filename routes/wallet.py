from __future__ import annotations

from fastapi import APIRouter, Request

import server as runtime
from models import AmountRequest, VaultLockRequest


router = APIRouter(tags=["wallet"])

for method, path, handler in (
    ("GET", "/api/wallet", runtime.get_wallet),
    ("GET", "/api/wallet/transactions", runtime.get_wallet_transactions),
    ("GET", "/api/wallet/poly", runtime.get_poly_wallet),
    ("POST", "/api/wallet/poly/generate", runtime.generate_poly_wallet),
    ("GET", "/api/vault", runtime.get_vault),
    ("GET", "/api/ladder/status", runtime.get_ladder_status),
    ("GET", "/api/vault/history", runtime.vault_history),
):
    router.add_api_route(path, handler, methods=[method])


@router.post("/api/wallet/deposit")
@runtime.limiter.limit("30/minute")
async def deposit(request: Request, body: AmountRequest):
    return await runtime.deposit(request, body.to_body())


@router.post("/api/wallet/withdraw")
@runtime.limiter.limit("30/minute")
async def request_withdraw(request: Request, body: AmountRequest):
    return await runtime.request_withdraw(request, body.to_body())


@router.post("/api/vault/lock")
@runtime.limiter.limit("30/minute")
async def lock_to_vault(request: Request, body: VaultLockRequest):
    return await runtime.lock_to_vault(request, body.to_body())
