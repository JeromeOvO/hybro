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
    service = HITLService()
    dependency_attrs = {
        "database_service": "_db_service",
        "db_service": "_db_service",
        "sse_manager": "_sse_manager",
        "a2a_service": "_a2a_service",
    }
    for name, value in kwargs.items():
        setattr(service, dependency_attrs.get(name, name), value)
    return service


__all__ = ["BoundHITLServiceProxy", "create_hitl_service"]
