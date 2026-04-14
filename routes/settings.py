from __future__ import annotations

from fastapi import APIRouter, Request

import server as runtime
from models import (
    DeviceApproveRequest,
    DeviceRenameRequest,
    DeviceRevokeRequest,
    NoteRequest,
    SettingsUpdateRequest,
    TelegramTestRequest,
)


router = APIRouter(tags=["settings"])

for method, path, handler in (
    ("GET", "/api/settings", runtime.get_settings),
    ("POST", "/api/telegram/webhook", runtime.telegram_webhook),
    ("POST", "/api/notifications/test-sms", runtime.test_sms),
    ("GET", "/api/vpn/status", runtime.vpn_status),
    ("POST", "/api/vpn/start", runtime.vpn_start),
    ("POST", "/api/vpn/renew", runtime.vpn_renew),
    ("GET", "/api/tunnel/status", runtime.tunnel_status),
    ("POST", "/api/tunnel/start", runtime.tunnel_start),
    ("POST", "/api/tunnel/stop", runtime.tunnel_stop),
    ("GET", "/api/devices", runtime.list_devices),
    ("GET", "/api/proxy/test", runtime.test_proxy),
    ("GET", "/api/notes", runtime.list_notes),
):
    router.add_api_route(path, handler, methods=[method])


@router.post("/api/telegram/test")
@runtime.limiter.limit("30/minute")
async def test_telegram(request: Request, body: TelegramTestRequest):
    return await runtime.test_telegram(request, body.to_body())


@router.post("/api/settings")
@runtime.limiter.limit("30/minute")
async def save_settings(request: Request, body: SettingsUpdateRequest):
    return await runtime.save_settings(request, body.to_body())


@router.post("/api/devices/approve")
@runtime.limiter.limit("30/minute")
async def approve_device(request: Request, body: DeviceApproveRequest):
    return await runtime.approve_device(request, body.to_body())


@router.post("/api/devices/revoke")
@runtime.limiter.limit("30/minute")
async def revoke_device(request: Request, body: DeviceRevokeRequest):
    return await runtime.revoke_device(request, body.to_body())


@router.post("/api/devices/rename")
@runtime.limiter.limit("30/minute")
async def rename_device(request: Request, body: DeviceRenameRequest):
    return await runtime.rename_device(request, body.to_body())


@router.post("/api/notes")
@runtime.limiter.limit("30/minute")
async def add_note(request: Request, body: NoteRequest):
    return await runtime.add_note(request, body.to_body())


@router.put("/api/notes/{nid}")
@runtime.limiter.limit("30/minute")
async def edit_note(nid: int, request: Request, body: NoteRequest):
    return await runtime.edit_note(nid, request, body.to_body())


@router.delete("/api/notes/{nid}")
@runtime.limiter.limit("30/minute")
async def remove_note(nid: int, request: Request):
    return await runtime.remove_note(nid, request)
