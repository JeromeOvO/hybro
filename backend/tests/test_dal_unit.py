import inspect
from types import SimpleNamespace
from typing import get_type_hints
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.dto import VectorRecord
from common.errors import ExternalServiceError, ObjectStorageError, TransientError
from common.protocols import MongoChangeStream


class FakeMongoChangeStream:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


@pytest.mark.asyncio
async def test_mongo_collection_adapter_maps_basic_operations():
    from dal.mongo.client import MongoCollectionAdapter

    collection = MagicMock()
    collection.find_one = AsyncMock(return_value={"_id": "1"})
    collection.insert_one = AsyncMock(return_value=SimpleNamespace(inserted_id="id1"))
    collection.insert_many = AsyncMock(
        return_value=SimpleNamespace(inserted_ids=["id1", "id2"])
    )
    collection.update_one = AsyncMock(
        return_value=SimpleNamespace(modified_count=0, upserted_id="up1")
    )
    collection.update_many = AsyncMock(return_value=SimpleNamespace(modified_count=2))
    collection.delete_one = AsyncMock(return_value=SimpleNamespace(deleted_count=1))
    collection.delete_many = AsyncMock(return_value=SimpleNamespace(deleted_count=3))
    collection.count_documents = AsyncMock(return_value=4)
    collection.create_index = AsyncMock(return_value="idx")
    collection.index_information = AsyncMock(return_value={"idx": {}})
    collection.drop_index = AsyncMock()
    watcher = FakeMongoChangeStream()
    collection.watch.return_value = watcher

    adapter = MongoCollectionAdapter(collection)

    assert await adapter.find_one({"a": 1}) == {"_id": "1"}
    assert await adapter.insert_one({"a": 1}) == "id1"
    assert await adapter.insert_many([{"a": 1}, {"a": 2}]) == ["id1", "id2"]
    assert await adapter.update_one({"a": 1}, {"$set": {"b": 2}}, upsert=True) is True
    assert await adapter.update_many({"a": 1}, {"$set": {"b": 2}}) == 2
    assert await adapter.delete_one({"a": 1}) is True
    assert await adapter.delete_many({"a": 1}) == 3
    assert await adapter.count({"a": 1}) == 4
    assert await adapter.create_index([("a", 1)], unique=True) == "idx"
    assert await adapter.index_information() == {"idx": {}}
    await adapter.drop_index("idx")
    async with adapter.watch() as stream:
        assert stream is watcher

    hints = get_type_hints(MongoCollectionAdapter.watch)
    assert hints["return"] is MongoChangeStream


@pytest.mark.asyncio
async def test_mongo_collection_adapter_materializes_find_and_aggregate():
    from dal.mongo.client import MongoCollectionAdapter

    find_cursor = MagicMock()
    find_cursor.sort.return_value = find_cursor
    find_cursor.skip.return_value = find_cursor
    find_cursor.limit.return_value = find_cursor
    find_cursor.to_list = AsyncMock(return_value=[{"a": 1}])

    aggregate_cursor = MagicMock()
    aggregate_cursor.to_list = AsyncMock(return_value=[{"total": 2}])

    collection = MagicMock()
    collection.find.return_value = find_cursor
    collection.aggregate.return_value = aggregate_cursor

    adapter = MongoCollectionAdapter(collection)

    result = await adapter.find(
        {"a": 1},
        projection={"a": 1},
        sort=[("a", -1)],
        skip=5,
        limit=10,
    )
    aggregate = await adapter.aggregate([{"$match": {"a": 1}}])

    assert result == [{"a": 1}]
    assert aggregate == [{"total": 2}]
    collection.find.assert_called_once_with({"a": 1}, projection={"a": 1})
    find_cursor.sort.assert_called_once_with([("a", -1)])
    find_cursor.skip.assert_called_once_with(5)
    find_cursor.limit.assert_called_once_with(10)
    find_cursor.to_list.assert_awaited_once_with(length=10)
    aggregate_cursor.to_list.assert_awaited_once_with(length=1000)


