from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from execution.orchestration.result_ingestor import (
    AgentResultIngestor,
    AgentResultRead,
    canonical_artifact_key,
)
from models.orchestration import AgentOutputRecord, OrchestrationRunState


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


def test_canonical_artifact_key_fallback_ignores_projection_owned_fields():
    artifact = {
        "name": "Quote",
        "parts": [{"kind": "text", "text": "payload"}],
        "artifact_key": "bogus-key-1",
        "source_agent_message_id": "bogus-message-1",
        "source_agent_id": "bogus-agent-1",
        "summary": "Bogus summary 1",
    }
    same_payload_different_projection = {
        "name": "Quote",
        "parts": [{"kind": "text", "text": "payload"}],
        "artifact_key": "bogus-key-2",
        "source_agent_message_id": "bogus-message-2",
        "source_agent_id": "bogus-agent-2",
        "summary": "Bogus summary 2",
    }

    assert canonical_artifact_key("agent-msg-1", 0, artifact) == (
        canonical_artifact_key(
            "agent-msg-1",
            0,
            same_payload_different_projection,
        )
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


def test_ingest_preserves_a2a_routing_metadata():
    result = AgentResultRead(
        agent_message_id="agent-msg-1",
        agent_id="agent-1",
        status="completed",
        text="The agent finished.",
        a2a_task_id="task-1",
        a2a_context_id="context-1",
        status_message="Awaiting confirmation",
    )

    updated = AgentResultIngestor().ingest(_run_state(), result)

    assert updated.agent_outputs[0].a2a_task_id == "task-1"
    assert updated.agent_outputs[0].a2a_context_id == "context-1"
    assert updated.agent_outputs[0].status_message == "Awaiting confirmation"


def test_reingesting_updates_a2a_routing_metadata():
    initial = AgentResultRead(
        agent_message_id="agent-msg-1",
        agent_id="agent-1",
        status="completed",
        text="The agent finished.",
        a2a_task_id="task-1",
        a2a_context_id="context-1",
        status_message="Initial",
    )
    updated_once = AgentResultIngestor().ingest(_run_state(), initial)
    reingested = AgentResultRead(
        agent_message_id="agent-msg-1",
        agent_id="agent-1",
        status="completed",
        text="The agent finished.",
        a2a_task_id="task-2",
        a2a_context_id="context-2",
        status_message="Replayed",
    )

    updated_twice = AgentResultIngestor().ingest(updated_once, reingested)

    assert updated_twice.agent_outputs[0].a2a_task_id == "task-2"
    assert updated_twice.agent_outputs[0].a2a_context_id == "context-2"
    assert updated_twice.agent_outputs[0].status_message == "Replayed"


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
    assert replayed.facts == [
        {
            "fact_id": "agent-msg-1:text",
            "source_agent_message_id": "agent-msg-1",
            "source_agent_id": "agent-1",
            "kind": "agent_text",
            "text": "The agent finished.",
        }
    ]


def test_sparse_terminal_replay_preserves_artifact_only_result():
    ingestor = AgentResultIngestor()
    artifact_only_result = AgentResultRead(
        agent_message_id="agent-msg-1",
        agent_id="agent-1",
        status="completed",
        artifacts=[{"artifact_id": "artifact-1", "name": "answer"}],
    )
    sparse_terminal = AgentResultRead(
        agent_message_id="agent-msg-1",
        agent_id="agent-1",
        status="completed",
    )

    rich_state = ingestor.ingest(_run_state(), artifact_only_result)
    replayed = ingestor.ingest(rich_state, sparse_terminal)

    assert replayed is rich_state
    assert replayed.state_version == 1
    assert replayed.agent_outputs[0].artifact_keys == [
        "agent-msg-1:artifact_id:artifact-1"
    ]
    assert replayed.artifacts == rich_state.artifacts


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


def test_ingest_artifact_overwrites_stale_source_metadata():
    result = AgentResultRead(
        agent_message_id="agent-msg-1",
        agent_id="agent-1",
        status="completed",
        text="See attached quote.",
        artifacts=[
            {
                "artifact_id": "quote-1",
                "name": "Quote",
                "source_agent_message_id": "stale-message",
                "source_agent_id": "stale-agent",
            }
        ],
    )

    updated = AgentResultIngestor().ingest(_run_state(), result)

    assert updated.artifacts[0]["source_agent_message_id"] == "agent-msg-1"
    assert updated.artifacts[0]["source_agent_id"] == "agent-1"


def test_ingest_artifact_blank_summary_falls_back_to_name_or_title():
    result = AgentResultRead(
        agent_message_id="agent-msg-1",
        agent_id="agent-1",
        status="completed",
        text="See attached quote.",
        artifacts=[
            {
                "artifact_id": "quote-1",
                "name": "Carrier A quote",
                "summary": None,
            },
            {
                "artifact_id": "quote-2",
                "title": "Carrier B quote",
                "summary": "   ",
            },
        ],
    )

    updated = AgentResultIngestor().ingest(_run_state(), result)

    assert updated.artifacts[0]["summary"] == "Carrier A quote"
    assert updated.artifacts[1]["summary"] == "Carrier B quote"


def test_reingesting_changed_text_updates_existing_fact():
    ingestor = AgentResultIngestor()
    first = AgentResultRead(
        agent_message_id="agent-msg-1",
        agent_id="agent-1",
        status="completed",
        text="Carrier A can quote the risk.",
    )
    changed = AgentResultRead(
        agent_message_id="agent-msg-1",
        agent_id="agent-2",
        status="completed",
        text="Carrier B declined the risk.",
    )

    once = ingestor.ingest(_run_state(), first)
    twice = ingestor.ingest(once, changed)

    assert len(twice.facts) == 1
    assert twice.facts[0] == {
        "fact_id": "agent-msg-1:text",
        "source_agent_message_id": "agent-msg-1",
        "source_agent_id": "agent-2",
        "kind": "agent_text",
        "text": "Carrier B declined the risk.",
    }


def test_reingesting_existing_artifact_updates_projection_metadata():
    artifact_key = "agent-msg-1:artifact_id:quote-1"
    state = _run_state(
        artifacts=[
            {
                "artifact_key": artifact_key,
                "artifact_id": "quote-1",
                "name": "Stale quote",
                "parts": [{"kind": "text", "text": "original"}],
                "description": "Stale description",
                "source_agent_message_id": "stale-message",
                "summary": "   ",
            }
        ]
    )
    result = AgentResultRead(
        agent_message_id="agent-msg-1",
        agent_id="agent-1",
        status="completed",
        text="See attached quote.",
        artifacts=[
            {
                "artifact_id": "quote-1",
                "name": "Carrier A quote",
                "description": "Current description",
                "summary": " ",
            }
        ],
    )

    updated = AgentResultIngestor().ingest(state, result)

    assert len(updated.artifacts) == 1
    assert updated.artifacts[0]["artifact_key"] == artifact_key
    assert updated.artifacts[0]["source_agent_message_id"] == "agent-msg-1"
    assert updated.artifacts[0]["source_agent_id"] == "agent-1"
    assert updated.artifacts[0]["summary"] == "Carrier A quote"
    assert updated.artifacts[0]["name"] == "Carrier A quote"
    assert updated.artifacts[0]["description"] == "Current description"
    assert "parts" not in updated.artifacts[0]


def test_reingesting_existing_artifact_removes_stale_arbitrary_payload_fields():
    artifact_key = "agent-msg-1:artifact_id:quote-1"
    state = _run_state(
        artifacts=[
            {
                "artifact_key": artifact_key,
                "artifact_id": "quote-1",
                "name": "Stale quote",
                "metadata": {"carrier": "stale"},
                "index": 7,
                "append": True,
                "lastChunk": False,
                "source_agent_message_id": "agent-msg-1",
                "source_agent_id": "agent-1",
                "summary": "Stale quote",
            }
        ]
    )
    result = AgentResultRead(
        agent_message_id="agent-msg-1",
        agent_id="agent-1",
        status="completed",
        artifacts=[
            {
                "artifact_id": "quote-1",
                "name": "Current quote",
            }
        ],
    )

    updated = AgentResultIngestor().ingest(state, result)

    assert updated.artifacts == [
        {
            "artifact_key": artifact_key,
            "artifact_id": "quote-1",
            "name": "Current quote",
            "source_agent_message_id": "agent-msg-1",
            "source_agent_id": "agent-1",
            "summary": "Current quote",
        }
    ]


def test_reingesting_existing_artifact_ignores_payload_projection_fields():
    artifact_key = "agent-msg-1:artifact_id:quote-1"
    state = _run_state(
        artifacts=[
            {
                "artifact_key": artifact_key,
                "artifact_id": "quote-1",
                "name": "Stale quote",
                "source_agent_message_id": "agent-msg-1",
                "source_agent_id": "agent-1",
                "summary": "Stale quote",
            }
        ]
    )
    result = AgentResultRead(
        agent_message_id="agent-msg-1",
        agent_id="agent-1",
        status="completed",
        artifacts=[
            {
                "artifact_key": "bogus-key",
                "artifact_id": "quote-1",
                "name": "Current quote",
                "source_agent_message_id": "bogus-message",
                "source_agent_id": "bogus-agent",
                "summary": "Payload summary",
            }
        ],
    )

    updated = AgentResultIngestor().ingest(state, result)

    assert updated.agent_outputs[0].artifact_keys == [artifact_key]
    assert updated.artifacts == [
        {
            "artifact_key": artifact_key,
            "artifact_id": "quote-1",
            "name": "Current quote",
            "source_agent_message_id": "agent-msg-1",
            "source_agent_id": "agent-1",
            "summary": "Payload summary",
        }
    ]


def test_reingesting_no_id_artifact_ignores_projection_fields_for_identity():
    ingestor = AgentResultIngestor()
    first = AgentResultRead(
        agent_message_id="agent-msg-1",
        agent_id="agent-1",
        status="completed",
        artifacts=[
            {
                "name": "Quote",
                "parts": [{"kind": "text", "text": "payload"}],
                "artifact_key": "bogus-key-1",
                "source_agent_message_id": "bogus-message-1",
                "source_agent_id": "bogus-agent-1",
                "summary": "Bogus summary 1",
            }
        ],
    )
    second = AgentResultRead(
        agent_message_id="agent-msg-1",
        agent_id="agent-1",
        status="completed",
        artifacts=[
            {
                "name": "Quote",
                "parts": [{"kind": "text", "text": "payload"}],
                "artifact_key": "bogus-key-2",
                "source_agent_message_id": "bogus-message-2",
                "source_agent_id": "bogus-agent-2",
                "summary": "Bogus summary 2",
            }
        ],
    )

    once = ingestor.ingest(_run_state(), first)
    twice = ingestor.ingest(once, second)

    assert len(twice.artifacts) == 1
    assert twice.agent_outputs[0].artifact_keys == once.agent_outputs[0].artifact_keys
    assert twice.artifacts[0]["artifact_key"] == once.artifacts[0]["artifact_key"]
    assert twice.artifacts[0]["source_agent_message_id"] == "agent-msg-1"
    assert twice.artifacts[0]["source_agent_id"] == "agent-1"
    assert twice.artifacts[0]["summary"] == "Bogus summary 2"


def test_blank_text_clears_fact_but_missing_text_omits_update():
    ingestor = AgentResultIngestor()
    first = AgentResultRead(
        agent_message_id="agent-msg-1",
        agent_id="agent-1",
        status="completed",
        text="Carrier A can quote the risk.",
    )
    blank = AgentResultRead(
        agent_message_id="agent-msg-1",
        agent_id="agent-1",
        status="completed",
        text="   ",
    )
    missing = AgentResultRead(
        agent_message_id="agent-msg-1",
        agent_id="agent-1",
        status="completed",
        text=None,
    )

    once = ingestor.ingest(_run_state(), first)
    twice = ingestor.ingest(once, blank)
    third = ingestor.ingest(once, missing)

    assert twice.facts == []
    assert third is once
    assert third.facts == once.facts


def test_reingesting_failed_result_without_text_removes_existing_fact():
    ingestor = AgentResultIngestor()
    first = AgentResultRead(
        agent_message_id="agent-msg-1",
        agent_id="agent-1",
        status="completed",
        text="Carrier A can quote the risk.",
    )
    failed = AgentResultRead(
        agent_message_id="agent-msg-1",
        agent_id="agent-1",
        status="failed",
        text=None,
        error="Carrier lookup failed.",
    )
    canceled = AgentResultRead(
        agent_message_id="agent-msg-1",
        agent_id="agent-1",
        status="canceled",
        text="",
    )

    once = ingestor.ingest(_run_state(), first)
    failed_update = ingestor.ingest(once, failed)
    canceled_update = ingestor.ingest(once, canceled)

    assert failed_update.facts == []
    assert canceled_update.facts == []


def test_reingesting_artifacts_replaces_current_keys_and_removes_stale_records():
    ingestor = AgentResultIngestor()
    other_agent = AgentResultRead(
        agent_message_id="agent-msg-2",
        agent_id="agent-2",
        status="completed",
        artifacts=[{"artifact_id": "other", "name": "Other quote"}],
    )
    two_artifacts = AgentResultRead(
        agent_message_id="agent-msg-1",
        agent_id="agent-1",
        status="completed",
        artifacts=[
            {"artifact_id": "quote-1", "name": "Quote 1"},
            {"artifact_id": "quote-2", "name": "Quote 2"},
        ],
    )
    one_artifact = AgentResultRead(
        agent_message_id="agent-msg-1",
        agent_id="agent-1",
        status="completed",
        artifacts=[{"artifact_id": "quote-1", "name": "Quote 1 updated"}],
    )
    state = ingestor.ingest(_run_state(), other_agent)
    with_two = ingestor.ingest(state, two_artifacts)
    with_one = ingestor.ingest(with_two, one_artifact)

    output_with_one = next(
        output
        for output in with_one.agent_outputs
        if output.agent_message_id == "agent-msg-1"
    )
    assert output_with_one.artifact_keys == ["agent-msg-1:artifact_id:quote-1"]
    assert [
        artifact["artifact_key"]
        for artifact in with_one.artifacts
        if artifact.get("source_agent_message_id") == "agent-msg-1"
    ] == ["agent-msg-1:artifact_id:quote-1"]
    assert any(
        artifact.get("artifact_key") == "agent-msg-2:artifact_id:other"
        for artifact in with_one.artifacts
    )

def test_reingesting_failed_or_canceled_text_removes_existing_fact():
    ingestor = AgentResultIngestor()
    first = AgentResultRead(
        agent_message_id="agent-msg-1",
        agent_id="agent-1",
        status="completed",
        text="Carrier A can quote the risk.",
    )
    failed = AgentResultRead(
        agent_message_id="agent-msg-1",
        agent_id="agent-1",
        status="failed",
        text="Partial carrier response.",
    )
    canceled = AgentResultRead(
        agent_message_id="agent-msg-1",
        agent_id="agent-1",
        status="canceled",
        text="Stopped before completion.",
    )

    once = ingestor.ingest(_run_state(), first)
    failed_update = ingestor.ingest(once, failed)
    canceled_update = ingestor.ingest(once, canceled)

    assert failed_update.facts == []
    assert canceled_update.facts == []


def test_ingest_artifact_summary_falls_back_to_description():
    result = AgentResultRead(
        agent_message_id="agent-msg-1",
        agent_id="agent-1",
        status="completed",
        artifacts=[
            {
                "artifact_id": "quote-1",
                "description": "Detailed quote description",
                "summary": " ",
            }
        ],
    )

    updated = AgentResultIngestor().ingest(_run_state(), result)

    assert updated.artifacts[0]["summary"] == "Detailed quote description"


def test_sparse_replay_preserves_legacy_artifact_without_source_metadata():
    state = _run_state(
        agent_outputs=[
            AgentOutputRecord(
                agent_message_id="agent-msg-1",
                agent_id="agent-1",
                status="completed",
                artifact_keys=["agent-msg-1:artifact_id:old"],
            ),
            AgentOutputRecord(
                agent_message_id="agent-msg-2",
                agent_id="agent-2",
                status="completed",
                artifact_keys=["agent-msg-2:artifact_id:other"],
            ),
        ],
        artifacts=[
            {
                "artifact_key": "agent-msg-1:artifact_id:old",
                "artifact_id": "old",
                "name": "Old quote",
            },
            {
                "artifact_key": "agent-msg-2:artifact_id:other",
                "artifact_id": "other",
                "name": "Other quote",
            },
        ],
    )
    result = AgentResultRead(
        agent_message_id="agent-msg-1",
        agent_id="agent-1",
        status="completed",
        artifacts=[],
    )

    updated = AgentResultIngestor().ingest(state, result)

    output = next(
        output
        for output in updated.agent_outputs
        if output.agent_message_id == "agent-msg-1"
    )
    assert updated is state
    assert output.artifact_keys == ["agent-msg-1:artifact_id:old"]
    assert [
        artifact["artifact_key"]
        for artifact in updated.artifacts
    ] == [
        "agent-msg-1:artifact_id:old",
        "agent-msg-2:artifact_id:other",
    ]


def test_sparse_replay_preserves_shared_artifact_key():
    shared_key = "shared:artifact_id:quote"
    state = _run_state(
        agent_outputs=[
            AgentOutputRecord(
                agent_message_id="agent-msg-1",
                agent_id="agent-1",
                status="completed",
                artifact_keys=[shared_key],
            ),
            AgentOutputRecord(
                agent_message_id="agent-msg-2",
                agent_id="agent-2",
                status="completed",
                artifact_keys=[shared_key],
            ),
        ],
        artifacts=[
            {
                "artifact_key": shared_key,
                "artifact_id": "quote",
                "name": "Shared quote",
            }
        ],
    )
    result = AgentResultRead(
        agent_message_id="agent-msg-1",
        agent_id="agent-1",
        status="completed",
        artifacts=[],
    )

    updated = AgentResultIngestor().ingest(state, result)

    output = next(
        output
        for output in updated.agent_outputs
        if output.agent_message_id == "agent-msg-1"
    )
    other_output = next(
        output
        for output in updated.agent_outputs
        if output.agent_message_id == "agent-msg-2"
    )
    assert updated is state
    assert output.artifact_keys == [shared_key]
    assert other_output.artifact_keys == [shared_key]
    assert updated.artifacts == [
        {
            "artifact_key": shared_key,
            "artifact_id": "quote",
            "name": "Shared quote",
        }
    ]
