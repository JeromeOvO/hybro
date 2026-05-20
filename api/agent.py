from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile
from uuid import uuid4

from api.agent_viewset import AgentViewSet
from app_shell.bound import (
    AgentCapabilityIssueStore,
    AgentCenterRouteOwner,
    AgentLivenessChecker,
    AgentLookup,
)
from common.auth import ClerkUser, get_current_user, get_optional_user, resolve_provider_name
from common.protocols import AgentAvatarManager
from models.agent import IssueStatus
from models.request import AgentCenterRequest, AgentSettingsUpdateRequest
from models.response import AgentCenterResponse

router = APIRouter()
agent_viewset = AgentViewSet()
router.include_router(agent_viewset.get_router())
agent_center: AgentCenterRouteOwner | None = None
agent_service: AgentLookup | None = None
capability_issue_service: AgentCapabilityIssueStore | None = None
agent_avatar_manager: AgentAvatarManager | None = None
agent_liveness_checker: AgentLivenessChecker | None = None


def bind_agent_dependencies(
    *,
    center: AgentCenterRouteOwner,
    service: AgentLookup,
    issue_service: AgentCapabilityIssueStore,
    avatar_manager: AgentAvatarManager,
) -> None:
    global agent_center, agent_service, capability_issue_service, agent_avatar_manager

    agent_center = center
    agent_service = service
    capability_issue_service = issue_service
    agent_avatar_manager = avatar_manager


def bind_agent_liveness_checker(checker: AgentLivenessChecker) -> None:
    global agent_liveness_checker

    agent_liveness_checker = checker


def _require_agent_center() -> AgentCenterRouteOwner:
    if agent_center is None:
        raise RuntimeError("Agent center dependency has not been bound")
    return agent_center


def _require_agent_service() -> AgentLookup:
    if agent_service is None:
        raise RuntimeError("Agent service dependency has not been bound")
    return agent_service


def _require_capability_issue_service() -> AgentCapabilityIssueStore:
    if capability_issue_service is None:
        raise RuntimeError("Capability issue dependency has not been bound")
    return capability_issue_service


def _require_agent_avatar_manager() -> AgentAvatarManager:
    if agent_avatar_manager is None:
        raise RuntimeError("Agent avatar dependency has not been bound")
    return agent_avatar_manager


def _require_agent_liveness_checker() -> AgentLivenessChecker:
    if agent_liveness_checker is None:
        raise RuntimeError("Agent liveness dependency has not been bound")
    return agent_liveness_checker


# ============= PROTECTED ENDPOINTS (Auth Required) =============


@router.post("/agent/registerAgent")
async def register_agent(
    request: Request,
    user: ClerkUser = Depends(get_current_user),
):
    """Register a new agent - PROTECTED (requires authentication)"""
    request_data = await request.json()
    agent_url = request_data.get("agent_url")
    # we should use current user's clerk id as provider_id
    provider_id = user.user_id

    if not agent_url:
        raise HTTPException(status_code=400, detail="agent_url is required")

    agent_center_request = AgentCenterRequest(
        agent_url=agent_url, provider_id=provider_id
    )
    agent_center_response = await agent_center.register_agent(agent_center_request)

    # Handle duplicate error from service layer
    if not agent_center_response.success and agent_center_response.status_code == 400:
        raise HTTPException(
            status_code=400,
            detail=agent_center_response.error,
        )

    return agent_center._mask_sensitive_information(
        agent_center_response, ["agent_url", "agent_card.url"]
    )


@router.get("/agent/getAgent/me")
async def get_agent_by_provider(
    user: ClerkUser = Depends(get_current_user),
):
    """Get agents by provider id - PROTECTED (requires authentication)"""
    provider_id = user.user_id
    if not provider_id:
        raise HTTPException(status_code=400, detail="provider_id is required")

    agent_center_request = AgentCenterRequest(provider_id=provider_id)
    agent_center_response = await agent_center.get_agents_by_provider_id(
        agent_center_request
    )

    # Resolve provider display name once for all agents (same owner)
    if agent_center_response.success and agent_center_response.agents:
        resolved_name = resolve_provider_name(provider_id)
        for agent in agent_center_response.agents:
            if not agent.agent_card.provider or not agent.agent_card.provider.organization:
                agent.provider_name = resolved_name

    return agent_center._mask_sensitive_information(
        agent_center_response, ["agent_url", "agent_card.url"]
    )