@pytest.mark.asyncio
async def test_ensure_runtime_indexes_uses_mongo_dal_specs():
    from container import ensure_runtime_indexes

    collections: dict[str, MagicMock] = {}

    def _collection(name: str):
        if name not in collections:
            collection = MagicMock()
            collection.create_index = AsyncMock(return_value=f"{name}_idx")
            collection.index_information = AsyncMock(return_value={})
            collection.drop_index = AsyncMock()
            collections[name] = collection
        return collections[name]

    mongo = MagicMock()
    mongo.collection.side_effect = _collection
    collections["agents"] = _collection("agents")
    collections["agents"].index_information.return_value = {
        "unique_normalized_url": {
            "partialFilterExpression": {"normalized_url": {"$exists": True}}
        }
    }

    await ensure_runtime_indexes(mongo=mongo)

    assert set(collections) >= {
        "agent_capability_issues",
        "agent_memories",
        "agents",
        "conversation_content",
        "orchestration_run_events",
        "orchestration_runs",
        "room_agent_messages",
        "room_memories",
        "room_quotes",
        "run_events",
        "runs",
        "user_memories",
    }
    collections["agents"].drop_index.assert_awaited_once_with("unique_normalized_url")
    assert _has_create_index(
        collections["agents"],
        [("normalized_url", 1)],
        unique=True,
        name="unique_normalized_url",
        partialFilterExpression={"normalized_url": {"$type": "string"}},
    )
    assert _has_create_index(
        collections["conversation_content"],
        [("room_id", 1), ("turn_id", 1)],
        unique=True,
        name="room_turn_unique",
    )
    assert _has_create_index(
        collections["conversation_content"],
        [
            ("content", "text"),
            ("turn_notes.keywords", "text"),
            ("turn_notes.entities", "text"),
            ("turn_notes.one_liner", "text"),
        ],
        unique=False,
        name="turn_notes_text",
    )
    assert _has_create_index(
        collections["room_agent_messages"],
        [("room_id", 1), ("has_task_tracking", 1), ("task_created_at", -1)],
        unique=False,
        name="room_task_created_sparse",
        sparse=True,
    )


def _has_create_index(collection: MagicMock, keys, **kwargs) -> bool:
    return any(
        call.args == (keys,) and call.kwargs == kwargs
        for call in collection.create_index.call_args_list
    )


@pytest.mark.asyncio
async def test_mongo_collection_adapter_preserves_zero_limit():
    from dal.mongo.client import MongoCollectionAdapter

    find_cursor = MagicMock()
    find_cursor.limit.return_value = find_cursor
    find_cursor.to_list = AsyncMock(return_value=[])

    collection = MagicMock()
    collection.find.return_value = find_cursor

    adapter = MongoCollectionAdapter(collection)

    assert await adapter.find({}, limit=0) == []
    find_cursor.limit.assert_called_once_with(0)
    find_cursor.to_list.assert_awaited_once_with(length=0)


@pytest.mark.asyncio
async def test_mongo_collection_adapter_delegates_bulk_write():
    from dal.mongo.client import MongoCollectionAdapter

    collection = MagicMock()
    collection.bulk_write = AsyncMock(return_value="bulk-result")

    adapter = MongoCollectionAdapter(collection)

    result = await adapter.bulk_write(["op"], ordered=False)

    assert result == "bulk-result"
    collection.bulk_write.assert_awaited_once_with(["op"], ordered=False)


@pytest.mark.asyncio
async def test_mongo_collection_adapter_delegates_distinct():
    from dal.mongo.client import MongoCollectionAdapter

    collection = MagicMock()
    collection.distinct = AsyncMock(return_value=["room-1"])

    adapter = MongoCollectionAdapter(collection)

    result = await adapter.distinct("room_id", {"state": "running"})

    assert result == ["room-1"]
    collection.distinct.assert_awaited_once_with("room_id", {"state": "running"})


@pytest.mark.asyncio
async def test_redis_kv_impl_uses_direct_redis_client():
    from dal.redis.kv import RedisKVImpl

    client = MagicMock()
    client.get = AsyncMock(return_value="value")
    client.set = AsyncMock(return_value=True)
    client.delete = AsyncMock(return_value=1)
    client.incrby = AsyncMock(return_value=3)
    client.exists = AsyncMock(return_value=1)
    client.ping = AsyncMock(return_value=True)
    client.aclose = AsyncMock()

    kv = RedisKVImpl(client=client)

    assert await kv.get("k") == "value"
    await kv.set("k", "v", ttl=10)
    assert await kv.delete("k") is True
    assert await kv.increment("k", amount=2) == 3
    assert await kv.setnx("k", "v", ttl=5) is True
    assert await kv.exists("k") is True
    assert await kv.ping() is True
    await kv.close()

    client.set.assert_any_await("k", "v", ex=10)
    client.incrby.assert_awaited_once_with("k", 2)
    client.set.assert_any_await("k", "v", nx=True, ex=5)
    client.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_redis_kv_impl_gracefully_degrades_without_url(monkeypatch):
    from dal.redis import kv as kv_module

    monkeypatch.setattr(kv_module.settings, "redis_url", "")

    kv = kv_module.RedisKVImpl()

    assert await kv.get("k") is None
    await kv.set("k", "v")
    assert await kv.delete("k") is False
    assert await kv.increment("k") == 0
    assert await kv.setnx("k", "v", ttl=1) is False
    assert await kv.exists("k") is False
    assert await kv.ping() is False


def test_redis_kv_impl_constructs_client_with_bounded_timeout(monkeypatch):
    from dal.redis import kv as kv_module

    calls = []
    client = MagicMock()

    def from_url(url, **kwargs):
        calls.append((url, kwargs))
        return client

    monkeypatch.setattr(kv_module.aioredis, "from_url", from_url)
    monkeypatch.setattr(kv_module.settings, "redis_url", "redis://localhost:6379/0")
    monkeypatch.setattr(kv_module.settings, "redis_max_connections", 17)

    kv = kv_module.RedisKVImpl()

    assert kv._ensure_client() is client
    assert calls == [
        (
            "redis://localhost:6379/0",
            {
                "decode_responses": True,
                "socket_connect_timeout": 5,
                "max_connections": 17,
            },
        )
    ]


