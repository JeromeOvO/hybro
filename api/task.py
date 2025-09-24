from fastapi import APIRouter, HTTPException
from models.request import TaskCenterRequest
from modules.TaskCenter import TaskCenter

router = APIRouter()


@router.get("/task/queryTask/{task_id}")
async def query_task(task_id: str):
    task_center = TaskCenter()

    if not task_id:
        raise HTTPException(status_code=400, detail="task_id is required")

    task_center_request = TaskCenterRequest(task_id=task_id)
    task_center_response = await task_center.query_meta_task_by_task_id(
        task_center_request
    )

    return task_center_response


@router.get("/task/queryBaseTask/{task_id}")
async def query_base_task(task_id: str):
    task_center = TaskCenter()

    if not task_id:
        raise HTTPException(status_code=400, detail="task_id is required")

    task_center_request = TaskCenterRequest(task_id=task_id)
    task_center_response = await task_center.query_base_task_by_task_id(
        task_center_request
    )
    return task_center_response


@router.get("/task/getAllSessions/{user_name}")
async def get_all_sessions(user_name: str):
    task_center = TaskCenter()
    task_center_request = TaskCenterRequest(user_name=user_name)
    task_center_response = await task_center.query_all_sessions(task_center_request)
    return task_center_response


@router.get("/task/getBaseTasksBySessionId/{session_id}")
async def get_base_task_by_session_id(session_id: str):
    task_center = TaskCenter()
    task_center_request = TaskCenterRequest(session_id=session_id)
    task_center_response = await task_center.query_base_tasks_by_session_id(
        task_center_request
    )
    return task_center_response


@router.get("/task/getMetaTasksByParentTaskId/{parent_task_id}")
async def get_meta_tasks_by_parent_task_id(parent_task_id: str):
    task_center = TaskCenter()
    task_center_request = TaskCenterRequest(parent_task_id=parent_task_id)
    task_center_response = await task_center.query_meta_tasks_by_parent_task_id(
        task_center_request
    )
    return task_center_response
