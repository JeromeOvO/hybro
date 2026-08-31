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
    ToolInteractionMessage,
    ToolResultMessage,
    UserMessage,
)


class TranscriptCorruptionError(ValueError):
    pass


def agent_messages_to_model(
    messages: list[object],
    *,
    include_notices: bool = True,
    prepare_orchestration_context: bool = False,
) -> list[ModelMessage]:
    """Convert the durable transcript into provider messages.

    Durable messages remain lossless. The optional orchestration view only
    bounds already-resolved historical call plans and labels Tool results by
    provenance so the model cannot mistake its own prior arguments for facts.
    """
    result: list[ModelMessage] = []
    calls: set[str] = set()
    results: set[str] = set()
    resolved_call_ids = {
        message.call_id
        for message in messages
        if isinstance(message, (ToolResultMessage, ToolInteractionMessage))
    }
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
                arguments = call.arguments
                if prepare_orchestration_context and call.call_id in resolved_call_ids:
                    arguments = _historical_tool_arguments(arguments)
                parts.append(
                    ModelToolCallPart(
                        call_id=call.call_id,
                        tool_name=call.tool_name,
                        arguments=arguments,
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
            if prepare_orchestration_context:
                evidence = message.status == "completed" and not message.is_error
                provenance = (
                    "[agent observation: verified completed result; usable as evidence]"
                    if evidence
                    else (
                        f"[agent observation: status={message.status}; "
                        "diagnostic only, not evidence]"
                    )
                )
                text = f"{provenance}\n{text}".strip()
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
        elif isinstance(message, ToolInteractionMessage):
            if message.call_id not in calls:
                raise TranscriptCorruptionError("orphan tool interaction")
            result.append(
                ModelMessage(
                    role="tool",
                    content=[
                        ModelToolResultPart(
                            call_id=message.call_id,
                            tool_name=message.tool_name,
                            content=[ModelTextPart(text=_interaction_text(message))],
                            is_error=False,
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

    failed_surface_calls = {
        msg.content[0].call_id
        for msg in result
        if msg.role == "tool"
        and getattr(msg.content[0], "tool_name", None) == "surface_agent_questions"
        and getattr(msg.content[0], "is_error", False)
    }

    folded: list[ModelMessage] = []
    tool_blocks: dict[str, ModelToolResultPart] = {}

    for msg in result:
        if msg.role == "assistant":
            parts = []
            for part in msg.content:
                if getattr(part, "tool_name", None) == "surface_agent_questions":
                    if getattr(part, "call_id", None) not in failed_surface_calls:
                        continue
                parts.append(part)
            if not parts:
                continue
            folded.append(msg.model_copy(update={"content": parts}))

        elif msg.role == "tool":
            part = msg.content[0]
            if getattr(part, "tool_name", None) == "surface_agent_questions":
                if getattr(part, "call_id", None) not in failed_surface_calls:
                    continue

            if hasattr(part, "call_id") and part.call_id in tool_blocks:
                existing = tool_blocks[part.call_id]
                existing_text = existing.content[0].text
                new_text = part.content[0].text
                updated = existing.model_copy(
                    update={
                        "content": [
                            ModelTextPart(text=f"{existing_text}\n\n{new_text}")
                        ],
                        "is_error": existing.is_error or part.is_error,
                    }
                )
                tool_blocks[part.call_id] = updated
                for i in range(len(folded) - 1, -1, -1):
                    if (
                        folded[i].role == "tool"
                        and getattr(folded[i].content[0], "call_id", None)
                        == part.call_id
                    ):
                        folded[i] = folded[i].model_copy(update={"content": [updated]})
                        break
            else:
                if hasattr(part, "call_id"):
                    tool_blocks[part.call_id] = part  # type: ignore
                folded.append(msg)
        else:
            folded.append(msg)

    return folded


_HISTORICAL_TASK_MAX_CHARS = 2_400
_HISTORICAL_COLLECTION_MAX_ITEMS = 20


def _historical_tool_arguments(arguments: dict[str, object]) -> dict[str, object]:
    """Build a bounded private context view for an already-resolved call."""

    bounded: dict[str, object] = {}
    for key, value in arguments.items():
        if key == "task" and isinstance(value, str):
            if len(value) <= _HISTORICAL_TASK_MAX_CHARS:
                bounded[key] = f"[historical plan, not evidence]\n{value}"
            else:
                head = value[:1_800].rstrip()
                tail = value[-400:].lstrip()
                bounded[key] = (
                    "[historical plan, not evidence; middle omitted]\n"
                    f"{head}\n…\n{tail}"
                )
            continue
        if isinstance(value, list):
            bounded[key] = value[:_HISTORICAL_COLLECTION_MAX_ITEMS]
            continue
        if isinstance(value, dict):
            serialized = json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
            bounded[key] = (
                value
                if len(serialized) <= 1_000
                else "[historical structured argument omitted]"
            )
            continue
        bounded[key] = value
    return bounded


def unresolved_call_ids(messages: list[object]) -> set[str]:
    calls: set[str] = set()
    results: set[str] = set()
    for message in messages:
        if isinstance(message, AssistantMessage):
            calls.update(call.call_id for call in message.tool_calls)
        elif isinstance(message, (ToolResultMessage, ToolInteractionMessage)):
            results.add(message.call_id)
    return calls - results


def _interaction_text(message: ToolInteractionMessage) -> str:
    """Render an executable, provider-neutral private interaction observation.

    The model must receive the exact platform presentation target and typed
    question inventory. Public projections never use this transcript text.
    """

    payload = {
        "presentation_id": message.presentation_id,
        "interaction_id": message.interaction_id,
        "interaction_fingerprint": message.interaction_fingerprint,
        "questions": [
            {
                "question_id": question.question_id,
                "interaction_kind": question.interaction_kind,
                "answer_kind": question.answer_kind,
                "required": question.required,
                "prompt": question.prompt,
                "choices": question.choices,
            }
            for question in message.questions
        ],
        "artifact_refs": list(message.artifact_refs),
    }
    return (
        "[agent input request; answer it from existing evidence, or ask the user]\n"
        + json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )


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
