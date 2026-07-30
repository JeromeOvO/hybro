"""High-level SDK facade for A2A client operations."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator
from contextlib import aclosing
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

from common.observability import get_logger, safe_exception_metadata

from .bounded_http import bounded_client, event_bounded_client
from .card_data import sdk_agent_card_data
from .constants import AGENT_CARD_WELL_KNOWN_PATH, PREV_AGENT_CARD_WELL_KNOWN_PATH
from .docker_host_fallback import (
    stream_with_docker_host_fallback,
    with_docker_host_fallback,
    with_docker_host_url_fallback,
)
from .message_factory import to_sdk_message

logger = get_logger(__name__)


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
    async with bounded_client(timeout=timeout) as client:
        return await with_docker_host_url_fallback(
            str(agent_url),
            lambda candidate_url: _fetch_agent_card_from_url(client, candidate_url),
        )
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
    started_at = time.perf_counter()
    card: AgentCard | None = None
    try:
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
        async with bounded_client(timeout=timeout) as client:
            response = await with_docker_host_fallback(
                card,
                lambda candidate: A2AClient(
                    client,
                    agent_card=candidate,
                ).send_message(request),
            )
        normalized = _normalize_response(response)
    except asyncio.CancelledError as exc:
        _log_a2a_completed(
            card=card,
            operation="message_send",
            started_at=started_at,
            outcome="cancelled",
            error=exc,
        )
        raise
    except Exception as exc:
        _log_a2a_completed(
            card=card,
            operation="message_send",
            started_at=started_at,
            outcome="error",
            error=exc,
        )
        raise
    outcome, error_type, error_code = _normalized_response_outcome(normalized)
    _log_a2a_completed(
        card=card,
        operation="message_send",
        started_at=started_at,
        outcome=outcome,
        error_type=error_type,
        error_code=error_code,
    )
    return normalized


async def stream_message(
    agent_card_data: Any,
    message_data: Any,
    *,
    accepted_output_modes: list[str] | None = None,
    timeout: float = 600.0,
) -> AsyncGenerator[dict[str, Any], None]:
    started_at = time.perf_counter()
    card: AgentCard | None = None
    outcome = "success"
    response_error_type: str | None = None
    response_error_code: Any = None
    try:
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
        async with event_bounded_client(timeout=timeout) as client:
            async with aclosing(
                stream_with_docker_host_fallback(
                    card,
                    lambda candidate: A2AClient(
                        client,
                        agent_card=candidate,
                    ).send_message_streaming(request),
                )
            ) as response_stream:
                async for response in response_stream:
                    normalized = _normalize_response(response)
                    (
                        response_outcome,
                        current_error_type,
                        current_error_code,
                    ) = _normalized_response_outcome(normalized)
                    if response_outcome == "error":
                        outcome = "error"
                        response_error_type = current_error_type
                        response_error_code = current_error_code
                    yield normalized
    except asyncio.CancelledError as exc:
        _log_a2a_completed(
            card=card,
            operation="message_stream",
            started_at=started_at,
            outcome="cancelled",
            error=exc,
        )
        raise
    except GeneratorExit as exc:
        _log_a2a_completed(
            card=card,
            operation="message_stream",
            started_at=started_at,
            outcome="cancelled",
            error=exc,
        )
        raise
    except Exception as exc:
        _log_a2a_completed(
            card=card,
            operation="message_stream",
            started_at=started_at,
            outcome="error",
            error=exc,
        )
        raise
    else:
        _log_a2a_completed(
            card=card,
            operation="message_stream",
            started_at=started_at,
            outcome=outcome,
            error_type=response_error_type,
            error_code=response_error_code,
        )


async def cancel_remote_task(
    agent_card_data: Any,
    task_id: str,
    *,
    timeout: float = 5.0,
) -> bool:
    started_at = time.perf_counter()
    card: AgentCard | None = None
    try:
        card = AgentCard(**sdk_agent_card_data(agent_card_data))
        async with bounded_client(timeout=timeout) as client:
            request = CancelTaskRequest(
                id=str(uuid4()), params=TaskIdParams(id=task_id)
            )
            response = await with_docker_host_fallback(
                card,
                lambda candidate: A2AClient(client, agent_card=candidate).cancel_task(
                    request
                ),
            )
        succeeded = not isinstance(response.root, JSONRPCErrorResponse)
        _log_a2a_completed(
            card=card,
            operation="task_cancel",
            started_at=started_at,
            outcome="success" if succeeded else "error",
            error_type=None if succeeded else "A2AJSONRPCError",
            error_code=(
                None if succeeded else getattr(response.root.error, "code", None)
            ),
        )
        return succeeded
    except asyncio.CancelledError as exc:
        _log_a2a_completed(
            card=card,
            operation="task_cancel",
            started_at=started_at,
            outcome="cancelled",
            error=exc,
        )
        raise
    except Exception as exc:
        _log_a2a_completed(
            card=card,
            operation="task_cancel",
            started_at=started_at,
            outcome="error",
            error=exc,
        )
        return False


async def send_hitl_reply(
    agent_url: str,
    message_data: Any,
    *,
    agent_id: str | None = None,
    push_notification_config: dict[str, Any] | None = None,
    blocking: bool = True,
    timeout: float = 600.0,
) -> dict[str, Any]:
    started_at = time.perf_counter()
    try:
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
        async with bounded_client(timeout=timeout) as client:
            response = await with_docker_host_url_fallback(
                agent_url,
                lambda candidate_url: A2AClient(
                    httpx_client=client,
                    url=candidate_url,
                ).send_message(request),
            )
        normalized = _normalize_response(response)
    except asyncio.CancelledError as exc:
        _log_a2a_completed(
            card=None,
            agent=agent_id,
            operation="hitl_reply",
            started_at=started_at,
            outcome="cancelled",
            error=exc,
        )
        raise
    except Exception as exc:
        _log_a2a_completed(
            card=None,
            agent=agent_id,
            operation="hitl_reply",
            started_at=started_at,
            outcome="error",
            error=exc,
        )
        raise
    outcome, error_type, error_code = _normalized_response_outcome(normalized)
    _log_a2a_completed(
        card=None,
        agent=agent_id,
        operation="hitl_reply",
        started_at=started_at,
        outcome=outcome,
        error_type=error_type,
        error_code=error_code,
    )
    return normalized


async def _fetch_agent_card_from_url(
    client: httpx.AsyncClient,
    agent_url: str,
) -> dict[str, Any]:
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


def _normalized_response_outcome(
    response: dict[str, Any],
) -> tuple[str, str | None, Any]:
    if response.get("kind") != "error":
        return "success", None, None
    error = response.get("error")
    error_code = error.get("code") if isinstance(error, dict) else None
    error_type = "A2AJSONRPCError" if isinstance(error, dict) else "A2AResponseError"
    return "error", error_type, error_code


def _model_data(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True)
    return {}


def _log_a2a_completed(
    *,
    card: AgentCard | None,
    agent: str | None = None,
    operation: str,
    started_at: float,
    outcome: str,
    error: BaseException | None = None,
    error_type: str | None = None,
    error_code: Any = None,
) -> None:
    fields: dict[str, Any] = {
        "agent": agent or (card.name if card is not None else None),
        "operation": operation,
        "attempt": 1,
        "outcome": outcome,
        "duration_ms": round((time.perf_counter() - started_at) * 1000, 3),
    }
    if error is not None:
        fields.update(safe_exception_metadata(error))
        fields["error_code"] = (
            error_code
            if error_code is not None
            else getattr(error, "status_code", None)
        )
    elif outcome == "error":
        fields["error_type"] = error_type or "A2AResponseError"
        fields["error_code"] = error_code
    log_method = logger.error if outcome == "error" else logger.info
    log_method("agent_call_completed", extra=fields)


__all__ = [
    "A2AClientFacadeError",
    "cancel_remote_task",
    "fetch_agent_card_with_fallback",
    "send_hitl_reply",
    "send_message",
    "stream_message",
]
