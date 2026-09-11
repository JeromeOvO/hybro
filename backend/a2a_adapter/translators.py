import json
from typing import Any

from common.dto import (
    AgentCardInterface,
    AgentCardSnapshot,
    AgentStreamEvent,
    AgentTaskResult,
    InternalAgentMessage,
)
from common.types import Artifact, Message, Part, Task, TaskState, TaskStatus

from .message_factory import from_sdk_message, to_internal_part
from .webhook_payloads import (
    task_from_artifact_update,
    task_from_status_update,
    task_from_task_frame,
)

TERMINAL_STATES = {"completed", "failed", "canceled", "cancelled", "rejected"}
PLATFORM_SUPPORTED_MODES = {
    "text/plain",
    "text/markdown",
    "text/html",
    "text/csv",
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "audio/wav",
    "audio/mpeg",
    "audio/mp4",
    "audio/webm",
    "video/mp4",
    "video/webm",
    "application/json",
    "application/pdf",
    "application/xml",
    "application/zip",
}
MODE_TO_MIMES: dict[str, set[str]] = {
    "text": {"text/plain"},
    "image": {"image/png", "image/jpeg", "image/gif", "image/webp"},
    "audio": {"audio/wav", "audio/mpeg", "audio/mp4", "audio/webm"},
    "video": {"video/mp4", "video/webm"},
    "json": {"application/json"},
    "form": {"text/plain"},
    "markdown": {"text/markdown", "text/plain"},
}


def internal_message_to_a2a(msg: InternalAgentMessage) -> dict[str, Any]:
    return {
        "role": msg.role,
        "parts": _jsonable(msg.parts),
        "metadata": {"agent_id": msg.agent_id, **_jsonable(msg.metadata)},
    }


def a2a_task_to_result(task_data: dict[str, Any], agent_id: str) -> AgentTaskResult:
    task_source = _jsonrpc_result(task_data) or task_data
    status_data = _read(task_source, "status")
    status = _normalize_status(status_data)
    error = _extract_error_text(
        _read(task_source, "error") or _read(task_data, "error")
    )
    if error is None and status in {"failed", "canceled", "cancelled"}:
        error = _stringify_message(_read(status_data, "message"))

    result: dict[str, Any] = {"raw": task_data}
    for key in ("artifacts", "message", "result"):
        value = _read(task_source, key)
        if value is not None:
            result[key] = value

    return AgentTaskResult(
        task_id=_first_non_empty(
            _read(task_source, "id"),
            _read(task_source, "taskId"),
            _read(task_source, "task_id"),
            "",
        ),
        agent_id=agent_id,
        status=status,
        result=result,
        error=error,
    )


def a2a_event_to_stream_event(
    event_data: dict[str, Any],
    agent_id: str,
) -> AgentStreamEvent:
    event_source = _jsonrpc_result(event_data) or event_data
    nested_task = _read(event_source, "task") or {}
    status_data = _read(event_source, "status")
    status = _normalize_status(status_data)
    payload: dict[str, Any] = {"raw": event_data}
    for key in ("status", "artifact", "message"):
        value = _read(event_source, key)
        if value is not None:
            payload[key] = value

    is_error = _read(event_source, "error") is not None
    final = bool(_read(event_source, "final")) or status in TERMINAL_STATES or is_error

    return AgentStreamEvent(
        task_id=_first_non_empty(
            _read(event_source, "task_id"),
            _read(event_source, "taskId"),
            _read(event_source, "id"),
            _read(nested_task, "id"),
            "",
        ),
        agent_id=agent_id,
        event_type=_first_non_empty(
            _read(event_source, "event_type"),
            _read(event_source, "type"),
            _read(event_source, "kind"),
            "error" if is_error else "",
            "message",
        ),
        payload=payload,
        final=final,
    )


def a2a_card_to_snapshot(card: Any, agent_url: str) -> AgentCardSnapshot:
    raw_card = _to_raw_dict(card)
    capabilities_data = _read(card, "capabilities") or {}
    input_modes = _read(card, "default_input_modes", "defaultInputModes", "input_modes")
    output_modes = _read(
        card,
        "default_output_modes",
        "defaultOutputModes",
        "output_modes",
    )
    return AgentCardSnapshot(
        agent_id=_first_non_empty(
            _read(card, "id"),
            _read(card, "agent_id"),
            _read(card, "name"),
            agent_url,
        ),
        url=_first_non_empty(
            _read(card, "url"),
            _interface_url(_read(card, "supported_interfaces", "supportedInterfaces")),
            _interface_url(
                _read(card, "additional_interfaces", "additionalInterfaces")
            ),
            agent_url,
        ),
        name=_read(card, "name"),
        description=_read(card, "description"),
        capabilities=_normalize_capabilities(
            capabilities_data,
            input_modes or [],
            output_modes or [],
        ),
        raw_card=raw_card,
        interfaces=_card_interfaces(card, agent_url),
    )


