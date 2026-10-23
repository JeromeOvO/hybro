"""C5 step 3: the final-message projection writes through the conversation port."""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pymongo.errors import DuplicateKeyError

from tests.test_module_boundaries import imported_modules
from tests.test_module_protocol_inventory import EXPECTED_METHODS

BACKEND = Path(__file__).resolve().parents[1]
PROJECTION = BACKEND / "execution/repository/orchestrator/projection.py"


class _Messages:
    def __init__(self, documents=None, *, race_document=None) -> None:
        self.documents = {doc["message_id"]: doc for doc in documents or []}
        self.race_document = race_document

    async def find_one(self, query):
        return self.documents.get(query["message_id"])

    async def insert_one(self, document):
        if self.race_document is not None:
            self.documents[document["message_id"]] = self.race_document
            raise DuplicateKeyError("message_id")
        self.documents[document["message_id"]] = document


def _store(collection):
    from conversation.repository import ConversationMessageStore

    return ConversationMessageStore(
        room_agent_messages=collection,
        room_user_messages=object(),
        message_repository=object(),
    )


def _document(conversation_id: str = "room-1") -> dict:
    return {
        "room_id": conversation_id,
        "message_id": "final-1",
        "message_type": "agent",
        "agent_id": "system_hybro",
        "message_content": {"message_text": "done"},
        "message_created_at": datetime(2026, 10, 21, tzinfo=UTC),
        "related_message_id": "user-1",
    }


def test_writer_port_declares_the_final_message_write():
    assert (
        "persist_final_agent_message"
        in (EXPECTED_METHODS["conversation.protocols"]["ConversationMessageWriter"])
    )


@pytest.mark.asyncio
async def test_final_message_write_inserts_a_normalized_document():
    from conversation import normalize_timeline_document

    collection = _Messages()

    assert await _store(collection).persist_final_agent_message(_document()) == (
        "accepted"
    )
    assert collection.documents["final-1"] == normalize_timeline_document(_document())


@pytest.mark.asyncio
async def test_final_message_write_replays_in_the_same_room():
    collection = _Messages([_document()])

    assert await _store(collection).persist_final_agent_message(_document()) == (
        "replayed"
    )


@pytest.mark.asyncio
async def test_final_message_write_conflicts_across_rooms():
    collection = _Messages([_document("other-room")])

    assert await _store(collection).persist_final_agent_message(_document()) == (
        "conflict"
    )


@pytest.mark.asyncio
async def test_final_message_write_resolves_a_duplicate_key_race():
    same_room = _Messages(race_document=_document())
    other_room = _Messages(race_document=_document("other-room"))

    assert await _store(same_room).persist_final_agent_message(_document()) == (
        "replayed"
    )
    assert await _store(other_room).persist_final_agent_message(_document()) == (
        "conflict"
    )


def test_projection_reaches_conversation_only_through_its_protocols():
    modules = imported_modules(PROJECTION)
    conversation_modules = {
        module
        for module in modules
        if module == "conversation" or module.startswith("conversation.")
    }

    assert conversation_modules == {"conversation.protocols"}
    assert "pymongo.errors" not in modules


def test_projector_takes_the_writer_port():
    from execution.repository.orchestrator.projection import (
        MongoFinalMessageProjector,
    )

    parameters = inspect.signature(MongoFinalMessageProjector).parameters
    assert "message_writer" in parameters
    assert "messages" not in parameters


def test_composition_injects_the_writer():
    import orchestrator_composition

    parameters = inspect.signature(
        orchestrator_composition.create_orchestrator_runtime
    ).parameters
    assert "message_writer" in parameters
    source = inspect.getsource(orchestrator_composition)
    assert 'mongo.collection("room_agent_messages")' not in source
    container_source = (BACKEND / "container.py").read_text()
    # One for the HITL store, one for the orchestrator runtime.
    assert container_source.count("message_writer=message_store") == 2
