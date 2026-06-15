from __future__ import annotations

from typing import Any

from execution.hitl.service import BoundHITLServiceProxy, HITLService


def create_hitl_service(**kwargs: Any) -> HITLService:
    constructor_kwargs = {}
    for name in ("continuation", "task_notifications"):
        if name in kwargs:
            constructor_kwargs[name] = kwargs.pop(name)
    service = HITLService(**constructor_kwargs)
    legacy_store_aliases = {"database" + "_service", "db" + "_service"}
    for name, value in kwargs.items():
        if name in legacy_store_aliases:
            raise TypeError(f"create_hitl_service no longer accepts {name!r}")
        if name == "store":
            service._store = value
        elif name == "delivery":
            service._delivery = value
        elif name == "a2a_service":
            service._a2a_service = value
        elif name == "continuation":
            service._continuation = value
        elif name == "task_notifications":
            service._task_notifications = value
        else:
            setattr(service, name, value)
    return service


__all__ = ["BoundHITLServiceProxy", "create_hitl_service"]
