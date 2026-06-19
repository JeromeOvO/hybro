from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from common.a2a_constants import is_terminal_state
from common.dto import (
    AgentInfo,
    AgentMessageInput,
    CreateRoomRequest,
    HubPublishLineageSnapshot,
    MembershipSeed,
    MembershipUpdateRequest,
    RoomInfo,
    RoomMessageInfo,
    SavedUserMessage,
    UserMessageInput,
)
from common.observability import NoopTracingProvider
from common.protocols import (
    AgentRegistry,
    AttachmentMetadataReader,
    MessageRepository,
    RoomMembershipSeedSource,
    RoomRepository,
)
from common.types import Message, Part, TaskState, TaskStatus, TextPart
from common.types import MessageRole as Role
from common.utils.a2a_helpers import sanitize_task_dict
from common.utils.logger import get_logger
from common.utils.time import ensure_utc, utcnow
from models.quote import QuotedSnippet, QuoteSourceKind
from models.response import RoomCenterUserMessageResponse
from models.room import Room, RoomAgentMessage, RoomUserMessage
from room.membership import resolve_membership_seed
from room.translators import (
    agent_message_doc_from_input,
    create_room_doc,
    message_info_from_doc,
    room_info_from_doc,
    saved_user_message_from_doc,
    user_message_doc_from_input,
)

_ALLOWED_ROOM_UPDATE_KEYS = frozenset({
    "room_name",
    "extend_info",
    "processing_message_id",
})

logger = get_logger(__name__)


