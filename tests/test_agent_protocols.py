from __future__ import annotations

import inspect

from agent.facade import AgentFacade
from agent.repository.mongo import AgentMongoRepository


def test_agent_facade_exposes_hub_support_protocol_methods() -> None:
    assert inspect.iscoroutinefunction(AgentFacade.count_hub_agents)
    assert inspect.iscoroutinefunction(AgentFacade.increment_agent_call_count)


async def test_agent_call_counter_uses_existing_success_count_field() -> None:
    class Collection:
        def __init__(self) -> None:
            self.calls = []

        async def update_one(self, query, update, **kwargs):
            self.calls.append((query, update, kwargs))

    class Mongo:
        def __init__(self) -> None:
            self.collection_obj = Collection()

        def collection(self, name):
            assert name == "agents"
            return self.collection_obj

    mongo = Mongo()
    repo = AgentMongoRepository(mongo)

    await repo.increment_agent_call_count("agent-1", success=True)

    assert mongo.collection_obj.calls == [
        ({"agent_id": "agent-1"}, {"$inc": {"call_count": 1, "call_success_count": 1}}, {})
    ]
