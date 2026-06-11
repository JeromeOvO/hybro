from __future__ import annotations

from typing import Any

from execution.hitl.service import BoundHITLServiceProxy, HITLService


def create_hitl_service(**kwargs: Any) -> HITLService:
    constructor_kwargs = {}
    for name in ("continuation", "task_notifications"):
        if name in kwargs:
            constructor_kwargs[name] = kwargs.pop(name)
    service = HITLService(**constructor_kwargs)
    # Map legacy parameter names to new internal attribute names
    for name, value in kwargs.items():
        if name in ("database_service", "db_service"):
            setattr(service, "_store", value)
        elif name == "store":
            setattr(service, "_store", value)
        elif name == "delivery":
            setattr(service, "_delivery", value)
        elif name == "a2a_service":
            setattr(service, "_a2a_service", value)
        elif name == "continuation":
            setattr(service, "_continuation", value)
        elif name == "task_notifications":
            setattr(service, "_task_notifications", value)
        else:
            setattr(service, name, value)
    return service


__all__ = ["BoundHITLServiceProxy", "create_hitl_service"]
