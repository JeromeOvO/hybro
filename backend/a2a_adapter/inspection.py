"""SDK-confined helpers for A2A inspection and dry-send probing."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import httpx
from a2a.client import A2ACardResolver, A2AClient
from a2a.client.errors import A2AClientHTTPError
from a2a.types import (
    AgentCard as SDKAgentCard,
)
from a2a.types import (
    JSONRPCErrorResponse,
    Message,
    MessageSendConfiguration,
    MessageSendParams,
    Role,
    SendMessageRequest,
    SendStreamingMessageRequest,
    TextPart,
)

from common.types import AgentCard

from .constants import AGENT_CARD_WELL_KNOWN_PATH, PREV_AGENT_CARD_WELL_KNOWN_PATH
from .docker_host_fallback import (
    stream_with_docker_host_fallback,
    with_docker_host_fallback,
)


async def fetch_agent_card_for_inspection(
    agent_url: str,
    *,
    timeout: float = 30.0,
) -> AgentCard:
    """Fetch an agent card for inspection and return the internal card model."""
    async with httpx.AsyncClient(timeout=timeout) as client:
        sdk_card = await _fetch_sdk_agent_card_with_fallback(client, agent_url)
    return _to_internal_card(sdk_card)


async def inspect_a2a_connection(
    agent_url: str,
    probe_text: str = "Hello, how are you?",
    *,
    timeout: float = 600.0,
) -> dict[str, Any]:
    """Fetch an agent card, dry-send a probe message, and return SDK-free data."""
    async with httpx.AsyncClient(timeout=timeout) as client:
        sdk_card = await _fetch_sdk_agent_card_with_fallback(client, agent_url)
        response = await _dry_send_message(client, sdk_card, probe_text)

    internal_card = _to_internal_card(sdk_card)
    return {
        "agent_card": internal_card,
        "result": response["result"],
        "status_code": response["status_code"],
    }


async def _fetch_sdk_agent_card_with_fallback(
    client: httpx.AsyncClient,
    agent_url: str,
) -> SDKAgentCard:
    resolver = A2ACardResolver(client, str(agent_url), AGENT_CARD_WELL_KNOWN_PATH)
    try:
        return await resolver.get_agent_card()
    except A2AClientHTTPError as exc:
        if exc.status_code != 404:
            raise
    fallback = A2ACardResolver(client, str(agent_url), PREV_AGENT_CARD_WELL_KNOWN_PATH)
    return await fallback.get_agent_card()


async def _dry_send_message(
    client: httpx.AsyncClient,
    card: SDKAgentCard,
    message_text: str,
) -> dict[str, Any]:
    message = Message(
        role=Role.user,
        parts=[TextPart(text=str(message_text))],
        message_id=str(uuid4()),
        context_id=str(uuid4()),
    )
    payload = MessageSendParams(
        message=message,
        configuration=MessageSendConfiguration(
            accepted_output_modes=_resolve_accepted_modes(card)
        ),
    )
    try:
        if getattr(card.capabilities, "streaming", False) is True:
            request = SendStreamingMessageRequest(
                id=str(uuid4()),
                method="message/stream",
                jsonrpc="2.0",
                params=payload,
            )
            last_result: dict[str, Any] | None = None
            async for stream_result in stream_with_docker_host_fallback(
                card,
                lambda candidate: A2AClient(
                    client,
                    agent_card=candidate,
                ).send_message_streaming(request),
            ):
                last_result = _validate_response(stream_result)
            return last_result or {
                "result": ["Response from agent is missing required 'kind' field."],
                "status_code": 500,
            }

        request = SendMessageRequest(
            id=str(uuid4()),
            method="message/send",
            jsonrpc="2.0",
            params=payload,
        )
        response = await with_docker_host_fallback(
            card,
            lambda candidate: A2AClient(client, agent_card=candidate).send_message(
                request
            ),
        )
        return _validate_response(response)
    except Exception as exc:
        return {"result": [f"Failed to send message: {exc}"], "status_code": 500}


def _validate_response(result: Any) -> dict[str, Any]:
    if isinstance(result.root, JSONRPCErrorResponse):
        error_data = result.root.error.model_dump(exclude_none=True)
        return {"result": [str(error_data)], "status_code": 500}

    response_data = result.root.result.model_dump(exclude_none=True)
    validation_errors = validate_message_data(response_data)
    return {"result": validation_errors, "status_code": 500 if validation_errors else 200}


def validate_response_data(result: dict[str, Any]) -> tuple[list[str], bool]:
    """Validate an SDK-free adapter response payload.

    Returns ``(errors, is_transport_error)`` so former compatibility callers
    can preserve their existing response model/status behavior.
    """
    if result.get("kind") == "error":
        return [str(result.get("error"))], True
    response_data = result.get("result") or {}
    return validate_message_data(response_data), False


def validate_message_data(data: dict[str, Any]) -> list[str]:
    """Validate an incoming SDK-free A2A message payload by kind."""
    return _validate_message(data)


def _validate_message(data: dict[str, Any]) -> list[str]:
    if "kind" not in data:
        return ["Response from agent is missing required 'kind' field."]

    kind = data.get("kind")
    validators = {
        "task": _validate_task,
        "status-update": _validate_status_update,
        "artifact-update": _validate_artifact_update,
        "message": _validate_agent_message,
    }
    validator = validators.get(str(kind))
    if validator:
        return validator(data)
    return [f"Unknown message kind received: '{kind}'."]


def _validate_task(data: dict[str, Any]) -> list[str]:
    errors = []
    if "id" not in data:
        errors.append("Task object missing required field: 'id'.")
    if "status" not in data or "state" not in data.get("status", {}):
        errors.append("Task object missing required field: 'status.state'.")
    return errors


def _validate_status_update(data: dict[str, Any]) -> list[str]:
    if "status" not in data or "state" not in data.get("status", {}):
        return ["StatusUpdate object missing required field: 'status.state'."]
    return []


def _validate_artifact_update(data: dict[str, Any]) -> list[str]:
    errors = []
    if "artifact" not in data:
        errors.append("ArtifactUpdate object missing required field: 'artifact'.")
    elif (
        "parts" not in data.get("artifact", {})
        or not isinstance(data.get("artifact", {}).get("parts"), list)
        or not data.get("artifact", {}).get("parts")
    ):
        errors.append("Artifact object must have a non-empty 'parts' array.")
    return errors


def _validate_agent_message(data: dict[str, Any]) -> list[str]:
    errors = []
    if (
        "parts" not in data
        or not isinstance(data.get("parts"), list)
        or not data.get("parts")
    ):
        errors.append("Message object must have a non-empty 'parts' array.")
    if "role" not in data or data.get("role") != "agent":
        errors.append("Message from agent must have 'role' set to 'agent'.")
    return errors


def _resolve_accepted_modes(card: SDKAgentCard) -> list[str]:
    modes = getattr(card, "default_output_modes", None) or getattr(
        card, "defaultOutputModes", None
    )
    return list(modes or ["text/plain"])


def _to_internal_card(card: SDKAgentCard) -> AgentCard:
    return AgentCard.model_validate(card.model_dump(mode="json"))


__all__ = [
    "fetch_agent_card_for_inspection",
    "inspect_a2a_connection",
    "validate_message_data",
    "validate_response_data",
]
