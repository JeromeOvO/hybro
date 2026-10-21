import re
from dataclasses import dataclass

from agent import sanitize_routing_agent_ids
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
    normalize_candidate_scope,
)
from conversation.attachments import resolve_and_apply_room_attachments
from conversation.idempotency import (
    IdempotencyConflictError,
    UnexpectedUserMessageDuplicateError,
    UserMessagePersistenceError,
    stored_fingerprint_matches,
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
from models.room_services_models import ParseResult, ResolvedRoutingScope

logger = get_logger(__name__)

_PERSISTED_EXECUTION_MODES = frozenset({"direct", "supervisor", "passthrough"})


def _persisted_execution_mode(info: dict) -> str:
    """The request's execution mode as stored on the user message."""
    mode = info.get("execution_mode")
    return mode if mode in _PERSISTED_EXECUTION_MODES else "direct"


_PUBLIC_USER_MESSAGE_EXTEND_INFO_STRING_KEYS = (
    "quoted_text",
    "quoted_sender_name",
    "quote_id",
)


@dataclass(slots=True)
class RoomMessagePreflightContext:
    request: RoomCenterUserMessageRequest
    target_group: str
    mentioned_agent_ids: list[str] | None
    user_message: RoomUserMessage
    client_request_id: str | None
    room: Room
    message_text: str
    pre_resolved_mentions: list[dict] | None
    pre_resolved_scope: ResolvedRoutingScope | None
    pre_resolved_selected_scope: ResolvedRoutingScope | None


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

    def reset_bindings(self) -> None:
        """Clear process-global composition state before a new lifespan starts."""
        self.__init__()

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

    async def _routing_excluded_agent_ids(self) -> frozenset[str]:
        reader = getattr(self, "_capability_issue_reader", None)
        if reader is None:
            return frozenset()
        return frozenset(await reader.get_excluded_agent_ids())

    async def _sanitize_routing_scope(
        self,
        agent_ids,
        *,
        sender_user_id: str | None,
        required_input_modes: list[str] | None = None,
    ):
        return await sanitize_routing_agent_ids(
            agent_ids,
            lookup=self._store.get_agent_by_agent_id,
            user_id=sender_user_id,
            excluded_agent_ids=await self._routing_excluded_agent_ids(),
            required_input_modes=required_input_modes,
        )

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

    # room user message management
    def parse_agent_mentions(
        self, message_text: str, room_agent_set: dict
    ) -> list[dict]:
        """
        Parse @agent mentions in format "<@agent-id|agentname>"

        Args:
            message_text: User input text with format "<@agent-id|agentname>"
            room_agent_set: Agent set in the room {agent_id: agent_name}

        Returns:
            list[dict]: Parsed mentions [{"agent_id": "xxx", "agent_name": "yyy", "mention_text": "<@xxx|yyy>"}]
        """
        mentions = []

        # pattern: <@agent_id|agent_name>
        slack_pattern = r"<@([^|]+)\|([^>]+)>"

        for match in re.finditer(slack_pattern, message_text):
            agent_id = match.group(1).strip()
            position = match.start()

            # Check if agent exists in room by agent_id
            if agent_id in room_agent_set:
                # Agent found in room
                room_agent_name = room_agent_set[agent_id]
                mentions.append(
                    {
                        "agent_id": agent_id,
                        "agent_name": room_agent_name,  # Use the name from room_agent_set
                        "mention_text": match.group(0),
                        "position": position,
                    }
                )
            else:
                logger.warning(
                    "Inline mention %s not in room agent set — ignored", agent_id
                )

        # Sort by position to maintain order
        mentions.sort(key=lambda x: x["position"])
        return mentions

    @staticmethod
    def _orchestration_request_info(
        request: RoomCenterUserMessageRequest,
    ) -> dict:
        info = request.extend_info if isinstance(request.extend_info, dict) else {}
        return dict(info)

    @classmethod
    def _selected_agent_ids_from_request(
        cls,
        request: RoomCenterUserMessageRequest,
    ) -> list[str] | None:
        scope = cls._orchestration_request_info(request).get("agent_scope")
        if not isinstance(scope, dict) or scope.get("source") != "mention":
            return None
        value = scope.get("agent_ids")
        if not isinstance(value, list):
            return None
        selected_agent_ids: list[str] = []
        for agent_id in value:
            if isinstance(agent_id, str) and agent_id.strip():
                selected_agent_ids.append(agent_id.strip())
        return selected_agent_ids

    async def _resolve_selected_candidate_scope(
        self,
        selected_agent_ids: list[str],
        sender_user_id: str | None,
        required_input_modes: list[str] | None = None,
    ) -> ResolvedRoutingScope | RoomCenterUserMessageResponse:
        agents, invalid_ids = await self._sanitize_routing_scope(
            selected_agent_ids,
            sender_user_id=sender_user_id,
            required_input_modes=required_input_modes,
        )
        selected_agent_set = {agent.agent_id: agent.agent_card.name for agent in agents}

        if invalid_ids:
            error_msg = (
                "Invalid or unauthorized selected candidate targets: "
                f"{', '.join(invalid_ids)}"
            )
            logger.warning(
                "Selected candidate targets rejected (invalid/unauthorized): %s",
                invalid_ids,
            )
            return RoomCenterUserMessageResponse(
                message_id=None,
                message=None,
                success=False,
                error=error_msg,
                scope_resolution_error=ScopeResolutionError(
                    code="unauthorized_candidate_scope",
                    message=error_msg,
                ),
                status_code=400,
            )

        if not selected_agent_set:
            error_msg = "Selected candidate scope is empty."
            return RoomCenterUserMessageResponse(
                message_id=None,
                message=None,
                success=False,
                error=error_msg,
                scope_resolution_error=ScopeResolutionError(
                    code="empty_scope",
                    message=error_msg,
                ),
                status_code=400,
            )

        return ResolvedRoutingScope(
            selected_agent_set=selected_agent_set,
            auto_assign_agents=False,
            agents=agents,
        )

    async def _validate_candidate_scope_metadata(
        self,
        request: RoomCenterUserMessageRequest,
        selected_agent_ids: list[str],
        sender_user_id: str | None,
    ) -> RoomCenterUserMessageResponse | None:
        info = self._orchestration_request_info(request)
        scope = info.get("agent_scope")
        if not isinstance(scope, dict) or scope.get("source") != "saved_group":
            return None

        candidate_scope_group_id = scope.get("group_id")
        if (
            not isinstance(candidate_scope_group_id, str)
            or not candidate_scope_group_id.strip()
        ):
            error_msg = (
                "candidate_scope_group_id is required for saved_group candidate scope"
            )
            return RoomCenterUserMessageResponse(
                message_id=None,
                message=None,
                success=False,
                error=error_msg,
                scope_resolution_error=ScopeResolutionError(
                    code="group_not_usable",
                    message=error_msg,
                ),
                status_code=400,
            )

        group_id = candidate_scope_group_id.strip()
        group = await self._store.get_agent_group_by_id(group_id)
        if not group:
            error_msg = (
                "The selected agent group no longer exists. "
                "Please choose a different group."
            )
            return RoomCenterUserMessageResponse(
                message_id=None,
                message=None,
                success=False,
                error=error_msg,
                scope_resolution_error=ScopeResolutionError(
                    code="group_not_usable",
                    message=error_msg,
                ),
                status_code=404,
            )

        if group.type != "builtin" and group.owner_id != sender_user_id:
            return RoomCenterUserMessageResponse(
                message_id=None,
                message=None,
                success=False,
                error="You do not have permission to use this saved group",
                status_code=403,
            )

        group_agent_ids = {str(agent_id) for agent_id in (group.agents or [])}
        if not group_agent_ids:
            error_msg = f"The selected agent group '{group.name}' has no members."
            return RoomCenterUserMessageResponse(
                message_id=None,
                message=None,
                success=False,
                error=error_msg,
                scope_resolution_error=ScopeResolutionError(
                    code="empty_scope",
                    message=error_msg,
                ),
                status_code=400,
            )

        out_of_group_ids = [
            agent_id
            for agent_id in selected_agent_ids
            if agent_id not in group_agent_ids
        ]
        if out_of_group_ids:
            error_msg = (
                "Selected candidate agents are not members of the selected saved group: "
                f"{', '.join(out_of_group_ids)}"
            )
            return RoomCenterUserMessageResponse(
                message_id=None,
                message=None,
                success=False,
                error=error_msg,
                scope_resolution_error=ScopeResolutionError(
                    code="group_not_usable",
                    message=error_msg,
                ),
                status_code=400,
            )

        return None

    async def _prepare_orchestration_envelope(
        self,
        request: RoomCenterUserMessageRequest,
        user_message: RoomUserMessage,
        selected_agent_set: dict,
        explicit_mentions: list[dict] | None,
        client_request_id: str | None,
    ) -> ParseResult:
        info = self._orchestration_request_info(request)
        scope = info.get("agent_scope")
        scope = scope if isinstance(scope, dict) else {"source": "room_default"}
        candidate_scope_mode = str(scope.get("source") or "room_default")
        candidate_scope_group_id = scope.get("group_id")
        sanitized_group_id = (
            candidate_scope_group_id.strip()
            if (
                candidate_scope_mode == "saved_group"
                and isinstance(candidate_scope_group_id, str)
                and candidate_scope_group_id.strip()
            )
            else None
        )
        candidate_scope = normalize_candidate_scope(
            room_id=request.room_id,
            source=candidate_scope_mode,
            group_id=sanitized_group_id,
            selected_agent_set=selected_agent_set,
            selected_by_user_id=request.user_id,
        )

        existing_extend_info = (
            user_message.extend_info
            if isinstance(user_message.extend_info, dict)
            else {}
        )
        envelope = {
            key: value
            for key in _PUBLIC_USER_MESSAGE_EXTEND_INFO_STRING_KEYS
            if isinstance((value := existing_extend_info.get(key)), str)
        }
        envelope.update(
            {
                "orchestration": True,
                "execution_mode": _persisted_execution_mode(info),
                "orchestration_run_id": user_message.message_id,
                "orchestration_status": "created",
                "candidate_scope_snapshot_id": candidate_scope.snapshot_id,
                "candidate_scope_source": candidate_scope.source,
                "candidate_scope_mode": candidate_scope.source,
                "candidate_agent_ids": list(candidate_scope.agent_ids),
                "candidate_scope_snapshot_version": candidate_scope.revision,
                "mentioned_agent_ids": [
                    mention["agent_id"] for mention in (explicit_mentions or [])
                ],
                "client_request_id": client_request_id,
            }
        )
        if candidate_scope.group_id:
            envelope["candidate_scope_group_id"] = candidate_scope.group_id

        user_message.extend_info = envelope
        persisted = await self._store.update_room_user_message_by_message_id(
            user_message.message_id,
            user_message,
        )
        if not persisted:
            logger.warning(
                "ConversationRuntime: failed to persist orchestration envelope for message %s",
                user_message.message_id,
            )
            return ParseResult(success=False)
        logger.info(
            "ConversationRuntime: orchestration envelope prepared for message %s (%d candidates)",
            user_message.message_id,
            len(selected_agent_set),
        )
        return ParseResult(success=True)

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

        orchestration_info = self._orchestration_request_info(request)
        execution_mode = _persisted_execution_mode(orchestration_info)
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
        selected_agent_ids = self._selected_agent_ids_from_request(request)
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
            metadata_error = await self._validate_candidate_scope_metadata(
                request,
                selected_agent_ids,
                sender_user_id=request.user_id,
            )
            if metadata_error is not None:
                return metadata_error, None
            scope_result = await self._resolve_selected_candidate_scope(
                selected_agent_ids,
                sender_user_id=request.user_id,
                required_input_modes=None,
            )
            if isinstance(scope_result, RoomCenterUserMessageResponse):
                return scope_result, None
            pre_resolved_selected_scope = scope_result
            if mentioned_agent_ids:
                mention_result = await self._validate_canonical_mentions(
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
            mention_result = await self._validate_canonical_mentions(
                mentioned_agent_ids,
                sender_user_id=request.user_id,
                required_input_modes=None,
            )
            if isinstance(mention_result, RoomCenterUserMessageResponse):
                return mention_result, None
            pre_resolved_mentions = mention_result
        elif target_group != "all_agents":
            scope_result = await self._resolve_explicit_target_scope(
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
        return await self._run_message_preflight_to_room(context)

    def discard_message_preflight(
        self,
        context: RoomMessagePreflightContext,
    ) -> None:
        """Abort hook Execution calls when it drops a persisted preflight.

        Preflight holds no per-message resources today, so there is nothing to
        release; the hook stays so Execution's abort path has one owner.
        """
        del context

    async def _run_message_preflight_to_room(
        self,
        context: RoomMessagePreflightContext,
    ) -> RoomCenterUserMessageResponse:
        request = context.request
        target_group = context.target_group
        user_message = context.user_message
        client_request_id = context.client_request_id
        room = context.room
        message_text = context.message_text
        pre_resolved_mentions = context.pre_resolved_mentions
        pre_resolved_scope = context.pre_resolved_scope
        pre_resolved_selected_scope = context.pre_resolved_selected_scope
        selected_scope_locked = pre_resolved_selected_scope is not None

        # ── Dispatch using pre-resolved scope ─────────────────────────────
        if selected_scope_locked:
            selected_agent_set = pre_resolved_selected_scope.selected_agent_set
            auto_assign = pre_resolved_selected_scope.auto_assign_agents
            agents = pre_resolved_selected_scope.agents
        elif pre_resolved_mentions:
            selected_agent_set = {
                mention["agent_id"]: mention["agent_name"]
                for mention in pre_resolved_mentions
            }
            agents = []
            for mention in pre_resolved_mentions:
                agent = await self._store.get_agent_by_agent_id(mention["agent_id"])
                if agent is not None:
                    agents.append(agent)
            auto_assign = False

        # Parse inline <@id|name> mentions from message text when the caller did
        # not provide canonical mentioned_agent_ids. This runs AFTER persistence,
        # so inline mentions are best-effort and do not get reject-before-persist
        # protection.
        #
        if pre_resolved_mentions is None:
            # When target_group is "all_agents", the room_agent_set may be empty
            # (e.g. newly created rooms from the homepage).  In that case, resolve
            # mentions against all active agents so inline @-mentions are honoured
            # regardless of room membership.
            if target_group == "all_agents":
                all_agents = await self._store.get_all_active_agents(
                    user_id=request.user_id,
                )
                effective_agent_set = {
                    a.agent_id: a.agent_card.name for a in (all_agents or [])
                }
            else:
                effective_agent_set = room.room_agent_set

            mentions = self.parse_agent_mentions(message_text, effective_agent_set)

            if mentions:
                mention_agents, _rejected = await self._sanitize_routing_scope(
                    [mention["agent_id"] for mention in mentions],
                    sender_user_id=request.user_id,
                    required_input_modes=None,
                )
                eligible_mention_ids = {agent.agent_id for agent in mention_agents}
                mentions = [
                    mention
                    for mention in mentions
                    if mention["agent_id"] in eligible_mention_ids
                ]
            if mentions:
                pre_resolved_mentions = mentions
                if not selected_scope_locked:
                    selected_agent_set = {
                        mention["agent_id"]: mention["agent_name"]
                        for mention in mentions
                    }
                    agents = []
                    for mention in mentions:
                        agent = await self._store.get_agent_by_agent_id(
                            mention["agent_id"]
                        )
                        if agent is not None:
                            agents.append(agent)
                    auto_assign = False

        # Target scope dispatch: reuse pre-resolved scope or run all_agents LLM.
        if selected_scope_locked:
            pass
        elif pre_resolved_mentions:
            pass
        elif target_group == "all_agents":
            selection_result = await self._resolve_explicit_target_scope(
                room,
                message_text,
                target_group,
                sender_user_id=request.user_id,
                required_input_modes=None,
            )
            if isinstance(selection_result, RoomCenterUserMessageResponse):
                # all_agents runs after persist — attach the real message_id
                # so the frontend knows the user message exists in the DB
                # and doesn't rollback optimistic state.
                selection_result.message_id = user_message.message_id
                selection_result.preflight_outcome = "failed"
                selection_result.preflight_details = (
                    selection_result.error or "Agent selection failed"
                )
                return selection_result
            selected_agent_set = selection_result.selected_agent_set
            auto_assign = selection_result.auto_assign_agents
            agents = selection_result.agents
        elif pre_resolved_scope is not None:
            selected_agent_set = pre_resolved_scope.selected_agent_set
            auto_assign = pre_resolved_scope.auto_assign_agents
            agents = pre_resolved_scope.agents
        else:
            selected_agent_set = {}
            auto_assign = True
            agents = []

        logger.info(
            "room_send_message_preflight_ready room_id=%s message_id=%s "
            "client_request_id=%s target_group=%s candidate_count=%d auto_assign=%s",
            request.room_id,
            user_message.message_id,
            client_request_id,
            target_group,
            len(selected_agent_set),
            auto_assign,
        )

        # Orchestration assembles its own context from the durable run state.
        parse_result = await self._prepare_orchestration_envelope(
            request=request,
            user_message=user_message,
            selected_agent_set=selected_agent_set,
            explicit_mentions=pre_resolved_mentions,
            client_request_id=client_request_id,
        )

        if not parse_result.success:
            logger.warning(
                "room_send_message_preflight_failed room_id=%s message_id=%s "
                "client_request_id=%s canceled=%s",
                request.room_id,
                user_message.message_id,
                client_request_id,
                parse_result.canceled,
            )
            return RoomCenterUserMessageResponse(
                message_id=user_message.message_id,
                message=user_message,
                success=parse_result.canceled,
                error="Failed to parse user message"
                if not parse_result.canceled
                else None,
                status_code=200 if parse_result.canceled else 500,
                preflight_outcome="canceled" if parse_result.canceled else "failed",
                preflight_details=None
                if parse_result.canceled
                else "Failed to parse user message",
            )

        logger.info(
            "room_send_message_preflight_completed room_id=%s message_id=%s "
            "client_request_id=%s preflight_outcome=ready",
            request.room_id,
            user_message.message_id,
            client_request_id,
        )
        return RoomCenterUserMessageResponse(
            message_id=user_message.message_id,
            dispatch_root_message_id=user_message.message_id,
            message=user_message,
            success=True,
            error=None,
            status_code=200,
            preflight_outcome="ready",
        )

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

    async def _validate_canonical_mentions(
        self,
        mentioned_agent_ids: list[str],
        sender_user_id: str | None,
        required_input_modes: list[str] | None = None,
    ) -> list[dict] | RoomCenterUserMessageResponse:
        """Validate and resolve mentioned_agent_ids into canonical mention dicts.

        Returns the mention list on success, or an error response on failure.
        Called once before persistence; the result is reused downstream.
        """
        agents, invalid_ids = await self._sanitize_routing_scope(
            mentioned_agent_ids,
            sender_user_id=sender_user_id,
            required_input_modes=required_input_modes,
        )
        canonical_mentions = [
            (
                {
                    "agent_id": agent.agent_id,
                    "agent_name": agent.agent_card.name,
                    "mention_text": (f"<@{agent.agent_id}|{agent.agent_card.name}>"),
                }
            )
            for agent in agents
        ]

        if invalid_ids:
            error_msg = (
                f"Invalid or unauthorized mention targets: {', '.join(invalid_ids)}"
            )
            logger.warning(
                "Canonical mention targets rejected (invalid/unauthorized): %s",
                invalid_ids,
            )
            return RoomCenterUserMessageResponse(
                message_id=None,
                message=None,
                success=False,
                error=error_msg,
                scope_resolution_error=ScopeResolutionError(
                    code="unauthorized_mention",
                    message=error_msg,
                ),
                status_code=400,
            )
        return canonical_mentions

    async def _resolve_explicit_target_scope(
        self,
        room: Room,
        message_text: str,
        target_group: str,
        sender_user_id: str | None = None,
        required_input_modes: list[str] | None = None,
    ) -> ResolvedRoutingScope | RoomCenterUserMessageResponse:
        """Resolve selected agents and auto-assign behavior for a target group.

        Returns:
            A named routing scope on success, or a RoomCenterUserMessageResponse
            on scope-resolution failure.

        Deterministic failures (room_team empty, saved_group missing/unauthorized/empty)
        always return structured error responses — never empty tuples.
        """

        async def select_agents_all_agents_mode() -> (
            ResolvedRoutingScope | RoomCenterUserMessageResponse
        ):
            try:
                active_agents = await self._store.get_all_active_agents(
                    user_id=sender_user_id
                )
                active_ids = [agent.agent_id for agent in (active_agents or [])]
                full_agents, _rejected = await self._sanitize_routing_scope(
                    active_ids,
                    sender_user_id=sender_user_id,
                    required_input_modes=required_input_modes,
                )
                selected = {
                    agent.agent_id: agent.agent_card.name for agent in full_agents
                }
                logger.info(
                    "All Agents mode: Providing all %s active agents to Supervisor",
                    len(full_agents),
                )
                return ResolvedRoutingScope(
                    selected_agent_set=selected,
                    auto_assign_agents=True,
                    agents=full_agents,
                )
            except Exception as e:
                error_msg = "Agent selection failed. Please try again."
                logger.error(
                    "All Agents mode selection failed: %s — returning scope error", e
                )
                return RoomCenterUserMessageResponse(
                    message_id=None,
                    message=None,
                    success=False,
                    error=error_msg,
                    scope_resolution_error=ScopeResolutionError(
                        code="empty_scope",
                        message=error_msg,
                    ),
                    status_code=500,
                )

        if target_group == "all_agents":
            return await select_agents_all_agents_mode()

        if target_group == "room_team":
            if room.room_agent_set:
                logger.info(
                    "Room Default mode: Using %s room agents as candidate scope",
                    len(room.room_agent_set),
                )
                room_agents, _rejected = await self._sanitize_routing_scope(
                    room.room_agent_set,
                    sender_user_id=sender_user_id,
                    required_input_modes=required_input_modes,
                )
                selected_agent_set = {
                    agent.agent_id: (
                        room.room_agent_set.get(agent.agent_id) or agent.agent_card.name
                    )
                    for agent in room_agents
                }
                return ResolvedRoutingScope(
                    selected_agent_set=selected_agent_set,
                    auto_assign_agents=True,
                    agents=room_agents,
                )

            error_msg = "This room has no agents. Add agents before sending a message."
            logger.warning(
                "Room Default mode: room has no agents — returning scope-resolution error"
            )
            return RoomCenterUserMessageResponse(
                message_id=None,
                message=None,
                success=False,
                error=error_msg,
                scope_resolution_error=ScopeResolutionError(
                    code="empty_scope",
                    message=error_msg,
                ),
                status_code=400,
            )

        # Custom group (saved-group override at send time)
        group = await self._store.get_agent_group_by_id(target_group)
        if not group:
            error_msg = "The selected agent group no longer exists. Please choose a different group."
            logger.warning(
                "Custom group %s not found — returning scope-resolution error",
                target_group,
            )
            return RoomCenterUserMessageResponse(
                message_id=None,
                message=None,
                success=False,
                error=error_msg,
                scope_resolution_error=ScopeResolutionError(
                    code="group_not_usable",
                    message=error_msg,
                ),
                status_code=404,
            )

        if group.type != "builtin" and group.owner_id != sender_user_id:
            logger.warning(
                "Sender %s not authorized to use saved group %s (owner: %s)",
                sender_user_id,
                target_group,
                group.owner_id,
            )
            return RoomCenterUserMessageResponse(
                message_id=None,
                message=None,
                success=False,
                error="You do not have permission to use this saved group",
                status_code=403,
            )

        if group.agents:
            agents, _rejected = await self._sanitize_routing_scope(
                group.agents,
                sender_user_id=sender_user_id,
                required_input_modes=required_input_modes,
            )

            selected_agent_set = {
                agent.agent_id: agent.agent_card.name for agent in agents
            }
            logger.info(
                "Custom group '%s': Using %s agents",
                group.name,
                len(selected_agent_set),
            )
            return ResolvedRoutingScope(
                selected_agent_set=selected_agent_set,
                auto_assign_agents=True,
                agents=agents,
            )

        error_msg = f"The selected agent group '{group.name}' has no members."
        logger.warning(
            "Custom group '%s' has no agents — returning scope-resolution error",
            group.name,
        )
        return RoomCenterUserMessageResponse(
            message_id=None,
            message=None,
            success=False,
            error=error_msg,
            scope_resolution_error=ScopeResolutionError(
                code="empty_scope",
                message=error_msg,
            ),
            status_code=400,
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


# Singleton export
conversation_runtime = ConversationRuntime()


__all__ = [
    "ConversationRuntime",
    "RoomMessagePreflightContext",
    "conversation_runtime",
]