@pytest.mark.asyncio
async def test_redis_kv_impl_raises_transient_error_for_configured_driver_failures():
    from dal.redis.kv import RedisKVImpl

    client = MagicMock()
    client.get = AsyncMock(side_effect=RuntimeError("get failed"))
    client.set = AsyncMock(side_effect=RuntimeError("set failed"))
    client.delete = AsyncMock(side_effect=RuntimeError("delete failed"))
    client.incrby = AsyncMock(side_effect=RuntimeError("increment failed"))
    client.exists = AsyncMock(side_effect=RuntimeError("exists failed"))

    kv = RedisKVImpl(client=client)

    with pytest.raises(TransientError):
        await kv.get("k")
    with pytest.raises(TransientError):
        await kv.set("k", "v")
    with pytest.raises(TransientError):
        await kv.delete("k")
    with pytest.raises(TransientError):
        await kv.increment("k")
    with pytest.raises(TransientError):
        await kv.setnx("k", "v", ttl=1)
    with pytest.raises(TransientError):
        await kv.exists("k")


@pytest.mark.asyncio
async def test_redis_streams_impl_normalizes_xread():
    from dal.redis.streams import RedisStreamsImpl

    client = MagicMock()
    client.xadd = AsyncMock(return_value="1-0")
    client.xread = AsyncMock(
        return_value=[("stream-a", [("1-0", {"payload": "one"})])]
    )

    streams = RedisStreamsImpl(client=client)

    assert await streams.xadd("stream-a", {"payload": "one"}, maxlen=100) == "1-0"
    assert await streams.xread({"stream-a": "0-0"}, block=5, count=10) == [
        {"stream": "stream-a", "id": "1-0", "fields": {"payload": "one"}}
    ]
    client.xadd.assert_awaited_once_with("stream-a", {"payload": "one"}, maxlen=100)
    client.xread.assert_awaited_once_with({"stream-a": "0-0"}, block=5, count=10)


@pytest.mark.asyncio
async def test_redis_streams_impl_gracefully_degrades_without_url(monkeypatch):
    from dal.redis import streams as streams_module

    monkeypatch.setattr(streams_module.settings, "redis_url", "")

    streams = streams_module.RedisStreamsImpl()

    assert await streams.xadd("stream-a", {"payload": "one"}) == ""
    assert await streams.xread({"stream-a": "0-0"}) == []
    assert await streams.ping() is False


def test_redis_streams_impl_constructs_client_with_bounded_timeout(monkeypatch):
    from dal.redis import streams as streams_module

    calls = []
    client = MagicMock()

    def from_url(url, **kwargs):
        calls.append((url, kwargs))
        return client

    monkeypatch.setattr(streams_module.aioredis, "from_url", from_url)
    monkeypatch.setattr(
        streams_module.settings, "redis_url", "redis://localhost:6379/0"
    )
    monkeypatch.setattr(streams_module.settings, "redis_max_connections", 17)

    streams = streams_module.RedisStreamsImpl()

    assert streams._ensure_client() is client
    assert calls == [
        (
            "redis://localhost:6379/0",
            {
                "decode_responses": True,
                "socket_connect_timeout": 5,
                "max_connections": 17,
            },
        )
    ]


@pytest.mark.asyncio
async def test_redis_streams_impl_raises_transient_error_for_configured_failures():
    from dal.redis.streams import RedisStreamsImpl

    client = MagicMock()
    client.xadd = AsyncMock(side_effect=RuntimeError("xadd failed"))
    client.xread = AsyncMock(side_effect=RuntimeError("xread failed"))

    streams = RedisStreamsImpl(client=client)

    with pytest.raises(TransientError):
        await streams.xadd("stream-a", {"payload": "one"})
    with pytest.raises(TransientError):
        await streams.xread({"stream-a": "0-0"})


@pytest.mark.asyncio
async def test_redis_pubsub_impl_yields_only_messages():
    from dal.redis.pubsub import RedisPubSubImpl

    assert inspect.iscoroutinefunction(RedisPubSubImpl.subscribe)

    pubsub = MagicMock()
    pubsub.subscribe = AsyncMock()
    pubsub.unsubscribe = AsyncMock()
    pubsub.aclose = AsyncMock()

    async def listen():
        yield {"type": "subscribe", "data": 1}
        yield {"type": "message"}
        yield {"type": "message", "data": "payload"}

    pubsub.listen = listen

    client = MagicMock()
    client.pubsub.return_value = pubsub

    pubsub_impl = RedisPubSubImpl(client=client)

    iterator = await pubsub_impl.subscribe("events")
    assert await anext(iterator) == "payload"
    await iterator.aclose()

    pubsub.subscribe.assert_awaited_once_with("events")
    pubsub.unsubscribe.assert_awaited_once_with("events")
    pubsub.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_redis_pubsub_impl_allocates_pubsub_lazily():
    from dal.redis.pubsub import RedisPubSubImpl

    pubsub = MagicMock()
    pubsub.subscribe = AsyncMock()
    pubsub.unsubscribe = AsyncMock()
    pubsub.aclose = AsyncMock()

    async def listen():
        yield {"type": "message", "data": "payload"}

    pubsub.listen = listen

    client = MagicMock()
    client.pubsub.return_value = pubsub

    pubsub_impl = RedisPubSubImpl(client=client)

    iterator = await pubsub_impl.subscribe("events")
    client.pubsub.assert_not_called()

    assert await anext(iterator) == "payload"
    client.pubsub.assert_called_once_with()
    await iterator.aclose()


