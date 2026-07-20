from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from execution.orchestration.failure_classifier import classify_agent_failure
from execution.orchestration.outcome_evaluator import semantic_fact_map
from execution.orchestration.result_ingestor import (
    AgentResultIngestor,
    AgentResultRead,
    canonical_artifact_key,
    related_open_failure_for_dispatch_intent,
)
from models.orchestration import (
    AgentOutputRecord,
    BlockerRecord,
    DispatchIntent,
    OrchestrationRunState,
)


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
    assert updated.agent_outputs[0].status_message is None


def test_ingest_preserves_structured_interactive_metadata():
    result = AgentResultRead(
        agent_message_id="agent-msg-1",
        agent_id="agent-1",
        status="awaiting_input",
        interactive_state="auth-required",
        requires_auth=True,
        requires_policy=False,
    )

    updated = AgentResultIngestor().ingest(_run_state(), result)

    output = updated.agent_outputs[0]
    assert output.interactive_state == "auth-required"
    assert output.requires_auth is True
    assert output.requires_policy is False


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
    assert updated_twice.agent_outputs[0].status_message is None


def test_ingest_does_not_persist_remote_status_message_as_output_or_observation():
    private_prompt = "PRIVATE_SENTINEL_result_ingestor_status_message"
    result = AgentResultRead(
        agent_message_id="agent-msg-1",
        agent_id="agent-1",
        status="awaiting_input",
        text=None,
        status_message=private_prompt,
        a2a_task_id="task-1",
        a2a_context_id="ctx-1",
        interactive_state="input-required",
    )

    updated = AgentResultIngestor().ingest(_run_state(), result)
    serialized = json.dumps(updated.model_dump(mode="json"), sort_keys=True)

    assert updated.agent_outputs[0].status_message is None
    assert updated.open_failures[0].error_message == "Agent requested additional input."
    assert "Agent requested additional input." in serialized
    assert private_prompt not in serialized


def test_ingest_failed_runtime_error_sanitizes_output_and_failure_shadow():
    private_exception = (
        "PRIVATE_EXCEPTION_SENTINEL_result_ingestor includes "
        "PRIVATE_TASK_SENTINEL_dispatch_body"
    )
    result = AgentResultRead(
        agent_message_id="agent-msg-1",
        agent_id="agent-1",
        status="failed",
        text=private_exception,
        error=private_exception,
        status_message=private_exception,
    )

    updated = AgentResultIngestor().ingest(_run_state(), result)
    serialized = json.dumps(updated.model_dump(mode="json"), sort_keys=True)

    output = updated.agent_outputs[0]
    assert output.status == "failed"
    assert output.text is None
    assert output.error == "Agent processing failed"
    assert output.status_message is None
    assert len(updated.open_failures) == 1
    failure = updated.open_failures[0]
    assert failure.error_code == "agent_execution_failed"
    assert failure.error_message == "Agent processing failed"
    assert failure.recoverable is True
    assert "Agent processing failed" in serialized
    assert "PRIVATE_EXCEPTION_SENTINEL_result_ingestor" not in serialized
    assert "PRIVATE_TASK_SENTINEL_dispatch_body" not in serialized


def test_ingest_failed_result_rejects_remote_error_code_from_public_state():
    private_error_code = "private_customer_denied"
    result = AgentResultRead(
        agent_message_id="agent-msg-1",
        agent_id="agent-1",
        status="failed",
        error="Remote agent denied the request.",
        error_code=private_error_code,
    )

    updated = AgentResultIngestor().ingest(_run_state(), result)
    serialized = json.dumps(updated.model_dump(mode="json"), sort_keys=True)

    output = updated.agent_outputs[0]
    assert output.error == "Agent processing failed"
    assert updated.open_failures[0].error_code == "agent_execution_failed"
    assert private_error_code not in serialized


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
            "fact_id": "agent-msg-1:text_evidence",
            "kind": "agent_text_evidence",
            "semantic_key": "agent_text_evidence:agent-msg-1",
            "value": "The agent finished.",
            "source_agent_message_id": "agent-msg-1",
            "source_agent_id": "agent-1",
            "evidence_refs": ["agent-msg-1", "agent-msg-1:text_or_status"],
            "trusted_for_blocker_keys": False,
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
            "fact_id": "agent-msg-1:text_evidence",
            "kind": "agent_text_evidence",
            "semantic_key": "agent_text_evidence:agent-msg-1",
            "value": "Carrier A can quote the risk.",
            "source_agent_message_id": "agent-msg-1",
            "source_agent_id": "agent-1",
            "evidence_refs": ["agent-msg-1", "agent-msg-1:text_or_status"],
            "trusted_for_blocker_keys": False,
        },
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

    assert len(twice.facts) == 2
    assert {fact["fact_id"] for fact in twice.facts} == {
        "agent-msg-1:text",
        "agent-msg-1:text_evidence",
    }


