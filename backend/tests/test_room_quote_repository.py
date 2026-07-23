from __future__ import annotations

from copy import deepcopy
from datetime import datetime

from models.quote import QuotedSnippet


class FakeCollection:
    def __init__(self, docs: list[dict] | None = None) -> None:
        self.docs = [deepcopy(doc) for doc in docs or []]
        self.insert_one_calls: list[dict] = []
        self.find_one_calls: list[dict] = []
        self.delete_one_calls: list[dict] = []
        self.delete_many_calls: list[dict] = []

    async def insert_one(self, document: dict) -> dict:
        self.insert_one_calls.append(deepcopy(document))
        self.docs.append(deepcopy(document))
        return {}

    async def find_one(self, query: dict) -> dict | None:
        self.find_one_calls.append(deepcopy(query))
        quote_id = query.get("quote_id")
        for doc in self.docs:
            if doc.get("quote_id") == quote_id:
                return deepcopy(doc)
        return None

    async def delete_one(self, query: dict) -> bool:
        self.delete_one_calls.append(deepcopy(query))
        quote_id = query.get("quote_id")
        for idx, doc in enumerate(self.docs):
            if doc.get("quote_id") == quote_id:
                self.docs.pop(idx)
                return True
        return False

    async def delete_many(self, query: dict) -> int:
        self.delete_many_calls.append(deepcopy(query))
        room_id = query.get("room_id")
        kept = []
        removed = 0
        for doc in self.docs:
            if doc.get("room_id") == room_id:
                removed += 1
            else:
                kept.append(deepcopy(doc))
        self.docs = kept
        return removed


class FakeMongo:
    def __init__(self, collection: FakeCollection) -> None:
        self._collection = collection

    def collection(self, name: str) -> FakeCollection:
        assert name == "room_quotes"
        return self._collection


def test_room_quote_repository_roundtrips_quote_model():
    from room.repository import RoomQuoteMongoRepository

    collection = FakeCollection()
    repo = RoomQuoteMongoRepository(mongo=FakeMongo(collection))

    snippet = QuotedSnippet(
        room_id="room-1",
        created_by_user_id="user-1",
        text="hello",
        source_message_id="m1",
        source_kind="user_turn",
    )

    inserted = snippet.model_copy(update={"created_at": datetime.fromtimestamp(0)})
    created_id = "Q1"

    # mutate stable id to avoid model_dump randomness in test assertions
    inserted = inserted.model_copy(update={"quote_id": created_id})
    snippet = inserted

    returned_id = __import__("asyncio").run(repo.insert(snippet))
    assert returned_id == created_id
    assert collection.insert_one_calls[0]["quote_id"] == created_id

    fetched = __import__("asyncio").run(repo.get_by_id(created_id))
    assert fetched["quote_id"] == created_id
    assert fetched["text"] == "hello"

    deleted = __import__("asyncio").run(repo.delete_by_id(created_id))
    assert deleted is True
    assert collection.delete_one_calls == [{"quote_id": created_id}]


def test_room_quote_repository_delete_for_room_uses_room_query():
    from room.repository import RoomQuoteMongoRepository

    collection = FakeCollection(
        [
            {"quote_id": "q1", "room_id": "room-a", "text": "a"},
            {"quote_id": "q2", "room_id": "room-b", "text": "b"},
        ]
    )
    repo = RoomQuoteMongoRepository(mongo=FakeMongo(collection))

    deleted = __import__("asyncio").run(repo.delete_for_room("room-a"))

    assert deleted == 1
    assert collection.delete_many_calls == [{"room_id": "room-a"}]
    assert len(collection.docs) == 1
    assert collection.docs[0]["quote_id"] == "q2"
