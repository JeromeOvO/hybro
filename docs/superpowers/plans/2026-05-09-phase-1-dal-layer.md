# Phase 1 DAL Layer Implementation Plan

> **For agentic workers:** REQUIRED: Use `superpowers:subagent-driven-development` if subagents are available, or `superpowers:executing-plans` to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add concrete DAL implementations for MongoDB, Redis KV/PubSub/Streams, Pinecone, S3, locks, leader election, and index registration without moving domain queries or changing existing singleton behavior.

**Architecture:** Create a new `dal/` package that adapts raw client SDKs directly to the Phase 0 protocols in `common/protocols/dal_protocols.py`. Existing Mongo, Redis, Pinecone, and S3 modules are reference material only; DAL imports only Common and third-party SDKs.

**Tech Stack:** Python 3.11+, Motor, `redis.asyncio`, Pinecone SDK, aioboto3, pytest, pytest-asyncio, stdlib `typing.Protocol`.

---

## Scope

Include:
- New DAL package under `dal/`.
- Protocol conformance and unit tests in `tests/test_dal_protocols.py` and `tests/test_dal_unit.py`.
- `pyproject.toml` package list updates for `dal` subpackages.

Exclude:
- No domain query refactor.
- No existing service imports from `dal/`.
- No `container.py`, global DAL singleton, or app startup wiring.
- No new dependencies.

Reference-only source files:
- Protocols: `common/protocols/dal_protocols.py`
- Mongo behavior patterns: `database/mongodb.py`
- Redis KV/Streams behavior patterns: `infrastructure/redis_service.py`
- Redis PubSub behavior patterns: `infrastructure/brokers/redis_broker.py`
- Lock/leader Lua patterns: `infrastructure/leader_election.py`
- Pinecone behavior patterns: `database/pinecone_db.py`
- S3 behavior patterns: `services/s3_service.py`
- Settings source: `common/config/settings.py`

Assumption:
- The target branch is `refactor/phase-1-dal` from `refactor/phase-0-common`.

## File Map

Create:
- `dal/__init__.py`: re-export concrete DAL classes only; no singleton/container wiring.
- `dal/mongo/__init__.py`, `dal/mongo/client.py`: `MongoDALImpl`, `MongoCollectionAdapter`.
- `dal/redis/__init__.py`, `dal/redis/kv.py`: `RedisKVImpl`.
- `dal/redis/pubsub.py`: `RedisPubSubImpl` using raw `redis.asyncio`.
- `dal/redis/streams.py`: `RedisStreamsImpl` using raw `redis.asyncio`.
- `dal/redis/lock.py`: `DistributedLockImpl`, `LeaderElectorImpl` using raw `redis.asyncio` and inline Lua.
- `dal/pinecone/__init__.py`, `dal/pinecone/client.py`: `VectorDALImpl`.
- `dal/s3/__init__.py`, `dal/s3/client.py`: `ObjectStorageDALImpl` using `aioboto3.Session`.
- `dal/index_registry.py`: `IndexRegistryImpl`.
- `tests/test_dal_protocols.py`: runtime protocol conformance and exports.
- `tests/test_dal_unit.py`: mocked adapter behavior.

Modify:
- `pyproject.toml`: add `dal`, `dal.mongo`, `dal.redis`, `dal.pinecone`, and `dal.s3` to `[tool.setuptools].packages`.

## Task 0: Prepare Branch

**Files:** none

- [ ] **Step 1: Start from Phase 0**

```bash
git switch refactor/phase-0-common
git switch -c refactor/phase-1-dal
```

Expected: branch is `refactor/phase-1-dal`.

- [ ] **Step 2: Confirm Phase 0 protocols exist**

```bash
uv run python -m pytest tests/test_common_foundation.py -v
```

Expected: PASS before Phase 1 changes.

## Task 1: Add Failing DAL Tests

**Files:**
- Create: `tests/test_dal_protocols.py`
- Create: `tests/test_dal_unit.py`

- [ ] **Step 1: Add protocol conformance tests**

Assert each implementation is importable and satisfies its runtime protocol:
`MongoDALImpl`, `MongoCollectionAdapter`, `RedisKVImpl`, `RedisPubSubImpl`, `RedisStreamsImpl`, `VectorDALImpl`, `ObjectStorageDALImpl`, `DistributedLockImpl`, `LeaderElectorImpl`, and `IndexRegistryImpl`.

- [ ] **Step 2: Add package-list test**

Assert `pyproject.toml` includes `dal`, `dal.mongo`, `dal.redis`, `dal.pinecone`, and `dal.s3`.

- [ ] **Step 3: Add mocked unit tests**

