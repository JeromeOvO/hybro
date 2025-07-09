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

    def __init__(self):
        self.task_service = TaskService()
        self.openai_service = OpenAIService()
        self.agent_service = AgentService()
        self.a2a_service = A2AService()


    async def decompose_task(self, request: OrchestrationCenterRequest) -> OrchestrationCenterResponse:

        """
        Decompose a root task into subtasks using OpenAI.
        
        Args:
            task_id: The ID of the root task to decompose
            
        Returns:
            List[str]: List of subtask IDs
            
        Raises:
            RuntimeError: If task not found or decomposition fails
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