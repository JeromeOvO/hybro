from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, TypeAlias, runtime_checkable

from pydantic import BaseModel
from models.agent import Agent, AgentCapabilityIssue, IssueStatus
from models.request import (
    AgentCenterRequest,
    ChatMemoryRequest,
    InspectionCenterRequest,
    RoomCenterRoomMessageRequest,
    RoomCenterRoomSettingRequest,
    RoomCenterUserMessageRequest,
)
from models.response import (
    AgentCenterResponse,
    ChatMemoryResponse,
    InsepectionCenterConnectionValidationResponse,
    InspectionCenterResponse,
    RoomCenterRoomMessageResponse,
    RoomCenterRoomSettingResponse,
    RoomCenterUserMessageResponse,
)


JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonMap: TypeAlias = Mapping[str, JsonValue]
RoutePayload: TypeAlias = BaseModel | JsonMap
ViewSetResult: TypeAlias = BaseModel | JsonMap | None
VectorIndexResult: TypeAlias = JsonMap | None


@runtime_checkable
class InspectionCenter(Protocol):
    async def inspect_a2a_connection(
        self, request: InspectionCenterRequest
    ) -> InsepectionCenterConnectionValidationResponse: ...
    async def inspect_agent_card(
        self, request: InspectionCenterRequest
    ) -> InspectionCenterResponse: ...


@runtime_checkable
class ViewSetRepository(Protocol):
    async def create(self, data: RoutePayload) -> ViewSetResult: ...
    async def delete(self, item_id: str | int) -> bool | ViewSetResult: ...
    async def get(self, item_id: str | int) -> ViewSetResult: ...
    async def get_all(
        self,
        *,
        skip: int = 0,
        limit: int = 100,
        filters: JsonMap | None = None,
        sort: list[tuple[str, int]] | None = None,
    ) -> list[ViewSetResult]: ...
    async def patch(self, item_id: str | int, data: RoutePayload) -> ViewSetResult: ...
    async def update(self, item_id: str | int, data: RoutePayload) -> ViewSetResult: ...


@runtime_checkable
class ViewSetDatabaseProvider(Protocol):
    def __call__(self) -> BaseModel | JsonMap: ...


@runtime_checkable
class ViewSetRepositoryFactory(Protocol):
    def __call__(
        self,
        *,
        collection_name: str,
        db: BaseModel | JsonMap,
        pinecone: BaseModel | JsonMap | None,
        pk_field: str = "_id",
    ) -> ViewSetRepository: ...


@runtime_checkable
class WebhookTransport(Protocol):
    async def handle_webhook(
        self, message_id: str, payload: dict[str, JsonValue], token: str
    ) -> dict[str, JsonValue]: ...


@runtime_checkable
class WebhookTransportFactory(Protocol):
    def __call__(self) -> WebhookTransport: ...


@runtime_checkable
class AgentCenterRouteOwner(Protocol):
    async def register_agent(self, request: AgentCenterRequest) -> AgentCenterResponse: ...
    async def get_agents_by_provider_id(
        self, request: AgentCenterRequest
    ) -> AgentCenterResponse: ...
    async def remove_agent(self, request: AgentCenterRequest) -> AgentCenterResponse: ...
    async def update_agent(self, request: AgentCenterRequest) -> AgentCenterResponse: ...
    async def get_agent_card_from_url(
        self, request: AgentCenterRequest
    ) -> AgentCenterResponse: ...
    async def query_agent_by_agent_id(
        self, request: AgentCenterRequest
    ) -> AgentCenterResponse: ...
    async def get_all_agents(self, request: AgentCenterRequest) -> AgentCenterResponse: ...
    async def get_all_active_agents(
        self, request: AgentCenterRequest
    ) -> AgentCenterResponse: ...
    async def get_agents_with_conditions(
        self, request: AgentCenterRequest
    ) -> AgentCenterResponse: ...
    def _mask_sensitive_information(
        self, response: AgentCenterResponse, fields: list[str]
    ) -> AgentCenterResponse: ...


@runtime_checkable
class AgentLookup(Protocol):
    async def get_agent_by_agent_id(self, agent_id: str) -> Agent | None: ...


