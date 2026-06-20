"""Outbound transport for hub-connected local A2A agents."""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from common.dto import HubCancelCommand, HubDispatchCommand, HubReplyCommand
from common.utils.logger import get_logger
from common.utils.time import utcnow
from execution.dispatch.transports.base import AgentTransport
from models.processing import ProcessingResult, ProcessingStatus

if TYPE_CHECKING:
    from execution.dispatch.dispatch_middleware import DispatchContext
    from execution.dispatch.response_handler import AgentResponseHandler
    from execution.ports import ExecutionDeliveryPort
    from models.room import RoomAgentMessage

logger = get_logger(__name__)


@runtime_checkable
class RelayTaskTracker(Protocol):
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


@runtime_checkable
class RelayAgentCallCounter(Protocol):
    async def increment_agent_call_count(self, agent_id: str, *, success: bool) -> bool: ...


class RelayTransport(AgentTransport):
    def __init__(
        self,
        response_handler: AgentResponseHandler,
        relay_service: Any,
        task_tracker: RelayTaskTracker,
        delivery: ExecutionDeliveryPort,
        call_counter: RelayAgentCallCounter | None = None,
        ownership_store: Any | None = None,
        ownership_lease_maintainer: Any | None = None,
        worker_id: str = "local-worker",
    ) -> None:
        super().__init__(response_handler)
        self.relay_service = relay_service
        self._task_tracker = task_tracker
        self._delivery = delivery
        self._call_counter = call_counter
        self._ownership_store = ownership_store
        self._ownership_lease_maintainer = ownership_lease_maintainer
        self._worker_id = worker_id

    async def dispatch(
        self,
        ctx: DispatchContext,
        message: RoomAgentMessage,
    ) -> ProcessingResult:
        if not self.relay_service:
            logger.error("Relay transport selected but relay service not available")
            return ProcessingResult(ProcessingStatus.FAILED, "Relay service unavailable")

        now = utcnow()
        task_id = f"relay-pending-{message.message_id[:12]}"
        task_data = {
            "id": task_id,
            "status": {"state": "submitted"},
            "context_id": message.message_id,
        }
        agent_url = ""
        if hasattr(ctx.agent, "agent_card") and hasattr(ctx.agent.agent_card, "url"):
            agent_url = ctx.agent.agent_card.url or ""
        elif hasattr(ctx.agent, "agent_card") and isinstance(ctx.agent.agent_card, dict):
            agent_url = ctx.agent.agent_card.get("url", "")

        await self._task_tracker.enable_task_tracking_on_message(
            message_id=message.message_id,
            webhook_token_hash="",
            agent_url=agent_url,
            task_created_at=now,
            task_updated_at=now,
            task_data=task_data,
        )
        if self._ownership_store is not None:
            ownership = await self._ownership_store.claim_or_refresh(
                {
                    "agent_message_id": message.message_id,
                    "local_task_id": task_id,
                },
                self._worker_id,
            )
            if self._ownership_lease_maintainer is not None:
                self._ownership_lease_maintainer.track(
                    ownership.get("aliases")
                    or {
                        "agent_message_id": message.message_id,
                        "local_task_id": task_id,
                    },
                    ownership.get("lease_token"),
                )

        command = HubDispatchCommand(
            hub_id=ctx.agent.hub_id,
            agent_id=ctx.agent.agent_id,
            local_agent_id=ctx.agent.local_agent_id,
            room_id=ctx.room_id,
            user_message_id=ctx.user_message_id,
            agent_message_id=message.message_id,
            payload=ctx.prepared_message.model_dump(mode="json"),
            task_id=task_id,
            task_data=task_data,
            task_created_at=now,
            task_updated_at=now,
        )
        if "push_to_hub" in vars(self.relay_service):
            delivery = self.relay_service.push_to_hub(
                command.hub_id,
                SimpleNamespace(
                    type="user_message",
                    room_id=command.room_id,
                    user_message_id=command.user_message_id,
                    agent_message_id=command.agent_message_id,
                    agent_id=command.agent_id,
                    local_agent_id=command.local_agent_id,
                    message=command.payload,
                ),
            )
            result = await delivery if inspect.isawaitable(delivery) else delivery
        else:
            delivery = self.relay_service.send_to_hub(command)
            result = await delivery if inspect.isawaitable(delivery) else delivery
        delivered = bool(getattr(result, "accepted", result))

        if self._call_counter is not None:
            try:
                await self._call_counter.increment_agent_call_count(
                    ctx.agent.agent_id, success=delivered
                )
            except Exception as exc:
                logger.warning(
                    "Failed to record hub agent call for %s: %s",
                    ctx.agent.agent_id,
                    exc,
                )

        if not delivered and not ctx.metadata.get("queued_for_offline", False):
            await self._delivery.send_error(
                ctx.room_id,
                "Hub agent is offline; message queued for later delivery",
                message_id=message.message_id,
            )

        return ProcessingResult(
            ProcessingStatus.RELAY_DISPATCHED,
            response_text="",
            message_id=message.message_id,
        )

    async def cancel_task(
        self,
        hub_id: str,
        agent_message_id: str,
        local_agent_id: str,
        task_id: str | None = None,
    ) -> bool:
        return await self.relay_service.cancel_hub_task(
            HubCancelCommand(
                hub_id=hub_id,
                agent_message_id=agent_message_id,
                local_agent_id=local_agent_id,
                task_id=task_id,
            )
        )

    async def reply_to_task(
        self,
        hub_id: str,
        agent_message_id: str,
        local_agent_id: str,
        reply_text: str,
        room_id: str,
        task_id: str | None = None,
        context_id: str | None = None,
    ) -> bool:
        return await self.relay_service.reply_to_hub_task(
            HubReplyCommand(
                hub_id=hub_id,
                agent_message_id=agent_message_id,
                local_agent_id=local_agent_id,
                room_id=room_id,
                reply_text=reply_text,
                task_id=task_id,
                context_id=context_id,
            )
        )
