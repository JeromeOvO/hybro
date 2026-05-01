# Horizontal Scaling Design — Multi-Instance Hybro Backend

**Status**: Phases 1-5 Implemented
**Author**: Kevin Lu & Cursor with Opus 4.6
**Related docs**: `SYSTEM_DESIGN_REVIEW.md` §2.1, §2.2, §2.15 · `EVENT_PIPELINE_DESIGN.md` · `NATIVE_SSE_MIGRATION_DESIGN.md` · `CONCURRENCY_ROADMAP.md` Layer C

---

## Implementation Status

**Phase 1 (Alternative A — Redis Pub/Sub for SSE fan-out + cancellation)** — COMPLETED on `feature/redis-implement`

What was built:
- `EventBroker` Protocol (`infrastructure/event_broker.py`) — generic publish/subscribe with kind-based message dispatch
- `RedisBroker` (`infrastructure/brokers/redis_broker.py`) — Redis Pub/Sub implementation with reconnect, exponential backoff
- SSEManager integration — broker publish in `broadcast_to_room()`, dynamic per-room channel subscribe/unsubscribe
- Cross-instance cancellation — broker fast path via `cancel:global` channel + MongoDB change stream safety net (both idempotent)
- Factory pattern (`infrastructure/brokers/__init__.py`) — swappable for NATS/RabbitMQ/Kafka
- Health endpoint reports `broker_connected` and returns `"degraded"` status when broker expected but down

### Divergences from this Document

| This Document Proposed | What Was Built | Rationale |
|----------------------|----------------|-----------|
| `EventBroadcaster` Protocol (typed methods like `artifact_update()`) | `EventBroker` Protocol (generic `publish/subscribe` with `kind` field) | Generic interface is simpler, MQ-agnostic. Kind-based handler dispatch in SSEManager provides type safety. EventBroadcaster remains valid as a future application-layer wrapper around EventBroker. |
| `RedisBroadcaster` + separate `EventSubscriber` classes | Single `RedisBroker` class + handler registration in SSEManager | Simpler: one class handles both pub and sub. SSEManager registers `_on_sse_event` and `_on_cancellation_event` handlers. |
| Per-message cancel channels (`cancel:{message_id}`) | Single global `cancel:global` channel | Cancellation volume is low; per-message channels add subscribe/unsubscribe overhead with no benefit. |
| `asyncio.gather` for parallel local + Redis publish (§4.4) | Sequential: broker publish → local delivery | Ensures broker failures never affect local delivery. No failure isolation complexity needed. See review feedback below. |
| `msgpack` serialization | `json` serialization | JSON is simpler to debug, sufficient for SSE payloads. Can switch to msgpack later if profiling shows need. |
| Graceful degradation to local-only (silent) | Degraded health status + ERROR logging | Silent degradation = split-brain in multi-instance. See review feedback below. |

### Review Feedback Addressed

**@JeromeOvO's concerns (PR review):**

1. **Last-Event-ID / SSE replay**: Both this doc (NG4) and EVENT_PIPELINE_DESIGN (NG2) explicitly defer this. Note: implementation requires frontend migration from custom fetch-based SSE client to `EventSource` API — not achievable with backend changes alone. Tracked in `NATIVE_SSE_MIGRATION_DESIGN.md`.

2. **Graceful degradation = split-brain**: Fixed. When `REDIS_URL` is configured but Redis is down, health endpoint returns `"status": "degraded"` and all publish failures log at ERROR level. Local delivery continues (better than total failure) but the broken state is visible to ALB health checks and monitoring.

3. **`asyncio.gather` failure semantics**: Does not apply — implementation uses sequential publish (broker first, then local). Broker failures are caught and logged; local delivery always proceeds.

### Phase 2 (Shared Cancellation/Dedup State) — COMPLETED on `feature/redis-implement`

What was built:
- `RedisService` (`infrastructure/redis_service.py`) — shared Redis client for KV + stream ops, separate from RedisBroker's Pub/Sub connection
- L1/L2 cancellation cache: L1 = in-memory TTLCache (sync, fast), L2 = Redis key (async, cross-instance)
- `check_cancelled()` async method — L1 → Redis L2 fallback for non-hot-path callers
- `cancel_message_and_broadcast()` writes Redis L2 key before broker publish
- `_on_cancellation_event()` persists incoming broker cancellations to Redis L2
- Terminal status dedup via Redis `set_nx` in `send_processing_status()` — cross-instance L1/L2 pattern

Divergences:
- `cancel_message()` and `create_token()` remain **sync** (plan preserved this to avoid breaking 5+ call sites)
- `RedisService` gracefully degrades (returns safe defaults when disconnected) matching RedisBroker pattern

### Phase 3 (Hub Relay via Redis Streams) — COMPLETED on `feature/redis-implement`

What was built:
- `RelayStreamService` (`infrastructure/relay_streams.py`) — Redis Streams adapter (push_event, read_events, heartbeat TTL keys)
- Dual-path `connect_hub()` — Redis Streams path (XREAD with blocking, `Last-Event-ID` resume) or in-memory Queue fallback
- Dual-path `push_to_hub()` — Redis Streams with heartbeat TTL check, or existing Queue + offline grace period
- `record_hub_heartbeat()` changed to async — Redis path delegates to stream service, Queue path unchanged
- `api/relay.py` — `Last-Event-ID` header + query param support, `id:` field in SSE frames for stream events
- Separate `RedisService` pool for blocking XREAD to avoid starving KV operations

Divergences:
- Hub heartbeat TTL (90s = 3× interval) naturally replaces `relay_offline_grace_period` on Redis path
- No explicit offline queue on Redis path — the stream IS the durable queue (capped at `relay_stream_maxlen`)

### Phase 4 (Leader Election for Background Jobs) — COMPLETED on `feature/redis-implement`

What was built:
- `LeaderElection` (`infrastructure/leader_election.py`) — SETNX + Lua scripts for safe renewal/release
- `hold()` context manager with automatic renewal task for long-running operations
- Leader wrapping on all 5 background jobs:
  - `StaleTaskChecker` (key: `stale_task_checker`, TTL: 2× check interval)
  - `CompactionSweep` (key: `compaction_sweep`, TTL: 2× interval)
  - `OrphanedUploadCleaner` (key: `orphaned_upload_cleaner`, TTL: 2× interval)
  - `AgentHealthService` (key: `agent_health_checker`, TTL: 2× check interval)
  - `RelayService._heartbeat_loop` (key: `relay_heartbeat_monitor`, TTL: 2× heartbeat interval)
- Shutdown order: stop jobs (sets `_running=False`, awaits current iteration) → release locks → drain SSE → close Redis

Divergences:
- Simple acquire/release pattern (not `hold()`) for most jobs since TTL = 2× interval and MongoDB claims are atomic
- Only relay heartbeat monitor gets leader election; `connect_hub` is NOT gated (any instance serves hub SSE)

### Phase 5 (Operational Hardening) — COMPLETED on `feature/redis-implement`

What was built:
- Enhanced `/health` endpoint: reports `redis_service_connected`, `relay_streams_available` alongside existing `broker_connected`
- Graceful shutdown draining: `SSEManager._draining` flag rejects new connections; configurable `shutdown_drain_seconds` (default 5s) before tearing down infrastructure
- Shutdown sequence: stop jobs → release leader locks → drain SSE → stop broker/Redis/MongoDB

### Remaining Phases

- ~~§4.8: `notify_task_update` bypass fix~~ — **COMPLETED.** Shared `_notify_task_update_impl` extracted; `AgentResponseHandler.notify_task_update()` is the handler-owned entry point; `QueueExecutor` routes through handler; standalone wrapper preserved for background jobs.
- ~~§4.9: `processing_status` side-effect separation~~ — **COMPLETED (functionally resolved).** Broker subscriber uses `_deliver_to_local_connections` (pure broadcast); side effects only run on originating instance. `broadcast_processing_status` self-documenting method deprioritized.
- ~~§4.10: Persistence path unification~~ — **COMPLETED (narrowed scope).** `_handle_stream_artifact_update` routes artifact chunks through `AgentResponseHandler` (atomic `accumulate_artifact_on_message` + SSE). `s3_converted` flag on `AgentEvent` prevents double S3 conversion. Message-chunk persistence (line 827) deferred to future `accumulate_history_on_message` atomic op.
- MongoDB change stream removal (deferred — keep as safety net until Redis-backed cancellation is proven in production)

---

## 1. Motivation

Hybro's backend runs as a **single Uvicorn process**. Every piece of real-time state — SSE connections, cancellation tokens, relay hub queues, background jobs — lives in process memory. This creates three production-grade problems:

1. **Zero horizontal scaling.** An agent response processed by Instance A cannot reach a client connected to Instance B. Adding instances behind a load balancer breaks SSE delivery.
2. **Single point of failure.** A process crash drops all SSE connections and loses all in-flight background work. The `StaleTaskChecker` recovers orphaned messages after a 2-minute delay, but events emitted during the gap are permanently lost.
3. **Resource ceiling.** A single event loop handles all HTTP requests, SSE streams, agent polling (120s timeouts), and background jobs. Under load, long-running agent calls starve request handling.

