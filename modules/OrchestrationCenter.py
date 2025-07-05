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
        pass

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


