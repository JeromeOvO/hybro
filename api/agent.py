from fastapi import APIRouter, HTTPException, Request
from ibm_watsonx_ai import functions

from api.agent_viewset import AgentViewSet
from models.request import AgentCenterRequest
from modules.AgentCenter import AgentCenter

router = APIRouter()
agent_viewset = AgentViewSet()
router.include_router(agent_viewset.get_router())
agent_center = AgentCenter()


@router.post("/agent/getAgentCardFromUrl")
async def get_agent_card_from_url(request: Request):
    request_data = await request.json()
    agent_url = request_data.get("agent_url")

    if not agent_url:
        raise HTTPException(status_code=400, detail="agent_url is required")

    agent_center_request = AgentCenterRequest(agent_url=agent_url)
    agent_center_response = await agent_center.get_agent_card_from_url(
        agent_center_request
    )
    agent_center_response_without_url = agent_center._mask_sensitive_information(
        agent_center_response, ["agent_url", "agent_card.url"]
    )
    return agent_center_response_without_url


@router.post("/agent/registerAgent")
async def register_agent(request: Request):
    request_data = await request.json()
    agent_url = request_data.get("agent_url")
    provider_id = request_data.get("provider_id")

    if not agent_url:
        raise HTTPException(status_code=400, detail="agent_url is required")

    agent_center_request = AgentCenterRequest(agent_url=agent_url, provider_id=provider_id)
    agent_center_response = await agent_center.register_agent(agent_center_request)
    agent_center_response_without_url = agent_center._mask_sensitive_information(
        agent_center_response, ["agent_url", "agent_card.url"]
    )
    return agent_center_response_without_url


@router.get("/agent/getAgent/{agent_id}")
async def get_agent(agent_id: str):
    if not agent_id:
        raise HTTPException(status_code=400, detail="agent_id is required")

    agent_center_request = AgentCenterRequest(agent_id=agent_id)
    agent_center_response = await agent_center.query_agent_by_agent_id(
        agent_center_request
    )
    agent_center_response_without_url = agent_center._mask_sensitive_information(
        agent_center_response,["agent_url","agent_card.url"]
    )
    return agent_center_response_without_url



@router.post("/agent/deleteAgent")
async def delete_agent(request: Request):
    request_data = await request.json()
    agent_id = request_data.get("agent_id")

    if not agent_id:
        raise HTTPException(status_code=400, detail="agent_id is required")

    agent_center_request = AgentCenterRequest(agent_id=agent_id)
    agent_center_response = await agent_center.remove_agent(agent_center_request)
    agent_center_response_without_url = agent_center._mask_sensitive_information(
        agent_center_response, ["agent_url", "agent_card.url"]
    )
    return agent_center_response_without_url


@router.get("/agent/getAllAgents")
async def get_agent_list():
    agent_center_request = AgentCenterRequest()
    agent_center_response = await agent_center.get_all_agents(agent_center_request)
    agent_center_response_without_url = agent_center._mask_sensitive_information(
        agent_center_response, ["agent_url", "agent_card.url"]
    )
    return agent_center_response_without_url


@router.post("/agent/getAgentListWithConditions")
async def get_agent_list_with_conditions():
    agent_center_request = AgentCenterRequest()
    agent_center_response = await agent_center.get_agents_with_conditions(
        agent_center_request
    )
    agent_center_response_without_url = agent_center._mask_sensitive_information(
        agent_center_response, ["agent_url", "agent_card.url"]
    )
    return agent_center_response_without_url


