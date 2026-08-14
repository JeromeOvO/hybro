import json
from typing import Any

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
