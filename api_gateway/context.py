"""Shared API Gateway request-context helpers."""

from __future__ import annotations

from fastapi import Request


def request_id(request: Request) -> str | None:
    return request.headers.get("x-request-id")
