import json
import uuid
from typing import Any

from a2a.types import Message, Part, Role, Task, TaskState, TaskStatus, TextPart

from common.utils.a2a_helpers import get_message_from_task, get_text_from_message
from common.utils.logger import get_logger
from models.error import (
    AgentNotAssignedError,
    AgentNotFoundError,
    TaskIdRequiredError,
    TaskNotFoundError,
)
from models.request import (
    AgentCenterRequest,
    ChatMemoryRequest,
    OrchestrationRequest,
    TaskCenterRequest,
)
from models.response import OrchestrationResponse
from models.task import MetaTask, TaskDefaultValue
from services.a2a_constants import SSEProcessingStatus, is_terminal_state
from services.a2a_service import a2a_service
from services.agent_resolver_service import agent_resolver_service
from services.agent_service import agent_service
from services.database_service import db_service
from services.memory_service import chat_memory_service
from services.openai_service import openai_service
from services.sse_services import sse_manager
from services.task_service import task_service

logger = get_logger(__name__)


class WorkflowCenter:
    """Task decomposition, agent assignment, workflow execution,
    and result summarization for MetaTask-based workflows."""

    def __init__(self):
        self.task_service = task_service
        self.openai_service = openai_service
        self.agent_service = agent_service
        self.a2a_service = a2a_service
        self.chat_memory_service = chat_memory_service
        self.database_service = db_service
        self.sse_manager = sse_manager
        self.agent_resolver = agent_resolver_service

    def _success_response(self, task_id: str, **kwargs) -> OrchestrationResponse:
        """Shorthand for a successful OrchestrationResponse."""
        return OrchestrationResponse(
            task_id=task_id, success=True, error=None, status_code=200, **kwargs
        )

    def _error_response(
        self, task_id: str, error: str, status_code: int = 500
    ) -> OrchestrationResponse:
        """Shorthand for a failed OrchestrationResponse."""
        return OrchestrationResponse(
            task_id=task_id, success=False, error=error, status_code=status_code
        )

    async def decompose_task(
        self, request: OrchestrationRequest
    ) -> OrchestrationResponse:
        """Decompose a root task into structured MetaTask subtasks using AI.

        Retrieves the base task, cleans up any existing meta tasks, uses OpenAI
        to generate execution steps, and creates MetaTasks with dependencies.

        Raises:
            TaskIdRequiredError: If task_id is missing.
            TaskNotFoundError: If the specified task is not found.
        """
        root_task_id = request.task_id
        if root_task_id is None:
            raise TaskIdRequiredError()

        query_result = await self.task_service.query_base_task_by_task_id(
            TaskCenterRequest(task_id=root_task_id)
        )
        base_task = query_result.base_task

        if base_task is None:
            raise TaskNotFoundError()

        # get chat context
        chat_context_response = (
            await self.chat_memory_service.get_chat_context_by_session_id(
                ChatMemoryRequest(
                    user_name=base_task.user_name, session_id=base_task.session_id
                )
            )
        )

        if not chat_context_response.success:
            return self._error_response(root_task_id, "Failed to get chat context")

        # Clean up any existing meta tasks before re-decomposition
        failed_deletions = await self._delete_existing_meta_tasks(root_task_id)
        if failed_deletions:
            return self._error_response(
                root_task_id,
                f"Failed to clean up {len(failed_deletions)} existing meta tasks before re-decomposition",
            )

        # Extract task goal/description from BaseTask
        task_description = self._get_first_text_from_task(base_task)

        best_agent = await self._select_best_agent_for_decomposition(
            task_description, user_id=request.user_id
        )
        if best_agent is None:
            return self._error_response(
                root_task_id,
                "No active agents available for task decomposition",
            )

        # Proceed with task decomposition
        decompose_task_response = await self.openai_service.decompose_task(
            base_task=base_task,
            context_data=chat_context_response.chat_context.context_data,
            best_agent=best_agent,
        )
        logger.info(
            "WorkflowCenter: decompose task response: %s", decompose_task_response
        )

        # Parse the OpenAI response and create MetaTasks
        try:
            response_data = json.loads(decompose_task_response)

            if "execution_steps" not in response_data:
                logger.error("Invalid response format: missing execution_steps")
                return self._error_response(
                    root_task_id,
                    "Invalid response format from OpenAI service",
                )

            first_step = response_data["execution_steps"][0]
            if (
                first_step.get("step_description") == "Analyze the task goal"
                or first_step.get("step_description")
                == "Error occurred during task decomposition"
            ):
                return self._error_response(
                    root_task_id,
                    "Failed to decompose task, please try again",
                )

            # Store created meta tasks to establish dependencies
            created_meta_tasks = {}

            for step in response_data["execution_steps"]:
                # Validate step structure
                if not all(
                    key in step
                    for key in [
                        "step_number",
                        "step_description",
                        "execution_context",
                        "expected_output",
                    ]
                ):
                    logger.warning("Skipping invalid step: %s", step)
                    continue

                # Combine step information into action-oriented task description
                task_description = (
                    f"YOUR TASK: {step['step_description']}\n\n"
                    f"CONTEXT: {step['execution_context']}\n\n"
                    f"REQUIRED OUTPUT: {step['expected_output']}\n\n"
                    f"IMPORTANT: Execute this task completely and provide the actual results. "
                    f"Do NOT just describe what should be done or outline a plan - actually perform the task and deliver concrete output."
                )

                # Create a new Task for the meta task
                meta_task_task = await self.task_service.create_a2a_task()
                if meta_task_task.history is None:
                    meta_task_task.history = []
                new_message = await self.task_service.create_a2a_message(
                    Role.user, task_description
                )
                meta_task_task.history.append(new_message)

                # Extract dependencies
                depends_on_steps = step.get("depends_on_steps", [])
                depends_on_task_ids = []

                # Convert step numbers to task IDs
                for dep_step_num in depends_on_steps:
                    if dep_step_num in created_meta_tasks:
                        depends_on_task_ids.append(created_meta_tasks[dep_step_num])

                # Create MetaTask with dependencies
                create_meta_task_response = (
                    await self.task_service.create_new_meta_task(
                        TaskCenterRequest(
                            parent_task_id=root_task_id,
                            user_name=base_task.user_name,
                            task=meta_task_task,
                            user_input=task_description,
                            execution_order=step["step_number"],
                            depends_on_tasks=depends_on_task_ids,  # NEW
                        )
                    )
                )

                # Store the mapping for dependency resolution
                if create_meta_task_response.success:
                    created_meta_tasks[step["step_number"]] = (
                        create_meta_task_response.task_id
                    )

            logger.info("Successfully created %s meta tasks", len(created_meta_tasks))

            return OrchestrationResponse(
                task_id=root_task_id,
                success=True,
                error=None,
                status_code=200,
                meta_task_ids=list(
                    created_meta_tasks.values()
                ),  # Add this field to response if needed
            )

        except json.JSONDecodeError as e:
            logger.error("Failed to parse OpenAI response as JSON: %s", e)
            return self._error_response(
                root_task_id,
                f"Invalid JSON response from OpenAI service: {str(e)}",
            )
        except Exception as e:
            logger.error("Error creating meta tasks: %s", e)
            return self._error_response(
                root_task_id,
                f"Failed to create meta tasks: {str(e)}",
            )

    async def assign_agents_metatasks_by_parent_task_id(
        self, request: OrchestrationRequest
    ) -> OrchestrationResponse:
        """Assign agents to all meta tasks under a parent task.

        Iterates over all MetaTasks for the given parent, calls
        assign_agent_to_meta_task for each, and returns batch results.

        Raises:
            TaskIdRequiredError: If task_id is missing.
        """
        parent_task_id = request.task_id
        if parent_task_id is None:
            raise TaskIdRequiredError()

        # Query all meta tasks under the parent task
        query_result = await self.task_service.query_meta_tasks_by_parent_task_id(
            TaskCenterRequest(parent_task_id=parent_task_id)
        )

        if not query_result.success:
            return self._error_response(parent_task_id, "Failed to query meta tasks")

        meta_tasks = query_result.meta_tasks
        if meta_tasks is None or len(meta_tasks) == 0:
            return OrchestrationResponse(
                task_id=parent_task_id,
                success=True,
                error="No meta tasks found under parent task",
                meta_task_ids=[],
                status_code=200,
            )

        # Track assignment results
        assigned_meta_task_ids = []
        failed_assignments = []

        # Assign agents to each meta task
        for meta_task in meta_tasks:
            try:
                # Call assign_agent_to_meta_task for each meta task
                assignment_response = await self.assign_agent_to_meta_task(
                    OrchestrationRequest(
                        task_id=meta_task.task_id, user_id=request.user_id
                    )
                )

                if assignment_response.success:
                    assigned_meta_task_ids.append(meta_task.task_id)
                    logger.info(
                        "WorkflowCenter: Successfully assigned agent %s to meta task %s",
                        assignment_response.agent_id,
                        meta_task.task_id,
                    )
                else:
                    failed_assignments.append(
                        {
                            "meta_task_id": meta_task.task_id,
                            "error": assignment_response.error,
                        }
                    )
                    logger.error(
                        "WorkflowCenter: Failed to assign agent to meta task %s: %s",
                        meta_task.task_id,
                        assignment_response.error,
                    )

            except Exception as e:
                failed_assignments.append(
                    {"meta_task_id": meta_task.task_id, "error": str(e)}
                )
                logger.error(
                    "WorkflowCenter: Exception while assigning agent to meta task %s: %s",
                    meta_task.task_id,
                    str(e),
                )

        # Determine overall success
        return self._build_batch_response(
            parent_task_id,
            assigned_meta_task_ids,
            failed_assignments,
            len(meta_tasks),
            "assigned",
        )

    async def assign_agent_to_meta_task(
        self, request: OrchestrationRequest
    ) -> OrchestrationResponse:
        """Assign the most suitable agent to a specific MetaTask.

        Uses the AgentResolverService to find the best accessible agent
        via vector similarity, LLM selection, and real-time health probing.

        Raises:
            TaskIdRequiredError: If task_id is missing.
            TaskNotFoundError: If the MetaTask is not found.
        """
        meta_task_id = request.task_id

        if meta_task_id is None:
            raise TaskIdRequiredError()

        query_result = await self.task_service.query_meta_task_by_task_id(
            TaskCenterRequest(task_id=meta_task_id)
        )
        meta_task = query_result.meta_task

        if meta_task is None:
            raise TaskNotFoundError()

        result = await self.agent_resolver.resolve(
            meta_task.task_description,
            count=3,
            use_llm_selection=True,
            user_id=request.user_id,
        )

        if result.agent is None:
            logger.warning(
                "WorkflowCenter: No accessible agent for meta task %s: %s",
                meta_task_id,
                result.failure_reason,
            )
            return self._error_response(
                meta_task_id,
                result.failure_reason or "No accessible agent available",
                status_code=200,
            )

        best_agent_id = result.agent.agent_id
        meta_task.agent_id = best_agent_id
        update_response = await self.task_service.update_meta_task_by_task_id(
            TaskCenterRequest(task_id=meta_task_id, meta_task=meta_task)
        )

        if update_response.success:
            return self._success_response(meta_task_id, agent_id=best_agent_id)
        else:
            return self._error_response(meta_task_id, "Failed to update meta task")

    async def run_workflow(
        self, request: OrchestrationRequest
    ) -> OrchestrationResponse:
        """Execute all meta tasks under a base task sequentially.

        Sorts meta tasks by execution_order, passes dependency context between
        steps, supports cancellation, and returns batch results.

        Raises:
            TaskIdRequiredError: If task_id is missing.
            TaskNotFoundError: If the base task is not found.
        """
        base_task_id = request.task_id
        if base_task_id is None:
            raise TaskIdRequiredError()

        # Validate base task exists
        query_result = await self.task_service.query_base_task_by_task_id(
            TaskCenterRequest(task_id=base_task_id)
        )
        base_task = query_result.base_task

        if base_task is None:
            raise TaskNotFoundError()

        logger.info("WorkflowCenter: Starting workflow for base task %s", base_task_id)

        # Get all meta tasks under the base task
        meta_tasks_result = await self.task_service.query_meta_tasks_by_parent_task_id(
            TaskCenterRequest(parent_task_id=base_task_id)
        )

        if not meta_tasks_result.success:
            logger.error(
                "WorkflowCenter: Failed to query meta tasks for base task %s",
                base_task_id,
            )
            return self._error_response(base_task_id, "Failed to query meta tasks")

        meta_tasks = meta_tasks_result.meta_tasks
        if meta_tasks is None or len(meta_tasks) == 0:
            logger.info(
                "WorkflowCenter: No meta tasks found under base task %s",
                base_task_id,
            )
            return OrchestrationResponse(
                task_id=base_task_id,
                success=True,
                error="No meta tasks found to execute",
                meta_task_ids=[],
                status_code=200,
            )

        # Sort meta tasks by execution_order to ensure proper sequence
        meta_tasks.sort(key=lambda x: x.execution_order)

        # Track execution results
        processed_meta_task_ids = []
        failed_executions = []

        logger.info(
            "WorkflowCenter: Starting sequential execution of %s meta tasks for base task %s",
            len(meta_tasks),
            base_task_id,
        )

        # Track completed task results for context passing
        completed_task_results = {}

        message_id = (base_task.extend_info or {}).get("message_id")

        # Execute each meta task sequentially
        for i, meta_task in enumerate(meta_tasks):
            # Check for cancellation before processing each task
            if self.sse_manager.is_cancelled(message_id):
                logger.info(
                    "WorkflowCenter: Workflow cancelled for base task %s, stopping all processing",
                    base_task_id,
                )
                # Persist canceled status on all remaining (unprocessed) meta tasks
                remaining_meta_tasks = meta_tasks[i:]
                await self._cancel_remaining_meta_tasks(remaining_meta_tasks)

                # Send terminal CANCELED status so the frontend clears the spinner
                if message_id:
                    await self.sse_manager.send_processing_status(
                        base_task_id,
                        SSEProcessingStatus.CANCELED,
                        message_id,
                    )
                else:
                    logger.warning(
                        "WorkflowCenter: No message_id in base task %s extend_info — "
                        "cannot send CANCELED SSE to frontend",
                        base_task_id,
                    )
                self.sse_manager.clear_cancellation(message_id)
                return OrchestrationResponse(
                    task_id=base_task_id,
                    meta_task_ids=processed_meta_task_ids,
                    success=True,
                    error="Workflow cancelled by user",
                    status_code=200,
                )

            try:
                logger.info(
                    "WorkflowCenter: Processing meta task %s/%s: %s (execution_order: %s)",
                    i + 1,
                    len(meta_tasks),
                    meta_task.task_id,
                    meta_task.execution_order,
                )

                # Collect context from dependent tasks
                if meta_task.depends_on_tasks:
                    context_from_previous = {}
                    for dep_task_id in meta_task.depends_on_tasks:
                        if dep_task_id in completed_task_results:
                            context_from_previous[dep_task_id] = completed_task_results[
                                dep_task_id
                            ]

                    # Update meta task with context
                    meta_task.context_from_previous = context_from_previous
                    await self.task_service.update_meta_task_by_task_id(
                        TaskCenterRequest(
                            task_id=meta_task.task_id, meta_task=meta_task
                        )
                    )

                # Process meta task - wait for completion before proceeding
                process_response = await self.process_meta_task(
                    OrchestrationRequest(
                        task_id=meta_task.task_id, user_id=request.user_id
                    )
                )

                if process_response.success:
                    processed_meta_task_ids.append(meta_task.task_id)

                    # Store the completed task result for future context
                    completed_task_results[
                        meta_task.task_id
                    ] = await self._extract_task_result(meta_task.task_id)

                    logger.info(
                        "WorkflowCenter: Successfully processed meta task %s (%s/%s)",
                        meta_task.task_id,
                        i + 1,
                        len(meta_tasks),
                    )
                else:
                    failed_executions.append(
                        {
                            "meta_task_id": meta_task.task_id,
                            "execution_order": meta_task.execution_order,
                            "error": process_response.error,
                        }
                    )
                    logger.error(
                        "WorkflowCenter: Failed to process meta task %s (%s/%s): %s",
                        meta_task.task_id,
                        i + 1,
                        len(meta_tasks),
                        process_response.error,
                    )

            except Exception as e:
                failed_executions.append(
                    {
                        "meta_task_id": meta_task.task_id,
                        "execution_order": meta_task.execution_order,
                        "error": str(e),
                    }
                )
                logger.error(
                    "WorkflowCenter: Exception while processing meta task %s (%s/%s): %s",
                    meta_task.task_id,
                    i + 1,
                    len(meta_tasks),
                    str(e),
                )

        # Determine overall success
        total_meta_tasks = len(meta_tasks)
        successful_executions = len(processed_meta_task_ids)

        logger.info(
            "WorkflowCenter: Workflow execution completed for base task %s: %s/%s successful",
            base_task_id,
            successful_executions,
            total_meta_tasks,
        )

        if successful_executions == total_meta_tasks:
            logger.info(
                "WorkflowCenter: All meta tasks executed successfully for base task %s",
                base_task_id,
            )
        elif successful_executions > 0:
            logger.warning(
                "WorkflowCenter: Partial workflow execution for base task %s",
                base_task_id,
            )
        else:
            logger.error(
                "WorkflowCenter: Complete workflow failure for base task %s",
                base_task_id,
            )

        return self._build_batch_response(
            base_task_id,
            processed_meta_task_ids,
            failed_executions,
            total_meta_tasks,
            "executed",
        )

    async def process_meta_task(
        self, request: OrchestrationRequest
    ) -> OrchestrationResponse:
        """Execute a MetaTask by sending it to the assigned agent via A2A.

        Validates the meta task and agent, sends the task message, processes
        the agent response (task/message/status-update/artifact-update), and
        updates the meta task accordingly.

        Raises:
            TaskIdRequiredError: If task_id is missing.
            TaskNotFoundError: If the MetaTask is not found.
            AgentNotAssignedError: If no agent is assigned.
            AgentNotFoundError: If the assigned agent is not available.
        """
        meta_task_id = request.task_id

        if meta_task_id is None:
            raise TaskIdRequiredError()
        query_result = await self.task_service.query_meta_task_by_task_id(
            TaskCenterRequest(task_id=meta_task_id)
        )
        meta_task = query_result.meta_task

        if meta_task is None:
            raise TaskNotFoundError()

        if meta_task.agent_id == TaskDefaultValue.NOT_ASSIGNED.value:
            raise AgentNotAssignedError()
        agent_query_result = await self.agent_service.query_agent_by_agent_id(
            AgentCenterRequest(agent_id=meta_task.agent_id)
        )

        if agent_query_result.agent is None:
            raise AgentNotFoundError()

        try:
            # Build enhanced task description with context
            task_description_with_context = (
                await self._build_task_description_with_context(meta_task)
            )

            message = Message(
                role=Role.user,
                message_id=str(uuid.uuid4()),
                parts=[Part(root=TextPart(text=task_description_with_context))],
            )

            send_response = await self.a2a_service.send_message_sync(
                agent_query_result.agent.agent_card, message
            )
            logger.info("WorkflowCenter: send response: %s", send_response)

            # Add null check for send_response
            if send_response is None:
                logger.error(
                    "WorkflowCenter: send_message_to_agent returned None for meta task %s",
                    meta_task_id,
                )
                return self._error_response(
                    meta_task_id,
                    "Failed to send message to agent - no response received",
                )

            process_response = await self.a2a_service.process_a2a_response(
                send_response
            )
            logger.info("WorkflowCenter: process response: %s", process_response)

            # Add null check for process_response
            if process_response is None:
                logger.error(
                    "WorkflowCenter: process_a2a_response returned None for meta task %s",
                    meta_task_id,
                )
                return self._error_response(
                    meta_task_id,
                    "Failed to process agent response - no valid response data",
                )

            if process_response.kind == "task":
                meta_task.task = process_response
                update_response = await self.task_service.update_meta_task_by_task_id(
                    TaskCenterRequest(task_id=meta_task_id, meta_task=meta_task)
                )
                return self._meta_task_update_response(
                    meta_task_id, update_response, "Failed to update meta task"
                )

            elif process_response.kind == "message":
                if meta_task.task:
                    if meta_task.task.history is None:
                        meta_task.task.history = []
                    meta_task.task.history.append(process_response)

                update_response = await self.task_service.update_task_of_meta_task(
                    TaskCenterRequest(task_id=meta_task_id, task=meta_task.task)
                )
                return self._meta_task_update_response(
                    meta_task_id, update_response, "Failed to update task"
                )

            elif process_response.kind == "status-update":
                # Handle status update responses - update task status and potentially add message
                if hasattr(process_response, "status") and hasattr(
                    process_response.status, "state"
                ):
                    if meta_task.task and meta_task.task.status is None:
                        meta_task.task.status = TaskStatus(state=TaskState.submitted)
                    if meta_task.task:
                        meta_task.task.status.state = process_response.status.state

                    # If there's a message in the status update, add it to history
                    if (
                        hasattr(process_response.status, "message")
                        and process_response.status.message
                        and meta_task.task
                    ):
                        if meta_task.task.history is None:
                            meta_task.task.history = []
                        meta_task.task.history.append(process_response.status.message)

                update_response = await self.task_service.update_task_of_meta_task(
                    TaskCenterRequest(task_id=meta_task_id, task=meta_task.task)
                )

                return self._meta_task_update_response(
                    meta_task_id,
                    update_response,
                    "Failed to update task with status update",
                )

            elif process_response.kind == "artifact-update":
                # Handle artifact update responses - add artifacts to task
                if hasattr(process_response, "artifact") and meta_task.task:
                    if meta_task.task.artifacts is None:
                        meta_task.task.artifacts = []
                    meta_task.task.artifacts.append(process_response.artifact)

                update_response = await self.task_service.update_task_of_meta_task(
                    TaskCenterRequest(task_id=meta_task_id, task=meta_task.task)
                )

                return self._meta_task_update_response(
                    meta_task_id,
                    update_response,
                    "Failed to update task with artifact",
                )

            # Handle case where process_response doesn't have expected kind
            else:
                logger.error(
                    "WorkflowCenter: Unexpected response kind '%s' for meta task %s",
                    getattr(process_response, "kind", "unknown"),
                    meta_task_id,
                )
                return self._error_response(
                    meta_task_id,
                    f"Unexpected response type from agent: "
                    f"{getattr(process_response, 'kind', 'unknown')}",
                )

        except Exception as e:
            logger.error("process_meta_task: error: %s", e)
            return self._error_response(meta_task_id, str(e))

    def _meta_task_update_response(
        self, meta_task_id: str, update_response, error_msg: str
    ) -> OrchestrationResponse:
        """Build an OrchestrationResponse from a task-service update result."""
        if update_response.success:
            return self._success_response(meta_task_id)
        return self._error_response(meta_task_id, error_msg)

    def _build_batch_response(
        self,
        task_id: str,
        success_ids: list[str],
        failed_items: list[dict],
        total: int,
        operation_name: str,
    ) -> OrchestrationResponse:
        """Build a 3-way (all success / partial / all failed) batch response."""
        if len(success_ids) == total:
            return OrchestrationResponse(
                task_id=task_id,
                meta_task_ids=success_ids,
                success=True,
                error=None,
                status_code=200,
            )
        elif len(success_ids) > 0:
            error_summary = (
                f"Partial success: {len(success_ids)}/{total} meta tasks {operation_name}. "
                f"Failed: {failed_items}"
            )
            return OrchestrationResponse(
                task_id=task_id,
                meta_task_ids=success_ids,
                success=False,
                error=error_summary,
                status_code=207,
            )
        else:
            error_summary = f"All {operation_name} failed. Failed: {failed_items}"
            return OrchestrationResponse(
                task_id=task_id,
                meta_task_ids=[],
                success=False,
                error=error_summary,
                status_code=500,
            )

    async def _cancel_remaining_meta_tasks(
        self, remaining_meta_tasks: list[MetaTask]
    ) -> None:
        """Persist ``TaskState.canceled`` on each unprocessed MetaTask.

        Skips tasks that have already reached a terminal state to avoid
        overwriting a legitimate final status.  This mirrors the
        ``_cancel_remaining_queue`` pattern in ``RoomMessageCenter``.
        """
        for meta_task in remaining_meta_tasks:
            try:
                # Skip if already terminal
                if (
                    meta_task.task
                    and meta_task.task.status
                    and is_terminal_state(meta_task.task.status.state)
                ):
                    continue

                # Set canceled status
                if meta_task.task is None:
                    meta_task.task = Task(
                        id=meta_task.task_id,
                        status=TaskStatus(state=TaskState.canceled),
                    )
                else:
                    meta_task.task.status = TaskStatus(state=TaskState.canceled)

                await self.task_service.update_task_of_meta_task(
                    TaskCenterRequest(task_id=meta_task.task_id, task=meta_task.task)
                )
                logger.info(
                    "WorkflowCenter: Persisted canceled status on meta task %s",
                    meta_task.task_id,
                )
            except Exception as e:
                logger.warning(
                    "WorkflowCenter: Failed to cancel meta task %s: %s",
                    meta_task.task_id,
                    e,
                )

    def _get_first_text_from_task(
        self, base_task, fallback: str = "No task description available"
    ) -> str:
        """Extract the first text content from a base task's history."""
        if (
            base_task.task
            and base_task.task.history
            and len(base_task.task.history) > 0
            and base_task.task.history[0].parts
            and len(base_task.task.history[0].parts) > 0
        ):
            first_part = base_task.task.history[0].parts[0].root
            if first_part.kind == "text":
                return first_part.text
        return fallback

    async def _delete_existing_meta_tasks(self, parent_task_id: str) -> list[str]:
        """Delete existing meta tasks under a parent task.

        Returns a list of task IDs that failed to delete (empty means all succeeded).
        """
        existing_meta_tasks_result = (
            await self.task_service.query_meta_tasks_by_parent_task_id(
                TaskCenterRequest(parent_task_id=parent_task_id)
            )
        )

        if (
            not existing_meta_tasks_result.success
            or not existing_meta_tasks_result.meta_tasks
        ):
            logger.info(
                "WorkflowCenter: No existing meta tasks found for base task %s, "
                "proceeding with new decomposition",
                parent_task_id,
            )
            return []

        logger.info(
            "WorkflowCenter: Found %s existing meta tasks for base task %s, "
            "deleting them...",
            len(existing_meta_tasks_result.meta_tasks),
            parent_task_id,
        )

        deleted_count = 0
        failed_deletions = []

        for meta_task in existing_meta_tasks_result.meta_tasks:
            try:
                delete_response = await self.task_service.delete_meta_task_by_task_id(
                    TaskCenterRequest(task_id=meta_task.task_id)
                )

                if delete_response.success:
                    deleted_count += 1
                    logger.info(
                        "WorkflowCenter: Deleted existing meta task %s",
                        meta_task.task_id,
                    )
                else:
                    failed_deletions.append(meta_task.task_id)
                    logger.error(
                        "WorkflowCenter: Failed to delete meta task %s: %s",
                        meta_task.task_id,
                        delete_response.error,
                    )

            except Exception as e:
                failed_deletions.append(meta_task.task_id)
                logger.error(
                    "WorkflowCenter: Exception while deleting meta task %s: %s",
                    meta_task.task_id,
                    str(e),
                )

        logger.info(
            "WorkflowCenter: Successfully deleted %s existing meta tasks",
            deleted_count,
        )

        if failed_deletions:
            logger.warning(
                "WorkflowCenter: Failed to delete %s meta tasks: %s",
                len(failed_deletions),
                failed_deletions,
            )

        return failed_deletions

    async def _select_best_agent_for_decomposition(
        self, task_description: str, user_id: str | None = None
    ):
        """Find the best accessible agent for task decomposition.

        Uses the AgentResolverService to query similar agents, rank them via
        LLM, and verify real-time accessibility.

        Returns the selected agent, or None if no accessible agents are available.
        """
        result = await self.agent_resolver.resolve(
            task_description,
            count=5,
            use_llm_selection=True,
            user_id=user_id,
        )

        if result.agent is None:
            logger.error(
                "WorkflowCenter: No accessible agent found for task decomposition: %s",
                result.failure_reason,
            )
            return None

        return result.agent

    async def _extract_task_result(self, meta_task_id: str) -> dict[str, Any]:
        """Extract the result from a completed meta task for context passing.

        Uses shared a2a_helpers for text extraction to avoid duplicating
        parsing logic across orchestrators.
        """
        query_result = await self.task_service.query_meta_task_by_task_id(
            TaskCenterRequest(task_id=meta_task_id)
        )

        if not query_result.success or not query_result.meta_task:
            return {"error": "Failed to retrieve task result"}

        meta_task = query_result.meta_task
        result: dict[str, Any] = {
            "task_id": meta_task_id,
            "task_description": meta_task.task_description,
            "messages": [],
            "artifacts": [],
        }

        # Extract the primary agent response using the shared helper
        if meta_task.task:
            message = get_message_from_task(meta_task.task)
            if message:
                text = get_text_from_message(message)
                if text:
                    result["messages"].append(text)

            # Also extract from history for any additional non-user messages
            # not captured by get_message_from_task (which returns a single message)
            if meta_task.task.history:
                for hist_message in meta_task.task.history:
                    if hasattr(hist_message, "role") and hist_message.role != Role.user:
                        text = get_text_from_message(hist_message)
                        # Avoid duplicating the text already added above
                        if text and text not in result["messages"]:
                            result["messages"].append(text)

            # Extract artifacts using the same part-traversal pattern
            if meta_task.task.artifacts:
                for artifact in meta_task.task.artifacts:
                    if hasattr(artifact, "parts") and artifact.parts:
                        artifact_text = "".join(
                            part.root.text
                            if hasattr(part, "root")
                            and part.root
                            and hasattr(part.root, "text")
                            else (
                                part.text if hasattr(part, "text") and part.text else ""
                            )
                            for part in artifact.parts
                        )
                        if artifact_text:
                            result["artifacts"].append(artifact_text)

        return result

    async def _build_task_description_with_context(self, meta_task: MetaTask) -> str:
        """Build task description including context from previous tasks."""
        base_description = meta_task.task_description or ""

        if not meta_task.context_from_previous:
            return base_description

        # Build context section
        context_section = "\n\n=== CONTEXT FROM PREVIOUS STEPS ===\n"

        for task_id, result in meta_task.context_from_previous.items():
            context_section += (
                f"\nResults from {result.get('task_description', 'Previous Task')}:\n"
            )

            # Add messages (agent responses)
            if result.get("messages"):
                for message in result["messages"]:
                    context_section += f"- {message}\n"

            # Add artifacts if available
            if result.get("artifacts"):
                for artifact in result["artifacts"]:
                    context_section += f"- {artifact}\n"

        context_section += "\n=== END CONTEXT ===\n\n"

        # Combine with instructions
        enhanced_description = f"""EXECUTE THE FOLLOWING TASK:
{base_description}

{context_section}

CRITICAL INSTRUCTIONS:
1. Execute this task completely - do NOT just describe what should be done
2. Provide concrete, actionable results and deliverables
3. Use the context from previous steps to inform your work
4. Deliver the expected output, not a plan for how to create it
5. If the task asks you to create something, actually create it - don't just outline how you would create it"""

        return enhanced_description

    async def summarize_meta_task_for_base_task(
        self, request: OrchestrationRequest
    ) -> OrchestrationResponse:
        """Synthesize results from MetaTasks into a final answer for the base task.

        Collects all MetaTask results, uses OpenAI to synthesize a coherent
        response, updates chat context, and marks the base task as completed.

        Raises:
            TaskIdRequiredError: If task_id is missing.
            TaskNotFoundError: If the base task is not found.
        """

        base_task_id = request.task_id
        if base_task_id is None:
            raise TaskIdRequiredError()

        query_result = await self.task_service.query_base_task_by_task_id(
            TaskCenterRequest(task_id=base_task_id)
        )
        base_task = query_result.base_task

        if base_task is None:
            raise TaskNotFoundError()

        query_result = await self.task_service.query_meta_tasks_by_parent_task_id(
            TaskCenterRequest(parent_task_id=base_task_id)
        )
        meta_tasks = query_result.meta_tasks

        if meta_tasks is None or len(meta_tasks) == 0:
            return self._error_response(
                base_task_id, "No meta tasks found", status_code=200
            )

        meta_task_summaries = []
        for meta_task in meta_tasks:
            # Use the existing _extract_task_result method instead of raw history
            task_result = await self._extract_task_result(meta_task.task_id)

            summary_parts = []
            summary_parts.append(
                f"Task: {task_result.get('task_description', 'No description')}"
            )

            # Add actual results (agent responses)
            if task_result.get("messages"):
                summary_parts.append("Results:")
                for message in task_result["messages"]:
                    summary_parts.append(f"- {message}")

            # Add artifacts if available
            if task_result.get("artifacts"):
                summary_parts.append("Additional outputs:")
                for artifact in task_result["artifacts"]:
                    summary_parts.append(f"- {artifact}")

            meta_task_summaries.append(
                f"{meta_task.task_id}: {' | '.join(summary_parts)}"
            )

        logger.info("WorkflowCenter: meta task summaries: %s", meta_task_summaries)

        meta_task_descriptions = [
            meta_task.task_description or "No description" for meta_task in meta_tasks
        ]

        first_message_text = self._get_first_text_from_task(base_task)

        summary_response = await self.openai_service.summarize_meta_task_for_base_task(
            first_message_text, meta_task_summaries, meta_task_descriptions
        )
        logger.info("WorkflowCenter: summary response: %s", summary_response)

        # update chat context
        chat_context_response = (
            await self.chat_memory_service.update_chat_context_by_session_id(
                ChatMemoryRequest(
                    user_name=base_task.user_name,
                    session_id=base_task.session_id,
                    user_input=first_message_text,
                    agent_response=summary_response,
                )
            )
        )

        if not chat_context_response.success:
            return self._error_response(base_task_id, "Failed to update chat context")

        if base_task.task.history is None:
            base_task.task.history = []
        base_task.task.history.append(
            Message(
                role=Role.agent,
                message_id=str(uuid.uuid4()),
                parts=[Part(root=TextPart(text=summary_response))],
            )
        )

        base_task.task.status = TaskStatus(state=TaskState.completed)

        update_response = await self.task_service.update_base_task_by_task_id(
            TaskCenterRequest(task_id=base_task_id, base_task=base_task)
        )

        if update_response.success:
            return self._success_response(base_task_id)
        else:
            return self._error_response(base_task_id, "Failed to update base task")


# Module-level singleton
workflow_center = WorkflowCenter()