def test_ingest_does_not_preserve_remote_prompt_as_text_evidence():
    private_prompt = "Need the requested limit before continuing."
    updated = AgentResultIngestor().ingest(
        _run_state(),
        AgentResultRead(
            agent_message_id="agent-msg-1",
            agent_id="agent-1",
            status="awaiting_input",
            status_message=private_prompt,
        ),
    )

    assert updated.facts == []
    assert private_prompt not in json.dumps(
        updated.model_dump(mode="json"),
        sort_keys=True,
    )


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


def test_ingest_narrative_only_artifact_preserves_untrusted_text_evidence():
    result = AgentResultRead(
        agent_message_id="agent-msg-narrative",
        agent_id="agent-1",
        status="completed",
        text="The attached narrative explains the partial result.",
        artifacts=[
            {
                "artifact_id": "narrative",
                "name": "Narrative",
                "parts": [
                    {
                        "kind": "text",
                        "text": "No structured data is present.",
                    }
                ],
            }
        ],
    )

    updated = AgentResultIngestor().ingest(_run_state(), result)

    evidence = next(
        fact
        for fact in updated.facts
        if fact.get("kind") == "agent_text_evidence"
    )
    assert evidence == {
        "fact_id": "agent-msg-narrative:text_evidence",
        "kind": "agent_text_evidence",
        "semantic_key": "agent_text_evidence:agent-msg-narrative",
        "value": "The attached narrative explains the partial result.",
        "source_agent_message_id": "agent-msg-narrative",
        "source_agent_id": "agent-1",
        "evidence_refs": [
            "agent-msg-narrative",
            "agent-msg-narrative:text_or_status",
        ],
        "trusted_for_blocker_keys": False,
    }
    assert not any(fact.get("kind") == "agent_text" for fact in updated.facts)
    assert semantic_fact_map(updated.facts) == {}


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

    assert len(twice.facts) == 2
    facts_by_id = {fact["fact_id"]: fact for fact in twice.facts}
    assert facts_by_id["agent-msg-1:text"] == {
        "fact_id": "agent-msg-1:text",
        "source_agent_message_id": "agent-msg-1",
        "source_agent_id": "agent-2",
        "kind": "agent_text",
        "text": "Carrier B declined the risk.",
    }
    assert facts_by_id["agent-msg-1:text_evidence"] == {
        "fact_id": "agent-msg-1:text_evidence",
        "kind": "agent_text_evidence",
        "semantic_key": "agent_text_evidence:agent-msg-1",
        "value": "Carrier B declined the risk.",
        "source_agent_message_id": "agent-msg-1",
        "source_agent_id": "agent-2",
        "evidence_refs": ["agent-msg-1", "agent-msg-1:text_or_status"],
        "trusted_for_blocker_keys": False,
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

def test_reingesting_failed_or_canceled_text_removes_remote_evidence():
    ingestor = AgentResultIngestor()
    failed_text = "PRIVATE_SENTINEL_failed_partial_response"
    canceled_text = "PRIVATE_SENTINEL_canceled_partial_response"
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
        text=failed_text,
    )
    canceled = AgentResultRead(
        agent_message_id="agent-msg-1",
        agent_id="agent-1",
        status="canceled",
        text=canceled_text,
    )

    once = ingestor.ingest(_run_state(), first)
    failed_update = ingestor.ingest(once, failed)
    canceled_update = ingestor.ingest(once, canceled)

    assert failed_update.facts == []
    assert canceled_update.facts == []
    assert failed_text not in failed_update.model_dump_json()
    assert canceled_text not in canceled_update.model_dump_json()


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


def test_ingest_failed_attachment_preflight_opens_recoverable_failure():
    result = AgentResultRead(
        agent_message_id="agent-msg-1",
        agent_id="agent-1",
        status="failed",
        text="",
        error="Agent does not accept the uploaded file type for: report.pdf (application/pdf).",
        status_message="agent_does_not_accept_file_type",
    )

    updated = AgentResultIngestor().ingest(_run_state(), result)

    assert len(updated.open_failures) == 1
    failure = updated.open_failures[0]
    assert failure.source == "a2a_adapter"
    assert failure.agent_id == "agent-1"
    assert failure.agent_message_id == "agent-msg-1"
    assert failure.error_code == "agent_does_not_accept_file_type"
    assert failure.recoverable is True
    assert failure.recovery_hints == ["retry_without_unsupported_attachments"]


