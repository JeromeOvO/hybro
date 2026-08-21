from __future__ import annotations

from execution.orchestrator.a2a_runtime.authorization import (
    CallableAuthorizationRefresh,
    CallableAuthReferenceVerification,
)
from execution.orchestrator.a2a_runtime.models import AgentToolBindingRecord
from execution.orchestrator.models import ToolDefinition

from ._orchestrator_v3_helpers import NOW


def binding():
    definition = ToolDefinition(
        name="agent_abc",
        label="Agent",
        description="test",
        input_schema={"type": "object"},
        execution_mode="parallel",
        side_effect_level="external",
    )
    return AgentToolBindingRecord(
        binding_id="binding-1",
        binding_digest="digest",
        run_id="run-1",
        room_id="room-1",
        room_epoch=1,
        tool_name=definition.name,
        definition=definition,
        agent_id="agent-1",
        card_digest="card",
        endpoint_scope="endpoint",
        endpoint_scope_digest="endpoint-digest",
        transport_kind="direct",
        candidate_scope_id="scope",
        candidate_scope_revision=1,
        authorization_basis_digest="basis",
        requesting_subject_digest="subject",
        input_modes=["text"],
        output_modes=["text"],
        created_at=NOW,
    )


async def test_callable_authorization_preserves_closed_outcome_inventory():
    for expected in ("authorized", "denied", "transient_failure"):
        port = CallableAuthorizationRefresh(lambda *_, value=expected: value)
        assert (
            await port.authorize(
                binding=binding(),
                requesting_subject_id="user-1",
                room_id="room-1",
                room_epoch=1,
                resource_refs=[],
            )
            == expected
        )


async def test_auth_reference_adapter_receives_complete_call_bound_context():
    seen = []

    def verify(*args):
        seen.append(args)
        return "verified-proof-digest"

    port = CallableAuthReferenceVerification(verify)
    assert (
        await port.verify(
            "authref:trusted",
            authenticated_answerer_id="user-1",
            call_record_id="call-1",
            binding_id="binding-1",
            binding_digest="binding-digest",
            room_id="room-1",
            room_epoch=1,
            interaction_id="interaction-1",
            interaction_revision=2,
            route_fingerprint="route-fingerprint",
            interaction_fingerprint="interaction-fingerprint",
            question_id="authorization",
            challenge_digest="challenge-digest",
            answer_digest="answer-digest",
        )
        == "verified-proof-digest"
    )
    assert seen == [
        (
            "authref:trusted",
            "user-1",
            "call-1",
            "binding-1",
            "binding-digest",
            "room-1",
            1,
            "interaction-1",
            2,
            "route-fingerprint",
            "interaction-fingerprint",
            "authorization",
            "challenge-digest",
            "answer-digest",
        )
    ]


async def test_unknown_authorization_outcome_fails_closed_as_transient():
    port = CallableAuthorizationRefresh(lambda *_: "maybe")
    assert (
        await port.authorize(
            binding=binding(),
            requesting_subject_id="user-1",
            room_id="room-1",
            room_epoch=1,
            resource_refs=[],
        )
        == "transient_failure"
    )
