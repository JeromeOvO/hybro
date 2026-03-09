"""AgentMessageProcessor — shared single-message dispatch logic.

Extracted from ``QueueExecutor._process_single_message`` so that both
``QueueExecutor`` and ``SupervisorExecutor`` can dispatch individual
agent messages without duplicating code.

Contains zero orchestration logic — only the mechanics of:
1. Building the A2A message via ``room_services.process_agent_message``
2. Running the DispatchChain (pre-dispatch middleware)
3. Choosing streaming vs sync dispatch (direct) or relay dispatch
4. Handling PAUSED (push notification) results
5. Returning a ``ProcessingResult``
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from a2a.types import TaskState

from common.utils.cancellation import CancellationToken
from common.utils.logger import get_logger
from models.hub import RelayToHubEvent
from models.processing import ProcessingResult, ProcessingStatus
from models.request import RoomCenterAgentMessageRequest
from models.room import RoomAgentMessage
from modules.dispatch_middleware import DispatchChain, DispatchContext
from modules.TaskStateManager import get_task
from services.task_notification_service import notify_task_update

if TYPE_CHECKING:
    from models.agent import Agent
    from modules.ResponseProcessor import ResponseProcessor
    from modules.TaskStateManager import TaskStateManager
    from modules.transports.direct import DirectTransport
    from modules.transports.relay import RelayTransport
    from services.a2a_service import A2AService
    from services.database_service import DatabaseService
    from services.relay_service import RelayService
    from services.room_services import RoomServices
    from services.sse_services import SSEManager

logger = get_logger(__name__)


class AgentMessageProcessor:
    """Shared single-message dispatch logic.

    Used by both ``QueueExecutor`` and ``SupervisorExecutor``.
    """

    def __init__(
        self,
        *,
        tsm: TaskStateManager,
        sse_manager: SSEManager,
        response_processor: ResponseProcessor,
        a2a_service: A2AService,
        room_services: RoomServices,
        database_service: DatabaseService,
        relay_service: RelayService | None = None,
        relay_transport: RelayTransport | None = None,
        direct_transport: DirectTransport | None = None,
        dispatch_chain: DispatchChain | None = None,
    ) -> None:
        self.tsm = tsm
        self.sse_manager = sse_manager
        self.response_processor = response_processor
        self.a2a_service = a2a_service
        self.room_services = room_services
        self.database_service = database_service
        self.direct_transport: DirectTransport | None = direct_transport
        self._relay_service_explicit = relay_service
        self._relay_transport_explicit = relay_transport
        self._dispatch_chain_explicit = dispatch_chain
        self._lazy_initialized = False
        self.relay_service: RelayService | None = relay_service
        self.relay_transport: RelayTransport | None = relay_transport
        self.dispatch_chain: DispatchChain = dispatch_chain or DispatchChain()

    def _ensure_relay_initialized(self) -> None:
        """Lazily resolve relay_service and build the dispatch chain.

        At module-import time the relay_service singleton is still ``None``
        because ``init_relay_service()`` runs during the FastAPI lifespan.
        This method re-checks on first call and wires up the middleware.
        """
        if self._lazy_initialized:
            return
        self._lazy_initialized = True

        if self._relay_service_explicit is not None:
            return

        try:
            from services.relay_service import relay_service as _svc
            if _svc is not None:
                self.relay_service = _svc
                if self._dispatch_chain_explicit is None:
                    from modules.middleware.hub_transport import HubTransportMiddleware
                    chain = DispatchChain()
                    chain.add(HubTransportMiddleware(_svc))
                    self.dispatch_chain = chain
                if self._relay_transport_explicit is None and self.relay_transport is None:
                    from modules.transports.relay import RelayTransport as _RT
                    from modules.agent_response_handler import AgentResponseHandler
                    from services.database_service import db_service
                    from services.sse_services import sse_manager as _sse
                    from modules.RoomMessageCenter import room_message_center as _rmc
                    handler = AgentResponseHandler(db_service, _sse, _rmc)
                    self.relay_transport = _RT(handler, _svc, db_service, _sse)
                    _svc.set_relay_transport(self.relay_transport)
                logger.info("AgentMessageProcessor: relay_service resolved lazily")
        except Exception:
            pass

    async def process_single_message(
        self,
        current_message: RoomAgentMessage,
        room_id: str,
        agent: Agent,
        user_message_id: str,
        *,
        token: CancellationToken | None = None,
        step_number: int | None = None,
        total_steps: int | None = None,
        quoted_text: str | None = None,
    ) -> ProcessingResult:
        """Process a single agent message with streaming support.

        Delegates the actual agent communication (streaming/sync) to the
        ``ResponseProcessor``, keeping orchestration logic in the caller.
        """
        self._ensure_relay_initialized()

        room_memory = await self.database_service.get_room_memory_by_room_id(room_id)

        process_response = await self.room_services.process_agent_message(
            RoomCenterAgentMessageRequest(message=current_message),
            room_memory=room_memory,
            quoted_text=quoted_text,
        )

        if not process_response.success:
            return ProcessingResult(ProcessingStatus.FAILED)

        prepared_message = process_response.a2a_message
        if prepared_message is None:
            return ProcessingResult(ProcessingStatus.FAILED)

        # --- DispatchMiddleware pre-dispatch ---
        ctx = DispatchContext(
            agent=agent,
            room_agent_message=current_message,
            room_id=room_id,
            user_message_id=user_message_id,
            prepared_message=prepared_message,
        )
        ctx = await self.dispatch_chain.run_pre_dispatch(ctx)

        if ctx.denied:
            logger.info(
                "Dispatch denied for message %s: %s",
                current_message.message_id,
                ctx.deny_reason,
            )
            return ProcessingResult(
                ProcessingStatus.FAILED,
                response_text=ctx.deny_reason or "Dispatch denied",
            )

        # --- Branch on transport ---
        if ctx.transport == "relay":
            return await self._dispatch_via_relay(ctx, current_message)

        # --- Direct transport (unchanged original logic) ---
        result = await self._dispatch_direct(
            ctx, current_message, agent, room_id, user_message_id,
            prepared_message, token, step_number, total_steps,
        )

        return await self.dispatch_chain.run_post_dispatch(ctx, result)

    # ------------------------------------------------------------------
    # Relay transport
    # ------------------------------------------------------------------

    async def _dispatch_via_relay(
        self,
        ctx: DispatchContext,
        current_message: RoomAgentMessage,
    ) -> ProcessingResult:
        if self.relay_transport is not None:
            result = await self.relay_transport.dispatch(ctx, current_message)
            return await self.dispatch_chain.run_post_dispatch(ctx, result)

        if not self.relay_service:
            logger.error(
                "Relay transport selected but relay_service not available"
            )
            return ProcessingResult(ProcessingStatus.FAILED, "Relay service unavailable")

        from common.utils.time import utcnow as _utcnow

        now = _utcnow()
        task_data = {
            "id": f"relay-pending-{current_message.message_id[:12]}",
            "status": {"state": "submitted"},
            "context_id": current_message.message_id,
        }
        agent_url = ""
        if hasattr(ctx.agent, "agent_card") and hasattr(ctx.agent.agent_card, "url"):
            agent_url = ctx.agent.agent_card.url or ""
        elif hasattr(ctx.agent, "agent_card") and isinstance(ctx.agent.agent_card, dict):
            agent_url = ctx.agent.agent_card.get("url", "")

        await self.database_service.enable_task_tracking_on_message(
            message_id=current_message.message_id,
            webhook_token_hash="",
            agent_url=agent_url,
            task_created_at=now,
            task_updated_at=now,
            task_data=task_data,
        )

        event = RelayToHubEvent(
            type="user_message",
            room_id=ctx.room_id,
            user_message_id=ctx.user_message_id,
            agent_message_id=current_message.message_id,
            agent_id=ctx.agent.agent_id,
            local_agent_id=ctx.agent.local_agent_id,
            message=ctx.prepared_message.model_dump(mode="json"),
        )

        queued_offline = ctx.metadata.get("queued_for_offline", False)
        delivered = await self.relay_service.push_to_hub(
            ctx.agent.hub_id, event
        )

        if not delivered and not queued_offline:
            await self.sse_manager.send_error(
                ctx.room_id,
                "Hub agent is offline; message queued for later delivery",
                message_id=current_message.message_id,
            )

        result = ProcessingResult(
            ProcessingStatus.RELAY_DISPATCHED,
            response_text="",
            message_id=current_message.message_id,
        )
        return await self.dispatch_chain.run_post_dispatch(ctx, result)

    # ------------------------------------------------------------------
    # Direct transport (original logic)
    # ------------------------------------------------------------------

    async def _dispatch_direct(
        self,
        ctx,
        current_message,
        agent,
        room_id,
        user_message_id,
        prepared_message,
        token,
        step_number,
        total_steps,
    ) -> ProcessingResult:
        support_streaming = self.a2a_service.has_streaming_capability(
            agent_card=agent.agent_card
        )

        rp = self.direct_transport.response_processor if self.direct_transport else self.response_processor
        full_response_text = ""
        paused_message_id = None
        if support_streaming:
            try:
                (
                    status,
                    full_response_text,
                ) = await rp.handle_streaming_response(
                    current_message,
                    agent.agent_card,
                    prepared_message,
                    room_id,
                    user_message_id,
                    token=token,
                    send_sse=True,
                    step_number=step_number,
                    total_steps=total_steps,
                )
            except Exception as exc:
                logger.error(
                    "AgentMessageProcessor: Unhandled exception in streaming for message %s: %s",
                    current_message.message_id,
                    exc,
                    exc_info=True,
                )
                await self.tsm.transition_task(
                    current_message, TaskState.failed,
                    error=f"Agent streaming failed: {exc}",
                    persist=True,
                )
                await notify_task_update(
                    message_id=current_message.message_id,
                    state=TaskState.failed,
                    room_id=room_id,
                    user_id=current_message.user_id or "",
                    error=f"Agent streaming failed: {exc}",
                )
                return ProcessingResult(ProcessingStatus.FAILED, "")
            if status != ProcessingStatus.SUCCESS:
                return ProcessingResult(status, full_response_text)
        else:
            (
                success,
                full_response_text,
                paused_message_id,
            ) = await rp.handle_sync_response(
                current_message,
                agent.agent_card,
                prepared_message,
                room_id,
                current_message.user_id,
                user_message_id=user_message_id,
                token=token,
                step_number=step_number,
                total_steps=total_steps,
            )
            if not success:
                task = get_task(current_message)
                was_canceled = (
                    (token and token.is_cancelled)
                    or (task and task.status and task.status.state == TaskState.canceled)
                )
                if was_canceled:
                    return ProcessingResult(ProcessingStatus.CANCELED)
                return ProcessingResult(ProcessingStatus.FAILED)

        if full_response_text is None and paused_message_id:
            task = get_task(current_message)
            if task and task.status and task.status.state == TaskState.input_required:
                logger.info(
                    "AgentMessageProcessor: Agent returned input_required for message %s",
                    paused_message_id,
                )
                task_data = task.model_dump(mode="json") if hasattr(task, "model_dump") else {}
                status_msg = None
                if task.status and task.status.message:
                    parts = task.status.message.parts or []
                    for p in parts:
                        if hasattr(p, "root") and hasattr(p.root, "text"):
                            status_msg = p.root.text
                            break
                        if hasattr(p, "text"):
                            status_msg = p.text
                            break
                return ProcessingResult(
                    ProcessingStatus.AWAITING_INPUT,
                    response_text="",
                    message_id=paused_message_id,
                    a2a_task_id=task_data.get("id") or (task.id if hasattr(task, "id") else None),
                    a2a_context_id=task_data.get("context_id") or (task.context_id if hasattr(task, "context_id") else None),
                    status_message=status_msg,
                )

            logger.info(
                "AgentMessageProcessor: Push notification task submitted for message %s; "
                "queue will be paused until task completes",
                paused_message_id,
            )
            return ProcessingResult(
                ProcessingStatus.PAUSED,
                response_text="",
                message_id=paused_message_id,
            )

        if full_response_text is None:
            logger.info(
                "AgentMessageProcessor: Async task submitted for message %s; "
                "skipping immediate agent response",
                current_message.message_id,
            )
            return ProcessingResult(ProcessingStatus.SUCCESS)

        current_message = (
            await self.database_service.get_room_agent_message_by_message_id(
                current_message.message_id
            )
        )

        if current_message is None:
            return ProcessingResult(ProcessingStatus.FAILED, full_response_text)

        return ProcessingResult(ProcessingStatus.SUCCESS, full_response_text)
