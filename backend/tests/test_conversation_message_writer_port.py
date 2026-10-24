"""C5 step 2: HITL writes to agent messages go through a conversation port."""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from tests.test_module_protocol_inventory import EXPECTED_METHODS

BACKEND = Path(__file__).resolve().parents[1]
WRITER_METHODS = {
    "persist_final_agent_message",
    "persist_hitl_interaction_metadata",
    "persist_hitl_request_id_on_message",
    "persist_hitl_user_answer",
    "persist_pending_hitl_on_agent_message",
    "update_agent_message_task_state",
}


def test_conversation_protocols_publish_the_writer_and_resolver_ports():
    from conversation import protocols

    assert EXPECTED_METHODS["conversation.protocols"] == {
        "ClientRequestIdResolver": {
            "resolve_client_request_id_for_agent_message",
            "resolve_client_request_id_for_message_id",
        },
        "ConversationMessageWriter": WRITER_METHODS,
    }
    assert set(protocols.__all__) == {
        "ClientRequestIdResolver",
        "ConversationMessageWriter",
        "FinalMessageWriteOutcome",
    }


def test_conversation_stores_implement_the_ports():
    from conversation.protocols import (
        ClientRequestIdResolver,
        ConversationMessageWriter,
    )
    from conversation.repository import ClientRequestIdStore, ConversationMessageStore

    message_store = ConversationMessageStore(
        room_agent_messages=object(),
        room_user_messages=object(),
        message_repository=object(),
    )

    assert isinstance(message_store, ConversationMessageWriter)
    assert isinstance(
        ClientRequestIdStore(message_store=message_store), ClientRequestIdResolver
    )


def test_hitl_store_holds_no_message_collection():
    from approval.repository import HITLRequestStore, hitl_store

    assert set(inspect.signature(HITLRequestStore).parameters) == {
        "hitl_requests",
        "message_writer",
    }
    source = inspect.getsource(hitl_store)
    assert "room_agent_messages" not in source
    assert "room_user_messages" not in source


@pytest.mark.asyncio
async def test_hitl_store_forwards_each_write_to_the_port():
    from approval.repository import HITLRequestStore

    writer = AsyncMock()
    for name in WRITER_METHODS:
        getattr(writer, name).return_value = True
    store = HITLRequestStore(hitl_requests=object(), message_writer=writer)
    projection_at = datetime(2026, 10, 21, tzinfo=UTC)

    assert await store.update_agent_message_task_state("m1", "completed") is True
    assert await store.persist_hitl_request_id_on_message("m1", "r1") is True
    assert await store.persist_hitl_user_answer("m1", "yes") is True
    assert (
        await store.persist_hitl_interaction_metadata(
            "m1", interaction_id="i1", question_count=2, question_index=1
        )
        is True
    )
    assert (
        await store.persist_pending_hitl_on_agent_message(
            "m1",
            request_id="r1",
            prompt="Approve?",
            prompt_type="confirmation",
            choices=None,
            a2a_task_id=None,
            a2a_context_id=None,
            interaction_id="i1",
            question_count=1,
            question_index=0,
            expected_request_id="r0",
            projection_at=projection_at,
        )
        is True
    )

    writer.update_agent_message_task_state.assert_awaited_once_with("m1", "completed")
    writer.persist_hitl_request_id_on_message.assert_awaited_once_with("m1", "r1")
    writer.persist_hitl_user_answer.assert_awaited_once_with("m1", "yes")
    writer.persist_hitl_interaction_metadata.assert_awaited_once_with(
        "m1", interaction_id="i1", question_count=2, question_index=1
    )
    writer.persist_pending_hitl_on_agent_message.assert_awaited_once_with(
        "m1",
        request_id="r1",
        prompt="Approve?",
        prompt_type="confirmation",
        choices=None,
        a2a_task_id=None,
        a2a_context_id=None,
        interaction_id="i1",
        question_count=1,
        question_index=0,
        expected_request_id="r0",
        projection_at=projection_at,
    )


def test_sse_resolver_is_typed_against_the_port():
    from execution.client_request_id import SSEClientRequestIdResolver

    annotation = (
        inspect.signature(SSEClientRequestIdResolver).parameters["resolver"].annotation
    )
    assert "ClientRequestIdResolver" in str(annotation)


def test_container_injects_the_message_store_as_the_writer():
    source = (BACKEND / "container.py").read_text()

    assert "message_writer=message_store" in source
