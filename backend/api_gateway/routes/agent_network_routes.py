"""Authenticated, caller-visible discovery and direct A2A messaging."""

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
    AgentGroupListResponse,
    AgentMessageRequest,
    AgentMessageResponse,
    AgentNetworkErrorDetail,
    AgentNetworkErrorResponse,
    AgentProfile,
    AgentSearchResponse,
    NetworkIdentifier,
)

router = APIRouter()

_ERROR_STATUS = {
    "FORBIDDEN": 403,
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
    responses={
        401: {"description": "Authentication required"},
        **{status: {"model": AgentNetworkErrorResponse} for status in (403, 404, 422)},
    },
)
async def discover_agents(
    network: AgentNetworkDependency,
    group_id: Annotated[NetworkIdentifier | None, Query()] = None,
) -> AgentDiscoveryResponse:
    """Return stored cards for active agents, optionally scoped to a saved team.

    Only public agents and the caller's own private agents are visible. Saved
    groups must belong to the caller. No network scan or card refresh is performed.
    `all_agents` selects all visible agents; `room_team` is not supported.
    """
    try:
        return await network.discover(group_id)
    except AppError as exc:
        raise _http_error(exc) from exc


@router.get(
    "/agents/groups",
    responses={401: {"description": "Authentication required"}},
)
async def list_network_groups(
    network: AgentNetworkDependency,
) -> AgentGroupListResponse:
    """List the caller's saved groups (subnets) with active visible member counts.

    Built-in selections such as `all_agents` and `room_team` are not included.
    """
    return await network.list_groups()


@router.get(
    "/agents/search",
    responses={
        401: {"description": "Authentication required"},
        **{status: {"model": AgentNetworkErrorResponse} for status in (403, 404, 422)},
    },
)
async def search_agents(
    network: AgentNetworkDependency,
    query: Annotated[str, Query(min_length=1, max_length=500)],
    group_id: Annotated[NetworkIdentifier | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=20)] = 5,
) -> AgentSearchResponse:
    """Rank visible active agents for a capability query, optionally in a group.

    Results are compact consumer views with the caller's groups for each agent.
    Ranking is lexical over Card names, descriptions, skills, and tags; an
    empty list means no agent matched the query terms.
    """
    try:
        return await network.search(query, group_id, limit)
    except AppError as exc:
        raise _http_error(exc) from exc


@router.get(
    "/agents/{agent_id}/profile",
    responses={
        401: {"description": "Authentication required"},
        404: {"model": AgentNetworkErrorResponse},
    },
)
async def get_agent_profile(
    network: AgentNetworkDependency,
    agent_id: NetworkIdentifier,
) -> AgentProfile:
    """Return a visible agent's consumer profile without transport or security data."""
    try:
        return await network.get_profile(agent_id)
    except AppError as exc:
        raise _http_error(exc) from exc


@router.post(
    "/agents/messages",
    responses={
        401: {"description": "Authentication required"},
        413: {"description": "Request body exceeds 6 MiB"},
        429: {"description": "Direct-send concurrency limit reached"},
        **{
            status: {"model": AgentNetworkErrorResponse}
            for status in (403, 404, 409, 422, 502, 504)
        },
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
    provide correlation, not idempotency. Targets must be public or caller-owned;
    saved groups must belong to the caller. Request bodies are capped at 6 MiB,
    with at most four admitted sends per backend worker and no waiting queue.
    """
    try:
        return await execution.send(request)
    except AppError as exc:
        raise _http_error(exc, request) from exc


mark_declared_owner(router, __name__)
