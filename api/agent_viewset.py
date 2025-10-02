from motor.motor_asyncio import AsyncIOMotorDatabase

import api.viewset as router
from database.pinecone_db import pinecone_db
from database.repository import Repository
from models.request import AgentCreate, AgentPatch, AgentUpdate
from models.response import (
    AgentResponse,
    PaginatedResponse,
    PaginationMeta,
)
from services.openai_service import openai_service


class AgentViewSet(router.ViewSet):
    """
    CRUD router for managing agents.
    Override update, create, delete methods to update pinecone index as needed.
    """
    def __init__(self):
        super().__init__(
            resource_name="agents",
            collection_name="agents",
            schema_out=AgentResponse,
            schema_in=AgentCreate,
            schemas={
                router.LIST: {"out": PaginatedResponse[AgentResponse], "meta": PaginationMeta},
                router.CREATE: {"out": AgentResponse, "in": AgentCreate},
                router.RETRIEVE: {"out": AgentResponse},
                router.UPDATE: {"out": AgentResponse, "in": AgentUpdate},
                router.DELETE: {"out": dict},
                router.PATCH: {"out": AgentResponse, "in": AgentPatch},
            },
            pk_field="agent_id",
        )

    async def update_pinecone_index(self, agent_id: str, description: str):
        """Update Pinecone index when an agent is created or updated."""
        # get embedding of agent description
        embedding_data = await openai_service.get_embedding(
            description
        )
        vector_data = {
            "id": str(agent_id),
            "values": embedding_data,
            "metadata": {"type": "a2a_agent"},
        }
        pinecone_db.upsert([vector_data])

    async def _handle_operation(self, repo_method: str, db: AsyncIOMotorDatabase, *args):
        """Generic handler for CRUD operations."""
        repo = Repository(collection_name=self.collection_name, db=db, pinecone=None, pk_field=self.pk_field)
        method = getattr(repo, repo_method)

        async def handle_result(result):
            if repo_method in [router.CREATE, router.UPDATE, router.PATCH]:
                description = result.get('agent_card', {}).get('description', '')
                await self.update_pinecone_index(result["agent_id"], description)

        if self.use_transactions:
            async with await db.client.start_session() as session:
                async with session.start_transaction():
                    result = await method(*args)
                    await handle_result(result)
                    return result
        result = await method(*args)
        await handle_result(result)
        return result
    
    def get_filters(self, db, filter_params):
        base_query = super().get_filters(db, filter_params)
        filters = filter_params.filters if filter_params else {}

        # Always exclude null agent_ids
        base_query["agent_id"] = {"$ne": None}
    
        if "search" in filters:
            search_term = filters.pop("search")
            base_query["$or"] = [
                {"name": {"$regex": search_term, "$options": "i"}},
                {"agent_card.description": {"$regex": search_term, "$options": "i"}},
                {"tags": {"$in": [search_term]}}
            ]

        return base_query
