from common.dto import (
    UserMessageInsertResult,
)
from common.protocols.context_memory_protocols import (
    ContextAssemblyPort,
    RoomMemoryCleanupPort,
)
from common.utils.logger import get_logger
from common.utils.time import utcnow
from conversation import (
    SUPPORTED_CANDIDATE_SCOPE_SOURCES,
)
from conversation.attachments import resolve_and_apply_room_attachments
from conversation.idempotency import (
    IdempotencyConflictError,
    UnexpectedUserMessageDuplicateError,
    UserMessagePersistenceError,
    stored_fingerprint_matches,
)
from conversation.preflight import (
    MessagePreflight,
    RoomMessagePreflightContext,
    persisted_execution_mode,
)
from conversation.target_scope import (
    TargetScopeResolver,
    orchestration_request_info,
    selected_agent_ids_from_request,
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
    UserMessageCommitCommand,
    UserMessageCommitService,
)
from models.request import (
    RoomCenterRoomMessageRequest,
    RoomCenterUserMessageRequest,
)
from models.response import (
    RoomCenterRoomMessageResponse,
    RoomCenterUserMessageResponse,
    ScopeResolutionError,
)
from models.room import (
    MAX_MESSAGE_LENGTH,
    Room,
    RoomUserMessage,
)
from models.room_services_models import ResolvedRoutingScope

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

    async def _resolve_and_apply_attachments(
        self,
        request: RoomCenterUserMessageRequest,
        user_message: RoomUserMessage,
    ) -> RoomCenterUserMessageResponse | None:
        return await resolve_and_apply_room_attachments(
            request=request,
            user_message=user_message,
            attachment_reader=getattr(self, "_attachment_metadata_reader", None),
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

        existing = await self._require_facade().get_user_message_by_idempotency_key(
            room_id,
            client_request_id,
        )
        if existing is None:
            return None
        message_id = existing.get("message_id")
        if not isinstance(message_id, str) or not message_id:
            raise UserMessagePersistenceError(
                "Idempotency record is missing a valid message_id"
            )

        stored_fingerprint = existing.get("idempotency_fingerprint")
        if stored_fingerprint is None:
            logger.warning(
                "Legacy idempotency replay without fingerprint "
                "room_id=%s client_request_id=%s message_id=%s",
                room_id,
                client_request_id,
                message_id,
            )
        elif not stored_fingerprint_matches(
            existing,
            fingerprint=idempotency_fingerprint,
            fingerprint_version=idempotency_fingerprint_version,
        ):
            return RoomCenterUserMessageResponse(
                room_id=room_id,
                message_id=None,
                message=None,
                success=False,
                error=(
                    "The client_request_id was already used for a different request"
                ),
                status_code=409,
            )

        return RoomCenterUserMessageResponse(
            room_id=room_id,
            message_id=message_id,
            dispatch_root_message_id=None,
            message=None,
            success=True,
            error=None,
            status_code=200,
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
        """Validate, scope-check, and persist the user message before heavy preflight."""
        client_request_id = (
            request.client_request_id.strip()
            if isinstance(getattr(request, "client_request_id", None), str)
            else None
        )
        request.client_request_id = client_request_id
        if (
            client_request_id
            and idempotency_fingerprint is not None
            and idempotency_fingerprint_version is not None
        ):
            replay = await self.get_idempotent_user_message(
                room_id=request.room_id or "",
                client_request_id=client_request_id,
                idempotency_fingerprint=idempotency_fingerprint,
                idempotency_fingerprint_version=idempotency_fingerprint_version,
            )
            if replay is not None:
                return replay, None

        validation_response = self._validate_send_message_request(request)
        if validation_response:
            return validation_response, None

        user_message = request.message
        if user_message is not None:
            # The authenticated request boundary, not client-supplied message
            # metadata, owns room/sender identity and the canonical turn key.
            user_message.room_id = request.room_id
            user_message.user_id = request.user_id
            user_message.client_request_id = client_request_id
            if idempotency_fingerprint is not None:
                # Canonical sendMessage accepts only user-authored content and
                # relationship fields. Everything else below is server-owned and
                # deliberately excluded from the semantic fingerprint.
                user_message.message_id = ""
                user_message.message_created_at = utcnow()
                user_message.message_type = "user"
                user_message.agent_id = None
                user_message.run_id = None
                user_message.step_number = None
                user_message.total_steps = None
                user_message.task_updated_at = None
                user_message.task_content = None
                user_message.processing_claimed_at = None
                user_message.quote_id = None
                user_message.message_content.message_task = None
                extend_info = (
                    user_message.extend_info
                    if isinstance(user_message.extend_info, dict)
                    else {}
                )
                legacy_quote_keys = (
                    ()
                    if user_message.quote is not None
                    else ("quoted_text", "quoted_sender_name")
                )
                user_message.extend_info = {
                    key: value
                    for key in legacy_quote_keys
                    if isinstance((value := extend_info.get(key)), str)
                } or None

        # Resolve attachments from both sources before persistence
        att_err = await self._resolve_and_apply_attachments(request, user_message)
        if att_err is not None:
            return att_err, None

        # ── Pre-persist scope validation ──────────────────────────────────
        # Fetch room early: needed for scope resolution and downstream flags.
        room = await self._store.get_room_by_room_id(request.room_id)
        if not room:
            return (
                RoomCenterUserMessageResponse(
                    message_id=None,
                    message=None,
                    success=False,
                    error="Room not found",
                    status_code=404,
                ),
                None,
            )

        orchestration_info = orchestration_request_info(request)
        execution_mode = persisted_execution_mode(orchestration_info)
        scope = orchestration_info.get("agent_scope")
        scope = scope if isinstance(scope, dict) else {"source": "room_default"}
        user_extend_info = (
            user_message.extend_info
            if isinstance(user_message.extend_info, dict)
            else {}
        )
        # Persist the canonical scope alongside the mode: the orchestrator
        # ingress reconstructs its envelope from the persisted user message
        # (never from in-flight requests).
        user_message.extend_info = {
            **user_extend_info,
            "execution_mode": execution_mode,
            "agent_scope": scope,
        }
        message_text = user_message.message_content.message_text

        # Validate deterministic scope BEFORE persistence so rejected messages
        # never get a real message_id in the database.
        # - canonical mentioned_agent_ids: validated via shared helper
        # - room_team / saved_group: validated via shared helper
        # - all_agents: LLM-driven, cannot pre-validate (persists first)
        # - inline text mentions: best-effort, not covered (persists first)
        pre_resolved_mentions: list[dict] | None = None
        pre_resolved_scope: ResolvedRoutingScope | None = None
        pre_resolved_selected_scope: ResolvedRoutingScope | None = None
        selected_agent_ids = selected_agent_ids_from_request(request)
        candidate_scope_mode = str(scope.get("source") or "room_default")

        if candidate_scope_mode not in SUPPORTED_CANDIDATE_SCOPE_SOURCES:
            supported_modes = ", ".join(sorted(SUPPORTED_CANDIDATE_SCOPE_SOURCES))
            error_msg = (
                f"Unsupported candidate_scope_mode {candidate_scope_mode!r}; "
                f"expected one of: {supported_modes}"
            )
            return (
                RoomCenterUserMessageResponse(
                    message_id=None,
                    message=None,
                    success=False,
                    error=error_msg,
                    scope_resolution_error=ScopeResolutionError(
                        code="invalid_target",
                        message=error_msg,
                    ),
                    status_code=400,
                ),
                None,
            )
        logger.info(
            "room_send_message_persist_started room_id=%s user_id=%s "
            "client_request_id=%s target_group=%s "
            "candidate_scope_mode=%s "
            "selected_count=%d mentioned_count=%d message_len=%d",
            request.room_id,
            request.user_id,
            client_request_id,
            target_group,
            candidate_scope_mode,
            len(selected_agent_ids or []),
            len(mentioned_agent_ids or []),
            len(message_text or ""),
        )

        if selected_agent_ids is not None:
            metadata_error = await self._target_scope.validate_candidate_scope_metadata(
                request,
                selected_agent_ids,
                sender_user_id=request.user_id,
            )
            if metadata_error is not None:
                return metadata_error, None
            scope_result = await self._target_scope.resolve_selected_candidate_scope(
                selected_agent_ids,
                sender_user_id=request.user_id,
                required_input_modes=None,
            )
            if isinstance(scope_result, RoomCenterUserMessageResponse):
                return scope_result, None
            pre_resolved_selected_scope = scope_result
            if mentioned_agent_ids:
                mention_result = await self._target_scope.validate_canonical_mentions(
                    mentioned_agent_ids,
                    sender_user_id=request.user_id,
                    required_input_modes=None,
                )
                if isinstance(mention_result, RoomCenterUserMessageResponse):
                    return mention_result, None
                pre_resolved_mentions = mention_result
                candidate_ids = set(pre_resolved_selected_scope.selected_agent_set)
                out_of_scope_mentions = [
                    mention["agent_id"]
                    for mention in pre_resolved_mentions
                    if mention["agent_id"] not in candidate_ids
                ]
                if out_of_scope_mentions:
                    error_msg = (
                        "Mentioned agents are outside the selected candidate scope: "
                        f"{', '.join(out_of_scope_mentions)}"
                    )
                    return (
                        RoomCenterUserMessageResponse(
                            message_id=None,
                            message=None,
                            success=False,
                            error=error_msg,
                            scope_resolution_error=ScopeResolutionError(
                                code="mention_outside_candidate_scope",
                                message=error_msg,
                            ),
                            status_code=400,
                        ),
                        None,
                    )
        elif mentioned_agent_ids:
            mention_result = await self._target_scope.validate_canonical_mentions(
                mentioned_agent_ids,
                sender_user_id=request.user_id,
                required_input_modes=None,
            )
            if isinstance(mention_result, RoomCenterUserMessageResponse):
                return mention_result, None
            pre_resolved_mentions = mention_result
        elif target_group != "all_agents":
            scope_result = await self._target_scope.resolve_explicit_target_scope(
                room,
                message_text,
                target_group,
                sender_user_id=request.user_id,
                required_input_modes=None,
            )
            if isinstance(scope_result, RoomCenterUserMessageResponse):
                return scope_result, None
            pre_resolved_scope = scope_result

        # ── Persist ───────────────────────────────────────────────────────
        qerr = await self._materialize_room_quote(room, request, user_message)
        if isinstance(qerr, RoomCenterUserMessageResponse):
            return qerr, None

        try:
            persistence = await self._persist_user_message(
                user_message,
                room_agent_set=room.room_agent_set if room else {},
                idempotency_fingerprint=idempotency_fingerprint,
                idempotency_fingerprint_version=idempotency_fingerprint_version,
            )
        except IdempotencyConflictError:
            await self._delete_uncommitted_quote(user_message)
            return (
                RoomCenterUserMessageResponse(
                    room_id=request.room_id,
                    message_id=None,
                    message=None,
                    success=False,
                    error=(
                        "The client_request_id was already used for a different request"
                    ),
                    status_code=409,
                ),
                None,
            )
        except UnexpectedUserMessageDuplicateError:
            await self._delete_uncommitted_quote(user_message)
            return (
                RoomCenterUserMessageResponse(
                    message_id=None,
                    message=None,
                    success=False,
                    error="User message uniqueness conflict",
                    status_code=500,
                ),
                None,
            )
        except UserMessagePersistenceError:
            await self._delete_uncommitted_quote(user_message)
            return (
                RoomCenterUserMessageResponse(
                    message_id=None,
                    message=None,
                    success=False,
                    error="Failed to add message",
                    status_code=500,
                ),
                None,
            )

        if not persistence.created:
            # This request lost the unique-index race. Only its own random quote
            # and pending attachment claims are compensated; winner state remains.
            await self._delete_uncommitted_quote(user_message)
            return (
                RoomCenterUserMessageResponse(
                    room_id=request.room_id,
                    message_id=persistence.message_id,
                    dispatch_root_message_id=None,
                    message=None,
                    success=True,
                    error=None,
                    status_code=200,
                ),
                None,
            )

        logger.info(
            "room_send_message_persisted room_id=%s message_id=%s "
            "client_request_id=%s target_group=%s",
            request.room_id,
            user_message.message_id,
            client_request_id,
            target_group,
        )

        return (
            RoomCenterUserMessageResponse(
                room_id=request.room_id,
                message_id=user_message.message_id,
                dispatch_root_message_id=user_message.message_id,
                message=None,
                success=True,
                error=None,
                status_code=200,
            ),
            RoomMessagePreflightContext(
                request=request,
                target_group=target_group,
                mentioned_agent_ids=mentioned_agent_ids,
                user_message=user_message,
                client_request_id=client_request_id,
                room=room,
                message_text=message_text,
                pre_resolved_mentions=pre_resolved_mentions,
                pre_resolved_scope=pre_resolved_scope,
                pre_resolved_selected_scope=pre_resolved_selected_scope,
            ),
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

    def _check_message_text_length(
        self, message: RoomUserMessage | None
    ) -> RoomCenterUserMessageResponse | None:
        """Reject messages exceeding MAX_MESSAGE_LENGTH (SDR 2.10)."""
        if (
            message
            and message.message_content
            and message.message_content.message_text
            and len(message.message_content.message_text) > MAX_MESSAGE_LENGTH
        ):
            return RoomCenterUserMessageResponse(
                message_id=None,
                message=None,
                success=False,
                error=f"Message text exceeds maximum length of {MAX_MESSAGE_LENGTH} characters",
                status_code=400,
            )
        return None

    def _validate_send_message_request(
        self, request: RoomCenterUserMessageRequest
    ) -> RoomCenterUserMessageResponse | None:
        """Validate required fields for a user message send."""
        if request.room_id is None:
            return RoomCenterUserMessageResponse(
                message_id=None,
                message=None,
                success=False,
                error="Room id is required",
                status_code=400,
            )

        if request.message is None:
            return RoomCenterUserMessageResponse(
                message_id=None,
                message=None,
                success=False,
                error="Message is required",
                status_code=400,
            )

        size_err = self._check_message_text_length(request.message)
        if size_err:
            return size_err

        return None

    async def _materialize_room_quote(
        self,
        room: Room,
        request: RoomCenterUserMessageRequest,
        user_message: RoomUserMessage,
    ) -> RoomCenterUserMessageResponse | None:
        facade = self._require_facade()
        return await facade.materialize_quote(
            room=room,
            request=request,
            user_message=user_message,
        )

    async def _delete_uncommitted_quote(
        self,
        user_message: RoomUserMessage,
    ) -> None:
        quote_id = getattr(user_message, "quote_id", None)
        if not quote_id:
            return
        try:
            await self._require_facade().delete_room_quote(quote_id)
        except Exception:
            logger.warning(
                "Failed to remove uncommitted quoted snippet %s for room %s",
                quote_id,
                user_message.room_id,
                exc_info=True,
            )

    async def _persist_user_message(
        self,
        user_message: RoomUserMessage,
        *,
        room_agent_set: dict[str, str] | None = None,
        idempotency_fingerprint: str | None = None,
        idempotency_fingerprint_version: int | None = None,
    ) -> UserMessageInsertResult:
        return await self._require_user_message_commit().commit(
            UserMessageCommitCommand(
                message=user_message,
                room_agent_set=room_agent_set or {},
                idempotency_fingerprint=idempotency_fingerprint,
                idempotency_fingerprint_version=idempotency_fingerprint_version,
            )
        )

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
