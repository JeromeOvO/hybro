from fastapi import HTTPException
from pydantic import BaseModel

from api import viewset
from api.viewset import REPO_ACTIONS_MAP
from app_shell.bound import EmbeddingProvider, VectorIndex
from models.request import AgentCreate, AgentPatch, AgentUpdate
from models.response import AgentResponse, PaginatedResponse, PaginationMeta

embedding_provider: EmbeddingProvider | None = None
vector_index: VectorIndex | None = None


def bind_agent_viewset_dependencies(
    *,
    embedding_source: EmbeddingProvider,
    vector_index_service: VectorIndex,
) -> None:
    global embedding_provider, vector_index

    embedding_provider = embedding_source
    vector_index = vector_index_service


def _require_embedding_provider() -> EmbeddingProvider:
    if embedding_provider is None:
        raise RuntimeError("AgentViewSet embedding dependency has not been bound")
    return embedding_provider


def _require_vector_index() -> VectorIndex:
    if vector_index is None:
        raise RuntimeError("AgentViewSet vector dependency has not been bound")
    return vector_index


class AgentViewSet(viewset.ViewSet):
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
                viewset.LIST: {
                    "out": PaginatedResponse[AgentResponse],
                    "meta": PaginationMeta,
                },
                viewset.CREATE: {"out": AgentResponse, "in": AgentCreate},
                viewset.RETRIEVE: {"out": AgentResponse},
                viewset.UPDATE: {"out": AgentResponse, "in": AgentUpdate},
                viewset.DELETE: {"out": dict},
                viewset.PATCH: {"out": AgentResponse, "in": AgentPatch},
            },
            pk_field="agent_id",
        )

    async def update_pinecone_index(self, agent_id: str, description: str):
        """Update Pinecone index when an agent is created or updated."""
        # get embedding of agent description
        embedding_data = await _require_embedding_provider().get_embedding(description)
        vector_data = {
            "id": str(agent_id),
            "values": embedding_data,
            "metadata": {"type": "a2a_agent", "agent_id": str(agent_id)},
        }
        _require_vector_index().upsert([vector_data])

    async def _update_db_and_pinecone(self, repo, action, *args):
        repo_method = getattr(repo, REPO_ACTIONS_MAP.get(action, action), None)
        schema: BaseModel | None = None
        existing_description: str | None = None
        new_description: str | None = None
        primary_key: str | None = None
        # We only want to update Pinecone index on create, update, patch, delete
        if action in [viewset.UPDATE, viewset.PATCH, viewset.DELETE]:
            primary_key = args[0]
            existing_agent = await repo.get(primary_key)
            if existing_agent:
                existing_description = (
                    existing_agent.get("agent_card", {}).get("description", "")
                    if existing_agent
                    else ""
                )
            else:
                raise HTTPException(status_code=404, detail="Agent not found")
        if action in [viewset.CREATE, viewset.UPDATE, viewset.PATCH]:
            schema = args[0] if action == viewset.CREATE else args[1]
            # agent_card field is optional and may be None in schema
            new_description = (
                schema.agent_card.description if schema.agent_card else None
            )
        if action == viewset.PATCH and new_description is None:
            # For patch, if description not provided, keep existing one
            new_description = existing_description
        # Update DB
        result = await repo_method(*args)
        # Update Pinecone index if description changed
        if result and existing_description != new_description:
            if new_description:
                await self.update_pinecone_index(result[repo.pk_field], new_description)
            elif existing_description:
                # No new description but had existing one,
                # this will also cover delete case
                _require_vector_index().delete([str(primary_key)])
        return result

    async def _handle_operation(
        self, action: str, repo: viewset.ViewSetRepository, *args
    ):
        """Generic handler for CRUD operations."""
        repo_method = getattr(repo, REPO_ACTIONS_MAP.get(action, action))
        if action in [viewset.LIST, viewset.RETRIEVE]:
            # No update to Pinecone index for read operations
            return await repo_method(*args)

        async def operation():
            return await self._update_db_and_pinecone(repo, action, *args)

        if self.use_transactions:
            return await viewset._require_repository_provider().run_in_transaction(
                operation
            )
        return await operation()

    def get_filters(self, filter_params):
        base_query = super().get_filters(filter_params)
        filters = filter_params.filters if filter_params else {}

        # Always exclude null agent_ids
        base_query["agent_id"] = {"$ne": None}

        if "search" in filters:
            search_term = filters.pop("search")
            base_query["$or"] = [
                {"name": {"$regex": search_term, "$options": "i"}},
                {"agent_card.description": {"$regex": search_term, "$options": "i"}},
                {"tags": {"$in": [search_term]}},
            ]

        return base_query
