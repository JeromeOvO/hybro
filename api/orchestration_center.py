from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from common.auth import ClerkUser, get_current_user
from models.request import OrchestrationRequest
from models.response import OrchestrationResponse
from modules.RoomMessageCenter import room_message_center
from modules.WorkflowCenter import workflow_center

router = APIRouter()


async def _get_task_request(
    request: Request,
    user: ClerkUser = Depends(get_current_user),
) -> OrchestrationRequest:
    """Parse and validate task_id from the request body, returning an OrchestrationRequest.

    The authenticated user's ID is attached so downstream services can
    apply visibility filtering (e.g. private-agent access checks).
    """
    request_data = await request.json()
    task_id = request_data.get("task_id")
    if not task_id:
        raise HTTPException(status_code=400, detail="task_id is required")
    return OrchestrationRequest(task_id=task_id, user_id=user.user_id)


TaskRequestDep = Depends(_get_task_request)


@router.post("/orchestrationCenter/decomposeTask")
async def decompose_task(req: OrchestrationRequest = TaskRequestDep):
    return await workflow_center.decompose_task(req)


@router.post("/orchestrationCenter/assignAgentsToMetaTasks")
async def assign_agents_to_meta_tasks_by_parent_task_id(
    req: OrchestrationRequest = TaskRequestDep,
):
    return await workflow_center.assign_agents_metatasks_by_parent_task_id(req)


@router.post("/orchestrationCenter/assignAgentToMetaTask")
async def assign_agent_to_meta_task(req: OrchestrationRequest = TaskRequestDep):
    return await workflow_center.assign_agent_to_meta_task(req)


@router.post("/orchestrationCenter/runWorkflow")
async def run_workflow(req: OrchestrationRequest = TaskRequestDep):
    return await workflow_center.run_workflow(req)


@router.post("/orchestrationCenter/retryMetaTask")
async def retry_meta_task(req: OrchestrationRequest = TaskRequestDep):
    return await workflow_center.process_meta_task(req)


@router.post("/orchestrationCenter/summarizeMetaTaskForBaseTask")
async def summarize_meta_task_for_base_task(
    req: OrchestrationRequest = TaskRequestDep,
):
    return await workflow_center.summarize_meta_task_for_base_task(req)


@router.post(
    "/orchestrationCenter/processRoomUserMessage",
    deprecated=True,
)
async def process_room_user_message(
    request: Request,
    background_tasks: BackgroundTasks,
    user: ClerkUser = Depends(get_current_user),
):
    """
    **Deprecated.** Message processing is now triggered internally by sendMessage.
    This endpoint returns HTTP 410 Gone.
    """
    return JSONResponse(
        status_code=410,
        content={
            "success": False,
            "error": "This endpoint is deprecated. Message processing is triggered by sendMessage.",
        },
    )
