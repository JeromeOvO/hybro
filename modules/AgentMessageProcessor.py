"""AgentMessageProcessor — thin transport router.

Extracted from ``QueueExecutor._process_single_message`` so that both
``QueueExecutor`` and ``SupervisorExecutor`` can dispatch individual
agent messages without duplicating code.

Contains zero orchestration logic — only the mechanics of:
1. Building the A2A message via ``room_services.process_agent_message``
2. Running the DispatchChain (pre-dispatch middleware)
3. Looking up the selected transport and calling ``transport.dispatch()``
4. Running post-dispatch middleware
5. Returning a ``ProcessingResult``
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from common.utils.cancellation import CancellationToken
from common.utils.logger import get_logger
from models.processing import ProcessingResult, ProcessingStatus
from models.request import RoomCenterAgentMessageRequest
from models.room import RoomAgentMessage
from modules.dispatch_middleware import DispatchChain, DispatchContext

if TYPE_CHECKING:
    from models.agent import Agent
    from modules.transports.base import AgentTransport
    from services.database_service import DatabaseService
    from services.relay_service import RelayService
    from services.room_services import RoomServices
    from services.sse_services import SSEManager

logger = get_logger(__name__)


class AgentMessageProcessor:
    """Shared single-message dispatch logic.

    Used by both ``QueueExecutor`` and ``SupervisorExecutor``.
    Routes to the appropriate transport via a ``transports`` dict keyed
    by transport name (``"direct"`` / ``"relay"``).
    """

    def __init__(
        self,
        *,
        sse_manager: SSEManager,
        room_services: RoomServices,
        database_service: DatabaseService,
        transports: dict[str, AgentTransport],
        relay_service: RelayService | None = None,
        dispatch_chain: DispatchChain | None = None,
    ) -> None:
        self.sse_manager = sse_manager
        self.room_services = room_services
        self.database_service = database_service
        self.transports = dict(transports)
        self._relay_service_explicit = relay_service
        self._dispatch_chain_explicit = dispatch_chain
        self._lazy_initialized = False
        self.relay_service: RelayService | None = relay_service
        self.dispatch_chain: DispatchChain = dispatch_chain or DispatchChain()

    def _ensure_relay_initialized(self) -> None:
        """Lazily resolve relay_service and build the dispatch chain.

        At module-import time the relay_service singleton is still ``None``
        because ``init_relay_service()`` runs during the FastAPI lifespan.
        This method re-checks on each call until relay is resolved, while
        ensuring CloudHealthMiddleware is always present from the first call.
        """
        if self._dispatch_chain_explicit is not None:
            return

        if not self._lazy_initialized:
            self._lazy_initialized = True
            from modules.middleware.cloud_health import CloudHealthMiddleware
            from services.agent_health_service import agent_health_service
            chain = DispatchChain()
            chain.add(CloudHealthMiddleware(agent_health_service))
            self.dispatch_chain = chain
            logger.info("AgentMessageProcessor: CloudHealthMiddleware initialized")

        if self._relay_service_explicit is not None or self.relay_service is not None:
            return

        try:
            from services.relay_service import relay_service as _svc
            if _svc is not None:
                self.relay_service = _svc
                from modules.middleware.hub_transport import HubTransportMiddleware
                self.dispatch_chain.add(HubTransportMiddleware(_svc))
                if "relay" not in self.transports and _svc.relay_transport is not None:
                    self.transports["relay"] = _svc.relay_transport
                logger.info("AgentMessageProcessor: relay_service resolved lazily")
        except Exception as exc:
            logger.warning("AgentMessageProcessor: relay init deferred: %s", exc)

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
        """Process a single agent message.

        Delegates the actual agent communication to the selected transport's
        ``dispatch()`` method, keeping orchestration logic in the caller.
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

        ctx = DispatchContext(
            agent=agent,
            room_agent_message=current_message,
            room_id=room_id,
            user_message_id=user_message_id,
            prepared_message=prepared_message,
            token=token,
            step_number=step_number,
            total_steps=total_steps,
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

        transport = self.transports.get(ctx.transport)
        if transport is None:
            logger.error(
                "Unknown transport %r for message %s",
                ctx.transport,
                current_message.message_id,
            )
            return ProcessingResult(
                ProcessingStatus.FAILED,
                response_text=f"Unknown transport: {ctx.transport}",
            )

        result = await transport.dispatch(ctx, current_message)
        return await self.dispatch_chain.run_post_dispatch(ctx, result)