def test_ingest_projection_bind_failure_uses_available_refs_recovery():
    result = AgentResultRead(
        agent_message_id="agent-msg-1",
        agent_id="agent-1",
        status="failed",
        text="",
        error=(
            "Attachment projection unavailable for "
            "report.pdf (application/pdf)."
        ),
        status_message="attachment_projection_unavailable",
    )

    updated = AgentResultIngestor().ingest(_run_state(), result)

    failure = updated.open_failures[0]
    assert failure.error_code == "attachment_projection_unavailable"
    assert failure.recoverable is True
    assert failure.recovery_hints == ["retry_with_available_refs"]


def test_ingest_later_success_resolves_only_matching_open_failure():
    ingestor = AgentResultIngestor()
    failed = ingestor.ingest(
        _run_state(
            dispatch_intents=[
                DispatchIntent(
                    step_id="step-1",
                    step_target_id="step-1:target-1",
                    dispatch_intent_id="intent-1",
                    planned_agent_message_id="agent-msg-1",
                    agent_id="agent-1",
                    task="Task 1",
                    task_hash="hash-1",
                ),
                DispatchIntent(
                    step_id="step-2",
                    step_target_id="step-2:target-1",
                    dispatch_intent_id="intent-2",
                    planned_agent_message_id="agent-msg-2",
                    agent_id="agent-1",
                    task="Task 2",
                    task_hash="hash-2",
                ),
            ]
        ),
        AgentResultRead(
            agent_message_id="agent-msg-1",
            agent_id="agent-1",
            status="failed",
            error="Agent does not accept the uploaded file type for: report.pdf (application/pdf).",
            status_message="agent_does_not_accept_file_type",
        ),
    )
    failed = ingestor.ingest(
        failed,
        AgentResultRead(
            agent_message_id="agent-msg-2",
            agent_id="agent-1",
            status="failed",
            error="Agent timed out while processing the request.",
            status_message="timeout",
        ),
    )

    recovered = ingestor.ingest(
        failed,
        AgentResultRead(
            agent_message_id="agent-msg-1",
            agent_id="agent-1",
            status="completed",
            text="Recovered answer.",
        ),
    )

    first_failure, second_failure = recovered.open_failures
    assert first_failure.dispatch_intent_id == "intent-1"
    assert first_failure.status == "resolved"
    assert first_failure.resolved_by_agent_message_id == "agent-msg-1"
    assert second_failure.dispatch_intent_id == "intent-2"
    assert second_failure.status == "open"
    assert second_failure.resolved_by_agent_message_id is None


def test_input_required_result_creates_recoverable_open_failure():
    state = _run_state()
    ingestor = AgentResultIngestor()

    updated = ingestor.ingest(
        state,
        AgentResultRead(
            agent_message_id="agent-msg-1",
            agent_id="agent-1",
            status="awaiting_input",
            text=None,
            status_message="Please provide the selected application text.",
            a2a_task_id="task-1",
            a2a_context_id="ctx-1",
        ),
    )

    assert updated.open_failures[0].error_code == "agent_input_required"
    assert updated.open_failures[0].recoverable is True
    assert updated.open_failures[0].recovery_hints == [
        "retry_with_available_resource_refs",
        "retry_after_resource_projection",
        "ask_user_if_missing",
    ]
    assert updated.open_failures[0].error_message == "Agent requested additional input."


def test_ingest_same_error_for_different_dispatches_creates_distinct_open_failures():
    ingestor = AgentResultIngestor()
    state = _run_state(
        dispatch_intents=[
            DispatchIntent(
                step_id="step-1",
                step_target_id="step-1:target-1",
                dispatch_intent_id="intent-1",
                planned_agent_message_id="agent-msg-1",
                agent_id="agent-1",
                task="Task 1",
                task_hash="hash-1",
            ),
            DispatchIntent(
                step_id="step-2",
                step_target_id="step-2:target-1",
                dispatch_intent_id="intent-2",
                planned_agent_message_id="agent-msg-2",
                agent_id="agent-1",
                task="Task 2",
                task_hash="hash-2",
            ),
        ]
    )

    once = ingestor.ingest(
        state,
        AgentResultRead(
            agent_message_id="agent-msg-1",
            agent_id="agent-1",
            status="failed",
            error="Agent does not accept the uploaded file type for: report.pdf (application/pdf).",
            status_message="agent_does_not_accept_file_type",
        ),
    )
    twice = ingestor.ingest(
        once,
        AgentResultRead(
            agent_message_id="agent-msg-2",
            agent_id="agent-1",
            status="failed",
            error="Agent does not accept the uploaded file type for: report.pdf (application/pdf).",
            status_message="agent_does_not_accept_file_type",
        ),
    )

    assert len(twice.open_failures) == 2
    assert [failure.dispatch_intent_id for failure in twice.open_failures] == [
        "intent-1",
        "intent-2",
    ]
    assert twice.open_failures[0].fingerprint != twice.open_failures[1].fingerprint


