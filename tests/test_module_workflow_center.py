"""
Unit tests for WorkflowCenter module.

Tests cover:
- decompose_task: valid decomposition and JSON parse failure fallback
- assign_agents_metatasks_by_parent_task_id: batch agent assignment
- process_meta_task: success update and error transition
- _cancel_remaining_meta_tasks: skip terminal, cancel the rest
- _build_task_description_with_context: context injection
"""

import json

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from a2a.types import Task, TaskState, TaskStatus

from models.request import OrchestrationRequest
from models.response import OrchestrationResponse
from models.task import MetaTask, TaskDefaultValue
from modules.WorkflowCenter import WorkflowCenter


def _make_workflow_center() -> WorkflowCenter:
    wc = object.__new__(WorkflowCenter)
    wc.task_service = MagicMock()
    wc.openai_service = MagicMock()
    wc.agent_service = MagicMock()
    wc.a2a_service = MagicMock()
    wc.chat_memory_service = MagicMock()
    wc.database_service = MagicMock()
    wc.sse_manager = MagicMock()
    wc.agent_resolver = MagicMock()
    return wc


def _a2a_task(
    task_id: str = "t-1",
    state: TaskState = TaskState.submitted,
) -> Task:
    return Task(
        id=task_id,
        contextId=f"ctx-{task_id}",
        status=TaskStatus(state=state),
    )


def _meta_task(
    task_id: str = "mt-1",
    parent_task_id: str = "root-1",
    agent_id: str = "agent-1",
    description: str = "Do something",
    task: Task | None = None,
    depends_on: list[str] | None = None,
    context: dict | None = None,
) -> MetaTask:
    return MetaTask(
        task_id=task_id,
        parent_task_id=parent_task_id,
        agent_id=agent_id,
        task_description=description,
        task=task,
        depends_on_tasks=depends_on,
        context_from_previous=context,
    )


# =============================================================================
# decompose_task Tests
# =============================================================================


class TestDecomposeTask:
    @pytest.mark.asyncio
    async def test_decompose_task_returns_meta_tasks(self):
        wc = _make_workflow_center()

        base_task = MagicMock()
        base_task.user_name = "alice"
        base_task.session_id = "sess-1"
        part_root = MagicMock()
        part_root.kind = "text"
        part_root.text = "Build a dashboard"
        part = MagicMock()
        part.root = part_root
        msg = MagicMock()
        msg.parts = [part]
        base_task.task = MagicMock()
        base_task.task.history = [msg]

        query_result = MagicMock()
        query_result.base_task = base_task
        wc.task_service.query_base_task_by_task_id = AsyncMock(return_value=query_result)

        chat_ctx = MagicMock()
        chat_ctx.success = True
        chat_ctx.chat_context = MagicMock()
        chat_ctx.chat_context.context_data = "prior context"
        wc.chat_memory_service.get_chat_context_by_session_id = AsyncMock(
            return_value=chat_ctx
        )

        meta_query = MagicMock()
        meta_query.success = False
        meta_query.meta_tasks = None
        wc.task_service.query_meta_tasks_by_parent_task_id = AsyncMock(
            return_value=meta_query
        )

        best_agent = MagicMock()
        best_agent.agent_id = "agent-best"
        resolver_result = MagicMock()
        resolver_result.agent = best_agent
        wc.agent_resolver.resolve = AsyncMock(return_value=resolver_result)

        decompose_response = json.dumps(
            {
                "execution_steps": [
                    {
                        "step_number": 1,
                        "step_description": "Gather requirements",
                        "execution_context": "Identify dashboard metrics",
                        "expected_output": "A list of metrics",
                        "depends_on_steps": [],
                    },
                    {
                        "step_number": 2,
                        "step_description": "Design layout",
                        "execution_context": "Create wireframe",
                        "expected_output": "Layout mockup",
                        "depends_on_steps": [1],
                    },
                ]
            }
        )
        wc.openai_service.decompose_task = AsyncMock(return_value=decompose_response)

        new_task = _a2a_task(task_id="new-t")
        wc.task_service.create_a2a_task = AsyncMock(return_value=new_task)

        wc.task_service.create_a2a_message = AsyncMock(return_value=MagicMock())

        create_resp = MagicMock()
        create_resp.success = True
        create_resp.task_id = "mt-new"
        wc.task_service.create_new_meta_task = AsyncMock(return_value=create_resp)

        request = OrchestrationRequest(task_id="root-1", user_id="u1")
        response = await wc.decompose_task(request)

        assert response.success is True
        assert response.task_id == "root-1"
        assert response.meta_task_ids is not None
        assert len(response.meta_task_ids) == 2
        assert wc.task_service.create_new_meta_task.call_count == 2

    @pytest.mark.asyncio
    async def test_decompose_task_fallback_on_llm_error(self):
        wc = _make_workflow_center()

        base_task = MagicMock()
        base_task.user_name = "bob"
        base_task.session_id = "sess-2"
        part_root = MagicMock()
        part_root.kind = "text"
        part_root.text = "Analyse data"
        part = MagicMock()
        part.root = part_root
        msg = MagicMock()
        msg.parts = [part]
        base_task.task = MagicMock()
        base_task.task.history = [msg]

        query_result = MagicMock()
        query_result.base_task = base_task
        wc.task_service.query_base_task_by_task_id = AsyncMock(return_value=query_result)

        chat_ctx = MagicMock()
        chat_ctx.success = True
        chat_ctx.chat_context = MagicMock()
        chat_ctx.chat_context.context_data = ""
        wc.chat_memory_service.get_chat_context_by_session_id = AsyncMock(
            return_value=chat_ctx
        )

        meta_query = MagicMock()
        meta_query.success = False
        meta_query.meta_tasks = None
        wc.task_service.query_meta_tasks_by_parent_task_id = AsyncMock(
            return_value=meta_query
        )

        best_agent = MagicMock()
        resolver_result = MagicMock()
        resolver_result.agent = best_agent
        wc.agent_resolver.resolve = AsyncMock(return_value=resolver_result)

        wc.openai_service.decompose_task = AsyncMock(
            return_value="NOT VALID JSON {{{bad"
        )

        request = OrchestrationRequest(task_id="root-2", user_id="u2")
        response = await wc.decompose_task(request)

        assert response.success is False
        assert response.task_id == "root-2"
        assert "Invalid JSON" in response.error


