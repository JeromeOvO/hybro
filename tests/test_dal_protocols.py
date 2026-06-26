import ast
import inspect
import tomllib
from pathlib import Path
from unittest.mock import MagicMock

from common.protocols import (
    DistributedLock,
    IndexRegistry,
    LeaderElector,
    MongoChangeStream,
    MongoCollection,
    MongoDAL,
    ObjectStorageDAL,
    RedisKV,
    RedisPubSub,
    RedisStreams,
    VectorDAL,
)


def test_dal_implementations_satisfy_runtime_protocols():
    from dal import (
        DistributedLockImpl,
        IndexRegistryImpl,
        LeaderElectorImpl,
        MongoCollectionAdapter,
        MongoDALImpl,
        ObjectStorageDALImpl,
        RedisKVImpl,
        RedisPubSubImpl,
        RedisStreamsImpl,
        VectorDALImpl,
    )

    assert isinstance(MongoDALImpl(database=MagicMock()), MongoDAL)
    assert isinstance(MongoCollectionAdapter(MagicMock()), MongoCollection)
    assert isinstance(RedisKVImpl(client=MagicMock()), RedisKV)
    assert isinstance(RedisPubSubImpl(client=MagicMock()), RedisPubSub)
    assert isinstance(RedisStreamsImpl(client=MagicMock()), RedisStreams)
    assert isinstance(VectorDALImpl(client=MagicMock()), VectorDAL)
    assert isinstance(
        ObjectStorageDALImpl(session=MagicMock(), bucket="bucket"),
        ObjectStorageDAL,
    )
    assert isinstance(DistributedLockImpl(client=MagicMock()), DistributedLock)
    assert isinstance(LeaderElectorImpl(client=MagicMock(), instance_id="i1"), LeaderElector)
    assert isinstance(IndexRegistryImpl(mongo=MagicMock()), IndexRegistry)


def test_mongo_collection_protocol_covers_repository_operations():
    required = {
        "find_one",
        "find",
        "find_one_and_update",
        "insert_one",
        "insert_many",
        "update_one",
        "update_many",
        "delete_one",
        "delete_many",
        "count",
        "aggregate",
        "create_index",
        "create_indexes",
        "bulk_write",
        "distinct",
        "find_one_by_stable_or_native_id",
        "watch",
    }
    assert required.issubset(set(MongoCollection.__dict__))


def test_mongo_change_stream_protocol_is_exported():
    import common.protocols as protocols
    from common.protocols import dal_protocols

    assert protocols.MongoChangeStream is MongoChangeStream
    assert dal_protocols.MongoChangeStream is MongoChangeStream
    assert "MongoChangeStream" in protocols.__all__
    assert "MongoChangeStream" in dal_protocols.__all__


def test_redis_protocols_expose_health_properties():
    assert isinstance(RedisKV.__dict__["is_connected"], property)
    assert isinstance(RedisStreams.__dict__["is_connected"], property)


def test_leader_elector_protocol_accepts_keyword_ttl_seconds():
    acquire_signature = inspect.signature(LeaderElector.try_acquire)
    renew_signature = inspect.signature(LeaderElector.renew)

    for signature in (acquire_signature, renew_signature):
        ttl_seconds = signature.parameters["ttl_seconds"]
        assert ttl_seconds.kind is inspect.Parameter.KEYWORD_ONLY
        assert ttl_seconds.default is None


def test_dal_top_level_exports_are_explicit():
    import dal

    assert set(dal.__all__) == {
        "DistributedLockImpl",
        "IndexRegistryImpl",
        "LeaderElectorImpl",
        "MongoCollectionAdapter",
        "MongoDALImpl",
        "ObjectStorageDALImpl",
        "RedisKVImpl",
        "RedisPubSubImpl",
        "RedisStreamsImpl",
        "VectorDALImpl",
    }


def test_dal_subpackages_are_packaged():
    pyproject = tomllib.loads(Path("pyproject.toml").read_text())
    packages = set(pyproject["tool"]["setuptools"]["packages"])

    assert {
        "dal",
        "dal.mongo",
        "dal.redis",
        "dal.pinecone",
        "dal.s3",
    }.issubset(packages)


def test_dal_does_not_import_legacy_layers():
    forbidden_roots = {"database", "infrastructure", "modules", "services"}

    for path in Path("dal").rglob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported = {alias.name.split(".")[0] for alias in node.names}
                assert imported.isdisjoint(forbidden_roots), path
            elif isinstance(node, ast.ImportFrom) and node.module:
                root = node.module.split(".")[0]
                assert root not in forbidden_roots, path
