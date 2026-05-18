import sys

from execution.hitl import service as _impl
from execution.hitl.service import (
    ContinuationLostError,
    HITLService,
    MAX_HITL_ROUNDS,
    hitl_service,
)
from execution.hitl.factory import BoundHITLServiceProxy, create_hitl_service


def bind_hitl_service(service: HITLService) -> None:
    _impl.hitl_service = service


def require_hitl_service() -> HITLService:
    return _impl.hitl_service


__all__ = [
    "BoundHITLServiceProxy",
    "ContinuationLostError",
    "HITLService",
    "MAX_HITL_ROUNDS",
    "bind_hitl_service",
    "create_hitl_service",
    "hitl_service",
    "require_hitl_service",
]

_impl.BoundHITLServiceProxy = BoundHITLServiceProxy
_impl.bind_hitl_service = bind_hitl_service
_impl.create_hitl_service = create_hitl_service
_impl.require_hitl_service = require_hitl_service
_impl.__all__ = __all__
sys.modules[__name__] = _impl