def _interface_url(interfaces: Any) -> str | None:
    """First advertised endpoint URL, or None when no interface declares one."""
    for item in _as_sequence(interfaces):
        url = _read(item, "url")
        if url:
            return str(url)
    return None


def _card_interfaces(card: Any, agent_url: str) -> list[AgentCardInterface]:
    """Project the card's callable bindings without guessing endpoints.

    A 1.0 card advertises ``supportedInterfaces``; an older card advertises a
    top-level ``url`` plus optional ``preferredTransport`` and
    ``additionalInterfaces``. Both are read here so consumers select a binding
    from an explicit list instead of re-deriving one from raw protocol fields.
    """
    declared = _as_sequence(_read(card, "supported_interfaces", "supportedInterfaces"))
    interfaces: list[AgentCardInterface] = []
    for item in declared:
        url = _read(item, "url")
        if not url:
            continue
        interfaces.append(
            AgentCardInterface(
                url=str(url),
                protocol_binding=_read(item, "protocol_binding", "protocolBinding"),
                protocol_version=_read(item, "protocol_version", "protocolVersion"),
                tenant=_read(item, "tenant"),
            )
        )
    if interfaces:
        return interfaces

    for item in _as_sequence(
        _read(card, "additional_interfaces", "additionalInterfaces")
    ):
        url = _read(item, "url")
        if not url:
            continue
        interfaces.append(
            AgentCardInterface(
                url=str(url),
                protocol_binding=_read(item, "transport", "protocol_binding"),
                protocol_version=_read(item, "protocol_version", "protocolVersion"),
                tenant=_read(item, "tenant"),
            )
        )

    primary_url = _read(card, "url") or agent_url
    if primary_url:
        interfaces.append(
            AgentCardInterface(
                url=str(primary_url),
                protocol_binding=_read(
                    card, "preferred_transport", "preferredTransport"
                ),
                protocol_version=_read(card, "protocol_version", "protocolVersion"),
                tenant=_read(card, "tenant"),
            )
        )
    return interfaces


def _as_sequence(value: Any) -> list[Any]:
    """Read a repeated field as a list.

    Protobuf repeated fields are not Python lists, so ``isinstance(x, list)``
    would silently drop every 1.0 interface and capability extension.
    """
    if value is None or isinstance(value, (str, bytes, dict)):
        return []
    try:
        return list(value)
    except TypeError:
        return []


def resolve_accepted_output_modes(agent_card: Any) -> list[str]:
    """Intersect an agent's declared output modes with platform capabilities."""
    raw_modes = getattr(agent_card, "default_output_modes", None)
    agent_modes = set(raw_modes if raw_modes is not None else ["text"])
    if not agent_modes:
        agent_modes = {"text"}

    agent_mime_modes: set[str] = set()
    for mode in agent_modes:
        if "/" in mode:
            agent_mime_modes.add(mode)
        elif mode in MODE_TO_MIMES:
            agent_mime_modes.update(MODE_TO_MIMES[mode])
        else:
            agent_mime_modes.add("text/plain")

    accepted = agent_mime_modes & PLATFORM_SUPPORTED_MODES
    if not accepted:
        accepted = {"text/plain"}
    return sorted(accepted)


def coerce_parts(parts: list[Any] | None) -> list[Part]:
    """Map SDK parts, wire dicts, or internal parts onto internal parts."""
    return [to_internal_part(part) for part in parts or []]


def message_to_completed_task(
    message: Message,
    context_id: str,
    *,
    task_id: str,
    artifact_id: str,
) -> Task:
    """Convert a message response into a completed task artifact."""
    return Task(
        id=task_id,
        context_id=context_id,
        status=TaskStatus(state=TaskState.completed),
        artifacts=[
            Artifact(
                artifact_id=artifact_id,
                name="response",
                parts=coerce_parts(message.parts),
            )
        ],
    )