def test_ingest_attachment_free_retry_resolves_only_related_open_failure():
    ingestor = AgentResultIngestor()
    state = _run_state(
        dispatch_intents=[
            DispatchIntent(
                step_id="step-1",
                step_target_id="step-1:target-1",
                dispatch_intent_id="intent-1",
                planned_agent_message_id="agent-msg-1",
                agent_id="agent-1",
                task="Underwrite submission A",
                task_hash="hash-a",
                artifact_refs=[
                    {
                        "kind": "artifact",
                        "ref_id": "broker-msg:artifact_id:submission-a",
                    }
                ],
                attachment_refs=[
                    {"kind": "attachment", "ref_id": "file-a"},
                ],
            ),
            DispatchIntent(
                step_id="step-2",
                step_target_id="step-2:target-1",
                dispatch_intent_id="intent-2",
                planned_agent_message_id="agent-msg-2",
                agent_id="agent-1",
                task="Underwrite submission B",
                task_hash="hash-b",
                artifact_refs=[
                    {
                        "kind": "artifact",
                        "ref_id": "broker-msg:artifact_id:submission-b",
                    }
                ],
                attachment_refs=[
                    {"kind": "attachment", "ref_id": "file-b"},
                ],
            ),
            DispatchIntent(
                step_id="step-3",
                step_target_id="step-3:target-1",
                dispatch_intent_id="intent-3",
                planned_agent_message_id="agent-msg-3",
                agent_id="agent-1",
                task="Underwrite submission A from broker artifact only",
                task_hash="hash-a",
                artifact_refs=[
                    {
                        "kind": "artifact",
                        "ref_id": "broker-msg:artifact_id:submission-a",
                    }
                ],
                attachment_refs=[],
            ),
        ]
    )

    state = ingestor.ingest(
        state,
        AgentResultRead(
            agent_message_id="agent-msg-1",
            agent_id="agent-1",
            status="failed",
            error="Agent does not accept the uploaded file type for: a.pdf (application/pdf).",
            status_message="agent_does_not_accept_file_type",
        ),
    )
    state = ingestor.ingest(
        state,
        AgentResultRead(
            agent_message_id="agent-msg-2",
            agent_id="agent-1",
            status="failed",
            error="Agent does not accept the uploaded file type for: b.pdf (application/pdf).",
            status_message="agent_does_not_accept_file_type",
        ),
    )

    recovered = ingestor.ingest(
        state,
        AgentResultRead(
            agent_message_id="agent-msg-3",
            agent_id="agent-1",
            status="completed",
            text="Recovered answer for submission A.",
        ),
    )

    first_failure, second_failure = recovered.open_failures
    assert first_failure.dispatch_intent_id == "intent-1"
    assert first_failure.status == "resolved"
    assert first_failure.resolved_by_agent_message_id == "agent-msg-3"
    assert second_failure.dispatch_intent_id == "intent-2"
    assert second_failure.status == "open"
    assert second_failure.resolved_by_agent_message_id is None


