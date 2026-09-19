"""One bounded, non-persistent send to a freshly resolved network agent."""

from __future__ import annotations

import asyncio

from a2a_adapter.client_facade import (
    A2AMessageValidationError,
    is_timeout_error,
    send_message,
)
from agent.protocols import AgentNetworkAccess
from common.errors import AppError, UpstreamError, ValidationError
from common.observability import bind_log_context
from models.agent_network import AgentMessageRequest, AgentMessageResponse

_SEND_TIMEOUT_SECONDS = 600.0


class DirectAgentExecution:
    def __init__(self, network: AgentNetworkAccess) -> None:
        self._network = network

    async def send(self, request: AgentMessageRequest) -> AgentMessageResponse:
        correlation = {
            "agent_id": request.agent_id,
            "client_request_id": request.client_request_id,
        }
        with bind_log_context(client_request_id=request.client_request_id):
            try:
                async with asyncio.timeout(_SEND_TIMEOUT_SECONDS):
                    target = await self._network.resolve_target(
                        request.agent_id, request.group_id
                    )
                    # The adapter generates a missing message ID and preserves all
                    # caller continuation IDs. Never retry an ambiguously sent turn.
                    response = await send_message(
                        target.raw_card,
                        request.message,
                        timeout=_SEND_TIMEOUT_SECONDS,
                        allow_docker_host_fallback=False,
                    )
                    if not _has_message_or_task(response):
                        raise UpstreamError(
                            "Agent returned an invalid or unsuccessful A2A response",
                            service="a2a",
                        )
                    return AgentMessageResponse(
                        agent_id=request.agent_id,
                        client_request_id=request.client_request_id,
                        result=response["result"],
                    )
            except AppError as exc:
                exc.details = {**exc.details, **correlation}
                raise
            except A2AMessageValidationError:
                raise ValidationError(
                    "Message cannot be represented as an A2A message",
                    details=correlation,
                ) from None
            except Exception as exc:
                # CancelledError is a BaseException, so caller cancellation passes
                # through unchanged. SDK HTTP timeouts are typed wrapped errors.
                if is_timeout_error(exc):
                    raise AppError(
                        "Agent request timed out",
                        code="UPSTREAM_TIMEOUT",
                        details=correlation,
                    ) from None
                raise UpstreamError(
                    "Agent request failed",
                    service="a2a",
                    details=correlation,
                ) from None


def _has_message_or_task(response: object) -> bool:
    if not isinstance(response, dict) or response.get("error") is not None:
        return False
    kind = response.get("kind")
    result = response.get("result")
    if not isinstance(result, dict) or result.get("kind") != kind:
        return False
    if kind == "message":
        return (
            isinstance(result.get("messageId"), str)
            and bool(result["messageId"])
            and isinstance(result.get("parts"), list)
            and bool(result["parts"])
        )
    if kind == "task":
        status = result.get("status")
        return (
            isinstance(result.get("id"), str)
            and bool(result["id"])
            and isinstance(status, dict)
            and bool(status.get("state"))
        )
    return False