def facade_result_to_model(response: dict[str, Any]) -> Message | Task:
    """Convert a normalized facade response into an internal Message or Task.

    The facade yields one 1.0 ``StreamResponse`` frame per call, tagged with the
    member that was present (``task`` / ``message``) or, for streaming calls,
    the event kind (``status_update`` / ``artifact_update``). Frame parsing is
    shared with webhook ingestion so both paths agree on artifact semantics,
    status messages, and HITL metadata.
    """
    kind = response.get("kind")
    result = response.get("result") or {}
    if kind == "message":
        # A message frame *is* the message; there is no wrapping key. Callers
        # expect the internal Message here (they branch on its kind), so the
        # ProtoJSON body is mapped explicitly rather than re-validated.
        return from_sdk_message(result)
    if kind == "task":
        return task_from_task_frame(result)
    if kind == "status-update":
        return task_from_status_update(result, "")
    if kind == "artifact-update":
        return task_from_artifact_update(result)
    raise ValueError(str(response.get("error") or "Unknown A2A response"))


def _normalize_status(status_data: Any) -> str:
    state = _read(status_data, "state")
    if state is not None:
        return _string_value(state)
    if status_data is not None:
        return _string_value(status_data)
    return "unknown"


def _extract_error_text(error_data: Any) -> str | None:
    if error_data is None:
        return None
    message = _read(error_data, "message")
    if message is not None:
        return _stringify_message(message)
    return _stringify_message(error_data)


def _stringify_message(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    text = _read(value, "text")
    if text is not None:
        return _string_value(text)
    parts = _read(value, "parts")
    if isinstance(parts, list):
        texts = [_stringify_message(part) for part in parts]
        return "\n".join(text for text in texts if text)
    root = _read(value, "root")
    if root is not None:
        return _stringify_message(root)
    message = _read(value, "message")
    if message is not None:
        return _stringify_message(message)
    try:
        return json.dumps(_jsonable(value), sort_keys=True)
    except TypeError:
        return str(value)


def _normalize_capabilities(
    capabilities_data: Any,
    input_modes: list[Any],
    output_modes: list[Any],
) -> list[str]:
    capabilities: set[str] = set()
    if isinstance(capabilities_data, list):
        capabilities.update(_string_value(item) for item in capabilities_data)
    elif isinstance(capabilities_data, dict):
        capabilities.update(
            _string_value(item)
            for item in capabilities_data.get("capabilities", [])
            if item
        )
        capabilities.update(_extension_names(capabilities_data.get("extensions")))
    else:
        capabilities.update(_extension_names(_read(capabilities_data, "extensions")))

    for key in ("streaming", "stream"):
        if bool(_read(capabilities_data, key)):
            capabilities.add("streaming")
    for key in ("push_notifications", "pushNotifications", "push-notifications"):
        if bool(_read(capabilities_data, key)):
            capabilities.add("push_notifications")

    capabilities.update(f"input:{_string_value(mode)}" for mode in input_modes)
    capabilities.update(f"output:{_string_value(mode)}" for mode in output_modes)
    return sorted(cap for cap in capabilities if cap)


def _extension_names(extensions: Any) -> set[str]:
    """Named extensions declared by a card's capabilities."""
    names: set[str] = set()
    for extension in _as_sequence(extensions):
        name = _read(extension, "name") or _read(extension, "uri")
        if name:
            names.add(_string_value(name))
    return names


def _to_raw_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return _jsonable(value)
    to_dict = getattr(value, "model_dump", None)
    if callable(to_dict):
        try:
            return _jsonable(to_dict(mode="json", by_alias=True))
        except TypeError:
            return _jsonable(to_dict())
    if _is_protobuf_message(value):
        # 1.0 cards are protobuf; the wire JSON is its camelCase ProtoJSON form.
        from google.protobuf.json_format import MessageToDict

        return _jsonable(MessageToDict(value))
    if hasattr(value, "__dict__"):
        return _jsonable(vars(value))
    return {}


def _is_protobuf_message(value: Any) -> bool:
    return hasattr(value, "DESCRIPTOR") and hasattr(value, "ListFields")


def _read(value: Any, *names: str) -> Any:
    if value is None:
        return None
    for name in names:
        if isinstance(value, dict) and name in value:
            return value[name]
        if hasattr(value, name):
            return getattr(value, name)
    return None


def _jsonrpc_result(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    if "jsonrpc" not in value:
        return None
    result = value.get("result")
    return result if isinstance(result, dict) else None


def _first_non_empty(*values: Any) -> str:
    for value in values:
        if value is not None and value != "":
            return _string_value(value)
    return ""


def _string_value(value: Any) -> str:
    if hasattr(value, "value"):
        return str(value.value)
    return str(value)


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, set | frozenset):
        return sorted(_jsonable(item) for item in value)
    if hasattr(value, "model_dump"):
        try:
            return value.model_dump(mode="json", by_alias=True)
        except TypeError:
            return value.model_dump()
    return value


__all__ = [
    "a2a_card_to_snapshot",
    "a2a_event_to_stream_event",
    "a2a_task_to_result",
    "internal_message_to_a2a",
]
