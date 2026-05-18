from __future__ import annotations

from typing import Any

from execution.hitl.service import HITLService


class BoundHITLServiceProxy:
    def __init__(self) -> None:
        self._service: HITLService | None = None

    def bind(self, service: HITLService) -> None:
        self._service = service

    def _require_service(self) -> HITLService:
        if self._service is None:
            raise RuntimeError("HITLService has not been bound at startup")
        return self._service

    def __getattr__(self, name: str) -> Any:
        return getattr(self._require_service(), name)


def create_hitl_service(**kwargs: Any) -> HITLService:
    return HITLService(**kwargs)


__all__ = ["BoundHITLServiceProxy", "create_hitl_service"]
