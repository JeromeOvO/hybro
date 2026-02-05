from fastapi import APIRouter, Depends, HTTPException, Request

from api.agent_viewset import AgentViewSet
from common.auth import ClerkUser, get_current_user, get_optional_user
from models.request import AgentCenterRequest, AgentSettingsUpdateRequest
from modules.AgentCenter import AgentCenter
from services.agent_service import agent_service

router = APIRouter()
agent_viewset = AgentViewSet()
router.include_router(agent_viewset.get_router())
agent_center = AgentCenter()


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

    # Check if current agent_url is already registered
    existing_agent = await agent_service.get_agent_by_url(agent_url)

    if existing_agent:
        raise HTTPException(
            status_code=400,
            detail="Agent with this URL is already registered",
        )
    agent_center_request = AgentCenterRequest(
        agent_url=agent_url, provider_id=provider_id
    )
    agent_center_response = await agent_center.register_agent(agent_center_request)

    return agent_center._mask_sensitive_information(agent_center_response, ["agent_url", "agent_card.url"])

@router.get("/agent/getAgent/me")
async def get_agent_by_provider(
        user: ClerkUser = Depends(get_current_user),
):
    """Get agents by provider id - PROTECTED (requires authentication)"""
    provider_id = user.user_id
    if not provider_id:
        raise HTTPException(status_code=400, detail="provider_id is required")

    agent_center_request = AgentCenterRequest(provider_id=provider_id)
    agent_center_response = await agent_center.get_agents_by_provider_id(agent_center_request)

    return agent_center._mask_sensitive_information(agent_center_response, ["agent_url", "agent_card.url"])

@router.post("/agent/deleteAgent")
async def delete_agent(
    request: Request,
    user: ClerkUser = Depends(get_current_user),
):
    """Delete an agent - PROTECTED (requires authentication and ownership)"""
    request_data = await request.json()
    agent_id = request_data.get("agent_id")

    if not agent_id:
        raise HTTPException(status_code=400, detail="agent_id is required")

    # Verify the agent exists and user owns it
    existing_agent = await agent_service.get_agent_by_agent_id(agent_id)
    if not existing_agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    if existing_agent.provider_id != user.user_id:
        raise HTTPException(
            status_code=403,
            detail="You do not have permission to delete this agent"
        )

    agent_center_request = AgentCenterRequest(agent_id=agent_id)
    agent_center_response = await agent_center.remove_agent(agent_center_request)

    return agent_center._mask_sensitive_information(agent_center_response, ["agent_url", "agent_card.url"])


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
    if not agent_id:
        raise HTTPException(status_code=400, detail="agent_id is required")

    # Verify the agent exists and user owns it
    existing_agent = await agent_service.get_agent_by_agent_id(agent_id)
    if not existing_agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    if existing_agent.provider_id != user.user_id:
        raise HTTPException(
            status_code=403,
            detail="You do not have permission to update this agent"
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
                detail="rate_limit_per_user_per_hour must be null or >= 1"
            )
        update_data["rate_limit_per_user_per_hour"] = rate_limit_user
    
    if "rate_limit_system_per_hour" in request_dict:
        rate_limit_system = request_dict["rate_limit_system_per_hour"]
        if rate_limit_system is not None and rate_limit_system < 1:
            raise HTTPException(
                status_code=400,
                detail="rate_limit_system_per_hour must be null or >= 1"
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

    return agent_center._mask_sensitive_information(agent_center_response, ["agent_url", "agent_card.url"])


@router.get("/agent/getAgent/{agent_id}")
async def get_agent(
    agent_id: str,
    user: ClerkUser | None = Depends(get_optional_user),
):
    """Get agent by ID - PUBLIC (authentication optional)"""
    if not agent_id:
        raise HTTPException(status_code=400, detail="agent_id is required")

    user_id = user.user_id if user else None
    agent_center_request = AgentCenterRequest(agent_id=agent_id, user_id=user_id)
    agent_center_response = await agent_center.query_agent_by_agent_id(
        agent_center_request
    )

    return agent_center._mask_sensitive_information(agent_center_response, ["agent_url", "agent_card.url"])


@router.get("/agent/getAllAgents")
async def get_agent_list(
    user: ClerkUser | None = Depends(get_optional_user)
):
    """Get all agents - PUBLIC (authentication optional)"""
    user_id = user.user_id if user else None
    agent_center_request = AgentCenterRequest(user_id=user_id)
    agent_center_response = await agent_center.get_all_agents(agent_center_request)

    return agent_center._mask_sensitive_information(agent_center_response, ["agent_url", "agent_card.url"])


@router.get("/agent/getAllActiveAgents")
async def get_all_active_agents(
    user: ClerkUser | None = Depends(get_optional_user)
):
    """Get all active agents - PUBLIC (authentication optional)
    
    Returns only agents with active status, filtering out inactive and deleted agents.
    If authenticated, also includes the user's private agents.
    """
    user_id = user.user_id if user else None
    agent_center_request = AgentCenterRequest(user_id=user_id)
    agent_center_response = await agent_center.get_all_active_agents(agent_center_request)
    return agent_center._mask_sensitive_information(agent_center_response, ["agent_url", "agent_card.url"])


@router.post("/agent/getAgentListWithConditions")
async def get_agent_list_with_conditions(
    user: ClerkUser | None = Depends(get_optional_user)
):
    """Get agents with conditions - PUBLIC (authentication optional)"""
    user_id = user.user_id if user else None
    agent_center_request = AgentCenterRequest(user_id=user_id)
    agent_center_response = await agent_center.get_agents_with_conditions(
        agent_center_request
    )

    return agent_center._mask_sensitive_information(agent_center_response, ["agent_url", "agent_card.url"])