class RoomFacade:
    def __init__(
        self,
        *,
        repository: RoomRepository,
        message_repository: MessageRepository,
        agent_registry: AgentRegistry,
        membership_source: RoomMembershipSeedSource,
        quote_repository: Any | None = None,
        attachment_metadata_reader: AttachmentMetadataReader | None = None,
        id_factory: Callable[[], str],
        now: Callable[[], datetime],
        tracer: Any | None = None,
    ) -> None:
        self._repository = repository
        self._message_repository = message_repository
        self._agent_registry = agent_registry
        self._membership_source = membership_source
        self._quote_repository = quote_repository
        self._attachment_metadata_reader = attachment_metadata_reader
        self._id_factory = id_factory
        self._now = now
        self._tracer = tracer or NoopTracingProvider()

    async def get_room(self, room_id: str) -> RoomInfo | None:
        doc = await self._repository.get_by_id(room_id)
        return room_info_from_doc(doc) if doc is not None else None

    async def get_room_agents(self, room_id: str) -> list[str]:
        room = await self.get_room(room_id)
        return list(room.agent_ids) if room is not None else []

    async def get_room_owner(self, room_id: str) -> str | None:
        doc = await self._repository.get_by_id(room_id)
        return _owner_id_from_doc(doc)

    async def create_room(self, request: CreateRoomRequest) -> RoomInfo:
        self._validate_create_room_request(request)
        resolved = await resolve_membership_seed(
            seed=request.membership_seed,
            owner_id=request.owner_id,
            agent_registry=self._agent_registry,
            membership_source=self._membership_source,
        )
        room_id = self._id_factory()
        doc = create_room_doc(
            room_id=room_id,
            owner_id=request.owner_id,
            owner_name=request.owner_name,
            room_name=request.room_name,
            agent_set=resolved.agent_set,
            created_at=self._now(),
            membership_origin=resolved.membership_origin,
            membership_origin_status=resolved.membership_origin_status,
            source_group_id=resolved.source_group_id,
            source_group_name=resolved.source_group_name,
            extend_info=request.extend_info,
        )
        await self._repository.create(doc)
        return room_info_from_doc(doc)

    async def delete_room(self, room_id: str, owner_id: str) -> bool:
        doc = await self._repository.get_by_id(room_id)
        if _owner_id_from_doc(doc) != owner_id:
            return False
        await self._message_repository.delete_for_room(room_id)
        return await self._repository.delete(room_id)

    async def update_room(self, room_id: str, updates: dict) -> RoomInfo | None:
        unknown = set(updates) - _ALLOWED_ROOM_UPDATE_KEYS
        if unknown:
            raise ValueError(f"Unknown room update keys: {sorted(unknown)}")
        updated = await self._repository.update_fields(room_id, dict(updates))
        return room_info_from_doc(updated) if updated is not None else None

    async def update_membership(
        self, room_id: str, request: MembershipUpdateRequest
    ) -> RoomInfo:
        doc = await self._repository.get_by_id(room_id)
        if doc is None:
            raise ValueError("Room not found")

        room = room_info_from_doc(doc)
        agent_set = dict(room.agent_set)
        for agent_id in request.remove_agent_ids or []:
            agent_set.pop(agent_id, None)

        additions = await self._resolve_agent_ids_for_update(
            list(request.add_agent_ids or []),
            requesting_user_id=room.owner_id,
        )
        agent_set.update(additions)

        origin = room.membership_origin
        status = room.membership_origin_status
        if origin in {"saved_group", "all_current_agents"}:
            status = "seeded_edited"
        else:
            origin = "manual"
            status = "manual"

        updated = await self._repository.set_membership(
            room_id,
            agent_set=agent_set,
            membership_origin=origin,
            membership_origin_status=status,
            source_group_id=room.source_group_id,
            source_group_name=room.source_group_name,
        )
        if updated is None:
            raise ValueError("Room not found")
        return room_info_from_doc(updated)

    async def list_rooms_for_owner(self, owner_id: str) -> list[RoomInfo]:
        return [
            room_info_from_doc(doc)
            for doc in await self._repository.get_by_owner(owner_id)
        ]

    async def replace_membership(
        self,
        room_id: str,
        seed: MembershipSeed,
        requesting_user_id: str | None = None,
    ) -> RoomInfo:
        doc = await self._repository.get_by_id(room_id)
        if doc is None:
            raise ValueError("Room not found")
        room = room_info_from_doc(doc)
        resolved_seed = seed
        if requesting_user_id is not None:
            resolved_seed = MembershipSeed(
                mode=seed.mode,
                agent_ids=list(seed.agent_ids) if seed.agent_ids is not None else None,
                group_id=seed.group_id,
                requesting_user_id=requesting_user_id,
            )
        resolved = await resolve_membership_seed(
            seed=resolved_seed,
            owner_id=room.owner_id,
            agent_registry=self._agent_registry,
            membership_source=self._membership_source,
        )
        updated = await self._repository.set_membership(
            room_id,
            agent_set=resolved.agent_set,
            membership_origin=resolved.membership_origin,
            membership_origin_status=resolved.membership_origin_status,
            source_group_id=resolved.source_group_id,
            source_group_name=resolved.source_group_name,
        )
        if updated is None:
            raise ValueError("Room not found")
        return room_info_from_doc(updated)

    async def delete_room_owned_messages(self, room_id: str) -> dict[str, int]:
        return await self._message_repository.delete_for_room(room_id)

    async def delete_room_quote(self, quote_id: str) -> bool:
        if self._quote_repository is None:
            return False
        return bool(await self._quote_repository.delete_by_id(quote_id))

    async def get_attachment_for_room_file(
        self, room_id: str, file_id: str
    ) -> dict | None:
        reader = self._attachment_metadata_reader
        if reader is None:
            return None
        return await reader.get_for_room_file(room_id, file_id)

    async def cleanup_room_owned_data(self, room_id: str) -> dict[str, int]:
        result: dict[str, int] = await self._message_repository.delete_for_room(
            room_id
        )
        if self._quote_repository is not None:
            result["quotes"] = await self._quote_repository.delete_for_room(room_id)
        return result

    async def save_user_message(
        self, room_id: str, message: UserMessageInput
    ) -> SavedUserMessage:
        await self._require_room(room_id)
        message_id = self._id_factory()
        doc = user_message_doc_from_input(
            room_id=room_id,
            message_id=message_id,
            message=message,
            created_at=self._now(),
        )
        await self._message_repository.save_user_message(doc)
        return saved_user_message_from_doc(doc)

    async def persist_user_message(self, user_message: RoomUserMessage) -> bool:
        try:
            if user_message.message_id == "":
                user_message.message_id = self._id_factory()
            doc = user_message.model_dump(mode="json", exclude={"quote"})
            _strip_file_urls(doc)
            return bool(await self._message_repository.save_user_message(doc))
        except Exception:
            logger.error("Failed to persist room user message", exc_info=True)
            return False

    async def save_agent_message(self, room_id: str, message: AgentMessageInput) -> str:
        await self._require_room(room_id)
        message_id = self._id_factory()
        doc = agent_message_doc_from_input(
            room_id=room_id,
            message_id=message_id,
            message=message,
            created_at=self._now(),
        )
        return await self._message_repository.save_agent_message(doc)

    async def update_agent_message(
        self, message_id: str, message: RoomAgentMessage
    ) -> bool:
        try:
            if (
                message.message_content
                and message.message_content.message_task
                and message.message_content.message_task.metadata is None
            ):
                existing_message = await self.get_agent_message_model(message_id)
                if (
                    existing_message
                    and existing_message.message_content
                    and existing_message.message_content.message_task
                    and existing_message.message_content.message_task.metadata
                    is not None
                ):
                    message.message_content.message_task.metadata = (
                        existing_message.message_content.message_task.metadata
                    )
            update_data = _strip_unset_task_tracking_fields(
                message.model_dump(exclude_unset=True, mode="json")
            )
            return bool(
                await self._message_repository.update_agent_message(
                    message_id,
                    update_data,
                )
            )
        except Exception:
            logger.error("Failed to update room agent message", exc_info=True)
            return False

    async def update_agent_message_status(
        self, message_id: str, status: str, **kwargs: Any
    ) -> bool:
        return await self._message_repository.update_status(message_id, status, **kwargs)

    async def get_message(self, message_id: str) -> RoomMessageInfo | None:
        doc = await self._message_repository.get_by_id(message_id)
        return message_info_from_doc(doc) if doc is not None else None

    async def get_user_message_model(self, message_id: str) -> RoomUserMessage | None:
        getter = getattr(self._message_repository, "get_user_message_by_id", None)
        doc = await getter(message_id) if getter is not None else None
        return _safe_parse_user_message(doc)

    async def get_agent_message_model(self, message_id: str) -> RoomAgentMessage | None:
        getter = getattr(self._message_repository, "get_agent_message_by_id", None)
        doc = await getter(message_id) if getter is not None else None
        return _safe_parse_agent_message(doc)

    async def get_user_messages_for_room(
        self, room_id: str
    ) -> list[RoomUserMessage]:
        try:
            docs = await self._message_repository.get_user_messages_for_room(room_id)
            return [
                message
                for doc in docs
                if (message := _safe_parse_user_message(doc)) is not None
            ]
        except Exception:
            logger.error("Failed to get room user messages", exc_info=True)
            return []

    async def get_agent_messages_for_room(
        self, room_id: str
    ) -> list[RoomAgentMessage]:
        try:
            docs = await self._message_repository.get_agent_messages_for_room(room_id)
            messages = [
                message
                for doc in docs
                if (message := _safe_parse_agent_message(doc)) is not None
            ]
            await self._auto_fail_stale_agent_messages(messages)
            return messages
        except Exception:
            logger.error("Failed to get room agent messages", exc_info=True)
            return []

    async def get_agent_messages_by_related_message_id(
        self, related_message_id: str
    ) -> list[RoomAgentMessage]:
        try:
            docs = await self._message_repository.get_agent_messages_by_related_message_id(
                related_message_id
            )
            return [
                message
                for doc in docs
                if (message := _safe_parse_agent_message(doc)) is not None
            ]
        except Exception:
            logger.error("Failed to get related room agent messages", exc_info=True)
            return []

    async def get_messages_for_room(
        self, room_id: str, limit: int = 100, before: datetime | None = None
    ) -> list[RoomMessageInfo]:
        return [
            message_info_from_doc(doc)
            for doc in await self._message_repository.get_for_room(room_id, limit, before)
        ]

    async def get_messages_by_ids(
        self, message_ids: list[str]
    ) -> list[RoomMessageInfo]:
        docs = await self._message_repository.get_by_ids(message_ids)
        by_id = {str(doc.get("message_id")): doc for doc in docs}
        return [
            message_info_from_doc(by_id[message_id])
            for message_id in message_ids
            if message_id in by_id
        ]

    async def get_turn_completion_kind(self, message_id: str) -> str | None:
        doc = await self._message_repository.get_by_id(message_id)
        if not isinstance(doc, dict):
            return None
        extend_info = doc.get("extend_info")
        if not isinstance(extend_info, dict):
            return None
        kind = extend_info.get("turn_completion_kind")
        return kind if kind in {"synthesis", "deterministic"} else None

    async def materialize_quote(
        self,
        *,
        room: Room,
        request: Any,
        user_message: RoomUserMessage,
    ) -> RoomCenterUserMessageResponse | None:
        payload = user_message.quote
        if payload is None:
            return None
        text = payload.text.strip()
        if not text:
            return RoomCenterUserMessageResponse(
                message_id=None,
                message=None,
                success=False,
                error="Quote text is required",
                status_code=400,
            )

        source_kind = (
            payload.source_kind.value
            if hasattr(payload.source_kind, "value")
            else payload.source_kind
        )
        source_kind_value = str(source_kind)
        if source_kind_value not in {"unknown", QuoteSourceKind.UNKNOWN.value, ""}:
            source_message = await self.get_message(payload.source_message_id)
            if source_message is None or source_message.room_id != room.room_id:
                return RoomCenterUserMessageResponse(
                    message_id=None,
                    message=None,
                    success=False,
                    error="Invalid quote source",
                    status_code=400,
                )
            expected_message_type = {
                "user_turn": "user",
                "agent": "agent",
                "synthesis": "agent",
            }.get(source_kind_value.lower())
            if (
                expected_message_type is not None
                and source_message.message_type != expected_message_type
            ):
                return RoomCenterUserMessageResponse(
                    message_id=None,
                    message=None,
                    success=False,
                    error="Invalid quote source type",
                    status_code=400,
                )

        if self._quote_repository is None:
            return RoomCenterUserMessageResponse(
                message_id=None,
                message=None,
                success=False,
                error="Could not save quoted context. Try again.",
                status_code=503,
            )

        try:
            snippet = QuotedSnippet(
                room_id=room.room_id,
                created_by_user_id=request.user_id or user_message.user_id or "",
                text=text,
                source_message_id=payload.source_message_id,
                source_kind=str(source_kind),
                source_agent_id=payload.source_agent_id,
                sender_display_name=payload.sender_display_name,
            )
            qid = await self._quote_repository.insert(snippet)
        except Exception as exc:
            logger.exception("Quote snippet creation failed: %s", exc)
            return RoomCenterUserMessageResponse(
                message_id=None,
                message=None,
                success=False,
                error="Could not save quoted context. Try again.",
                status_code=500,
            )

        if not isinstance(qid, str):
            qid = str(qid)
        user_message.quote_id = qid
        extend_info = dict(user_message.extend_info or {})
        extend_info["quoted_text"] = text
        if payload.sender_display_name:
            extend_info["quoted_sender_name"] = payload.sender_display_name
        extend_info["quote_id"] = qid
        user_message.extend_info = extend_info
        user_message.quote = None
        return None

    async def _auto_fail_stale_agent_messages(  # noqa: C901
        self,
        messages: list[RoomAgentMessage],
    ) -> None:
        stale_task_threshold = 10 * 60

        def is_task_stale(msg: RoomAgentMessage) -> bool:
            timestamp = msg.task_updated_at or msg.task_created_at
            if timestamp is None:
                return True
            return (utcnow() - ensure_utc(timestamp)).total_seconds() > stale_task_threshold

        def mark_failed(msg: RoomAgentMessage, error_text: str) -> None:
            task = msg.message_content.message_task if msg.message_content else None
            if task:
                task.status = TaskStatus(
                    state=TaskState.failed,
                    message=Message(
                        message_id=self._id_factory(),
                        role=Role.AGENT,
                        parts=[Part(root=TextPart(text=error_text))],
                    ),
                )
            msg.task_updated_at = utcnow()

        for msg in messages:
            task = msg.message_content.message_task if msg.message_content else None
            if task is None:
                continue
            current_state = task.status.state
            if is_terminal_state(current_state):
                continue
            if not msg.has_task_tracking:
                if current_state == TaskState.working and is_task_stale(msg):
                    mark_failed(
                        msg,
                        "Task did not complete — the connection was lost, "
                        "possibly due to a server restart.",
                    )
                    await self.update_agent_message(msg.message_id, msg)
                continue
            if is_task_stale(msg):
                mark_failed(
                    msg,
                    "Task did not complete — no progress was received within "
                    "the expected timeframe. This may have been caused by "
                    "a server restart or agent failure.",
                )
                await self.update_agent_message(msg.message_id, msg)

    async def get_message_thread(
        self, parent_message_id: str
    ) -> list[RoomMessageInfo]:
        return [
            message_info_from_doc(doc)
            for doc in await self._message_repository.get_thread(parent_message_id)
        ]

    async def verify_room_agent_membership(self, room_id: str, agent_id: str) -> bool:
        return agent_id in await self.get_room_agents(room_id)

    async def verify_room_hub_ownership(self, room_id: str, hub_id: str) -> bool:
        agent_ids = await self.get_room_agents(room_id)
        if not agent_ids:
            return False
        agents = await self._agent_registry.get_agents_by_ids(agent_ids)
        return any(agent.hub_id == hub_id for agent in agents)

    async def get_hub_publish_lineage(
        self, *, room_id: str, agent_message_id: str
    ) -> HubPublishLineageSnapshot | None:
        room_doc = await self._repository.get_by_id(room_id)
        if room_doc is None:
            return None
        message_doc = await self._message_repository.get_by_id(agent_message_id)
        if message_doc is None or message_doc.get("room_id") != room_id:
            return None
        agent_id = message_doc.get("agent_id")
        if not agent_id:
            return None
        agents = await self._agent_registry.get_agents_by_ids([agent_id])
        if not agents:
            return None
        agent = agents[0]
        related_message_id = (
            message_doc.get("related_message_id") or message_doc.get("parent_message_id")
        )
        task_data = (
            message_doc.get("message_content", {})
            .get("message_task", {})
            if isinstance(message_doc.get("message_content"), dict)
            else {}
        )
        tracked_task_id = task_data.get("id") if isinstance(task_data, dict) else None
        root_user_message_id = message_doc.get(
            "turn_id"
        ) or await self._resolve_root_user_message_id(related_message_id)
        return HubPublishLineageSnapshot(
            room_id=room_id,
            room_owner_id=_owner_id_from_doc(room_doc) or "",
            agent_message_id=agent_message_id,
            agent_id=agent_id,
            agent_hub_id=agent.hub_id or "",
            related_message_id=related_message_id,
            turn_id=message_doc.get("turn_id"),
            run_id=message_doc.get("run_id"),
            root_user_message_id=root_user_message_id,
            tracked_task_id=tracked_task_id,
            lifecycle_message_id=root_user_message_id,
            client_request_id=message_doc.get("client_request_id"),
            cancellation_message_ids=[
                item
                for item in [agent_message_id, related_message_id, root_user_message_id]
                if item
            ],
        )

    async def _resolve_root_user_message_id(self, message_id: str | None) -> str | None:
        cursor = message_id
        visited: set[str] = set()
        for _ in range(20):
            if not isinstance(cursor, str) or not cursor or cursor in visited:
                return None
            visited.add(cursor)
            doc = await self._message_repository.get_by_id(cursor)
            if doc is None:
                return cursor
            if doc.get("message_type") == "user":
                return cursor
            turn_id = doc.get("turn_id")
            if isinstance(turn_id, str) and turn_id:
                return turn_id
            cursor = doc.get("related_message_id") or doc.get("parent_message_id")
        return None

    async def authorize_hub_publish(
        self, *, hub_id: str, owner_id: str, room_id: str, agent_message_id: str
    ) -> HubPublishLineageSnapshot | None:
        lineage = await self.get_hub_publish_lineage(
            room_id=room_id, agent_message_id=agent_message_id
        )
        if lineage is None:
            return None
        if lineage.room_owner_id != owner_id:
            return None
        if lineage.agent_hub_id != hub_id:
            return None
        return lineage

    async def is_message_cancelled(self, message_id: str) -> bool:
        repository_checker = getattr(self._message_repository, "is_message_cancelled", None)
        if repository_checker is not None:
            return bool(await repository_checker(message_id))
        doc = await self._message_repository.get_by_id(message_id)
        if doc is None:
            return False
        status = str(doc.get("status") or doc.get("message_status") or "").lower()
        return bool(doc.get("is_cancelled")) or status in {"cancelled", "canceled"}

    async def track_hub_task(self, message_id: str, task_data: dict) -> None:
        task_fields = {
            f"message_content.message_task.{key}": value
            for key, value in task_data.items()
            if key != "status"
        }
        status_data = task_data.get("status")
        if isinstance(status_data, dict):
            task_fields.update(
                {
                    f"message_content.message_task.status.{key}": value
                    for key, value in status_data.items()
                    if key != "state"
                }
            )
        await self._message_repository.update_status(
            message_id, "processing", **task_fields
        )

    def _validate_create_room_request(self, request: CreateRoomRequest) -> None:
        if not request.owner_id:
            raise ValueError("owner_id is required")
        if not request.owner_name:
            raise ValueError("owner_name is required")
        if not request.room_name:
            raise ValueError("room_name is required")

    async def _resolve_agent_ids_for_update(
        self,
        agent_ids: list[str],
        *,
        requesting_user_id: str | None,
    ) -> dict[str, str]:
        if not agent_ids:
            return {}
        agents = await self._agent_registry.get_agents_by_ids(agent_ids)
        agents_by_id = {agent.agent_id: agent for agent in agents}
        missing = [agent_id for agent_id in agent_ids if agent_id not in agents_by_id]
        if missing:
            raise ValueError(f"Unknown or deleted agent IDs: {', '.join(missing)}")

        inaccessible: list[str] = []
        inactive: list[str] = []
        for agent in agents:
            if agent.status != "active":
                inactive.append(agent.agent_id)
            elif not _is_visible(agent, requesting_user_id):
                inaccessible.append(agent.agent_id)

        if inaccessible:
            raise ValueError(f"Access denied to private agents: {', '.join(inaccessible)}")
        if inactive:
            raise ValueError(f"Inactive agent IDs: {', '.join(inactive)}")
        return {agent.agent_id: agent.name or agent.agent_id for agent in agents}

    async def _require_room(self, room_id: str) -> dict:
        doc = await self._repository.get_by_id(room_id)
        if doc is None:
            raise ValueError("Room not found")
        return doc


