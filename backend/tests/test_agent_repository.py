from __future__ import annotations

from copy import deepcopy
from typing import Any
from unittest.mock import MagicMock

import pytest

from agent.repository import AgentMongoRepository


class FakeCollection:
    def __init__(self, docs: list[dict] | None = None) -> None:
        self.docs = [deepcopy(doc) for doc in docs or []]
        self.find_one_calls: list[dict] = []
        self.find_calls: list[tuple[dict, dict]] = []
        self.find_one_and_update_calls: list[tuple[dict, dict, dict]] = []
        self.update_one_calls: list[tuple[dict, dict, dict]] = []
        self.update_many_calls: list[tuple[dict, dict]] = []
        self.delete_one_calls: list[dict] = []
        self.count_calls: list[dict] = []
        self.duplicate_normalized_urls: set[str] = set()
        self.return_false_for_noop = False

    async def find_one(self, query: dict, **kwargs) -> dict | None:
        self.find_one_calls.append(deepcopy(query))
        for doc in self.docs:
            if _matches(doc, query):
                return deepcopy(doc)
        return None

    async def find(self, query: dict, **kwargs) -> list[dict]:
        self.find_calls.append((deepcopy(query), deepcopy(kwargs)))
        matches = [deepcopy(doc) for doc in self.docs if _matches(doc, query)]
        limit = kwargs.get("limit")
        return matches[:limit] if limit else matches

    async def find_one_and_update(
        self, query: dict, update: dict, **kwargs
    ) -> dict | None:
        self.find_one_and_update_calls.append(
            (deepcopy(query), deepcopy(update), deepcopy(kwargs))
        )
        for doc in self.docs:
            if _matches(doc, query):
                updated = deepcopy(doc)
                _apply_update(updated, update, inserting=False)
                if _duplicates_normalized_url(updated, self.duplicate_normalized_urls):
                    raise DuplicateKeyError("duplicate normalized_url")
                doc.clear()
                doc.update(updated)
                return deepcopy(doc)
        if kwargs.get("upsert"):
            new_doc = _query_identity(query)
            _apply_update(new_doc, update, inserting=True)
            if _duplicates_normalized_url(new_doc, self.duplicate_normalized_urls):
                raise DuplicateKeyError("duplicate normalized_url")
            self.docs.append(new_doc)
            return deepcopy(new_doc)
        return None

    async def update_one(self, query: dict, update: dict, **kwargs) -> bool:
        self.update_one_calls.append(
            (deepcopy(query), deepcopy(update), deepcopy(kwargs))
        )
        for doc in self.docs:
            if _matches(doc, query):
                updated = deepcopy(doc)
                _apply_update(updated, update)
                if _duplicates_normalized_url(updated, self.duplicate_normalized_urls):
                    raise DuplicateKeyError("duplicate normalized_url")
                if updated == doc and self.return_false_for_noop:
                    return False
                doc.clear()
                doc.update(updated)
                return True
        if kwargs.get("upsert"):
            new_doc = _query_identity(query)
            _apply_update(new_doc, update)
            if _duplicates_normalized_url(new_doc, self.duplicate_normalized_urls):
                raise DuplicateKeyError("duplicate normalized_url")
            self.docs.append(new_doc)
            return True
        return False

    async def update_many(self, query: dict, update: dict) -> int:
        self.update_many_calls.append((deepcopy(query), deepcopy(update)))
        count = 0
        for doc in self.docs:
            if _matches(doc, query):
                _apply_update(doc, update)
                count += 1
        return count

    async def delete_one(self, query: dict) -> bool:
        self.delete_one_calls.append(deepcopy(query))
        for index, doc in enumerate(self.docs):
            if _matches(doc, query):
                self.docs.pop(index)
                return True
        return False

    async def count(self, query: dict) -> int:
        self.count_calls.append(deepcopy(query))
        return sum(1 for doc in self.docs if _matches(doc, query))


def _repo(
    docs: list[dict] | None = None,
) -> tuple[AgentMongoRepository, FakeCollection]:
    collection = FakeCollection(docs)
    mongo = MagicMock()
    mongo.collection.return_value = collection
    return AgentMongoRepository(mongo=mongo), collection


