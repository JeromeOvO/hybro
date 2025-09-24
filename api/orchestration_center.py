from fastapi import APIRouter, HTTPException, Request
from models.request import OrchestrationCenterRequest
from modules.OrchestrationCenter import OrchestrationCenter

router = APIRouter()


@router.post("/orchestrationCenter/decomposeTask")
async def decompose_task(request: Request):
    orchestration_center = OrchestrationCenter()
    request_data = await request.json()
    task_id = request_data.get("task_id")

    if not task_id:
        raise HTTPException(status_code=400, detail="task_id is required")

    orchestration_center_request = OrchestrationCenterRequest(task_id=task_id)
    orchestration_center_response = await orchestration_center.decompose_task(
        orchestration_center_request
    )

    return orchestration_center_response


@router.post("/orchestrationCenter/assignAgentsToMetaTasks")
async def assign_agents_to_meta_tasks_by_parent_task_id(request: Request):
    orchestration_center = OrchestrationCenter()
    request_data = await request.json()
    task_id = request_data.get("task_id")

    if not task_id:
        raise HTTPException(status_code=400, detail="task_id is required")

    orchestration_center_request = OrchestrationCenterRequest(task_id=task_id)
    orchestration_center_response = (
        await orchestration_center.assign_agents_metatasks_by_parent_task_id(
            orchestration_center_request
        )
    )

    return orchestration_center_response


@router.post("/orchestrationCenter/assignAgentToMetaTask")
async def assign_agent_to_meta_task(request: Request):
    orchestration_center = OrchestrationCenter()
    request_data = await request.json()
    task_id = request_data.get("task_id")

    if not task_id:
        raise HTTPException(status_code=400, detail="task_id is required")

    orchestration_center_request = OrchestrationCenterRequest(task_id=task_id)
    orchestration_center_response = (
        await orchestration_center.assign_agent_to_meta_task(
            orchestration_center_request
        )
    )

    return orchestration_center_response


@router.post("/orchestrationCenter/runWorkflow")
async def run_workflow(request: Request):
    orchestration_center = OrchestrationCenter()
    request_data = await request.json()
    task_id = request_data.get("task_id")

    if not task_id:
        raise HTTPException(status_code=400, detail="task_id is required")

    orchestration_center_request = OrchestrationCenterRequest(task_id=task_id)
    orchestration_center_response = await orchestration_center.run_workflow(
        orchestration_center_request
    )

    return orchestration_center_response


@router.post("/orchestrationCenter/retryMetaTask")
async def retry_meta_task(request: Request):
    orchestration_center = OrchestrationCenter()
    request_data = await request.json()
    task_id = request_data.get("task_id")

    if not task_id:
        raise HTTPException(status_code=400, detail="task_id is required")

    orchestration_center_request = OrchestrationCenterRequest(task_id=task_id)
    orchestration_center_response = await orchestration_center.process_meta_task(
        orchestration_center_request
    )

    return orchestration_center_response


@router.post("/orchestrationCenter/summarizeMetaTaskForBaseTask")
async def summarize_meta_task_for_base_task(request: Request):
    orchestration_center = OrchestrationCenter()
    request_data = await request.json()
    task_id = request_data.get("task_id")

    if not task_id:
        raise HTTPException(status_code=400, detail="task_id is required")

    orchestration_center_request = OrchestrationCenterRequest(task_id=task_id)
    orchestration_center_response = (
        await orchestration_center.summarize_meta_task_for_base_task(
            orchestration_center_request
        )
    )

    return orchestration_center_response


@router.post("/orchestrationCenter/processRoomUserMessage")
async def process_room_user_message(request: Request):
    orchestration_center = OrchestrationCenter()
    request_data = await request.json()
    room_id = request_data.get("room_id")
    room_user_message_id = request_data.get("room_user_message_id")
    room_related_message_id = request_data.get("room_related_message_id")
    orchestration_center_request = OrchestrationCenterRequest(
        room_id=room_id,
        room_user_message_id=room_user_message_id,
        room_related_message_id=room_related_message_id,
    )
    orchestration_center_response = (
        await orchestration_center.process_room_user_message(
            orchestration_center_request
        )
    )
    return orchestration_center_response