@pytest.mark.asyncio
async def test_redis_pubsub_impl_publishes_with_direct_client():
    from dal.redis.pubsub import RedisPubSubImpl

    client = MagicMock()
    client.publish = AsyncMock(return_value=1)

    pubsub_impl = RedisPubSubImpl(client=client)

    await pubsub_impl.publish("events", "payload")

    client.publish.assert_awaited_once_with("events", "payload")


@pytest.mark.asyncio
async def test_redis_pubsub_impl_raises_transient_error_for_publish_failure():
    from dal.redis.pubsub import RedisPubSubImpl

    client = MagicMock()
    client.publish = AsyncMock(side_effect=RuntimeError("publish failed"))

    pubsub_impl = RedisPubSubImpl(client=client)

    with pytest.raises(TransientError):
        await pubsub_impl.publish("events", "payload")


@pytest.mark.asyncio
async def test_redis_pubsub_impl_surfaces_subscribe_setup_failure():
    from dal.redis.pubsub import RedisPubSubImpl

    pubsub = MagicMock()
    pubsub.subscribe = AsyncMock(side_effect=RuntimeError("subscribe failed"))
    pubsub.unsubscribe = AsyncMock()
    pubsub.aclose = AsyncMock()

    client = MagicMock()
    client.pubsub.return_value = pubsub

    pubsub_impl = RedisPubSubImpl(client=client)
    iterator = await pubsub_impl.subscribe("events")

    with pytest.raises(TransientError):
        await anext(iterator)


def test_redis_pubsub_impl_accepts_explicit_max_connections(monkeypatch):
    from dal.redis import pubsub as pubsub_module

    captured = {}

    def from_url(url, **kwargs):
        captured.update(kwargs)
        return MagicMock()

    monkeypatch.setattr(pubsub_module.aioredis, "from_url", from_url)

    pubsub_impl = pubsub_module.RedisPubSubImpl(
        url="redis://localhost:6379/0",
        max_connections=120,
    )
    pubsub_impl._ensure_client()

    assert pubsub_impl.max_connections == 120
    assert captured["max_connections"] == 120


@pytest.mark.asyncio
async def test_redis_pubsub_impl_gracefully_degrades_without_url(monkeypatch):
    from dal.redis import pubsub as pubsub_module

    monkeypatch.setattr(pubsub_module.settings, "redis_url", "")

    pubsub_impl = pubsub_module.RedisPubSubImpl()

    await pubsub_impl.publish("events", "payload")
    iterator = await pubsub_impl.subscribe("events")

    with pytest.raises(StopAsyncIteration):
        await anext(iterator)

    assert await pubsub_impl.ping() is False


@pytest.mark.asyncio
async def test_distributed_lock_impl_uses_owner_checked_lua():
    from dal.redis.lock import DistributedLockImpl

    client = MagicMock()
    client.set = AsyncMock(return_value=True)
    client.eval = AsyncMock(return_value=1)

    lock = DistributedLockImpl(client=client)

    assert await lock.acquire("resource", "owner", ttl=30) is True
    assert await lock.release("resource", "owner") is True
    assert await lock.renew("resource", "owner", ttl=45) is True

    client.set.assert_awaited_once_with("lock:resource", "owner", nx=True, ex=30)
    assert client.eval.await_count == 2
    release_args = client.eval.await_args_list[0].args
    renew_args = client.eval.await_args_list[1].args
    assert release_args[1:] == (1, "lock:resource", "owner")
    assert renew_args[1:] == (1, "lock:resource", "owner", "45")


@pytest.mark.asyncio
async def test_distributed_lock_impl_gracefully_degrades_without_url(monkeypatch):
    from dal.redis import lock as lock_module

    monkeypatch.setattr(lock_module.settings, "redis_url", "")

    lock = lock_module.DistributedLockImpl()

    assert await lock.acquire("resource", "owner") is False
    assert await lock.release("resource", "owner") is False
    assert await lock.renew("resource", "owner") is False


