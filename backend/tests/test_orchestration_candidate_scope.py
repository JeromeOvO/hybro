from types import SimpleNamespace

from execution.orchestration.candidate_scope import (
    candidate_scope_from_legacy_envelope,
    normalize_candidate_scope,
)


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


def test_candidate_scope_from_legacy_envelope_uses_candidate_agent_ids():
    scope = candidate_scope_from_legacy_envelope(
        room_id="room-1",
        envelope={
            "candidate_scope_mode": "explicit_selection",
            "candidate_agent_ids": ["agent-2", "agent-1"],
            "candidate_scope_snapshot_version": 1,
        },
    )

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
