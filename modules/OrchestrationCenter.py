from models.request import OrchestrationCenterRequest, TaskCenterRequest, AgentCenterRequest
from models.response import OrchestrationCenterResponse
from models.error import AgentNotFoundError, AgentNotAssignedError, A2AServiceError, TaskIdRequiredError, TaskNotFoundError
from models.task import TaskDefaultValue
from services.task_service import TaskService
from services.openai_service import OpenAIService
from services.agent_service import AgentService
from services.a2a_service import A2AService
from a2a.types import Message, Role, TextPart, Part, TaskState, TaskStatus, Task
import uuid
import logging
import json

logger = logging.getLogger(__name__)

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

        self.task_service = TaskService()
        self.openai_service = OpenAIService()
        self.agent_service = AgentService()
        self.a2a_service = A2AService()


    async def decompose_task(self, request: OrchestrationCenterRequest) -> OrchestrationCenterResponse:
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
        
        query_result = await self.task_service.query_base_task_by_task_id(TaskCenterRequest(task_id=root_task_id))
        base_task = query_result.base_task
        
        if base_task is None:
            raise TaskNotFoundError()

        # Check for existing meta tasks and delete them if found
        existing_meta_tasks_result = await self.task_service.query_meta_tasks_by_parent_task_id(
            TaskCenterRequest(parent_task_id=root_task_id)
        )
        
        if existing_meta_tasks_result.success and existing_meta_tasks_result.meta_tasks:
            logger.info(f"OrchestrationCenter: Found {len(existing_meta_tasks_result.meta_tasks)} existing meta tasks for base task {root_task_id}, deleting them...")
            
            # Delete existing meta tasks
            deleted_count = 0
            failed_deletions = []
            
            for meta_task in existing_meta_tasks_result.meta_tasks:
                try:
                    delete_response = await self.task_service.delete_meta_task_by_task_id(
                        TaskCenterRequest(task_id=meta_task.task_id)
                    )
                    
                    if delete_response.success:
                        deleted_count += 1
                        logger.info(f"OrchestrationCenter: Deleted existing meta task {meta_task.task_id}")
                    else:
                        failed_deletions.append(meta_task.task_id)
                        logger.error(f"OrchestrationCenter: Failed to delete meta task {meta_task.task_id}: {delete_response.error}")
                        
                except Exception as e:
                    failed_deletions.append(meta_task.task_id)
                    logger.error(f"OrchestrationCenter: Exception while deleting meta task {meta_task.task_id}: {str(e)}")
            
            logger.info(f"OrchestrationCenter: Successfully deleted {deleted_count} existing meta tasks")
            
            if failed_deletions:
                logger.warning(f"OrchestrationCenter: Failed to delete {len(failed_deletions)} meta tasks: {failed_deletions}")

        else:
            logger.info(f"OrchestrationCenter: No existing meta tasks found for base task {root_task_id}, proceeding with new decomposition")

        # Proceed with task decomposition
        decompose_task_response = await self.openai_service.decompose_task(base_task)
        logger.info(f"OrchestrationCenter: decompose task response: {decompose_task_response}")
        
        # Parse the OpenAI response and create MetaTasks
        try:
            response_data = json.loads(decompose_task_response)

            if "execution_steps" not in response_data:
                logger.error("Invalid response format: missing execution_steps")
                return OrchestrationCenterResponse(
                    task_id=root_task_id,
                    success=False,
                    error="Invalid response format from OpenAI service",
                    status_code=500
                )
            
            first_step = response_data["execution_steps"][0]
            if (first_step.get("step_description") == "Analyze the task goal" or 
                first_step.get("step_description") == "Error occurred during task decomposition"):
                return OrchestrationCenterResponse(
                    task_id=root_task_id,
                    success=False,
                    error="Failed to decompose task, please try again",
                    status_code=500
                )
            
            created_meta_task_ids = []
            
            for step in response_data["execution_steps"]:
                # Validate step structure
                if not all(key in step for key in ["step_number", "step_description", "execution_content", "expected_output"]):
                    logger.warning(f"Skipping invalid step: {step}")
                    continue
                
                # Combine step information into task description
                task_description = (
                    f"Step Description: {step['step_description']}\n"
                    f"Execution Content: {step['execution_content']}\n"
                    f"Expected Output: {step['expected_output']}"
                )
                
                # Create a new Task for the meta task
                meta_task_task = await self.task_service.create_a2a_task()
                if meta_task_task.history is None:
                    meta_task_task.history = []
                new_message = await self.task_service.create_a2a_message(Role.user, task_description)
                meta_task_task.history.append(new_message)
                
                # Create MetaTask
                create_meta_task_response = await self.task_service.create_new_meta_task(
                    TaskCenterRequest(
                        parent_task_id=root_task_id,
                        user_name=base_task.user_name,
                        task=meta_task_task,
                        user_input=task_description
                    )
                )
                
                if create_meta_task_response.success:
                    # Update the execution_order for the meta task
                    meta_task_id = create_meta_task_response.task_id
                    meta_task_query_result = await self.task_service.query_meta_task_by_task_id(
                        TaskCenterRequest(task_id=meta_task_id)
                    )
                    
                    if meta_task_query_result.success and meta_task_query_result.meta_task:
                        meta_task = meta_task_query_result.meta_task
                        meta_task.execution_order = step["step_number"]
                        
                        # Update the meta task with execution order
                        update_response = await self.task_service.update_meta_task_by_task_id(
                            TaskCenterRequest(
                                task_id=meta_task_id,
                                meta_task=meta_task
                            )
                        )
                        
                        if update_response.success:
                            created_meta_task_ids.append(meta_task_id)
                            logger.info(f"Created meta task {meta_task_id} with execution order {step['step_number']}")
                        else:
                            logger.error(f"Failed to update meta task {meta_task_id} with execution order")
                    else:
                        logger.error(f"Failed to query created meta task {meta_task_id}")
                else:
                    logger.error(f"Failed to create meta task for step {step['step_number']}")
            
            logger.info(f"Successfully created {len(created_meta_task_ids)} meta tasks")
            
            return OrchestrationCenterResponse(
                task_id=root_task_id,
                success=True,
                error=None,
                status_code=200,
                meta_task_ids=created_meta_task_ids  # Add this field to response if needed
            )
            
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse OpenAI response as JSON: {e}")
            return OrchestrationCenterResponse(
                task_id=root_task_id,
                success=False,
                error=f"Invalid JSON response from OpenAI service: {str(e)}",
                status_code=500
            )
        except Exception as e:
            logger.error(f"Error creating meta tasks: {e}")
            return OrchestrationCenterResponse(
                task_id=root_task_id,
                success=False,
                error=f"Failed to create meta tasks: {str(e)}",
                status_code=500
            )
        

    async def assign_agents_metatasks_by_parent_task_id(self, request: OrchestrationCenterRequest) -> OrchestrationCenterResponse:
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
                status_code=500
            )
        
        meta_tasks = query_result.meta_tasks
        if meta_tasks is None or len(meta_tasks) == 0:
            return OrchestrationCenterResponse(
                task_id=parent_task_id,
                success=True,
                error="No meta tasks found under parent task",
                meta_task_ids=[],
                status_code=200
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
                    logger.info(f"OrchestrationCenter: Successfully assigned agent {assignment_response.agent_id} to meta task {meta_task.task_id}")
                else:
                    failed_assignments.append({
                        "meta_task_id": meta_task.task_id,
                        "error": assignment_response.error
                    })
                    logger.error(f"OrchestrationCenter: Failed to assign agent to meta task {meta_task.task_id}: {assignment_response.error}")
                    
            except Exception as e:
                failed_assignments.append({
                    "meta_task_id": meta_task.task_id,
                    "error": str(e)
                })
                logger.error(f"OrchestrationCenter: Exception while assigning agent to meta task {meta_task.task_id}: {str(e)}")
        
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
                status_code=200
            )
        elif successful_assignments > 0:
            # Partial success
            error_summary = f"Partial success: {successful_assignments}/{total_meta_tasks} meta tasks assigned. Failed assignments: {failed_assignments}"
            return OrchestrationCenterResponse(
                task_id=parent_task_id,
                meta_task_ids=assigned_meta_task_ids,
                success=False,
                error=error_summary,
                status_code=207  # Multi-Status
            )
        else:
            # Complete failure
            error_summary = f"All assignments failed. Failed assignments: {failed_assignments}"
            return OrchestrationCenterResponse(
                task_id=parent_task_id,
                meta_task_ids=[],
                success=False,
                error=error_summary,
                status_code=500
            )
        
    async def assign_agent_to_meta_task(self, request: OrchestrationCenterRequest) -> OrchestrationCenterResponse:
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

        query_result = await self.task_service.query_meta_task_by_task_id(TaskCenterRequest(task_id=meta_task_id))
        meta_task = query_result.meta_task

        if meta_task is None:
            raise TaskNotFoundError()
        
        agents_matched_response = await self.agent_service.query_similar_agents(AgentCenterRequest(
            query_text=meta_task.task_description,
            agent_count=1
        ))
        
        if agents_matched_response.agents is None or len(agents_matched_response.agents) == 0:
            return OrchestrationCenterResponse(
                task_id=meta_task_id,
                success=False,
                error="No agent matched",
                status_code=200
            )
        
        meta_task.agent_id = agents_matched_response.agents[0].agent_id
        update_response = await self.task_service.update_meta_task_by_task_id(TaskCenterRequest(
            task_id=meta_task_id,
            meta_task=meta_task
        ))

        if update_response.success:
            return OrchestrationCenterResponse(
                task_id=meta_task_id,
                success=True,
                error=None,
                agent_id=agents_matched_response.agents[0].agent_id,
                status_code=200
            )
        else:
            return OrchestrationCenterResponse(
                task_id=meta_task_id,
                success=False,
                error="Failed to update meta task",
                status_code=500
            )
        
    async def run_workflow(self, request: OrchestrationCenterRequest) -> OrchestrationCenterResponse:
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
        query_result = await self.task_service.query_base_task_by_task_id(TaskCenterRequest(task_id=base_task_id))
        base_task = query_result.base_task
        
        if base_task is None:
            raise TaskNotFoundError()
        
        logger.info(f"OrchestrationCenter: Starting workflow for base task {base_task_id}")
        
        # Get all meta tasks under the base task
        meta_tasks_result = await self.task_service.query_meta_tasks_by_parent_task_id(
            TaskCenterRequest(parent_task_id=base_task_id)
        )
        
        if not meta_tasks_result.success:
            logger.error(f"OrchestrationCenter: Failed to query meta tasks for base task {base_task_id}")
            return OrchestrationCenterResponse(
                task_id=base_task_id,
                success=False,
                error="Failed to query meta tasks",
                status_code=500
            )
        
        meta_tasks = meta_tasks_result.meta_tasks
        if meta_tasks is None or len(meta_tasks) == 0:
            logger.info(f"OrchestrationCenter: No meta tasks found under base task {base_task_id}")
            return OrchestrationCenterResponse(
                task_id=base_task_id,
                success=True,
                error="No meta tasks found to execute",
                meta_task_ids=[],
                status_code=200
            )
        
        # Sort meta tasks by execution_order to ensure proper sequence
        meta_tasks.sort(key=lambda x: x.execution_order)
        
        # Track execution results
        processed_meta_task_ids = []
        failed_executions = []
        
        logger.info(f"OrchestrationCenter: Starting sequential execution of {len(meta_tasks)} meta tasks for base task {base_task_id}")
        
        # Execute each meta task sequentially
        for i, meta_task in enumerate(meta_tasks):
            try:
                logger.info(f"OrchestrationCenter: Processing meta task {i+1}/{len(meta_tasks)}: {meta_task.task_id} (execution_order: {meta_task.execution_order})")
                
                # Process meta task - wait for completion before proceeding
                process_response = await self.process_meta_task(
                    OrchestrationCenterRequest(task_id=meta_task.task_id)
                )
                
                if process_response.success:
                    processed_meta_task_ids.append(meta_task.task_id)
                    logger.info(f"OrchestrationCenter: Successfully processed meta task {meta_task.task_id} ({i+1}/{len(meta_tasks)})")
                else:
                    failed_executions.append({
                        "meta_task_id": meta_task.task_id,
                        "execution_order": meta_task.execution_order,
                        "error": process_response.error
                    })
                    logger.error(f"OrchestrationCenter: Failed to process meta task {meta_task.task_id} ({i+1}/{len(meta_tasks)}): {process_response.error}")
                    
            except Exception as e:
                failed_executions.append({
                    "meta_task_id": meta_task.task_id,
                    "execution_order": meta_task.execution_order,
                    "error": str(e)
                })
                logger.error(f"OrchestrationCenter: Exception while processing meta task {meta_task.task_id} ({i+1}/{len(meta_tasks)}): {str(e)}")
        
        # Determine overall success
        total_meta_tasks = len(meta_tasks)
        successful_executions = len(processed_meta_task_ids)
        
        logger.info(f"OrchestrationCenter: Workflow execution completed for base task {base_task_id}: {successful_executions}/{total_meta_tasks} successful")
        
        if successful_executions == total_meta_tasks:
            # All executions successful
            logger.info(f"OrchestrationCenter: All meta tasks executed successfully for base task {base_task_id}")
            return OrchestrationCenterResponse(
                task_id=base_task_id,
                meta_task_ids=processed_meta_task_ids,
                success=True,
                error=None,
                status_code=200
            )
        elif successful_executions > 0:
            # Partial success
            error_summary = f"Partial success: {successful_executions}/{total_meta_tasks} meta tasks executed. Failed executions: {failed_executions}"
            logger.warning(f"OrchestrationCenter: Partial workflow execution for base task {base_task_id}: {error_summary}")
            return OrchestrationCenterResponse(
                task_id=base_task_id,
                meta_task_ids=processed_meta_task_ids,
                success=False,
                error=error_summary,
                status_code=207  # Multi-Status
            )
        else:
            # Complete failure
            error_summary = f"All executions failed. Failed executions: {failed_executions}"
            logger.error(f"OrchestrationCenter: Complete workflow failure for base task {base_task_id}: {error_summary}")
            return OrchestrationCenterResponse(
                task_id=base_task_id,
                meta_task_ids=[],
                success=False,
                error=error_summary,
                status_code=500
            )


    async def process_meta_task(self, request: OrchestrationCenterRequest) -> OrchestrationCenterResponse:
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
        query_result = await self.task_service.query_meta_task_by_task_id(TaskCenterRequest(task_id=meta_task_id))
        meta_task = query_result.meta_task

        if meta_task is None:
            raise TaskNotFoundError()

        if(meta_task.agent_id == TaskDefaultValue.NOT_ASSIGNED.value):
            raise AgentNotAssignedError()
        agent_query_result = await self.agent_service.query_agent_by_agent_id(AgentCenterRequest(agent_id=meta_task.agent_id))
        
        if(agent_query_result.agent is None):
            raise AgentNotFoundError()
        
        try:
            message = Message(
                role=Role.user,
                messageId=str(uuid.uuid4()),
                parts=[Part(root=TextPart(text=meta_task.task_description or ""))],
             )

            send_response = await self.a2a_service.send_message_to_agent(agent_query_result.agent.agent_card.url, message)
            logger.info(f"OrchestrationCenter: send response: {send_response}")
            
            # Add null check for send_response
            if send_response is None:
                logger.error(f"OrchestrationCenter: send_message_to_agent returned None for meta task {meta_task_id}")
                return OrchestrationCenterResponse(
                    task_id=meta_task_id,
                    success=False,
                    error="Failed to send message to agent - no response received",
                    status_code=500
                )
            
            process_response = await self.a2a_service.process_a2a_response(send_response)
            logger.info(f"OrchestrationCenter: process response: {process_response}")

            # Add null check for process_response
            if process_response is None:
                logger.error(f"OrchestrationCenter: process_a2a_response returned None for meta task {meta_task_id}")
                return OrchestrationCenterResponse(
                    task_id=meta_task_id,
                    success=False,
                    error="Failed to process agent response - no valid response data",
                    status_code=500
                )

            if process_response.kind == 'task':
                meta_task.task = process_response
                update_response = await self.task_service.update_meta_task_by_task_id(TaskCenterRequest(
                    task_id=meta_task_id,
                    meta_task=meta_task
                ))
                if update_response.success:
                    return OrchestrationCenterResponse(
                        task_id=meta_task_id,
                        success=True,
                        error=None,
                        status_code=200
                        )
                else:
                    return OrchestrationCenterResponse(
                        task_id=meta_task_id,
                        success=False,
                        error="Failed to update meta task",
                        status_code=500
                    )
                
            elif process_response.kind == 'message':

                if meta_task.task:
                    if meta_task.task.history is None:
                        meta_task.task.history = []
                    meta_task.task.history.append(process_response)

                update_response = await self.task_service.update_task_of_meta_task(TaskCenterRequest(
                        task_id=meta_task_id,
                        task=meta_task.task
                    ))
                if update_response.success:
                        return OrchestrationCenterResponse(
                            task_id=meta_task_id,
                            success=True,
                            error=None,
                            status_code=200
                        )
                else:
                    return OrchestrationCenterResponse(
                        task_id=meta_task_id,
                        success=False,
                        error="Failed to update task",
                        status_code=500
                    )
            
            elif process_response.kind == 'status-update':
                # Handle status update responses - update task status and potentially add message
                if hasattr(process_response, 'status') and hasattr(process_response.status, 'state'):
                    if meta_task.task and meta_task.task.status is None:
                        meta_task.task.status = TaskStatus(state=TaskState.submitted)
                    if meta_task.task:
                        meta_task.task.status.state = process_response.status.state
                    
                    # If there's a message in the status update, add it to history
                    if hasattr(process_response.status, 'message') and process_response.status.message and meta_task.task:
                        if meta_task.task.history is None:
                            meta_task.task.history = []
                        meta_task.task.history.append(process_response.status.message)
                
                update_response = await self.task_service.update_task_of_meta_task(TaskCenterRequest(
                    task_id=meta_task_id,
                    task=meta_task.task
                ))
                
                if update_response.success:
                    return OrchestrationCenterResponse(
                        task_id=meta_task_id,
                        success=True,
                        error=None,
                        status_code=200
                    )
                else:
                    return OrchestrationCenterResponse(
                        task_id=meta_task_id,
                        success=False,
                        error="Failed to update task with status update",
                        status_code=500
                    )
            
            elif process_response.kind == 'artifact-update':
                # Handle artifact update responses - add artifacts to task
                if hasattr(process_response, 'artifact') and meta_task.task:
                    if meta_task.task.artifacts is None:
                        meta_task.task.artifacts = []
                    meta_task.task.artifacts.append(process_response.artifact)
                
                update_response = await self.task_service.update_task_of_meta_task(TaskCenterRequest(
                    task_id=meta_task_id,
                    task=meta_task.task
                ))
                
                if update_response.success:
                    return OrchestrationCenterResponse(
                        task_id=meta_task_id,
                        success=True,
                        error=None,
                        status_code=200
                    )
                else:
                    return OrchestrationCenterResponse(
                        task_id=meta_task_id,
                        success=False,
                        error="Failed to update task with artifact",
                        status_code=500
                    )
            
            # Handle case where process_response doesn't have expected kind
            else:
                logger.error(f"OrchestrationCenter: Unexpected response kind '{getattr(process_response, 'kind', 'unknown')}' for meta task {meta_task_id}")
                return OrchestrationCenterResponse(
                    task_id=meta_task_id,
                    success=False,
                    error=f"Unexpected response type from agent: {getattr(process_response, 'kind', 'unknown')}",
                    status_code=500
                )

                
        except Exception as e:
            logger.error(f"process_meta_task: error: {e}")
            return OrchestrationCenterResponse(
                task_id=meta_task_id,
                success=False,
                error=str(e),
                status_code=500
            )


    async def summarize_meta_task_for_base_task(self, request: OrchestrationCenterRequest) -> OrchestrationCenterResponse:
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
        
        query_result = await self.task_service.query_base_task_by_task_id(TaskCenterRequest(task_id=base_task_id))
        base_task = query_result.base_task
        
        if base_task is None:
            raise TaskNotFoundError()
        
        query_result = await self.task_service.query_meta_tasks_by_parent_task_id(TaskCenterRequest(parent_task_id=base_task_id))
        meta_tasks = query_result.meta_tasks
        
        if meta_tasks is None or len(meta_tasks) == 0:
            return OrchestrationCenterResponse(
                task_id=base_task_id,
                success=False,
                error="No meta tasks found",
                status_code=200
            )
        
        meta_task_summaries = []
        for meta_task in meta_tasks:
            meta_task_history = meta_task.task.history if meta_task.task and meta_task.task.history else []
            for message in meta_task_history:
                for part in message.parts:
                    if part.root.kind == 'text' and part.root.text is not None:
                        meta_task_summaries.append(
                            meta_task.task_id + ": " + message.role.value + ": " + part.root.text
                        )
        
        logger.info(f"OrchestrationCenter: meta task summaries: {meta_task_summaries}")

        meta_task_descriptions = [meta_task.task_description for meta_task in meta_tasks]

        if base_task.task and base_task.task.history and len(base_task.task.history) > 0 and base_task.task.history[0].parts and len(base_task.task.history[0].parts) > 0:
            first_part = base_task.task.history[0].parts[0].root
            if first_part.kind == 'text':
                first_message_text = first_part.text
            else:
                first_message_text = "No text description available"
        else:
            first_message_text = "No task description available"

        summary_response = await self.openai_service.summarize_meta_task_for_base_task(first_message_text, meta_task_summaries, meta_task_descriptions)
        logger.info(f"OrchestrationCenter: summary response: {summary_response}")

        if base_task.task.history is None:
            base_task.task.history = []
        base_task.task.history.append(Message(
            role=Role.agent,
            messageId=str(uuid.uuid4()),
            parts=[Part(root=TextPart(text=summary_response))]
        ))

        base_task.task.status = TaskStatus(state=TaskState.completed)
        
        update_response = await self.task_service.update_base_task_by_task_id(TaskCenterRequest(
            task_id=base_task_id,
            base_task=base_task
        ))
        
        if update_response.success:
            return OrchestrationCenterResponse(
                task_id=base_task_id,
                success=True,
                error=None,
                status_code=200
            )
        else:
            return OrchestrationCenterResponse(
                task_id=base_task_id,
                success=False,
                error="Failed to update base task",
                status_code=500
        )