# =============================================================================
# assign_agents_metatasks_by_parent_task_id Tests
# =============================================================================


class TestAssignAgentsToMetaTasks:
    @pytest.mark.asyncio
    async def test_assign_agents_to_metatasks(self):
        wc = _make_workflow_center()

        mt1 = _meta_task(task_id="mt-1", description="Research topic")
        mt2 = _meta_task(task_id="mt-2", description="Write report")

        meta_query = MagicMock()
        meta_query.success = True
        meta_query.meta_tasks = [mt1, mt2]
        wc.task_service.query_meta_tasks_by_parent_task_id = AsyncMock(
            return_value=meta_query
        )

        task_query = MagicMock()
        task_query.meta_task = None
        wc.task_service.query_meta_task_by_task_id = AsyncMock(
            side_effect=lambda req: MagicMock(
                meta_task=_meta_task(task_id=req.task_id, description="desc")
            )
        )

        resolver_result = MagicMock()
        resolver_result.agent = MagicMock(agent_id="best-agent")
        wc.agent_resolver.resolve = AsyncMock(return_value=resolver_result)

        update_resp = MagicMock()
        update_resp.success = True
        wc.task_service.update_meta_task_by_task_id = AsyncMock(
            return_value=update_resp
        )

        request = OrchestrationRequest(task_id="root-1", user_id="u1")
        response = await wc.assign_agents_metatasks_by_parent_task_id(request)

        assert response.success is True
        assert response.meta_task_ids is not None
        assert set(response.meta_task_ids) == {"mt-1", "mt-2"}


# =============================================================================
# process_meta_task Tests
# =============================================================================