@router.post("/agent/deleteAgent")
async def delete_agent(
    request: Request,
    user: ClerkUser = Depends(get_current_user),
):
    """Delete an agent - PROTECTED (requires authentication and ownership)"""
    request_data = await request.json()
    agent_id = request_data.get("agent_id")

    if not agent_id or not agent_id.strip():
        raise HTTPException(status_code=400, detail="agent_id is required")

    # Verify the agent exists and user owns it
    existing_agent = await _require_agent_service().get_agent_by_agent_id(agent_id)
    if not existing_agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    if existing_agent.provider_id != user.user_id:
        raise HTTPException(
            status_code=403, detail="You do not have permission to delete this agent"
        )

    agent_center_request = AgentCenterRequest(agent_id=agent_id)
    agent_center_response = await agent_center.remove_agent(agent_center_request)

    return agent_center._mask_sensitive_information(
        agent_center_response, ["agent_url", "agent_card.url"]
    )


@router.put("/agent/updateAgent/{agent_id}")
async def update_agent(
    agent_id: str,
    request_body: AgentSettingsUpdateRequest,
    user: ClerkUser = Depends(get_current_user),
):
    """
    Update an agent's settings - PROTECTED (requires authentication and ownership)

    Allows updating agent settings including rate limits:
    - rate_limit_per_user_per_hour: Max requests per user per hour (null = unlimited)
    - rate_limit_system_per_hour: Max total requests per hour (null = unlimited)
    - agent_status: Agent status (active/inactive)
    """
    if not agent_id or not agent_id.strip():
        raise HTTPException(status_code=400, detail="agent_id is required")

    # Verify the agent exists and user owns it
    existing_agent = await _require_agent_service().get_agent_by_agent_id(agent_id)
    if not existing_agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    if existing_agent.provider_id != user.user_id:
        raise HTTPException(
            status_code=403, detail="You do not have permission to update this agent"
        )

    # Build update data from request, only including explicitly set fields
    update_data = {}
    request_dict = request_body.model_dump(exclude_unset=True)

    # Validate rate limits: must be None or positive integer (>= 1)
    if "rate_limit_per_user_per_hour" in request_dict:
        rate_limit_user = request_dict["rate_limit_per_user_per_hour"]
        if rate_limit_user is not None and rate_limit_user < 1:
            raise HTTPException(
                status_code=400,
                detail="rate_limit_per_user_per_hour must be null or >= 1",
            )
        update_data["rate_limit_per_user_per_hour"] = rate_limit_user

    if "rate_limit_system_per_hour" in request_dict:
        rate_limit_system = request_dict["rate_limit_system_per_hour"]
        if rate_limit_system is not None and rate_limit_system < 1:
            raise HTTPException(
                status_code=400,
                detail="rate_limit_system_per_hour must be null or >= 1",
            )
        update_data["rate_limit_system_per_hour"] = rate_limit_system

    if "agent_status" in request_dict:
        update_data["agent_status"] = request_dict["agent_status"]

    if "is_public" in request_dict:
        update_data["is_public"] = request_dict["is_public"]

    if not update_data:
        raise HTTPException(status_code=400, detail="No valid fields to update")

    # Create updated agent model
    updated_agent = existing_agent.model_copy(update=update_data)

    agent_center_request = AgentCenterRequest(agent_id=agent_id, agent=updated_agent)
    agent_center_response = await agent_center.update_agent(agent_center_request)

    return agent_center_response


