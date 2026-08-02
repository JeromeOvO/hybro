from __future__ import annotations

import pytest

from common.dto import ExecutionRequest
from execution.idempotency import (
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
            "message_id": "server-draft-1",
            "message_created_at": "2026-08-01T12:00:00Z",
            "message_type": "user",
            "related_message_id": "parent-1",
            "message_content": {
                "message_text": "hello",
                "attachments": [
                    {
                        "file_id": "file-1",
                        "file_url": "https://files.example/old",
                        "file_name": "resolved.txt",
                        "size_bytes": 10,
                        "mime_type": "text/plain",
                    }
                ],
                "content_summary": {"attachment_count": 1},
            },
            "quote_id": "server-quote-id",
            "quote": {
                "text": " quoted content ",
                "source_message_id": "source-1",
                "source_kind": "agent",
                "sender_display_name": "Agent One",
                "source_agent_id": "agent-1",
            },
            "extend_info": {
                "quoted_text": "quoted content",
                "quoted_sender_name": "Agent One",
                "quote_id": "server-quote-id",
            },
        },
        "attachments": [{"file_id": "file-1", "file_url": "https://files.example/old"}],
        "client_request_id": "request-1",
        "target_group": "room_team",
        "message_target_mode": "room_default",
        "mentioned_agent_ids": ["agent-2", "agent-1"],
        "selected_agent_ids": ["agent-3", "agent-1"],
        "candidate_scope_mode": "explicit_selection",
        "candidate_scope_group_id": "group-1",
        "parent_message_id": "parent-1",
        "mode": "supervisor",
    }
    data.update(overrides)
    return ExecutionRequest(**data)


def _legacy_quote_request(extend_info: dict) -> ExecutionRequest:
    message = _request().model_dump(mode="json")["message"]
    message["quote"] = None
    message["extend_info"] = extend_info
    return _request(message=message)


def test_fingerprint_is_stable_across_dict_and_id_set_order():
    first = _request()
    mutable_message = first.model_dump(mode="json")["message"]
    reordered_message = {
        key: value for key, value in reversed(list(mutable_message.items()))
    }
    second = _request(
        message=reordered_message,
        mentioned_agent_ids=["agent-1", "agent-2"],
        selected_agent_ids=["agent-1", "agent-3"],
    )

    assert build_execution_request_fingerprint(first) == (
        build_execution_request_fingerprint(second)
    )


def test_fingerprint_excludes_generated_ids_timestamps_file_urls_and_sender_name():
    first = _request()
    changed = first.model_dump(mode="json")["message"]
    changed["message_id"] = "another-server-message-id"
    changed["message_created_at"] = "2030-01-01T00:00:00Z"
    changed["quote_id"] = "another-server-quote-id"
    changed["extend_info"] = {
        **changed["extend_info"],
        "quote_id": "another-server-quote-id",
    }
    changed["message_content"] = {
        **changed["message_content"],
        "attachments": [
            {
                **changed["message_content"]["attachments"][0],
                "file_url": "https://files.example/new",
                "file_name": "server-name-changed.txt",
                "size_bytes": 999,
                "mime_type": "application/octet-stream",
            }
        ],
    }
    second = _request(
        sender_name="Changed Display Name",
        message=changed,
        attachments=[{"file_id": "file-1", "file_url": "https://files.example/new"}],
    )

    assert build_execution_request_fingerprint(first) == (
        build_execution_request_fingerprint(second)
    )


def test_server_owned_and_unused_request_fields_do_not_affect_fingerprint():
    first = _request(message_text="unused fallback", target_agent_ids=["unused-1"])
    changed_message = first.model_dump(mode="json")["message"]
    changed_message.update(
        {
            "message_type": "agent",
            "agent_id": "spoofed-agent",
            "run_id": "spoofed-run",
            "processing_claimed_at": "2026-08-01T00:00:00Z",
            "step_number": 99,
            "task_content": "spoofed task",
        }
    )
    changed_message["extend_info"] = {
        **changed_message["extend_info"],
        "turn_completion_kind": "synthesis",
        "custom_internal": "spoofed",
    }
    second = _request(
        message=changed_message,
        message_text="another unused fallback",
        target_agent_ids=["unused-2"],
    )

    assert build_execution_request_fingerprint(first) == (
        build_execution_request_fingerprint(second)
    )


def test_implicit_candidate_scope_default_matches_explicit_effective_mode():
    implicit = _request(candidate_scope_mode=None)
    explicit = _request(candidate_scope_mode="explicit_selection")

    assert build_execution_request_fingerprint(implicit) == (
        build_execution_request_fingerprint(explicit)
    )


def test_ignored_candidate_group_id_does_not_affect_fingerprint():
    first = _request(candidate_scope_group_id="ignored-group-1")
    second = _request(candidate_scope_group_id="ignored-group-2")

    assert build_execution_request_fingerprint(first) == (
        build_execution_request_fingerprint(second)
    )


def test_saved_candidate_group_id_affects_fingerprint():
    first = _request(
        candidate_scope_mode="saved_group",
        candidate_scope_group_id="group-1",
    )
    second = _request(
        candidate_scope_mode="saved_group",
        candidate_scope_group_id="group-2",
    )

    assert build_execution_request_fingerprint(first) != (
        build_execution_request_fingerprint(second)
    )


