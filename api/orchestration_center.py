from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.params import Depends as DependsParam
from fastapi.responses import JSONResponse

from common.auth import ClerkUser, get_current_user
from models.request import OrchestrationRequest

router = APIRouter()
workflow_center: Any | None = None
LEGACY_GONE_RESPONSES = {
    410: {
        "description": "Legacy workflow endpoint deprecated",
    }
}


def bind_orchestration_dependencies(workflow: Any) -> None:
    global workflow_center

    workflow_center = workflow


def get_workflow_center() -> Any:
    if workflow_center is None:
        raise RuntimeError("Workflow center dependency has not been bound")
    return workflow_center


def _resolve_dependency(value: Any, provider) -> Any:
    if isinstance(value, DependsParam):
        return provider()
    return value


def _legacy_workflow_gone() -> JSONResponse:
    return JSONResponse(
        status_code=410,
        content={
            "success": False,
            "error": (
                "This legacy workflow endpoint is deprecated. "
                "Use room message APIs for active workflows."
            ),
        },
    )


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


@router.post(
    "/orchestrationCenter/decomposeTask",
    status_code=410,
    responses=LEGACY_GONE_RESPONSES,
)
async def decompose_task():
    return _legacy_workflow_gone()


@router.post(
    "/orchestrationCenter/assignAgentsToMetaTasks",
    status_code=410,
    responses=LEGACY_GONE_RESPONSES,
)
async def assign_agents_to_meta_tasks_by_parent_task_id():
    return _legacy_workflow_gone()


@router.post(
    "/orchestrationCenter/assignAgentToMetaTask",
    status_code=410,
    responses=LEGACY_GONE_RESPONSES,
)
async def assign_agent_to_meta_task():
    return _legacy_workflow_gone()


@router.post(
    "/orchestrationCenter/runWorkflow",
    status_code=410,
    responses=LEGACY_GONE_RESPONSES,
)
async def run_workflow():
    return _legacy_workflow_gone()


@router.post(
    "/orchestrationCenter/retryMetaTask",
    status_code=410,
    responses=LEGACY_GONE_RESPONSES,
)
async def retry_meta_task():
    return _legacy_workflow_gone()


@router.post(
    "/orchestrationCenter/summarizeMetaTaskForBaseTask",
    status_code=410,
    responses=LEGACY_GONE_RESPONSES,
)
async def summarize_meta_task_for_base_task():
    return _legacy_workflow_gone()


@router.post(
    "/orchestrationCenter/processRoomUserMessage",
    deprecated=True,
    status_code=410,
    responses=LEGACY_GONE_RESPONSES,
)
async def process_room_user_message(
):
    """
    **Deprecated.** Message processing is now triggered internally by sendMessage.
    This endpoint returns HTTP 410 Gone.
    """
    return _legacy_workflow_gone()
