from fastapi import Depends, HTTPException, Query, Request, status
from pydantic import BaseModel

from api_gateway.viewsets import base as viewset
from api_gateway.viewsets.base import REPO_ACTIONS_MAP
from common.auth import ClerkUser, get_current_user, get_optional_user
from common.protocols import (
    AgentVectorIndexWriter,
    EmbeddingServiceProtocol,
    ViewSetFilterParams,
    ViewSetPaginationParams,
)
from models.request import (
    AgentCreate,
    AgentPatch,
    AgentUpdate,
)
from models.response import AgentResponse, PaginatedResponse, PaginationMeta

embedding_provider: EmbeddingServiceProtocol | None = None
vector_index: AgentVectorIndexWriter | None = None


def bind_agent_viewset_dependencies(
    *,
    embedding_source: EmbeddingServiceProtocol,
    vector_index_service: AgentVectorIndexWriter,
) -> None:
    global embedding_provider, vector_index

    embedding_provider = embedding_source
    vector_index = vector_index_service


def _require_embedding_provider() -> EmbeddingServiceProtocol:
    if embedding_provider is None:
        raise RuntimeError("AgentViewSet embedding dependency has not been bound")
    return embedding_provider


def _require_vector_index() -> AgentVectorIndexWriter:
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

    def _visibility_filter(self, user: ClerkUser | None) -> dict:
        public_filter = {"is_public": {"$ne": False}}
        if user is None:
            return public_filter
        return {"$or": [public_filter, {"provider_id": user.user_id}]}

    def _merge_visibility_filter(self, base_query: dict, user: ClerkUser | None) -> dict:
        visibility = self._visibility_filter(user)
        if "$or" in base_query:
            return {"$and": [base_query, visibility]}
        return {**base_query, **visibility}

    def _is_visible(self, agent: dict, user: ClerkUser | None) -> bool:
        if agent.get("is_public") is not False:
            return True
        return user is not None and agent.get("provider_id") == user.user_id

    def _mask_agent_payload(self, value):
        if isinstance(value, BaseModel):
            value = value.model_dump(mode="json")
        if isinstance(value, dict):
            public_url = value.get("public_url")
            agent_card = value.get("agent_card")
            if isinstance(agent_card, BaseModel):
                agent_card = agent_card.model_dump(mode="json")
                value["agent_card"] = agent_card
            if isinstance(agent_card, dict) and "url" in agent_card:
                agent_card["url"] = public_url
        return value

    def _add_list_route(self):
        """Add GET / route for listing visible agents."""
        schema_out = self.schemas[viewset.LIST]["out"]

        async def list_endpoint(
            request: Request,
            repo: viewset.ViewSetRepository = Depends(self.get_viewset_repository),
            user: ClerkUser | None = Depends(get_optional_user),
            page: int | None = Query(None, ge=1, description="Page number (1-indexed)"),
            limit: int | None = Query(
                None, ge=1, le=100, description="Number of items per page"
            ),
            sort_by: str | None = Query(None, description="Field to sort by"),
            sort_order: int = Query(
                -1, description="Sort order: 1 for ascending, -1 for descending"
            ),
        ):
            filter_dict = {
                k: v
                for k, v in request.query_params.items()
                if k not in ["page", "limit", "sort_by", "sort_order"]
            }
            pagination = None
            if page is not None or limit is not None:
                pagination = ViewSetPaginationParams(page=page or 1, limit=limit or 10)
            filter_params = ViewSetFilterParams(
                filters=filter_dict, sort_by=sort_by, sort_order=sort_order
            )
            result = await self._list(repo, pagination, filter_params, user=user)
            items = getattr(result, "items", None)
            if items is not None:
                result.items = [self._mask_agent_payload(item) for item in items]
            return result

        self.router.add_api_route(
            "",
            list_endpoint,
            methods=["GET"],
            response_model=schema_out,
        )

    def _add_retrieve_route(self):
        """Add GET /{item_id} route for retrieving a visible agent."""
        schema_out = self.schemas[viewset.RETRIEVE]["out"]

        async def retrieve_endpoint(
            item_id: str,
            repo: viewset.ViewSetRepository = Depends(self.get_viewset_repository),
            user: ClerkUser | None = Depends(get_optional_user),
        ):
            result = await self._retrieve(repo, item_id)
            if not result or not self._is_visible(result, user):
                raise HTTPException(status_code=404, detail="agents not found")
            return self._mask_agent_payload(result)

        self.router.add_api_route(
            "/{item_id}",
            retrieve_endpoint,
            methods=["GET"],
            response_model=schema_out,
        )

    def _add_update_route(self):
        schema_out = self.schemas[viewset.UPDATE]["out"]
        schema_in = self.schemas[viewset.UPDATE]["in"]

        async def update_endpoint(
            item_id: str,
            item: schema_in,
            repo: viewset.ViewSetRepository = Depends(self.get_viewset_repository),
            user: ClerkUser = Depends(get_current_user),
        ):
            return await self._update(repo, item_id, item, user=user)

        self.router.add_api_route(
            "/{item_id}",
            update_endpoint,
            methods=["PUT"],
            response_model=schema_out,
        )

    def _add_patch_route(self):
        schema_out = self.schemas[viewset.PATCH]["out"]
        schema_in = self.schemas[viewset.PATCH]["in"]

        async def patch_endpoint(
            item_id: str,
            item: schema_in,
            repo: viewset.ViewSetRepository = Depends(self.get_viewset_repository),
            user: ClerkUser = Depends(get_current_user),
        ):
            return await self._patch(repo, item_id, item, user=user)

        self.router.add_api_route(
            "/{item_id}",
            patch_endpoint,
            methods=["PATCH"],
            response_model=schema_out,
        )

    def _add_delete_route(self):
        async def delete_endpoint(
            item_id: str,
            repo: viewset.ViewSetRepository = Depends(self.get_viewset_repository),
            user: ClerkUser = Depends(get_current_user),
        ):
            return await self._delete(repo, item_id, user=user)

        self.router.add_api_route(
            "/{item_id}",
            delete_endpoint,
            methods=["DELETE"],
            status_code=status.HTTP_204_NO_CONTENT,
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

    async def _update_db_and_pinecone(self, repo, action, *args, user=None):
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
                if user is not None and existing_agent.get("provider_id") != user.user_id:
                    raise HTTPException(
                        status_code=403,
                        detail="You do not have permission to modify this agent",
                    )
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
        self, action: str, repo: viewset.ViewSetRepository, *args, user=None
    ):
        """Generic handler for CRUD operations."""
        repo_method = getattr(repo, REPO_ACTIONS_MAP.get(action, action))
        if action in [viewset.LIST, viewset.RETRIEVE]:
            # No update to Pinecone index for read operations
            return await repo_method(*args)

        async def operation():
            return await self._update_db_and_pinecone(repo, action, *args, user=user)

        if self.use_transactions:
            return await viewset._require_repository_provider().run_in_transaction(
                operation
            )
        return await operation()

    async def _update(self, repo: viewset.ViewSetRepository, item_id, item, *, user=None):
        result = await self._handle_operation(viewset.UPDATE, repo, item_id, item, user=user)
        if not result:
            raise HTTPException(
                status_code=400, detail=f"Failed to update {self.resource_name}"
            )
        return result

    async def _patch(self, repo: viewset.ViewSetRepository, item_id, item, *, user=None):
        result = await self._handle_operation(viewset.PATCH, repo, item_id, item, user=user)
        if not result:
            raise HTTPException(
                status_code=400, detail=f"Failed to patch {self.resource_name}"
            )
        return result

    async def _delete(self, repo: viewset.ViewSetRepository, item_id, *, user=None):
        return await self._handle_operation(viewset.DELETE, repo, item_id, user=user)

    def get_filters(self, filter_params, user: ClerkUser | None = None):
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

        return self._merge_visibility_filter(base_query, user)

    async def _list(
        self,
        repo: viewset.ViewSetRepository,
        pagination: ViewSetPaginationParams | None = None,
        filters: ViewSetFilterParams | None = None,
        *,
        user: ClerkUser | None = None,
    ):
        custom_filters = self.get_filters(filters, user=user)
        processed_filters = ViewSetFilterParams(
            filters=custom_filters,
            sort_by=filters.sort_by if filters else None,
            sort_order=filters.sort_order if filters else -1,
        )

        return await self._handle_operation(
            viewset.LIST, repo, pagination, processed_filters
        )