def test_ingest_cross_agent_retry_resolves_unique_lineage_failure():
    ingestor = AgentResultIngestor()
    state = _run_state(
        dispatch_intents=[
            DispatchIntent(
                step_id="step-1",
                step_target_id="step-1:target-1",
                dispatch_intent_id="intent-1",
                planned_agent_message_id="agent-msg-1",
                agent_id="agent-1",
                task="Underwrite submission A",
                task_hash="hash-a",
                artifact_refs=[
                    {
                        "kind": "artifact",
                        "ref_id": "broker-msg:artifact_id:submission-a",
                    }
                ],
            ),
            DispatchIntent(
                step_id="step-2",
                step_target_id="step-2:target-1",
                dispatch_intent_id="intent-2",
                planned_agent_message_id="agent-msg-2",
                agent_id="agent-2",
                task="Underwrite submission A",
                task_hash="hash-a",
                artifact_refs=[
                    {
                        "kind": "artifact",
                        "ref_id": "broker-msg:artifact_id:submission-a",
                    }
                ],
            ),
        ]
    )
    failed = ingestor.ingest(
        state,
        AgentResultRead(
            agent_message_id="agent-msg-1",
            agent_id="agent-1",
            status="failed",
            error="Agent rate limit exceeded.",
            status_message="rate_limited",
        ),
    )

    recovered = ingestor.ingest(
        failed,
        AgentResultRead(
            agent_message_id="agent-msg-2",
            agent_id="agent-2",
            status="completed",
            text="Recovered with alternate underwriter.",
        ),
    )

    assert recovered.open_failures[0].status == "resolved"
    assert recovered.open_failures[0].resolved_by_agent_message_id == "agent-msg-2"


def test_ingest_unrelated_cross_agent_success_keeps_failure_open():
    ingestor = AgentResultIngestor()
    state = _run_state(
        dispatch_intents=[
            DispatchIntent(
                step_id="step-1",
                step_target_id="step-1:target-1",
                dispatch_intent_id="intent-1",
                planned_agent_message_id="agent-msg-1",
                agent_id="agent-1",
                task="Underwrite submission A",
                task_hash="hash-a",
                artifact_refs=[
                    {
                        "kind": "artifact",
                        "ref_id": "broker-msg:artifact_id:submission-a",
                    }
                ],
            ),
            DispatchIntent(
                step_id="step-2",
                step_target_id="step-2:target-1",
                dispatch_intent_id="intent-2",
                planned_agent_message_id="agent-msg-2",
                agent_id="agent-2",
                task="Underwrite submission B",
                task_hash="hash-b",
                artifact_refs=[
                    {
                        "kind": "artifact",
                        "ref_id": "broker-msg:artifact_id:submission-b",
                    }
                ],
            ),
        ]
    )
    failed = ingestor.ingest(
        state,
        AgentResultRead(
            agent_message_id="agent-msg-1",
            agent_id="agent-1",
            status="failed",
            error="Agent rate limit exceeded.",
            status_message="rate_limited",
        ),
    )

    recovered = ingestor.ingest(
        failed,
        AgentResultRead(
            agent_message_id="agent-msg-2",
            agent_id="agent-2",
            status="completed",
            text="Completed unrelated submission B.",
        ),
    )

    assert recovered.open_failures[0].status == "open"
    assert recovered.open_failures[0].resolved_by_agent_message_id is None


def test_failure_hints_only_advertise_supported_recovery_paths():
    rate_limited = classify_agent_failure(
        agent_id="agent-1",
        agent_message_id="agent-msg-1",
        error="Agent rate limit exceeded.",
        status_message="rate_limited",
    )
    generic = classify_agent_failure(
        agent_id="agent-1",
        agent_message_id="agent-msg-2",
        error="Agent execution failed.",
        status_message=None,
    )

    assert rate_limited is not None
    assert rate_limited.recovery_hints == ["retry_different_agent"]
    assert generic is not None
    assert generic.recovery_hints == ["retry_with_refined_task"]


def test_ingest_unrelated_attachment_free_success_does_not_resolve_lone_attachment_failure():
    ingestor = AgentResultIngestor()
    state = _run_state(
        dispatch_intents=[
            DispatchIntent(
                step_id="step-1",
                step_target_id="step-1:target-1",
                dispatch_intent_id="intent-1",
                planned_agent_message_id="agent-msg-1",
                agent_id="agent-1",
                task="Underwrite submission A",
                task_hash="hash-a",
                artifact_refs=[
                    {
                        "kind": "artifact",
                        "ref_id": "broker-msg:artifact_id:submission-a",
                    }
                ],
                attachment_refs=[
                    {"kind": "attachment", "ref_id": "file-a"},
                ],
            ),
            DispatchIntent(
                step_id="step-2",
                step_target_id="step-2:target-1",
                dispatch_intent_id="intent-2",
                planned_agent_message_id="agent-msg-2",
                agent_id="agent-1",
                task="Underwrite unrelated submission B from broker artifact only",
                task_hash="hash-b",
                artifact_refs=[
                    {
                        "kind": "artifact",
                        "ref_id": "broker-msg:artifact_id:submission-b",
                    }
                ],
                attachment_refs=[],
            ),
        ]
    )

    failed = ingestor.ingest(
        state,
        AgentResultRead(
            agent_message_id="agent-msg-1",
            agent_id="agent-1",
            status="failed",
            error="Agent does not accept the uploaded file type for: a.pdf (application/pdf).",
            status_message="agent_does_not_accept_file_type",
        ),
    )

    recovered = ingestor.ingest(
        failed,
        AgentResultRead(
            agent_message_id="agent-msg-2",
            agent_id="agent-1",
            status="completed",
            text="Completed unrelated submission B.",
        ),
    )

    assert len(recovered.open_failures) == 1
    assert recovered.open_failures[0].dispatch_intent_id == "intent-1"
    assert recovered.open_failures[0].status == "open"
    assert recovered.open_failures[0].resolved_by_agent_message_id is None