The `EVENT_PIPELINE_DESIGN.md` introduced the `EventBroadcaster` seam specifically to enable this transition — `LocalBroadcaster` wraps the in-process `SSEManager`, and the design calls for a `RedisBroadcaster` swap at Layer C. This document is that Layer C design.

### Current In-Memory State Inventory

| Singleton | File | Critical State | Scaling Impact |
|---|---|---|---|
| `SSEManager` | `services/sse_services.py` | `room_connections` dict, `cancelled_messages` TTLCache, `_terminal_status_sent` TTLCache, `_cancellation_tokens` TTLCache | **Breaks SSE delivery** — events never reach clients on other instances |
| `RelayService` | `services/relay_service.py` | `_hub_queues` dict, `_offline_queues` dict, `_last_hub_heartbeat` dict | **Breaks hub relay** — hub bound to one instance |
| `RoomMessageCenter` | `modules/RoomMessageCenter.py` | In-process `BackgroundTasks`, `asyncio.create_task()` | **Work lost on crash** — no durable queue |
| `AgentHealthService` | `services/agent_health_service.py` | `_failure_counts` dict, periodic health check task | Redundant checks, unsynchronized failure counts |
| `StaleTaskChecker` | `jobs/stale_task_checker.py` | `_recovery_semaphore`, periodic task | Double-recovery risk (mitigated by atomic claims) |
| Background jobs | `jobs/*.py` | Periodic `asyncio.Task` instances | Redundant execution across instances |

---

## 2. Goals and Non-Goals

### Goals

- **G1**: Multiple backend instances behind a load balancer can serve SSE connections, with events reaching clients regardless of which instance processed the agent response.
- **G2**: Hub relay connections survive instance restarts — hubs reconnect to any instance and resume receiving events.
- **G3**: Background jobs (stale task checker, compaction sweep, orphaned upload cleanup) run on exactly one instance at a time via leader election.
- **G4**: Cancellation tokens, terminal-status dedup, and processing status are visible across instances.
- **G5**: The `EventBroadcaster` interface from `EVENT_PIPELINE_DESIGN.md` is the primary integration point — zero changes to transports. Two prerequisite refactors to the handler layer (§4.8, §4.9) resolve deferred debts from that design.

### Non-Goals

- **NG1**: Durable task queue (Celery/Dramatiq) — separate workstream per `SYSTEM_DESIGN_REVIEW.md` §2.2. This design assumes `BackgroundTasks` / `asyncio.create_task()` continue as-is; durability is orthogonal.
- **NG2**: WebSocket migration — SSE remains the transport protocol. The notification layer is transport-agnostic by design.
- **NG3**: Auto-scaling / Kubernetes operators — this design enables multi-instance, not auto-scaling. Orchestration is deployment-specific.
- **NG4**: Event sourcing / replay — `Last-Event-ID` support is in `NATIVE_SSE_MIGRATION_DESIGN.md` §3.4. This design covers cross-instance fan-out, not reconnect replay.
- **NG5**: Geographic distribution — all instances share a single MongoDB replica set and Redis cluster in the same region.

---

## 3. Alternatives Considered

### Alternative A — Redis Pub/Sub Only (SSE Fan-Out)

Add Redis Pub/Sub for SSE event delivery. Each instance subscribes to room channels and relays events to its local `SSEManager`. All other in-memory state (relay queues, cancellation tokens, background jobs) remains unchanged.

| Dimension | Assessment |
|---|---|
| LOE | ~3–4 days |
| New infrastructure | Redis (single instance or cluster) |
| Fixes SSE fan-out | Yes |
| Fixes relay SPOF | No — hub queues still in-memory |
| Fixes background job duplication | No |
| Fixes cancellation cross-instance | Partial — can piggyback on pub/sub channels |

**Verdict**: Solves the most critical problem (SSE delivery) but leaves relay and background jobs broken. Acceptable as a Phase 1 if time-constrained, but incomplete for production multi-instance.

### Alternative B — Redis Pub/Sub + Streams + Leader Election (Recommended)

Redis Pub/Sub for SSE fan-out, Redis Streams for hub relay durability, Redis-based leader election for background jobs, and Redis hashes for shared cancellation/dedup state. Single Redis dependency covers all five scaling problems.

| Dimension | Assessment |
|---|---|
| LOE | ~8–12 days (phased) |
| New infrastructure | Redis 7+ (Streams, Pub/Sub, SETNX, hashes) |
| Fixes SSE fan-out | Yes |
| Fixes relay SPOF | Yes — hub events survive instance restarts |
| Fixes background job duplication | Yes — leader election |
| Fixes cancellation cross-instance | Yes — shared Redis hashes |

**Verdict**: Best cost/benefit ratio. Single infrastructure dependency. Each phase is independently shippable. Redis is the industry standard for this exact problem class.

### Alternative C — MongoDB Change Streams for Everything

Use MongoDB Change Streams as the event bus. Write events to a `room_events` collection; each instance watches the collection and delivers to local SSE connections.

| Dimension | Assessment |
|---|---|
| LOE | ~6–8 days |
| New infrastructure | None (MongoDB already required) |
| Fixes SSE fan-out | Yes |
| Fixes relay SPOF | Partial — requires a `relay_events` collection |
| Latency | Higher — write + oplog tail vs. in-memory pub/sub |
| Throughput | Lower — every event is a DB write + oplog read |

**Verdict**: Eliminates the Redis dependency but adds write amplification on every streaming event. For high-frequency events (`artifact_update` chunks arrive every ~50ms during streaming), the DB write overhead is significant. Change Streams also have a ~100ms propagation delay vs. Redis Pub/Sub's sub-millisecond delivery. Acceptable for low-throughput events but not for the streaming hot path.

### Alternative D — NATS / RabbitMQ Message Broker

Use a dedicated message broker (NATS JetStream, RabbitMQ) for all cross-instance communication.

| Dimension | Assessment |
|---|---|
| LOE | ~10–15 days |
| New infrastructure | NATS/RabbitMQ cluster |
| Operational complexity | High — new infrastructure to deploy, monitor, and maintain |
| Features | Richer than Redis (persistence, replay, consumer groups) |

**Verdict**: Over-engineered for Hybro's current scale. NATS/RabbitMQ shine at >10 instances with complex routing topologies. Redis covers Hybro's needs (fan-out, streams, leader election) with lower operational overhead and a simpler deployment model.

---

## 4. Proposed Design (Alternative B)

### 4.1 Architecture Overview

```
                    ┌──────────────────────────────────┐
                    │           Load Balancer           │
                    │  (round-robin, no sticky sessions)│
                    └──────────┬───────────────────────┘
                               │
              ┌────────────────┼────────────────┐
              ▼                ▼                 ▼
     ┌─────────────┐  ┌─────────────┐  ┌─────────────┐
     │ Instance A   │  │ Instance B   │  │ Instance C   │
     │              │  │              │  │              │
     │ FastAPI      │  │ FastAPI      │  │ FastAPI      │
     │ SSEManager   │  │ SSEManager   │  │ SSEManager   │
     │ (local only) │  │ (local only) │  │ (local only) │
     │              │  │              │  │              │
     │ Redis─────┐  │  │ Redis─────┐  │  │ Redis─────┐  │
     │ Broadcaster│  │  │ Broadcaster│  │  │ Broadcaster│  │
     │ + Subscriber│ │  │ + Subscriber│ │  │ + Subscriber│ │
     └──────┬──────┘  └──────┬──────┘  └──────┬──────┘
            │                │                 │
            └────────────────┼─────────────────┘
                             ▼
                    ┌─────────────────┐
                    │     Redis 7+    │
                    │                 │
                    │ • Pub/Sub       │  ← SSE event fan-out
                    │ • Streams       │  ← Hub relay events
                    │ • Hashes        │  ← Cancellation, dedup
                    │ • SETNX + TTL   │  ← Leader election
                    │ • Keys + TTL    │  ← Processing status
                    └────────┬────────┘
                             │
                    ┌────────┴────────┐
                    │    MongoDB      │
                    │  (replica set)  │
                    │  Source of truth │
                    └─────────────────┘
```

### 4.2 SSE Event Fan-Out via Redis Pub/Sub

This is the core mechanism. When any instance needs to broadcast an SSE event to a room, it publishes to a Redis Pub/Sub channel. Every instance subscribed to that channel delivers the event to its local SSE connections.

#### Channel Naming

```
room:{room_id}:events     — all SSE events for a room
hub:{hub_id}:events        — relay events for a hub (§4.5)
```

#### Publish Path (RedisBroadcaster)

The `RedisBroadcaster` implements the `EventBroadcaster` protocol from `EVENT_PIPELINE_DESIGN.md`:

