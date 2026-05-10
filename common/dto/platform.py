from datetime import datetime
from typing import Any

from pydantic import Field

from common.dto.base import FrozenDTO


class RateLimitInfo(FrozenDTO):
    limit: int
    remaining: int
    reset_at: datetime
    scope: str | None = None


class FileMetadata(FrozenDTO):
    file_id: str
    room_id: str
    user_id: str
    s3_key: str
    mime_type: str
    file_name: str
    size_bytes: int
    created_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class GatewayRoute(FrozenDTO):
    agent_id: str
    gateway_url: str
    methods: list[str] = Field(default_factory=list)


class GatewayRequest(FrozenDTO):
    agent_id: str
    payload: dict[str, Any] = Field(default_factory=dict)
    user_id: str | None = None
    room_id: str | None = None


class GatewayResponse(FrozenDTO):
    status_code: int
    payload: dict[str, Any] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)


class RateLimitResult(FrozenDTO):
    allowed: bool
    info: RateLimitInfo | None = None
    reason: str | None = None


class FileInfo(FrozenDTO):
    file_id: str
    file_name: str
    mime_type: str
    size_bytes: int
    url: str | None = None


__all__ = [
    "FileInfo",
    "FileMetadata",
    "GatewayRequest",
    "GatewayResponse",
    "GatewayRoute",
    "RateLimitInfo",
    "RateLimitResult",
]
