from fastapi import APIRouter, HTTPException, Request
from modules.InspectionCenter import InspectionCenter
from models.request import InspectionCenterRequest
from loguru import logger

router = APIRouter()


@router.post("/inspectionCenter/inspectAgentCard")
async def inspect_agent(request: Request):
    inspection_center = InspectionCenter()
    request_data = await request.json()
    agent_url = request_data.get("agent_url")
    if not agent_url:
        raise HTTPException(status_code=400, detail="agent_url is required")
    logger.info("inspectionCenter/inspect request: {}", agent_url)
    inspection_center_request = InspectionCenterRequest(agent_url=agent_url)
    inspection_center_response = await inspection_center.inspect_agent_card(
        inspection_center_request
    )
    return inspection_center_response


@router.post("/inspectionCenter/inspectA2AConnection")
async def inspect_a2a_connection(request: Request):
    inspection_center = InspectionCenter()
    request_data = await request.json()
    agent_url = request_data.get("agent_url")
    if not agent_url:
        raise HTTPException(status_code=400, detail="agent_url is required")
    logger.info("inspectionCenter/inspectA2AConnection request: {}", agent_url)
    inspection_center_request = InspectionCenterRequest(agent_url=agent_url)
    inspection_center_response = await inspection_center.inspect_a2a_connection(
        inspection_center_request
    )
    return inspection_center_response