```python
class RedisBroadcaster:
    """Cross-instance event broadcaster via Redis Pub/Sub.

    Replaces LocalBroadcaster when running multiple instances.
    The EventBroadcaster protocol ensures zero handler changes.
    """

    def __init__(self, redis: Redis, local_sse: SSEManager,
                 instance_id: str) -> None:
        self._redis = redis
        self._local = local_sse  # local SSEManager for self-publish optimization (§4.4)
        self._instance_id = instance_id

    async def artifact_update(
        self, room_id, message_id, agent_id, artifact, *,
        append=False, last_chunk=False,
    ):
        payload = {
            "method": "artifact_update",
            "origin": self._instance_id,
            "args": {
                "room_id": room_id,
                "message_id": message_id,
                "agent_id": agent_id,
                "artifact": artifact,
                "append": append,
                "last_chunk": last_chunk,
            },
        }
        # Self-publish optimization: deliver locally AND publish to Redis in
        # parallel. Subscriber skips messages with matching origin (§4.4).
        await asyncio.gather(
            self._local.send_artifact_update(
                room_id, message_id, agent_id, artifact,
                append=append, last_chunk=last_chunk,
            ),
            self._redis.publish(
                f"room:{room_id}:events", msgpack.packb(payload),
            ),
        )

    async def task_update(self, room_id, message_id, status, **kw):
        payload = {
            "method": "task_update",
            "origin": self._instance_id,
            "args": {"room_id": room_id, "message_id": message_id,
                     "status": status, **kw},
        }
        await asyncio.gather(
            self._local.send_task_update(
                room_id=room_id, message_id=message_id,
                status=status, **kw,
            ),
            self._redis.publish(
                f"room:{room_id}:events", msgpack.packb(payload),
            ),
        )

    # ... remaining protocol methods follow the same pattern
```

> **Implementation note (2026-03):** The actual implementation uses sequential publish (broker first, then local delivery) instead of `asyncio.gather`. This eliminates the need for failure isolation between the two paths. See "Divergences from this Document" above.

**Serialization**: msgpack (not JSON) for the pub/sub payload. Artifact chunks can contain binary-like data; msgpack handles bytes natively and is ~2× faster to encode/decode than JSON for typical SSE payloads.

> **Note**: The constructor accepts `local_sse` for the self-publish optimization detailed in §4.4. Every protocol method delivers locally AND publishes to Redis in parallel — the subscriber skips messages from the same `origin`.

#### Subscribe Path (EventSubscriber)

Each instance runs a background coroutine that subscribes to room channels and dispatches events to the local `SSEManager`:

```python
class EventSubscriber:
    """Listens to Redis Pub/Sub and dispatches to local SSEManager."""

    def __init__(
        self, redis: Redis, sse_manager: SSEManager, instance_id: str,
    ) -> None:
        self._redis = redis
        self._sse = sse_manager
        self._instance_id = instance_id
        self._pubsub = redis.pubsub()
        self._active_rooms: set[str] = set()

    async def subscribe_room(self, room_id: str) -> None:
        if room_id not in self._active_rooms:
            await self._pubsub.subscribe(f"room:{room_id}:events")
            self._active_rooms.add(room_id)

    async def unsubscribe_room(self, room_id: str) -> None:
        if room_id in self._active_rooms:
            await self._pubsub.unsubscribe(f"room:{room_id}:events")
            self._active_rooms.discard(room_id)

    async def run(self) -> None:
        """Main loop — runs as asyncio.Task on startup."""
        async for message in self._pubsub.listen():
            if message["type"] != "message":
                continue
            payload = msgpack.unpackb(message["data"])
            if payload["origin"] == self._instance_id:
                continue  # skip self-published events
            await self._dispatch(payload)

    async def _dispatch(self, payload: dict) -> None:
        method = payload["method"]
        args = payload["args"]
        handler = getattr(self._sse, f"send_{method}", None)
        if handler:
            await handler(**args)
        else:
            logger.warning("EventSubscriber: unknown method %r — skipping", method)
```

#### Subscription Lifecycle

Instances subscribe to room channels **on SSE connection open** and unsubscribe **when the last SSE connection for a room closes**:

```python
# In api/sse.py — stream_room_messages endpoint
connection = await sse_manager.add_connection(room_id)
await event_subscriber.subscribe_room(room_id)
try:
    # ... stream events ...
finally:
    await sse_manager.remove_connection(room_id, connection.connection_id)
    if not sse_manager.has_connections(room_id):
        await event_subscriber.unsubscribe_room(room_id)
```

This ensures instances only receive events for rooms with active local clients, keeping Redis traffic proportional to active connections rather than total rooms.

> **Implementation note — subscribe/unsubscribe race.** The check-then-act sequence (`has_connections` → `unsubscribe_room`) has a TOCTOU window: two connections closing concurrently can both read `has_connections == True` before either finishes, so neither calls `unsubscribe_room` (leaked subscription). The reverse race (both read False, both unsubscribe) is harmless — `EventSubscriber` ignores redundant calls. Implementations should use an `asyncio.Lock` or atomic ref-count inside `EventSubscriber` to serialize subscribe/unsubscribe against the connection count.

### 4.3 Shared Cancellation and Dedup State

Three TTLCaches in `SSEManager` must become cross-instance visible:

| Current (in-memory) | Redis replacement | Key pattern | TTL |
|---|---|---|---|
| `cancelled_messages: TTLCache` | Redis hash | `cancelled:{message_id}` | 1 hour |
| `_terminal_status_sent: TTLCache` | Redis hash | `terminal:{room_id}:{message_id}` | 5 min |
| `_cancellation_tokens: TTLCache` | Redis key + Pub/Sub signal | `cancel_token:{message_id}` | 1 hour |

#### Cancellation Token Cross-Instance Signaling

`CancellationToken` is an in-process `asyncio.Event`. It cannot be shared directly. Instead:

1. When a cancel request arrives, the receiving instance:
   - Sets `cancelled:{message_id}` in Redis with 1-hour TTL
   - Publishes to `cancel:{message_id}` channel
   - Signals the local `CancellationToken` (if one exists)

2. On processing start, the instance:
   - Creates a local `CancellationToken`
   - Checks Redis `cancelled:{message_id}` — if set, pre-signal the token
   - Subscribes to `cancel:{message_id}` channel
   - On message received, signals the local token

3. On processing end:
   - Unsubscribes from `cancel:{message_id}` channel
   - Deletes the local token

This preserves the cooperative cancellation semantics (`token.race()`, `token.check()`) while making cancellation visible across instances. The existing MongoDB Change Stream for `cancelled_messages` collection can be **removed** once Redis handles cancellation — it was a partial mitigation for exactly this problem.

#### Processing Status

`SSEManager.send_processing_status` persists **`runs` / `run_events`** via **`RunCommandHandler.record_processing_status`** for lifecycle truth; **`rooms.processing_message_id`** is legacy and not updated here. Page-refresh recovery uses **`active_runs`** on room APIs (see event-sourced lifecycle doc).

The terminal-status dedup cache (`_terminal_status_sent`) moves to Redis to prevent double-sends across instances. The check-and-set operation uses `SET NX`:

```python
async def _is_terminal_already_sent(self, room_id: str, message_id: str) -> bool:
    key = f"terminal:{room_id}:{message_id}"
    was_set = await self._redis.set(key, "1", nx=True, ex=300)
    return was_set is None  # None means key already existed
```

### 4.4 Self-Publish Optimization

The `RedisBroadcaster` publishes to Redis, and the `EventSubscriber` receives the message and calls `SSEManager.send_*`. But the publishing instance already has the event in-process — routing through Redis adds ~1ms of latency for local clients.

**Decision**: The `RedisBroadcaster` delivers to the local `SSEManager` **and** publishes to Redis in parallel (see the `asyncio.gather` calls in the §4.2 code above). The subscriber skips messages from its own `instance_id` (the `origin` field in the payload).

This ensures local clients see events with zero additional latency while remote clients receive them via Redis.

> **Implementation note (2026-03):** The actual implementation uses sequential publish (broker first, then local delivery) instead of `asyncio.gather`. This eliminates the need for failure isolation between the two paths. See "Divergences from this Document" above.

### 4.5 Hub Relay via Redis Streams

The `RelayService` currently holds per-hub `asyncio.Queue` instances. When a hub SSE connection drops and reconnects to a different instance, events are lost. Redis Streams provide durable, ordered, multi-consumer message delivery — exactly what hub relay needs.

#### Stream Naming

```
hub:{hub_id}:relay     — relay events for a specific hub
```

#### Producer Side (Backend → Hub)

When the backend needs to dispatch a task to a hub agent, it writes to the hub's Redis Stream instead of an in-memory queue:

```python
class RedisRelayService:
    async def dispatch_to_hub(self, hub_id: str, event: dict) -> str:
        stream_key = f"hub:{hub_id}:relay"
        entry_id = await self._redis.xadd(
            stream_key,
            {"payload": msgpack.packb(event)},
            maxlen=10_000,  # bounded stream — prevents unbounded growth
        )
        return entry_id
```

