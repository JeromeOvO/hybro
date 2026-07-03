"""AgentMessageProcessor — thin transport router.

Extracted from ``QueueExecutor._process_single_message`` so that both
``QueueExecutor`` and ``SupervisorExecutor`` can dispatch individual
agent messages without duplicating code.

Contains zero orchestration logic — only the mechanics of:
1. Building the A2A message via ``room_runtime.process_agent_message``
2. Running the DispatchChain (pre-dispatch middleware)
3. Looking up the selected transport and calling ``transport.dispatch()``
4. Running post-dispatch middleware
5. Returning a ``ProcessingResult``
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from common.utils.cancellation import CancellationToken
from common.utils.logger import get_logger
from execution.dispatch.dispatch_middleware import DispatchChain, DispatchContext
from models.processing import ProcessingResult, ProcessingStatus
from models.request import RoomCenterAgentMessageRequest
from models.room import RoomAgentMessage

if TYPE_CHECKING:
    from execution.dispatch.transports.base import AgentTransport
    from execution.ports import ExecutionDeliveryPort, RoomRuntimePort
    from models.agent import Agent


if TYPE_CHECKING:
    class RoomMemoryReader(Protocol):
        async def get_room_memory_by_room_id(self, room_id: str): ...

    class TaskTrackerPort(Protocol):
        async def enable_task_tracking_on_message(
            self,
            message_id: str,
            *,
            webhook_token_hash: str | None,
            agent_url: str | None,
            task_created_at,
            task_updated_at,
            task_data: dict,
        ) -> bool: ...

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
        delivery: ExecutionDeliveryPort,
        room_runtime: RoomRuntimePort,
        room_memory_reader: RoomMemoryReader,
        task_tracker: TaskTrackerPort,
        transports: dict[str, AgentTransport],
        relay_service: object | None = None,
        dispatch_chain: DispatchChain | None = None,
        health_service: object | None = None,
        cloud_health_cache_ttl: float = 30.0,
        cloud_health_check_timeout: float = 5.0,
    ) -> None:
        self.delivery = delivery
        self.room_runtime = room_runtime
        self._room_memory_reader = room_memory_reader
        self._task_tracker = task_tracker
        self.transports = dict(transports)
        self._relay_service_explicit = relay_service
        self._dispatch_chain_explicit = dispatch_chain
        self._health_service = health_service
        self._cloud_health_cache_ttl = cloud_health_cache_ttl
        self._cloud_health_check_timeout = cloud_health_check_timeout
        self._lazy_initialized = False
        self.relay_service: object | None = relay_service
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
            from execution.dispatch.middleware.cloud_health import CloudHealthMiddleware
            chain = DispatchChain()
            if self._health_service is not None:
                chain.add(
                    CloudHealthMiddleware(
                        self._health_service,
                        cache_ttl=self._cloud_health_cache_ttl,
                        check_timeout=self._cloud_health_check_timeout,
                    )
                )
            self.dispatch_chain = chain
            logger.info("AgentMessageProcessor: CloudHealthMiddleware initialized")

        if self._relay_service_explicit is not None:
            if self.relay_service is None:
                self.relay_service = self._relay_service_explicit
            self._add_hub_transport_middleware(self.relay_service)

    def _add_hub_transport_middleware(self, relay_service: object) -> None:
        if self._dispatch_chain_explicit is not None:
            return

        from execution.dispatch.middleware.hub_transport import HubTransportMiddleware

        middlewares = getattr(self.dispatch_chain, "_middlewares", [])
        if any(isinstance(mw, HubTransportMiddleware) for mw in middlewares):
            return
        self.dispatch_chain.add(HubTransportMiddleware(relay_service))
        logger.info("AgentMessageProcessor: HubTransportMiddleware initialized")

    def bind_relay_service(
        self, relay_service: object, transport: AgentTransport | None = None
    ) -> None:
        self._ensure_relay_initialized()
        self._relay_service_explicit = relay_service
        self.relay_service = relay_service

        resolved_transport = transport or getattr(relay_service, "relay_transport", None)
        if resolved_transport is None:
            direct_transport = self.transports.get("direct")
            response_handler = getattr(direct_transport, "response_handler", None)
            if response_handler is not None:
                from execution.dispatch.transports.relay import RelayTransport

                resolved_transport = RelayTransport(
                    response_handler=response_handler,
                    relay_service=relay_service,
                    task_tracker=self._task_tracker,
                    call_counter=getattr(relay_service, "agent_call_counter", None),
                    delivery=self.delivery,
                    ownership_store=getattr(relay_service, "task_ownership_store", None),
                    ownership_lease_maintainer=getattr(
                        relay_service,
                        "ownership_lease_maintainer",
                        None,
                    ),
                    worker_id=getattr(relay_service, "worker_id", "local-worker"),
                )
        if resolved_transport is not None:
            self.transports["relay"] = resolved_transport
        self._add_hub_transport_middleware(relay_service)

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

        room_memory = await self._room_memory_reader.get_room_memory_by_room_id(room_id)

        process_response = await self.room_runtime.process_agent_message(
            RoomCenterAgentMessageRequest(message=current_message),
            room_memory=room_memory,
            quoted_text=quoted_text,
            orchestration_user_message_id=user_message_id,
        )

        preflight_failure = (
            current_message.extend_info.get("attachment_preflight_failure")
            if isinstance(current_message.extend_info, dict)
            else None
        )
        preflight_code = (
            str(preflight_failure.get("code"))
            if isinstance(preflight_failure, dict) and preflight_failure.get("code")
            else None
        )

        if not process_response.success:
            return ProcessingResult(
                ProcessingStatus.FAILED,
                response_text=process_response.error
                or "Agent message preparation failed",
                status_message=preflight_code,
            )

        prepared_message = process_response.a2a_message
        if prepared_message is None:
            return ProcessingResult(
                ProcessingStatus.FAILED,
                response_text=process_response.error
                or "Agent message preparation failed",
                status_message=preflight_code,
            )

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
