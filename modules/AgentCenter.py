import logging
import uuid

from models.request import AgentCenterRequest
from models.response import AgentCenterResponse
from models.agent import Agent
from models.error import AgentCardRequiredError, AgentIdRequiredError, AgentNotFoundError, QueryTextRequiredError, IllgalParameterError
from services.database_service import DatabaseService
from services.agent_service import AgentService
from services.a2a_service import A2AService

# Impletementation of agent management service

class AgentCenter:
    def __init__(self):
        self.database_service = DatabaseService()
        self.agent_service = AgentService()
        self.a2a_service = A2AService()

    async def register_agent(self, request: AgentCenterRequest) -> AgentCenterResponse:

        agent_url = request.agent_url
        
        if not agent_url:
            raise IllgalParameterError()
        
        agent_card = await self.a2a_service.get_agent_card_from_url(agent_url)
        request.agent_card = agent_card

        return await self.agent_service.register_agent(request)
    
    
    async def update_agent(self, request: AgentCenterRequest) -> AgentCenterResponse:
        
        return await self.agent_service.update_agent(request)


    async def remove_agent(self, request: AgentCenterRequest) -> AgentCenterResponse:
        
        return await self.agent_service.remove_agent(request)
    
    async def query_agent_by_agent_id(self, request: AgentCenterRequest) -> AgentCenterResponse:
       
       return await self.agent_service.query_agent_by_agent_id(request)
    
    
    async def get_all_agents(self, request: AgentCenterRequest) -> AgentCenterResponse:
        
        return await self.agent_service.get_all_agents(request)
    
    async def query_similar_agents(self, request: AgentCenterRequest) -> AgentCenterResponse:

        return await self.agent_service.query_similar_agents(request)