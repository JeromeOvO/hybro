# Concurrency Roadmap for Room Memory Writes

This document describes the three-layer strategy for making room memory
mutations safe under concurrent access. **Layer A is implemented.** Layers B
and C are documented here for future multi-instance deployments.

---

## Problem Statement

`RoomMemory` is a single MongoDB document that multiple code paths mutate:

| Writer | Field(s) Touched |
|---|---|
| `add_agent_response_to_memory` | `memory_content.conversation_history` (append) |
| `add_synthesis_to_history` | `memory_content.conversation_history` (append) |
| `update_room_summary` | `room_summary`, `room_facts` |
| `compact_room_memory` | `memory_content.conversation_history[i]` (per-element) |
| `initialize_or_update_room_memory` | `memory_content.conversation_history` (append) |

Before Layer A, every writer did a full-document **read → modify in Python →
`$set` entire document**. Two concurrent writers would silently overwrite each
other's changes (last-writer-wins).

---

## Layer A: Atomic MongoDB Operators (IMPLEMENTED)

**Status: Done**

Each mutation now uses the narrowest MongoDB operator for its target field(s):

| Method | MongoDB Operator | Why Safe |
|---|---|---|
| `push_conversation_turn` | `$push` on the array | Appends are commutative |
| `trim_conversation_history` | `$push` with `$each: []` + `$slice: -N` | Atomic cap from tail |
| `update_room_summary_atomic` | `$set` on `room_summary` + `$push` on `room_facts` | Disjoint from history |
| `compact_turns_atomic` | `bulk_write` with `arrayFilters` | Per-element updates |

Because the writers now target **disjoint fields**, they can run concurrently
without conflict even on a single MongoDB instance.

### Database Methods Added (`database/mongodb.py`)

- `push_conversation_turn(room_id, turn_dict)` — `$push` + `$inc` + `$set`
- `trim_conversation_history(room_id, max_turns, summary_addition)` — `$push/$slice` + `$set`
- `update_room_summary_atomic(room_id, summary_dict, new_facts)` — `$set` + `$push/$slice`
- `compact_turns_atomic(room_id, compacted_turns)` — `bulk_write` with `arrayFilters`
- `get_room_summary_projection(room_id)` — lightweight `find_one` for summary + facts only
- `get_conversation_history_length(room_id)` — aggregation `$size` without loading the array

---

## Layer B: Optimistic Concurrency Control (FUTURE)

**Status: Not started — implement when any of these become true:**
- Multiple processes write the **same field** concurrently (e.g. two agents
  responding to the same room at exactly the same time)
- A writer that still needs to read-modify-write (e.g. a future analytics
  aggregation) is added

### Design

1. Add a `version: int` field to `RoomMemory` (default 0).
2. Every atomic update includes `{"room_id": room_id, "version": expected}` in
   the filter and `{"$inc": {"version": 1}}` in the update.
3. If `modified_count == 0`, the version was bumped by another writer → retry
   with exponential backoff (max 3 attempts).

```python
# Pseudocode
async def push_conversation_turn_occ(self, room_id, turn, expected_version):
    result = await self.room_memories_collection.update_one(
        {"room_id": room_id, "version": expected_version},
        {
            "$push": {"memory_content.conversation_history": turn},
            "$inc": {"total_messages": 1, "version": 1},
            "$set": {"last_activity_at": utcnow()},
        },
    )
    if result.modified_count == 0:
        raise OptimisticLockConflict(room_id, expected_version)
```

### Migration

- Add `version` field to `RoomMemory` Pydantic model (default 0).
- Run a one-time migration: `db.room_memories.updateMany({}, {$set: {version: 0}})`.
- Update all atomic methods to accept and check `expected_version`.

---

## Layer C: Distributed Locking (FUTURE)

**Status: Not started — implement when:**
- Running 2+ backend instances behind a load balancer
- Layer B retries alone cause unacceptable latency under high concurrency

### Option 1: MongoDB Advisory Locks

Use a dedicated `locks` collection with TTL:

```python
async def acquire_room_lock(room_id: str, ttl_seconds: int = 30) -> bool:
    try:
        await db.locks.insert_one({
            "_id": f"room:{room_id}",
            "acquired_at": utcnow(),
            "expires_at": utcnow() + timedelta(seconds=ttl_seconds),
        })
        return True
    except DuplicateKeyError:
        return False

async def release_room_lock(room_id: str):
    await db.locks.delete_one({"_id": f"room:{room_id}"})
```

- Create a TTL index: `db.locks.createIndex({"expires_at": 1}, {expireAfterSeconds: 0})`
- Locks auto-expire if the holder crashes.

### Option 2: Redis SETNX (if Redis is already in the stack)

```python
acquired = await redis.set(f"lock:room:{room_id}", instance_id, nx=True, ex=30)
```

### Integration Point

Wrap the per-room processing in `RoomMessageCenter.process_room_user_message`
with the lock:

```python
if not await acquire_room_lock(room_id):
    # Return 429 or queue the message
    ...
try:
    result = await self._process_room_message_inner(...)
finally:
    await release_room_lock(room_id)
```

---

## Decision Matrix

| Scenario | Layer A | Layer B | Layer C |
|---|---|---|---|
| Single instance, normal load | ✅ sufficient | Not needed | Not needed |
| Single instance, high burst | ✅ sufficient | Nice-to-have | Not needed |
| Multi-instance, low contention | ✅ sufficient | Recommended | Not needed |
| Multi-instance, high contention | ✅ required | ✅ required | ✅ required |
