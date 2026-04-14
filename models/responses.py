from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AuthResponse(BaseModel):
    ok: bool = True
    token: str


class VersionResponse(BaseModel):
    version: str
    commit: str
    build_timestamp: str
    source: str


class ErrorPayload(BaseModel):
    ok: bool = False
    error: str
    detail: Any = None
    path: str = ""
    status_code: int = 500
    request_id: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)
