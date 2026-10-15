"""Session facade: client sends, checks, and closes; owner lists and reads.

A session sends each client message to one named agent through the
passthrough execution mode (no orchestrator model turns), records every turn
in a conversation container owned by the Session product, and lets the owner
decide agent interactions in the Hybro app. The client sees only the session
state; it never sees the container id or the approval question.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime

from common.dto import (
    CreateRoomRequest,
    ExecutionAck,
    ExecutionRequest,
    MembershipSeed,
    MembershipUpdateRequest,
    RunInfo,
)
from common.errors import AppError
from common.idempotency import (
    MAX_CLIENT_REQUEST_ID_LENGTH,
    normalize_client_request_id,
)
from common.protocols import (
    AgentNetworkTargetResolver,
    ExecutionEngine,
    HITLManager,
    RoomHistoryReader,
    RoomManagement,
    RoomMessageStore,
    RoomRegistry,
)
from common.utils.logger import get_logger
from common.utils.time import utcnow
from models.room import MAX_MESSAGE_LENGTH
from models.session import (
    SessionAgent,
    SessionClosed,
    SessionListResponse,
    SessionMessageRequest,
    SessionMessageResponse,
    SessionOwnerRecord,
    SessionReply,
    SessionStatus,
    SessionSummary,
)
from session.models import SessionRecord, new_session_id
from session.projection import (
    BUSY,
    busy_state,
    final_message_id,
    resolve_state,
)
from session.protocols import SessionOwner, SessionStore
from session.waiter import CorrelatedOutcome, SessionWaiter

logger = get_logger(__name__)

# The engine's 409 acks carry no error code; the fingerprint conflict is
# recognised by its fixed text (conversation runtime) and pinned by a test.
FINGERPRINT_CONFLICT_ERROR = (
    "The client_request_id was already used for a different request"
)
_ROOM_NAME_WORDS = 8
_ROOM_NAME_MAX = 80
_TITLE_MAX = 120
_LIST_LIMIT = 100


def _error(code: str, message: str) -> AppError:
    return AppError(message, code=code)


def _state_value(run: RunInfo) -> str:
    return str(getattr(run.state, "value", run.state))


class SessionFacade:
    def __init__(
        self,
        *,
        store: SessionStore,
        rooms: RoomManagement,
        registry: RoomRegistry,
        history: RoomHistoryReader,
        messages: RoomMessageStore,
        engine: ExecutionEngine,
        approvals: HITLManager,
        waiter: SessionWaiter,
        now: Callable[[], datetime] = utcnow,
        id_factory: Callable[[], str] = new_session_id,
    ) -> None:
        self._store = store
        self._rooms = rooms
        self._registry = registry
        self._history = history
        self._messages = messages
        self._engine = engine
        self._approvals = approvals
        self._waiter = waiter
        self._now = now
        self._id_factory = id_factory

    # -- Client --------------------------------------------------------------

    async def send(
        self,
        request: SessionMessageRequest,
        *,
        owner: SessionOwner,
        resolver: AgentNetworkTargetResolver,
    ) -> SessionMessageResponse:
        client_request_id = _validated_request_id(request.client_request_id)
        text = _validated_text(request.text)
        await _resolve_agent(resolver, request.agent_id, request.subnet_id)
        record = await self._session_for_send(request, owner, client_request_id, text)
        record = await self._ensure_container(record, owner, text)
        conversation_id = record.conversation_id
        assert conversation_id is not None

        is_retry = record.last_client_request_id == client_request_id
        if not is_retry:
            runs = await self._engine.get_runs_for_room(conversation_id)
            if runs:
                return await self._busy(record, runs, client_request_id)

        seq_before = await self._waiter.latest_seq(conversation_id)
        execution_request = ExecutionRequest(
            room_id=conversation_id,
            sender_id=owner.user_id,
            sender_name=record.client_label,
            # The chat send shape; the server assigns the message id.
            message={
                "room_id": conversation_id,
                "message_id": "",
                "message_type": "user",
                "message_content": {"message_text": text},
            },
            client_request_id=client_request_id,
            mode="passthrough",
            agent_scope={"source": "mention", "agent_ids": [request.agent_id]},
        )
        ack = await self._engine.execute(execution_request)
        if not ack.success:
            return await self._refused(record, ack, client_request_id)
        if not ack.message_id:
            raise _error("execution_error", "The message was not recorded")

        if is_retry:
            after = record.last_send_event_seq
        else:
            after = seq_before
            record = (
                await self._store.record_send(
                    record.session_id,
                    client_request_id=client_request_id,
                    user_message_id=ack.message_id,
                    event_seq=seq_before,
                    at=self._now(),
                )
                or record
            )
        if ack.should_start_orchestration:
            self._engine.schedule_orchestration(execution_request, ack)
            record = await self._accept_agent(record, request.agent_id)

        outcome = await self._waiter.wait(
            conversation_id, client_request_id, after=after
        )
        state, reason = resolve_state(outcome, closed=False)
        replies = await self._replies(outcome)
        return SessionMessageResponse(
            session_id=record.session_id,
            accepted=True,
            state=state,
            reply=replies[0].text if replies else None,
            paused_agent_id=(
                await self._paused_agent_id(record)
                if state == "waiting_on_owner"
                else None
            ),
            reason=reason,
            client_request_id=client_request_id,
        )

    async def check(self, session_id: str, *, owner: SessionOwner) -> SessionStatus:
        record = await self._owned(session_id, owner)
        return await self._status(record)

    async def close(self, session_id: str, *, owner: SessionOwner) -> SessionClosed:
        record = await self._owned(session_id, owner)
        if record.status == "closed":
            return SessionClosed(session_id=record.session_id, status="closed")
        if record.conversation_id:
            await self._stop_activity(record.conversation_id, owner)
        closed = await self._store.mark_closed(record.session_id, self._now())
        return SessionClosed(
            session_id=record.session_id,
            status=closed.status if closed is not None else "closed",
        )

    # -- Owner ---------------------------------------------------------------

    async def list_for_owner(self, *, owner: SessionOwner) -> SessionListResponse:
        records = await self._store.list_for_owner(owner.user_id, _LIST_LIMIT)
        statuses = await asyncio.gather(*(self._status(r) for r in records))
        return SessionListResponse(
            sessions=[
                SessionSummary(
                    session_id=record.session_id,
                    client_label=record.client_label,
                    title=record.title,
                    agents=status.agents,
                    state=status.state,
                    paused_agent_id=status.paused_agent_id,
                    conversation_id=record.conversation_id,
                    opened_at=record.opened_at,
                    last_activity_at=record.last_activity_at,
                    closed_at=record.closed_at,
                )
                for record, status in zip(records, statuses, strict=True)
            ]
        )

    async def record_for_owner(
        self, session_id: str, *, owner: SessionOwner
    ) -> SessionOwnerRecord:
        record = await self._owned(session_id, owner)
        status = await self._status(record)
        return SessionOwnerRecord(
            **status.model_dump(),
            client_label=record.client_label,
            title=record.title,
            status=record.status,
            conversation_id=record.conversation_id,
            closed_at=record.closed_at,
        )

    # -- Internals -------------------------------------------------------------

    async def _session_for_send(
        self,
        request: SessionMessageRequest,
        owner: SessionOwner,
        client_request_id: str,
        text: str,
    ) -> SessionRecord:
        if request.session_id is None:
            label = (request.client_label or "").strip()
            if not label:
                raise _error(
                    "invalid_request", "client_label is required to open a session"
                )
            record = await self._open(owner, label, client_request_id, text)
        else:
            record = await self._owned(request.session_id, owner)
        if record.status == "closed":
            raise _error("session_closed", "The session is closed")
        return record

    async def _open(
        self, owner: SessionOwner, label: str, client_request_id: str, text: str
    ) -> SessionRecord:
        now = self._now()
        return await self._store.reserve(
            SessionRecord(
                session_id=self._id_factory(),
                owner_user_id=owner.user_id,
                client_label=label,
                title=_title(text),
                creation_request_id=client_request_id,
                opened_at=now,
                last_activity_at=now,
            )
        )

    async def _owned(self, session_id: str, owner: SessionOwner) -> SessionRecord:
        record = await self._store.get(session_id)
        if record is None or record.owner_user_id != owner.user_id:
            raise _error("session_not_found", "Session not found")
        return record

    async def _ensure_container(
        self, record: SessionRecord, owner: SessionOwner, text: str
    ) -> SessionRecord:
        if record.conversation_id:
            return record
        room = await self._rooms.create_room(
            CreateRoomRequest(
                owner_id=owner.user_id,
                owner_name=owner.name or owner.user_id,
                room_name=_room_name(record.client_label, text),
                # Agents join the container only after an accepted send.
                membership_seed=MembershipSeed(mode="manual", agent_ids=[]),
                owner_product="session",
            )
        )
        current = await self._store.set_conversation_if_unset(
            record.session_id, room.room_id
        )
        if current is None:
            raise _error("session_not_found", "Session not found")
        if current.conversation_id != room.room_id:
            # A concurrent request created the container first.
            try:
                await self._rooms.delete_room(room.room_id, owner.user_id)
            except Exception:
                logger.warning(
                    "session_orphan_container_delete_failed",
                    extra={"session_id": record.session_id, "room_id": room.room_id},
                    exc_info=True,
                )
        return current

    async def _accept_agent(
        self, record: SessionRecord, agent_id: str
    ) -> SessionRecord:
        conversation_id = record.conversation_id
        assert conversation_id is not None
        try:
            members = await self._registry.get_room_agents(conversation_id)
            if agent_id not in members:
                await self._rooms.update_membership(
                    conversation_id,
                    MembershipUpdateRequest(add_agent_ids=[agent_id]),
                )
        except Exception:
            # The run is already scheduled; membership is presentation only.
            logger.warning(
                "session_member_add_failed",
                extra={"session_id": record.session_id, "agent_id": agent_id},
                exc_info=True,
            )
        return (
            await self._store.record_accepted_agent(record.session_id, agent_id)
            or record
        )

    async def _busy(
        self,
        record: SessionRecord,
        runs: list[RunInfo],
        client_request_id: str,
    ) -> SessionMessageResponse:
        state = busy_state([_state_value(run) for run in runs])
        return SessionMessageResponse(
            session_id=record.session_id,
            accepted=False,
            state=state,
            paused_agent_id=(
                await self._paused_agent_id(record, runs)
                if state == "waiting_on_owner"
                else None
            ),
            reason=BUSY,
            client_request_id=client_request_id,
        )

    async def _refused(
        self,
        record: SessionRecord,
        ack: ExecutionAck,
        client_request_id: str,
    ) -> SessionMessageResponse:
        message = ack.error or "The message was not accepted"
        if ack.status_code == 409:
            if message == FINGERPRINT_CONFLICT_ERROR:
                raise _error("request_conflict", message)
            # The engine saw an active run after the busy check: a race.
            runs = await self._engine.get_runs_for_room(record.conversation_id or "")
            return await self._busy(record, runs, client_request_id)
        if ack.status_code == 404:
            raise _error("not_found", message)
        if 400 <= ack.status_code < 500:
            raise _error("request_rejected", message)
        raise _error("execution_error", message)

    async def _status(self, record: SessionRecord) -> SessionStatus:
        outcome = await self._outcome(record)
        closed = record.status == "closed"
        state, reason = resolve_state(outcome, closed=closed)
        return SessionStatus(
            session_id=record.session_id,
            state=state,
            latest_replies=await self._replies(outcome),
            paused_agent_id=(
                await self._paused_agent_id(record)
                if state == "waiting_on_owner"
                else None
            ),
            reason=reason,
            agents=await self._agents(record),
            opened_at=record.opened_at,
            last_activity_at=record.last_activity_at,
        )

    async def _outcome(self, record: SessionRecord) -> CorrelatedOutcome | None:
        if not record.conversation_id or not record.last_client_request_id:
            return None
        return await self._waiter.scan(
            record.conversation_id,
            record.last_client_request_id,
            after=record.last_send_event_seq,
        )

    async def _replies(self, outcome: CorrelatedOutcome | None) -> list[SessionReply]:
        # Only the final answer is a client reply. Agent-call rows can carry
        # the owner's approval question, which the client must never see.
        message_id = final_message_id(outcome)
        if message_id is None:
            return []
        messages = await self._history.get_messages_by_ids([message_id])
        replies = []
        for message in messages:
            text = message.content.get("message_text")
            if isinstance(text, str) and text:
                replies.append(
                    SessionReply(
                        message_id=message.message_id,
                        text=text,
                        created_at=message.created_at,
                    )
                )
        return replies

    async def _agents(self, record: SessionRecord) -> list[SessionAgent]:
        if not record.conversation_id:
            return []
        room = await self._registry.get_room(record.conversation_id)
        if room is None:
            return []
        return [
            SessionAgent(agent_id=agent_id, name=room.agent_set.get(agent_id))
            for agent_id in room.agent_ids
        ]

    async def _paused_agent_id(
        self, record: SessionRecord, runs: list[RunInfo] | None = None
    ) -> str | None:
        """The agent of the run awaiting the owner, from its trigger message."""
        if runs is None and record.conversation_id:
            runs = await self._engine.get_runs_for_room(record.conversation_id)
        awaiting = next(
            (run for run in runs or [] if _state_value(run) == "awaiting_input"),
            None,
        )
        if awaiting is not None and awaiting.trigger_message_id:
            message = await self._messages.get_message(awaiting.trigger_message_id)
            extend_info = message.metadata.get("extend_info") if message else None
            candidates = (
                extend_info.get("candidate_agent_ids")
                if isinstance(extend_info, dict)
                else None
            )
            if isinstance(candidates, list) and candidates:
                return str(candidates[0])
        return record.last_accepted_agent_id

    async def _stop_activity(self, conversation_id: str, owner: SessionOwner) -> None:
        """Best-effort stop: cancel a pending approval, else the active run."""
        try:
            pending = await self._approvals.get_pending_hitl(conversation_id)
        except Exception:
            logger.warning(
                "session_close_pending_lookup_failed",
                extra={"room_id": conversation_id},
                exc_info=True,
            )
            pending = []
        interactions = {
            request.interaction_id: request.interaction_version
            for request in pending
            if request.interaction_id and request.interaction_version is not None
        }
        if interactions:
            for interaction_id, version in interactions.items():
                try:
                    await self._approvals.cancel_hitl_interaction(
                        conversation_id, interaction_id, version
                    )
                except Exception:
                    logger.warning(
                        "session_close_interaction_cancel_failed",
                        extra={
                            "room_id": conversation_id,
                            "interaction_id": interaction_id,
                        },
                        exc_info=True,
                    )
            return
        for run in await self._engine.get_runs_for_room(conversation_id):
            if not run.trigger_message_id:
                continue
            try:
                await self._engine.cancel(
                    conversation_id,
                    run.trigger_message_id,
                    requested_by_user_id=owner.user_id,
                )
            except Exception:
                # Pending local agent-call cancellation and reconciliation are
                # tolerated; the session is closed regardless.
                logger.warning(
                    "session_close_run_cancel_failed",
                    extra={"room_id": conversation_id, "run_id": run.run_id},
                    exc_info=True,
                )


def _validated_request_id(value: str | None) -> str:
    if not isinstance(value, str):
        raise _error("invalid_request", "client_request_id is required")
    normalized = normalize_client_request_id(value)
    if not normalized:
        raise _error("invalid_request", "client_request_id is required")
    if len(normalized) > MAX_CLIENT_REQUEST_ID_LENGTH:
        raise _error(
            "invalid_request",
            "client_request_id exceeds maximum length of "
            f"{MAX_CLIENT_REQUEST_ID_LENGTH} characters",
        )
    return normalized


def _validated_text(text: str | None) -> str:
    if not isinstance(text, str) or not text.strip():
        raise _error("invalid_request", "text is required")
    if len(text) > MAX_MESSAGE_LENGTH:
        raise _error(
            "invalid_request",
            f"text exceeds maximum length of {MAX_MESSAGE_LENGTH} characters",
        )
    return text


async def _resolve_agent(
    resolver: AgentNetworkTargetResolver, agent_id: str, subnet_id: str | None
) -> None:
    try:
        await resolver.resolve_target(agent_id, subnet_id)
    except AppError as exc:
        if exc.code == "NOT_FOUND":
            entity = exc.details.get("entity_type")
            code = "subnet_not_found" if entity == "AgentGroup" else "agent_not_found"
            raise _error(code, exc.message) from exc
        if exc.code == "CONFLICT":
            raise _error("agent_unavailable", exc.message) from exc
        if exc.code == "VALIDATION":
            raise _error("invalid_request", exc.message) from exc
        if exc.code == "FORBIDDEN":
            raise _error("forbidden", exc.message) from exc
        raise


def _title(text: str) -> str | None:
    """The owner-facing session name: the first message on one short line."""
    line = " ".join(text.split())
    if len(line) <= _TITLE_MAX:
        return line or None
    return line[: _TITLE_MAX - 1].rstrip() + "…"


def _room_name(client_label: str, text: str) -> str:
    words = " ".join(text.split()[:_ROOM_NAME_WORDS])
    name = f"{client_label}: {words}" if words else client_label
    return name if len(name) <= _ROOM_NAME_MAX else name[: _ROOM_NAME_MAX - 3] + "..."


__all__ = ["FINGERPRINT_CONFLICT_ERROR", "SessionFacade", "SessionOwner"]