def test_ingest_same_task_text_with_different_refs_does_not_resolve_open_failure():
    ingestor = AgentResultIngestor()
    task_text = "Underwrite submission"
    state = _run_state(
        dispatch_intents=[
            DispatchIntent(
                step_id="step-1",
                step_target_id="step-1:target-1",
                dispatch_intent_id="intent-1",
                planned_agent_message_id="agent-msg-1",
                agent_id="agent-1",
                task=task_text,
                task_hash="hash-shared-task-text",
                artifact_refs=[
                    {
                        "kind": "artifact",
                        "ref_id": "broker-msg:artifact_id:submission-a",
                    }
                ],
                attachment_refs=[
                    {"kind": "attachment", "ref_id": "file-a"},
                ],
            ),
            DispatchIntent(
                step_id="step-2",
                step_target_id="step-2:target-1",
                dispatch_intent_id="intent-2",
                planned_agent_message_id="agent-msg-2",
                agent_id="agent-1",
                task=task_text,
                task_hash="hash-shared-task-text",
                artifact_refs=[
                    {
                        "kind": "artifact",
                        "ref_id": "broker-msg:artifact_id:submission-b",
                    }
                ],
                attachment_refs=[],
            ),
        ]
    )

    failed = ingestor.ingest(
        state,
        AgentResultRead(
            agent_message_id="agent-msg-1",
            agent_id="agent-1",
            status="failed",
            error="Agent does not accept the uploaded file type for: a.pdf (application/pdf).",
            status_message="agent_does_not_accept_file_type",
        ),
    )

    recovered = ingestor.ingest(
        failed,
        AgentResultRead(
            agent_message_id="agent-msg-2",
            agent_id="agent-1",
            status="completed",
            text="Completed different submission with the same task text.",
        ),
    )

    assert len(recovered.open_failures) == 1
    assert recovered.open_failures[0].dispatch_intent_id == "intent-1"
    assert recovered.open_failures[0].status == "open"
    assert recovered.open_failures[0].resolved_by_agent_message_id is None


def test_related_open_failure_for_dispatch_intent_requires_more_than_task_text_match():
    task_text = "Underwrite submission"
    failed_intent = DispatchIntent(
        step_id="step-1",
        step_target_id="step-1:target-1",
        dispatch_intent_id="intent-1",
        planned_agent_message_id="agent-msg-1",
        agent_id="agent-1",
        task=task_text,
        task_hash="hash-shared-task-text",
        artifact_refs=[
            {
                "kind": "artifact",
                "ref_id": "broker-msg:artifact_id:submission-a",
            }
        ],
        attachment_refs=[
            {"kind": "attachment", "ref_id": "file-a"},
        ],
    )
    retry_intent = DispatchIntent(
        step_id="step-2",
        step_target_id="step-2:target-1",
        dispatch_intent_id="intent-2",
        planned_agent_message_id="agent-msg-2",
        agent_id="agent-1",
        task=task_text,
        task_hash="hash-shared-task-text",
        artifact_refs=[
            {
                "kind": "artifact",
                "ref_id": "broker-msg:artifact_id:submission-b",
            }
        ],
        attachment_refs=[],
    )
    state = _run_state(
        dispatch_intents=[failed_intent, retry_intent],
    )
    failed_state = AgentResultIngestor().ingest(
        state,
        AgentResultRead(
            agent_message_id="agent-msg-1",
            agent_id="agent-1",
            status="failed",
            error="Agent does not accept the uploaded file type for: a.pdf (application/pdf).",
            status_message="agent_does_not_accept_file_type",
        ),
    )

    assert (
        related_open_failure_for_dispatch_intent(
            failed_state.open_failures,
            retry_intent=retry_intent,
            dispatch_intents=failed_state.dispatch_intents,
        )
        is None
    )