Cover Mongo adapter method mappings, Redis direct SDK behavior, PubSub message filtering, Redis Streams normalization, owner-safe locks, leader election, Pinecone DTO mapping, S3 aioboto3 calls, and index registry ordering.

- [ ] **Step 4: Verify tests fail for missing DAL package**

```bash
uv run python -m pytest tests/test_dal_protocols.py tests/test_dal_unit.py -v
```

Expected: FAIL with import errors for `dal`.

## Task 2: Implement Mongo DAL

**Files:**
- Create: `dal/mongo/__init__.py`
- Create: `dal/mongo/client.py`

- [ ] **Step 1: Add `MongoCollectionAdapter`**

Adapt Motor collection methods directly. `find()` must return `list[dict]` by awaiting cursor `to_list(length=limit or 1000)`. `watch()` returns Motor's native async iterator.

- [ ] **Step 2: Add `MongoDALImpl`**

Constructor accepts optional injected `client` or `database` for tests. Default connection uses `common.config.settings`.

Read Mongo pool size from settings if available; fall back to Motor defaults. Verify field names exist in `common/config/settings.py` before using them.

- [ ] **Step 3: Export Mongo classes**

Re-export from `dal/mongo/__init__.py`.

## Task 3: Implement Redis DAL

**Files:**
- Create: `dal/redis/__init__.py`
- Create: `dal/redis/kv.py`
- Create: `dal/redis/pubsub.py`
- Create: `dal/redis/streams.py`
- Create: `dal/redis/lock.py`

- [ ] **Step 1: Add `RedisKVImpl`**

Use `redis.asyncio.from_url(settings.redis_url, decode_responses=True)` directly. Do not import from `infrastructure/`.

Mappings:
- `get(key)` -> `await self._client.get(key)`
- `set(key, value, ttl=None)` -> `await self._client.set(key, value, ex=ttl)`
- `delete(key)` -> `bool(await self._client.delete(key))`
- `increment(key, amount=1)` -> `await self._client.incrby(key, amount)`
- `setnx(key, value, ttl)` -> `bool(await self._client.set(key, value, nx=True, ex=ttl))`
- `exists(key)` -> `bool(await self._client.exists(key))`
- `ping()` -> true on successful `await self._client.ping()`, false on error
- `close()` -> `await self._client.aclose()`

If `settings.redis_url` is empty, all operations return `None`, `False`, or `0` without raising.

- [ ] **Step 2: Add `RedisStreamsImpl`**

Use a separate raw `redis.asyncio.from_url(settings.redis_url, decode_responses=True)` connection.

Mappings:
- `xadd(stream, fields, maxlen=None)` -> `await self._client.xadd(stream, fields, maxlen=maxlen)`
- `xread(streams, block=0, count=100)` -> `await self._client.xread(streams, block=block, count=count)` and normalize tuples into `[{"stream": name, "id": entry_id, "fields": fields_dict}, ...]`
- `ping()` and `close()` match KV behavior.

- [ ] **Step 3: Add `RedisPubSubImpl`**

Use a dedicated raw `redis.asyncio` connection. `subscribe()` must filter `msg["type"] == "message"` before yielding `msg["data"]`; skip subscribe/unsubscribe confirmation messages.

- [ ] **Step 4: Add `DistributedLockImpl`**

Use raw `redis.asyncio`; constructor may accept an injected Redis client to share with KV.

Define inline Lua scripts:

```python
_RELEASE_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""

_RENEW_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('expire', KEYS[1], ARGV[2])
end
return 0
"""
```

Mappings:
- `acquire(key, owner, ttl=60)` -> `await self._client.set(f"lock:{key}", owner, nx=True, ex=ttl)`
- `release(key, owner)` -> execute `_RELEASE_SCRIPT`
- `renew(key, owner, ttl=60)` -> execute `_RENEW_SCRIPT`

- [ ] **Step 5: Add `LeaderElectorImpl`**

Implement directly with raw `redis.asyncio` and the same inline Lua owner-check pattern, using `leader:{job_name}` keys.

Constructor:
`LeaderElectorImpl(client: redis.asyncio.Redis, instance_id: str | None = None)`

Default `instance_id`:
`f"{socket.gethostname()}:{os.getpid()}"`

Mappings:
- `try_acquire(job_name, ttl=60)` -> `await self._client.set(f"leader:{job_name}", self._instance_id, nx=True, ex=ttl)`
- `renew(job_name, ttl=60)` -> Lua check value equals `instance_id`, then `EXPIRE`
- `release(job_name)` -> Lua check value equals `instance_id`, then `DEL`
- `release_all(job_names)` -> loop calling `release()` for each