#### Consumer Side (Hub SSE Endpoint)

The hub's SSE endpoint reads from the Redis Stream. On reconnect, the hub sends its last-seen entry ID, and the stream delivers only missed events:

```python
# api/relay.py — relay SSE endpoint
@router.get("/relay/events")
async def relay_events(hub_id: str, last_event_id: str | None = None):
    stream_key = f"hub:{hub_id}:relay"
    start_id = last_event_id or "0-0"

    async def event_generator():
        while True:
            entries = await redis.xread(
                {stream_key: start_id}, count=10, block=5000,
            )
            if entries:
                for entry_id, data in entries[0][1]:
                    event = msgpack.unpackb(data[b"payload"])
                    yield f"id: {entry_id}\ndata: {json.dumps(event)}\n\n"
                    start_id = entry_id
            else:
                yield f": heartbeat\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
```

#### Offline Queue Replacement

The current `_offline_queues` (bounded deque with TTL per hub) is replaced by Redis Stream's built-in retention. Events persist in the stream until the hub reads them or the `maxlen` cap evicts the oldest entries. This is strictly better than the in-memory deque:

| Feature | In-Memory Deque | Redis Stream |
|---|---|---|
| Survives instance restart | No | Yes |
| Hub reconnects to different instance | Lost events | Resumes from last ID |
| Bounded size | Yes (deque maxlen) | Yes (XADD maxlen) |
| TTL-based eviction | Manual (per-entry check) | Stream trimming or XTRIM |
| Consumer acknowledgment | N/A | XACK via consumer groups (available but not used in this design — single consumer per hub uses plain XREAD) |

#### Hub Heartbeat

The current heartbeat tracking (`_last_hub_heartbeat` dict) moves to a Redis key with TTL:

```python
async def record_hub_heartbeat(self, hub_id: str) -> None:
    await self._redis.set(f"hub:{hub_id}:heartbeat", "1", ex=90)

async def is_hub_alive(self, hub_id: str) -> bool:
    return await self._redis.exists(f"hub:{hub_id}:heartbeat") > 0
```

The 90-second TTL (3× the 30-second heartbeat interval) means a hub is considered dead if it misses 3 consecutive heartbeats, regardless of which instance receives them.

### 4.6 Leader Election for Background Jobs

Background jobs must run on exactly one instance. Redis-based leader election provides this:

```python
class LeaderElection:
    """Simple Redis-based leader election with TTL renewal."""

    def __init__(self, redis: Redis, instance_id: str) -> None:
        self._redis = redis
        self._instance_id = instance_id

    async def try_acquire(self, job_name: str, ttl_seconds: int = 60) -> bool:
        acquired = await self._redis.set(
            f"leader:{job_name}",
            self._instance_id,
            nx=True,
            ex=ttl_seconds,
        )
        return acquired is not None

    async def renew(self, job_name: str, ttl_seconds: int = 60) -> bool:
        # Only renew if we are still the leader
        script = """
        if redis.call('get', KEYS[1]) == ARGV[1] then
            return redis.call('expire', KEYS[1], ARGV[2])
        end
        return 0
        """
        result = await self._redis.eval(
            script, 1, f"leader:{job_name}",
            self._instance_id, str(ttl_seconds),
        )
        return result == 1

    async def release(self, job_name: str) -> None:
        script = """
        if redis.call('get', KEYS[1]) == ARGV[1] then
            return redis.call('del', KEYS[1])
        end
        return 0
        """
        await self._redis.eval(
            script, 1, f"leader:{job_name}", self._instance_id,
        )
```

Each background job wraps its periodic loop with a leader check:

```python
class StaleTaskChecker:
    async def _run_loop(self):
        while True:
            if await self._leader.try_acquire("stale_task_checker", ttl_seconds=120):
                try:
                    await self._check_stale_tasks()
                finally:
                    await self._leader.release("stale_task_checker")
            await asyncio.sleep(self._interval)
```

Jobs that need leader election:

| Job | Current Interval | Leader Key | TTL |
|---|---|---|---|
| `StaleTaskChecker` | 60s | `leader:stale_task_checker` | 120s |
| `CompactionSweep` | 300s | `leader:compaction_sweep` | 600s |
| `OrphanedUploadCleaner` | 3600s | `leader:orphaned_upload_cleaner` | 7200s |
| `AgentHealthChecker` | 120s | `leader:agent_health_checker` | 240s |
| `RelayHeartbeatMonitor` | 30s | `leader:relay_heartbeat_monitor` | 60s |

The TTL is always 2× the interval to ensure the lock doesn't expire between runs if one iteration takes longer than expected.

### 4.7 Instance Identity

Each instance needs a stable identifier for leader election and self-publish dedup. Generated on startup:

```python
import uuid
import os

INSTANCE_ID = os.environ.get("HYBRO_INSTANCE_ID", str(uuid.uuid4()))
```

For container deployments, set `HYBRO_INSTANCE_ID` to the container hostname or pod name. For local development, a random UUID suffices.

### 4.8 Prerequisite: `notify_task_update` Bypass (EVENT_PIPELINE_DESIGN §4.9)

`EVENT_PIPELINE_DESIGN.md` §4.9 explicitly deferred a problem to "Phase 3+ when `RedisBroadcaster` is needed." This document is that Phase 3+. The problem must be resolved here.

#### The Problem

The handler's `_notify()` helper delegates to `notify_task_update` (`services/task_notification_service.py`), a module-level function that:
1. Reads the persisted message from DB
2. Extracts content/error/artifacts
3. Calls `notification_service.send_task_update(...)` — which is `SSEManager.send_task_update` **directly**

This means terminal `task_update` SSE events (completed, failed, canceled, input-required) bypass the `EventBroadcaster` entirely. In a multi-instance deployment, a terminal event processed on Instance A only reaches clients on Instance A — clients on Instance B never see the task complete.

Streaming events (artifact chunks, status updates) flow through the broadcaster and are covered by `RedisBroadcaster`. But the most important event — the one that tells the frontend "the task is done" — does not.

#### The Fix

Refactor `notify_task_update` to accept and use the broadcaster. The EVENT_PIPELINE_DESIGN §4.9 proposed two options:

- **(a)** Accept a broadcaster parameter: `notify_task_update(..., broadcaster=...)`.
- **(b)** Become a method on `AgentResponseHandler` that uses `self._broadcaster`.

**Decision: Option (b).** The function already performs handler-adjacent work (DB read, content extraction, notification dispatch). Making it a method on `AgentResponseHandler` is a natural home and avoids threading a broadcaster parameter through a module-level function with no class state.

```python
# BEFORE — module-level function in services/task_notification_service.py
async def notify_task_update(room_id, message_id, status, ...):
    message = await db.get_agent_message(message_id)
    # ... extract content, artifacts, agent name ...
    await notification_service.send_task_update(  # ← SSEManager directly
        room_id=room_id, message_id=message_id, status=status, ...
    )

# AFTER — method on AgentResponseHandler
class AgentResponseHandler:
    async def notify_task_update(self, room_id, message_id, status, ...):
        message = await self._db.get_agent_message(message_id)
        # ... extract content, artifacts, agent name (same logic) ...
        await self._broadcaster.task_update(  # ← through broadcaster
            room_id=room_id, message_id=message_id, status=status, ...
        )
```

All call sites of `notify_task_update(...)` become `self.response_handler.notify_task_update(...)`. Since the handler already has `self._broadcaster`, no new wiring is needed.

#### Call-Site Audit for `notify_task_update`

| Caller | File | Refactoring |
|---|---|---|
| `_notify()` helper | `agent_response_handler.py` | Already on the handler — becomes `self.notify_task_update()` |
| `_emit_terminal()` | `modules/transports/direct.py` | Calls `response_handler.notify_task_update(...)` |
| Stale task recovery | `jobs/stale_task_checker.py` | Needs handler reference or calls `notify_task_update` via the handler |
| Any other callers | Grep for `notify_task_update` | Mechanically update to use handler method |

#### Ordering Preservation

`notify_task_update` is currently called **after** `tsm.transition_task()` persists the terminal state to DB. The function reads the persisted message, then sends the SSE event. This ordering is preserved — the only change is the delivery mechanism (`broadcaster.task_update` instead of `sse_manager.send_task_update`).

The `_finalize_streaming` ordering constraint from EVENT_PIPELINE_DESIGN §4.8 remains intact: the final artifact chunk broadcasts via `broadcaster.artifact_update`, is `await`ed, and only then does `notify_task_update` fire the terminal `broadcaster.task_update`.

### 4.9 Prerequisite: `processing_status` Side-Effect Separation (EVENT_PIPELINE_DESIGN §4.2)

`EVENT_PIPELINE_DESIGN.md` §4.2 flagged that `SSEManager.send_processing_status` is **not a pure broadcast** — it performs three operations:

