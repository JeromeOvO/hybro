from pydantic import BaseModel

from app_shell.viewset import AppShellDALViewSetRepository


class FakeCollection:
    def __init__(self) -> None:
        self.updated: list[tuple[dict, dict]] = []

    async def update_one(self, query: dict, update: dict) -> None:
        self.updated.append((query, update))

    async def find_one(self, query: dict):
        return {"_id": query["_id"], "ok": True}


class FakeMongo:
    def __init__(self) -> None:
        self.collection_ref = FakeCollection()

    def collection(self, name: str) -> FakeCollection:
        return self.collection_ref


class UpdatePayload(BaseModel):
    name: str
    description: str | None = None


async def test_viewset_repository_update_accepts_json_map_payload():
    mongo = FakeMongo()
    repo = AppShellDALViewSetRepository(
        mongo=mongo,
        collection_name="agents",
        pk_field="_id",
    )

    result = await repo.update("a1", {"name": "Agent", "active": True})

    assert result == {"_id": "a1", "ok": True}
    assert mongo.collection_ref.updated == [
        ({"_id": "a1"}, {"$set": {"name": "Agent", "active": True}})
    ]


async def test_viewset_repository_patch_preserves_model_exclude_unset():
    mongo = FakeMongo()
    repo = AppShellDALViewSetRepository(
        mongo=mongo,
        collection_name="agents",
        pk_field="_id",
    )

    result = await repo.patch("a1", UpdatePayload(name="Agent"))

    assert result == {"_id": "a1", "ok": True}
    assert mongo.collection_ref.updated == [
        ({"_id": "a1"}, {"$set": {"name": "Agent"}})
    ]
