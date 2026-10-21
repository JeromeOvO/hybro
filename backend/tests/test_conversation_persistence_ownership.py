"""C5 step 1b: conversation owns message, room, and correlation persistence."""

from __future__ import annotations

import importlib
import inspect
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.test_module_boundaries import DAL_IMPORT_DEBT


@pytest.mark.parametrize(
    "module",
    [
        "dal.runtime_store.parts.message_store",
        "dal.runtime_store.parts.task_lifecycle_store",
    ],
)
def test_conversation_persistence_left_dal(module):
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module)


def test_conversation_repository_exports_the_moved_stores():
    from conversation.repository import (
        ClientRequestIdStore,
        ConversationMessageStore,
        ConversationRoomStore,
    )

    assert ConversationMessageStore.__module__ == "conversation.repository.messages"
    assert ConversationRoomStore.__module__ == "conversation.repository.rooms"
    assert ClientRequestIdStore.__module__ == (
        "conversation.repository.client_request_ids"
    )


def test_message_document_helpers_live_in_conversation():
    import dal.runtime_store.parts.parsing as dal_parsing
    from conversation.repository import message_documents

    for name in (
        "_safe_parse_agent_message",
        "_safe_parse_user_message",
        "_safe_parse_room",
        "_strip_file_urls",
        "_strip_unset_task_tracking_fields",
        "_extract_text_from_artifact_parts",
        "_mongo_update_succeeded",
        "_modified_count",
    ):
        assert callable(getattr(message_documents, name)), name
        assert not hasattr(dal_parsing, name), name


def test_agent_runtime_store_has_no_room_methods():
    from agent.repository import AgentRuntimeStore

    for name in (
        "get_room_by_room_id",
        "get_rooms_by_room_owner_id",
        "update_room_by_room_id",
    ):
        assert not hasattr(AgentRuntimeStore, name), name


def test_runtime_repository_store_no_longer_serves_conversation_data():
    from common.protocols import runtime_store_protocols as protocols
    from dal.runtime_store import RuntimeRepositoryStore

    for name in (
        "messages",
        "tasks",
        "get_room_by_room_id",
        "upsert_room_agent_message",
        "resolve_client_request_id_for_message_id",
    ):
        assert not hasattr(RuntimeRepositoryStore, name), name
    assert not hasattr(protocols, "RuntimeMessageStore")
    assert not hasattr(protocols, "RuntimeTaskLifecycleStore")


def test_message_store_debt_entry_is_gone():
    assert "dal/runtime_store/parts/message_store.py" not in DAL_IMPORT_DEBT


@pytest.mark.asyncio
async def test_room_store_reads_and_writes_through_the_room_repository():
    from conversation.repository import ConversationRoomStore
    from models.room import Room

    room_repository = MagicMock()
    room_repository.get_by_id = AsyncMock(
        return_value={
            "room_id": "r1",
            "room_name": "Room",
            "room_owner_id": "owner",
            "room_owner_name": "Owner",
        }
    )
    store = ConversationRoomStore(room_repository=room_repository)

    room = await store.get_room_by_room_id("r1")

    assert isinstance(room, Room)
    assert room.room_id == "r1"
    room_repository.get_by_id.assert_awaited_once_with("r1")


def test_container_builds_conversation_stores_directly():
    import container

    source = inspect.getsource(container)

    assert "runtime_store.messages" not in source
    assert "runtime_store.tasks" not in source
    assert "ConversationMessageStore(" in source
    assert "ClientRequestIdStore(" in source
