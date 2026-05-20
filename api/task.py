from typing import Any

from fastapi import APIRouter
from fastapi.params import Depends as DependsParam
from fastapi.responses import JSONResponse

router = APIRouter()
task_center: Any | None = None
LEGACY_GONE_RESPONSES = {
    410: {
        "description": "Legacy task endpoint deprecated",
    }
}


def bind_task_dependencies(center: Any) -> None:
    global task_center

    task_center = center


def get_task_center() -> Any:
    if task_center is None:
        raise RuntimeError("Task center dependency has not been bound")
    return task_center


def _resolve_dependency(value: Any, provider) -> Any:
    if isinstance(value, DependsParam):
        return provider()
    return value


def _legacy_task_gone() -> JSONResponse:
    return JSONResponse(
        status_code=410,
        content={
            "success": False,
            "error": (
                "This legacy task endpoint is deprecated. "
                "Use room and run APIs for active workflow state."
            ),
        },
    )


@router.get(
    "/task/queryTask/{task_id}",
    status_code=410,
    responses=LEGACY_GONE_RESPONSES,
)
async def query_task(
    task_id: str,
):
    return _legacy_task_gone()


@router.get(
    "/task/queryBaseTask/{task_id}",
    status_code=410,
    responses=LEGACY_GONE_RESPONSES,
)
async def query_base_task(
    task_id: str,
):
    return _legacy_task_gone()


@router.get(
    "/task/getAllSessions/{user_name}",
    status_code=410,
    responses=LEGACY_GONE_RESPONSES,
)
async def get_all_sessions(
    user_name: str,
):
    return _legacy_task_gone()


@router.get(
    "/task/getBaseTasksBySessionId/{session_id}",
    status_code=410,
    responses=LEGACY_GONE_RESPONSES,
)
async def get_base_task_by_session_id(
    session_id: str,
):
    return _legacy_task_gone()


@router.get(
    "/task/getMetaTasksByParentTaskId/{parent_task_id}",
    status_code=410,
    responses=LEGACY_GONE_RESPONSES,
)
async def get_meta_tasks_by_parent_task_id(
    parent_task_id: str,
):
    return _legacy_task_gone()