def test_empty_and_absent_mention_sets_are_semantically_equivalent():
    absent = _request(mentioned_agent_ids=None)
    empty = _request(mentioned_agent_ids=[])
    whitespace = _request(mentioned_agent_ids=["  "])

    expected = build_execution_request_fingerprint(absent)
    assert build_execution_request_fingerprint(empty) == expected
    assert build_execution_request_fingerprint(whitespace) == expected


def test_empty_selected_agent_set_remains_distinct_from_absence():
    absent = _request(
        selected_agent_ids=None, candidate_scope_mode="explicit_selection"
    )
    empty = _request(selected_agent_ids=[], candidate_scope_mode="explicit_selection")

    assert build_execution_request_fingerprint(absent) != (
        build_execution_request_fingerprint(empty)
    )


@pytest.mark.parametrize(
    "ignored_value",
    [None, ["Alice"], {"name": "Alice"}],
)
def test_non_string_legacy_quote_metadata_matches_missing_field(ignored_value):
    missing = _legacy_quote_request({})
    ignored = _legacy_quote_request({"quoted_sender_name": ignored_value})

    assert build_execution_request_fingerprint(ignored) == (
        build_execution_request_fingerprint(missing)
    )


@pytest.mark.parametrize("field", ["quoted_text", "quoted_sender_name"])
def test_valid_legacy_quote_string_changes_affect_fingerprint(field):
    first = _legacy_quote_request({field: "First"})
    second = _legacy_quote_request({field: "Second"})

    assert build_execution_request_fingerprint(first) != (
        build_execution_request_fingerprint(second)
    )


def test_structured_quote_ignores_legacy_sender_fallback_metadata():
    first_message = _request().model_dump(mode="json")["message"]
    first_message["quote"]["sender_display_name"] = None
    first_message["extend_info"]["quoted_sender_name"] = "Alice"
    second_message = _request().model_dump(mode="json")["message"]
    second_message["quote"]["sender_display_name"] = None
    second_message["extend_info"]["quoted_sender_name"] = "Bob"

    assert build_execution_request_fingerprint(_request(message=first_message)) == (
        build_execution_request_fingerprint(_request(message=second_message))
    )


@pytest.mark.parametrize(
    "change",
    [
        {"message_text": "different text"},
        {"attachment_file_id": "file-2"},
        {"message_target_mode": "all_agents", "target_group": "all_agents"},
        {"target_group": "saved-group", "target_group_id": "saved-group"},
        {"selected_agent_ids": ["agent-1", "agent-4"]},
        {"mentioned_agent_ids": ["agent-1", "agent-4"]},
        {"candidate_scope_mode": "saved_group"},
        {"mode": "direct"},
        {"parent_message_id": "parent-2"},
        {"quote_text": "different quote"},
        {"quote_source_message_id": "source-2"},
    ],
)
def test_semantic_payload_changes_change_fingerprint(change):
    first = _request()
    message = first.model_dump(mode="json")["message"]
    overrides = {}

    if "message_text" in change:
        message["message_content"] = {
            **message["message_content"],
            "message_text": change["message_text"],
        }
    if "attachment_file_id" in change:
        file_id = change["attachment_file_id"]
        overrides["attachments"] = [{"file_id": file_id}]
        message["message_content"] = {
            **message["message_content"],
            "attachments": [{"file_id": file_id}],
        }
    if "quote_text" in change:
        message["quote"] = {**message["quote"], "text": change["quote_text"]}
    if "quote_source_message_id" in change:
        message["quote"] = {
            **message["quote"],
            "source_message_id": change["quote_source_message_id"],
        }

    for key, value in change.items():
        if key not in {
            "message_text",
            "attachment_file_id",
            "quote_text",
            "quote_source_message_id",
        }:
            overrides[key] = value
    overrides["message"] = message
    second = _request(**overrides)

    assert build_execution_request_fingerprint(first) != (
        build_execution_request_fingerprint(second)
    )


def test_v1_fingerprint_payload_and_digest_are_golden():
    request = _request()

    assert execution_request_fingerprint_payload(request) == {
        "room_id": "room-1",
        "sender_id": "user-1",
        "message_content": {"message_text": "hello"},
        "related_message_id": "parent-1",
        "parent_message_id": None,
        "execution_parent_message_id": "parent-1",
        "attachment_file_ids": ["file-1"],
        "quote": {
            "text": "quoted content",
            "source_message_id": "source-1",
            "source_kind": "agent",
            "sender_display_name": "Agent One",
            "source_agent_id": "agent-1",
        },
        "legacy_quote": None,
        "mode": "supervisor",
        "message_target_mode": "room_default",
        "target_group": "room_team",
        "target_group_id": None,
        "mentioned_agent_ids": ["agent-1", "agent-2"],
        "selected_agent_ids": ["agent-1", "agent-3"],
        "candidate_scope_mode": "explicit_selection",
        "candidate_scope_group_id": None,
    }
    assert build_execution_request_fingerprint(request) == (
        "51314a10914d7a516638acea00d9aa67f7994cf58a46181dd1779d0b22800a86"
    )
