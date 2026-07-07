from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from execution.orchestration.result_ingestor import (
    AgentResultIngestor,
    AgentResultRead,
    canonical_artifact_key,
)
from models.orchestration import OrchestrationRunState


def _run_state(**overrides):
    values = {
        "run_id": "run-1",
        "room_id": "room-1",
        "user_message_id": "user-msg-1",
        "goal": "Collect agent results",
        "candidate_agent_ids": ["agent-1", "agent-2"],
        "client_request_id": "cr-1",
    }
    values.update(overrides)
    return OrchestrationRunState(**values)


def test_canonical_artifact_key_coalesces_snake_and_camel_ids():
    assert canonical_artifact_key("agent-msg-1", 0, {"artifact_id": "artifact-1"}) == (
        canonical_artifact_key("agent-msg-1", 99, {"artifactId": "artifact-1"})
    )
    assert canonical_artifact_key("agent-msg-1", 0, {"artifact_id": "artifact-1"}) == (
        canonical_artifact_key("agent-msg-1", 99, {"id": "artifact-1"})
    )
    assert canonical_artifact_key("agent-msg-1", 0, {"part_id": "part-1"}) == (
        canonical_artifact_key("agent-msg-1", 99, {"partId": "part-1"})
    )


def test_canonical_artifact_key_uses_stable_hash_without_id():
    artifact = {
        "b": datetime(2026, 7, 5, tzinfo=UTC),
        "a": {"nested": True},
    }
    expected_digest = hashlib.sha256(
        json.dumps(
            artifact,
            sort_keys=True,
            default=str,
        ).encode("utf-8")
    ).hexdigest()[:16]

    assert canonical_artifact_key("agent-msg-1", 3, artifact) == (
        f"agent-msg-1:3:{expected_digest}"
    )


def test_ingest_adds_output_and_artifact_records_without_mutating_input():
    state = _run_state(state_version=4)
    original_dump = state.model_dump()
    result = AgentResultRead(
        agent_message_id="agent-msg-1",
        agent_id="agent-1",
        status="completed",
        text="The agent finished.",
        artifacts=[
            {
                "artifactId": "artifact-1",
                "name": "answer",
                "parts": [{"kind": "text", "text": "payload"}],
            },
            {
                "artifact_id": "artifact-1",
                "name": "duplicate",
                "parts": [{"kind": "text", "text": "duplicate"}],
            },
        ],
    )

    updated = AgentResultIngestor().ingest(state, result)

    key = canonical_artifact_key(
        "agent-msg-1",
        0,
        {
            "artifactId": "artifact-1",
            "name": "answer",
            "parts": [{"kind": "text", "text": "payload"}],
        },
    )
    assert updated is not state
    assert state.model_dump() == original_dump
    assert updated.state_version == 5
    assert len(updated.agent_outputs) == 1
    assert updated.agent_outputs[0].agent_message_id == "agent-msg-1"
    assert updated.agent_outputs[0].agent_id == "agent-1"
    assert updated.agent_outputs[0].status == "completed"
    assert updated.agent_outputs[0].text == "The agent finished."
    assert updated.agent_outputs[0].artifact_keys == [key]
    assert updated.artifacts == [
        {
            "artifact_key": key,
            "artifactId": "artifact-1",
            "name": "answer",
            "parts": [{"kind": "text", "text": "payload"}],
            "source_agent_message_id": "agent-msg-1",
            "source_agent_id": "agent-1",
            "summary": "answer",
        }
    ]


def test_reingesting_same_result_is_idempotent_for_outputs_and_artifacts():
    result = AgentResultRead(
        agent_message_id="agent-msg-1",
        agent_id="agent-1",
        status="completed",
        text="The agent finished.",
        artifacts=[{"id": "artifact-1", "parts": [{"kind": "text", "text": "x"}]}],
    )
    ingestor = AgentResultIngestor()

    once = ingestor.ingest(_run_state(), result)
    twice = ingestor.ingest(once, result)

    assert once.state_version == 1
    assert twice is once
    assert twice.state_version == 1
    assert len(twice.agent_outputs) == 1
    assert len(twice.artifacts) == 1
    assert twice.agent_outputs[0].artifact_keys == once.agent_outputs[0].artifact_keys
    assert twice.artifacts == once.artifacts


def test_sparse_terminal_replay_preserves_richer_result_without_version_bump():
    ingestor = AgentResultIngestor()
    rich_result = AgentResultRead(
        agent_message_id="agent-msg-1",
        agent_id="agent-1",
        status="completed",
        text="The agent finished.",
        artifacts=[{"artifact_id": "artifact-1", "name": "answer"}],
        error="diagnostic detail",
    )
    sparse_terminal = AgentResultRead(
        agent_message_id="agent-msg-1",
        agent_id="agent-1",
        status="completed",
    )

    rich_state = ingestor.ingest(_run_state(), rich_result)
    replayed = ingestor.ingest(rich_state, sparse_terminal)

    assert replayed is rich_state
    assert replayed.state_version == 1
    assert replayed.agent_outputs[0].text == "The agent finished."
    assert replayed.agent_outputs[0].error == "diagnostic detail"
    assert replayed.agent_outputs[0].artifact_keys == [
        "agent-msg-1:artifact_id:artifact-1"
    ]


def test_ingest_projects_text_into_deduplicated_fact():
    result = AgentResultRead(
        agent_message_id="agent-msg-1",
        agent_id="agent-1",
        status="completed",
        text="Carrier A can quote the risk.",
    )

    updated = AgentResultIngestor().ingest(_run_state(), result)

    assert updated.facts == [
        {
            "fact_id": "agent-msg-1:text",
            "source_agent_message_id": "agent-msg-1",
            "source_agent_id": "agent-1",
            "kind": "agent_text",
            "text": "Carrier A can quote the risk.",
        }
    ]


def test_reingesting_same_text_does_not_duplicate_fact():
    result = AgentResultRead(
        agent_message_id="agent-msg-1",
        agent_id="agent-1",
        status="completed",
        text="Carrier A can quote the risk.",
    )
    ingestor = AgentResultIngestor()

    once = ingestor.ingest(_run_state(), result)
    twice = ingestor.ingest(once, result)

    assert len(twice.facts) == 1
    assert twice.facts[0]["fact_id"] == "agent-msg-1:text"


def test_ingest_artifact_records_source_and_summary():
    result = AgentResultRead(
        agent_message_id="agent-msg-1",
        agent_id="agent-1",
        status="completed",
        text="See attached quote.",
        artifacts=[
            {
                "artifact_id": "quote-1",
                "name": "Quote",
                "mime_type": "application/json",
                "summary": "Premium quote from Carrier A",
            }
        ],
    )

    updated = AgentResultIngestor().ingest(_run_state(), result)

    assert updated.artifacts[0]["artifact_key"] == "agent-msg-1:artifact_id:quote-1"
    assert updated.artifacts[0]["source_agent_message_id"] == "agent-msg-1"
    assert updated.artifacts[0]["source_agent_id"] == "agent-1"
    assert updated.artifacts[0]["summary"] == "Premium quote from Carrier A"