@pytest.mark.asyncio
async def test_get_by_id_queries_agent_id():
    repo, collection = _repo([{"agent_id": "a1", "name": "A"}])

    assert await repo.get_by_id("a1") == {"agent_id": "a1", "name": "A"}
    assert collection.find_one_calls == [{"agent_id": "a1"}]


@pytest.mark.asyncio
async def test_get_by_ids_and_provider_keep_dict_outputs():
    repo, collection = _repo(
        [
            {"agent_id": "a1", "provider_id": "u1"},
            {"agent_id": "a2", "provider_id": "u2"},
        ]
    )

    assert await repo.get_by_ids(["a2", "a1"]) == [
        {"agent_id": "a1", "provider_id": "u1"},
        {"agent_id": "a2", "provider_id": "u2"},
    ]
    assert await repo.get_by_provider("u1") == [{"agent_id": "a1", "provider_id": "u1"}]
    assert collection.find_calls[0][0] == {"agent_id": {"$in": ["a2", "a1"]}}
    assert collection.find_calls[1][0] == {"provider_id": "u1"}


@pytest.mark.asyncio
async def test_get_public_includes_missing_is_public_with_limit():
    repo, collection = _repo(
        [
            {"agent_id": "public", "is_public": True},
            {"agent_id": "legacy"},
            {"agent_id": "private", "is_public": False},
        ]
    )

    assert await repo.get_public(limit=1) == [{"agent_id": "public", "is_public": True}]
    assert collection.find_calls[-1] == (
        {"$or": [{"is_public": True}, {"is_public": {"$exists": False}}]},
        {"limit": 1},
    )


@pytest.mark.asyncio
async def test_list_visible_filters_public_owned_and_active_agents():
    repo, _ = _repo(
        [
            {"agent_id": "public-active", "is_public": True, "agent_status": "active"},
            {"agent_id": "legacy-active", "agent_status": "active"},
            {
                "agent_id": "owned-private",
                "is_public": False,
                "provider_id": "u1",
                "agent_status": "active",
            },
            {
                "agent_id": "other-private",
                "is_public": False,
                "provider_id": "u2",
                "agent_status": "active",
            },
            {"agent_id": "inactive", "is_public": True, "agent_status": "inactive"},
        ]
    )

    unauthenticated = await repo.list_visible(active_only=True)
    authenticated = await repo.list_visible(user_id="u1", active_only=True)

    assert [doc["agent_id"] for doc in unauthenticated] == [
        "public-active",
        "legacy-active",
    ]
    assert [doc["agent_id"] for doc in authenticated] == [
        "public-active",
        "legacy-active",
        "owned-private",
    ]


@pytest.mark.asyncio
async def test_list_visible_combines_query_with_visibility_filters():
    repo, collection = _repo(
        [
            {"agent_id": "matching", "is_public": True, "agent_status": "active"},
            {"agent_id": "hidden", "is_public": True, "agent_status": "inactive"},
            {"agent_id": "private", "is_public": False, "agent_status": "active"},
        ]
    )

    result = await repo.list_visible(query={"agent_status": "active"})

    assert [doc["agent_id"] for doc in result] == ["matching"]
    assert collection.find_calls[-1] == (
        {
            "$and": [
                {"agent_status": "active"},
                {"$or": [{"is_public": True}, {"is_public": {"$exists": False}}]},
            ]
        },
        {},
    )


@pytest.mark.asyncio
async def test_find_by_normalized_url_checks_field_then_legacy_card_url():
    repo, collection = _repo(
        [
            {
                "agent_id": "legacy",
                "provider_id": "u1",
                "agent_card": {"url": "HTTP://127.0.0.1:80/.well-known/agent.json"},
            },
            {
                "agent_id": "exact",
                "provider_id": "u2",
                "normalized_url": "https://example.com",
            },
        ]
    )

    exact = await repo.find_by_normalized_url("https://example.com")
    legacy = await repo.find_by_normalized_url("http://localhost", provider_id="u1")

    assert exact["agent_id"] == "exact"
    assert legacy["agent_id"] == "legacy"
    assert collection.find_one_calls[0] == {"normalized_url": "https://example.com"}
    assert collection.find_calls[-1][0] == {
        "normalized_url": {"$exists": False},
        "provider_id": "u1",
    }


