import sys

from execution.hitl import service as _impl
from execution.hitl.factory import create_hitl_service
from execution.hitl.service import (
    MAX_HITL_ROUNDS,
    BoundHITLServiceProxy,
    ContinuationLostError,
    HITLService,
    hitl_service,
)


def bind_hitl_service(service: HITLService) -> None:
    _impl.hitl_service.bind(service)


def require_hitl_service() -> BoundHITLServiceProxy:
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
