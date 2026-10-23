from common.protocols.context_memory_protocols import (
    ContextAssemblyPort,
    RoomMemoryCleanupPort,
)
from common.utils.logger import get_logger
from conversation.message_sending import RoomMessageSender
from conversation.preflight import (
    MessagePreflight,
    RoomMessagePreflightContext,
)
from conversation.target_scope import (
    TargetScopeResolver,
)
from conversation.timeline import (
    TimelineCursorError,
    decode_timeline_cursor,
    encode_timeline_cursor,
)
from conversation.timeline_projection import RoomTimelineProjector
from conversation.unbound import (
    UNBOUND_RUNTIME_STORE,
)
from conversation.user_message_persistence import (
    UserMessageCommitService,
)
from models.request import (
    RoomCenterRoomMessageRequest,
    RoomCenterUserMessageRequest,
)
from models.response import (
    RoomCenterRoomMessageResponse,
    RoomCenterUserMessageResponse,
)

logger = get_logger(__name__)


class ConversationRuntime:
    def __init__(self, *, room_store=None):
        if room_store is None:
            room_store = UNBOUND_RUNTIME_STORE
        self._store = room_store
        self._room_files = None
        self._facade = None
        self._bound = False
        self._context_assembly: ContextAssemblyPort | None = None
        self._room_memory_cleanup: RoomMemoryCleanupPort | None = None
        self._attachment_metadata_reader = None
        self._capability_issue_reader = None
        self._timeline_projector: RoomTimelineProjector | None = None
        self._user_message_commit: UserMessageCommitService | None = None

    def bind_room_files(self, room_files) -> None:
        self._room_files = room_files

    def bind_store(self, store) -> None:
        """Inject the room runtime persistence store explicitly at startup."""
        self._store = store

    @property
    def room_files(self):
        files = getattr(self, "_room_files", None)
        if files is None:
            raise RuntimeError(
                "ConversationRuntime.bind_room_files() not called - startup incomplete"
            )
        return files

    def bind_facade(self, facade) -> None:
        self._facade = facade
        self._bound = True

    def bind_context_memory(
        self,
        *,
        context_assembly: ContextAssemblyPort | None = None,
        room_memory_cleanup: RoomMemoryCleanupPort | None = None,
    ) -> None:
        self._context_assembly = context_assembly
        self._room_memory_cleanup = room_memory_cleanup

    def bind_attachment_metadata_reader(self, reader) -> None:
        self._attachment_metadata_reader = reader

    def bind_capability_issue_reader(self, reader) -> None:
        self._capability_issue_reader = reader

    def bind_timeline_projector(self, projector: RoomTimelineProjector) -> None:
        self._timeline_projector = projector

    def missing_required_bindings(self) -> list[str]:
        checks = (
            (
                "runtime_store",
                getattr(self, "_store", None) is not None
                and getattr(self, "_store", None) is not UNBOUND_RUNTIME_STORE,
            ),
            ("facade", getattr(self, "_facade", None) is not None),
            (
                "user_message_commit",
                getattr(self, "_user_message_commit", None) is not None,
            ),
            (
                "timeline_projector",
                getattr(self, "_timeline_projector", None) is not None,
            ),
        )
        return [name for name, is_bound in checks if not is_bound]

    def bind_user_message_commit(self, service: UserMessageCommitService) -> None:
        self._user_message_commit = service

    def _require_user_message_commit(self) -> UserMessageCommitService:
        service = getattr(self, "_user_message_commit", None)
        if service is None:
            raise RuntimeError(
                "ConversationRuntime user-message commit service has not been bound"
            )
        return service

    def _require_timeline_projector(self) -> RoomTimelineProjector:
        projector = getattr(self, "_timeline_projector", None)
        if projector is None:
            raise RuntimeError(
                "ConversationRuntime timeline projector has not been bound"
            )
        return projector

    @property
    def _target_scope(self) -> TargetScopeResolver:
        return TargetScopeResolver(
            store=self._store,
            capability_issue_reader=getattr(self, "_capability_issue_reader", None),
        )

    @property
    def _preflight(self) -> MessagePreflight:
        return MessagePreflight(store=self._store, target_scope=self._target_scope)

    def _require_facade(self):
        if not getattr(self, "_bound", False) or getattr(self, "_facade", None) is None:
            raise RuntimeError(
                "ConversationRuntime.bind_facade() not called - startup incomplete"
            )
        return self._facade

    # --- Attachment resolution helpers ---

    @property
    def _message_sender(self) -> RoomMessageSender:
        return RoomMessageSender(
            store=self._store,
            target_scope=self._target_scope,
            facade=self._facade if getattr(self, "_bound", False) else None,
            user_message_commit=getattr(self, "_user_message_commit", None),
            attachment_metadata_reader=getattr(
                self, "_attachment_metadata_reader", None
            ),
        )

    async def get_idempotent_user_message(
        self,
        *,
        room_id: str,
        client_request_id: str,
        idempotency_fingerprint: str,
        idempotency_fingerprint_version: int,
    ) -> RoomCenterUserMessageResponse | None:
        """Return a stable replay/conflict response without running side effects."""
        return await self._message_sender.get_idempotent_user_message(
            room_id=room_id,
            client_request_id=client_request_id,
            idempotency_fingerprint=idempotency_fingerprint,
            idempotency_fingerprint_version=idempotency_fingerprint_version,
        )

    async def persist_message_to_room(
        self,
        request: RoomCenterUserMessageRequest,
        target_group: str = "room_team",
        mentioned_agent_ids: list[str] | None = None,
        *,
        idempotency_fingerprint: str | None = None,
        idempotency_fingerprint_version: int | None = None,
    ) -> tuple[RoomCenterUserMessageResponse, RoomMessagePreflightContext | None]:
        return await self._message_sender.persist_message_to_room(
            request,
            target_group,
            mentioned_agent_ids,
            idempotency_fingerprint=idempotency_fingerprint,
            idempotency_fingerprint_version=idempotency_fingerprint_version,
        )

    async def run_message_preflight_to_room(
        self,
        context: RoomMessagePreflightContext,
    ) -> RoomCenterUserMessageResponse:
        return await self._preflight.run(context)

    def discard_message_preflight(
        self,
        context: RoomMessagePreflightContext,
    ) -> None:
        """Abort hook Execution calls when it drops a persisted preflight.

        Preflight holds no per-message resources today, so there is nothing to
        release; the hook stays so Execution's abort path has one owner.
        """
        del context

    async def inquiry_room_messages_by_room_id(
        self, request: RoomCenterRoomMessageRequest
    ) -> RoomCenterRoomMessageResponse:
        """
        Retrieve all messages in a room, including user messages and agent messages.
        For user messages: return message_text from message_content
        For agent messages: return text from sanitized completed artifacts.
        Sort by creation time and return
        """
        if request.room_id is None:
            return RoomCenterRoomMessageResponse(
                message_list=None,
                success=False,
                error="Room id is required",
                status_code=400,
            )

        try:
            room_id = request.room_id

            limit = request.limit if request.limit is not None else 200
            if (
                isinstance(limit, bool)
                or not isinstance(limit, int)
                or not 1 <= limit <= 200
            ):
                return RoomCenterRoomMessageResponse(
                    room_id=room_id,
                    message_list=None,
                    success=False,
                    error="limit must be an integer between 1 and 200",
                    status_code=400,
                )
            try:
                before = (
                    decode_timeline_cursor(request.cursor, room_id=room_id)
                    if request.cursor is not None
                    else None
                )
            except TimelineCursorError:
                return RoomCenterRoomMessageResponse(
                    room_id=room_id,
                    message_list=None,
                    success=False,
                    error="Invalid timeline cursor",
                    status_code=400,
                )

            page = await self._require_facade().get_timeline_page(
                room_id,
                limit=limit,
                before=before,
            )
            combined_messages = await self._require_timeline_projector().project(page)
            next_cursor = (
                encode_timeline_cursor(room_id, page.next_position)
                if page.has_more and page.next_position is not None
                else None
            )
            return RoomCenterRoomMessageResponse(
                room_id=room_id,
                message_list=combined_messages,
                has_more=page.has_more,
                next_cursor=next_cursor,
                success=True,
                error=None,
                status_code=200,
            )

        except Exception:
            logger.error(
                "Failed to retrieve room timeline room_id=%s",
                request.room_id,
                exc_info=True,
            )
            return RoomCenterRoomMessageResponse(
                room_id=request.room_id,
                message_list=None,
                success=False,
                error="Failed to retrieve room timeline",
                status_code=500,
            )


__all__ = [
    "ConversationRuntime",
]