@pytest.mark.asyncio
async def test_public_url_exists_update_delete_and_health():
    repo, collection = _repo(
        [{"agent_id": "a1", "public_url": "https://story.hybro.ai"}]
    )

    assert await repo.public_url_exists("story", "hybro.ai") is True
    assert await repo.public_url_exists("free", "hybro.ai") is False
    assert collection.count_calls[-2:] == [
        {"public_url": {"$regex": "://story\\.hybro\\.ai"}},
        {"public_url": {"$regex": "://free\\.hybro\\.ai"}},
    ]

    assert await repo.update("a1", {"agent_status": "inactive"}) == {
        "agent_id": "a1",
        "public_url": "https://story.hybro.ai",
        "agent_status": "inactive",
    }
    collection.return_false_for_noop = True
    assert await repo.update("a1", {"agent_status": "inactive"}) == {
        "agent_id": "a1",
        "public_url": "https://story.hybro.ai",
        "agent_status": "inactive",
    }
    assert await repo.update("missing", {"agent_status": "inactive"}) is None

    await repo.update_health("a1", healthy=True)
    assert (await repo.get_by_id("a1"))["agent_status"] == "active"
    assert await repo.delete("a1") is True
    assert await repo.delete("a1") is False


@pytest.mark.asyncio
async def test_find_by_normalized_url_limits_legacy_fallback_scan():
    repo, collection = _repo(
        [
            {
                "agent_id": "legacy",
                "provider_id": "u1",
                "agent_card": {"url": "https://legacy.example/.well-known/agent.json"},
            }
        ]
    )

    found = await repo.find_by_normalized_url(
        "https://legacy.example", provider_id="u1"
    )

    assert found["agent_id"] == "legacy"
    assert collection.find_calls[-1] == (
        {"normalized_url": {"$exists": False}, "provider_id": "u1"},
        {"limit": 500},
    )


@pytest.mark.asyncio
def _matches(doc: dict, query: dict) -> bool:
    return all(_matches_field(doc, key, expected) for key, expected in query.items())


def _matches_field(doc: dict, key: str, expected: Any) -> bool:
    if key == "$or":
        return any(_matches(doc, branch) for branch in expected)
    if key == "$and":
        return all(_matches(doc, branch) for branch in expected)
    actual = _get_path(doc, key)
    exists = _path_exists(doc, key)
    if isinstance(expected, dict):
        for op, value in expected.items():
            if op == "$in" and actual not in value:
                return False
            if op == "$nin" and actual in value:
                return False
            if op == "$ne" and actual == value:
                return False
            if op == "$exists" and exists is not bool(value):
                return False
            if op == "$regex" and value.replace("\\.", ".").strip(".*") not in str(
                actual or ""
            ):
                return False
        return True
    return actual == expected


def _get_path(doc: dict, path: str) -> Any:
    current: Any = doc
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _path_exists(doc: dict, path: str) -> bool:
    current: Any = doc
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return False
        current = current[part]
    return True


def _apply_update(doc: dict, update: dict, *, inserting: bool = False) -> None:
    if inserting:
        for path, value in update.get("$setOnInsert", {}).items():
            _set_path(doc, path, value)
    for path, value in update.get("$set", {}).items():
        _set_path(doc, path, value)
    for path in update.get("$unset", {}):
        _unset_path(doc, path)


def _set_path(doc: dict, path: str, value: Any) -> None:
    current = doc
    parts = path.split(".")
    for part in parts[:-1]:
        current = current.setdefault(part, {})
    current[parts[-1]] = value


def _unset_path(doc: dict, path: str) -> None:
    current = doc
    parts = path.split(".")
    for part in parts[:-1]:
        current = current.get(part, {})
    current.pop(parts[-1], None)


def _query_identity(query: dict) -> dict:
    return {key: value for key, value in query.items() if not isinstance(value, dict)}


def _duplicates_normalized_url(doc: dict, duplicate_normalized_urls: set[str]) -> bool:
    normalized_url = doc.get("normalized_url")
    return normalized_url is not None and normalized_url in duplicate_normalized_urls


class DuplicateKeyError(Exception):
    pass
