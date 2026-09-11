"""SDK-confined helpers for A2A inspection and dry-send probing."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from common.types import AgentCard

from .client_facade import fetch_agent_card_with_fallback, send_message, stream_message
from .translators import a2a_card_to_snapshot

_STATUS_OK = 200
_STATUS_PROBE_FAILED = 500


async def fetch_agent_card_for_inspection(
    agent_url: str,
    *,
    timeout: float = 30.0,
) -> AgentCard:
    """Fetch an agent card for inspection and return the internal card model."""
    payload = await fetch_agent_card_with_fallback(agent_url, timeout=timeout)
    return _to_internal_card(payload, agent_url)


async def inspect_a2a_connection(
    agent_url: str,
    probe_text: str = "Hello, how are you?",
    *,
    timeout: float = 600.0,
) -> dict[str, Any]:
    """Fetch an agent card, dry-send a probe message, and return SDK-free data."""
    card_data = await fetch_agent_card_with_fallback(agent_url, timeout=timeout)
    internal_card = _to_internal_card(card_data, agent_url)
    message = {
        "role": "user",
        "message_id": uuid4().hex,
        "context_id": uuid4().hex,
        "parts": [{"kind": "text", "text": str(probe_text)}],
    }

    try:
        response = await _probe(card_data, message, timeout=timeout)
    except Exception as exc:
        return {
            "agent_card": internal_card,
            "result": [f"Failed to send message: {exc}"],
            "status_code": _STATUS_PROBE_FAILED,
        }

    if response is None:
        return {
            "agent_card": internal_card,
            "result": ["Response from agent is missing required 'kind' field."],
            "status_code": _STATUS_PROBE_FAILED,
        }

    errors, is_transport_error = validate_response_data(response)
    failed = is_transport_error or bool(errors)
    return {
        "agent_card": internal_card,
        "result": errors,
        "status_code": _STATUS_PROBE_FAILED if failed else _STATUS_OK,
    }


async def _probe(
    card_data: dict[str, Any],
    message: dict[str, Any],
    *,
    timeout: float,
) -> dict[str, Any] | None:
    """Send the probe over whichever transport the agent advertises."""
    accepted_modes = _resolve_accepted_modes(card_data)
    if _capability(card_data, "streaming"):
        last_frame: dict[str, Any] | None = None
        async for frame in stream_message(
            card_data, message, accepted_output_modes=accepted_modes, timeout=timeout
        ):
            last_frame = frame
        return last_frame
    return await send_message(
        card_data,
        message,
        accepted_output_modes=accepted_modes,
        blocking=True,
        timeout=timeout,
    )


def _to_internal_card(card_data: Any, agent_url: str) -> AgentCard:
    """Project fetched card data onto the internal card model."""
    return AgentCard.model_validate(a2a_card_to_snapshot(card_data, agent_url).raw_card)


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
    """Validate an incoming SDK-free A2A message payload by event kind."""
    if not isinstance(data, dict) or not data:
        return ["Response from agent is missing required 'kind' field."]

    kind = _event_kind(data)
    if kind is None:
        return ["Response from agent is missing required 'kind' field."]

    validators = {
        "task": _validate_task,
        "status-update": _validate_status_update,
        "artifact-update": _validate_artifact_update,
        "message": _validate_agent_message,
    }
    validator = validators.get(kind)
    if validator:
        return validator(data)
    return [f"Unknown message kind received: '{kind}'."]


def _event_kind(data: dict[str, Any]) -> str | None:
    """Identify a response payload by its member, or by a legacy ``kind``."""
    for member, kind in (
        ("task", "task"),
        ("statusUpdate", "status-update"),
        ("artifactUpdate", "artifact-update"),
        ("message", "message"),
    ):
        if member in data:
            return kind
    raw_kind = data.get("kind")
    if isinstance(raw_kind, str) and raw_kind.strip():
        return raw_kind.strip().replace("_", "-").lower()
    return None


def _validate_task(data: dict[str, Any]) -> list[str]:
    task = data.get("task") if isinstance(data.get("task"), dict) else data
    errors = []
    if not task.get("id"):
        errors.append("Task object missing required field: 'id'.")
    status = task.get("status")
    if not isinstance(status, dict) or not status.get("state"):
        errors.append("Task object missing required field: 'status.state'.")
    return errors


def _validate_status_update(data: dict[str, Any]) -> list[str]:
    event = (
        data.get("statusUpdate") if isinstance(data.get("statusUpdate"), dict) else data
    )
    status = event.get("status")
    if not isinstance(status, dict) or not status.get("state"):
        return ["StatusUpdate object missing required field: 'status.state'."]
    return []


def _validate_artifact_update(data: dict[str, Any]) -> list[str]:
    event = (
        data.get("artifactUpdate")
        if isinstance(data.get("artifactUpdate"), dict)
        else data
    )
    artifact = event.get("artifact")
    if not isinstance(artifact, dict):
        return ["ArtifactUpdate object missing required field: 'artifact'."]
    parts = artifact.get("parts")
    if not isinstance(parts, list) or not parts:
        return ["Artifact object must have a non-empty 'parts' array."]
    return []


def _validate_agent_message(data: dict[str, Any]) -> list[str]:
    message = data.get("message") if isinstance(data.get("message"), dict) else data
    errors = []
    parts = message.get("parts")
    if not isinstance(parts, list) or not parts:
        errors.append("Message object must have a non-empty 'parts' array.")
    role = str(message.get("role") or "").upper().removeprefix("ROLE_")
    if role != "AGENT":
        errors.append("Message from agent must have 'role' set to 'agent'.")
    return errors


def _resolve_accepted_modes(card_data: dict[str, Any]) -> list[str]:
    modes = card_data.get("defaultOutputModes") or card_data.get("default_output_modes")
    return list(modes or ["text/plain"])


def _capability(card_data: dict[str, Any], name: str) -> Any:
    capabilities = card_data.get("capabilities")
    return capabilities.get(name) if isinstance(capabilities, dict) else None


__all__ = [
    "fetch_agent_card_for_inspection",
    "inspect_a2a_connection",
    "validate_message_data",
    "validate_response_data",
]