@pytest.mark.asyncio
async def test_distributed_lock_impl_close_closes_client():
    from dal.redis.lock import DistributedLockImpl

    client = MagicMock()
    client.aclose = AsyncMock()

    lock = DistributedLockImpl(client=client)

    await lock.close()

    client.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_leader_elector_impl_uses_instance_id_owner_checks():
    from dal.redis.lock import LeaderElectorImpl

    client = MagicMock()
    client.set = AsyncMock(return_value=True)
    client.eval = AsyncMock(return_value=1)

    elector = LeaderElectorImpl(client, instance_id="inst")

    assert await elector.try_acquire("job", ttl=30) is True
    assert await elector.renew("job", ttl=45) is True
    await elector.release("job")
    await elector.release_all(["job2"])

    client.set.assert_awaited_once_with("leader:job", "inst", nx=True, ex=30)
    assert client.eval.await_count == 3


@pytest.mark.asyncio
async def test_leader_elector_impl_accepts_ttl_seconds_alias():
    from dal.redis.lock import LeaderElectorImpl

    client = MagicMock()
    client.set = AsyncMock(return_value=True)
    client.eval = AsyncMock(return_value=1)

    elector = LeaderElectorImpl(client, instance_id="inst")

    assert await elector.try_acquire("job", ttl=30, ttl_seconds=120) is True
    assert await elector.renew("job", ttl=45, ttl_seconds=180) is True

    client.set.assert_awaited_once_with("leader:job", "inst", nx=True, ex=120)
    renew_args = client.eval.await_args.args
    assert renew_args[1:] == (1, "leader:job", "inst", "180")


@pytest.mark.asyncio
async def test_leader_elector_impl_gracefully_degrades_without_url(monkeypatch):
    from dal.redis import lock as lock_module

    monkeypatch.setattr(lock_module.settings, "redis_url", "")

    elector = lock_module.LeaderElectorImpl(instance_id="inst")

    assert await elector.try_acquire("job") is False
    assert await elector.renew("job") is False
    assert await elector.release("job") is None


@pytest.mark.asyncio
async def test_leader_elector_impl_close_closes_client():
    from dal.redis.lock import LeaderElectorImpl

    client = MagicMock()
    client.aclose = AsyncMock()

    elector = LeaderElectorImpl(client, instance_id="inst")

    await elector.close()

    client.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_vector_dal_impl_maps_pinecone_matches():
    from dal.pinecone.client import VectorDALImpl

    index = MagicMock()
    index.query.return_value = SimpleNamespace(
        matches=[
            SimpleNamespace(id="v1", score=0.9, metadata={"room_id": "r1"}),
            {"id": "v2", "score": 0.8, "metadata": None},
        ]
    )
    index.upsert.return_value = None
    index.delete.return_value = None

    client = MagicMock()
    client.Index.return_value = index

    vector = VectorDALImpl(client=client)

    results = await vector.search("idx", [0.1], top_k=2, filter={"x": "y"})
    await vector.upsert("idx", [VectorRecord(id="v3", vector=[0.2], metadata={"m": 1})])
    await vector.delete("idx", ["v3"])

    assert [item.id for item in results] == ["v1", "v2"]
    assert results[0].metadata == {"room_id": "r1"}
    assert results[1].metadata == {}
    index.query.assert_called_once_with(
        vector=[0.1],
        top_k=2,
        include_metadata=True,
        filter={"x": "y"},
    )
    index.upsert.assert_called_once_with(
        vectors=[{"id": "v3", "values": [0.2], "metadata": {"m": 1}}]
    )
    index.delete.assert_called_once_with(ids=["v3"])


@pytest.mark.asyncio
async def test_vector_dal_impl_ping_uses_instance_default_index():
    from dal.pinecone.client import VectorDALImpl

    index = MagicMock()
    index.describe_index_stats.return_value = {}
    client = MagicMock()
    client.Index.return_value = index

    vector = VectorDALImpl(client=client, index_name="custom-index")

    assert await vector.ping() is True
    client.Index.assert_called_once_with("custom-index")


@pytest.mark.asyncio
async def test_vector_dal_impl_uses_settings_pinecone_config(monkeypatch):
    from common.config import settings
    from dal.pinecone.client import VectorDALImpl

    monkeypatch.setattr(settings, "pinecone_api_key", "settings-key")
    monkeypatch.setattr(settings, "pinecone_index_name", "settings-index")

    index = MagicMock()
    index.describe_index_stats.return_value = {}
    client = MagicMock()
    client.Index.return_value = index
    pinecone_factory = MagicMock(return_value=client)
    monkeypatch.setattr("dal.pinecone.client.pinecone.Pinecone", pinecone_factory)

    vector = VectorDALImpl()

    assert await vector.ping() is True
    pinecone_factory.assert_called_once_with(api_key="settings-key")
    client.Index.assert_called_once_with("settings-index")


def test_pinecone_index_config_falls_back_to_default_when_empty():
    from common.config import (
        PINECONE_INDEX_NAME_DEFAULT,
        Settings,
    )

    settings = Settings(_env_file=None, pinecone_index_name="")

    assert settings.pinecone_index_name == PINECONE_INDEX_NAME_DEFAULT