class TestProcessMetaTask:
    @pytest.mark.asyncio
    async def test_process_meta_task_success_updates_state(self):
        wc = _make_workflow_center()

        inner_task = _a2a_task(task_id="mt-1")
        mt = _meta_task(task_id="mt-1", agent_id="agent-1", task=inner_task)

        task_query = MagicMock()
        task_query.meta_task = mt
        wc.task_service.query_meta_task_by_task_id = AsyncMock(
            return_value=task_query
        )

        agent_obj = MagicMock()
        agent_obj.agent = MagicMock()
        agent_obj.agent.agent_card = MagicMock()
        wc.agent_service.query_agent_by_agent_id = AsyncMock(return_value=agent_obj)

        send_resp = MagicMock()
        wc.a2a_service.send_message_sync = AsyncMock(return_value=send_resp)

        process_resp = MagicMock()
        process_resp.kind = "task"
        wc.a2a_service.process_a2a_response = AsyncMock(return_value=process_resp)

        update_resp = MagicMock()
        update_resp.success = True
        wc.task_service.update_meta_task_by_task_id = AsyncMock(
            return_value=update_resp
        )

        request = OrchestrationRequest(task_id="mt-1", user_id="u1")
        response = await wc.process_meta_task(request)

        assert response.success is True
        assert response.task_id == "mt-1"
        wc.task_service.update_meta_task_by_task_id.assert_called_once()

    @pytest.mark.asyncio
    async def test_process_meta_task_error_transitions_to_failed(self):
        wc = _make_workflow_center()

        inner_task = _a2a_task(task_id="mt-2")
        mt = _meta_task(task_id="mt-2", agent_id="agent-1", task=inner_task)

        task_query = MagicMock()
        task_query.meta_task = mt
        wc.task_service.query_meta_task_by_task_id = AsyncMock(
            return_value=task_query
        )

        agent_obj = MagicMock()
        agent_obj.agent = MagicMock()
        agent_obj.agent.agent_card = MagicMock()
        wc.agent_service.query_agent_by_agent_id = AsyncMock(return_value=agent_obj)

        wc.a2a_service.send_message_sync = AsyncMock(
            side_effect=RuntimeError("connection refused")
        )

        request = OrchestrationRequest(task_id="mt-2", user_id="u1")
        response = await wc.process_meta_task(request)

        assert response.success is False
        assert response.task_id == "mt-2"
        assert "connection refused" in response.error


# =============================================================================
# _cancel_remaining_meta_tasks Tests
# =============================================================================


class TestCancelRemainingMetaTasks:
    @pytest.mark.asyncio
    async def test_cancel_remaining_meta_tasks(self):
        wc = _make_workflow_center()

        completed_task = _a2a_task(task_id="mt-done", state=TaskState.completed)
        mt_completed = _meta_task(task_id="mt-done", task=completed_task)

        pending_task = _a2a_task(task_id="mt-pending", state=TaskState.working)
        mt_pending = _meta_task(task_id="mt-pending", task=pending_task)

        submitted_task = _a2a_task(task_id="mt-submitted", state=TaskState.submitted)
        mt_submitted = _meta_task(task_id="mt-submitted", task=submitted_task)

        update_resp = MagicMock()
        update_resp.success = True
        wc.task_service.update_task_of_meta_task = AsyncMock(
            return_value=update_resp
        )

        await wc._cancel_remaining_meta_tasks(
            [mt_completed, mt_pending, mt_submitted]
        )

        assert wc.task_service.update_task_of_meta_task.call_count == 2

        assert mt_pending.task.status.state == TaskState.canceled
        assert mt_submitted.task.status.state == TaskState.canceled

        assert mt_completed.task.status.state == TaskState.completed


# =============================================================================
# _build_task_description_with_context Tests
# =============================================================================


class TestBuildTaskDescriptionWithContext:
    @pytest.mark.asyncio
    async def test_build_task_description_with_context(self):
        wc = _make_workflow_center()

        context = {
            "dep-task-1": {
                "task_description": "Gather requirements",
                "messages": ["Found 5 key metrics"],
                "artifacts": ["metrics.csv"],
            }
        }
        mt = _meta_task(
            task_id="mt-ctx",
            description="Design dashboard layout",
            context=context,
        )

        result = await wc._build_task_description_with_context(mt)

        assert "Design dashboard layout" in result
        assert "CONTEXT FROM PREVIOUS STEPS" in result
        assert "Gather requirements" in result
        assert "Found 5 key metrics" in result
        assert "metrics.csv" in result

    @pytest.mark.asyncio
    async def test_build_task_description_without_context_returns_base(self):
        wc = _make_workflow_center()

        mt = _meta_task(task_id="mt-plain", description="Simple task")

        result = await wc._build_task_description_with_context(mt)

        assert result == "Simple task"