1. **Terminal-status deduplication** via `_terminal_status_sent` TTLCache
2. **DB persistence** via **`RunCommandHandler.record_processing_status`** (`runs` / `run_events`; **`FEATURE_RUN_DUAL_WRITE`**)
3. **SSE broadcast** to connected clients

`LocalBroadcaster.processing_status()` delegates to `SSEManager.send_processing_status`, inheriting all three behaviors. But when `RedisBroadcaster` publishes to Redis and the `EventSubscriber` on a remote instance calls `SSEManager.send_processing_status`, it would **re-execute the DB persistence on every receiving instance** — writing redundant processing status updates and potentially corrupting CAS (compare-and-swap) semantics.

#### The Fix

Split `send_processing_status` into side-effect and broadcast components:

```python
# SSEManager — new method
async def broadcast_processing_status(
    self, room_id, status, *, message_id=None, details=None,
    client_request_id=None,
):
    """Pure broadcast — no DB writes, no dedup checks.
    Called by EventSubscriber on receiving instances.
    """
    event = self._build_processing_status_event(
        room_id, status, message_id=message_id, details=details,
        client_request_id=client_request_id,
    )
    await self.broadcast_to_room(room_id, event)
```

The side-effect logic (dedup + DB persistence) stays in the handler's `_on_processing_status` method, which runs **only on the sender** (the instance that produced the event). The broadcaster emits a pure broadcast payload. The subscriber on remote instances calls the new `broadcast_processing_status` (broadcast-only, no side effects).

#### Data Flow After Fix

```
Sender Instance:
  AgentResponseHandler._on_processing_status(event)
    ├─→ Dedup check (Redis SET NX, §4.3)
    ├─→ DB persistence (record_processing_status → runs / run_events)
    └─→ self._broadcaster.processing_status(...)
          ├─→ Local: SSEManager.broadcast_processing_status(...)   [broadcast only]
          └─→ Redis: PUBLISH room:{room_id}:events {payload}

Remote Instance (via EventSubscriber):
  EventSubscriber._dispatch(payload)
    └─→ SSEManager.broadcast_processing_status(...)               [broadcast only]
```

The dedup check and DB persistence run **exactly once** on the sender. All instances (including the sender) receive the pure broadcast for local SSE delivery.

#### Impact on EventSubscriber Dispatch

The `EventSubscriber._dispatch` method must route `processing_status` events to `broadcast_processing_status` instead of `send_processing_status`:

```python
async def _dispatch(self, payload: dict) -> None:
    method = payload["method"]
    args = payload["args"]
    if method == "processing_status":
        await self._sse.broadcast_processing_status(**args)
    else:
        handler = getattr(self._sse, f"send_{method}", None)
        if handler:
            await handler(**args)
```

### 4.10 Prerequisite: Persistence Path Unification (EVENT_PIPELINE_DESIGN §4.7)

`EVENT_PIPELINE_DESIGN.md` §4.7 documented two incompatible persistence APIs for streaming artifacts:

| API | Used by | Mechanism | Concurrency model |
|---|---|---|---|
| `TaskStateManager.persist_message(message)` | DirectTransport | Full-document replace via `update_agent_message_by_message_id` | Single-writer: safe because DirectTransport owns the in-memory `RoomAgentMessage` and writes the whole document |
| `DatabaseService.accumulate_artifact_on_message(msg_id, artifact, append)` | Handler (`_on_artifact`) for Relay/Webhook | Atomic MongoDB `$push` / `$set` on the artifact array | Multi-writer safe: no read-modify-write |

The EVENT_PIPELINE_DESIGN deferred unification, noting that "a future phase can unify both paths onto `accumulate_artifact_on_message` once the in-memory `MessageStreamingState` is refactored." That future phase is now.

#### Why Unify Now

While the two paths don't create new *race conditions* under horizontal scaling (room processing is still sequential), they create **operational complexity** that compounds with every other prerequisite in this design:

1. **Two code paths to reason about.** The handler's `_on_artifact` uses atomic ops; DirectTransport's streaming path uses full-document replace. Any bug fix, schema change, or S3 conversion improvement must be applied in both places.
2. **`skip_persist` flag proliferation.** DirectTransport events set `skip_persist=True` to prevent double-persistence. This flag exists solely to accommodate the dual-path design — it adds a conditional branch on every event that the handler processes.
3. **Blocks clean handler ownership.** §4.8 moves `notify_task_update` into the handler. §4.9 moves `processing_status` side effects into the handler. If artifact persistence also lives in the handler, the handler becomes the *single owner* of all persistence — a much cleaner architecture than "handler owns everything except streaming artifact persistence, which DirectTransport owns."
4. **Prerequisite chain simplification.** With unified persistence, the `skip_persist` flag on `AgentEvent` can be removed entirely. All events flow through the handler, which decides what to persist. Transports become pure normalizers.

#### The Fix

Converge all streaming artifact persistence onto `accumulate_artifact_on_message` (atomic ops). Remove DirectTransport's `tsm.persist_message` calls for artifact chunks. The handler's `_on_artifact` becomes the single persistence path for all three transports.

**Before:**
```
DirectTransport                    RelayTransport / WebhookTransport
      │                                      │
      ├→ tsm.persist_message()               ├→ handler._on_artifact()
      │   (full-doc replace)                 │     ├→ S3 conversion
      │                                      │     ├→ accumulate_artifact_on_message()
      ├→ handler.handle(AgentEvent(          │     └→ broadcaster.artifact_update()
      │     skip_persist=True))              │
      │     └→ broadcaster.artifact_update() │
```

**After:**
```
DirectTransport    RelayTransport    WebhookTransport
      │                  │                  │
      └──────────────────┴──────────────────┘
                         │
                  handler._on_artifact()
                    ├→ S3 conversion
                    ├→ accumulate_artifact_on_message()  (single path)
                    └→ broadcaster.artifact_update()
```

#### What Changes in DirectTransport

1. **Remove `tsm.persist_message()` calls for artifact chunks** (currently at `direct.py` L827 and similar). The handler now persists via atomic ops.
2. **Remove `skip_persist=True` from artifact `AgentEvent` construction.** Streaming artifact events no longer set this flag — the handler's `_on_artifact` is the single persistence path.
3. **Keep `skip_persist=True` for terminal `AgentEvent`s** (`_emit_terminal`). Terminal events (`response`, `error`, `canceled`) still set `skip_persist=True` because `tsm.transition_task()` already persisted the terminal state (status, error, completion time) via full-document replace. Without this flag, `_on_response` / `_on_error` would re-persist, causing a double-write. The `skip_persist` field stays on `AgentEvent` but is now only used by terminal event kinds — not artifact streaming.
4. **Keep `tsm.transition_task()` for terminal state transitions.** Terminal persistence is a different concern — it writes task state, not artifacts. This path is already clean and single-writer.
5. **DirectTransport's `MessageStreamingState`** continues to accumulate content in-memory for the `full_response_text` assembly (needed for coordinator summaries, non-text-part extraction, etc.). The in-memory state is still useful for *content assembly*; it just no longer drives *persistence*.

#### Ordering Preservation

The current flow is:
1. DirectTransport accumulates artifact in memory
2. `tsm.persist_message()` writes full document to DB
3. Handler broadcasts via `broadcaster.artifact_update()`

After unification:
1. DirectTransport accumulates artifact in memory (unchanged — still useful for content assembly)
2. Handler receives `AgentEvent(kind="artifact_update")`
3. Handler calls `accumulate_artifact_on_message()` — atomic DB write
4. Handler calls `broadcaster.artifact_update()` — SSE delivery

Since handler methods are `await`ed sequentially, persistence completes before broadcast. This is slightly different from the current order (DirectTransport persists *before* emitting the event to the handler), but the effect is the same: DB is updated before SSE delivery. The frontend's DB reconciliation reads the persisted state, which is consistent either way.

#### Risk: Partial Artifact on Crash

With the current design, DirectTransport persists each chunk immediately via `tsm.persist_message()`. If the process crashes mid-stream, the DB has all chunks received so far (as a full-document snapshot).

With atomic ops, each chunk is appended individually via `$push`. If the process crashes mid-stream, the DB has all chunks received so far (as individually appended entries). The recovery semantics are identical — the `StaleTaskChecker` detects the orphaned message and either retries or fails it.

The only difference is the *write pattern* during normal operation: full-doc replace (current) vs. atomic append (unified). Atomic append is strictly safer under concurrent access — though concurrent access doesn't occur today for DirectTransport, it removes a latent hazard.

### 4.11 Load Balancer Requirements

**No sticky sessions required.** The design is fully stateless from the load balancer's perspective:

- **SSE connections** are long-lived but instance-independent. Events reach any instance via Redis Pub/Sub. A reconnecting client can land on any instance.
- **REST API calls** are stateless (JWT auth, no server-side sessions).
- **Hub relay connections** are long-lived but use Redis Streams with `last_event_id`. A reconnecting hub can land on any instance and resume from its last-seen position.

