from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from execution.orchestration.action_validator import PlannerActionValidationError
from execution.orchestration.candidate_scope import normalize_candidate_scope
from execution.orchestration.context_builder import (
    MissingRequiredQuoteError,
    build_orchestration_planner_context,
)
from execution.orchestration.planner import RoomSupervisorPlannerAdapter
from execution.orchestration.resources import ResourceProjectionRef, ResourceRef
from execution.orchestration.room_supervisor_service import RoomSupervisorService
from models.orchestration import (
    AgentOutputRecord,
    AuthorizationBasis,
    BlockerRecord,
    CandidateAgentSnapshot,
    CandidateScopeSnapshot,
    CompletionEvidence,
    DelegationOutcomeRecord,
    DispatchIntent,
    GoalFamilyDispositionRecord,
    OpenFailureRecord,
    OrchestrationRunState,
    OrchestrationStatus,
    ParticipantSnapshot,
    PendingAgentContinuation,
    PlannerActionRecord,
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


def test_candidate_scope_falls_back_to_run_state_candidate_ids_when_scope_absent():
    context = build_orchestration_planner_context(
        run_state=_run_state(candidate_agent_ids=["agent-1"]),
        message_text="Use the selected agent",
    )

    assert context.candidate_scope.agent_ids == ["agent-1"]
    assert [agent.agent_id for agent in context.candidate_scope.agents] == ["agent-1"]


def test_candidate_scope_legacy_string_is_single_agent_id():
    context = build_orchestration_planner_context(
        run_state=_run_state(candidate_agent_ids=["agent-1"]),
        candidate_scope="agent-1",
        message_text="Use the selected agent",
    )

    assert context.candidate_scope.agent_ids == ["agent-1"]
    assert [agent.agent_id for agent in context.candidate_scope.agents] == ["agent-1"]


def test_candidate_scope_prefers_run_state_snapshot_over_legacy_argument():
    snapshot = CandidateScopeSnapshot(
        snapshot_id="scope-1",
        revision=3,
        source="saved_group",
        room_id="room-1",
        group_id="group-1",
        agent_ids=["agent-2"],
        agents=[
            CandidateAgentSnapshot(
                agent_id="agent-2",
                name="Insurer",
                role="insurer",
                capability_summary="Produces quote options.",
                status="active",
                source="saved_group",
            )
        ],
        authorization_basis=AuthorizationBasis(
            kind="saved_group_member",
            room_id="room-1",
            group_id="group-1",
            selected_by_user_id="user-1",
        ),
    )

    context = build_orchestration_planner_context(
        run_state=_run_state(candidate_agent_ids=["agent-2"], candidate_scope=snapshot),
        candidate_scope=[_candidate("agent-1", "Legacy Agent")],
        message_text="Use the saved scope",
    )

    assert context.candidate_scope.mode == "saved_group"
    assert context.candidate_scope.group_id == "group-1"
    assert context.candidate_scope.snapshot_version == 3
    assert context.candidate_scope.agent_ids == ["agent-2"]
    assert [agent.agent_name for agent in context.candidate_scope.agents] == ["Insurer"]


def test_candidate_scope_overlays_live_resource_capabilities_on_snapshot():
    snapshot = CandidateScopeSnapshot(
        snapshot_id="scope-1",
        revision=2,
        source="saved_group",
        room_id="room-1",
        group_id="group-1",
        agent_ids=["agent-2"],
        agents=[CandidateAgentSnapshot(agent_id="agent-2", name="Stored Insurer")],
    )

    context = build_orchestration_planner_context(
        run_state=_run_state(candidate_agent_ids=["agent-2"], candidate_scope=snapshot),
        candidate_scope=[
            AgentProfile(
                agent_id="agent-2",
                agent_name="Live Insurer",
                capabilities=["quote", "risk"],
                input_modes=["text", "application/pdf"],
                output_modes=["application/json"],
                supports_file_upload=True,
                success_rate=0.85,
            ),
            _candidate("agent-1", "Out of Scope"),
        ],
        message_text="Use the saved scope",
    )

    assert context.candidate_scope.mode == "saved_group"
    assert context.candidate_scope.group_id == "group-1"
    assert context.candidate_scope.snapshot_version == 2
    assert context.candidate_scope.agent_ids == ["agent-2"]
    candidate = context.candidate_scope.agents[0]
    assert candidate.agent_name == "Live Insurer"
    assert candidate.capabilities == ["quote", "risk"]
    assert candidate.input_modes == ["text", "application/pdf"]
    assert candidate.output_modes == ["application/json"]
    assert candidate.supports_file_upload is True
    assert candidate.success_rate == 0.85


def test_candidate_scope_snapshot_falls_back_to_agent_ids_when_agents_empty():
    snapshot = CandidateScopeSnapshot(
        snapshot_id="scope-1",
        revision=1,
        source="explicit_selection",
        room_id="room-1",
        agent_ids=["agent-1"],
    )

    context = build_orchestration_planner_context(
        run_state=_run_state(candidate_agent_ids=["agent-1"], candidate_scope=snapshot),
        candidate_scope=[_candidate("agent-2", "Legacy Agent")],
        message_text="Use the selected agent",
    )

    assert context.candidate_scope.agent_ids == ["agent-1"]
    assert [agent.agent_id for agent in context.candidate_scope.agents] == ["agent-1"]


def test_candidate_scope_partial_snapshot_preserves_all_agent_ids_in_order():
    snapshot = CandidateScopeSnapshot(
        snapshot_id="scope-1",
        revision=1,
        source="explicit_selection",
        room_id="room-1",
        agent_ids=["agent-a", "agent-b"],
        agents=[
            CandidateAgentSnapshot(
                agent_id="agent-a",
                name="Broker",
                capability_summary="Collects broker requirements.",
                status="active",
            )
        ],
    )

    context = build_orchestration_planner_context(
        run_state=_run_state(
            candidate_agent_ids=["agent-a", "agent-b"],
            candidate_scope=snapshot,
        ),
        message_text="Use the selected agents",
    )

    assert context.candidate_scope.agent_ids == ["agent-a", "agent-b"]
    assert [agent.agent_id for agent in context.candidate_scope.agents] == [
        "agent-a",
        "agent-b",
    ]
    assert context.candidate_scope.agents[0].agent_name == "Broker"
    assert context.candidate_scope.agents[0].description == (
        "Collects broker requirements."
    )
    assert context.candidate_scope.agents[1].agent_name is None


def test_context_builder_prefers_run_state_candidate_scope():
    state = _run_state(
        candidate_agent_ids=["legacy-agent"],
        candidate_scope=CandidateScopeSnapshot(
            snapshot_id="scope-1",
            source="explicit_selection",
            room_id="room-1",
            agent_ids=["agent-1"],
            agents=[CandidateAgentSnapshot(agent_id="agent-1", name="Agent One")],
        ),
    )

    context = build_orchestration_planner_context(
        run_state=state,
        candidate_scope=["legacy-agent"],
        message_text="Need a quote",
    )

    assert context.candidate_agent_ids == ["agent-1"]
    assert context.candidate_scope.agents[0].agent_name == "Agent One"


def test_context_builder_exposes_run_state_extensions():
    state = _run_state(
        participant_snapshot=ParticipantSnapshot(
            mode="sequential",
            ordered_agent_ids=["agent-1", "agent-2"],
            turn_policy="sequential_rounds",
        ),
        system_agent_message_id="sys-msg-1",
        active_dispatches=[
            {"agent_message_id": "agent-msg-1", "agent_id": "agent-1", "status": "running"}
        ],
        last_planner_action=PlannerActionRecord(
            action="delegate", reasoning="need quote"
        ),
        completion_evidence=CompletionEvidence(
            satisfied_criteria=["quote_collected"],
            referenced_fact_ids=["fact-1"],
            referenced_artifact_keys=[],
            unresolved_questions=[],
            final_answer_intent="answer_user",
            confidence=0.9,
        ),
    )

    payload = build_orchestration_planner_context(
        run_state=state,
        candidate_scope=["agent-1"],
        message_text="Need a quote",
    ).prompt_payload()

    state_context = payload["state_context"]
    assert state_context["participant_snapshot"]["mode"] == "sequential"
    assert state_context["system_agent_message_id"] == "sys-msg-1"
    assert state_context["active_dispatches"][0]["agent_message_id"] == "agent-msg-1"
    assert state_context["last_planner_action"]["action"] == "delegate"
    assert state_context["completion_evidence"]["confidence"] == 0.9


def test_context_builder_exposes_open_failures_to_planner():
    state = _run_state(
        open_failures=[
            OpenFailureRecord(
                failure_id="failure-1",
                fingerprint="fp",
                source="a2a_adapter",
                agent_id="agent-1",
                agent_message_id="agent-msg-1",
                error_code="timeout",
                error_message="Timed out",
                recoverable=True,
                status="open",
                recovery_hints=["retry_same_agent_with_smaller_context"],
            )
        ]
    )

    context = build_orchestration_planner_context(
        run_state=state,
        message_text="Finish the workflow",
    )

    assert context.state_context.open_failures[0]["failure_id"] == "failure-1"
    assert context.state_context.open_failures[0]["recoverable"] is True


def test_context_builder_exposes_immutable_outcome_policy_views_without_artifacts():
    outcome = DelegationOutcomeRecord(
        outcome_id="outcome-1",
        dispatch_intent_id="intent-1",
        agent_id="agent-1",
        goal_family_fingerprint="family-1",
        goal_revision_fingerprint="revision-1",
        attempt_fingerprint="attempt-1",
        status="partial",
        missing_output_keys=["summary"],
        remaining_required_obligations=["summary"],
        blockers=[
            BlockerRecord(
                key="blocker-1",
                description="Need a required input.",
                blocked_output_keys=["summary"],
                source="agent",
            )
        ],
    )
    state = _run_state(
        dispatch_intents=[
            DispatchIntent(
                step_id="step-1",
                step_target_id="target-1",
                dispatch_intent_id="intent-1",
                planned_agent_message_id="planned-1",
                agent_id="agent-1",
                task="Summarize the request.",
                task_hash="task-1",
                status="completed",
            )
        ],
        delegation_outcomes=[outcome],
        pending_agent_continuations=[
            PendingAgentContinuation(
                continuation_id="continuation-1",
                source_intent_id="intent-1",
                source_agent_message_id="agent-message-1",
                agent_id="agent-1",
                goal_family_fingerprint="family-1",
                goal_revision_fingerprint="revision-1",
                a2a_task_id="task-1",
                a2a_context_id="context-1",
            )
        ],
        goal_family_dispositions=[
            GoalFamilyDispositionRecord(
                event_id="disposition-1",
                goal_family_fingerprint="family-1",
                through_goal_revision_fingerprint="revision-1",
                status="superseded",
                reason="A replacement plan is active.",
            )
        ],
        blockers=outcome.blockers,
        artifacts=[{"key": "artifact-1", "payload": {"sensitive": "value"}}],
    )

    context = build_orchestration_planner_context(
        run_state=state,
        message_text="Continue the workflow",
    )
    state.delegation_outcomes[0].status = "fulfilled"
    payload = context.prompt_payload()["state_context"]

    assert payload["outcomes"][0]["status"] == "partial"
    assert payload["continuations"][0]["continuation_id"] == "continuation-1"
    assert payload["dispositions"][0]["event_id"] == "disposition-1"
    assert payload["blockers"][0]["key"] == "blocker-1"
    assert payload["attempt_chain_views"] == [
        {
            "agent_id": "agent-1",
            "goal_revision_fingerprint": "revision-1",
            "same_agent_attempt_number": 1,
            "required_progress_epoch": 0,
            "no_progress_repair_used_in_epoch": False,
            "latest_outcome_id": "outcome-1",
        }
    ]
    assert "payload" not in payload["attempt_chain_views"][0]


def test_candidate_scope_mapping_falls_back_to_agent_ids_when_agents_empty():
    context = build_orchestration_planner_context(
        run_state=_run_state(candidate_agent_ids=["agent-1"]),
        candidate_scope={"agents": [], "agent_ids": ["agent-1"]},
        message_text="Use the selected agent",
    )

    assert context.candidate_scope.agent_ids == ["agent-1"]
    assert [agent.agent_id for agent in context.candidate_scope.agents] == ["agent-1"]


def test_candidate_scope_partial_mapping_preserves_all_agent_ids_in_order():
    context = build_orchestration_planner_context(
        run_state=_run_state(candidate_agent_ids=["agent-a", "agent-b"]),
        candidate_scope={
            "source": "explicit_selection",
            "agent_ids": ["agent-a", "agent-b"],
            "agents": [
                {
                    "agent_id": "agent-a",
                    "name": "Broker",
                    "capability_summary": "Collects broker requirements.",
                    "status": "active",
                }
            ],
        },
        message_text="Use the selected agents",
    )

    assert context.candidate_scope.agent_ids == ["agent-a", "agent-b"]
    assert [agent.agent_id for agent in context.candidate_scope.agents] == [
        "agent-a",
        "agent-b",
    ]
    assert context.candidate_scope.agents[0].agent_name == "Broker"
    assert context.candidate_scope.agents[0].description == (
        "Collects broker requirements."
    )
    assert context.candidate_scope.agents[1].agent_name is None


def test_candidate_scope_snapshot_agent_summary_and_status_are_visible():
    snapshot = CandidateScopeSnapshot(
        snapshot_id="scope-1",
        revision=1,
        source="saved_group",
        room_id="room-1",
        agent_ids=["agent-1"],
        agents=[
            CandidateAgentSnapshot(
                agent_id="agent-1",
                name="Broker",
                capability_summary="Collects broker requirements.",
                status="active",
            )
        ],
    )

    context = build_orchestration_planner_context(
        run_state=_run_state(candidate_agent_ids=["agent-1"], candidate_scope=snapshot),
        message_text="Use the selected agent",
    )

    agent = context.candidate_scope.agents[0]
    assert agent.description == "Collects broker requirements."
    assert agent.capabilities == ["Collects broker requirements."]
    assert agent.is_healthy is True


def test_candidate_scope_dict_agent_summary_and_status_are_visible():
    context = build_orchestration_planner_context(
        run_state=_run_state(candidate_agent_ids=["agent-1"]),
        candidate_scope={
            "source": "saved_group",
            "agent_ids": ["agent-1"],
            "agents": [
                {
                    "agent_id": "agent-1",
                    "name": "Broker",
                    "capability_summary": "Collects broker requirements.",
                    "status": "active",
                }
            ],
        },
        message_text="Use the selected agent",
    )

    agent = context.candidate_scope.agents[0]
    assert agent.description == "Collects broker requirements."
    assert agent.capabilities == ["Collects broker requirements."]
    assert agent.is_healthy is True


def test_candidate_scope_snapshot_includes_agent_card_input_modes_for_planner():
    agent = SimpleNamespace(
        agent_id="insurer-1",
        agent_status="active",
        agent_card=SimpleNamespace(
            name="Cyber Insurer",
            description="Underwrites structured cyber submissions.",
            default_input_modes=["text"],
            default_output_modes=["text"],
            skills=[SimpleNamespace(id="underwrite-cyber")],
        ),
        call_count=10,
        call_success_count=9,
    )
    scope = normalize_candidate_scope(
        room_id="room-1",
        source="explicit_selection",
        selected_agent_set=[agent],
        selected_by_user_id="user-1",
    )

    context = build_orchestration_planner_context(
        run_state=_run_state(candidate_agent_ids=["insurer-1"], candidate_scope=scope),
        message_text="Underwrite the submission",
    )

    planner_agent = context.candidate_scope.agents[0]
    assert planner_agent.agent_id == "insurer-1"
    assert planner_agent.capabilities == ["underwrite-cyber"]
    assert planner_agent.input_modes == ["text"]
    assert planner_agent.output_modes == ["text"]
    assert planner_agent.supports_file_upload is False


def test_candidate_scope_snapshot_includes_serialized_agent_card_modes_for_planner():
    agent = {
        "agent_id": "insurer-1",
        "agent_status": "active",
        "agent_card": {
            "name": "Cyber Insurer",
            "description": "Underwrites structured cyber submissions.",
            "default_input_modes": ["text"],
            "default_output_modes": ["text"],
            "skills": [{"id": "underwrite-cyber"}],
        },
        "call_count": 10,
        "call_success_count": 9,
    }
    scope = normalize_candidate_scope(
        room_id="room-1",
        source="explicit_selection",
        selected_agent_set=[agent],
        selected_by_user_id="user-1",
    )

    context = build_orchestration_planner_context(
        run_state=_run_state(candidate_agent_ids=["insurer-1"], candidate_scope=scope),
        message_text="Underwrite the submission",
    )

    planner_agent = context.candidate_scope.agents[0]
    assert planner_agent.agent_id == "insurer-1"
    assert planner_agent.capabilities == ["underwrite-cyber"]
    assert planner_agent.input_modes == ["text"]
    assert planner_agent.output_modes == ["text"]
    assert planner_agent.supports_file_upload is False


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


def test_state_context_excludes_resolved_question_history():
    context = build_orchestration_planner_context(
        run_state=_run_state(
            open_questions=[
                {
                    "request_id": "hitl-open",
                    "status": "open",
                    "prompt": "Which account?",
                },
                {
                    "request_id": "hitl-resolved-status",
                    "status": "resolved",
                    "prompt": "Which region?",
                    "answer": "US",
                },
                {
                    "request_id": "hitl-resolved-flag",
                    "resolved": True,
                    "prompt": "Which currency?",
                    "answer": "USD",
                },
            ]
        ),
        message_text="Continue",
    )

    assert context.state_context.open_questions == [
        {
            "prompt": "Which account?",
            "request_id": "hitl-open",
            "status": "open",
        }
    ]


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
async def test_planner_adapter_supervisor_prompt_satisfies_json_mode_requirement():
    supervisor_service = SimpleNamespace(
        call_planner_json=AsyncMock(
            return_value={
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
        ),
        parse_planner_action=RoomSupervisorService.parse_planner_action,
    )
    context = build_orchestration_planner_context(
        run_state=_run_state(candidate_agent_ids=["agent-1"]),
        candidate_scope=[_candidate("agent-1", "Agent One")],
        message_text="Handle this workflow",
    )
    adapter = RoomSupervisorPlannerAdapter(supervisor_service=supervisor_service)

    await adapter.plan(context)

    call_kwargs = supervisor_service.call_planner_json.await_args.kwargs
    combined_prompt = (
        call_kwargs["system_prompt"] + "\n" + call_kwargs["user_prompt"]
    ).lower()
    assert "json" in combined_prompt


@pytest.mark.asyncio
async def test_planner_adapter_supervisor_prompt_requires_delegate_target_task():
    supervisor_service = SimpleNamespace(
        call_planner_json=AsyncMock(
            return_value={
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
        ),
        parse_planner_action=RoomSupervisorService.parse_planner_action,
    )
    context = build_orchestration_planner_context(
        run_state=_run_state(candidate_agent_ids=["agent-1"]),
        candidate_scope=[_candidate("agent-1", "Agent One")],
        message_text="Handle this workflow",
    )
    adapter = RoomSupervisorPlannerAdapter(supervisor_service=supervisor_service)

    await adapter.plan(context)

    system_prompt = supervisor_service.call_planner_json.await_args.kwargs[
        "system_prompt"
    ]
    assert '"task"' in system_prompt
    assert "required" in system_prompt.lower()


@pytest.mark.asyncio
async def test_planner_adapter_supervisor_prompt_guides_attachment_ref_selection():
    supervisor_service = SimpleNamespace(
        call_planner_json=AsyncMock(
            return_value={
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
        ),
        parse_planner_action=RoomSupervisorService.parse_planner_action,
    )
    context = build_orchestration_planner_context(
        run_state=_run_state(candidate_agent_ids=["agent-1"]),
        candidate_scope=[_candidate("agent-1", "Agent One")],
        message_text="Handle this workflow",
    )
    adapter = RoomSupervisorPlannerAdapter(supervisor_service=supervisor_service)

    await adapter.plan(context)

    system_prompt = supervisor_service.call_planner_json.await_args.kwargs[
        "system_prompt"
    ]
    assert "Only include attachment_refs when the target agent's candidate_scope input_modes support that attachment MIME." in system_prompt
    assert "Prefer artifact_refs over raw attachment_refs when an upstream agent has produced a structured artifact." in system_prompt


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


def test_context_builder_exposes_recovery_directives_for_validated_blocker():
    state = _run_state(
        blockers=[
            BlockerRecord(
                key="blocker-1",
                description="Need requested limit.",
                blocked_output_keys=["quote"],
                source="agent",
                claimed_user_only=True,
                validated_user_only=True,
                validation_status="validated",
            )
        ]
    )

    context = build_orchestration_planner_context(
        run_state=state,
        message_text="Finish the workflow",
    )

    assert context.state_context.recovery_directives == [
        {
            "code": "ask_user_for_validated_blocker",
            "blocker_keys": ["blocker-1"],
            "reason": "Validated user-only blocker is open.",
        }
    ]
