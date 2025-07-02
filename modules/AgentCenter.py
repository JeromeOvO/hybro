import logging
import uuid

from models.request import AgentCenterRequest
from models.response import AgentCenterResponse
from models.agent import Agent
from models.error import AgentCardRequiredError, AgentIdRequiredError, AgentNotFoundError, QueryTextRequiredError, IllgalParameterError

from services.database_service import DatabaseService

# Impletementation of agent management service

class AgentCenter:
    def __init__(self):
        self.database_service = DatabaseService()

    async def register_agent(self, request: AgentCenterRequest) -> AgentCenterResponse:

        # check if agent_card is provided
        if request.agent_card is None:
            raise AgentCardRequiredError()
        
        new_agent_id = str(uuid.uuid4())
        # create agent
        agent = Agent(
            agent_id=new_agent_id,
            agent_card=request.agent_card
        )
        
        # add agent to database
        try:
            agent_add_result = await self.database_service.add_agent(agent)
        except Exception as e:
            logging.error(f"AgentCenter: Failed to add agent to database: {str(e)}")
            return AgentCenterResponse(
                agent_id=new_agent_id,
                success=False,
                error=str(e),
                status_code=500
            )
        
        return AgentCenterResponse(
            agent_id=new_agent_id,
            agent=agent,
            success=agent_add_result,
            error=None,
            status_code=200
        )
    
    async def update_agent(self, request: AgentCenterRequest) -> AgentCenterResponse:
        
        agent_id = request.agent_id
        if agent_id is None:
            raise AgentIdRequiredError()
        
        # update the whole agent
        if request.agent:
            agent = request.agent

            try:
                agent_update_result = await self.database_service.update_agent_by_agent_id(agent_id, agent)
            except Exception as e:
                logging.error(f"AgentCenter: Failed to update agent in database: {str(e)}")
                return AgentCenterResponse(
                    agent_id=agent_id,
                    success=False,
                    error=str(e),
                    status_code=500
                )
            
            return AgentCenterResponse(
                agent_id=agent_id,
                agent=agent,
                success=agent_update_result,
                error=None,
                status_code=200
            )
        

        # update the agent card
        if request.agent_card:
            agent_card = request.agent_card
            
            try:
                agent_update_result = await self.database_service.update_agent_agent_card_by_agent_id(agent_id, agent_card)
            except Exception as e:
                logging.error(f"AgentCenter: Failed to update agent card in database: {str(e)}")
                return AgentCenterResponse(
                    agent_id=agent_id,
                    success=False,
                    error=str(e),
                    status_code=500
                )   
            
            return AgentCenterResponse(
                agent_id=agent_id,
                agent_card=agent_card,
                success=agent_update_result,
                error=None,
                status_code=200
            )


    async def remove_agent(self, request: AgentCenterRequest) -> AgentCenterResponse:
        agent_id = request.agent_id
        if agent_id is None:
            raise AgentIdRequiredError()
        
        try:
            agent_delete_result = await self.database_service.delete_agent_by_agent_id(agent_id)
        except Exception as e:
            logging.error(f"AgentCenter: Failed to delete agent in database: {str(e)}")
            return AgentCenterResponse(
                agent_id=agent_id,
                success=False,
                error=str(e),
                status_code=500
            )
        
        return AgentCenterResponse(
            agent_id=agent_id,
            success=agent_delete_result,
            error=None,
            status_code=200
        )
    
    async def query_agent_by_agent_id(self, request: AgentCenterRequest) -> AgentCenterResponse:
        agent_id = request.agent_id
        if agent_id is None:
            raise AgentIdRequiredError()
        
        try:
            agent = await self.database_service.get_agent_by_agent_id(agent_id)
            if not agent:
                raise AgentNotFoundError()
        except Exception as e:
            logging.error(f"AgentCenter: Failed to get agent in database: {str(e)}")
            return AgentCenterResponse(
                agent_id=agent_id,
                success=False,
                error=str(e),
                status_code=500
            )
        
        return AgentCenterResponse(
            agent_id=agent.agent_id,
            agent=agent,
            success=True,
            error=None,
            status_code=200
        )
    
    
    async def get_all_agents(self, request: AgentCenterRequest) -> AgentCenterResponse:
        try:
            agents = await self.database_service.get_all_agents()
        except Exception as e:
            logging.error(f"AgentCenter: Failed to get all agents in database: {str(e)}")
            return AgentCenterResponse(
                success=False,
                error=str(e),
                status_code=500
            )
        
        return AgentCenterResponse(
            agents=agents,
            success=True,   
            error=None,
            status_code=200
        )
    
    async def query_similar_agents(self, request: AgentCenterRequest) -> AgentCenterResponse:
        query_text = request.query_text
        if query_text is None:
            raise QueryTextRequiredError()
        
        if request.agent_count is not None and request.agent_count <= 0:
            raise IllgalParameterError()
        
        try:
            if request.agent_count is not None and request.agent_count > 0:
                agents = await self.database_service.query_similar_agents(query_text, request.agent_count)
            else:
                agents = await self.database_service.query_similar_agents(query_text, 5)
        except Exception as e:
            logging.error(f"AgentCenter: Failed to get similar agents in database: {str(e)}")
            return AgentCenterResponse(
                success=False,
                error=str(e),
                status_code=500
            )
        
        return AgentCenterResponse(
            agents=agents,
            success=True,
            error=None,
            status_code=200
        )