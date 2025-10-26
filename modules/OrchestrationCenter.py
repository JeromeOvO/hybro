import json
import uuid
from collections import deque
from typing import Any

from a2a.types import Message, Part, Role, TaskState, TaskStatus, TextPart

from common.utils.logger import get_logger
from models.error import (
    AgentNotAssignedError,
    AgentNotFoundError,
    TaskIdRequiredError,
    TaskNotFoundError,
)
from models.memory import MemoryContent, RoomMemory
from models.request import (
    AgentCenterRequest,
    ChatMemoryRequest,
    OrchestrationCenterRequest,
    RoomCenterAgentMessageRequest,
    RoomCenterMemoryRequest,
    TaskCenterRequest,
)
from models.response import OrchestrationCenterResponse
from models.task import MetaTask, TaskDefaultValue
from services.a2a_service import a2a_service
from services.agent_service import agent_service
from services.database_service import db_service
from services.debate_service import debate_service
from services.memory_service import chat_memory_service, room_memory_service
from services.openai_service import openai_service
from services.room_services import room_services
from services.sse_services import sse_manager
from services.task_service import task_service

logger = get_logger(__name__)


class OrchestrationCenter:
    """
    OrchestrationCenter - Multi-Agent Task Orchestration and Workflow Management Service

    OrchestrationCenter serves as the central hub for orchestrating complex multi-agent workflows,
    task decomposition, agent assignment, and result synthesis in the multi-agent system.
    It provides comprehensive orchestration capabilities and acts as a controller layer for
    external interface requests.

    Key Responsibilities:
    1. Task Decomposition and Planning:
       - Analyzes complex tasks and breaks them into manageable subtasks
       - Uses AI to create structured execution plans
       - Generates MetaTasks with proper execution order
       - Ensures logical task flow and dependencies

    2. Agent Assignment and Matching:
       - Matches appropriate agents to specific subtasks
       - Uses semantic similarity for optimal agent selection
       - Manages agent availability and capability matching
       - Ensures balanced workload distribution

    3. Workflow Orchestration:
       - Coordinates task execution across multiple agents
       - Manages task dependencies and execution order
       - Handles task state transitions and progress tracking
       - Ensures workflow completion and quality

    4. Result Synthesis and Integration:
       - Collects and synthesizes results from multiple agents
       - Generates comprehensive final answers
       - Integrates diverse perspectives and solutions
       - Maintains result quality and coherence

    5. External Interface Controller:
       - Provides RESTful API endpoints for orchestration operations
       - Handles orchestration request validation and response formatting
       - Manages cross-service communication and coordination

    6. Quality Assurance and Monitoring:
       - Monitors task execution progress and quality
       - Handles errors and recovery mechanisms
       - Ensures system reliability and performance
       - Provides detailed orchestration analytics

    Service Dependencies:
    - TaskService: For task lifecycle management and persistence
    - OpenAIService: For AI-powered task decomposition and synthesis
    - AgentService: For agent discovery and capability matching
    - A2AService: For agent communication and protocol handling

    Usage:
    This center is typically used by external clients, other centers (like TaskCenter),
    and internal services that need to orchestrate complex multi-agent workflows.
    The center enables sophisticated problem-solving through coordinated agent collaboration.
    """

    def __init__(self):
        """
        Initialize OrchestrationCenter with required service dependencies.

        Sets up the task service for task management, OpenAI service for AI operations,
        agent service for agent management, and A2A service for agent communication.
        """

        self.task_service = task_service  # Use singleton
        self.openai_service = openai_service  # Use singleton
        self.agent_service = agent_service  # Use singleton
        self.a2a_service = a2a_service  # Use singleton
        self.chat_memory_service = chat_memory_service  # Use singleton
        self.room_services = room_services  # Use singleton
        self.room_memory_service = room_memory_service  # Use singleton
        self.database_service = db_service  # Use singleton
        self.debate_service = debate_service  # Use singleton
        self.sse_manager = sse_manager  # Use singleton

    async def decompose_task(
        self, request: OrchestrationCenterRequest
    ) -> OrchestrationCenterResponse:
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
            request: OrchestrationCenterRequest containing:
                - task_id: The ID of the root task to decompose
                - decomposition_parameters: Optional parameters for decomposition

        Returns:
            OrchestrationCenterResponse containing:
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

        # get chat context
        chat_context_response = (
            await self.chat_memory_service.get_chat_context_by_session_id(
                ChatMemoryRequest(
                    user_name=base_task.user_name, session_id=base_task.session_id
                )
            )
        )

        if not chat_context_response.success:
            return OrchestrationCenterResponse(
                task_id=root_task_id,
                success=False,
                error="Failed to get chat context",
                status_code=500,
            )

        if base_task is None:
            raise TaskNotFoundError()

        # Check for existing meta tasks and delete them if found
        existing_meta_tasks_result = (
            await self.task_service.query_meta_tasks_by_parent_task_id(
                TaskCenterRequest(parent_task_id=root_task_id)
            )
        )

        if existing_meta_tasks_result.success and existing_meta_tasks_result.meta_tasks:
            logger.info(
                "OrchestrationCenter: Found %s existing meta tasks for base task %s, "
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
                            "OrchestrationCenter: Deleted existing meta task %s",
                            meta_task.task_id,
                        )
                    else:
                        failed_deletions.append(meta_task.task_id)
                        logger.error(
                            "OrchestrationCenter: Failed to delete meta task %s: %s",
                            meta_task.task_id,
                            delete_response.error,
                        )

                except Exception as e:
                    failed_deletions.append(meta_task.task_id)
                    logger.error(
                        "OrchestrationCenter: Exception while deleting meta task %s: %s",
                        meta_task.task_id,
                        str(e),
                    )

            logger.info(
                "OrchestrationCenter: Successfully deleted %s existing meta tasks",
                deleted_count,
            )

            if failed_deletions:
                logger.warning(
                    "OrchestrationCenter: Failed to delete %s meta tasks: %s",
                    len(failed_deletions),
                    failed_deletions,
                )

        else:
            logger.info(
                "OrchestrationCenter: No existing meta tasks found for base task %s, "
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
            task_description, count=5
        )

        best_agent_id = await self.openai_service.select_best_agent_for_task(
            task_description, similar_agents
        )

        best_agent = await self.database_service.get_agent_by_agent_id(best_agent_id)

        # Proceed with task decomposition
        decompose_task_response = await self.openai_service.decompose_task(
            base_task=base_task,
            context_data=chat_context_response.chat_context.context_data,
            best_agent=best_agent,
        )
        logger.info(
            "OrchestrationCenter: decompose task response: %s", decompose_task_response
        )

        # Parse the OpenAI response and create MetaTasks
        try:
            response_data = json.loads(decompose_task_response)

            if "execution_steps" not in response_data:
                logger.error("Invalid response format: missing execution_steps")
                return OrchestrationCenterResponse(
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
                return OrchestrationCenterResponse(
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

                # Combine step information into task description
                task_description = (
                    f"Step Description: {step['step_description']}\n"
                    f"Execution Context: {step['execution_context']}\n"
                    f"Expected Output: {step['expected_output']}\n"
                    f"Depends On Steps: {step['depends_on_steps']}"
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

            return OrchestrationCenterResponse(
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
            return OrchestrationCenterResponse(
                task_id=root_task_id,
                success=False,
                error=f"Invalid JSON response from OpenAI service: {str(e)}",
                status_code=500,
            )
        except Exception as e:
            logger.error("Error creating meta tasks: %s", e)
            return OrchestrationCenterResponse(
                task_id=root_task_id,
                success=False,
                error=f"Failed to create meta tasks: {str(e)}",
                status_code=500,
            )

    async def assign_agents_metatasks_by_parent_task_id(
        self, request: OrchestrationCenterRequest
    ) -> OrchestrationCenterResponse:
        """
        Assign agents to all meta tasks under a specific parent task (BaseTask).

        This method performs batch agent assignment:
        1. Retrieves all MetaTasks under the specified parent task
        2. For each MetaTask, calls assign_agent_to_meta_task to assign the most suitable agent
        3. Tracks assignment results and provides comprehensive feedback

        Args:
            request: OrchestrationCenterRequest containing:
                - task_id: The parent task ID (BaseTask ID) to find meta tasks for

        Returns:
            OrchestrationCenterResponse containing:
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
            return OrchestrationCenterResponse(
                task_id=parent_task_id,
                success=False,
                error="Failed to query meta tasks",
                status_code=500,
            )

        meta_tasks = query_result.meta_tasks
        if meta_tasks is None or len(meta_tasks) == 0:
            return OrchestrationCenterResponse(
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
                    OrchestrationCenterRequest(task_id=meta_task.task_id)
                )

                if assignment_response.success:
                    assigned_meta_task_ids.append(meta_task.task_id)
                    logger.info(
                        "OrchestrationCenter: Successfully assigned agent %s to meta task %s",
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
                        "OrchestrationCenter: Failed to assign agent to meta task %s: %s",
                        meta_task.task_id,
                        assignment_response.error,
                    )

            except Exception as e:
                failed_assignments.append(
                    {"meta_task_id": meta_task.task_id, "error": str(e)}
                )
                logger.error(
                    "OrchestrationCenter: Exception while assigning agent to meta task %s: %s",
                    meta_task.task_id,
                    str(e),
                )

        # Determine overall success
        total_meta_tasks = len(meta_tasks)
        successful_assignments = len(assigned_meta_task_ids)

        if successful_assignments == total_meta_tasks:
            # All assignments successful
            return OrchestrationCenterResponse(
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
            return OrchestrationCenterResponse(
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
            return OrchestrationCenterResponse(
                task_id=parent_task_id,
                meta_task_ids=[],
                success=False,
                error=error_summary,
                status_code=500,
            )

    async def assign_agent_to_meta_task(
        self, request: OrchestrationCenterRequest
    ) -> OrchestrationCenterResponse:
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
            request: OrchestrationCenterRequest containing:
                - task_id: The MetaTask ID to assign an agent to
                - assignment_parameters: Optional assignment preferences

        Returns:
            OrchestrationCenterResponse containing:
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
            return OrchestrationCenterResponse(
                task_id=meta_task_id,
                success=False,
                error="No agent matched",
                status_code=200,
            )

        best_agent_id = await self.openai_service.select_best_agent_for_task(
            meta_task.task_description, agents_matched_response.agents
        )

        meta_task.agent_id = best_agent_id
        update_response = await self.task_service.update_meta_task_by_task_id(
            TaskCenterRequest(task_id=meta_task_id, meta_task=meta_task)
        )

        if update_response.success:
            return OrchestrationCenterResponse(
                task_id=meta_task_id,
                success=True,
                error=None,
                agent_id=best_agent_id,
                status_code=200,
            )
        else:
            return OrchestrationCenterResponse(
                task_id=meta_task_id,
                success=False,
                error="Failed to update meta task",
                status_code=500,
            )

    async def run_workflow(
        self, request: OrchestrationCenterRequest
    ) -> OrchestrationCenterResponse:
        """
        Run a workflow of meta tasks.

        This method executes all meta tasks under a base task in sequence:
        1. Retrieves the base task and validates its existence
        2. Gets all meta tasks under the base task (using base_task_id as parent_task_id)
        3. Executes each meta task sequentially using process_meta_task
        4. Returns only after all meta tasks have been processed

        Args:
            request: OrchestrationCenterRequest containing:
                - task_id: The base task ID (used as parent_task_id for meta tasks)

        Returns:
            OrchestrationCenterResponse containing:
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

        logger.info(
            "OrchestrationCenter: Starting workflow for base task %s", base_task_id
        )

        # Get all meta tasks under the base task
        meta_tasks_result = await self.task_service.query_meta_tasks_by_parent_task_id(
            TaskCenterRequest(parent_task_id=base_task_id)
        )

        if not meta_tasks_result.success:
            logger.error(
                "OrchestrationCenter: Failed to query meta tasks for base task %s",
                base_task_id,
            )
            return OrchestrationCenterResponse(
                task_id=base_task_id,
                success=False,
                error="Failed to query meta tasks",
                status_code=500,
            )

        meta_tasks = meta_tasks_result.meta_tasks
        if meta_tasks is None or len(meta_tasks) == 0:
            logger.info(
                "OrchestrationCenter: No meta tasks found under base task %s",
                base_task_id,
            )
            return OrchestrationCenterResponse(
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
            "OrchestrationCenter: Starting sequential execution of %s meta tasks for base task %s",
            len(meta_tasks),
            base_task_id,
        )

        # Track completed task results for context passing
        completed_task_results = {}

        # Execute each meta task sequentially
        for i, meta_task in enumerate(meta_tasks):
            try:
                logger.info(
                    "OrchestrationCenter: Processing meta task %s/%s: %s (execution_order: %s)",
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
                    OrchestrationCenterRequest(task_id=meta_task.task_id)
                )

                if process_response.success:
                    processed_meta_task_ids.append(meta_task.task_id)

                    # Store the completed task result for future context
                    completed_task_results[
                        meta_task.task_id
                    ] = await self._extract_task_result(meta_task.task_id)

                    logger.info(
                        "OrchestrationCenter: Successfully processed meta task %s (%s/%s)",
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
                        "OrchestrationCenter: Failed to process meta task %s (%s/%s): %s",
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
                    "OrchestrationCenter: Exception while processing meta task %s (%s/%s): %s",
                    meta_task.task_id,
                    i + 1,
                    len(meta_tasks),
                    str(e),
                )

        # Determine overall success
        total_meta_tasks = len(meta_tasks)
        successful_executions = len(processed_meta_task_ids)

        logger.info(
            "OrchestrationCenter: Workflow execution completed for base task %s: %s/%s successful",
            base_task_id,
            successful_executions,
            total_meta_tasks,
        )

        if successful_executions == total_meta_tasks:
            # All executions successful
            logger.info(
                "OrchestrationCenter: All meta tasks executed successfully for base task %s",
                base_task_id,
            )
            return OrchestrationCenterResponse(
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
                "OrchestrationCenter: Partial workflow execution for base task %s: %s",
                base_task_id,
                error_summary,
            )
            return OrchestrationCenterResponse(
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
                "OrchestrationCenter: Complete workflow failure for base task %s: %s",
                base_task_id,
                error_summary,
            )
            return OrchestrationCenterResponse(
                task_id=base_task_id,
                meta_task_ids=[],
                success=False,
                error=error_summary,
                status_code=500,
            )

    async def process_meta_task(
        self, request: OrchestrationCenterRequest
    ) -> OrchestrationCenterResponse:
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
            request: OrchestrationCenterRequest containing:
                - task_id: The MetaTask ID to process
                - execution_parameters: Optional execution preferences

        Returns:
            OrchestrationCenterResponse containing:
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
                messageId=str(uuid.uuid4()),
                parts=[Part(root=TextPart(text=task_description_with_context))],
            )

            send_response = await self.a2a_service.send_message_to_agent(
                agent_query_result.agent.agent_card.url, message
            )
            logger.info("OrchestrationCenter: send response: %s", send_response)

            # Add null check for send_response
            if send_response is None:
                logger.error(
                    "OrchestrationCenter: send_message_to_agent returned None for meta task %s",
                    meta_task_id,
                )
                return OrchestrationCenterResponse(
                    task_id=meta_task_id,
                    success=False,
                    error="Failed to send message to agent - no response received",
                    status_code=500,
                )

            process_response = await self.a2a_service.process_a2a_response(
                send_response
            )
            logger.info("OrchestrationCenter: process response: %s", process_response)

            # Add null check for process_response
            if process_response is None:
                logger.error(
                    "OrchestrationCenter: process_a2a_response returned None for meta task %s",
                    meta_task_id,
                )
                return OrchestrationCenterResponse(
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
                    return OrchestrationCenterResponse(
                        task_id=meta_task_id, success=True, error=None, status_code=200
                    )
                else:
                    return OrchestrationCenterResponse(
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
                    return OrchestrationCenterResponse(
                        task_id=meta_task_id, success=True, error=None, status_code=200
                    )
                else:
                    return OrchestrationCenterResponse(
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
                    return OrchestrationCenterResponse(
                        task_id=meta_task_id, success=True, error=None, status_code=200
                    )
                else:
                    return OrchestrationCenterResponse(
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
                    return OrchestrationCenterResponse(
                        task_id=meta_task_id, success=True, error=None, status_code=200
                    )
                else:
                    return OrchestrationCenterResponse(
                        task_id=meta_task_id,
                        success=False,
                        error="Failed to update task with artifact",
                        status_code=500,
                    )

            # Handle case where process_response doesn't have expected kind
            else:
                logger.error(
                    "OrchestrationCenter: Unexpected response kind '%s' for meta task %s",
                    getattr(process_response, "kind", "unknown"),
                    meta_task_id,
                )
                return OrchestrationCenterResponse(
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
            return OrchestrationCenterResponse(
                task_id=meta_task_id, success=False, error=str(e), status_code=500
            )

    async def _extract_task_result(self, meta_task_id: str) -> dict[str, Any]:
        """Extract the result from a completed meta task for context passing."""
        query_result = await self.task_service.query_meta_task_by_task_id(
            TaskCenterRequest(task_id=meta_task_id)
        )

        if not query_result.success or not query_result.meta_task:
            return {"error": "Failed to retrieve task result"}

        meta_task = query_result.meta_task
        result = {
            "task_id": meta_task_id,
            "task_description": meta_task.task_description,
            "messages": [],
            "artifacts": [],
        }

        # Extract messages from task history - be more flexible with filtering
        if meta_task.task and meta_task.task.history:
            for message in meta_task.task.history:
                # Don't filter by role - include all messages except the initial user message
                if hasattr(message, "role") and message.role != Role.user:
                    message_content = []
                    if hasattr(message, "parts") and message.parts:
                        for part in message.parts:
                            # Handle different part structures
                            if hasattr(part, "root"):
                                # Current structure: part.root.text
                                if hasattr(part.root, "text") and part.root.text:
                                    message_content.append(part.root.text)
                            elif hasattr(part, "text") and part.text:
                                # Alternative structure: part.text
                                message_content.append(part.text)
                            elif isinstance(part, str):
                                # Simple string
                                message_content.append(part)

                    if message_content:
                        result["messages"].extend(message_content)
                # Handle responses that don't have role attribute
                elif not hasattr(message, "role"):
                    # This might be a processed A2A response - try to extract text content
                    if hasattr(message, "text") and message.text:
                        result["messages"].append(message.text)
                    elif hasattr(message, "content") and message.content:
                        result["messages"].append(message.content)

        # Extract artifacts if available
        if meta_task.task and meta_task.task.artifacts:
            for artifact in meta_task.task.artifacts:
                artifact_content = []
                if hasattr(artifact, "parts") and artifact.parts:
                    for part in artifact.parts:
                        if (
                            hasattr(part, "root")
                            and hasattr(part.root, "text")
                            and part.root.text
                        ):
                            artifact_content.append(part.root.text)
                        elif hasattr(part, "text") and part.text:
                            artifact_content.append(part.text)
                if artifact_content:
                    result["artifacts"].extend(artifact_content)

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
        enhanced_description = f"""{base_description}

{context_section}

IMPORTANT: Use the context from previous steps above to inform your response. Reference and build upon the previous results as needed to complete this step effectively."""

        return enhanced_description

    async def summarize_meta_task_for_base_task(
        self, request: OrchestrationCenterRequest
    ) -> OrchestrationCenterResponse:
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
            request: OrchestrationCenterRequest containing:
                - task_id: The base task ID to synthesize results for
                - synthesis_parameters: Optional synthesis preferences

        Returns:
            OrchestrationCenterResponse containing:
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
            return OrchestrationCenterResponse(
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

        logger.info("OrchestrationCenter: meta task summaries: %s", meta_task_summaries)

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
        logger.info("OrchestrationCenter: summary response: %s", summary_response)

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
            return OrchestrationCenterResponse(
                task_id=base_task_id,
                success=False,
                error="Failed to update chat context",
                status_code=500,
            )

        if not chat_context_response.success:
            return OrchestrationCenterResponse(
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
                messageId=str(uuid.uuid4()),
                parts=[Part(root=TextPart(text=summary_response))],
            )
        )

        base_task.task.status = TaskStatus(state=TaskState.completed)

        update_response = await self.task_service.update_base_task_by_task_id(
            TaskCenterRequest(task_id=base_task_id, base_task=base_task)
        )

        if update_response.success:
            return OrchestrationCenterResponse(
                task_id=base_task_id, success=True, error=None, status_code=200
            )
        else:
            return OrchestrationCenterResponse(
                task_id=base_task_id,
                success=False,
                error="Failed to update base task",
                status_code=500,
            )

    # room services
    async def process_room_user_message(
        self, request: OrchestrationCenterRequest
    ) -> OrchestrationCenterResponse:
        if request.room_id is None:
            return OrchestrationCenterResponse(
                success=False,
                error="Room id is required",
                status_code=400,
            )

        room_id = request.room_id
        room_user_message_id = request.room_user_message_id

        # get room memory
        room_memory_response = (
            await self.room_memory_service.get_room_memory_by_room_id(
                RoomCenterMemoryRequest(room_id=room_id)
            )
        )
        if not room_memory_response.success:
            return OrchestrationCenterResponse(
                success=False,
                error=room_memory_response.error,
                status_code=500,
            )

        room_memory = room_memory_response.memory
        if room_memory is None:
            # Fallback: initialize empty room memory content
            room_memory_content_text = ""
        else:
            room_memory_content = room_memory.memory_content
            if room_memory_content is None:
                room_memory_content_text = ""
            else:
                room_memory_content_text = room_memory_content.memory_text or ""

        # Always add a delimiter even if empty, so the injected prompt remains consistent
        room_memory_content_text = (room_memory_content_text or "") + "\n\n"

        if room_user_message_id is None:
            return OrchestrationCenterResponse(
                success=False,
                error="Room user message id is required",
                status_code=400,
            )

        # processing agent messages by the order of messsage chain
        query_response = (
            await self.room_services.inquiry_agent_messages_by_related_message_id(
                RoomCenterAgentMessageRequest(related_message_id=room_user_message_id)
            )
        )
        if not query_response.success:
            return OrchestrationCenterResponse(
                room_id=room_id,
                success=False,
                error=query_response.error,
                status_code=500,
            )

        # initialize message queue
        message_queue = (
            deque(query_response.message_list)
            if query_response.message_list is not None
            else deque()
        )
        message_list = []

        while len(message_queue) > 0:
            current_message = message_queue.popleft()
            message_list.append(current_message)

            if current_message.agent_id is None:
                user_input = (
                    current_message.message_content.message_task.history[0]
                    .parts[0]
                    .root.text
                )
                matched_agents = await self.database_service.query_similar_agents(
                    user_input
                )
                if len(matched_agents) > 0:
                    current_message.agent_id = matched_agents[0].agent_id
                else:
                    return OrchestrationCenterResponse(
                        success=False,
                        error="Agent id is not found",
                        status_code=500,
                    )

                agent = matched_agents[0]
                current_message.agent_id = agent.agent_id
                update_success = (
                    await self.database_service.update_room_agent_message_by_message_id(
                        message_id=current_message.message_id,
                        room_agent_message=current_message,
                    )
                )
                if not update_success:
                    return OrchestrationCenterResponse(
                        success=False,
                        error="Failed to update agent message",
                        status_code=500,
                    )

            process_response = await self.room_services.process_agent_message(
                RoomCenterAgentMessageRequest(message=current_message),
                room_memory_content_text,
            )
            if not process_response.success:
                return OrchestrationCenterResponse(
                    success=False,
                    error=process_response.error,
                    status_code=500,
                )
            message_data = await self.a2a_service.process_a2a_response(
                process_response.a2a_response
            )
            message_data = await self.room_services.handle_a2a_response_for_room(
                current_message, message_data
            )
            if not message_data:
                return OrchestrationCenterResponse(
                    success=False,
                    error="Failed to handle a2a response",
                    status_code=500,
                )

            current_message = (
                await self.database_service.get_room_agent_message_by_message_id(
                    current_message.message_id
                )
            )
            if current_message is None:
                return OrchestrationCenterResponse(
                    success=False,
                    error="Agent message not found",
                    status_code=500,
                )
            logger.info(
                "OrchestrationCenter: Sending agent response to room %s for message %s",
                room_id,
                current_message.message_id,
            )
            await self.sse_manager.send_agent_response(
                room_id,
                current_message.message_id,
                current_message.agent_id,
                current_message.message_content.message_task.history[-1]
                .parts[0]
                .root.text,
            )

            next_messages = await self.database_service.get_room_agent_messages_by_related_message_id(
                current_message.message_id
            )
            for next_message in next_messages:
                new_agent_message = (
                    await self.debate_service.inject_short_debate_for_agent_message(
                        next_message
                    )
                )
                if new_agent_message is None:
                    continue
                message_queue.append(new_agent_message)

        logger.info(
            "OrchestrationCenter: Sending processing status to room %s for message %s",
            room_id,
            room_user_message_id,
        )
        await self.sse_manager.send_processing_status(
            room_id, "completed", room_user_message_id
        )

        # update room memory
        new_room_memory_content_text = (
            await self.openai_service.generate_room_memory_content(
                query_response.message_list, room_memory_content
            )
        )

        room_memory_response = (
            await self.room_memory_service.update_room_memory_by_room_id(
                RoomCenterMemoryRequest(
                    room_id=room_id,
                    memory=RoomMemory(
                        room_id=room_id,
                        memory_id=room_memory.memory_id,
                        memory_content=MemoryContent(
                            memory_text=new_room_memory_content_text
                        ),
                    ),
                )
            )
        )

        if not room_memory_response.success:
            logger.error(
                "OrchestrationCenter: Failed to update room memory: %s",
                room_memory_response.error,
            )

        return OrchestrationCenterResponse(
            room_id=room_id, success=True, error=None, status_code=200
        )
