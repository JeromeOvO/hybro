from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.params import Depends as DependsParam

from models.request import TaskCenterRequest

router = APIRouter()
task_center: Any | None = None


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


@router.get("/task/queryTask/{task_id}")
async def query_task(
    task_id: str,
    center: Any = Depends(get_task_center),
):
    center = _resolve_dependency(center, get_task_center)
    if not task_id:
        raise HTTPException(status_code=400, detail="task_id is required")

    task_center_request = TaskCenterRequest(task_id=task_id)
    task_center_response = await center.query_meta_task_by_task_id(
        task_center_request
    )

    return task_center_response


@router.get("/task/queryBaseTask/{task_id}")
async def query_base_task(
    task_id: str,
    center: Any = Depends(get_task_center),
):
    center = _resolve_dependency(center, get_task_center)
    if not task_id:
        raise HTTPException(status_code=400, detail="task_id is required")

    task_center_request = TaskCenterRequest(task_id=task_id)
    task_center_response = await center.query_base_task_by_task_id(
        task_center_request
    )
    return task_center_response


@router.get("/task/getAllSessions/{user_name}")
async def get_all_sessions(
    user_name: str,
    center: Any = Depends(get_task_center),
):
    center = _resolve_dependency(center, get_task_center)
    task_center_request = TaskCenterRequest(user_name=user_name)
    task_center_response = await center.query_all_sessions(task_center_request)
    return task_center_response


@router.get("/task/getBaseTasksBySessionId/{session_id}")
async def get_base_task_by_session_id(
    session_id: str,
    center: Any = Depends(get_task_center),
):
    center = _resolve_dependency(center, get_task_center)
    task_center_request = TaskCenterRequest(session_id=session_id)
    task_center_response = await center.query_base_tasks_by_session_id(
        task_center_request
    )
    return task_center_response


@router.get("/task/getMetaTasksByParentTaskId/{parent_task_id}")
async def get_meta_tasks_by_parent_task_id(
    parent_task_id: str,
    center: Any = Depends(get_task_center),
):
    center = _resolve_dependency(center, get_task_center)
    task_center_request = TaskCenterRequest(parent_task_id=parent_task_id)
    task_center_response = await center.query_meta_tasks_by_parent_task_id(
        task_center_request
    )
    return task_center_response
