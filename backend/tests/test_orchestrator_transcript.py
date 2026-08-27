from __future__ import annotations

import pytest

from execution.orchestrator.models import (
    ArtifactRefPart,
    AssistantMessage,
    DataPart,
    SessionNotice,
    TextPart,
    ToolCall,
    ToolResultMessage,
    UsageRecord,
)
from execution.orchestrator.transcript import (
    TranscriptCorruptionError,
    agent_messages_to_model,
    unresolved_call_ids,
)
from tests._orchestrator_helpers import NOW, user_message


def assistant(call_id="call-1"):
    return AssistantMessage(
        message_id="assistant-1",
        content=[TextPart(text="calling")],
        tool_calls=[
            ToolCall(call_id=call_id, tool_name="echo", arguments={"value": 1})
        ],
        finish_reason="tool_calls",
        usage=UsageRecord(input_tokens=2, output_tokens=3),
        created_at=NOW,
    )


def result(call_id="call-1"):
    return ToolResultMessage(
        message_id="result-1",
        call_id=call_id,
        tool_name="echo",
        status="completed",
        content=[
            DataPart(data={"b": 2, "a": 1}),
            ArtifactRefPart(artifact_ref="artifact-1"),
        ],
        artifact_refs=["artifact-1"],
        is_error=False,
        created_at=NOW,
    )


def test_lossless_conversion_preserves_complete_tool_pairs_and_notices():
    notice = SessionNotice(
        notice_id="notice-1", code="wrap_up", content="finish", created_at=NOW
    )
    converted = agent_messages_to_model([user_message(), assistant(), result(), notice])
    assert [message.role for message in converted] == [
        "user",
        "assistant",
        "tool",
        "user",
    ]
    assert converted[1].content[1].arguments == {"value": 1}
    assert converted[2].content[0].call_id == "call-1"
    assert converted[2].content[0].tool_name == "echo"
    assert '{"a":1,"b":2}' in converted[2].content[0].content[0].text
    # File-backed artifact references must stay visible to the model turn;
    # otherwise the kernel cannot see that a document is attached and keeps
    # re-dispatching the same Agent.
    assert "[artifact reference: artifact-1]" in converted[2].content[0].content[0].text
    assert converted[3].content[0].text == "[runtime:wrap_up] finish"
    assert unresolved_call_ids([assistant(), result()]) == set()


def test_orchestration_context_bounds_resolved_plans_and_labels_result_provenance():
    long_task = "A" * 3_000 + "PRIVATE_MIDDLE" + "Z" * 3_000
    planned = assistant().model_copy(
        update={
            "tool_calls": [
                ToolCall(
                    call_id="call-1",
                    tool_name="echo",
                    arguments={"task": long_task, "attachment_refs": ["file-1"]},
                )
            ]
        }
    )

    converted = agent_messages_to_model(
        [planned, result()], prepare_orchestration_context=True
    )

    arguments = converted[0].content[1].arguments
    assert arguments["task"].startswith(
        "[historical plan, not evidence; middle omitted]"
    )
    assert "PRIVATE_MIDDLE" not in arguments["task"]
    assert len(arguments["task"]) < 2_400
    assert arguments["attachment_refs"] == ["file-1"]
    observation = converted[1].content[0].content[0].text
    assert observation.startswith(
        "[agent observation: verified completed result; usable as evidence]"
    )
    # Context preparation never mutates the durable transcript.
    assert planned.tool_calls[0].arguments["task"] == long_task


def test_orchestration_context_marks_failed_results_as_diagnostic_only():
    failed = result().model_copy(
        update={"status": "failed", "is_error": True, "error_message": "timeout"}
    )

    converted = agent_messages_to_model(
        [assistant(), failed], prepare_orchestration_context=True
    )

    assert (
        converted[1]
        .content[0]
        .content[0]
        .text.startswith(
            "[agent observation: status=failed; diagnostic only, not evidence]"
        )
    )


def test_unresolved_calls_are_reported_and_orphan_results_rejected():
    assert unresolved_call_ids([assistant()]) == {"call-1"}
    with pytest.raises(TranscriptCorruptionError, match="orphan"):
        agent_messages_to_model([result()])


def test_duplicate_calls_and_results_are_rejected():
    with pytest.raises(TranscriptCorruptionError, match="duplicate assistant"):
        agent_messages_to_model([assistant(), assistant()])
    with pytest.raises(TranscriptCorruptionError, match="duplicate tool result"):
        agent_messages_to_model([assistant(), result(), result()])
