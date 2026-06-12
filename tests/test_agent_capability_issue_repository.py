from __future__ import annotations

import pytest


class FakeCollection:
    def __init__(self):
        self.inserted = []
        self.pipeline = None

    async def insert_one(self, doc):
        self.inserted.append(doc)
        return doc["issue_id"]

    async def aggregate(self, pipeline):
        self.pipeline = pipeline
        return [{"_id": "agent-1"}, {"_id": "agent-2"}]


class FakeMongo:
    def __init__(self):
        self.collection_obj = FakeCollection()

    def collection(self, name: str):
        assert name == "agent_capability_issues"
        return self.collection_obj


@pytest.mark.asyncio
async def test_capability_issue_repository_records_issue():
    mongo = FakeMongo()
    repo = __import__("agent.repository.capability_issue_mongo", fromlist=["AgentCapabilityIssueMongoRepository"]).AgentCapabilityIssueMongoRepository(mongo)

    await repo.insert({"issue_id": "issue-1", "agent_id": "agent-1"})

    assert mongo.collection_obj.inserted == [{"issue_id": "issue-1", "agent_id": "agent-1"}]


@pytest.mark.asyncio
async def test_capability_issue_repository_lists_excluded_agent_ids():
    mongo = FakeMongo()
    repo = __import__("agent.repository.capability_issue_mongo", fromlist=["AgentCapabilityIssueMongoRepository"]).AgentCapabilityIssueMongoRepository(mongo)

    result = await repo.list_excluded_agent_ids(threshold=2)

    assert result == {"agent-1", "agent-2"}
    assert mongo.collection_obj.pipeline == [
        {"$match": {"status": "open"}},
        {"$group": {"_id": "$agent_id", "count": {"$sum": 1}}},
        {"$match": {"count": {"$gte": 2}}},
    ]