@pytest.mark.asyncio
async def test_object_storage_dal_impl_uses_aioboto3_session_directly():
    from dal.s3.client import ObjectStorageDALImpl

    client = AsyncMock()
    client.generate_presigned_url.return_value = "https://signed"
    context = AsyncMock()
    context.__aenter__.return_value = client
    session = MagicMock()
    session.client.return_value = context

    storage = ObjectStorageDALImpl(session=session, bucket="bucket", region="us-west-2")

    assert await storage.put("key", b"data", content_type="text/plain") == "key"
    assert await storage.get_presigned_url("key", ttl=30) == "https://signed"
    assert await storage.delete("key") is True

    client.upload_fileobj.assert_awaited_once()
    client.generate_presigned_url.assert_awaited_once_with(
        "get_object",
        Params={"Bucket": "bucket", "Key": "key"},
        ExpiresIn=30,
    )
    client.delete_object.assert_awaited_once_with(Bucket="bucket", Key="key")


def test_object_storage_error_is_external_service_error():
    assert issubclass(ObjectStorageError, ExternalServiceError)
    assert ObjectStorageError("boom").details["service"] == "object_storage"


@pytest.mark.asyncio
async def test_object_storage_dal_get_text_returns_none_for_missing_key():
    from botocore.exceptions import ClientError

    from dal.s3.client import ObjectStorageDALImpl

    client = AsyncMock()
    client.get_object.side_effect = ClientError(
        {"Error": {"Code": "NoSuchKey", "Message": "missing"}},
        "GetObject",
    )
    context = AsyncMock()
    context.__aenter__.return_value = client
    session = MagicMock()
    session.client.return_value = context
    storage = ObjectStorageDALImpl(session=session, bucket="bucket", region="us-west-2")

    assert await storage.get_text("missing") is None


@pytest.mark.asyncio
async def test_object_storage_dal_get_bytes_returns_object_bytes():
    from dal.s3.client import ObjectStorageDALImpl

    body = AsyncMock()
    body.read.side_effect = [b"pdf-bytes", b""]
    client = AsyncMock()
    client.get_object.return_value = {"Body": body, "ContentLength": 9}
    context = AsyncMock()
    context.__aenter__.return_value = client
    session = MagicMock()
    session.client.return_value = context
    storage = ObjectStorageDALImpl(session=session, bucket="bucket", region="us-west-2")

    assert await storage.get_bytes("uploads/r/f/doc.pdf", max_bytes=20) == b"pdf-bytes"
    client.get_object.assert_awaited_once_with(
        Bucket="bucket",
        Key="uploads/r/f/doc.pdf",
    )
    assert body.read.await_args_list[0].args == (21,)


@pytest.mark.asyncio
async def test_object_storage_dal_get_bytes_returns_none_for_missing_key():
    from botocore.exceptions import ClientError

    from dal.s3.client import ObjectStorageDALImpl

    client = AsyncMock()
    client.get_object.side_effect = ClientError(
        {"Error": {"Code": "NoSuchKey", "Message": "missing"}},
        "GetObject",
    )
    context = AsyncMock()
    context.__aenter__.return_value = client
    session = MagicMock()
    session.client.return_value = context
    storage = ObjectStorageDALImpl(session=session, bucket="bucket", region="us-west-2")

    assert await storage.get_bytes("missing", max_bytes=20) is None


@pytest.mark.asyncio
async def test_object_storage_dal_get_bytes_rejects_declared_oversize():
    from dal.s3.client import ObjectStorageDALImpl

    body = AsyncMock()
    client = AsyncMock()
    client.get_object.return_value = {"Body": body, "ContentLength": 21}
    context = AsyncMock()
    context.__aenter__.return_value = client
    session = MagicMock()
    session.client.return_value = context
    storage = ObjectStorageDALImpl(session=session, bucket="bucket", region="us-west-2")

    with pytest.raises(ObjectStorageError, match="exceeds max_bytes"):
        await storage.get_bytes("too-large", max_bytes=20)

    body.read.assert_not_awaited()


@pytest.mark.asyncio
async def test_object_storage_dal_get_bytes_uses_bounded_body_read_when_length_missing():
    from dal.s3.client import ObjectStorageDALImpl

    body = AsyncMock()
    body.read.return_value = b"x" * 21
    client = AsyncMock()
    client.get_object.return_value = {"Body": body}
    context = AsyncMock()
    context.__aenter__.return_value = client
    session = MagicMock()
    session.client.return_value = context
    storage = ObjectStorageDALImpl(session=session, bucket="bucket", region="us-west-2")

    with pytest.raises(ObjectStorageError, match="exceeds max_bytes"):
        await storage.get_bytes("unknown-length", max_bytes=20)

    body.read.assert_awaited_once_with(21)


@pytest.mark.asyncio
async def test_object_storage_dal_get_bytes_rejects_short_chunk_oversize_body():
    from dal.s3.client import ObjectStorageDALImpl

    body = AsyncMock()
    body.read.side_effect = [b"x" * 10, b"y" * 11]
    client = AsyncMock()
    client.get_object.return_value = {"Body": body}
    context = AsyncMock()
    context.__aenter__.return_value = client
    session = MagicMock()
    session.client.return_value = context
    storage = ObjectStorageDALImpl(session=session, bucket="bucket", region="us-west-2")

    with pytest.raises(ObjectStorageError, match="exceeds max_bytes"):
        await storage.get_bytes("short-chunk-oversize", max_bytes=20)

    assert body.read.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("max_bytes", [0, -1])
