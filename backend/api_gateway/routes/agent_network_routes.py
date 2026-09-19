"""Anonymous instance-wide discovery and direct A2A messaging.

These endpoints intentionally do not apply owner/private visibility filtering.
They must only be exposed on a trusted network until authentication is added.
"""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from api_gateway.dependencies import (
    AgentNetworkDependency,
    DirectAgentExecutionDependency,
)
from api_gateway.registry import mark_declared_owner
from common.errors import AppError
from models.agent_network import (
    AgentDiscoveryResponse,
    AgentMessageRequest,
    AgentMessageResponse,
    AgentNetworkErrorDetail,
    AgentNetworkErrorResponse,
    NetworkIdentifier,
)

router = APIRouter()

_ERROR_STATUS = {
    "NOT_FOUND": 404,
    "CONFLICT": 409,
    "VALIDATION": 422,
    "UPSTREAM": 502,
    "UPSTREAM_TIMEOUT": 504,
}


def _http_error(
    error: AppError, request: AgentMessageRequest | None = None
) -> HTTPException:
    detail = AgentNetworkErrorDetail(
        code=error.code,
        message=error.message,
        agent_id=request.agent_id if request else None,
        client_request_id=request.client_request_id if request else None,
    )
    return HTTPException(
        status_code=_ERROR_STATUS.get(error.code, 500),
        detail=detail.model_dump(exclude_none=True),
    )


@router.get(
    "/agents/discovery",
    responses={status: {"model": AgentNetworkErrorResponse} for status in (404, 422)},
)
async def discover_agents(
    network: AgentNetworkDependency,
    group_id: Annotated[NetworkIdentifier | None, Query()] = None,
) -> AgentDiscoveryResponse:
    """Return stored cards for active agents, optionally scoped to a saved team.

    Anonymous access includes private agents. No network scan or card refresh is
    performed. `all_agents` selects all; `room_team` requires room context and
    is not supported by this endpoint.
    """
    try:
        return await network.discover(group_id)
    except AppError as exc:
        raise _http_error(exc) from exc


@router.post(
    "/agents/messages",
    responses={
        status: {"model": AgentNetworkErrorResponse}
        for status in (404, 409, 422, 502, 504)
    },
)
async def send_agent_message(
    request: AgentMessageRequest,
    execution: DirectAgentExecutionDependency,
) -> AgentMessageResponse:
    """Send one A2A message to a registered active agent without creating a room.

    `result` preserves the remote A2A Message or Task, including nonterminal and
    failed task states. The call waits at most 600 seconds. A timeout does not
    cancel remote work; requests are not automatically resent. Request IDs
    provide correlation, not idempotency. Anonymous access has no owner isolation.
    """
    try:
        return await execution.send(request)
    except AppError as exc:
        raise _http_error(exc, request) from exc


mark_declared_owner(router, __name__)
