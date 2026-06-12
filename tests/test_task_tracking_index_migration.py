import pytest

from database.migration.recreate_task_tracking_indexes import run_migration_on_db


class FakeCollection:
    def __init__(self, indexes):
        self.indexes = dict(indexes)
        self.dropped: list[str] = []
        self.created: list[tuple[list[tuple[str, int]], dict]] = []

    async def index_information(self):
        return self.indexes

    async def drop_index(self, name: str):
        self.dropped.append(name)
        self.indexes.pop(name)

    async def create_index(self, keys, **kwargs):
        self.created.append((list(keys), dict(kwargs)))
        self.indexes[kwargs["name"]] = {"key": list(keys), **kwargs}
        return kwargs["name"]


class FakeDB:
    def __init__(self, collection):
        self.room_agent_messages = collection


@pytest.mark.asyncio
async def test_recreates_equivalent_legacy_task_tracking_indexes():
    collection = FakeCollection(
        {
            "has_task_tracking_1": {
                "key": [("has_task_tracking", 1)],
                "sparse": True,
            },
            "task_updated_at_1_message_content.message_task.status.state_1": {
                "key": [
                    ("task_updated_at", 1),
                    ("message_content.message_task.status.state", 1),
                ],
                "sparse": True,
            },
            "task_created_at_1_message_content.message_task.status.state_1": {
                "key": [
                    ("task_created_at", 1),
                    ("message_content.message_task.status.state", 1),
                ],
                "sparse": True,
            },
            "user_id_1_message_content.message_task.status.state_1_has_task_tracking_1": {
                "key": [
                    ("user_id", 1),
                    ("message_content.message_task.status.state", 1),
                    ("has_task_tracking", 1),
                ],
                "sparse": True,
            },
            "room_id_1_has_task_tracking_1_task_created_at_-1": {
                "key": [
                    ("room_id", 1),
                    ("has_task_tracking", 1),
                    ("task_created_at", -1),
                ],
                "sparse": True,
            },
        }
    )

    result = await run_migration_on_db(FakeDB(collection), dry_run=False)

    assert collection.dropped == [
        "has_task_tracking_1",
        "task_updated_at_1_message_content.message_task.status.state_1",
        "task_created_at_1_message_content.message_task.status.state_1",
        "user_id_1_message_content.message_task.status.state_1_has_task_tracking_1",
        "room_id_1_has_task_tracking_1_task_created_at_-1",
    ]
    assert [call[1]["name"] for call in collection.created] == [
        "has_task_tracking_sparse",
        "task_updated_state_sparse",
        "task_created_state_sparse",
        "user_task_state_sparse",
        "room_task_created_sparse",
    ]
    assert result.dropped == 5
    assert result.created == 5


@pytest.mark.asyncio
async def test_dry_run_reports_equivalent_legacy_indexes_without_mutating():
    collection = FakeCollection(
        {
            "has_task_tracking_1": {
                "key": [("has_task_tracking", 1)],
                "sparse": True,
            },
        }
    )

    result = await run_migration_on_db(FakeDB(collection), dry_run=True)

    assert result.dropped == 0
    assert result.created == 0
    assert result.would_drop == ["has_task_tracking_1"]
    assert collection.dropped == []
    assert collection.created == []


@pytest.mark.asyncio
async def test_refuses_to_drop_non_equivalent_indexes_with_matching_keys():
    collection = FakeCollection(
        {
            "has_task_tracking_1": {
                "key": [("has_task_tracking", 1)],
                "sparse": False,
            },
        }
    )

    with pytest.raises(RuntimeError, match="non-equivalent index"):
        await run_migration_on_db(FakeDB(collection), dry_run=False)

    assert collection.dropped == []
    assert collection.created == []
