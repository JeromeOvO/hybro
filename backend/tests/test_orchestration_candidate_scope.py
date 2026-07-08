from types import SimpleNamespace

import pytest

from execution.orchestration.candidate_scope import (
    candidate_scope_from_legacy_envelope,
    normalize_candidate_scope,
)
from models.orchestration import CandidateAgentSnapshot, CandidateScopeSnapshot
from models.supervisor import AgentProfile


def test_normalize_candidate_scope_from_mapping_preserves_order_and_names():
    scope = normalize_candidate_scope(
        room_id="room-1",
        source="saved_group",
        group_id="group-1",
        selected_by_user_id="user-1",
        selected_agent_set={"agent-1": "Broker", "agent-2": "Insurer"},
    )

    assert scope.source == "saved_group"
    assert scope.room_id == "room-1"
    assert scope.group_id == "group-1"
    assert scope.agent_ids == ["agent-1", "agent-2"]
    assert [agent.name for agent in scope.agents] == ["Broker", "Insurer"]
    assert scope.authorization_basis.kind == "saved_group_member"


def test_normalize_candidate_scope_falls_back_to_agent_ids_when_agents_empty():
    scope = normalize_candidate_scope(
        room_id="room-1",
        source="explicit_selection",
        selected_agent_set={"agents": [], "agent_ids": ["agent-1"]},
    )

    assert scope.agent_ids == ["agent-1"]
    assert [agent.agent_id for agent in scope.agents] == ["agent-1"]


def test_normalize_candidate_scope_treats_string_as_single_agent_id():
    scope = normalize_candidate_scope(
        room_id="room-1",
        source="explicit_selection",
        selected_agent_set="agent-1",
    )

    assert scope.agent_ids == ["agent-1"]
    assert [agent.agent_id for agent in scope.agents] == ["agent-1"]


def test_normalize_candidate_scope_treats_single_object_as_one_agent():
    scope = normalize_candidate_scope(
        room_id="room-1",
        source="explicit_selection",
        selected_agent_set=SimpleNamespace(agent_id="agent-1", name="Agent One"),
    )

    assert scope.agent_ids == ["agent-1"]
    assert [agent.name for agent in scope.agents] == ["Agent One"]


def test_normalize_candidate_scope_partial_mapping_preserves_all_agent_ids():
    scope = normalize_candidate_scope(
        room_id="room-1",
        source="explicit_selection",
        selected_agent_set={
            "agent_ids": ["agent-a", "agent-b"],
            "agents": [
                {
                    "agent_id": "agent-a",
                    "name": "Broker",
                    "capability_summary": "Collects broker requirements.",
                    "status": "active",
                    "input_modes": ["text", "application/pdf"],
                    "output_modes": ["application/json"],
                    "supports_file_upload": True,
                }
            ],
        },
    )

    assert scope.agent_ids == ["agent-a", "agent-b"]
    assert [agent.agent_id for agent in scope.agents] == ["agent-a", "agent-b"]
    assert scope.agents[0].name == "Broker"
    assert scope.agents[0].capability_summary == "Collects broker requirements."
    assert scope.agents[0].status == "active"
    assert scope.agents[0].input_modes == ["text", "application/pdf"]
    assert scope.agents[0].output_modes == ["application/json"]
    assert scope.agents[0].supports_file_upload is True
    assert scope.agents[1].name is None


def test_normalize_candidate_scope_object_snapshot_preserves_all_agent_ids():
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

    scope = normalize_candidate_scope(
        room_id="room-1",
        source="explicit_selection",
        selected_agent_set=snapshot,
    )

    assert scope.agent_ids == ["agent-a", "agent-b"]
    assert [agent.agent_id for agent in scope.agents] == ["agent-a", "agent-b"]
    assert scope.agents[0].name == "Broker"
    assert scope.agents[0].capability_summary == "Collects broker requirements."
    assert scope.agents[0].status == "active"
    assert scope.agents[1].name is None


def test_normalize_candidate_scope_agent_profile_preserves_metadata():
    scope = normalize_candidate_scope(
        room_id="room-1",
        source="explicit_selection",
        selected_agent_set=AgentProfile(
            agent_id="agent-1",
            agent_name="Broker",
            description="Handles broker intake.",
            capabilities=["quotes", "risk"],
            input_modes=["text", "application/pdf"],
            output_modes=["application/json"],
            supports_file_upload=True,
            success_rate=0.8,
            is_healthy=False,
        ),
    )

    assert scope.agent_ids == ["agent-1"]
    assert scope.agents[0].name == "Broker"
    assert scope.agents[0].capability_summary == "Handles broker intake."
    assert scope.agents[0].status == "inactive"
    assert scope.agents[0].capabilities == ["quotes", "risk"]
    assert scope.agents[0].input_modes == ["text", "application/pdf"]
    assert scope.agents[0].output_modes == ["application/json"]
    assert scope.agents[0].supports_file_upload is True
    assert scope.agents[0].success_rate == 0.8


def test_candidate_scope_from_legacy_envelope_uses_candidate_agent_ids():
    scope = candidate_scope_from_legacy_envelope(
        room_id="room-1",
        envelope={
            "candidate_scope_snapshot_id": "scope-snapshot-1",
            "candidate_scope_mode": "explicit_selection",
            "candidate_agent_ids": ["agent-2", "agent-1"],
            "candidate_scope_snapshot_version": 1,
        },
    )

    assert scope.snapshot_id == "scope-snapshot-1"
    assert scope.source == "explicit_selection"
    assert scope.agent_ids == ["agent-2", "agent-1"]
    assert scope.revision == 1
    assert scope.authorization_basis.kind == "explicit_selection"


def test_candidate_scope_from_legacy_envelope_filters_registry_to_candidate_ids():
    scope = candidate_scope_from_legacy_envelope(
        room_id="room-1",
        envelope={
            "candidate_scope_mode": "saved_group",
            "candidate_agent_ids": ["agent-2", "agent-1"],
            "candidate_scope_group_id": "group-1",
        },
        selected_agent_set=[
            {"agent_id": "agent-1", "name": "Broker", "role": "broker"},
            {"agent_id": "agent-2", "name": "Insurer", "role": "insurer"},
            {"agent_id": "agent-3", "name": "Auditor", "role": "audit"},
        ],
    )

    assert scope.agent_ids == ["agent-2", "agent-1"]
    assert [agent.name for agent in scope.agents] == ["Insurer", "Broker"]
    assert scope.group_id == "group-1"


def test_normalize_candidate_scope_rejects_unknown_source():
    with pytest.raises(ValueError, match="unsupported candidate scope source"):
        normalize_candidate_scope(
            room_id="room-1",
            source="saved_groups",
            selected_agent_set=["agent-1"],
        )


def test_legacy_candidate_scope_rejects_unknown_source():
    with pytest.raises(ValueError, match="unsupported candidate scope source"):
        candidate_scope_from_legacy_envelope(
            room_id="room-1",
            envelope={
                "candidate_scope_mode": "saved_groups",
                "candidate_agent_ids": ["agent-1"],
            },
        )
