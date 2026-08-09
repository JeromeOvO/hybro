from __future__ import annotations

import pytest

from common.dto import ExecutionRequest
from execution.idempotency import (
    IDEMPOTENCY_FINGERPRINT_VERSION,
    build_execution_request_fingerprint,
    execution_request_fingerprint_payload,
)


def _request(**overrides) -> ExecutionRequest:
    data = {
        "room_id": "room-1",
        "sender_id": "user-1",
        "sender_name": "Display Name",
        "message": {
            "room_id": "room-1",
            "message_id": "draft-1",
            "message_created_at": "2026-08-01T12:00:00Z",
            "message_type": "user",
            "related_message_id": "parent-1",
            "message_content": {"message_text": "hello"},
            "quote": {
                "text": " quoted content ",
                "source_message_id": "source-1",
                "source_kind": "agent",
                "sender_display_name": "Agent One",
                "source_agent_id": "agent-1",
            },
        },
        "attachments": [{"file_id": "file-1", "file_url": "https://old"}],
        "client_request_id": "request-1",
        "parent_message_id": "parent-1",
        "mode": "supervisor",
        "agent_scope": {"source": "mention", "agent_ids": ["agent-2", "agent-1"]},
    }
    data.update(overrides)
    return ExecutionRequest(**data)


def test_v2_fingerprint_normalizes_mention_order() -> None:
    first = _request()
    second = _request(
        agent_scope={"source": "mention", "agent_ids": ["agent-1", "agent-2"]}
    )
    assert IDEMPOTENCY_FINGERPRINT_VERSION == 2
    assert build_execution_request_fingerprint(
        first
    ) == build_execution_request_fingerprint(second)


@pytest.mark.parametrize(
    "change",
    [
        {"mode": "direct"},
        {"agent_scope": {"source": "room_default"}},
        {"agent_scope": {"source": "all_agents"}},
        {"agent_scope": {"source": "saved_group", "group_id": "group-1"}},
        {"agent_scope": {"source": "mention", "agent_ids": ["agent-3"]}},
        {"parent_message_id": "parent-2"},
    ],
)
def test_execution_contract_changes_affect_fingerprint(change) -> None:
    assert build_execution_request_fingerprint(_request()) != (
        build_execution_request_fingerprint(_request(**change))
    )


def test_saved_group_id_affects_fingerprint() -> None:
    first = _request(agent_scope={"source": "saved_group", "group_id": "group-1"})
    second = _request(agent_scope={"source": "saved_group", "group_id": "group-2"})
    assert build_execution_request_fingerprint(
        first
    ) != build_execution_request_fingerprint(second)


def test_server_owned_fields_do_not_affect_fingerprint() -> None:
    first = _request()
    message = first.model_dump(mode="json")["message"]
    message["message_id"] = "server-generated"
    message["message_created_at"] = "2030-01-01T00:00:00Z"
    second = _request(sender_name="Changed", message=message)
    assert build_execution_request_fingerprint(
        first
    ) == build_execution_request_fingerprint(second)


def test_v2_payload_contains_only_canonical_mode_and_scope() -> None:
    payload = execution_request_fingerprint_payload(_request())
    assert payload["mode"] == "supervisor"
    assert payload["agent_scope"] == {
        "source": "mention",
        "agent_ids": ["agent-1", "agent-2"],
    }
    for legacy in (
        "message_target_mode",
        "target_group",
        "target_group_id",
        "mentioned_agent_ids",
        "selected_agent_ids",
        "candidate_scope_mode",
        "candidate_scope_group_id",
    ):
        assert legacy not in payload
