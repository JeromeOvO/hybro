"""
Discovery API Endpoints

Public API for external developers to discover agents using semantic search.
Requires API key authentication via X-API-Key header.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.params import Depends as DependsParam
from loguru import logger
from pydantic import BaseModel, Field

from api_gateway.dependencies import (
    get_discovery_default_limit,
    get_discovery_rate_limiter,
    get_discovery_service,
)
from api_gateway.registry import mark_declared_owner as _mark_declared_owner
from common.api_key_auth import get_api_key
from common.errors import PlatformRouteError
from common.protocols import APIKeyRateLimiter, GatewayDiscoveryProvider
from models.api_key import APIKey
from models.response import DiscoveryErrorResponse, DiscoveryResponse

router = APIRouter()


class DiscoveryRequest(BaseModel):
    """Request body for agent discovery endpoint."""

    query: str = Field(..., description="Text query to search for matching agents")
    limit: int | None = Field(
        default=None,
        ge=1,
        le=100,
        description="Maximum number of agents to return (default: 10, max: 100)",
    )


def _resolve_dependency(value: Any, provider) -> Any:
    if isinstance(value, DependsParam):
        return provider()
    return value


def _raise_http_error(error: PlatformRouteError) -> None:
    headers = None
    detail = error.detail
    if isinstance(error.detail, dict) and "retry_after" in error.detail:
        headers = {"Retry-After": str(error.detail["retry_after"])}
        detail = dict(error.detail)
        detail.pop("retry_after", None)
    raise HTTPException(
        status_code=error.status_code,
        detail=detail,
        headers=headers,
    ) from error


@router.post(
    "/discovery/agents",
    response_model=DiscoveryResponse,
    responses={
        401: {
            "model": DiscoveryErrorResponse,
            "description": "Invalid or missing API key",
        },
        429: {"model": DiscoveryErrorResponse, "description": "Rate limit exceeded"},
        500: {"model": DiscoveryErrorResponse, "description": "Internal server error"},
    },
    summary="Discover Agents",
    description="""
Search for agents based on a text query using semantic search.

Returns a list of A2A-compliant AgentCards for agents that match the query
with a similarity score above the confidence threshold.

**Authentication:** Requires `X-API-Key` header with a valid API key.

**Example Request:**
```json
{
    "query": "I need help with data analysis",
    "limit": 5
}
```

**Example Response:**
```json
{
    "query": "I need help with data analysis",
    "agents": [
        {
            "agent_card": { ... },
            "match_score": 0.92
        }
    ],
    "count": 1
}
```
    """,
)
async def discover_agents(
    request_body: DiscoveryRequest,
    api_key: APIKey = Depends(get_api_key),
    svc: GatewayDiscoveryProvider = Depends(get_discovery_service),
    rate_limiter: APIKeyRateLimiter = Depends(get_discovery_rate_limiter),
    default_limit: int = Depends(get_discovery_default_limit),
) -> DiscoveryResponse:
    """
    Discover agents based on a text query.

    Uses semantic search to find agents matching the query.
    Only returns agents with a match score >= threshold.

    Args:
        request_body: The discovery request with query and optional limit
        api_key: Validated API key (injected by dependency)

    Returns:
        DiscoveryResponse with matching agents and their scores

    Raises:
        HTTPException 401: Invalid or missing API key
        HTTPException 429: Rate limit exceeded
        HTTPException 500: Internal server error
    """
    # Log the request (anonymized)
    query_preview = (
        request_body.query[:50] + "..."
        if len(request_body.query) > 50
        else request_body.query
    )
    rate_limiter = _resolve_dependency(rate_limiter, get_discovery_rate_limiter)
    default_limit = _resolve_dependency(default_limit, get_discovery_default_limit)
    logger.info(
        f"Discovery API: Request from key {api_key.key_id[:8]}... | "
        f"Query: '{query_preview}' | Limit: {request_body.limit or default_limit}"
    )

    # Check rate limits before processing
    try:
        await rate_limiter.check_rate_limit(api_key)
    except PlatformRouteError as exc:
        _raise_http_error(exc)
    svc = _resolve_dependency(svc, get_discovery_service)

    try:
        result = await svc.discover_agents(
            query=request_body.query,
            limit=request_body.limit,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Discovery API: Error for key {api_key.key_id[:8]}... | Error: {str(e)}"
        )
        raise HTTPException(
            status_code=500,
            detail={
                "error": "internal_error",
                "message": "An unexpected error occurred while searching for agents",
            },
        ) from e

    await rate_limiter.record_request(api_key)

    logger.info(
        f"Discovery API: Returned {result.count} agents for key {api_key.key_id[:8]}..."
    )

    return result


_mark_declared_owner(router, __name__)
