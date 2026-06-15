"""Shared API Gateway error mapping helpers."""

from __future__ import annotations

from fastapi import HTTPException


def internal_server_error(message: str = "Internal server error") -> HTTPException:
    return HTTPException(status_code=500, detail=message)
