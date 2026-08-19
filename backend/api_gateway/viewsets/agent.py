from fastapi import Depends, HTTPException, Query, Request, status
from pydantic import BaseModel

from api_gateway.viewsets import base as viewset
from api_gateway.viewsets.base import REPO_ACTIONS_MAP
from common.auth import ClerkUser, get_current_user, get_optional_user
from common.protocols import ViewSetFilterParams, ViewSetPaginationParams
from models.request import (
    AgentCreate,
    AgentPatch,
    AgentUpdate,
)
from models.response import AgentResponse, PaginatedResponse, PaginationMeta


class AgentViewSet(viewset.ViewSet):
    """
    CRUD router for managing agents.
    CRUD router for agent documents.
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

    def _merge_visibility_filter(
        self, base_query: dict, user: ClerkUser | None
    ) -> dict:
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
            if isinstance(agent_card, dict) and "url" in agent_card and public_url:
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

    def _add_create_route(self):
        schema_out = self.schemas[viewset.CREATE]["out"]
        schema_in = self.schemas[viewset.CREATE]["in"]

        async def create_endpoint(
            item: schema_in,
            repo: viewset.ViewSetRepository = Depends(self.get_viewset_repository),
            provider: viewset.ViewSetRepositoryProvider = Depends(
                viewset.get_viewset_repository_provider
            ),
            user: ClerkUser = Depends(get_current_user),
        ):
            del user
            return await self._create(
                repo,
                item,
                repository_provider=provider,
            )

        self.router.add_api_route(
            "",
            create_endpoint,
            methods=["POST"],
            response_model=schema_out,
            status_code=status.HTTP_201_CREATED,
        )

    def _add_update_route(self):
        schema_out = self.schemas[viewset.UPDATE]["out"]
        schema_in = self.schemas[viewset.UPDATE]["in"]

        async def update_endpoint(
            item_id: str,
            item: schema_in,
            repo: viewset.ViewSetRepository = Depends(self.get_viewset_repository),
            provider: viewset.ViewSetRepositoryProvider = Depends(
                viewset.get_viewset_repository_provider
            ),
            user: ClerkUser = Depends(get_current_user),
        ):
            return await self._update(
                repo,
                item_id,
                item,
                repository_provider=provider,
                user=user,
            )

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
            provider: viewset.ViewSetRepositoryProvider = Depends(
                viewset.get_viewset_repository_provider
            ),
            user: ClerkUser = Depends(get_current_user),
        ):
            return await self._patch(
                repo,
                item_id,
                item,
                repository_provider=provider,
                user=user,
            )

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
            provider: viewset.ViewSetRepositoryProvider = Depends(
                viewset.get_viewset_repository_provider
            ),
            user: ClerkUser = Depends(get_current_user),
        ):
            return await self._delete(
                repo,
                item_id,
                repository_provider=provider,
                user=user,
            )

        self.router.add_api_route(
            "/{item_id}",
            delete_endpoint,
            methods=["DELETE"],
            status_code=status.HTTP_204_NO_CONTENT,
        )

    async def _update_db(self, repo, action, *args, user=None):
        repo_method = getattr(repo, REPO_ACTIONS_MAP.get(action, action), None)
        if action in [viewset.UPDATE, viewset.PATCH, viewset.DELETE]:
            existing_agent = await repo.get(args[0])
            if existing_agent:
                if (
                    user is not None
                    and existing_agent.get("provider_id") != user.user_id
                ):
                    raise HTTPException(
                        status_code=403,
                        detail="You do not have permission to modify this agent",
                    )
            else:
                raise HTTPException(status_code=404, detail="Agent not found")
        return await repo_method(*args)

    async def _handle_operation(
        self,
        action: str,
        repo: viewset.ViewSetRepository,
        *args,
        user=None,
        repository_provider: viewset.ViewSetRepositoryProvider | None = None,
    ):
        """Generic handler for CRUD operations."""
        repo_method = getattr(repo, REPO_ACTIONS_MAP.get(action, action))
        if action in [viewset.LIST, viewset.RETRIEVE]:
            return await repo_method(*args)

        async def operation():
            return await self._update_db(repo, action, *args, user=user)

        if self.use_transactions:
            if repository_provider is None:
                raise RuntimeError(
                    "ViewSet repository provider is required for transactions"
                )
            return await repository_provider.run_in_transaction(operation)
        return await operation()

    async def _create(
        self,
        repo: viewset.ViewSetRepository,
        item,
        **operation_deps,
    ):
        result = await self._handle_operation(
            viewset.CREATE,
            repo,
            item,
            **operation_deps,
        )
        if not result:
            raise HTTPException(
                status_code=400, detail=f"Failed to create {self.resource_name}"
            )
        return result

    async def _update(
        self,
        repo: viewset.ViewSetRepository,
        item_id,
        item,
        **operation_deps,
    ):
        result = await self._handle_operation(
            viewset.UPDATE,
            repo,
            item_id,
            item,
            **operation_deps,
        )
        if not result:
            raise HTTPException(
                status_code=400, detail=f"Failed to update {self.resource_name}"
            )
        return result

    async def _patch(
        self,
        repo: viewset.ViewSetRepository,
        item_id,
        item,
        **operation_deps,
    ):
        result = await self._handle_operation(
            viewset.PATCH,
            repo,
            item_id,
            item,
            **operation_deps,
        )
        if not result:
            raise HTTPException(
                status_code=400, detail=f"Failed to patch {self.resource_name}"
            )
        return result

    async def _delete(
        self,
        repo: viewset.ViewSetRepository,
        item_id,
        **operation_deps,
    ):
        return await self._handle_operation(
            viewset.DELETE,
            repo,
            item_id,
            **operation_deps,
        )

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