def test_ingest_replan_success_resolves_related_timeout_without_broad_same_agent_fallback():
    ingestor = AgentResultIngestor()
    state = _run_state(
        dispatch_intents=[
            DispatchIntent(
                step_id="step-1",
                step_target_id="step-1:target-1",
                dispatch_intent_id="intent-1",
                planned_agent_message_id="agent-msg-1",
                agent_id="agent-1",
                task="Summarize claim packet",
                task_hash="hash-shared",
                artifact_refs=[
                    {"kind": "artifact", "ref_id": "artifact-claim"},
                ],
            ),
            DispatchIntent(
                step_id="step-2",
                step_target_id="step-2:target-1",
                dispatch_intent_id="intent-2",
                planned_agent_message_id="agent-msg-2",
                agent_id="agent-1",
                task="Summarize unrelated packet",
                task_hash="hash-other",
                artifact_refs=[
                    {"kind": "artifact", "ref_id": "artifact-other"},
                ],
            ),
            DispatchIntent(
                step_id="step-3",
                step_target_id="step-3:target-1",
                dispatch_intent_id="intent-3",
                planned_agent_message_id="agent-msg-3",
                agent_id="agent-1",
                task="Retry claim packet with tighter prompt",
                task_hash="hash-shared",
                artifact_refs=[
                    {"kind": "artifact", "ref_id": "artifact-claim"},
                ],
            ),
        ]
    )

    state = ingestor.ingest(
        state,
        AgentResultRead(
            agent_message_id="agent-msg-1",
            agent_id="agent-1",
            status="failed",
            error="Agent timed out while processing the request.",
            status_message="timeout",
        ),
    )
    state = ingestor.ingest(
        state,
        AgentResultRead(
            agent_message_id="agent-msg-2",
            agent_id="agent-1",
            status="failed",
            error="Agent timed out while processing the unrelated request.",
            status_message="timeout",
        ),
    )

    recovered = ingestor.ingest(
        state,
        AgentResultRead(
            agent_message_id="agent-msg-3",
            agent_id="agent-1",
            status="completed",
            text="Recovered timeout response.",
        ),
    )

    first_failure, second_failure = recovered.open_failures
    assert first_failure.dispatch_intent_id == "intent-1"
    assert first_failure.status == "resolved"
    assert first_failure.resolved_by_agent_message_id == "agent-msg-3"
    assert second_failure.dispatch_intent_id == "intent-2"
    assert second_failure.status == "open"


def test_ingest_projects_agent_observation_unknowns_and_candidate_blockers():
    state = _run_state()
    updated = AgentResultIngestor().ingest(
        state,
        AgentResultRead(
            agent_message_id="agent-msg-1",
            agent_id="agent-1",
            status="completed",
            artifacts=[
                {
                    "artifact_id": "submission",
                    "name": "submission",
                    "parts": [
                        {
                            "kind": "data",
                            "data": {
                                "client": {"name": "Example Inc", "industry": None},
                                "missing_fields": ["client.industry"],
                            },
                        }
                    ],
                }
            ],
        ),
    )

    assert [item.key for item in updated.unknowns] == [
        "agent_missing:agent-1:client.industry"
    ]
    assert [item.key for item in updated.blockers] == [
        "agent_blocker:agent-1:client.industry"
    ]
    blocker = updated.blockers[0]
    assert blocker.source == "agent"
    assert blocker.claimed_user_only is False
    assert blocker.validated_user_only is False
    assert blocker.validation_status == "candidate"
    assert updated.facts[0]["kind"] == "agent_observation"
    assert updated.facts[0]["semantic_key"] == (
        "agent_observation:agent-msg-1:submission:client.name"
    )


def test_ingest_ignores_optional_nulls_without_explicit_missing_signal():
    updated = AgentResultIngestor().ingest(
        _run_state(),
        AgentResultRead(
            agent_message_id="agent-msg-1",
            agent_id="agent-1",
            status="completed",
            artifacts=[
                {
                    "artifact_id": "submission",
                    "name": "submission",
                    "parts": [
                        {
                            "kind": "data",
                            "data": {
                                "optional_note": None,
                                "optional_endorsement": None,
                                "optional_reference": None,
                            },
                        }
                    ],
                }
            ],
        ),
    )

    assert updated.facts == []
    assert updated.unknowns == []
    assert updated.blockers == []


