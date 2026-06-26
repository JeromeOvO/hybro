from execution.hitl.factory import create_hitl_service
from execution.hitl.service import (
    MAX_HITL_ROUNDS,
    BoundHITLServiceProxy,
    ContinuationLostError,
    HITLService,
)

hitl_service = BoundHITLServiceProxy()


def bind_hitl_service(service: HITLService) -> None:
    hitl_service.bind(service)


def require_hitl_service() -> BoundHITLServiceProxy:
    return hitl_service


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