def _is_visible(agent: AgentInfo, user_id: str | None) -> bool:
    return agent.is_public or (user_id is not None and agent.provider_id == user_id)


def _owner_id_from_doc(doc: dict | None) -> str | None:
    if doc is None or not doc.get("room_owner_id"):
        return None
    return str(doc["room_owner_id"])


def _safe_parse_user_message(doc: dict | None) -> RoomUserMessage | None:
    if doc is None:
        return None
    try:
        return RoomUserMessage.model_validate(doc)
    except Exception:
        logger.warning("Invalid room user message document", exc_info=True)
        return None


def _safe_parse_agent_message(doc: dict | None) -> RoomAgentMessage | None:
    if doc is None:
        return None
    try:
        content = doc.get("message_content")
        if content and isinstance(content, dict):
            task = content.get("message_task")
            if task and isinstance(task, dict):
                sanitize_task_dict(task)
        return RoomAgentMessage.model_validate(doc)
    except Exception:
        logger.warning("Invalid room agent message document", exc_info=True)
        return None


def _strip_file_urls(doc: dict) -> None:
    target = doc.get("$set", doc)
    content = target.get("message_content")
    if not content:
        return
    for attachment in content.get("attachments") or []:
        attachment.pop("file_url", None)


def _strip_unset_task_tracking_fields(update_data: dict[str, Any]) -> dict[str, Any]:
    task_tracking_fields = {
        "webhook_token_hash",
        "pending_continuation",
        "last_notified_state",
        "agent_url",
        "task_created_at",
        "task_updated_at",
        "task_content",
    }
    for field in task_tracking_fields:
        if update_data.get(field) is None:
            update_data.pop(field, None)
    if update_data.get("has_task_tracking") is False:
        update_data.pop("has_task_tracking", None)
    return update_data
