from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from models.request import OrchestrationRequest
from models.response import OrchestrationResponse
from modules.RoomMessageCenter import room_message_center
from modules.WorkflowCenter import workflow_center

router = APIRouter()


@router.post("/orchestrationCenter/decomposeTask")
async def decompose_task(request: Request):
    request_data = await request.json()
    task_id = request_data.get("task_id")

    if not task_id:
        raise HTTPException(status_code=400, detail="task_id is required")

    orchestration_request = OrchestrationRequest(task_id=task_id)
    orchestration_response = await workflow_center.decompose_task(orchestration_request)

    return orchestration_response


@router.post("/orchestrationCenter/assignAgentsToMetaTasks")
async def assign_agents_to_meta_tasks_by_parent_task_id(request: Request):
    request_data = await request.json()
    task_id = request_data.get("task_id")

    if not task_id:
        raise HTTPException(status_code=400, detail="task_id is required")

    orchestration_request = OrchestrationRequest(task_id=task_id)
    orchestration_response = (
        await workflow_center.assign_agents_metatasks_by_parent_task_id(
            orchestration_request
        )
    )

    return orchestration_response


@router.post("/orchestrationCenter/assignAgentToMetaTask")
async def assign_agent_to_meta_task(request: Request):
    request_data = await request.json()
    task_id = request_data.get("task_id")

    if not task_id:
        raise HTTPException(status_code=400, detail="task_id is required")

    orchestration_request = OrchestrationRequest(task_id=task_id)
    orchestration_response = await workflow_center.assign_agent_to_meta_task(
        orchestration_request
    )

    return orchestration_response


@router.post("/orchestrationCenter/runWorkflow")
async def run_workflow(request: Request):
    request_data = await request.json()
    task_id = request_data.get("task_id")

    if not task_id:
        raise HTTPException(status_code=400, detail="task_id is required")

    orchestration_request = OrchestrationRequest(task_id=task_id)
    orchestration_response = await workflow_center.run_workflow(orchestration_request)

    return orchestration_response


@router.post("/orchestrationCenter/retryMetaTask")
async def retry_meta_task(request: Request):
    request_data = await request.json()
    task_id = request_data.get("task_id")

    if not task_id:
        raise HTTPException(status_code=400, detail="task_id is required")

    orchestration_request = OrchestrationRequest(task_id=task_id)
    orchestration_response = await workflow_center.process_meta_task(
        orchestration_request
    )

    return orchestration_response


@router.post("/orchestrationCenter/summarizeMetaTaskForBaseTask")
async def summarize_meta_task_for_base_task(request: Request):
    request_data = await request.json()
    task_id = request_data.get("task_id")

    if not task_id:
        raise HTTPException(status_code=400, detail="task_id is required")

    orchestration_request = OrchestrationRequest(task_id=task_id)
    orchestration_response = await workflow_center.summarize_meta_task_for_base_task(
        orchestration_request
    )

    return orchestration_response


@router.post("/orchestrationCenter/processRoomUserMessage")
async def process_room_user_message(
    request: Request, background_tasks: BackgroundTasks
):
    """
    Process a room user message asynchronously.

    This endpoint returns immediately after validation and queues the actual
    processing as a background task. Agent responses are delivered via SSE.
    """
    request_data = await request.json()
    room_id = request_data.get("room_id")
    room_user_message_id = request_data.get("room_user_message_id")
    room_related_message_id = request_data.get("room_related_message_id")

    # Validate required fields early
    if not room_id:
        return OrchestrationResponse(
            success=False,
            error="Room id is required",
            status_code=400,
        )
    if not room_user_message_id:
        return OrchestrationResponse(
            success=False,
            error="Room user message id is required",
            status_code=400,
        )

    orchestration_request = OrchestrationRequest(
        room_id=room_id,
        room_user_message_id=room_user_message_id,
        room_related_message_id=room_related_message_id,
    )

    # Queue the actual processing as a background task.
    # This returns immediately while agents process in the background.
    background_tasks.add_task(
        room_message_center.process_room_user_message, orchestration_request
    )

    # Return success immediately - actual results come via SSE
    return OrchestrationResponse(
        room_id=room_id,
        success=True,
        error=None,
        status_code=202,  # 202 Accepted - processing started
    )