_AVATAR_ALLOWED_TYPES = frozenset({"image/jpeg", "image/png", "image/webp", "image/gif"})
_AVATAR_MAX_BYTES = 5 * 1024 * 1024  # 5 MB
_AVATAR_EXT_MAP = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
}


@router.post("/agent/{agent_id}/avatar")
async def upload_agent_avatar(
    agent_id: str,
    file: UploadFile,
    user: ClerkUser = Depends(get_current_user),
):
    """Upload a custom avatar image for an agent - PROTECTED (requires ownership)

    Accepts multipart/form-data with a single `file` field.
    Stores the image under the agent-avatars/ S3 prefix (publicly readable)
    and persists the permanent URL to agent_card.iconUrl in MongoDB.

    Returns: { "iconUrl": "<permanent public URL>" }
    """
    existing_agent = await _require_agent_service().get_agent_by_agent_id(agent_id)
    if not existing_agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    if existing_agent.provider_id != user.user_id:
        raise HTTPException(
            status_code=403, detail="You do not have permission to update this agent"
        )

    if file.content_type not in _AVATAR_ALLOWED_TYPES:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type: {file.content_type}. Allowed: jpeg, png, webp, gif",
        )

    content = await file.read()
    if len(content) > _AVATAR_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Avatar image must be ≤ 5 MB")

    ext = _AVATAR_EXT_MAP[file.content_type]
    s3_key = f"agent-avatars/{agent_id}/{uuid4().hex}.{ext}"

    icon_url = await _require_agent_avatar_manager().store_avatar(
        agent_id=agent_id,
        s3_key=s3_key,
        content=content,
        content_type=file.content_type,
    )

    return {"iconUrl": icon_url}


# ============= CAPABILITY ISSUE ENDPOINTS (Auth Required) =============


@router.get("/agent/{agent_id}/capability-issues")
async def get_capability_issues(
    agent_id: str,
    status: str | None = Query(None, description="Filter by status: open or resolved"),
    limit: int = Query(100, ge=1, le=500, description="Max issues to return"),
    offset: int = Query(0, ge=0, description="Number of issues to skip"),
    user: ClerkUser = Depends(get_current_user),
):
    """Get capability issues for an agent - PROTECTED (requires ownership)"""
    existing_agent = await _require_agent_service().get_agent_by_agent_id(agent_id)
    if not existing_agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    if existing_agent.provider_id != user.user_id:
        raise HTTPException(
            status_code=403,
            detail="You do not have permission to view issues for this agent",
        )

    issue_status = None
    if status:
        try:
            issue_status = IssueStatus(status)
        except ValueError:
            raise HTTPException(
                status_code=400, detail="Invalid status. Use 'open' or 'resolved'."
            ) from None

    issues = await _require_capability_issue_service().get_issues_for_agent(
        agent_id, status=issue_status, limit=limit, offset=offset
    )
    return {"issues": [issue.model_dump(mode="json") for issue in issues]}


@router.post("/agent/{agent_id}/capability-issues/resolve-all")
async def resolve_all_capability_issues(
    agent_id: str,
    user: ClerkUser = Depends(get_current_user),
):
    """Bulk resolve all open capability issues for an agent - PROTECTED"""
    existing_agent = await _require_agent_service().get_agent_by_agent_id(agent_id)
    if not existing_agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    if existing_agent.provider_id != user.user_id:
        raise HTTPException(
            status_code=403,
            detail="You do not have permission to resolve issues for this agent",
        )

    count = await _require_capability_issue_service().resolve_all_for_agent(
        agent_id, user.user_id
    )
    return {"resolved_count": count}


@router.post("/agent/capability-issues/{issue_id}/resolve")
async def resolve_capability_issue(
    issue_id: str,
    user: ClerkUser = Depends(get_current_user),
):
    """Resolve a single capability issue - PROTECTED (requires ownership)"""
    issue = await _require_capability_issue_service().get_issue_by_id(issue_id)
    if not issue:
        raise HTTPException(status_code=404, detail="Issue not found")

    agent = await _require_agent_service().get_agent_by_agent_id(issue.agent_id)
    if not agent or agent.provider_id != user.user_id:
        raise HTTPException(
            status_code=403,
            detail="You do not have permission to resolve this issue",
        )

    result = await _require_capability_issue_service().resolve_issue(
        issue_id,
        user.user_id,
    )
    if not result:
        raise HTTPException(
            status_code=400, detail="Issue is already resolved or not found"
        )
    return {"issue": result.model_dump(mode="json")}


