"""High-level SDK facade for A2A client operations."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from typing import Any
from uuid import uuid4

import httpx
from a2a.client import A2ACardResolver as SDKCardResolver
from a2a.client import A2AClient
from a2a.client.errors import A2AClientHTTPError
from a2a.types import (
    AgentCard,
    CancelTaskRequest,
    JSONRPCErrorResponse,
    MessageSendConfiguration,
    MessageSendParams,
    PushNotificationConfig,
    SendMessageRequest,
    SendStreamingMessageRequest,
    TaskIdParams,
)

from .card_data import sdk_agent_card_data
from .constants import AGENT_CARD_WELL_KNOWN_PATH, PREV_AGENT_CARD_WELL_KNOWN_PATH
from .message_factory import to_sdk_message

logger = logging.getLogger(__name__)


class A2AClientFacadeError(Exception):
    """Raised when an A2A client operation fails."""

    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


async def fetch_agent_card_with_fallback(
    agent_url: str,
    *,
    timeout: float = 30.0,
) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=timeout) as client:
        for path in (AGENT_CARD_WELL_KNOWN_PATH, PREV_AGENT_CARD_WELL_KNOWN_PATH):
            resolver = SDKCardResolver(client, str(agent_url), path)
            try:
                card = await resolver.get_agent_card()
                return card.model_dump(mode="json")
            except A2AClientHTTPError as exc:
                if exc.status_code == 404 and path == AGENT_CARD_WELL_KNOWN_PATH:
                    continue
                raise A2AClientFacadeError(
                    f"Failed to fetch card: {exc}",
                    status_code=exc.status_code,
                ) from exc
            except Exception as exc:
                raise A2AClientFacadeError(str(exc)) from exc
    raise A2AClientFacadeError("Agent card not found at any known path")


async def send_message(
    agent_card_data: Any,
    message_data: Any,
    *,
    accepted_output_modes: list[str] | None = None,
    push_notification_config: dict[str, Any] | None = None,
    blocking: bool = True,
    timeout: float = 600.0,
) -> dict[str, Any]:
    card = AgentCard(**sdk_agent_card_data(agent_card_data))
    sdk_message = to_sdk_message(message_data)
    push_config = (
        PushNotificationConfig(**push_notification_config)
        if push_notification_config
        else None
    )
    request = SendMessageRequest(
        id=str(uuid4()),
        method="message/send",
        jsonrpc="2.0",
        params=MessageSendParams(
            message=sdk_message,
            configuration=MessageSendConfiguration(
                accepted_output_modes=accepted_output_modes or ["text/plain"],
                push_notification_config=push_config,
                blocking=blocking,
            ),
        ),
    )
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await A2AClient(client, agent_card=card).send_message(request)
    return _normalize_response(response)


async def stream_message(
    agent_card_data: Any,
    message_data: Any,
    *,
    accepted_output_modes: list[str] | None = None,
    timeout: float = 600.0,
) -> AsyncGenerator[dict[str, Any], None]:
    card = AgentCard(**sdk_agent_card_data(agent_card_data))
    request = SendStreamingMessageRequest(
        id=str(uuid4()),
        method="message/stream",
        jsonrpc="2.0",
        params=MessageSendParams(
            message=to_sdk_message(message_data),
            configuration=MessageSendConfiguration(
                accepted_output_modes=accepted_output_modes or ["text/plain"],
            ),
        ),
    )
    async with httpx.AsyncClient(timeout=timeout) as client:
        async for response in A2AClient(client, agent_card=card).send_message_streaming(
            request
        ):
            yield _normalize_response(response)


async def cancel_remote_task(
    agent_card_data: Any,
    task_id: str,
    *,
    timeout: float = 5.0,
) -> bool:
    try:
        card = AgentCard(**sdk_agent_card_data(agent_card_data))
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await asyncio.wait_for(
                A2AClient(client, agent_card=card).cancel_task(
                    CancelTaskRequest(id=str(uuid4()), params=TaskIdParams(id=task_id))
                ),
                timeout=timeout,
            )
        return not isinstance(response.root, JSONRPCErrorResponse)
    except Exception:
        return False


async def send_hitl_reply(
    agent_url: str,
    message_data: Any,
    *,
    push_notification_config: dict[str, Any] | None = None,
    blocking: bool = True,
    timeout: float = 600.0,
) -> dict[str, Any]:
    push_config = (
        PushNotificationConfig(**push_notification_config)
        if push_notification_config
        else None
    )
    request = SendMessageRequest(
        id=str(uuid4()),
        method="message/send",
        jsonrpc="2.0",
        params=MessageSendParams(
            message=to_sdk_message(message_data),
            configuration=MessageSendConfiguration(
                push_notification_config=push_config,
                blocking=blocking,
            ),
        ),
    )
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await A2AClient(httpx_client=client, url=agent_url).send_message(
            request
        )
    return _normalize_response(response)


def _normalize_response(response: Any) -> dict[str, Any]:
    if not response:
        return {"kind": "error", "error": "No response", "result": None}
    if isinstance(response.root, JSONRPCErrorResponse):
        return {
            "kind": "error",
            "error": response.root.error.model_dump(mode="json"),
            "result": None,
        }
    result = response.root.result
    return {
        "kind": result.kind,
        "result": result.model_dump(mode="json"),
        "error": None,
    }


def _model_data(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True)
    return {}


__all__ = [
    "A2AClientFacadeError",
    "cancel_remote_task",
    "fetch_agent_card_with_fallback",
    "send_hitl_reply",
    "send_message",
    "stream_message",
]