Recommended load balancer configuration:

| Setting | Value | Rationale |
|---|---|---|
| Algorithm | Round-robin | Simplest; no affinity needed |
| Idle timeout | 120s | Must exceed SSE heartbeat interval (30s) with margin for agent polling (120s) |
| Health check | `GET /api/v1/health` | Backend already has a health endpoint |
| Connection draining | 30s | Allow in-flight SSE events to deliver before instance shutdown |
| `X-Forwarded-For` | Enabled | For rate limiting and logging |
| WebSocket upgrade | Not needed | SSE only; no WebSocket support |

### 4.12 Frontend Impact

The frontend requires **zero code changes** for basic horizontal scaling. The architecture is already resilient:

1. **Auth is stateless.** JWT via `Authorization` header on REST calls, JWT via query param on SSE. No cookies or server-side sessions.

2. **SSE reconnection recovers from DB.** On reconnect, `useRoomSSEConnection` runs three recovery steps: HITL catch-up (REST fetch), safety-net reconciliation (REST refetch of `room.processing_message_id`), and full DB reconciliation (`reconcileWithDb()` fetches all messages). This works regardless of which instance the new SSE connection lands on.

3. **Source-priority conflict resolution.** The `upsert.ts` module resolves SSE vs. DB conflicts with `sse > db > optimistic` ordering. Duplicate events from the brief window during reconnect are handled gracefully.

4. **Cancel is a REST call.** `POST /sse/message/{messageId}/cancel` hits the load balancer; the receiving instance writes to Redis, and the processing instance picks up the signal.

**One optional improvement**: Add jitter to the SSE reconnection backoff. The current linear backoff (1s, 2s, 3s, 4s, 5s) has no jitter. If an instance goes down and N clients reconnect simultaneously, they'll hit the remaining instances in synchronized waves. Adding ±30% jitter spreads the load:

```typescript
// SSEConnection.attemptReconnect — optional improvement
const baseDelay = this.reconnectDelay * this.reconnectAttempts
const jitter = baseDelay * 0.3 * (Math.random() * 2 - 1)
const delay = Math.max(500, baseDelay + jitter)
```

### 4.13 Redis Configuration

#### Single-Instance vs. Cluster

For Hybro's current scale (estimated <100 concurrent rooms, <1000 SSE connections), a single Redis instance with persistence is sufficient. Redis Cluster is only needed at >10K concurrent Pub/Sub channels or >100K messages/second.

#### Persistence

Enable Redis AOF (append-only file) persistence for hub relay streams and leader election keys. Pub/Sub messages are ephemeral by design — they are delivered to connected subscribers only, and a brief gap during Redis restart is acceptable (SSE reconnection handles it).

```
# redis.conf
appendonly yes
appendfsync everysec
```

#### Memory Limits

Estimated Redis memory usage:

| Data | Count | Size per entry | Total |
|---|---|---|---|
| Pub/Sub channels | ~100 rooms | ~0 (channels are metadata only) | Negligible |
| Hub relay streams | ~20 hubs × 10K entries | ~1KB per entry | ~200MB |
| Cancellation keys | ~10K concurrent | ~64 bytes | ~640KB |
| Terminal dedup keys | ~10K concurrent | ~64 bytes | ~640KB |
| Leader election keys | 5 jobs | ~64 bytes | ~320 bytes |
| Hub heartbeat keys | ~20 hubs | ~64 bytes | ~1.3KB |
| **Total** | | | **~200MB** |

Set `maxmemory 512mb` with `maxmemory-policy noeviction` to prevent silent data loss. Monitor with `INFO memory`.

---

## 5. Migration Plan

### Phase 1: Redis Infrastructure + RedisBroadcaster (SSE Fan-Out)

**Scope**: 3 new files, 6 modified files. Largest user-facing impact.

**Prerequisites**: `EVENT_PIPELINE_DESIGN.md` Phases 1–3 complete (handler uses `EventBroadcaster` protocol, DirectTransport consolidated, `sse_manager` removed from DirectTransport).

**Step 0 — Resolve deferred debts from EVENT_PIPELINE_DESIGN:**

0a. Move `notify_task_update` from `services/task_notification_service.py` into `AgentResponseHandler` as a method. Update all call sites (`_notify()`, `_emit_terminal()`, stale task recovery) to use `self.response_handler.notify_task_update(...)`. The method calls `self._broadcaster.task_update(...)` instead of `SSEManager.send_task_update` directly. (Resolves EVENT_PIPELINE_DESIGN §4.9.)

0b. Split `SSEManager.send_processing_status` into two methods: the existing method (with dedup + DB persistence side effects) for sender-side use, and a new `broadcast_processing_status` (pure broadcast, no side effects) for subscriber-side use. Move the dedup and DB persistence into the handler's `_on_processing_status`. (Resolves EVENT_PIPELINE_DESIGN §4.2 implementation note.)

0c. Unify artifact persistence onto `accumulate_artifact_on_message` (atomic ops). Remove `tsm.persist_message()` calls for streaming artifact chunks in `DirectTransport`. Remove `skip_persist=True` from **artifact** `AgentEvent` construction only — terminal events (`_emit_terminal`) keep `skip_persist=True` to prevent double-persistence since `tsm.transition_task()` already wrote the terminal state. Keep `tsm.transition_task()` for terminal state transitions (different concern). (Resolves EVENT_PIPELINE_DESIGN §4.7.)

**Step 1 — Redis infrastructure:**

1. Add `redis[hiredis]` and `msgpack` to `pyproject.toml`.
2. Create `services/redis_service.py` — Redis connection pool singleton using `redis.asyncio.Redis` with health checks.
3. Create `modules/redis_broadcaster.py` — `RedisBroadcaster` implementing `EventBroadcaster` protocol.
4. Create `modules/event_subscriber.py` — `EventSubscriber` background coroutine. Routes `processing_status` events to `broadcast_processing_status` (not `send_processing_status`) to avoid re-running side effects.
5. Update `main.py` lifespan to:
   - Initialize Redis connection pool on startup.
   - Start `EventSubscriber.run()` as `asyncio.Task`.
   - Shutdown Redis pool on shutdown.
6. Update wiring site (`modules/RoomMessageCenter.py` or DI factory) to pass `RedisBroadcaster(redis, sse_manager, instance_id)` instead of `LocalBroadcaster(sse_manager)` when `REDIS_URL` is configured.
7. Update `api/sse.py` to call `event_subscriber.subscribe_room()` / `unsubscribe_room()` on connection open/close.

**Validation**: Deploy two instances behind a load balancer. Send a message via Instance A. Verify both streaming events AND the terminal `task_update` arrive at a client connected to Instance B. Verify `processing_status` DB writes happen exactly once (on the sender instance).

**Fallback**: If `REDIS_URL` is not configured, fall back to `LocalBroadcaster` (single-instance mode). This allows gradual rollout and local development without Redis.

> **Implementation note (2026-03):** Silent fallback to local-only was identified as a split-brain risk in PR review. The implementation logs broker failures at ERROR level and returns `"status": "degraded"` from the health endpoint when `REDIS_URL` is configured but the broker is disconnected.

### Phase 2: Shared Cancellation and Dedup State

**Scope**: 2–3 modified files (`services/sse_services.py`, `common/utils/cancellation.py`, cancel API endpoint).

1. Replace `cancelled_messages` TTLCache with Redis `SET NX EX` checks.
2. Replace `_terminal_status_sent` TTLCache with Redis `SET NX EX` checks.
3. Replace `_cancellation_tokens` TTLCache with local token + Redis Pub/Sub signaling.
4. Remove the MongoDB Change Stream watcher for `cancelled_messages` (no longer needed — Redis handles cross-instance cancellation).

**Validation**: Cancel a message via Instance A while it's processing on Instance B. Verify cancellation propagates within 100ms.

### Phase 3: Hub Relay via Redis Streams

**Scope**: 1 new file, 2 modified files.

1. Create `services/redis_relay_service.py` — `RedisRelayService` using Redis Streams.
2. Update `api/relay.py` — SSE endpoint reads from Redis Stream with `last_event_id`.
3. Update all relay producers to use `RedisRelayService.dispatch_to_hub()`.
4. Move hub heartbeat tracking to Redis keys with TTL.
5. Remove in-memory `_hub_queues`, `_offline_queues`, `_last_hub_heartbeat` from `RelayService`.

**Validation**: Start a hub connected to Instance A. Kill Instance A. Hub reconnects to Instance B. Verify no events are lost (hub sends `last_event_id`, receives events from Redis Stream).

### Phase 4: Leader Election for Background Jobs

**Scope**: 1 new file, 5 modified files.