# ============= PUBLIC ENDPOINTS (No Auth Required) =============


@router.post("/agent/getAgentCardFromUrl")
async def get_agent_card_from_url(request: Request):
    """Get agent card from URL - PUBLIC (no authentication required)"""
    request_data = await request.json()
    agent_url = request_data.get("agent_url")

    if not agent_url:
        raise HTTPException(status_code=400, detail="agent_url is required")

    agent_center_request = AgentCenterRequest(agent_url=agent_url)
    agent_center_response = await agent_center.get_agent_card_from_url(
        agent_center_request
    )

    return agent_center._mask_sensitive_information(
        agent_center_response, ["agent_url", "agent_card.url"]
    )


@router.get("/agent/getAgent/{agent_id}")
async def get_agent(
    agent_id: str,
    user: ClerkUser | None = Depends(get_optional_user),
):
    """Get agent by ID - PUBLIC (authentication optional)"""
    if not agent_id:
        raise HTTPException(status_code=400, detail="agent_id is required")
    if not agent_id.strip():
        return AgentCenterResponse(
            agent_id=agent_id,
            success=False,
            error="agent_id is required",
            status_code=400,
        )

    user_id = user.user_id if user else None
    agent_center_request = AgentCenterRequest(agent_id=agent_id, user_id=user_id)
    agent_center_response = await agent_center.query_agent_by_agent_id(
        agent_center_request
    )

    if agent_center_response.success and agent_center_response.agent:
        agent_center_response.agent = await _require_agent_liveness_checker()(
            agent_center_response.agent
        )
        # Resolve provider display name from Clerk if agent_card has no provider
        agent = agent_center_response.agent
        if not agent.agent_card.provider or not agent.agent_card.provider.organization:
            agent.provider_name = resolve_provider_name(agent.provider_id)

    return agent_center._mask_sensitive_information(
        agent_center_response, ["agent_url", "agent_card.url"]
    )


@router.get("/agent/getAllAgents")
async def get_agent_list(user: ClerkUser | None = Depends(get_optional_user)):
    """Get all agents - PUBLIC (authentication optional)"""
    user_id = user.user_id if user else None
    agent_center_request = AgentCenterRequest(user_id=user_id)
    agent_center_response = await agent_center.get_all_agents(agent_center_request)

    return agent_center._mask_sensitive_information(
        agent_center_response, ["agent_url", "agent_card.url"]
    )


@router.get("/agent/getAllActiveAgents")
async def get_all_active_agents(user: ClerkUser | None = Depends(get_optional_user)):
    """Get all active agents - PUBLIC (authentication optional)

    Returns only agents with active status, filtering out inactive and deleted agents.
    If authenticated, also includes the user's private agents.
    """
    user_id = user.user_id if user else None
    agent_center_request = AgentCenterRequest(user_id=user_id)
    agent_center_response = await agent_center.get_all_active_agents(
        agent_center_request
    )
    return agent_center._mask_sensitive_information(
        agent_center_response, ["agent_url", "agent_card.url"]
    )


@router.post("/agent/getAgentListWithConditions")
async def get_agent_list_with_conditions(
    user: ClerkUser | None = Depends(get_optional_user),
):
    """Get agents with conditions - PUBLIC (authentication optional)"""
    user_id = user.user_id if user else None
    agent_center_request = AgentCenterRequest(user_id=user_id)
    agent_center_response = await agent_center.get_agents_with_conditions(
        agent_center_request
    )

    return agent_center._mask_sensitive_information(
        agent_center_response, ["agent_url", "agent_card.url"]
    )
