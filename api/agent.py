from fastapi import APIRouter, Depends, HTTPException, Request

from api.agent_viewset import AgentViewSet
from common.auth import ClerkUser, get_current_user
from models.request import AgentCenterRequest
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

    return agent_center_response


@router.post("/agent/deleteAgent")
async def delete_agent(
    request: Request,
    user: ClerkUser = Depends(get_current_user),
):
    """Delete an agent - PROTECTED (requires authentication)"""
    request_data = await request.json()
    agent_id = request_data.get("agent_id")

    if not agent_id:
        raise HTTPException(status_code=400, detail="agent_id is required")

    agent_center_request = AgentCenterRequest(agent_id=agent_id)
    agent_center_response = await agent_center.remove_agent(agent_center_request)

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
    return agent_center_response


@router.get("/agent/getAgent/{agent_id}")
async def get_agent(agent_id: str):
    """Get agent by ID - PUBLIC (no authentication required)"""
    if not agent_id:
        raise HTTPException(status_code=400, detail="agent_id is required")

    agent_center_request = AgentCenterRequest(agent_id=agent_id)
    agent_center_response = await agent_center.query_agent_by_agent_id(
        agent_center_request
    )

    return agent_center_response


@router.get("/agent/getAllAgents")
async def get_agent_list():
    """Get all agents - PUBLIC (no authentication required)"""
    agent_center_request = AgentCenterRequest()
    agent_center_response = await agent_center.get_all_agents(agent_center_request)
    return agent_center_response


@router.post("/agent/getAgentListWithConditions")
async def get_agent_list_with_conditions():
    """Get agents with conditions - PUBLIC (no authentication required)"""
    agent_center_request = AgentCenterRequest()
    agent_center_response = await agent_center.get_agents_with_conditions(
        agent_center_request
    )

    return agent_center_response