@runtime_checkable
class AgentCapabilityIssueStore(Protocol):
    async def get_issues_for_agent(
        self,
        agent_id: str,
        *,
        status: IssueStatus | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AgentCapabilityIssue]: ...
    async def resolve_all_for_agent(self, agent_id: str, provider_id: str) -> int: ...
    async def get_issue_by_id(self, issue_id: str) -> AgentCapabilityIssue | None: ...
    async def resolve_issue(
        self, issue_id: str, provider_id: str
    ) -> AgentCapabilityIssue | None: ...


@runtime_checkable
class AgentLivenessChecker(Protocol):
    async def __call__(self, agent: Agent) -> Agent: ...


@runtime_checkable
class AgentSelectionSuggester(Protocol):
    async def suggest_agents(
        self, message_text: str, top_k: int = 3
    ) -> dict[str, JsonValue]: ...


@runtime_checkable
class EmbeddingProvider(Protocol):
    async def get_embedding(self, text: str) -> list[float] | None: ...


@runtime_checkable
class VectorIndex(Protocol):
    def upsert(self, vectors: list[dict[str, JsonValue]]) -> VectorIndexResult: ...
    def delete(self, ids: list[str]) -> VectorIndexResult: ...


@runtime_checkable
class LegacyWorkflowCenter(Protocol):
    pass


@runtime_checkable
class LegacyTaskCenter(Protocol):
    pass


@runtime_checkable
class LegacyMemoryCenter(Protocol):
    async def add_chat_context(self, request: ChatMemoryRequest) -> ChatMemoryResponse: ...
    async def get_chat_context_by_session_id(
        self, request: ChatMemoryRequest
    ) -> ChatMemoryResponse: ...
    async def update_chat_context_by_session_id(
        self, request: ChatMemoryRequest
    ) -> ChatMemoryResponse: ...
    async def delete_chat_context_by_session_id(
        self, request: ChatMemoryRequest
    ) -> ChatMemoryResponse: ...


@runtime_checkable
class RoomCenterRouteOwner(Protocol):
    async def create_new_room(
        self, request: RoomCenterRoomSettingRequest
    ) -> RoomCenterRoomSettingResponse: ...
    async def inquiry_rooms_by_room_owner_id(
        self, request: RoomCenterRoomSettingRequest
    ) -> RoomCenterRoomSettingResponse: ...
    async def inquiry_room_messages_by_room_id(
        self, request: RoomCenterRoomMessageRequest
    ) -> RoomCenterRoomMessageResponse: ...
    async def inquiry_room_setting(
        self, request: RoomCenterRoomSettingRequest
    ) -> RoomCenterRoomSettingResponse: ...
    async def update_room_setting(
        self, request: RoomCenterRoomSettingRequest
    ) -> RoomCenterRoomSettingResponse: ...
    async def create_and_parse_user_message(
        self, request: RoomCenterUserMessageRequest
    ) -> RoomCenterUserMessageResponse: ...
    async def send_message(
        self, request: RoomCenterUserMessageRequest
    ) -> RoomCenterUserMessageResponse: ...


@runtime_checkable
class SSEManagerRouteOwner(Protocol):
    async def add_connection(self, room_id: str) -> BaseModel | JsonMap: ...
    async def remove_connection(self, room_id: str, connection_id: str) -> None: ...
    def get_room_status(self, room_id: str) -> dict[str, JsonValue]: ...


__all__ = [
    "AgentCapabilityIssueStore",
    "AgentCenterRouteOwner",
    "AgentLivenessChecker",
    "AgentLookup",
    "AgentSelectionSuggester",
    "EmbeddingProvider",
    "InspectionCenter",
    "LegacyMemoryCenter",
    "LegacyTaskCenter",
    "LegacyWorkflowCenter",
    "RoomCenterRouteOwner",
    "SSEManagerRouteOwner",
    "VectorIndex",
    "ViewSetDatabaseProvider",
    "ViewSetRepository",
    "ViewSetRepositoryFactory",
    "WebhookTransport",
    "WebhookTransportFactory",
]
