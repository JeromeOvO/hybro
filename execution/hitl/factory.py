from __future__ import annotations

from typing import Any

from execution.hitl.service import BoundHITLServiceProxy, HITLService


def create_hitl_service(**kwargs: Any) -> HITLService:
    constructor_kwargs = {}
    for name in ("continuation", "task_notifications"):
        if name in kwargs:
            constructor_kwargs[name] = kwargs.pop(name)
    service = HITLService(**constructor_kwargs)
    dependency_attrs = {
        "database_service": "_db_service",
        "db_service": "_db_service",
        "sse_manager": "_sse_manager",
        "a2a_service": "_a2a_service",
        "continuation": "_continuation",
        "task_notifications": "_task_notifications",
    }
    for name, value in kwargs.items():
        setattr(service, dependency_attrs.get(name, name), value)
    return service


__all__ = ["BoundHITLServiceProxy", "create_hitl_service"]
