from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from execution.orchestration.action_validator import PlannerActionValidationError
from execution.orchestration.context_builder import (
    MissingRequiredQuoteError,
    build_orchestration_planner_context,
)
from execution.orchestration.planner import RoomSupervisorPlannerAdapter
from execution.orchestration.resources import ResourceProjectionRef, ResourceRef
from execution.orchestration.room_supervisor_service import RoomSupervisorService
from models.orchestration import (
    AgentOutputRecord,
    DispatchIntent,
    OrchestrationRunState,
    OrchestrationStatus,
    PlannerActionType,
)
from models.supervisor import AgentProfile

BASE_TIME = datetime(2026, 7, 5, 12, 0, tzinfo=UTC)


def _run_state(**overrides) -> OrchestrationRunState:
    values = {
        "run_id": "run-1",
        "room_id": "room-1",
        "user_message_id": "message-1",
        "goal": "Coordinate the selected agents",
        "candidate_agent_ids": ["agent-1", "agent-2"],
        "client_request_id": "client-1",
        "status": OrchestrationStatus.RUNNING,
        "state_version": 3,
        "step_budget": 8,
        "steps_used": 2,
        "created_at": BASE_TIME,
        "updated_at": BASE_TIME,
    }
    values.update(overrides)
    return OrchestrationRunState(**values)


def _candidate(agent_id: str, name: str) -> AgentProfile:
    return AgentProfile(
        agent_id=agent_id,
        agent_name=name,
        description=f"{name} description",
        capabilities=["research"],
        success_rate=0.75,
        is_healthy=True,
    )


def test_context_builder_exposes_available_resources():
    resource = ResourceRef(
        ref_id="file:file-1",
        kind="attachment",
        origin="user_message",
        source_message_id="message-1",
        file_name="submission.pdf",
        mime_type="application/pdf",
        status="ready",
        summary="submission.pdf (application/pdf, 128 bytes)",
        supported_by_agent_ids=[],
        projections=[
            ResourceProjectionRef(
                ref_id="ctx:file-file-1:text",
                kind="context",
                source_ref_id="file:file-1",
                mime_type="text/plain",
                status="ready",
                recommended_for_input_modes=["text"],
                summary="Extracted 500 characters from 2 PDF page(s).",
            )
        ],
    )

    payload = build_orchestration_planner_context(
        run_state=_run_state(),
        message_text="Use the attachment",
        available_resources=[resource],
    ).prompt_payload()

    available = payload["available_resources"]
    assert available[0]["ref_id"] == "file:file-1"
    assert available[0]["projections"][0]["ref_id"] == "ctx:file-file-1:text"
    assert available[0]["projections"][0]["status"] == "ready"


def test_context_builder_exposes_candidate_resource_capabilities():
    context = build_orchestration_planner_context(
        run_state=_run_state(candidate_agent_ids=["agent-1"]),
        candidate_scope=[
            AgentProfile(
                agent_id="agent-1",
                agent_name="PDF Agent",
                input_modes=["text", "application/pdf"],
                output_modes=["application/json"],
                supports_file_upload=True,
            )
        ],
        message_text="Review the submission",
    )

    candidate = context.prompt_payload()["candidate_scope"]["agents"][0]
    assert candidate["input_modes"] == ["text", "application/pdf"]
    assert candidate["output_modes"] == ["application/json"]
    assert candidate["supports_file_upload"] is True


def test_quote_text_is_preserved_verbatim():
    quote = "  First line\n\nSecond\tline with trailing spaces  "

    context = build_orchestration_planner_context(
        run_state=_run_state(),
        candidate_scope=[_candidate("agent-1", "Agent One")],
        message_text="Use this exact quote",
        quote=quote,
    )

    assert context.quote is not None
    assert context.quote.text == quote


def test_required_quote_failure_is_clear():
    with pytest.raises(MissingRequiredQuoteError, match="quote_required=True"):
        build_orchestration_planner_context(
            run_state=_run_state(),
            candidate_scope=[_candidate("agent-1", "Agent One")],
            message_text="Reply to the quote",
            quote_required=True,
        )


def test_candidate_scope_comes_from_sidecar_scope_not_message_mentions():
    context = build_orchestration_planner_context(
        run_state=_run_state(candidate_agent_ids=["agent-1"]),
        candidate_scope=[_candidate("agent-1", "Agent One")],
        message_text="@agent-2 and @Agent Two should not be inferred from text",
    )

    assert context.candidate_scope.agent_ids == ["agent-1"]
    assert [agent.agent_id for agent in context.candidate_scope.agents] == ["agent-1"]
    assert "agent-2" not in context.candidate_agent_ids


@pytest.mark.parametrize(
    "candidate_scope",
    [
        {"mode": "saved_group", "group_id": "group-1"},
        {"mode": "saved_group", "group_id": "group-1", "agents": []},
    ],
)
def test_metadata_only_candidate_scope_does_not_invent_agent_ids(candidate_scope):
    context = build_orchestration_planner_context(
        run_state=_run_state(candidate_agent_ids=[]),
        candidate_scope=candidate_scope,
        message_text="Continue",
    )

    assert context.candidate_scope.mode == "saved_group"
    assert context.candidate_scope.group_id == "group-1"
    assert context.candidate_scope.agent_ids == []
    assert context.candidate_scope.agents == []


