from collections.abc import Callable, Iterable

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel

from app_shell.bound import ViewSetRepository, ViewSetRepositoryProvider, ViewSetResult
from models.request import FilterParams, PaginationParams

# from models.response import PaginatedResponse

# CRUD operation constants
LIST = "list"
RETRIEVE = "retrieve"
CREATE = "create"
UPDATE = "update"
PATCH = "patch"
DELETE = "delete"

REPO_ACTIONS_MAP = {
    LIST: "get_all",
    RETRIEVE: "get",
    CREATE: "create",
    UPDATE: "update",
    DELETE: "delete",
    PATCH: "patch",
}

repository_provider: ViewSetRepositoryProvider | None = None


def bind_viewset_dependencies(
    *,
    provider: ViewSetRepositoryProvider,
) -> None:
    global repository_provider

    repository_provider = provider


def _require_repository_provider() -> ViewSetRepositoryProvider:
    if repository_provider is None:
        raise RuntimeError("ViewSet repository provider has not been bound")
    return repository_provider


class ViewSet:
    """
    A class-based CRUD router generator for FastAPI with MongoDB support.
    
    Arguments:
        resource_name (str): The name of the resource (used in the URL path).
        collection_name (str): The name of the collection in the database.
        schema_in (type[BaseModel] | None): Pydantic model for input data.
        schema_out (type[BaseModel] | None): Pydantic model for output data.
        schemas (dict[str, dict[str, type[BaseModel]]] | None): Specific schemas per operation.
        allow (Iterable[str]): List of allowed operations (LIST, RETRIEVE, CREATE, DELETE, UPDATE, PATCH).
        use_transactions (bool): Whether to use transactions for operations.
        id_type (type): The type of the item ID (default is str).
        pk_field (str): The name of the primary key field (default is "_id").

    Features:
    - Automatic CRUD endpoint generation
    - Customizable operations (allow/disallow specific operations)
    - Override support for custom implementations
    - Transaction support
    - Validation and error handling
    """

    def __init__(
        self,
        *,
        resource_name: str,
        collection_name: str,
        # Schema configuration - can be single schemas or per-method
        schema_in: type[BaseModel] | None = None,
        schema_out: type[BaseModel] | None = None,
        schemas: dict[str, dict[str, type[BaseModel]]] | None = None,
        # Operation configuration
        allow: Iterable[str] = (LIST, RETRIEVE, CREATE, DELETE, UPDATE, PATCH),
        use_transactions: bool = False,
        id_type: type = str,
        pk_field: str = "_id",
    ):
        self.resource_name = resource_name
        self.collection_name = collection_name
        self.allow = set(allow)
        self.use_transactions = use_transactions
        self.id_type = id_type
        self.pk_field = pk_field

        # Configure schemas - either global or per-method
        self._setup_schemas(schema_in, schema_out, schemas)

        self.router = APIRouter(
            prefix=f"/{resource_name}", tags=[resource_name.capitalize()]
        )

        # Build the router
        self._build_router()

    def _setup_schemas(
        self,
        schema_in: type[BaseModel] | None,
        schema_out: type[BaseModel] | None,
        schemas: dict[str, dict[str, type[BaseModel]]] | None,
    ):
        """Setup schemas for each operation."""
        # Default schemas for all operations
        default_schemas = {
            LIST: {"out": schema_out},
            RETRIEVE: {"out": schema_out},
            CREATE: {"in": schema_in, "out": schema_out},
            UPDATE: {"in": schema_in, "out": schema_out},
            PATCH: {"in": schema_in, "out": schema_out},
            DELETE: {"out": None},  # No response schema for delete
        }

        # Override with specific schemas if provided
        if schemas:
            for operation, operation_schemas in schemas.items():
                if operation in default_schemas:
                    default_schemas[operation].update(operation_schemas)

        self.schemas = default_schemas

    def _build_router(self):
        """Build the FastAPI router with all enabled endpoints."""
        actions = [LIST, RETRIEVE, CREATE, DELETE, UPDATE, PATCH]
        for action in actions:
            if action in self.allow:
                getattr(self, f"_add_{action}_route")()

    def get_viewset_repository(self) -> ViewSetRepository:
        return _require_repository_provider().get_repository(
            collection_name=self.collection_name,
            pk_field=self.pk_field,
        )

    def _add_list_route(self):
        """Add GET / route for listing items."""
        schema_out = self.schemas[LIST]["out"]

        async def list_endpoint(
            request: Request,
            repo: ViewSetRepository = Depends(self.get_viewset_repository),
            page: int | None = Query(None, ge=1, description="Page number (1-indexed)"),
            limit: int | None = Query(
                None, ge=1, le=100, description="Number of items per page"
            ),
            sort_by: str | None = Query(None, description="Field to sort by"),
            sort_order: int = Query(
                -1, description="Sort order: 1 for ascending, -1 for descending"
            ),
        ):
            # Extract filters from query parameters (excluding pagination/sort params)
            filter_dict = {
                k: v
                for k, v in request.query_params.items()
                if k not in ["page", "limit", "sort_by", "sort_order"]
            }
            # Create pagination params if page or limit is provided
            pagination = None
            if page is not None or limit is not None:
                pagination = PaginationParams(page=page or 1, limit=limit or 10)

            # Create filter params
            filter_params = FilterParams(
                filters=filter_dict, sort_by=sort_by, sort_order=sort_order
            )

            return await self._list(repo, pagination, filter_params)

        # Use the configured schema for LIST operation
        response_model = schema_out

        self.router.add_api_route(
            "",
            list_endpoint,
            methods=["GET"],
            response_model=response_model,
        )

    def _add_retrieve_route(self):
        """Add GET /{item_id} route for retrieving single item."""
        schema_out = self.schemas[RETRIEVE]["out"]

        async def retrieve_endpoint(
            item_id: str,
            repo: ViewSetRepository = Depends(self.get_viewset_repository),
        ):
            result = await self._retrieve(repo, item_id)
            if not result:
                raise HTTPException(
                    status_code=404, detail=f"{self.resource_name} not found"
                )
            return result

        self.router.add_api_route(
            "/{item_id}",
            retrieve_endpoint,
            methods=["GET"],
            response_model=schema_out,
        )

    def _add_create_route(self):
        """Add POST / route for creating items."""
        schema_out = self.schemas[CREATE]["out"]
        schema_in = self.schemas[CREATE]["in"]

        async def create_endpoint(
            item: schema_in,
            repo: ViewSetRepository = Depends(self.get_viewset_repository),
        ):
            return await self._create(repo, item)

        self.router.add_api_route(
            "",
            create_endpoint,
            methods=["POST"],
            response_model=schema_out,
            status_code=status.HTTP_201_CREATED,
        )

    def _add_update_route(self):
        """Add PUT /{item_id} route for full updates."""
        schema_out = self.schemas[UPDATE]["out"]
        schema_in = self.schemas[UPDATE]["in"]

        async def update_endpoint(
            item_id: str,
            item: schema_in,
            repo: ViewSetRepository = Depends(self.get_viewset_repository),
        ):
            return await self._update(repo, item_id, item)

        self.router.add_api_route(
            "/{item_id}",
            update_endpoint,
            methods=["PUT"],
            response_model=schema_out,
        )

    def _add_patch_route(self):
        """Add PATCH /{item_id} route for partial updates."""
        schema_out = self.schemas[PATCH]["out"]
        schema_in = self.schemas[PATCH]["in"]

        async def patch_endpoint(
            item_id: str,
            item: schema_in,
            repo: ViewSetRepository = Depends(self.get_viewset_repository),
        ):
            return await self._patch(repo, item_id, item)

        self.router.add_api_route(
            "/{item_id}",
            patch_endpoint,
            methods=["PATCH"],
            response_model=schema_out,
        )

    def _add_delete_route(self):
        """Add DELETE /{item_id} route for deleting items."""

        async def delete_endpoint(
            item_id: str,
            repo: ViewSetRepository = Depends(self.get_viewset_repository),
        ):
            return await self._delete(repo, item_id)

        self.router.add_api_route(
            "/{item_id}",
            delete_endpoint,
            methods=["DELETE"],
            status_code=status.HTTP_204_NO_CONTENT,
        )

    # --- Default endpoint implementations (can be overridden) ---

    async def _handle_operation(
        self, action: str, repo: ViewSetRepository, *args
    ) -> ViewSetResult:
        """Generic handler for CRUD operations."""
        method = getattr(repo, REPO_ACTIONS_MAP.get(action, action))

        async def operation() -> ViewSetResult:
            return await method(*args)

        if self.use_transactions:
            return await _require_repository_provider().run_in_transaction(operation)
        return await operation()

    def get_filters(
        self, filter_params: FilterParams | None = None
    ) -> dict:
        """
        Get the base query filter for the list endpoint.
        Similar to Django's get_queryset - override in subclasses for custom filtering.

        Args:
            db: Database connection
            filter_params: Filter parameters from request

        Returns:
            dict: MongoDB filter query

        Example override:
        def get_filters(self, db, filter_params):
            base_query = super().get_filters(db, filter_params)
            filters = filter_params.filters if filter_params else {}

            if "search" in filters:
                search_term = filters.pop("search")
                base_query["$or"] = [
                    {"name": {"$regex": search_term, "$options": "i"}},
                    {"description": {"$regex": search_term, "$options": "i"}},
                    {"tags": {"$in": [search_term]}}
                ]

            return base_query
        """
        if filter_params and filter_params.filters:
            return filter_params.filters
        return {}

    async def _list(
        self,
        repo: ViewSetRepository,
        pagination: PaginationParams | None = None,
        filters: FilterParams | None = None,
    ):
        # Use the customizable get_filters method
        custom_filters = self.get_filters(filters)

        # Create a new FilterParams with the processed query
        processed_filters = FilterParams(
            filters=custom_filters,
            sort_by=filters.sort_by if filters else None,
            sort_order=filters.sort_order if filters else -1,
        )

        return await self._handle_operation(
            LIST, repo, pagination, processed_filters
        )

    async def _retrieve(self, repo: ViewSetRepository, item_id):
        result = await self._handle_operation(RETRIEVE, repo, item_id)
        # if not result:
        #     raise HTTPException(
        #         status_code=404, detail=f"{self.resource_name} not found"
        #     )
        return result

    async def _create(self, repo: ViewSetRepository, item):
        result = await self._handle_operation(CREATE, repo, item)
        if not result:
            raise HTTPException(
                status_code=400, detail=f"Failed to create {self.resource_name}"
            )
        return result

    async def _update(self, repo: ViewSetRepository, item_id, item):
        result = await self._handle_operation(UPDATE, repo, item_id, item)
        if not result:
            raise HTTPException(
                status_code=400, detail=f"Failed to update {self.resource_name}"
            )
        return result

    async def _patch(self, repo: ViewSetRepository, item_id, item):
        result = await self._handle_operation(PATCH, repo, item_id, item)
        if not result:
            raise HTTPException(
                status_code=400, detail=f"Failed to patch {self.resource_name}"
            )
        return result

    async def _delete(self, repo: ViewSetRepository, item_id):
        return await self._handle_operation(DELETE, repo, item_id)

    # --- Public methods for customization ---

    def add_custom_route(
        self, path: str, endpoint: Callable, methods: list[str], **kwargs
    ):
        """Add a custom route to this router."""
        self.router.add_api_route(path, endpoint, methods=methods, **kwargs)

    def get_router(self) -> APIRouter:
        """Get the configured FastAPI router."""
        return self.router