1. Create `common/utils/leader_election.py` — `LeaderElection` class.
2. Wrap each background job's main loop with leader check:
   - `jobs/stale_task_checker.py`
   - `jobs/compaction_sweep.py`
   - `jobs/cleanup_orphaned_uploads.py`
   - `services/agent_health_service.py`
   - Relay heartbeat monitor (in `services/relay_service.py` or `RedisRelayService`)

**Validation**: Run 3 instances. Verify only one runs each background job. Kill the leader. Verify another instance picks up leadership within one interval.

### Phase 5: Operational Hardening

**Scope**: Monitoring, alerting, graceful degradation.

1. Add Redis health check to `GET /api/v1/health` endpoint.
2. Add Prometheus metrics (or OpenTelemetry):
   - `hybro_redis_pubsub_publish_total` — events published
   - `hybro_redis_pubsub_receive_total` — events received from other instances
   - `hybro_redis_pubsub_latency_ms` — publish-to-receive latency
   - `hybro_redis_stream_depth` — hub relay stream depth per hub
   - `hybro_leader_election_acquisitions_total` — leader elections won
   - `hybro_sse_connections_active` — per-instance SSE connection count
3. Add graceful degradation: if Redis is unreachable, fall back to local-only delivery (single-instance mode) with a health status flag that alerts operators.

> **Implementation note (2026-03):** Silent fallback to local-only was identified as a split-brain risk in PR review. The implementation logs broker failures at ERROR level and returns `"status": "degraded"` from the health endpoint when `REDIS_URL` is configured but the broker is disconnected.

4. Add connection draining on shutdown: stop accepting new SSE connections, wait for in-flight events, then close.

---

## 6. Risks and Mitigations

| Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|
| **Redis SPOF** — single Redis instance goes down | All cross-instance delivery stops; local delivery continues | Medium | Phase 5 graceful degradation falls back to local-only mode. Deploy Redis with persistence + sentinel for automatic failover. |
| **Pub/Sub message loss** — Redis Pub/Sub is fire-and-forget; no persistence | Events emitted while an instance is briefly disconnected from Redis are lost | Low | SSE reconnection + DB reconciliation recovers missed events. Pub/Sub loss only affects real-time delivery, not data integrity. |
| **msgpack schema evolution** — adding fields to broadcast payloads | Old instances can't deserialize new fields | Low | msgpack handles unknown fields gracefully (ignores them). Use dict-based payloads, not positional tuples. |
| **Thundering herd on instance failure** — all clients from failed instance reconnect simultaneously | Remaining instances overwhelmed by reconnection burst | Medium | Add jitter to frontend reconnect backoff (§4.12). Load balancer health checks remove failed instance quickly. |
| **Redis Stream unbounded growth** — hub goes offline permanently | Stream grows to maxlen cap (10K entries) then stabilizes | Very Low | `XADD maxlen 10000` enforces a hard cap. Monitor with `XLEN`. |
| **Leader election split-brain** — TTL expires while job is still running | Two instances run the same job briefly | Low | Jobs are idempotent (stale task checker uses atomic claims, compaction sweep uses per-room locks). Brief overlap is harmless. |
| **Event ordering across instances** — Redis Pub/Sub does not guarantee ordering across publishers | SSE events from different instances may arrive out of order | Low | Within a single room, processing is sequential (one message at a time). Multiple publishers to the same room channel don't occur in normal flow. Edge case: cancel + terminal event from different instances — frontend handles both idempotently. |
| **Local + remote duplicate delivery** — self-publish optimization delivers locally AND subscriber receives from Redis before `origin` check | Client sees duplicate event | Very Low | Subscriber filters `origin == self._instance_id`. Race window is <1ms. Frontend `upsert.ts` deduplicates by `message_id`. |
| **`notify_task_update` bypass (if Step 0a skipped)** — terminal `task_update` events go through `SSEManager` directly, never reaching Redis Pub/Sub | Terminal events (completed, failed, canceled) invisible to clients on other instances — task bubbles stuck in "working" state forever | **High** | Step 0a is mandatory. If deferred, terminal events from all three transports are broken in multi-instance. Cannot ship Phase 1 without it. |
| **`processing_status` side-effect duplication (if Step 0b skipped)** — subscriber calls `send_processing_status` on remote instances, re-running DB persistence and CAS dedup | Redundant DB writes per instance; potential CAS corruption (`clear_if_matches` runs N times) | Medium | Step 0b separates broadcast from side effects. If deferred, DB writes multiply by instance count — functionally harmless but operationally wasteful and could corrupt CAS semantics under race conditions. |
| **Persistence dual-path retained (if Step 0c skipped)** — DirectTransport continues using `tsm.persist_message` for artifact chunks while handler uses `accumulate_artifact_on_message` | Two code paths for the same data; `skip_persist` flag proliferation; handler not the single owner of persistence; future schema/S3 changes must be applied in two places | Low | Step 0c is not a scaling blocker (both paths write to the same MongoDB), but skipping it leaves the architecture in a messy intermediate state that contradicts the "handler owns all persistence" principle established by Steps 0a and 0b. Strongly recommended before Phase 1 for architectural cleanliness. |

---

## 7. Testing Strategy

### Unit Tests

1. **`RedisBroadcaster` protocol conformance** — Verify `isinstance(broadcaster, EventBroadcaster)`.
2. **Publish payload structure** — For each `EventBroadcaster` method, verify Redis `PUBLISH` is called with the correct channel and msgpack-encoded payload.
3. **Self-publish dedup** — Verify local `SSEManager` receives the event AND Redis `PUBLISH` is called, but subscriber skips messages with matching `origin`.
4. **Leader election** — Verify `try_acquire` returns True on first call, False on second (different instance). Verify `renew` extends TTL only for the current leader. Verify `release` only deletes if current leader.
5. **Cancellation cross-instance** — Verify cancel signal published to Redis triggers local `CancellationToken.cancel()` on receiving instance.

### Integration Tests

6. **Two-instance fan-out** — Start two instances sharing a Redis instance. Connect SSE client to Instance A. Process a message on Instance B. Verify SSE events arrive at the client.
7. **Hub relay reconnect** — Connect hub to Instance A. Kill Instance A. Hub reconnects to Instance B. Verify events dispatched after kill are delivered via Redis Stream.
8. **Leader failover** — Start two instances. Verify only one runs `stale_task_checker`. Kill the leader. Verify the other picks up within one interval.
9. **Graceful degradation** — Kill Redis. Verify local SSE delivery continues. Verify health endpoint reports unhealthy. Restart Redis. Verify cross-instance delivery resumes.

### Load Tests

10. **Pub/Sub throughput** — Simulate 100 rooms × 10 msg/s artifact updates. Verify Redis CPU < 20% and publish-to-receive latency < 5ms p99.
11. **Stream depth** — Simulate 20 hubs with 1 offline for 10 minutes. Verify stream depth stays within `maxlen` cap and no OOM.

---

## 8. Estimated Level of Effort

| Phase | Scope | LOE | Dependencies |
|---|---|---|---|
| Phase 1 (Step 0) | Prerequisite refactors: `notify_task_update` migration + `processing_status` split + persistence unification | 3–4 days | EVENT_PIPELINE_DESIGN Phases 1–3 |
| Phase 1 (Steps 1–7) | Redis infra + RedisBroadcaster + EventSubscriber | 3–4 days | Step 0 |
| Phase 2 | Shared cancellation + dedup state | 1–2 days | Phase 1 |
| Phase 3 | Hub relay via Redis Streams | 2–3 days | Phase 1 |
| Phase 4 | Leader election for background jobs | 1–2 days | Phase 1 |
| Phase 5 | Operational hardening (metrics, degradation, draining) | 1–2 days | Phase 1 |
| **Total** | | **11–17 days** (Phases 2–5 can partially parallelize) | |

Phases 2, 3, and 4 are independent of each other and can be worked in parallel after Phase 1 lands. Phase 5 depends on Phase 1 for metrics plumbing but can be started concurrently.

---

## 9. Dependency Graph

```
EVENT_PIPELINE_DESIGN.md
  Phase 1: EventBroadcaster protocol + LocalBroadcaster
  Phase 2: DirectTransport consolidation
  Phase 3: Remove sse_manager from DirectTransport
      │
      ▼
HORIZONTAL_SCALING_DESIGN.md (this document)
  Phase 1 Step 0: Prerequisite refactors ────────────────────────┐
      │  (a) notify_task_update → handler method                 │
      │  (b) processing_status side-effect split                 │
      │  (c) persistence unification → accumulate_artifact       │
      │      ↑ requires EVENT_PIPELINE Phase 2-3: handler must   │
      │        use broadcaster for artifact events before 0c     │
      │        can make handler the single persistence path      │
      ▼                                                          │
  Phase 1 Steps 1-7: Redis infra + RedisBroadcaster ────────────┤
      │                                                          │
      ├──→ Phase 2: Shared cancellation/dedup                    │
      │         (removes MongoDB Change Stream watcher)          │
      │                                                          │
      ├──→ Phase 3: Hub relay via Redis Streams                  │
      │         (replaces in-memory relay queues)                │
      │                                                          │
      ├──→ Phase 4: Leader election for background jobs          │
      │                                                          │
      └──→ Phase 5: Operational hardening                        │
                                                                 │
                                                                 ▼
                                                     PRODUCTION MULTI-INSTANCE
                                                     (2+ instances behind LB)
```

