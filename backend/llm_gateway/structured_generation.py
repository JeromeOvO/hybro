import json
from hashlib import sha256
from typing import Any

from .turn_types import GatewayToolDefinition, GatewayTurnEvent


class StructuredActionError(ValueError):
    """Raised when locally validated model action output is not executable."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


JSON_OBJECT_INSTRUCTION = (
    "Return only valid JSON. Do not include markdown fences or explanatory text. "
    "Start with { and end with }."
)


def with_json_object_instruction(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return _with_system_instruction(messages, JSON_OBJECT_INSTRUCTION)


def with_json_schema_instruction(
    messages: list[dict[str, Any]],
    schema: dict[str, Any],
) -> list[dict[str, Any]]:
    instruction = (
        f"{JSON_OBJECT_INSTRUCTION}\n"
        "The JSON object must conform to this schema:\n"
        f"{json.dumps(schema, ensure_ascii=False, separators=(',', ':'))}"
    )
    return _with_system_instruction(messages, instruction)


def structured_action_instruction(tools: list[GatewayToolDefinition]) -> str:
    tool_names = [tool.name for tool in tools]
    schema = {
        "oneOf": [
            {
                "type": "object",
                "properties": {
                    "action": {"const": "final"},
                    "content": {"type": "string"},
                },
                "required": ["action", "content"],
                "additionalProperties": False,
            },
            {
                "type": "object",
                "properties": {
                    "action": {"const": "tool_calls"},
                    "calls": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "properties": {
                                "tool_name": {"enum": tool_names},
                                "arguments": {"type": "object"},
                            },
                            "required": ["tool_name", "arguments"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["action", "calls"],
                "additionalProperties": False,
            },
        ]
    }
    return (
        f"{JSON_OBJECT_INSTRUCTION}\nReturn exactly one action matching this schema:\n"
        f"{json.dumps(schema, ensure_ascii=False, separators=(',', ':'))}"
    )


def parse_structured_action(
    raw: str,
    *,
    tools: list[GatewayToolDefinition],
    turn_id: str,
    provider_request_id: str | None = None,
) -> list[GatewayTurnEvent]:
    try:
        action = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise StructuredActionError(
            "structured action is not valid JSON", code="malformed_structured_action"
        ) from exc
    if not isinstance(action, dict):
        raise StructuredActionError(
            "structured action must be an object", code="invalid_structured_action"
        )
    if action.get("action") == "final":
        if set(action) != {"action", "content"} or not isinstance(
            action.get("content"), str
        ):
            raise StructuredActionError(
                "invalid final action", code="invalid_structured_action"
            )
        events = []
        if action["content"]:
            events.append(
                GatewayTurnEvent(
                    kind="text_delta",
                    delta=action["content"],
                    provider_request_id=provider_request_id,
                )
            )
        events.append(
            GatewayTurnEvent(
                kind="finish",
                finish_reason="stop",
                provider_request_id=provider_request_id,
            )
        )
        return events
    if action.get("action") != "tool_calls" or set(action) != {"action", "calls"}:
        raise StructuredActionError(
            "invalid structured action discriminator", code="invalid_structured_action"
        )
    calls = action.get("calls")
    if not isinstance(calls, list) or not calls:
        raise StructuredActionError(
            "tool action requires calls", code="invalid_structured_action"
        )
    events: list[GatewayTurnEvent] = []
    call_ids: set[str] = set()
    for index, call in enumerate(calls):
        if not isinstance(call, dict) or set(call) != {"tool_name", "arguments"}:
            raise StructuredActionError(
                "invalid structured tool call", code="invalid_structured_action"
            )
        name = call.get("tool_name")
        arguments = call.get("arguments")
        if not isinstance(name, str) or not name:
            raise StructuredActionError(
                "tool name must be a non-empty string", code="invalid_structured_action"
            )
        if not isinstance(arguments, dict):
            raise StructuredActionError(
                "tool arguments must be an object", code="invalid_tool_arguments"
            )
        call_id = (
            "call_" + sha256(f"{turn_id}:{index}:{name}".encode()).hexdigest()[:24]
        )
        if call_id in call_ids:
            raise StructuredActionError(
                "duplicate tool call", code="duplicate_tool_call"
            )
        call_ids.add(call_id)
        arguments_json = json.dumps(arguments, separators=(",", ":"), sort_keys=True)
        events.extend(
            [
                GatewayTurnEvent(
                    kind="tool_call_start",
                    tool_index=index,
                    call_id=call_id,
                    tool_name=name,
                    provider_request_id=provider_request_id,
                ),
                GatewayTurnEvent(
                    kind="tool_call_arguments_delta",
                    tool_index=index,
                    call_id=call_id,
                    delta=arguments_json,
                    provider_request_id=provider_request_id,
                ),
                GatewayTurnEvent(
                    kind="tool_call_end",
                    tool_index=index,
                    call_id=call_id,
                    provider_request_id=provider_request_id,
                ),
            ]
        )
    events.append(
        GatewayTurnEvent(
            kind="finish",
            finish_reason="tool_calls",
            provider_request_id=provider_request_id,
        )
    )
    return events


def _with_system_instruction(
    messages: list[dict[str, Any]],
    instruction: str,
) -> list[dict[str, Any]]:
    updated = [dict(message) for message in messages]
    if updated and updated[0].get("role") == "system":
        updated[0]["content"] = f"{updated[0].get('content', '')}\n\n{instruction}"
    else:
        updated.insert(0, {"role": "system", "content": instruction})
    return updated
