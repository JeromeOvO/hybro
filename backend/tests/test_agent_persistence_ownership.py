"""C5 step 1c: agent owns agent-group and agent-read persistence."""

from __future__ import annotations

import importlib
import inspect
from unittest.mock import AsyncMock, MagicMock

import pytest


def test_agent_room_part_left_dal():
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("dal.runtime_store.parts.agent_room_store")


def test_agent_repository_exports_the_runtime_store():
    from agent.repository import AgentRuntimeStore

    assert AgentRuntimeStore.__module__ == "agent.repository.runtime_store"
    assert set(inspect.signature(AgentRuntimeStore).parameters) == {
        "agent_groups",
        "agent_repository",
    }


def test_agent_document_parsers_live_in_agent():
    from agent.repository import documents

    for name in ("_safe_parse_agent", "_safe_parse_agent_group"):
        assert callable(getattr(documents, name)), name


class _Collection:
    def __init__(self, docs):
        self.docs = docs
        self.inserted = []

    async def find(self, query, limit=None):
        return [
            doc
            for doc in self.docs
            if all(doc.get(key) == value for key, value in query.items())
        ]

    async def find_one(self, query):
        found = await self.find(query)
        return found[0] if found else None

    async def insert_one(self, doc):
        self.inserted.append(doc)


@pytest.mark.asyncio
async def test_runtime_store_reads_and_writes_agent_groups():
    from agent.repository import AgentRuntimeStore
    from models.agent_group import AgentGroup

    groups = _Collection(
        [
            {
                "group_id": "g1",
                "name": "One",
                "type": "user",
                "owner_id": "o1",
                "agents": ["a1"],
            },
            {
                "group_id": "g2",
                "name": "Two",
                "type": "user",
                "owner_id": "o2",
                "agents": [],
            },
        ]
    )
    store = AgentRuntimeStore(agent_groups=groups, agent_repository=MagicMock())

    owned = await store.get_agent_groups_by_owner("o1")
    assert [group.group_id for group in owned] == ["g1"]
    assert (await store.get_agent_group_by_id("g2")).name == "Two"

    group = AgentGroup(group_id="g3", name="Three", type="user", owner_id="o1")
    assert await store.add_agent_group(group) is True
    assert groups.inserted[0]["group_id"] == "g3"


@pytest.mark.asyncio
async def test_runtime_store_reads_agents_through_the_agent_repository():
    from agent.repository import AgentRuntimeStore

    agent_repository = MagicMock()
    agent_repository.get_by_id = AsyncMock(
        return_value={"agent_id": "a1", "agent_card": {"name": "Alpha"}}
    )
    agent_repository.increment_agent_call_count = AsyncMock()
    store = AgentRuntimeStore(
        agent_groups=_Collection([]),
        agent_repository=agent_repository,
    )

    assert await store.get_agent_name_by_agent_id("a1") == "Alpha"
    await store.increment_agent_call_count("a1", success=True)
    agent_repository.increment_agent_call_count.assert_awaited_once_with(
        "a1", success=True
    )


def test_container_builds_the_agent_runtime_store_directly():
    import container

    source = inspect.getsource(container)

    assert "runtime_store.agent_room" not in source
    assert "AgentRuntimeStore(" in source