### Relationship to Other Design Docs

| Design Doc | Relationship |
|---|---|
| `EVENT_PIPELINE_DESIGN.md` | **Hard prerequisite** — `EventBroadcaster` protocol is the integration point. Phases 1–3 must be complete (broadcaster protocol, DirectTransport consolidation, `sse_manager` removed). Three deferred debts from that design (§4.9 `notify_task_update` bypass, §4.2 `processing_status` side effects, §4.7 persistence path unification) are resolved here in Phase 1 Step 0. |
| `NATIVE_SSE_MIGRATION_DESIGN.md` | **Independent** — `Last-Event-ID` (§3.4) complements this design by adding reconnect replay within a single instance. Can be implemented before or after. |
| `CONCURRENCY_ROADMAP.md` Layer C | **Superseded for locking** — this design uses Redis SETNX for distributed locking instead of MongoDB advisory locks. Layer A (atomic operators) and Layer B (OCC) remain valid. |
| `SYSTEM_DESIGN_REVIEW.md` §2.1 | **Resolves** — SSE fan-out via Redis Pub/Sub. |
| `SYSTEM_DESIGN_REVIEW.md` §2.2 | **Orthogonal** — durable task queue is a separate workstream. This design enables multi-instance without requiring a task queue. |
| `SYSTEM_DESIGN_REVIEW.md` §2.15 | **Resolves** — hub relay SPOF via Redis Streams. |

---

## Appendix A: File Change Summary

| File | Phase | Change |
|---|---|---|
| `services/task_notification_service.py` | 1 (Step 0a) | Remove `notify_task_update` function (moved to handler) |
| `modules/agent_response_handler.py` | 1 (Step 0a) | Add `notify_task_update` method using `self._broadcaster.task_update(...)` |
| `modules/transports/direct.py` | 1 (Step 0a) | Update `_emit_terminal` to call `response_handler.notify_task_update(...)` |
| `jobs/stale_task_checker.py` | 1 (Step 0a) | Update recovery path to call handler's `notify_task_update` |
| `services/sse_services.py` | 1 (Step 0b) | Add `broadcast_processing_status` (pure broadcast, no side effects); dedup + DB persistence remain in `send_processing_status` for local use only |
| `modules/agent_response_handler.py` | 1 (Step 0b) | `_on_processing_status` gains dedup + DB persistence logic (moved from SSEManager) |
| `modules/transports/direct.py` | 1 (Step 0c) | Remove `tsm.persist_message()` calls for artifact streaming chunks; remove `skip_persist=True` from artifact `AgentEvent` construction (terminal events keep it). Keep `tsm.transition_task()` for terminal states. |
| `modules/agent_response_handler.py` | 1 (Step 0c) | `_on_artifact` becomes the single persistence path for all transports via `accumulate_artifact_on_message()` |
| `models/agent_event.py` (or equiv.) | 1 (Step 0c) | `skip_persist` field remains on `AgentEvent` but is now only used by terminal event kinds. Document that artifact events must not set it. |
| `tests/test_direct_transport.py` | 1 (Step 0c) | Update assertions: `persist_message` no longer called for artifact chunks; verify `_on_artifact` → `accumulate_artifact_on_message` flow |
| `tests/test_transport_parity.py` | 1 (Step 0c) | All three transports now use the same persistence path — simplify parity assertions |
| `pyproject.toml` | 1 | Add `redis[hiredis]`, `msgpack` dependencies |
| `config/settings.py` | 1 | Add `REDIS_URL`, `HYBRO_INSTANCE_ID` settings |
| `services/redis_service.py` | 1 | **New** — Redis connection pool singleton |
| `modules/redis_broadcaster.py` | 1 | **New** — `RedisBroadcaster` implementing `EventBroadcaster` |
| `modules/event_subscriber.py` | 1 | **New** — `EventSubscriber` background coroutine (routes `processing_status` to `broadcast_processing_status`) |
| `main.py` | 1 | Initialize Redis pool and EventSubscriber on startup |
| `modules/RoomMessageCenter.py` | 1 | Conditional wiring: `RedisBroadcaster` if `REDIS_URL`, else `LocalBroadcaster` |
| `api/sse.py` | 1 | Subscribe/unsubscribe room on connection open/close |
| `services/sse_services.py` | 2 | Replace TTLCaches with Redis checks; remove Change Stream watcher |
| `common/utils/cancellation.py` | 2 | Add Redis Pub/Sub signaling for cross-instance cancel |
| `services/redis_relay_service.py` | 3 | **New** — `RedisRelayService` using Redis Streams |
| `api/relay.py` | 3 | Read from Redis Stream with `last_event_id` |
| `services/relay_service.py` | 3 | Remove in-memory queues (or keep as fallback) |
| `common/utils/leader_election.py` | 4 | **New** — `LeaderElection` class |
| `jobs/stale_task_checker.py` | 4 | Wrap with leader check |
| `jobs/compaction_sweep.py` | 4 | Wrap with leader check |
| `jobs/cleanup_orphaned_uploads.py` | 4 | Wrap with leader check |
| `services/agent_health_service.py` | 4 | Wrap with leader check |
| `api/health.py` | 5 | Add Redis health check |

---

## Appendix B: Single-Instance Compatibility

The design must support single-instance deployment (local development, staging) without Redis:

```python
# modules/RoomMessageCenter.py — wiring logic
if settings.REDIS_URL:
    redis_pool = await create_redis_pool(settings.REDIS_URL)
    broadcaster = RedisBroadcaster(redis_pool, sse_manager, INSTANCE_ID)
    subscriber = EventSubscriber(redis_pool, sse_manager, INSTANCE_ID)
    asyncio.create_task(subscriber.run())
else:
    broadcaster = LocalBroadcaster(sse_manager)
```

All Redis-dependent features (shared cancellation, hub relay streams, leader election) have local fallbacks:

| Feature | With Redis | Without Redis |
|---|---|---|
| SSE fan-out | Redis Pub/Sub | Local `SSEManager` only |
| Cancellation | Redis hash + Pub/Sub | Local TTLCache + MongoDB Change Stream |
| Terminal dedup | Redis SET NX | Local TTLCache |
| Hub relay | Redis Streams | In-memory `asyncio.Queue` (current behavior) |
| Leader election | Redis SETNX | All instances run all jobs (current behavior) |
| Background jobs | Leader-elected | All instances run all jobs |

This ensures `pip install && python -m hybro` works without Redis, and multi-instance mode activates by setting a single `REDIS_URL` environment variable.

---

## Appendix C: Gap Resolution Log — EVENT_PIPELINE_DESIGN.md Deferred Debts

_All deferred debts from `EVENT_PIPELINE_DESIGN.md` that are relevant to horizontal scaling or architectural cleanliness have been resolved in this document. Only the middleware pipeline (§9) remains correctly deferred._

| Deferred Item | EVENT_PIPELINE Section | Issue | Resolution in This Document |
|---|---|---|---|
| `notify_task_update` bypasses broadcaster | §4.9 | Terminal `task_update` events go through `SSEManager.send_task_update` directly, never reaching the `EventBroadcaster`. In multi-instance, terminal events are invisible to clients on other instances. | §4.8: Move `notify_task_update` into `AgentResponseHandler` as a method that calls `self._broadcaster.task_update(...)`. Migration Phase 1 Step 0a. |
| `processing_status` has non-broadcast side effects | §4.2 (impl note) | `SSEManager.send_processing_status` performs dedup + DB persistence alongside broadcast. If subscriber calls it on remote instances, side effects run N times. | §4.9: Split into `send_processing_status` (side effects, sender-only) and `broadcast_processing_status` (pure broadcast, subscriber-safe). EventSubscriber routes to the latter. Migration Phase 1 Step 0b. |
| Persistence path unification (DirectTransport vs. handler) | §4.7 | Two persistence APIs (`tsm.persist_message` vs. `accumulate_artifact_on_message`) for the same data. | §4.10: Converge streaming artifact persistence onto `accumulate_artifact_on_message` (atomic ops). Remove `tsm.persist_message` for artifact chunks from DirectTransport. Terminal events keep `skip_persist=True` (already persisted by `tsm.transition_task`). Handler's `_on_artifact` becomes the single artifact persistence path. Migration Phase 1 Step 0c. |
| Middleware pipeline | §9 | Handler could graduate to a middleware chain if 3+ cross-cutting concerns emerge. | **Not resolved here — correctly deferred.** Redis pub/sub and the broadcaster are not cross-cutting concerns in the middleware sense; they are delivery mechanisms. No new middlewares are needed for horizontal scaling. |
