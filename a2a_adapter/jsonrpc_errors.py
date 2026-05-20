from __future__ import annotations

from a2a.types import (
    ContentTypeNotSupportedError,
    JSONRPCResponse,
    UnsupportedOperationError,
)


def build_incompatible_types_error(request_id):
    return JSONRPCResponse(id=request_id, error=ContentTypeNotSupportedError())


def build_not_implemented_error(request_id):
    return JSONRPCResponse(id=request_id, error=UnsupportedOperationError())


__all__ = ["build_incompatible_types_error", "build_not_implemented_error"]