async def test_object_storage_dal_get_bytes_rejects_non_positive_max_bytes_before_s3_call(
    max_bytes,
):
    from dal.s3.client import ObjectStorageDALImpl

    session = MagicMock()
    storage = ObjectStorageDALImpl(session=session, bucket="bucket", region="us-west-2")

    with pytest.raises(ObjectStorageError, match="max_bytes must be positive"):
        await storage.get_bytes("key", max_bytes=max_bytes)

    session.client.assert_not_called()


@pytest.mark.asyncio
async def test_object_storage_dal_put_file_uploads_file_like_and_returns_key():
    from dal.s3.client import ObjectStorageDALImpl

    client = AsyncMock()
    context = AsyncMock()
    context.__aenter__.return_value = client
    session = MagicMock()
    session.client.return_value = context
    body = MagicMock()
    storage = ObjectStorageDALImpl(session=session, bucket="bucket", region="us-west-2")

    assert (
        await storage.put_file(
            "file-key",
            body,
            content_type="text/plain",
            content_length=12,
        )
        == "file-key"
    )

    client.upload_fileobj.assert_awaited_once_with(
        body,
        "bucket",
        "file-key",
        ExtraArgs={"ContentType": "text/plain"},
    )


@pytest.mark.asyncio
async def test_object_storage_dal_presigned_url_includes_download_filename():
    from dal.s3.client import ObjectStorageDALImpl

    client = AsyncMock()
    client.generate_presigned_url.return_value = "https://signed"
    context = AsyncMock()
    context.__aenter__.return_value = client
    session = MagicMock()
    session.client.return_value = context
    storage = ObjectStorageDALImpl(session=session, bucket="bucket", region="us-west-2")

    assert (
        await storage.get_presigned_url("reports/key", ttl=99, filename="report 1.pdf")
        == "https://signed"
    )

    client.generate_presigned_url.assert_awaited_once_with(
        "get_object",
        Params={
            "Bucket": "bucket",
            "Key": "reports/key",
            "ResponseContentDisposition": (
                "attachment; filename*=UTF-8''report%201.pdf"
            ),
        },
        ExpiresIn=99,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["NoSuchKey", "404", "NotFound"])
async def test_object_storage_dal_get_text_returns_none_for_missing_codes(code):
    from botocore.exceptions import ClientError

    from dal.s3.client import ObjectStorageDALImpl

    client = AsyncMock()
    client.get_object.side_effect = ClientError(
        {"Error": {"Code": code, "Message": "missing"}},
        "GetObject",
    )
    context = AsyncMock()
    context.__aenter__.return_value = client
    session = MagicMock()
    session.client.return_value = context
    storage = ObjectStorageDALImpl(session=session, bucket="bucket", region="us-west-2")

    assert await storage.get_text("missing") is None


@pytest.mark.asyncio
async def test_object_storage_dal_get_text_raises_when_response_has_no_body():
    from dal.s3.client import ObjectStorageDALImpl

    client = AsyncMock()
    client.get_object.return_value = {}
    context = AsyncMock()
    context.__aenter__.return_value = client
    session = MagicMock()
    session.client.return_value = context
    storage = ObjectStorageDALImpl(session=session, bucket="bucket", region="us-west-2")

    with pytest.raises(ObjectStorageError):
        await storage.get_text("key")


@pytest.mark.asyncio
async def test_object_storage_dal_get_text_wraps_invalid_utf8():
    from dal.s3.client import ObjectStorageDALImpl

    body = AsyncMock()
    body.read.return_value = b"\xff"
    client = AsyncMock()
    client.get_object.return_value = {"Body": body}
    context = AsyncMock()
    context.__aenter__.return_value = client
    session = MagicMock()
    session.client.return_value = context
    storage = ObjectStorageDALImpl(session=session, bucket="bucket", region="us-west-2")

    with pytest.raises(ObjectStorageError):
        await storage.get_text("key")


@pytest.mark.asyncio
async def test_object_storage_dal_delete_wraps_unexpected_storage_failure():
    from botocore.exceptions import ClientError

    from dal.s3.client import ObjectStorageDALImpl

    client = AsyncMock()
    client.delete_object.side_effect = ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "denied"}},
        "DeleteObject",
    )
    context = AsyncMock()
    context.__aenter__.return_value = client
    session = MagicMock()
    session.client.return_value = context
    storage = ObjectStorageDALImpl(session=session, bucket="bucket", region="us-west-2")

    with pytest.raises(ObjectStorageError):
        await storage.delete("key")


class _AsyncIter:
    def __init__(self, items):
        self._items = iter(items)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._items)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


