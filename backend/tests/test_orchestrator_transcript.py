from __future__ import annotations

import pytest

from execution.orchestrator.models import (
    ArtifactRefPart,
    AssistantMessage,
    DataPart,
    SessionNotice,
    TextPart,
    ToolCall,
    ToolInteractionMessage,
    ToolInteractionQuestion,
    ToolResultMessage,
    UsageRecord,
    UserMessage,
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


def test_tool_interaction_presents_then_resolves_with_single_result():
    interaction = ToolInteractionMessage(
        message_id="interaction:call-1:fp",
        call_id="call-1",
        tool_name="echo",
        presentation_id="prs_123",
        interaction_id="interaction-1",
        interaction_fingerprint="fp",
        questions=[
            ToolInteractionQuestion(
                question_id="q-1",
                interaction_kind="questionnaire",
                prompt="Which provider?",
                answer_kind="text",
                required=True,
            )
        ],
        created_at=NOW,
    )
    # A presented interaction closes the call for unresolved-call accounting
    # without consuming the call's single ToolResultMessage slot.
    assert unresolved_call_ids([assistant(), interaction]) == set()
    converted = agent_messages_to_model([assistant(), interaction])
    assert converted[1].role == "tool"
    assert converted[1].content[0].call_id == "call-1"
    observation = converted[1].content[0].content[0].text
    assert "agent input request" in observation
    assert '"presentation_id":"prs_123"' in observation
    assert '"interaction_id":"interaction-1"' in observation
    assert '"interaction_fingerprint":"fp"' in observation
    assert '"question_id":"q-1"' in observation
    assert '"interaction_kind":"questionnaire"' in observation
    assert '"answer_kind":"text"' in observation
    assert '"required":true' in observation
    # The final ToolResultMessage for the same call folds into the call's
    # single tool response message, keeping a valid assistant-tool structure.
    final = agent_messages_to_model([assistant(), interaction, result()])
    assert [message.role for message in final] == ["assistant", "tool"]
    final_text = final[1].content[0].content[0].text
    assert "agent input request" in final_text
    assert "artifact-1" in final_text


def test_multi_round_hitl_transcript_folding_strips_surface_questions_and_reconciles_turns():
    interaction1 = ToolInteractionMessage(
        message_id="interaction:call-1:fp1",
        call_id="call-1",
        tool_name="travel_planner",
        presentation_id="prs_1",
        interaction_id="interaction-1",
        interaction_fingerprint="fp1",
        questions=[
            ToolInteractionQuestion(
                question_id="q-1",
                interaction_kind="questionnaire",
                prompt="Destination?",
                answer_kind="text",
                required=True,
            )
        ],
        created_at=NOW,
    )
    surface_call = AssistantMessage(
        message_id="assistant-surface",
        content=[],
        tool_calls=[
            ToolCall(
                call_id="call-surface",
                tool_name="surface_agent_questions",
                arguments={},
            )
        ],
        finish_reason="tool_calls",
        usage=None,
        created_at=NOW,
    )
    surface_result = ToolResultMessage(
        message_id="result-surface",
        call_id="call-surface",
        tool_name="surface_agent_questions",
        status="completed",
        content=[TextPart(text="Answers applied")],
        artifact_refs=[],
        is_error=False,
        created_at=NOW,
    )
    interaction2 = ToolInteractionMessage(
        message_id="interaction:call-1:fp2",
        call_id="call-1",
        tool_name="travel_planner",
        presentation_id="prs_2",
        interaction_id="interaction-2",
        interaction_fingerprint="fp2",
        questions=[
            ToolInteractionQuestion(
                question_id="q-2",
                interaction_kind="questionnaire",
                prompt="Duration?",
                answer_kind="text",
                required=True,
            )
        ],
        created_at=NOW,
    )
    completed = ToolResultMessage(
        message_id="result-final",
        call_id="call-1",
        tool_name="travel_planner",
        status="completed",
        content=[TextPart(text="Here is your travel plan")],
        artifact_refs=[],
        is_error=False,
        created_at=NOW,
    )

    transcript = [
        UserMessage(
            message_id="u1",
            content=[TextPart(text="Plan trip")],
            created_at=NOW,
        ),
        AssistantMessage(
            message_id="a1",
            content=[],
            tool_calls=[
                ToolCall(
                    call_id="call-1",
                    tool_name="travel_planner",
                    arguments={"task": "Plan trip"},
                )
            ],
            finish_reason="tool_calls",
            usage=None,
            created_at=NOW,
        ),
        interaction1,
        surface_call,
        surface_result,
        interaction2,
        completed,
    ]

    converted = agent_messages_to_model(transcript, prepare_orchestration_context=True)
    assert [m.role for m in converted] == ["user", "assistant", "tool"]
    assert converted[1].content[0].call_id == "call-1"
    tool_text = converted[2].content[0].content[0].text
    assert "Destination?" in tool_text
    assert "Duration?" in tool_text
    assert "Here is your travel plan" in tool_text


def test_transcript_preserves_rejected_surface_call():
    assistant = AssistantMessage(
        message_id="m1",
        content=[],
        tool_calls=[
            ToolCall(
                call_id="call-fail",
                tool_name="surface_agent_questions",
                arguments={},
            )
        ],
        finish_reason="tool_calls",
        usage=None,
        created_at=NOW,
    )
    result = ToolResultMessage(
        message_id="m2",
        call_id="call-fail",
        tool_name="surface_agent_questions",
        status="completed",
        content=[TextPart(text="schema validation error")],
        artifact_refs=[],
        is_error=True,
        created_at=NOW,
    )

    transcript = [assistant, result]
    folded = agent_messages_to_model(transcript)

    assert len(folded) == 2
    assert folded[0].role == "assistant"
    assert folded[0].content[0].tool_name == "surface_agent_questions"
    assert folded[1].role == "tool"
    assert "schema validation error" in folded[1].content[0].content[0].text
    assert folded[1].content[0].is_error is True
