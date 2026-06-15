from __future__ import annotations

from common.dto import (
    HubCancelCommand,
    HubDispatchCommand,
    HubDispatchResult,
    HubReplyCommand,
    OfflineHubFailureCommand,
)
from hub_runtime_bridge.transport.relay_transport import (
    cancel_command_to_event,
    dispatch_command_to_event,
    reply_command_to_event,
)


class HubRelayService:
    def __init__(self, *, push_event, offline_failure_port=None, call_counter=None) -> None:
        self._push_event = push_event
        self._offline_failure_port = offline_failure_port
        self._call_counter = call_counter

    async def send_to_hub(self, command: HubDispatchCommand) -> HubDispatchResult:
        delivery_result = await self._push_event(
            command.hub_id, dispatch_command_to_event(command)
        )
        delivered = bool(delivery_result)
        hub_offline = delivery_result is False
        if self._call_counter:
            try:
                await self._call_counter.increment_agent_call_count(
                    command.agent_id, success=delivered
                )
            except Exception:
                pass
        if hub_offline and self._offline_failure_port:
            await self._offline_failure_port.mark_hub_message_failed(
                OfflineHubFailureCommand(
                    room_id=command.room_id,
                    agent_message_id=command.agent_message_id,
                    agent_id=command.agent_id,
                    task_id=command.task_id,
                    error_text="Hub agent is offline; message queued for later delivery",
                )
            )
        return HubDispatchResult(
            hub_id=command.hub_id,
            accepted=delivered,
            task_id=command.task_id,
            error=None
            if delivered
            else ("hub_offline" if hub_offline else "hub_dispatch_failed"),
        )

    async def cancel_hub_task(self, command: HubCancelCommand) -> bool:
        return bool(await self._push_event(command.hub_id, cancel_command_to_event(command)))

    async def reply_to_hub_task(self, command: HubReplyCommand) -> bool:
        return bool(await self._push_event(command.hub_id, reply_command_to_event(command)))


__all__ = ["HubRelayService"]
