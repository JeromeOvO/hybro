from models.request import OrchestrationCenterRequest, TaskCenterRequest, AgentCenterRequest
from models.response import OrchestrationCenterResponse
from models.error import AgentNotFoundError, AgentNotAssignedError, A2AServiceError, TaskIdRequiredError, TaskNotFoundError
from models.task import TaskDefaultValue
from services.task_service import TaskService
from services.openai_service import OpenAIService
from services.agent_service import AgentService
from services.a2a_service import A2AService
from a2a.types import Message, Role, TextPart, TaskState, TaskStatus, Task
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
        2. Uses OpenAI to analyze and decompose the task into execution steps
        3. Creates structured MetaTasks for each execution step
        4. Assigns proper execution order and dependencies
        5. Returns comprehensive orchestration results
        
        The decomposition process includes:
        - Task goal analysis and understanding
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
        
        if meta_task.agent_id != TaskDefaultValue.NOT_ASSIGNED.value:
            raise AgentNotAssignedError()
        
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
                parts=[TextPart(text=meta_task.task_description)],
             )

            send_response = await self.a2a_service.send_message_to_agent(agent_query_result.agent.agent_card.url, message)
            logger.info(f"OrchestrationCenter: send response: {send_response}")
            process_response = await self.a2a_service.process_a2a_response(send_response)
            logger.info(f"OrchestrationCenter: process response: {process_response}")

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
            meta_task_history = meta_task.task.history
            for message in meta_task_history:
                for part in message.parts:
                    if part.root.text is not None:
                        meta_task_summaries.append(
                            meta_task.task_id + ": " + message.role.value + ": " + part.root.text
                        )
        
        logger.info(f"OrchestrationCenter: meta task summaries: {meta_task_summaries}")

        meta_task_descriptions = [meta_task.task_description for meta_task in meta_tasks]

        summary_response = await self.openai_service.summarize_meta_task_for_base_task(base_task.task.history[0].parts[0].root.text, meta_task_summaries, meta_task_descriptions)
        logger.info(f"OrchestrationCenter: summary response: {summary_response}")

        base_task.task.history.append(Message(
            role=Role.agent,
            messageId=str(uuid.uuid4()),
            parts=[TextPart(text=summary_response)]
        ))
        
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