def test_state_context_is_deterministic_and_includes_run_plan_outputs_artifacts():
    first = _run_state(
        facts=[{"b": 2, "a": 1}],
        open_questions=[{"z": "last", "a": "first"}],
        dispatch_intents=[
            DispatchIntent(
                step_id="step-2",
                step_target_id="target-1",
                dispatch_intent_id="intent-1",
                planned_agent_message_id="planned-1",
                agent_id="agent-1",
                task="Review the account",
                task_hash="hash-1",
                status="completed",
            )
        ],
        agent_outputs=[
            AgentOutputRecord(
                agent_message_id="agent-message-1",
                agent_id="agent-1",
                status="success",
                text="Answer text",
                artifact_keys=["artifact-2", "artifact-1"],
            )
        ],
        artifacts=[{"type": "file", "key": "artifact-1"}],
        completion_criteria=[{"done": False, "criterion": "collect answer"}],
        decision_log=[{"why": "needed specialist", "step": 1}],
        pending_hitl_request_ids=["hitl-2", "hitl-1"],
    )
    second = _run_state(
        facts=[{"a": 1, "b": 2}],
        open_questions=[{"a": "first", "z": "last"}],
        dispatch_intents=first.dispatch_intents,
        agent_outputs=first.agent_outputs,
        artifacts=[{"key": "artifact-1", "type": "file"}],
        completion_criteria=[{"criterion": "collect answer", "done": False}],
        decision_log=[{"step": 1, "why": "needed specialist"}],
        pending_hitl_request_ids=["hitl-2", "hitl-1"],
    )

    first_context = build_orchestration_planner_context(
        run_state=first,
        candidate_scope=[_candidate("agent-1", "Agent One")],
        message_text="Continue",
    )
    second_context = build_orchestration_planner_context(
        run_state=second,
        candidate_scope=[_candidate("agent-1", "Agent One")],
        message_text="Continue",
    )

    first_dump = first_context.state_context.model_dump(mode="json")
    second_dump = second_context.state_context.model_dump(mode="json")

    assert first_dump == second_dump
    assert first_dump["run"]["run_id"] == "run-1"
    assert first_dump["current_step"] == {
        "steps_used": 2,
        "step_budget": 8,
        "steps_remaining": 6,
        "next_step_number": 3,
    }
    assert first_dump["current_plan"][0]["agent_id"] == "agent-1"
    assert first_dump["agent_outputs"][0]["text"] == "Answer text"
    assert first_dump["artifacts"] == [{"key": "artifact-1", "type": "file"}]


@pytest.mark.asyncio
async def test_planner_adapter_parses_and_validates_against_context_boundary():
    async def raw_action(_context):
        return {
            "action": "delegate",
            "reasoning": "route to scoped specialist",
            "targets": [
                {
                    "agent_id": "agent-1",
                    "agent_name": "Agent One",
                    "task": "Handle the request",
                }
            ],
        }

    context = build_orchestration_planner_context(
        run_state=_run_state(candidate_agent_ids=["agent-1"]),
        candidate_scope=[_candidate("agent-1", "Agent One")],
        message_text="@agent-2 should not become a valid target",
    )
    adapter = RoomSupervisorPlannerAdapter(raw_action_provider=raw_action)

    action = await adapter.plan(context)

    assert action.action == PlannerActionType.DELEGATE
    assert action.targets[0].agent_id == "agent-1"


@pytest.mark.asyncio
async def test_planner_adapter_keeps_v2_validation_outside_prompt_text():
    seen_prompt_values: list[str] = []

    async def raw_action(context):
        prompt_payload = context.prompt_payload()
        _collect_strings(prompt_payload, seen_prompt_values)
        return {
            "action": "delegate",
            "reasoning": "message mentioned an out-of-scope agent",
            "targets": [
                {
                    "agent_id": "agent-2",
                    "agent_name": "Agent Two",
                    "task": "Handle the request",
                }
            ],
        }

    context = build_orchestration_planner_context(
        run_state=_run_state(candidate_agent_ids=["agent-1"]),
        candidate_scope=[_candidate("agent-1", "Agent One")],
        message_text="@agent-2 please take this",
    )
    adapter = RoomSupervisorPlannerAdapter(raw_action_provider=raw_action)

    with pytest.raises(PlannerActionValidationError, match="candidate_agent_ids"):
        await adapter.plan(context)

    assert "planner_action_schema_version" not in "\n".join(seen_prompt_values)


@pytest.mark.asyncio
async def test_planner_adapter_uses_public_supervisor_service_boundary():
    parsed_action = RoomSupervisorService.parse_planner_action(
        {
            "action": "delegate",
            "reasoning": "use scoped agent",
            "targets": [
                {
                    "agent_id": "agent-1",
                    "agent_name": "Agent One",
                    "task": "Handle the request",
                }
            ],
        }
    )
    service = SimpleNamespace(
        call_planner_json=AsyncMock(
            return_value={
                "action": "delegate",
                "reasoning": "use scoped agent",
                "targets": [
                    {
                        "agent_id": "agent-1",
                        "agent_name": "Agent One",
                        "task": "Handle the request",
                    }
                ],
            }
        ),
        parse_planner_action=MagicMock(return_value=parsed_action),
    )
    context = build_orchestration_planner_context(
        run_state=_run_state(candidate_agent_ids=["agent-1"]),
        candidate_scope=[_candidate("agent-1", "Agent One")],
        message_text="Delegate",
    )

    action = await RoomSupervisorPlannerAdapter(
        supervisor_service=service,
    ).plan(context)

    assert action.action == PlannerActionType.DELEGATE
    service.call_planner_json.assert_awaited_once()
    service.parse_planner_action.assert_called_once()


def _collect_strings(value, output: list[str]) -> None:
    if isinstance(value, str):
        output.append(value)
        return
    if isinstance(value, Mapping):
        for nested in value.values():
            _collect_strings(nested, output)
        return
    if isinstance(value, list):
        for nested in value:
            _collect_strings(nested, output)
