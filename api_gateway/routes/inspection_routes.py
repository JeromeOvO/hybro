from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.params import Depends as DependsParam
from loguru import logger

from agent.protocols import AgentInspection
from api_gateway.dependencies import get_inspection_center
from api_gateway.registry import mark_declared_owner as _mark_declared_owner
from models.request import InspectionCenterRequest

router = APIRouter()


def _resolve_dependency(value: Any, provider) -> Any:
    if isinstance(value, DependsParam):
        return provider()
    return value


@router.post("/inspectionCenter/inspectAgentCard")
async def inspect_agent(
    request: Request,
    center: AgentInspection = Depends(get_inspection_center),
):
    request_data = await request.json()
    agent_url = request_data.get("agent_url")
    if not agent_url:
        raise HTTPException(status_code=400, detail="agent_url is required")
    center = _resolve_dependency(center, get_inspection_center)
    logger.info("inspectionCenter/inspect request: {}", agent_url)
    inspection_center_request = InspectionCenterRequest(agent_url=agent_url)
    inspection_center_response = await center.inspect_agent_card(
        inspection_center_request
    )
    return inspection_center_response


@router.post("/inspectionCenter/inspectA2AConnection")
async def inspect_a2a_connection(
    request: Request,
    center: AgentInspection = Depends(get_inspection_center),
):
    request_data = await request.json()
    agent_url = request_data.get("agent_url")
    if not agent_url:
        raise HTTPException(status_code=400, detail="agent_url is required")
    center = _resolve_dependency(center, get_inspection_center)
    logger.info("inspectionCenter/inspectA2AConnection request: {}", agent_url)
    inspection_center_request = InspectionCenterRequest(agent_url=agent_url)
    inspection_center_response = await center.inspect_a2a_connection(
        inspection_center_request
    )
    return inspection_center_response


_mark_declared_owner(router, __name__)