- [ ] **Step 6: Export Redis classes**

Re-export from `dal/redis/__init__.py`.

## Task 4: Implement Pinecone, S3, and Index Registry

**Files:**
- Create: `dal/pinecone/__init__.py`
- Create: `dal/pinecone/client.py`
- Create: `dal/s3/__init__.py`
- Create: `dal/s3/client.py`
- Create: `dal/index_registry.py`

- [ ] **Step 1: Add `VectorDALImpl`**

Use `common.config.settings` and Pinecone SDK directly.

Map Pinecone match results to:
`VectorSearchResult(id=match.id, score=match.score, metadata=match.metadata or {})`.

Use `asyncio.to_thread()` for sync Pinecone calls.

- [ ] **Step 2: Add `ObjectStorageDALImpl`**

Use `aioboto3.Session` directly. Do not import from `services/`.

Constructor reads:
- `settings.aws_access_key_id`
- `settings.aws_secret_access_key`
- `settings.s3_region`
- `settings.s3_bucket_name`

Mappings:
- `put(key, data, content_type="")` -> upload `BytesIO(data)` with `upload_fileobj`, `ExtraArgs={"ContentType": content_type or "application/octet-stream"}`, return `key`
- `get_presigned_url(key, ttl=3600)` -> `generate_presigned_url("get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=ttl)`
- `delete(key)` -> `delete_object(Bucket=bucket, Key=key)`, return `True` on success

- [ ] **Step 3: Add `IndexRegistryImpl`**

Constructor:
`IndexRegistryImpl(mongo: MongoDAL)`

`register(module_name, collection, index_spec, **kwargs)` stores registrations in memory.

`ensure_all()` calls:
`self._mongo.collection(collection).create_index(index_spec, **kwargs)` in registration order.

## Task 5: Add Top-Level Exports and Packaging

**Files:**
- Create: `dal/__init__.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Re-export DAL classes**

`dal/__init__.py` must have explicit `__all__` listing all Impl class names:
`MongoDALImpl`, `MongoCollectionAdapter`, `RedisKVImpl`, `RedisPubSubImpl`, `RedisStreamsImpl`, `DistributedLockImpl`, `LeaderElectorImpl`, `VectorDALImpl`, `ObjectStorageDALImpl`, and `IndexRegistryImpl`.

- [ ] **Step 2: Update package metadata**

Add `dal`, `dal.mongo`, `dal.redis`, `dal.pinecone`, and `dal.s3` to `[tool.setuptools].packages`.

## Task 6: Verification

**Files:**
- Test: `tests/test_dal_protocols.py`
- Test: `tests/test_dal_unit.py`
- Test: `tests/test_common_foundation.py`

- [ ] **Step 1: Run DAL tests**

```bash
uv run python -m pytest tests/test_dal_protocols.py tests/test_dal_unit.py -v
```

Expected: PASS.

- [ ] **Step 2: Run import compile check**

```bash
uv run python -m compileall dal/
```

Expected: no syntax or import failures.

- [ ] **Step 3: Run existing Phase 0 regression**

```bash
uv run python -m pytest tests/test_common_foundation.py -v
```

Expected: PASS.

- [ ] **Step 4: Check scoped diff**

```bash
git status --short
git diff --stat
```

Expected changes are limited to `dal/**`, DAL tests, `pyproject.toml`, and this plan file.

## Task 7: Commit

**Files:**
- All files changed by this plan.

- [ ] **Step 1: Stage scoped files**

```bash
git add dal tests/test_dal_protocols.py tests/test_dal_unit.py pyproject.toml docs/superpowers/plans/2026-05-09-phase-1-dal-layer.md
```

- [ ] **Step 2: Commit**

```bash
git commit -m "feat: add DAL layer implementations (mongo, redis, pinecone, s3)"
```

Expected: one commit on `refactor/phase-1-dal`.

## Guardrails

- DAL imports only `common.*` and third-party SDKs: Motor, `redis.asyncio`, Pinecone, and aioboto3.
- Do not import from `infrastructure/`, `services/`, `database/`, or `modules/`. Reference those files for implementation patterns only; copy necessary generic logic directly into DAL.
- Import settings from `common.config.settings`, not `config.settings` or `os.getenv`.
- Keep DAL generic: no agents, rooms, tasks, hubs, or memory-specific query methods.
- Do not make old singletons delegate to DAL in this phase.
- Keep Redis pools separate by default: KV, Streams, and PubSub use distinct raw Redis clients unless tests inject shared clients.
- Redis operations preserve graceful degradation; Mongo, Pinecone, and S3 operation failures may propagate except `ping()`, which returns `False`.
