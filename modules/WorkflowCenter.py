import json
import uuid
from typing import Any

from a2a.types import Message, Part, Role, TaskState, TaskStatus, TextPart

from common.utils.a2a_helpers import get_message_from_task, get_text_from_message
from common.utils.logger import get_logger
from models.agent import AgentStatus
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
from services.a2a_service import a2a_service
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

    async def decompose_task(
        self, request: OrchestrationRequest
    ) -> OrchestrationResponse:
        """
        Decompose a complex root task into structured subtasks using AI.

        This method orchestrates the complete task decomposition process:
        1. Retrieves the base task and validates its existence
        2. Checks for existing meta tasks and deletes them if found (for re-decomposition)
        3. Uses OpenAI to analyze and decompose the task into execution steps
        4. Creates structured MetaTasks for each execution step
        5. Assigns proper execution order and dependencies
        6. Returns comprehensive orchestration results

        The decomposition process includes:
        - Task goal analysis and understanding
        - Existing meta tasks cleanup (if any)
        - AI-powered step generation and planning
        - MetaTask creation with detailed descriptions
        - Execution order assignment and validation
        - Error handling and quality assurance

        Args:
            request: OrchestrationRequest containing:
                - task_id: The ID of the root task to decompose
                - decomposition_parameters: Optional parameters for decomposition

        Returns:
            OrchestrationResponse containing:
                - task_id: The original root task ID
                - success: Boolean indicating success/failure
                - meta_task_ids: List of created MetaTask IDs
                - status_code: HTTP status code
                - error: Error message if applicable

        Raises:
            TaskIdRequiredError: If task_id is missing
            TaskNotFoundError: If the specified task is not found
            DecompositionError: If AI decomposition fails
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
            return OrchestrationResponse(
                task_id=root_task_id,
                success=False,
                error="Failed to get chat context",
                status_code=500,
            )

        # Check for existing meta tasks and delete them if found
        existing_meta_tasks_result = (
            await self.task_service.query_meta_tasks_by_parent_task_id(
                TaskCenterRequest(parent_task_id=root_task_id)
            )
        )

        if existing_meta_tasks_result.success and existing_meta_tasks_result.meta_tasks:
            logger.info(
                "WorkflowCenter: Found %s existing meta tasks for base task %s, "
                "deleting them...",
                len(existing_meta_tasks_result.meta_tasks),
                root_task_id,
            )

            # Delete existing meta tasks
            deleted_count = 0
            failed_deletions = []

            for meta_task in existing_meta_tasks_result.meta_tasks:
                try:
                    delete_response = (
                        await self.task_service.delete_meta_task_by_task_id(
                            TaskCenterRequest(task_id=meta_task.task_id)
                        )
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

        else:
            logger.info(
                "WorkflowCenter: No existing meta tasks found for base task %s, "
                "proceeding with new decomposition",
                root_task_id,
            )

        # Extract task goal/description from BaseTask
        if (
            base_task.task.history
            and len(base_task.task.history) > 0
            and len(base_task.task.history[0].parts) > 0
        ):
            first_part = base_task.task.history[0].parts[0].root
            task_description = (
                first_part.text
                if first_part.kind == "text"
                else "No text content available"
            )
        else:
            task_description = "No task description available"

        similar_agents = await self.database_service.query_similar_agents(
            task_description, count=5, active_only=True
        )

        if not similar_agents:
            logger.error(
                "WorkflowCenter: No active agents found for task decomposition"
            )
            return OrchestrationResponse(
                task_id=root_task_id,
                success=False,
                error="No active agents available for task decomposition",
                status_code=500,
            )

        best_agent_id = await self.openai_service.select_best_agent_for_task(
            task_description, similar_agents
        )

        best_agent = await self.database_service.get_agent_by_agent_id(best_agent_id)

        # Verify the selected agent is active, fallback to first similar agent if not
        if best_agent is None or best_agent.agent_status != AgentStatus.active:
            logger.warning(
                "WorkflowCenter: Selected agent %s is not active, falling back to first active agent",
                best_agent_id,
            )
            best_agent = similar_agents[0]  # Already filtered for active agents

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
                return OrchestrationResponse(
                    task_id=root_task_id,
                    success=False,
                    error="Invalid response format from OpenAI service",
                    status_code=500,
                )

            first_step = response_data["execution_steps"][0]
            if (
                first_step.get("step_description") == "Analyze the task goal"
                or first_step.get("step_description")
                == "Error occurred during task decomposition"
            ):
                return OrchestrationResponse(
                    task_id=root_task_id,
                    success=False,
                    error="Failed to decompose task, please try again",
                    status_code=500,
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
            return OrchestrationResponse(
                task_id=root_task_id,
                success=False,
                error=f"Invalid JSON response from OpenAI service: {str(e)}",
                status_code=500,
            )
        except Exception as e:
            logger.error("Error creating meta tasks: %s", e)
            return OrchestrationResponse(
                task_id=root_task_id,
                success=False,
                error=f"Failed to create meta tasks: {str(e)}",
                status_code=500,
            )

    async def assign_agents_metatasks_by_parent_task_id(
        self, request: OrchestrationRequest
    ) -> OrchestrationResponse:
        """
        Assign agents to all meta tasks under a specific parent task (BaseTask).

        This method performs batch agent assignment:
        1. Retrieves all MetaTasks under the specified parent task
        2. For each MetaTask, calls assign_agent_to_meta_task to assign the most suitable agent
        3. Tracks assignment results and provides comprehensive feedback

        Args:
            request: OrchestrationRequest containing:
                - task_id: The parent task ID (BaseTask ID) to find meta tasks for

        Returns:
            OrchestrationResponse containing:
                - task_id: The parent task ID
                - meta_task_ids: List of all meta task IDs that were processed
                - success: Boolean indicating overall success/failure
                - error: Error message if applicable
                - status_code: HTTP status code
        """
        parent_task_id = request.task_id
        if parent_task_id is None:
            raise TaskIdRequiredError()

        # Query all meta tasks under the parent task
        query_result = await self.task_service.query_meta_tasks_by_parent_task_id(
            TaskCenterRequest(parent_task_id=parent_task_id)
        )

        if not query_result.success:
            return OrchestrationResponse(
                task_id=parent_task_id,
                success=False,
                error="Failed to query meta tasks",
                status_code=500,
            )

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
                    OrchestrationRequest(task_id=meta_task.task_id)
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
        total_meta_tasks = len(meta_tasks)
        successful_assignments = len(assigned_meta_task_ids)

        if successful_assignments == total_meta_tasks:
            # All assignments successful
            return OrchestrationResponse(
                task_id=parent_task_id,
                meta_task_ids=assigned_meta_task_ids,
                success=True,
                error=None,
                status_code=200,
            )
        elif successful_assignments > 0:
            # Partial success
            error_summary = (
                f"Partial success: {successful_assignments}/{total_meta_tasks} meta tasks assigned."
                f"Failed assignments: {failed_assignments}"
            )
            return OrchestrationResponse(
                task_id=parent_task_id,
                meta_task_ids=assigned_meta_task_ids,
                success=False,
                error=error_summary,
                status_code=207,  # Multi-Status
            )
        else:
            # Complete failure
            error_summary = (
                f"All assignments failed. Failed assignments: {failed_assignments}"
            )
            return OrchestrationResponse(
                task_id=parent_task_id,
                meta_task_ids=[],
                success=False,
                error=error_summary,
                status_code=500,
            )

    async def assign_agent_to_meta_task(
        self, request: OrchestrationRequest
    ) -> OrchestrationResponse:
        """
        Assign the most suitable agent to a specific MetaTask.

        This method performs intelligent agent assignment:
        1. Retrieves the MetaTask and validates its current state
        2. Uses semantic similarity to find the best matching agent
        3. Validates agent availability and capability
        4. Assigns the agent to the MetaTask
        5. Updates the MetaTask with agent assignment

        The assignment process includes:
        - MetaTask validation and state checking
        - Agent capability analysis and matching
        - Semantic similarity calculation
        - Assignment validation and confirmation
        - Error handling and fallback mechanisms

        Args:
            request: OrchestrationRequest containing:
                - task_id: The MetaTask ID to assign an agent to
                - assignment_parameters: Optional assignment preferences

        Returns:
            OrchestrationResponse containing:
                - task_id: The MetaTask ID
                - success: Boolean indicating success/failure
                - assigned_agent_id: The ID of the assigned agent
                - status_code: HTTP status code
                - error: Error message if applicable

        Raises:
            TaskIdRequiredError: If task_id is missing
            TaskNotFoundError: If the MetaTask is not found
            AgentNotAssignedError: If agent assignment fails
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

        agents_matched_response = await self.agent_service.query_similar_agents(
            AgentCenterRequest(query_text=meta_task.task_description, agent_count=3)
        )

        if (
            agents_matched_response.agents is None
            or len(agents_matched_response.agents) == 0
        ):
            return OrchestrationResponse(
                task_id=meta_task_id,
                success=False,
                error="No active agent matched",
                status_code=200,
            )

        # Filter for active agents only (safety check - db_service should already filter)
        active_agents = [
            agent
            for agent in agents_matched_response.agents
            if agent.agent_status == AgentStatus.active
        ]

        if not active_agents:
            logger.warning(
                "WorkflowCenter: No active agents available for meta task %s",
                meta_task_id,
            )
            return OrchestrationResponse(
                task_id=meta_task_id,
                success=False,
                error="No active agent available",
                status_code=200,
            )

        best_agent_id = await self.openai_service.select_best_agent_for_task(
            meta_task.task_description, active_agents
        )

        # Verify the selected agent is in our active list, fallback if not
        best_agent = next(
            (a for a in active_agents if a.agent_id == best_agent_id), None
        )
        if best_agent is None or best_agent.agent_status != AgentStatus.active:
            logger.warning(
                "WorkflowCenter: Selected agent %s is not active, using first active agent",
                best_agent_id,
            )
            best_agent_id = active_agents[0].agent_id

        meta_task.agent_id = best_agent_id
        update_response = await self.task_service.update_meta_task_by_task_id(
            TaskCenterRequest(task_id=meta_task_id, meta_task=meta_task)
        )

        if update_response.success:
            return OrchestrationResponse(
                task_id=meta_task_id,
                success=True,
                error=None,
                agent_id=best_agent_id,
                status_code=200,
            )
        else:
            return OrchestrationResponse(
                task_id=meta_task_id,
                success=False,
                error="Failed to update meta task",
                status_code=500,
            )

    async def run_workflow(
        self, request: OrchestrationRequest
    ) -> OrchestrationResponse:
        """
        Run a workflow of meta tasks.

        This method executes all meta tasks under a base task in sequence:
        1. Retrieves the base task and validates its existence
        2. Gets all meta tasks under the base task (using base_task_id as parent_task_id)
        3. Executes each meta task sequentially using process_meta_task
        4. Returns only after all meta tasks have been processed

        Args:
            request: OrchestrationRequest containing:
                - task_id: The base task ID (used as parent_task_id for meta tasks)

        Returns:
            OrchestrationResponse containing:
                - task_id: The base task ID
                - meta_task_ids: List of all meta task IDs that were processed
                - success: Boolean indicating overall success/failure
                - error: Error message if applicable
                - status_code: HTTP status code
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
            return OrchestrationResponse(
                task_id=base_task_id,
                success=False,
                error="Failed to query meta tasks",
                status_code=500,
            )

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
                    OrchestrationRequest(task_id=meta_task.task_id)
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
            # All executions successful
            logger.info(
                "WorkflowCenter: All meta tasks executed successfully for base task %s",
                base_task_id,
            )
            return OrchestrationResponse(
                task_id=base_task_id,
                meta_task_ids=processed_meta_task_ids,
                success=True,
                error=None,
                status_code=200,
            )
        elif successful_executions > 0:
            # Partial success
            error_summary = (
                f"Partial success: {successful_executions}/{total_meta_tasks} meta tasks executed. "
                f"Failed executions: {failed_executions}"
            )
            logger.warning(
                "WorkflowCenter: Partial workflow execution for base task %s: %s",
                base_task_id,
                error_summary,
            )
            return OrchestrationResponse(
                task_id=base_task_id,
                meta_task_ids=processed_meta_task_ids,
                success=False,
                error=error_summary,
                status_code=207,  # Multi-Status
            )
        else:
            # Complete failure
            error_summary = (
                f"All executions failed. Failed executions: {failed_executions}"
            )
            logger.error(
                "WorkflowCenter: Complete workflow failure for base task %s: %s",
                base_task_id,
                error_summary,
            )
            return OrchestrationResponse(
                task_id=base_task_id,
                meta_task_ids=[],
                success=False,
                error=error_summary,
                status_code=500,
            )

    async def process_meta_task(
        self, request: OrchestrationRequest
    ) -> OrchestrationResponse:
        """
        Execute a MetaTask by sending it to the assigned agent.

        This method orchestrates the MetaTask execution process:
        1. Validates the MetaTask and its agent assignment
        2. Prepares the task message for the agent
        3. Sends the task to the assigned agent via A2A protocol
        4. Processes the agent's response and updates task state
        5. Handles task completion and result collection

        The execution process includes:
        - Task validation and state checking
        - Agent communication setup and message preparation
        - A2A protocol message exchange
        - Response processing and task state updates
        - Error handling and recovery mechanisms

        Args:
            request: OrchestrationRequest containing:
                - task_id: The MetaTask ID to process
                - execution_parameters: Optional execution preferences

        Returns:
            OrchestrationResponse containing:
                - task_id: The MetaTask ID
                - success: Boolean indicating success/failure
                - task_state: Current state of the MetaTask
                - agent_response: Response from the assigned agent
                - status_code: HTTP status code
                - error: Error message if applicable

        Raises:
            TaskIdRequiredError: If task_id is missing
            TaskNotFoundError: If the MetaTask is not found
            AgentNotAssignedError: If no agent is assigned
            AgentNotFoundError: If the assigned agent is not available
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
                return OrchestrationResponse(
                    task_id=meta_task_id,
                    success=False,
                    error="Failed to send message to agent - no response received",
                    status_code=500,
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
                return OrchestrationResponse(
                    task_id=meta_task_id,
                    success=False,
                    error="Failed to process agent response - no valid response data",
                    status_code=500,
                )

            if process_response.kind == "task":
                meta_task.task = process_response
                update_response = await self.task_service.update_meta_task_by_task_id(
                    TaskCenterRequest(task_id=meta_task_id, meta_task=meta_task)
                )
                if update_response.success:
                    return OrchestrationResponse(
                        task_id=meta_task_id, success=True, error=None, status_code=200
                    )
                else:
                    return OrchestrationResponse(
                        task_id=meta_task_id,
                        success=False,
                        error="Failed to update meta task",
                        status_code=500,
                    )

            elif process_response.kind == "message":
                if meta_task.task:
                    if meta_task.task.history is None:
                        meta_task.task.history = []
                    meta_task.task.history.append(process_response)

                update_response = await self.task_service.update_task_of_meta_task(
                    TaskCenterRequest(task_id=meta_task_id, task=meta_task.task)
                )
                if update_response.success:
                    return OrchestrationResponse(
                        task_id=meta_task_id, success=True, error=None, status_code=200
                    )
                else:
                    return OrchestrationResponse(
                        task_id=meta_task_id,
                        success=False,
                        error="Failed to update task",
                        status_code=500,
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

                if update_response.success:
                    return OrchestrationResponse(
                        task_id=meta_task_id, success=True, error=None, status_code=200
                    )
                else:
                    return OrchestrationResponse(
                        task_id=meta_task_id,
                        success=False,
                        error="Failed to update task with status update",
                        status_code=500,
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

                if update_response.success:
                    return OrchestrationResponse(
                        task_id=meta_task_id, success=True, error=None, status_code=200
                    )
                else:
                    return OrchestrationResponse(
                        task_id=meta_task_id,
                        success=False,
                        error="Failed to update task with artifact",
                        status_code=500,
                    )

            # Handle case where process_response doesn't have expected kind
            else:
                logger.error(
                    "WorkflowCenter: Unexpected response kind '%s' for meta task %s",
                    getattr(process_response, "kind", "unknown"),
                    meta_task_id,
                )
                return OrchestrationResponse(
                    task_id=meta_task_id,
                    success=False,
                    error=(
                        f"Unexpected response type from agent: "
                        f"{getattr(process_response, 'kind', 'unknown')}"
                    ),
                    status_code=500,
                )

        except Exception as e:
            logger.error("process_meta_task: error: %s", e)
            return OrchestrationResponse(
                task_id=meta_task_id, success=False, error=str(e), status_code=500
            )

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
        """
        Synthesize results from multiple MetaTasks into a comprehensive final answer.

        This method orchestrates the result synthesis process:
        1. Collects all MetaTask results and their execution histories
        2. Analyzes the original base task goal and requirements
        3. Synthesizes diverse agent responses into a coherent answer
        4. Generates a comprehensive final response
        5. Updates the base task with the final result

        The synthesis process includes:
        - Result collection and validation
        - Goal analysis and requirement understanding
        - Multi-agent response synthesis
        - Final answer generation and formatting
        - Quality assurance and validation

        Args:
            request: OrchestrationRequest containing:
                - task_id: The base task ID to synthesize results for
                - synthesis_parameters: Optional synthesis preferences

        Returns:
            OrchestrationResponse containing:
                - task_id: The base task ID
                - success: Boolean indicating success/failure
                - final_answer: The synthesized comprehensive answer
                - synthesis_summary: Summary of the synthesis process
                - status_code: HTTP status code
                - error: Error message if applicable

        Raises:
            TaskIdRequiredError: If task_id is missing
            TaskNotFoundError: If the base task is not found
            SynthesisError: If result synthesis fails
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
            return OrchestrationResponse(
                task_id=base_task_id,
                success=False,
                error="No meta tasks found",
                status_code=200,
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

        if (
            base_task.task
            and base_task.task.history
            and len(base_task.task.history) > 0
            and base_task.task.history[0].parts
            and len(base_task.task.history[0].parts) > 0
        ):
            first_part = base_task.task.history[0].parts[0].root
            if first_part.kind == "text":
                first_message_text = first_part.text
            else:
                first_message_text = "No text description available"
        else:
            first_message_text = "No task description available"

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
            return OrchestrationResponse(
                task_id=base_task_id,
                success=False,
                error="Failed to update chat context",
                status_code=500,
            )

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
            return OrchestrationResponse(
                task_id=base_task_id, success=True, error=None, status_code=200
            )
        else:
            return OrchestrationResponse(
                task_id=base_task_id,
                success=False,
                error="Failed to update base task",
                status_code=500,
            )


# Module-level singleton
workflow_center = WorkflowCenter()