def test_ingest_sanitizes_external_blocker_validation_flags():
    updated = AgentResultIngestor().ingest(
        _run_state(),
        AgentResultRead(
            agent_message_id="agent-msg-1",
            agent_id="agent-1",
            status="completed",
            artifacts=[
                {
                    "artifact_id": "artifact-1",
                    "name": "submission",
                    "blockers": [
                        {
                            "key": "external-blocker",
                            "description": "Agent says the user must answer.",
                            "blocked_output_keys": ["quote"],
                            "source": "agent",
                            "claimed_user_only": True,
                            "validated_user_only": True,
                            "validation_status": "validated",
                        }
                    ],
                    "parts": [{"kind": "data", "data": {"value": 1}}],
                }
            ],
        ),
    )

    external = next(item for item in updated.blockers if item.key == "external-blocker")
    assert external.claimed_user_only is False
    assert external.validated_user_only is False
    assert external.validation_status == "candidate"
    assert external.evidence_refs == ["agent-msg-1:artifact_id:artifact-1"]


def test_ingest_does_not_downgrade_existing_validated_blocker_on_replay():
    state = _run_state()
    state.blockers = [
        BlockerRecord(
            key="agent_blocker:agent-1:client.industry",
            description="Agent reported missing input: client.industry",
            blocked_output_keys=["broker_submission"],
            source="agent",
            evidence_refs=["agent-msg-1:artifact_id:submission"],
            claimed_user_only=True,
            validated_user_only=True,
            validation_status="validated",
            status="open",
        )
    ]

    updated = AgentResultIngestor().ingest(
        state,
        AgentResultRead(
            agent_message_id="agent-msg-1",
            agent_id="agent-1",
            status="completed",
            artifacts=[
                {
                    "artifact_id": "submission",
                    "name": "submission",
                    "parts": [
                        {
                            "kind": "data",
                            "data": {"missing_fields": ["client.industry"]},
                        }
                    ],
                }
            ],
        ),
    )

    blocker = updated.blockers[0]
    assert blocker.validation_status == "validated"
    assert blocker.validated_user_only is True
    assert blocker.claimed_user_only is True


def test_ingest_exact_replay_preserves_resolved_validated_blocker():
    state = _run_state(
        blockers=[
            BlockerRecord(
                key="agent_blocker:agent-1:client.industry",
                description="Agent reported missing input: client.industry",
                blocked_output_keys=["broker_submission"],
                source="agent",
                evidence_refs=["agent-msg-1:artifact_id:submission", "hitl-fact-1"],
                claimed_user_only=True,
                validated_user_only=True,
                validation_status="validated",
                status="resolved",
            )
        ]
    )

    updated = AgentResultIngestor().ingest(
        state,
        AgentResultRead(
            agent_message_id="agent-msg-1",
            agent_id="agent-1",
            status="completed",
            artifacts=[
                {
                    "artifact_id": "submission",
                    "name": "submission",
                    "parts": [
                        {
                            "kind": "data",
                            "data": {"missing_fields": ["client.industry"]},
                        }
                    ],
                }
            ],
        ),
    )

    blocker = updated.blockers[0]
    assert blocker.status == "resolved"
    assert blocker.validation_status == "validated"
    assert blocker.validated_user_only is True


def test_ingest_new_evidence_reopens_resolved_agent_blocker_as_candidate():
    state = _run_state(
        blockers=[
            BlockerRecord(
                key="agent_blocker:agent-1:client.industry",
                description="Agent reported missing input: client.industry",
                blocked_output_keys=["broker_submission"],
                source="agent",
                evidence_refs=["agent-msg-1:artifact_id:submission", "hitl-fact-1"],
                claimed_user_only=True,
                validated_user_only=True,
                validation_status="validated",
                status="resolved",
            )
        ]
    )

    updated = AgentResultIngestor().ingest(
        state,
        AgentResultRead(
            agent_message_id="agent-msg-2",
            agent_id="agent-1",
            status="completed",
            artifacts=[
                {
                    "artifact_id": "submission",
                    "name": "submission",
                    "parts": [
                        {
                            "kind": "data",
                            "data": {"missing_fields": ["client.industry"]},
                        }
                    ],
                }
            ],
        ),
    )

    blocker = updated.blockers[0]
    assert blocker.status == "open"
    assert blocker.validation_status == "candidate"
    assert blocker.claimed_user_only is False
    assert blocker.validated_user_only is False
    assert blocker.evidence_refs == [
        "agent-msg-1:artifact_id:submission",
        "agent-msg-2:artifact_id:submission",
        "hitl-fact-1",
    ]
