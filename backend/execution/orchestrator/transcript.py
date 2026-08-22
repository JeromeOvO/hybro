"""Lossless, provider-neutral transcript conversion."""

from __future__ import annotations

import json

from .models import (
    ArtifactRefPart,
    AssistantMessage,
    DataPart,
    ModelMessage,
    ModelTextPart,
    ModelToolCallPart,
    ModelToolResultPart,
    SessionNotice,
    TextPart,
    ToolResultMessage,
    UserMessage,
)


class TranscriptCorruptionError(ValueError):
    pass


def agent_messages_to_model(
    messages: list[object],
    *,
    include_notices: bool = True,
) -> list[ModelMessage]:
    result: list[ModelMessage] = []
    calls: set[str] = set()
    results: set[str] = set()
    for message in messages:
        if isinstance(message, UserMessage):
            result.append(
                ModelMessage(
                    role="user",
                    content=[ModelTextPart(text=_content_text(message.content))],
                )
            )
        elif isinstance(message, AssistantMessage):
            parts: list[ModelTextPart | ModelToolCallPart] = []
            text = _content_text(message.content)
            if text:
                parts.append(ModelTextPart(text=text))
            for call in message.tool_calls:
                if call.call_id in calls:
                    raise TranscriptCorruptionError("duplicate assistant tool call")
                calls.add(call.call_id)
                parts.append(
                    ModelToolCallPart(
                        call_id=call.call_id,
                        tool_name=call.tool_name,
                        arguments=call.arguments,
                    )
                )
            result.append(ModelMessage(role="assistant", content=parts))
        elif isinstance(message, ToolResultMessage):
            if message.call_id not in calls:
                raise TranscriptCorruptionError("orphan tool result")
            if message.call_id in results:
                raise TranscriptCorruptionError("duplicate tool result")
            results.add(message.call_id)
            text = _content_text(message.content)
            refs = "\n".join(
                f"[artifact reference: {ref}]" for ref in message.artifact_refs
            )
            if refs:
                text = f"{text}\n{refs}".strip()
            if message.error_message:
                text = f"{text}\n{message.error_message}".strip()
            result.append(
                ModelMessage(
                    role="tool",
                    content=[
                        ModelToolResultPart(
                            call_id=message.call_id,
                            tool_name=message.tool_name,
                            content=[ModelTextPart(text=text)],
                            is_error=message.is_error,
                        )
                    ],
                )
            )
        elif isinstance(message, SessionNotice):
            if include_notices:
                result.append(
                    ModelMessage(
                        role="user",
                        content=[
                            ModelTextPart(
                                text=f"[runtime:{message.code}] {message.content}"
                            )
                        ],
                    )
                )
        else:
            raise TranscriptCorruptionError(
                f"unsupported transcript message {type(message).__name__}"
            )
    return result


def unresolved_call_ids(messages: list[object]) -> set[str]:
    calls: set[str] = set()
    results: set[str] = set()
    for message in messages:
        if isinstance(message, AssistantMessage):
            calls.update(call.call_id for call in message.tool_calls)
        elif isinstance(message, ToolResultMessage):
            results.add(message.call_id)
    return calls - results


def _content_text(parts: list[object]) -> str:
    rendered: list[str] = []
    for part in parts:
        if isinstance(part, TextPart):
            rendered.append(part.text)
        elif isinstance(part, DataPart):
            rendered.append(
                json.dumps(
                    part.data,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
        elif isinstance(part, ArtifactRefPart):
            rendered.append(
                f"[artifact reference: {part.artifact_ref}"
                f"{f' ({part.mime_type})' if part.mime_type else ''}]"
            )
    return "\n".join(item for item in rendered if item)


__all__ = [
    "TranscriptCorruptionError",
    "agent_messages_to_model",
    "unresolved_call_ids",
]