@pytest.mark.asyncio
async def test_object_storage_dal_delete_prefix_deletes_all_objects_and_counts():
    from dal.s3.client import ObjectStorageDALImpl

    objects = [AsyncMock(), AsyncMock(), AsyncMock()]
    bucket = MagicMock()
    bucket.objects.filter.return_value = _AsyncIter(objects)
    resource = AsyncMock()
    resource.Bucket.return_value = bucket
    context = AsyncMock()
    context.__aenter__.return_value = resource
    session = MagicMock()
    session.resource.return_value = context
    storage = ObjectStorageDALImpl(session=session, bucket="bucket", region="us-west-2")

    assert await storage.delete_prefix("uploads/room-1/") == 3

    resource.Bucket.assert_awaited_once_with("bucket")
    bucket.objects.filter.assert_called_once_with(Prefix="uploads/room-1/")
    for obj in objects:
        obj.delete.assert_awaited_once()


def test_object_storage_dal_public_url_uses_bucket_region_and_key():
    from dal.s3.client import ObjectStorageDALImpl

    storage = ObjectStorageDALImpl(session=MagicMock(), bucket="bucket", region="us-west-2")

    assert (
        storage.get_public_url("agent-avatars/a.png")
        == "https://bucket.s3.us-west-2.amazonaws.com/agent-avatars/a.png"
    )


@pytest.mark.asyncio
async def test_object_storage_dal_head_returns_metadata_and_none_for_missing():
    from botocore.exceptions import ClientError

    from dal.s3.client import ObjectStorageDALImpl

    client = AsyncMock()
    client.head_object.side_effect = [
        {
            "ContentType": "image/png",
            "ContentLength": 123,
            "LastModified": "date",
        },
        ClientError({"Error": {"Code": "404", "Message": "missing"}}, "HeadObject"),
    ]
    context = AsyncMock()
    context.__aenter__.return_value = client
    session = MagicMock()
    session.client.return_value = context
    storage = ObjectStorageDALImpl(session=session, bucket="bucket", region="us-west-2")

    assert await storage.head("key") == {
        "content_type": "image/png",
        "content_length": 123,
        "last_modified": "date",
    }
    assert await storage.head("missing") is None


@pytest.mark.asyncio
async def test_object_storage_dal_wraps_unexpected_failures_in_object_storage_error():
    from botocore.exceptions import ClientError

    from dal.s3.client import ObjectStorageDALImpl

    client = AsyncMock()
    client.upload_fileobj.side_effect = RuntimeError("network")
    client.get_object.side_effect = ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "denied"}},
        "GetObject",
    )
    client.generate_presigned_url.side_effect = RuntimeError("presign")
    client.head_object.side_effect = RuntimeError("head")
    context = AsyncMock()
    context.__aenter__.return_value = client
    session = MagicMock()
    session.client.return_value = context
    storage = ObjectStorageDALImpl(session=session, bucket="bucket", region="us-west-2")

    for call in (
        lambda: storage.put("key", b"data"),
        lambda: storage.put_file("key", MagicMock()),
        lambda: storage.get_text("key"),
        lambda: storage.get_presigned_url("key"),
        lambda: storage.head("key"),
    ):
        with pytest.raises(ObjectStorageError):
            await call()


@pytest.mark.asyncio
async def test_index_registry_ensures_registered_indexes_in_order():
    from dal.index_registry import IndexRegistryImpl

    collection_a = MagicMock()
    collection_a.create_index = AsyncMock(return_value="idx-a")
    collection_b = MagicMock()
    collection_b.create_index = AsyncMock(return_value="idx-b")
    mongo = MagicMock()
    mongo.collection.side_effect = [collection_a, collection_b]

    registry = IndexRegistryImpl(mongo=mongo)
    registry.register("agent", "agents", [("agent_id", 1)], unique=True)
    registry.register("room", "rooms", [("room_id", 1)])

    await registry.ensure_all()

    assert mongo.collection.call_args_list[0].args == ("agents",)
    assert mongo.collection.call_args_list[1].args == ("rooms",)
    collection_a.create_index.assert_awaited_once_with([("agent_id", 1)], unique=True)
    collection_b.create_index.assert_awaited_once_with([("room_id", 1)])


@pytest.mark.asyncio
async def test_index_registry_attempts_all_indexes_before_raising():
    from dal.index_registry import IndexRegistryImpl

    collection_a = MagicMock()
    collection_a.create_index = AsyncMock(side_effect=ValueError("bad index"))
    collection_b = MagicMock()
    collection_b.create_index = AsyncMock(return_value="idx-b")
    mongo = MagicMock()
    mongo.collection.side_effect = [collection_a, collection_b]

    registry = IndexRegistryImpl(mongo=mongo)
    registry.register("agent", "agents", [("agent_id", 1)])
    registry.register("room", "rooms", [("room_id", 1)])

    with pytest.raises(RuntimeError, match="agent:agents: bad index"):
        await registry.ensure_all()

    assert mongo.collection.call_count == 2
    collection_a.create_index.assert_awaited_once_with([("agent_id", 1)])
    collection_b.create_index.assert_awaited_once_with([("room_id", 1)])
