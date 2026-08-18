from __future__ import annotations

import pytest

from common.a2a_constants import HYBRO_A2A_INTERACTION_METADATA_KEY
from common.types import (
    Message,
    MessageRole,
    Part,
    Task,
    TaskState,
    TaskStatus,
    TextPart,
)
from execution.dispatch.a2a_interaction import input_observation_from_a2a
from execution.dispatch.agent_ingress_router import (
    UNSUPPORTED_INTERACTION_CODE,
    AgentIngressRoute,
    AgentIngressRouter,
)
from execution.orchestration.run_store import InMemoryOrchestrationRunStore
from models.orchestration import DispatchIntent, OrchestrationRunState
from models.room import MessageContent, RoomAgentMessage


def _task(metadata: dict | None) -> Task:
    return Task(
        id="remote-task",
        contextId="remote-context",
        status=TaskStatus(
            state=TaskState.input_required,
            message=Message(
                role=MessageRole.AGENT,
                messageId="remote-message",
                parts=[Part(root=TextPart(text="RAW_PRIVATE_PROMPT"))],
                metadata=metadata,
            ),
        ),
    )


def _typed_metadata() -> dict:
    return {
        HYBRO_A2A_INTERACTION_METADATA_KEY: {
            "schema_version": 1,
            "interaction_id": "interaction-1",
            "questions": [
                {
                    "question_id": "q1",
                    "interaction_kind": "questionnaire",
                    "prompt": "First typed question?",
                    "answer_kind": "text",
                },
                {
                    "question_id": "q2",
                    "interaction_kind": "questionnaire",
                    "prompt": "Second typed question?",
                    "answer_kind": "single_choice",
                    "choices": ["a", "b"],
                },
            ],
        }
    }


class _Reader:
    def __init__(self, message: RoomAgentMessage) -> None:
        self.message = message

    async def get_room_agent_message_by_message_id(self, message_id: str):
        return self.message if self.message.message_id == message_id else None


def _message(*, run_id: str | None = None) -> RoomAgentMessage:
    return RoomAgentMessage(
        message_id="agent-message",
        room_id="room-1",
        agent_id="agent-1",
        run_id=run_id,
        message_content=MessageContent(message_text="local dispatch"),
    )


@pytest.mark.asyncio
async def test_conversation_typed_uses_typed_inventory_not_raw_prompt():
    observation = input_observation_from_a2a(_task(_typed_metadata()))
    router = AgentIngressRouter(
        message_reader=_Reader(_message()),
        orchestration_run_store=InMemoryOrchestrationRunStore(),
    )

    decision = await router.decide(
        message_id="agent-message",
        room_id="room-1",
        agent_id="agent-1",
        observation=observation,
    )

    assert decision.route == AgentIngressRoute.CONVERSATION_TYPED
    assert [question.prompt for question in decision.interaction_spec.questions] == [
        "First typed question?",
        "Second typed question?",
    ]
    assert "RAW_PRIVATE_PROMPT" not in decision.interaction_spec.model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "metadata",
    [
        None,
        {"untrusted": "PRIVATE"},
        {
            HYBRO_A2A_INTERACTION_METADATA_KEY: {
                "schema_version": "invalid",
                "interaction_id": "PRIVATE",
                "questions": [],
            }
        },
    ],
)
async def test_absent_untyped_and_invalid_metadata_are_safe_unsupported(metadata):
    observation = input_observation_from_a2a(_task(metadata))
    router = AgentIngressRouter(
        message_reader=_Reader(_message()),
        orchestration_run_store=InMemoryOrchestrationRunStore(),
    )

    decision = await router.decide(
        message_id="agent-message",
        room_id="room-1",
        agent_id="agent-1",
        observation=observation,
    )

    assert decision.route == AgentIngressRoute.UNSUPPORTED
    assert decision.error_code == UNSUPPORTED_INTERACTION_CODE
    assert "PRIVATE" not in (decision.public_error or "")


@pytest.mark.asyncio
async def test_supervisor_owner_is_verified_and_private_observation_cas_precedes_return():
    store = InMemoryOrchestrationRunStore()
    intent = DispatchIntent(
        step_id="step-1",
        step_target_id="target-1",
        dispatch_intent_id="intent-1",
        planned_agent_message_id="agent-message",
        agent_id="agent-1",
        task="delegate",
        task_hash="hash",
    )
    await store.create_run(
        OrchestrationRunState(
            run_id="run-1",
            room_id="room-1",
            user_message_id="user-message",
            goal="goal",
            candidate_agent_ids=["agent-1"],
            dispatch_intents=[intent],
        )
    )
    router = AgentIngressRouter(
        message_reader=_Reader(_message(run_id="run-1")),
        orchestration_run_store=store,
    )
    observation = input_observation_from_a2a(_task(_typed_metadata()))

    first = await router.decide(
        message_id="agent-message",
        room_id="room-1",
        agent_id="agent-1",
        observation=observation,
    )
    second = await router.decide(
        message_id="agent-message",
        room_id="room-1",
        agent_id="agent-1",
        observation=observation,
    )
    saved = await store.get_run("run-1")

    assert first.route == second.route == AgentIngressRoute.SUPERVISOR_OBSERVATION
    assert saved is not None
    assert saved.state_version == 1
    assert len(saved.private_agent_input_observations) == 1
    serialized = saved.private_agent_input_observations[0].model_dump_json()
    assert "RAW_PRIVATE_PROMPT" in serialized
    assert saved.agent_outputs == []


@pytest.mark.asyncio
async def test_supervisor_dispatch_mismatch_fails_closed_without_private_append():
    store = InMemoryOrchestrationRunStore()
    await store.create_run(
        OrchestrationRunState(
            run_id="run-1",
            room_id="room-1",
            user_message_id="user-message",
            goal="goal",
            candidate_agent_ids=["agent-1"],
        )
    )
    router = AgentIngressRouter(
        message_reader=_Reader(_message(run_id="run-1")),
        orchestration_run_store=store,
    )
    decision = await router.decide(
        message_id="agent-message",
        room_id="room-1",
        agent_id="agent-1",
        observation=input_observation_from_a2a(_task(_typed_metadata())),
    )
    saved = await store.get_run("run-1")

    assert decision.route == AgentIngressRoute.UNSUPPORTED
    assert saved is not None
    assert saved.private_agent_input_observations == []
