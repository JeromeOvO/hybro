from typing import Any

JSON_OBJECT_INSTRUCTION = (
    "Return only valid JSON. Do not include markdown fences or explanatory text. "
    "Start with { and end with }."
)


def with_json_object_instruction(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    updated = [dict(message) for message in messages]
    if updated and updated[0].get("role") == "system":
        updated[0]["content"] = (
            f"{updated[0].get('content', '')}\n\n{JSON_OBJECT_INSTRUCTION}"
        )
    else:
        updated.insert(0, {"role": "system", "content": JSON_OBJECT_INSTRUCTION})
    return updated
