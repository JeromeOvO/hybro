# Phase 6 Delivery Module Extraction Implementation Plan

> **Execution note:** Use the repository's standard implementation-plan workflow for this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract `delivery/` so it owns SSE connection management, Redis-backed cross-instance fan-out, frontend frame translation, terminal status deduplication, cancellation propagation, and internal event dispatch through the Common `EventPublisher` and `SSETransport` protocols.

**Architecture:** Build a pure Delivery package that imports only stdlib/third-party utilities plus `common.*` and relative `delivery.*` modules. DAL dependencies are injected through Common protocols (`MongoCollection`, `RedisKV`, `RedisPubSub`); `container.py` owns concrete `dal.*` imports, with `main.py` using container helpers rather than constructing concrete DAL clients directly. `DeliveryFacade` wires `EventPublisherImpl` and `SSETransportImpl`; `services/sse_services.py` becomes a C3 migration adapter that delegates to the facade while preserving existing call sites until Phase 7b removes direct `sse_manager` usage.

**Tech Stack:** Python 3.11+, FastAPI SSE response format, Redis Pub/Sub via `common.protocols.RedisPubSub` and `dal.redis.RedisPubSubImpl`, Redis KV via `common.protocols.RedisKV`, MongoDB change streams via `common.protocols.MongoCollection`, `cachetools.TTLCache`, Pydantic DTOs in `common.dto`, pytest, pytest-asyncio, AST import-boundary tests.

---

## Scope

Include:
- Create the `delivery/` package on branch `phase-6-delivery-module`.
- Implement `EventPublisher` and `SSETransport` runtime protocols from `common/protocols/delivery_protocols.py`.
- Port local SSE connection management, queueing, heartbeat behavior, draining mode, and room status behavior from `services/sse_services.py`.
- Port terminal processing-status deduplication with L1 `TTLCache` and L2 Redis `SET NX` semantics.
- Port cancellation state, cancellation tokens, Redis cancellation propagation, and per-worker Mongo change-stream watcher.
- Implement Redis Pub/Sub fan-out for frontend-visible SSE frames and internal-only domain events.
- Implement pure DomainEvent-to-SSE-frame translation for all 9 current `DeliveryEvent` variants.
- Implement `emit_internal()` and `register_internal_handler()` for `MessageCommitted`, `RunStateChanged`, and `HubAgentResponseInternal`.
- Convert `services/sse_services.py` to a fail-fast C3 migration adapter that delegates to the new delivery facade.
- Wire Delivery in `container.py` and bind the legacy `sse_manager` during app startup.
- Add unit, adapter, and import-boundary tests.

Exclude:
- Do not migrate Execution callers to `EventPublisher.emit()`; Phase 7b owns that.
- Do not build `execution/` or `ExecutionFacade`.
- Do not remove the legacy `services/sse_services.py` module path.
- Do not change API routes or frontend SSE response format.
- Do not move or delete `infrastructure/event_broker.py` or `infrastructure/brokers/redis_broker.py`.
- Do not import business modules from `delivery/**`: no `agent`, `room`, `context_memory`, `execution`, `hub_runtime_bridge`, `jobs`, `platform_module`, `modules`, `services`, `models`, `api`, `database`, `main`, `container`, or `config`.
- Do not call `run_command_handler.record_processing_status()` from Delivery or the C3 adapter. Phase 6 is blocked until Phase 7a has moved recording to callers across all production call sites.

## Current Repo Check

Before implementation:
- Branch setup: `git switch main`, then `git switch -c phase-6-delivery-module`.
- `delivery/` currently has no source files, only stale `__pycache__` files.
- `common/protocols/delivery_protocols.py` already exports `EventPublisher` and `SSETransport`.
- `common/dto/delivery.py` already defines `ProcessingStatusEvent`, `RunEventNotification`, `AgentMessagePartial`, `AgentMessageFinal`, `CancellationEvent`, `HITLRequestEvent`, `HITLResolvedEvent`, `HubAgentEvent`, and `DebateRoundEvent`.
- `common/dto/internal_events.py` already defines `MessageCommitted`, `RunStateChanged`, and `HubAgentResponseInternal`.
- `dal/redis/kv.py` and `dal/redis/pubsub.py` already provide DAL implementations that Delivery can consume through Common protocols, but their Phase 6 failure contract must be tightened so configured Redis driver failures propagate as `TransientError` instead of being silently converted to duplicate/healthy outcomes.
- `dal/mongo/client.py` already exposes `MongoCollectionAdapter.watch()`, so the cancellation watcher can depend on `MongoCollection`.
- `container.py` currently has Agent, Room, and Context & Memory deps, but no `DeliveryDeps` implementation.
- `main.py` currently wires `services.sse_services.sse_manager` directly to legacy infrastructure broker and Redis service.

Critical compatibility notes:
- The Common `DeliveryEvent` union does not cover every legacy SSE method (`task_submitted`, `task_update`, `artifact_update`, `error`, `user_message`, and generic `broadcast_to_room`). Add a private, non-protocol compatibility method on `EventPublisherImpl`, `_emit_legacy_frame(room_id, frame)`, reachable from the C3 adapter only through `DeliveryFacade.compat.emit_legacy_frame()`. Add a reference-boundary check so no production code outside `delivery/facade.py` calls the private method. Phase 7b removes this helper when callers migrate to typed events.
- The legacy `_resolve_client_request_id()` in `services/sse_services.py` imports `services.database_service`, which Delivery cannot import. Keep any temporary DB fallback in the C3 adapter only, or remove it when callers already pass `client_request_id`. Never move this resolver into `delivery/**`.
- Do not widen `common.dto.ProcessingStatusEvent` in Phase 6. The typed DTO remains aligned with `docs/MODULAR_DECOUPLING_DESIGN.md §4.5` (`queued|processing|completed|failed|canceled`, `details: dict | None`) for future typed callers. Legacy `send_processing_status()` must route through `DeliveryFacade.compat.emit_legacy_frame()` and preserve raw legacy payloads, including statuses `processing`, `completed`, `canceled`, `failed`, `rejected`, `rate_limited`, `error`, and `awaiting_input`, plus string `details`. Terminal dedup must still apply to raw legacy terminal statuses via Delivery config; `awaiting_input` is non-terminal.

Phase 7a prerequisite artifacts, external to this Phase 6 plan:
- `services/run_lifecycle_service.py` must already have `RunLifecycleService.record_processing_status()` returning `dict | None` from the payload-returning lifecycle writer.
- Production callers under `modules/`, `services/`, `api/`, and `jobs/` must already record processing status before calling legacy `sse_manager.send_processing_status()`, preserving the legacy `run_event` SSE branch when enabled.
- `tests/test_phase7a_processing_status_gate.py`, `tests/test_phase7a_processing_status_golden.py`, and `tests/fixtures/phase7a_processing_status_callers.json` must already exist and pass, proving the call-site migration, run-event ordering, and transport-only exceptions.
- `docs/superpowers/plans/2026-05-16-phase-7a-delivery-extraction-handoff.md` is a historical audit of post-emit business side effects not covered by the record-before-send gate. Before Delivery extraction starts, each historical item and every current terminal/frontend-visible manifest entry must be covered by the Task 1b manifest-driven coverage audit with exact executable test nodes.
- If any of these artifacts are missing or fail in Task 1, Phase 6 is blocked. Do not implement Phase 7a caller migration, `RunLifecycleService` return-shape changes, or the Phase 7a fixture in the Phase 6 branch.
- Current-worktree status as of 2026-05-16: Phase 7a artifacts are present and `tests/test_phase7a_processing_status_gate.py` passes. Task 1 must still rerun the gate on the implementation branch and stop if it fails.

## File Inventory

Create:
- `delivery/__init__.py`: Task 2 creates a minimal package initializer that imports no non-existent facade/publisher/transport classes. Task 9 updates it to export `DeliveryFacade`, `EventPublisherImpl`, and `SSETransportImpl` after those classes exist.
- `delivery/config.py`: `DeliveryConfig` dataclass with pure literal defaults for heartbeat timeout, Redis prefixes/channels, dead-letter channel, terminal status set, cancellation TTL, dedup TTL, TTL cache maxsizes, Redis reconnect backoff, change-stream backoff, per-worker Redis room-subscription production limit, and drain seconds, plus `DeliveryStartupPolicy`. It must not import `common.config.settings`, top-level `config.settings`, or annotate/accept the full app `Settings` type; resolved primitive Delivery settings are passed in by the container/app shell through a container-owned extraction helper before facade construction.
- `delivery/facade.py`: wires `EventPublisherImpl`, `SSETransportImpl`, `CrossInstanceEventBus`, shared config/state, and the adapter-only `DeliveryCompatibility` accessor.
- `delivery/types.py`: shared Delivery-only typing helpers, including the `TaskRunner` protocol, bus callback protocol/type aliases, and `RoomSubscriptionLimitExceeded`.
- `delivery/event_publisher.py`: concrete `EventPublisher`; owns `emit()`, `emit_internal()`, handler registration, Redis fan-out, incoming broker dispatch, best-effort logging, dead-letter publication, trace propagation, and delivery metrics.
- `delivery/translator.py`: pure `DeliveryEvent` to legacy SSE frame dict translation.
- `delivery/sse/__init__.py`: exports SSE internals needed by tests and adapter.
- `delivery/sse/connection.py`: `SSEConnection` with queue, heartbeat frame generation, JSON compatibility, and close semantics.
- `delivery/sse/manager.py`: `SSETransportImpl`; owns room connection maps, local delivery, dynamic room subscription hooks, draining, room status, and legacy compatibility helpers.
- `delivery/sse/deduplication.py`: terminal status dedup using `TTLCache` and optional `RedisKV`.
- `delivery/sse/cancellation_watcher.py`: cancellation state, cancellation tokens, Redis L2 checks, and Mongo change-stream watcher.
- `delivery/event_bus/__init__.py`: exports `CrossInstanceEventBus`.
- `delivery/event_bus/cross_instance.py`: Redis Pub/Sub fan-out using `RedisPubSub` protocol, not `infrastructure.brokers`.
- `tests/test_delivery_protocols.py`: protocol conformance, exports, packaging, and import-boundary tests.
- `tests/test_delivery_translator.py`: translation tests for all 9 `DeliveryEvent` variants.
- `tests/test_delivery_sse_connection.py`: queue, close, and heartbeat tests.
- `tests/test_delivery_sse_manager.py`: local connection, room status, local broadcast, ordering, cleanup, and draining tests.
- `tests/test_delivery_deduplication.py`: L1/L2 terminal dedup behavior and Redis failure fallback.
- `tests/test_delivery_cancellation.py`: local cancellation, token signaling, Redis L2, broker cancellation, and watcher behavior.
- `tests/test_delivery_event_bus.py`: Redis Pub/Sub envelope, self-dedup, dynamic room subscriptions, cancellation, and internal events.
- `tests/test_delivery_event_publisher.py`: `emit()`, `_emit_legacy_frame()`, fan-out, dead-letter, no-raise, and `emit_internal()` behavior.
- `tests/test_sse_adapter_delivery.py`: legacy `SSEManager.bind_facade()` fail-fast and delegation tests.

Modify:
- `common/protocols/delivery_protocols.py`: update `SSETransport.connect()` to a synchronous protocol method returning `AsyncIterator[dict]` so callers can use `async for frame in transport.connect(...): ...` without awaiting the generator object first.
- `common/protocols/dal_protocols.py`: add public `MongoChangeStream` and update `MongoCollection.watch()` to return an async context manager whose entered value is an async iterator.
- `common/protocols/__init__.py`: export `MongoChangeStream` with the other DAL protocols.
- `dal/mongo/client.py`: update `MongoCollectionAdapter.watch()` public annotation and behavior to match the new `MongoChangeStream` async-context-manager protocol.
- `common/dto/delivery.py`: add optional `trace_id` to `DeliveryEventBase`/`DeliveryEnvelope` and optional `correlation_id` to `RunEventNotification` if not already present, defaulting to `None` for backward compatibility.
- `common/observability/tracing.py`: add `traced_create_task(coro, *, name: str | None = None)`, `get_current_trace_id()`, and `trace_id_context(trace_id: str | None)`.
- `common/observability/__init__.py`: export `traced_create_task`, `get_current_trace_id`, and `trace_id_context`.
- `common/config/settings.py`: add deployment-configurable Delivery settings for every runtime `DeliveryConfig` field: heartbeat interval, shutdown drain seconds, cancellation/terminal TTLs, cancellation/token/terminal cache maxsizes, terminal statuses, Redis channels/prefixes, internal/dead-letter channels, reconnect delays, dead-letter memory max length, handler shutdown timeout, change-stream backoff, Redis pool size, room-subscription limit, and reserved Pub/Sub connection headroom.
- `dal/redis/kv.py`: make configured Redis driver failures raise `TransientError` for Redis data operations (`get()`, `set()`, `delete()`, `increment()`, `setnx()`, and `exists()`) instead of returning silent fallback values.
- `dal/redis/pubsub.py`: make configured Redis driver failures raise `TransientError` for `publish()` and make `subscribe()`/listen failures visible to callers so the Delivery event bus can reconnect.
- `dal/redis/streams.py`: make configured Redis driver failures raise `TransientError` for `xadd()` and `xread()` instead of returning silent fallback values.
- `pyproject.toml`: add `delivery`, `delivery.sse`, and `delivery.event_bus` to `[tool.setuptools].packages`.
- `container.py`: add `DeliveryDeps`, `create_delivery_facade(...)`, and `create_delivery_deps(facade)`.
- `main.py`: obtain DAL protocol objects and extracted collections from `container.py` helpers, bind `services.sse_services.sse_manager` to the new facade, and route SSE health/drain/start/stop through the adapter.
- `services/sse_services.py`: replace implementation with a C3 adapter delegating to a bound facade-like object without importing concrete `delivery.*`; if legacy tests still import `services.sse_services.SSEConnection`, provide a local compatibility wrapper with the old constructor/method shape or retarget those tests to import `delivery.sse.connection.SSEConnection` directly. Never import or re-export concrete Delivery classes from `services/sse_services.py`.
- Existing SSE tests: migrate legacy implementation assertions to either new `delivery/**` tests or adapter delegation tests. If tests currently instantiate `services.sse_services.SSEConnection(room_id="...")` or call `send_message()`, either keep a compatibility wrapper in `services/sse_services.py` with that constructor/method shape or retarget those tests to the new `delivery.sse.connection.SSEConnection` constructor and stop claiming constructor compatibility.
- `docs/MODULAR_DECOUPLING_DESIGN.md`: update Phase 6 status and document the compatibility, Redis failure, tracing, watch-protocol, dead-letter, and shutdown decisions from Task 13. This is an acceptance item, not optional cleanup.
- `tests/test_dal_unit.py`: add concrete Redis KV/PubSub/Streams failure-contract tests and watch protocol conformance coverage where appropriate.

Reference-only:
- `services/sse_services.py`: source behavior to port.
- `infrastructure/brokers/redis_broker.py`: source behavior for current envelope style and reconnect expectations.
- `infrastructure/event_broker.py`: legacy protocol; do not import it from Delivery.
- `common/dto/delivery.py`: frontend-visible event DTOs.
- `common/dto/internal_events.py`: internal event DTOs.
- `common/protocols/dal_protocols.py`: `MongoCollection`, `RedisKV`, and `RedisPubSub`.
- `dal/redis/kv.py`, `dal/redis/pubsub.py`: concrete app-shell implementations.
- `tests/test_dal_protocols.py`: DAL protocol/runtime conformance, including Redis transient failure and Mongo watch shape tests.
- `tests/test_service_sse.py`, `tests/test_sse_event_broker.py`, `tests/test_api_sse.py`, `tests/test_multi_worker_safety.py`: behavior to preserve or re-target.
- `tests/test_phase7a_processing_status_gate.py`, `tests/test_phase7a_processing_status_golden.py`, `tests/fixtures/phase7a_processing_status_callers.json`, and `services/run_lifecycle_service.py`: Phase 7a prerequisite artifacts to verify, not Phase 6 files to implement except where Task 10 must update the golden test to bind the C3 adapter facade.

## Dependency Diagram

```text
api.sse / legacy modules and services
  -> services.sse_services.sse_manager             C3 adapter only
    -> delivery.facade.DeliveryFacade
      -> EventPublisherImpl                        Common EventPublisher
      -> SSETransportImpl                          Common SSETransport
      -> CrossInstanceEventBus                     RedisPubSub protocol
      -> TerminalStatusDeduplicator                RedisKV protocol
      -> CancellationWatcher                       MongoCollection + RedisKV protocols
      -> translator.to_sse_frame                   pure function

container.py                                      composition root
  -> dal.MongoDALImpl / RedisKVImpl / RedisPubSubImpl

main.py                                           app shell, uses container helpers
  -> create_delivery_facade(...)
  -> create_delivery_deps(delivery_facade)
  -> sse_manager.bind_facade(delivery_facade)
```

## Forbidden and Allowed Imports

Allowed from `delivery/**`:
- stdlib modules.
- Third-party libraries already used for delivery primitives, currently `cachetools` and Pydantic `TypeAdapter` if needed.
- `common.*`.
- relative imports inside `delivery`.

DAL dependency rule:
- `delivery/**` depends on DAL through injected Common protocol instances only: `MongoCollection`, `RedisKV`, and `RedisPubSub`.
- Do not import concrete `dal.*` implementations from `delivery/**`; `container.py` creates and passes those implementations. `main.py` should call container helpers and not import concrete `dal.*` implementations for Delivery, Mongo, or Redis construction.

Direct-import forbidden from `delivery/**`:
- `a2a_adapter`
- `agent`
- `api`
- `config`
- `container`
- `context_memory`
- `dal`
- `database`
- `execution`
- `hub_runtime_bridge`
- `infrastructure`
- `jobs`
- `llm_gateway`
- `main`
- `models`
- `modules`
- `platform_module`
- `room`
- `services`

Add `tests/test_delivery_protocols.py::test_delivery_import_boundary` that AST-parses every `delivery/**/*.py` and fails on forbidden direct imports. The AST test should distinguish root module imports, but it must treat global settings singleton access as forbidden even though `common` is otherwise an allowed layer. Reject `from common.config import settings`, `from common.config.settings import settings`, `from config.settings import settings`, `import common.config` / `import common.config.settings` followed by `.settings` access, and dynamic imports such as `importlib.import_module("common.config")`, `importlib.import_module("common.config.settings")`, or `__import__("common.config.settings")` when used to obtain settings. It must also reject dynamic imports of forbidden roots, including `importlib.import_module("services...")` and `__import__("services...")`; Delivery cannot hide settings, business, or concrete DAL dependencies behind dynamic import strings.

Add `tests/test_delivery_protocols.py::test_business_modules_do_not_import_delivery_concretes` that AST-parses all production roots and app-shell files outside the single composition boundary, including `main.py`, `a2a_adapter/`, `agent/`, `common/`, `config/`, `context_memory/`, `dal/`, `database/`, `execution/`, `hub_runtime_bridge/`, `infrastructure/`, `jobs/`, `llm_gateway/`, `models/`, `modules/`, `platform_module/`, `room/`, `services/`, and `api/`, and rejects `import delivery` / `from delivery...` everywhere except `container.py`. The test must also reject dynamic concrete Delivery imports such as `importlib.import_module("delivery...")` and `__import__("delivery...")` in those roots. Production modules, `main.py`, and `services/sse_services.py` may import `common.protocols.delivery_protocols` or DTOs from `common.dto.delivery`, but not concrete `delivery.*`.

Task 11 adds `tests/test_delivery_protocols.py::test_main_does_not_import_or_instantiate_concrete_dal` after moving concrete DAL construction behind container helpers. That test AST-parses `main.py` and fails on concrete DAL imports or constructor references such as `MongoDALImpl`, `VectorDALImpl`, any `*DALImpl`, `RedisKVImpl`, and `RedisPubSubImpl`. Do not add this as an always-on Task 2 test unless it is marked xfail/skip until Task 11, because current `main.py` still violates the rule before the wiring task.

## Interface Definitions

### DeliveryConfig

```python
from dataclasses import dataclass, field

@dataclass(frozen=True)
class DeliveryConfig:
    heartbeat_interval_seconds: float = 30.0
    shutdown_drain_seconds: float = 5.0
    cancellation_ttl_seconds: int = 3600
    terminal_dedup_ttl_seconds: int = 300
    cancellation_cache_maxsize: int = 10_000
    cancellation_token_cache_maxsize: int = 10_000
    terminal_dedup_cache_maxsize: int = 10_000
    redis_sse_channel_prefix: str = "sse:room:"
    redis_cancel_channel: str = "cancel:global"
    redis_internal_channel: str = "internal:global"
    redis_dead_letter_channel: str = "delivery:dead_letter"
    redis_cancel_key_prefix: str = "cancelled:"
    redis_terminal_key_prefix: str = "terminal:"
    dead_letter_memory_maxlen: int = 1000
    handler_shutdown_timeout_seconds: float = 5.0
    redis_reconnect_delay: float = 1.0
    redis_reconnect_max_delay: float = 30.0
    redis_max_connections: int = 50
    redis_subscription_reserved_connections: int = 10
    redis_room_subscription_production_limit: int = 40
    cs_backoff_base: float = 1.0
    cs_backoff_max: float = 30.0
    cs_backoff_factor: float = 2.0
    cs_jitter_fraction: float = 0.25
    terminal_processing_statuses: frozenset[str] = field(
        default_factory=lambda: frozenset(
            {"completed", "failed", "canceled", "rejected", "rate_limited", "error"}
        )
    )

    def __post_init__(self) -> None:
        positive_fields = {
            "heartbeat_interval_seconds": self.heartbeat_interval_seconds,
            "shutdown_drain_seconds": self.shutdown_drain_seconds,
            "cancellation_ttl_seconds": self.cancellation_ttl_seconds,
            "terminal_dedup_ttl_seconds": self.terminal_dedup_ttl_seconds,
            "cancellation_cache_maxsize": self.cancellation_cache_maxsize,
            "cancellation_token_cache_maxsize": self.cancellation_token_cache_maxsize,
            "terminal_dedup_cache_maxsize": self.terminal_dedup_cache_maxsize,
            "dead_letter_memory_maxlen": self.dead_letter_memory_maxlen,
            "handler_shutdown_timeout_seconds": self.handler_shutdown_timeout_seconds,
            "redis_reconnect_delay": self.redis_reconnect_delay,
            "redis_reconnect_max_delay": self.redis_reconnect_max_delay,
            "redis_max_connections": self.redis_max_connections,
            "redis_subscription_reserved_connections": self.redis_subscription_reserved_connections,
            "redis_room_subscription_production_limit": self.redis_room_subscription_production_limit,
            "cs_backoff_base": self.cs_backoff_base,
            "cs_backoff_max": self.cs_backoff_max,
            "cs_backoff_factor": self.cs_backoff_factor,
        }
        for name, value in positive_fields.items():
            if value <= 0:
                raise ValueError(f"{name} must be positive")
        if not 0 <= self.cs_jitter_fraction <= 1:
            raise ValueError("cs_jitter_fraction must be between 0 and 1")
        if self.redis_reconnect_max_delay < self.redis_reconnect_delay:
            raise ValueError("redis_reconnect_max_delay must be >= redis_reconnect_delay")
        if self.cs_backoff_max < self.cs_backoff_base:
            raise ValueError("cs_backoff_max must be >= cs_backoff_base")
        if self.cs_backoff_factor < 1.0:
            raise ValueError("cs_backoff_factor must be >= 1.0")
        string_fields = {
            "redis_sse_channel_prefix": self.redis_sse_channel_prefix,
            "redis_cancel_channel": self.redis_cancel_channel,
            "redis_internal_channel": self.redis_internal_channel,
            "redis_dead_letter_channel": self.redis_dead_letter_channel,
            "redis_cancel_key_prefix": self.redis_cancel_key_prefix,
            "redis_terminal_key_prefix": self.redis_terminal_key_prefix,
        }
        for name, value in string_fields.items():
            if not value:
                raise ValueError(f"{name} must be non-empty")
        if isinstance(self.terminal_processing_statuses, str):
            raise ValueError("terminal_processing_statuses must be an iterable of strings, not a raw string")
        try:
            raw_statuses = list(self.terminal_processing_statuses)
        except TypeError as exc:
            raise ValueError("terminal_processing_statuses must be an iterable of strings") from exc
        if any(not isinstance(status, str) for status in raw_statuses):
            raise ValueError("terminal_processing_statuses must contain only strings")
        normalized_statuses = frozenset(status.strip().lower() for status in raw_statuses)
        if not normalized_statuses or any(not status for status in normalized_statuses):
            raise ValueError("terminal_processing_statuses must be non-empty")
        object.__setattr__(self, "terminal_processing_statuses", normalized_statuses)
        if (
            self.redis_room_subscription_production_limit
            + self.redis_subscription_reserved_connections
            > self.redis_max_connections
        ):
            raise ValueError("room subscription limit plus reserved connections must fit Redis pool")
```

Defaults are pure literals. `delivery/config.py` must not import `common.config.settings`, top-level `config.settings`, any app-shell settings singleton, or the full app `Settings` type. The app shell/container resolves deployment settings into a `DeliveryConfig` and passes that resolved config into `create_delivery_facade()`.

Deployment configurability requirement: add settings/env-backed fields for every runtime `DeliveryConfig` field in `common/config/settings.py` or equivalent Common settings naming. A container-owned helper, for example `container.create_delivery_config(settings)`, must read only the relevant Delivery-owned settings from the full app settings object and construct `DeliveryConfig(...)`. Do not implement a settings-backed classmethod on `DeliveryConfig` inside `delivery/config.py`. The helper must map `heartbeat_interval_seconds`, `shutdown_drain_seconds`, `cancellation_ttl_seconds`, `terminal_dedup_ttl_seconds`, `cancellation_cache_maxsize`, `cancellation_token_cache_maxsize`, `terminal_dedup_cache_maxsize`, `redis_sse_channel_prefix`, `redis_cancel_channel`, `redis_internal_channel`, `redis_dead_letter_channel`, `redis_cancel_key_prefix`, `redis_terminal_key_prefix`, `dead_letter_memory_maxlen`, `handler_shutdown_timeout_seconds`, `redis_reconnect_delay`, `redis_reconnect_max_delay`, `redis_max_connections`, `redis_subscription_reserved_connections`, `redis_room_subscription_production_limit`, `cs_backoff_base`, `cs_backoff_max`, `cs_backoff_factor`, `cs_jitter_fraction`, and `terminal_processing_statuses`. Common settings or the container helper must parse the terminal-status env value into an iterable of strings before constructing `DeliveryConfig`; `DeliveryConfig(terminal_processing_statuses="failed,completed")` must fail because raw strings are ambiguous. Do not leave documented production knobs as code-only defaults.

The default room subscription limit must be compatible with the actual `RedisPubSubImpl` pool. Current `settings.redis_max_connections` defaults to `50`, while Delivery uses long-lived Pub/Sub subscriptions for cancellation/internal channels and one active room channel per subscribed room. `DeliveryConfig` itself validates `redis_room_subscription_production_limit + redis_subscription_reserved_connections <= redis_max_connections`; with the default pool this yields a default room limit of `40`. If a deployment wants 100 active rooms per worker, it must explicitly raise the actual Redis Pub/Sub pool size and the Delivery config together or implement multiplexed Pub/Sub first. Task 2 config tests must reject an unsafe config such as `redis_max_connections=50`, `redis_subscription_reserved_connections=10`, `redis_room_subscription_production_limit=100`; Task 11 then proves the same validated pool size is passed into `RedisPubSubImpl(max_connections=...)`.

`DeliveryConfig` must fail fast on invalid env/settings values at construction time. Task 2 tests must reject non-positive heartbeat/drain/TTL/cache/backoff/reconnect values, `redis_reconnect_max_delay < redis_reconnect_delay`, `cs_backoff_max < cs_backoff_base`, `cs_backoff_factor < 1.0`, `cs_jitter_fraction` outside `[0, 1]`, empty Redis channel/prefix strings, raw-string `terminal_processing_statuses`, non-string terminal statuses, blank terminal statuses, and an empty `terminal_processing_statuses` set. Valid terminal statuses are normalized to a `frozenset[str]` by stripping whitespace and lowercasing.

`DeliveryConfig.redis_max_connections` must not drift from the concrete DAL client. The container must pass the same value into `RedisPubSubImpl(max_connections=...)` or construct both from the same settings object; do not let `RedisPubSubImpl` read a different global `settings.redis_max_connections` internally than the value Delivery validated. Pool-coherence tests belong in Task 6/Task 11 after `RedisPubSubImpl(max_connections=...)` exists, not in the Task 2 skeleton tests.

`shutdown_drain_seconds` exists in Delivery config so the application shell can use the same immutable Delivery configuration for graceful shutdown: `main.py` creates one `delivery_config`, passes it into `create_delivery_facade()`, calls `set_draining(True)`, sleeps `delivery_config.shutdown_drain_seconds`, then closes delivery infrastructure. Do not read a second drain value from global settings during shutdown.

### Runtime Config Fidelity

Every `DeliveryConfig` runtime field must have at least one custom-value usage test in the task that owns the consuming component:
- `heartbeat_interval_seconds`: Task 3 proves SSE heartbeat timeout uses a custom value.
- `terminal_dedup_ttl_seconds`, `terminal_dedup_cache_maxsize`, `terminal_processing_statuses`, and `redis_terminal_key_prefix`: Task 4 proves terminal dedup TTL/cache/status/key behavior uses custom config.
- `cancellation_ttl_seconds`, `cancellation_cache_maxsize`, `cancellation_token_cache_maxsize`, and `redis_cancel_key_prefix`: Task 5 proves cancellation L1/token TTL/cache/key behavior uses custom config.
- `redis_sse_channel_prefix`, `redis_cancel_channel`, `redis_internal_channel`, `redis_dead_letter_channel`, `redis_reconnect_delay`, `redis_reconnect_max_delay`, `redis_room_subscription_production_limit`, `redis_subscription_reserved_connections`, and `redis_max_connections`: Task 6/Task 11 prove event bus channels, backoff, capacity, and pool coherence use custom config.
- `cs_backoff_base`, `cs_backoff_max`, `cs_backoff_factor`, and `cs_jitter_fraction`: Task 5 proves change-stream reconnect/backoff uses custom config through an injected sleeper/backoff recorder or fake clock.
- `handler_shutdown_timeout_seconds` and `dead_letter_memory_maxlen`: Task 8 proves publisher stop timeout and fallback dead-letter deque max length use custom config.
- `shutdown_drain_seconds`: Task 11 proves app-shell graceful shutdown sleeps the resolved config value.

No custom-config usage test should pass if an implementation hard-codes the legacy default literal while ignoring the resolved `DeliveryConfig`.

### DeliveryStartupPolicy

`DeliveryStartupPolicy` lives in `delivery/config.py` next to `DeliveryConfig`, because it is a pure startup configuration DTO and is constructed by the container/app shell before facade creation.

```python
@dataclass(frozen=True)
class DeliveryStartupPolicy:
    redis_expected: bool
    multi_worker: bool
    allow_degraded_change_stream: bool = False

    def __post_init__(self) -> None:
        if self.allow_degraded_change_stream and (self.redis_expected or self.multi_worker):
            raise ValueError("degraded change stream is allowed only in single-worker no-Redis mode")
```

`redis_expected` and `multi_worker` intentionally have no dataclass defaults. Production container wiring must derive and pass an explicit policy; tests should use an explicit fatal fixture such as `DeliveryStartupPolicy(redis_expected=True, multi_worker=True, allow_degraded_change_stream=False)`.

`DeliveryFacade.start()` uses this explicit policy to decide whether initial `MongoCollection.watch()` setup failure is fatal or degraded:
- If `allow_degraded_change_stream` is `False`, initial change-stream setup failure is fatal. `DeliveryFacade.start()` rolls back already-started components, preserves failed health state, and re-raises.
- If `allow_degraded_change_stream` is `True`, initial change-stream setup failure is allowed only when `multi_worker=False` and `redis_expected=False`. Startup continues degraded, `change_stream_connected=False`, local in-process cancellation still works, and health reports degraded/unready for watcher status.
- Any attempt to set `allow_degraded_change_stream=True` while `multi_worker=True` or `redis_expected=True` is a configuration error and must fail before component startup.

The app shell/container owns deriving this policy from runtime mode/settings. Delivery does not inspect global settings or infer deployment mode from Redis client objects.

### Translator Contract

```python
from datetime import datetime
from typing import Any

from common.dto import DeliveryEvent

def to_sse_frame(event: DeliveryEvent, *, timestamp: datetime) -> dict[str, Any]:
    """Pure translation. No I/O, no Redis, no DB, no logging side effects."""
```

Frame shape must preserve the current API wire format:

```python
{
    "type": "processing_status",
    "timestamp": "2026-05-15T12:00:00+00:00",
    "room_id": "room-1",
    "data": {...},
}
```

Required mappings:
- `ProcessingStatusEvent` -> `type="processing_status"`, data includes `status`, `message_id`, `details`, data `timestamp`, optional `agent_id`, optional `client_request_id`, optional `agents`.
- `RunEventNotification` -> `type="run_event"`, data includes `event_id`, `run_id`, `seq`, `type` from `run_event_type`, `payload`, and always includes `correlation_id` with a `str | None` value preserving the current legacy `client_request_id` field.
- `AgentMessagePartial` -> `type="agent_response_partial"`, data includes `message_id`, `agent_id`, `content_delta`, and data `timestamp`.
- `AgentMessageFinal` -> `type="agent_response"`, data includes `message_id`, `agent_id`, data `timestamp`, plus fields from `content` so callers can pass legacy keys such as `content`, `parts`, and `related_message_id`.
- `CancellationEvent` -> `type="cancellation"`, data includes `message_id`, optional `reason`, and data `timestamp`.
- `HITLRequestEvent` -> `type="hitl_input_requested"`, data includes `request_id`, `message_id`, `prompt`, `prompt_type`, `source`, and data `timestamp`.
- `HITLResolvedEvent` -> `type="hitl_status_update"`, data includes `request_id`, `message_id`, `status="resolved"`, and data `timestamp`.
- `HubAgentEvent` -> `type="hub_agent_event"`, data includes `hub_id`, `agent_id`, `message_id`, `status`, optional `partial`, and data `timestamp`.
- `DebateRoundEvent` -> `type="debate_round"`, data includes `round_number`, `agent_id`, `message_id`, and data `timestamp`.

### EventPublisherImpl Constructor

```python
class EventPublisherImpl:
    def __init__(
        self,
        *,
        sse_transport: "SSETransportImpl",
        event_bus: "CrossInstanceEventBus",
        deduplicator: "TerminalStatusDeduplicator",
        config: DeliveryConfig,
        now: Callable[[], datetime],
        instance_id: str,
        task_runner: "TaskRunner",
        metrics: MetricsCollector | None = None,
    ) -> None: ...
```

Protocol methods:
- `async emit(event: DeliveryEvent) -> None`: translate, terminal-dedup where applicable, deliver to local SSE connections, and Redis fan-out; never raise. It does not dispatch internal handlers. This follows the N8 split: frontend-visible events use `emit()`, while module-to-module events use `emit_internal()`.
- `async emit_internal(event: InternalEvent) -> None`: schedule same-worker handlers, Redis fan-out to other workers, never deliver to SSE clients, never raise.
- `register_internal_handler(event_type: str, handler: Callable) -> None`: support multiple handlers per event type and dispatch all registered handlers in registration order.
- `async start() -> None`: component-level protocol hook invoked by `DeliveryFacade.start()`. It must not be used as the app-shell lifecycle entry point and must not independently start the event bus; facade owns bus startup so there is one lifecycle path.
- `async stop() -> None`: drain/cancel tracked handler tasks and publish/log pending failures where possible. It does not directly stop or close the event bus; `DeliveryFacade.stop()` owns component shutdown order.

Compatibility method:
- `async _emit_legacy_frame(room_id: str, frame: dict) -> None`: same local + fan-out path as `emit()`, but accepts already-shaped legacy SSE frames. Only `DeliveryFacade.compat` may call this private method; `services/sse_services.py` must not reach through `facade.event_publisher` to call non-protocol methods. The leading underscore is intentional: this is not a Delivery module API and must be removed after Phase 7b migrates callers to typed events.
- `_emit_legacy_frame()` must apply terminal dedup itself when `frame["type"] == "processing_status"` by reading `frame["data"]["message_id"]` and `frame["data"]["status"]` and consulting the same `TerminalStatusDeduplicator`. All other legacy frame types bypass dedup.

Fire-and-forget handler contract:
- Internal handlers are invoked in background tasks created through injected `task_runner`, which defaults to exported `common.observability.traced_create_task` implemented in `common/observability/tracing.py`.
- `emit_internal()` schedules subscriber handlers in background tasks and must not await subscriber completion. `emit()` does not dispatch subscriber handlers at all; it may await local SSE delivery and Redis fan-out because those are Delivery transport operations.
- The publisher tracks every scheduled handler task and every asynchronous handler-failure dead-letter task. `stop()` waits up to `handler_shutdown_timeout_seconds` for pending handler/dead-letter tasks, then cancels unfinished tasks and logs their event type/name. This is how the fire-and-forget invariant coexists with the design requirement that Delivery drains pending deliveries during shutdown.
- Tests should inject a deterministic task runner that records handler and dead-letter coroutines so the test can await them explicitly and assert dead-letter publication behavior.

Task runner contract:

```python
from delivery.types import TaskRunner

class TaskRunner(Protocol):
    def __call__(
        self,
        coro: Awaitable[Any],
        *,
        name: str | None = None,
    ) -> asyncio.Task: ...
```

Use keyword-only `name` everywhere to match `common.observability.tracing.traced_create_task(coro, *, name=None)`.
The protocol lives in `delivery/types.py`; do not duplicate local `TaskRunner` protocol definitions across implementation files.

Dead-letter contract:
- `emit()` catches translator, local delivery, and bus failures. `emit_internal()` catches internal handler and bus failures.
- Local SSE delivery failures are warning-only and are not dead-lettered; these are expected transient connection failures. They must not prevent Redis fan-out for typed `emit()` or legacy `_emit_legacy_frame()`.
- Translator, Redis fan-out, and internal-handler failures are logged with structured fields and published to `redis_dead_letter_channel` when Redis Pub/Sub is configured and healthy. A bounded in-memory `deque(maxlen=dead_letter_memory_maxlen)` remains as fallback/test aid only; it is not the primary dead-letter mechanism.
- If dead-letter publication itself fails, log the failure and retain the in-memory fallback. Do not recursively dead-letter dead-letter failures.
- Caller-visible exceptions are forbidden.

Observability contract:
- Redis envelopes and SSE frames include a `trace_id` when the incoming DTO/frame already carries one, or when `common.observability.get_current_trace_id()` returns one. Do not invent a random trace id when no trace context exists. For typed SSE frames, place the trace id in `frame["data"]["trace_id"]`; for Redis fan-out envelopes, place it in top-level `envelope["trace_id"]`. For legacy `_emit_legacy_frame()` frames, preserve the exact input frame and do not inject `trace_id` into `frame` unless the legacy frame already has one; the Redis envelope may still carry top-level `trace_id` for observability without mutating the delivered legacy frame.
- Phase 6 adds `get_current_trace_id() -> str | None` and `trace_id_context(trace_id: str | None)` to `common/observability/tracing.py`. Tests and future app-shell code set explicit context with `with trace_id_context("trace-123"):`; the no-OpenTelemetry implementation reads that contextvar or returns `None`. It must not import OpenTelemetry or create synthetic ids.
- Emit the design metric names through `common.observability.MetricsCollector` with a no-op default: `hybro_delivery_sse_connections` gauge labeled by `worker_id`, `hybro_delivery_events_emitted_total` counter labeled by `event_type`, and `hybro_delivery_events_deduplicated_total` counter labeled by `event_type`. Additional failure/dead-letter metrics are allowed only if Task 13 documents them as extensions, not replacements.
- Metrics and trace collection failures must never affect delivery behavior.

### SSETransportImpl Constructor

```python
class SSETransportImpl:
    def __init__(
        self,
        *,
        cancellation_watcher: "CancellationWatcher",
        event_bus: "CrossInstanceEventBus",
        config: DeliveryConfig,
        now: Callable[[], datetime],
        id_factory: Callable[[], str],
        instance_id: str,
        task_runner: "TaskRunner",
        metrics: MetricsCollector | None = None,
    ) -> None: ...
```

Protocol methods:
- `def connect(room_id: str, connection_id: str) -> AsyncIterator[dict]`: create a connection and return an async iterator that yields queued frames, yields heartbeat frames after `config.heartbeat_interval_seconds` of inactivity, default 30 seconds, and disconnects in `finally`. Phase 6 must update `common/protocols/delivery_protocols.py` from `async def connect(...)` to this synchronous async-generator factory shape.
- `async disconnect(connection_id: str) -> None`: remove connection from whichever room owns it.
- `is_cancelled(message_id: str) -> bool`: L1 fast path.
- `async mark_cancelled(message_id: str) -> None`: local L1 + token signal + Redis L2 + Redis Pub/Sub fan-out.
- `set_draining(draining: bool) -> None`: reject new connections when true.
- `async start_cancellation_watcher() -> None`: start the per-worker Mongo change-stream watcher.

Compatibility methods needed by the C3 adapter:
- `async open_connection(room_id: str) -> SSEConnection`.
- `async remove_connection(room_id: str, connection_id: str) -> None`.
- `async broadcast_frame_to_room(room_id: str, frame: dict) -> None`.
- `async close_all_connections() -> None`: close all active SSE connections, clear room/reverse maps, unsubscribe all rooms, and emit `hybro_delivery_sse_connections` gauge value `0`.
- `get_room_status(room_id: str) -> dict`.
- `cancel_message(message_id: str) -> None`.
- `async cancel_message_and_broadcast(message_id: str) -> None`.
- `async check_cancelled(message_id: str) -> bool`.
- `clear_cancellation(message_id: str) -> None`.
- `create_token(message_id: str) -> CancellationToken`.
- `get_token(message_id: str) -> CancellationToken | None`.
- `remove_token(message_id: str) -> None`.

### DeliveryFacade Compatibility Accessor

`DeliveryDeps` exposes only Common protocols, but the C3 adapter still needs legacy-only methods during the migration. Do not call private or non-protocol methods through `facade.event_publisher` or `facade.sse_transport` from `services/sse_services.py`. Instead, `DeliveryFacade` exposes an adapter-only concrete accessor:

```python
class DeliveryCompatibility:
    async def emit_legacy_frame(self, room_id: str, frame: dict) -> None: ...
    async def open_connection(self, room_id: str) -> SSEConnection: ...
    async def remove_connection(self, room_id: str, connection_id: str) -> None: ...
    def get_room_status(self, room_id: str) -> dict: ...
    def is_cancelled(self, message_id: str) -> bool: ...
    def cancel_message(self, message_id: str) -> None: ...
    async def cancel_message_and_broadcast(self, message_id: str) -> None: ...
    async def check_cancelled(self, message_id: str) -> bool: ...
    def clear_cancellation(self, message_id: str) -> None: ...
    def create_token(self, message_id: str) -> CancellationToken: ...
    def get_token(self, message_id: str) -> CancellationToken | None: ...
    def remove_token(self, message_id: str) -> None: ...
    async def start_change_stream_watcher(self) -> None: ...
    async def stop_change_stream_watcher(self) -> None: ...
    async def start_redis_service(self, redis_service: Any | None = None) -> None: ...
    async def stop_redis_service(self) -> None: ...
    async def start_event_broker(self, broker: Any | None = None) -> None: ...
    async def stop_event_broker(self) -> None: ...
    @property
    def change_stream_connected(self) -> bool: ...
    @property
    def delivery_kv_connected(self) -> bool: ...
    @property
    def delivery_pubsub_connected(self) -> bool: ...
    async def refresh_health(self) -> None: ...
    @property
    def redis_connected(self) -> bool: ...
    @property
    def broker_connected(self) -> bool: ...
```

`DeliveryFacade.compat: DeliveryCompatibility` is intentionally concrete and adapter-only. It is not included in `DeliveryDeps`, is not part of the Common protocols, and is removed when the C3 adapter is removed. `broker_connected` and `redis_connected` are legacy aliases for `delivery_pubsub_connected` and `delivery_kv_connected`; new app-shell health code should use the explicit names.

`SSEManager.unbind_facade()` resets the C3 adapter to fail-fast state after app shutdown. `bind_facade()` may bind a new started facade after unbind; it must not keep delegating to a stopped facade.

## Tasks

### Task 1: Branch, Baseline, and Behavior Characterization

**Files:**
- Reference: `services/sse_services.py`
- Reference: `tests/test_service_sse.py`
- Reference: `tests/test_sse_event_broker.py`
- Reference: `tests/test_api_sse.py`

- [ ] Create branch.

Run:

```bash
git switch main
git switch -c phase-6-delivery-module
```

- [ ] Run current SSE-related tests before edits.

Run:

```bash
PYTHONPATH=. uv run pytest -q tests/test_service_sse.py tests/test_sse_event_broker.py tests/test_api_sse.py tests/test_multi_worker_safety.py
```

Expected: pass on current main. If tests fail before edits, record failures in the implementation notes and do not mask them with Delivery changes.

- [ ] Confirm Phase 7a prerequisite.

Run:

```bash
rg -n "send_processing_status\\(" modules services api jobs
rg -n "record_processing_status|run_command_handler|run_event_sse_enabled" modules services api jobs
rg -n "record_processing_status|run_command_handler|run_event_sse_enabled" services/sse_services.py
```

Expected: the first two broad `rg` commands are informational discovery only. They may match allowed definitions, imports, helper names, and Phase 7a call-site code; do not treat their output as pass/fail proof. The authoritative proof for caller ordering is `tests/test_phase7a_processing_status_gate.py`. The strict no-output scan at this point is scoped to `services/sse_services.py`: it must not contain `record_processing_status`, `run_command_handler`, or `run_event_sse_enabled` after Phase 7a because the adapter/send path must be transport-only. After `delivery/` exists, Task 14 repeats the strict no-output scan across `delivery/` and `services/sse_services.py`.

Blocking gate: If `services/sse_services.py.send_processing_status()` still performs a real `run_command_handler.record_processing_status()` side effect, STOP. Phase 7a must land before Phase 6 proceeds. Do not carry the record call into the C3 adapter as a hidden compatibility behavior, because that would preserve the Rule 6 violation and defeat the extraction gate.

This gate is intentionally broad. The earlier Phase 7a checklist named the known Execution files, but this worktree may have additional `send_processing_status()` call sites in production `modules/`, `services/`, `api/`, or `jobs/`. Phase 6 does not migrate those callers; it only refuses to start extraction until the record-before-emit separation is already true across the production tree.

Verify the manifest-backed Phase 7a proof before continuing. These artifacts must already exist from Phase 7a. Do not create or update them as part of Phase 6, except Task 10 may update `tests/test_phase7a_processing_status_golden.py` only to bind the C3 adapter/fake facade required by the new fail-fast adapter. If the executable Phase 7a gate or golden tests fail, STOP and land the owning Phase 7a/handoff work before starting Phase 6.

- Phase 7a must have generated `tests/fixtures/phase7a_processing_status_callers.json` from AST-discovered `send_processing_status(...)` call expressions under `modules/`, `services/`, `api/`, and `jobs/`. Use the `rg` output above only as a human cross-check.
- The manifest must record `path`, `function_or_method`, `line`, `status_expression`, `requires_recording: bool`, and either `record_call_line` or `transport_only_reason` for each call site.
- The manifest must be generated from AST call expressions, not raw grep, so comment/docstring mentions such as `modules/transports/direct.py` do not become fake call sites.
- The manifest must record expected argument expressions for `room_id`, `status`, `message_id`, and `client_request_id`, plus `expects_run_event_sse: bool`.
- Phase 7a must have updated the lifecycle facade/port used by these callers so `record_processing_status()` returns the `dict | None` run-event payload. In this repo that means `RunLifecycleService.record_processing_status()` delegates to `RunCommandHandler.record_processing_status()` and returns its payload instead of `None`. Callers must not bypass the lifecycle port to call `RunCommandHandler` directly unless the manifest explicitly marks that as a temporary Phase 7a exception.
- Current pre-Phase-7a files that must appear in the manifest are `modules/QueueExecutor.py`, `modules/agent_response_handler.py`, `api/sse.py`, `jobs/stale_task_checker.py`, `modules/SupervisorExecutor.py`, `services/task_notification_service.py`, `services/room_services.py`, `modules/WorkflowCenter.py`, and `modules/RoomMessageCenter.py`; the exact call-expression line list must be regenerated by Phase 7a before Phase 6 starts.
- Phase 7a must have added `tests/test_phase7a_processing_status_gate.py` that AST-parses production `modules/`, `services/`, `api/`, and `jobs/`, fails on any unlisted `send_processing_status()` call, and for every `requires_recording=true` entry asserts the send call is preceded on the same simple control-flow path by `record_processing_status()` using matching `room_id`, `status`, `message_id`, and `client_request_id` expressions.
- The AST gate is intentionally conservative: if control flow is too complex to prove same-path record-before-send and matching arguments, the manifest entry must be marked `manual_review_required` and Phase 6 remains blocked until the caller is simplified or a focused unit/golden test proves record-then-run-event-then-processing-status order for that call path.
- Transport-only entries are allowed only with an explicit reason, for example SSE replay/API transport, helper wrapper that delegates to an already-recorded caller, or test-only shim. These entries must not call `record_processing_status()` themselves.
- If a caller uses the returned `last_run_event_payload` and `run_event_sse_enabled()` is true today, Phase 7a must preserve that behavior without depending on Delivery. Before Phase 6 exists, callers should emit the legacy `run_event` SSE through `sse_manager.broadcast_to_room(room_id, "run_event", payload)` after `record_processing_status()` and before `send_processing_status()`. The payload must include `event_id`, `run_id`, `seq`, `type`, `payload`, and `correlation_id=client_request_id`, matching the current `send_processing_status()` branch. The manifest should set `expects_run_event_sse=true` for those paths, and tests must verify `record_processing_status()` -> legacy `run_event` broadcast -> processing status order. Phase 7b later migrates that broadcast to `EventPublisher.emit(RunEventNotification(...))`.
- Run `PYTHONPATH=. uv run pytest -q tests/test_phase7a_processing_status_gate.py` before Task 2. Expected: pass. If it fails, STOP; Phase 7a is incomplete.

### Task 1b: Verify Phase 7a Delivery Extraction Handoff Coverage

**Files:**
- Reference only: `docs/superpowers/plans/2026-05-16-phase-7a-delivery-extraction-handoff.md`
- Reference only: `tests/fixtures/phase7a_processing_status_callers.json`
- Test: `tests/test_phase7a_processing_status_gate.py`
- Test: handoff-listed focused tests, including `tests/test_module_room_message_center.py`, `tests/test_module_queue_executor.py`, `tests/test_stale_task_checker_run_lifecycle.py`, and `tests/test_api_sse.py`

- [ ] Verify the handoff audit is covered by executable tests before creating Delivery package code.

Blocking gate requirements:
- Phase 6 does not edit `modules/RoomMessageCenter.py`, `modules/QueueExecutor.py`, the Phase 7a handoff doc, or the Phase 7a manifest fixture to clear business-side-effect ordering. Those are settled Phase 7a/handoff artifacts, external to this plan.
- Task 1b is STOP-only. If any handoff item or manifest-discovered terminal/frontend-visible send path is unresolved, unclassified, missing an exact proof node, or still has required post-emit business side effects, stop before Task 2 and return the work to the owning Phase 7a/handoff cleanup. Do not create `delivery/` code until that cleanup lands.
- Read `docs/superpowers/plans/2026-05-16-phase-7a-delivery-extraction-handoff.md` as a historical audit/reference for known side-effect-ordering risks, then audit against the manifest-driven coverage table below. Stale "Remaining Audit Items" prose in the handoff doc is not ignored; each historical item must be covered by the exact proving test(s) in this Phase 6 table, and those exact test nodes must exist.
- Preserve the design invariant: required business side effects complete before Delivery emits terminal/frontend-visible status. A row may be classified best-effort only when the proving test demonstrates Delivery extraction does not require a callback into business modules after emit.
- The whole-file pytest command below is not sufficient by itself. First run the existing-proof collect-only command, then verify the missing-proof backlog is empty before proceeding. If any existing proof node is not collected, or if any backlog node still does not exist, treat the gate as failed even if running the entire file later exits successfully.
- Derive the audit from `tests/fixtures/phase7a_processing_status_callers.json`, not only from historical prose. Enumerate every manifest entry where `terminal_status=true`, where `status_expression` is terminal/frontend-visible (`completed`, `failed`, `canceled`, `rejected`, `rate_limited`, `error`, or corresponding `SSEProcessingStatus.*` values), or where `expects_run_event_sse=true` and the send path can clear or terminalize frontend state. For each entry, the Task 1b audit must record one of: `required_side_effects_before_emit` with exact proving test node(s), `best_effort_after_emit` with exact proving test node(s), or `no_post_emit_business_side_effects` with exact code reference and/or test node. Any manifest entry in that set without a classification blocks Phase 6.
- Run the Phase 7a AST gate, the Phase 7a golden ordering tests, and the focused tests that cover handoff-listed risk areas. At minimum, include `tests/test_phase7a_processing_status_gate.py`, `tests/test_phase7a_processing_status_golden.py`, `tests/test_module_room_message_center.py`, `tests/test_module_queue_executor.py`, `tests/test_stale_task_checker_run_lifecycle.py`, and `tests/test_api_sse.py`.
- If any required test node below is missing, if any listed test fails, if the manifest-driven audit finds an unclassified terminal/frontend-visible send path, or if a current review identifies a concrete handoff item with no corresponding focused test coverage, STOP. Delivery extraction cannot begin because Rule 6 requires Delivery to translate and deliver only; it cannot rely on post-emit callbacks into business modules.
- Do not regenerate `tests/fixtures/phase7a_processing_status_callers.json` in Phase 6. If the fixture is stale, that is evidence Phase 7a/handoff work must land first.

Existing proof nodes that currently collect:

```bash
PYTHONPATH=. uv run pytest --collect-only -q \
  tests/test_phase7a_processing_status_gate.py::test_production_processing_status_callers_are_manifest_covered \
  tests/test_phase7a_processing_status_gate.py::test_pre_recorded_payload_requires_awaited_assignment \
  tests/test_phase7a_processing_status_golden.py::test_golden_send_message_processing_status_order \
  tests/test_stale_task_checker_run_lifecycle.py::test_watchdog_broadcasts_pre_recorded_payload_before_failed_status \
  tests/test_stale_task_checker_run_lifecycle.py::test_watchdog_payload_none_suppresses_metric_and_delivery \
  tests/test_api_sse.py::TestCancelMessage::test_paused_agent_cleanup_failure_does_not_block_root_cancellation
```

Current verified result after Phase 7a handoff cleanup: 6 existing nodes collect.
If this command fails, STOP; even the already-existing proof set is broken.

Current full-gate status:
- Current verified diagnostic full-gate result: 34 nodes collected, 0 nodes
  missing, exit zero.
- Missing node breakdown: none. The historical 2 `QueueExecutor`, 20
  `RoomMessageCenter`, 1 `agent_response_handler`, and 5 `service_room` proof
  gaps have executable nodes.
- Manifest coverage check result: 40 relevant manifest entries, 0 missing call
  IDs in this Phase 6 table.
- The Phase 7a handoff doc now has no unresolved Remaining Audit Items. Required
  non-run business side effects have either moved before terminal/frontend-visible
  `processing_status` emits or are covered by focused transport-only/best-effort
  proofs.

Task 1b decision rule:
- Existing-proof command fails: STOP.
- Existing-proof command passes but missing handoff proof backlog is non-empty: STOP.
- Diagnostic full-gate command fails with missing nodes: STOP.
- Secondary broad suite passes while backlog remains non-empty: still STOP. The broad suite is a smoke check only and never authorizes Delivery extraction.
- Proceed to Task 2 only when existing proofs collect, the missing handoff proof backlog is empty, the diagnostic full-gate command collects every node, and the secondary suite passes.

Missing handoff proof backlog:

None. The 28 historical handoff proof nodes now exist and are included in the
diagnostic full-gate command below.

Diagnostic full-gate command for future handoff cleanup verification:

```bash
PYTHONPATH=. uv run pytest --collect-only -q \
  tests/test_phase7a_processing_status_gate.py::test_production_processing_status_callers_are_manifest_covered \
  tests/test_phase7a_processing_status_gate.py::test_pre_recorded_payload_requires_awaited_assignment \
  tests/test_phase7a_processing_status_golden.py::test_golden_send_message_processing_status_order \
  tests/test_stale_task_checker_run_lifecycle.py::test_watchdog_broadcasts_pre_recorded_payload_before_failed_status \
  tests/test_stale_task_checker_run_lifecycle.py::test_watchdog_payload_none_suppresses_metric_and_delivery \
  tests/test_module_queue_executor.py::test_deferred_sse_status_has_no_required_post_emit_business_side_effects \
  tests/test_module_queue_executor.py::test_resume_from_continuation_failure_records_before_terminal_emit \
  tests/test_module_room_message_center.py::test_failed_room_lock_notifies_non_terminal_tasks_before_failed_processing_status \
  tests/test_module_room_message_center.py::test_supervisor_v2_prep_missing_notifies_before_failed_processing_status \
  tests/test_module_room_message_center.py::test_queue_canceled_side_effects_complete_before_canceled_processing_status \
  tests/test_module_room_message_center.py::test_queue_failure_appends_turn_failed_and_notifies_before_failed_processing_status \
  tests/test_module_room_message_center.py::test_v1_resume_failure_notifies_non_terminal_tasks_before_terminal_emit \
  tests/test_module_room_message_center.py::test_root_queue_completion_appends_turn_completed_before_completed_processing_status \
  tests/test_module_room_message_center.py::test_supervisor_v2_corrupted_data_notifies_before_failed_processing_status \
  tests/test_module_room_message_center.py::test_supervisor_v2_clarify_resume_failed_has_no_required_post_emit_side_effects \
  tests/test_module_room_message_center.py::test_supervisor_v2_planning_failure_notifies_before_failed_processing_status \
  tests/test_module_room_message_center.py::test_supervisor_v2_execution_failure_notifies_before_failed_processing_status \
  tests/test_module_room_message_center.py::test_supervisor_v2_resume_deserialization_failure_notifies_before_failed_status \
  tests/test_module_room_message_center.py::test_supervisor_v2_resume_room_lookup_failure_notifies_before_failed_status \
  tests/test_module_room_message_center.py::test_supervisor_v2_resume_executor_failure_notifies_before_failed_status \
  tests/test_module_room_message_center.py::test_supervisor_v2_resume_canceled_appends_and_notifies_before_canceled_status \
  tests/test_module_room_message_center.py::test_supervisor_v2_completed_appends_turn_completed_before_completed_processing_status \
  tests/test_module_room_message_center.py::test_supervisor_v2_canceled_appends_and_notifies_before_terminal_status \
  tests/test_module_room_message_center.py::test_supervisor_v2_failed_appends_and_notifies_before_terminal_status \
  tests/test_module_room_message_center.py::test_v1_resume_completion_side_effects_complete_before_completed_processing_status \
  tests/test_module_room_message_center.py::test_supervisor_v2_terminal_post_loop_side_effects_complete_before_terminal_status_or_are_best_effort \
  tests/test_module_room_message_center.py::test_clarifying_soft_complete_appends_turn_completed_before_frontend_completed_status \
  tests/test_agent_response_handler.py::test_processing_status_callback_has_no_required_post_emit_business_side_effects \
  tests/test_service_room.py::test_send_message_failure_call_01_side_effects_before_failed_processing_status \
  tests/test_service_room.py::test_send_message_failure_call_02_side_effects_before_failed_processing_status \
  tests/test_service_room.py::test_send_message_canceled_side_effects_before_canceled_processing_status \
  tests/test_service_room.py::test_send_message_failure_call_03_side_effects_before_failed_processing_status \
  tests/test_service_room.py::test_no_agents_fallback_side_effects_before_completed_processing_status \
  tests/test_api_sse.py::TestCancelMessage::test_paused_agent_cleanup_failure_does_not_block_root_cancellation
```

Expected current result after handoff cleanup: every exact node is collected
(34 total).

Required manifest-driven coverage map:

The manifest-driven audit is complete only when all 40 relevant entries below
are present and classified. The missing handoff proof backlog is empty only
after these 40 rows are present in the plan, the diagnostic full-gate command
collects every exact proof node, and the secondary suite passes. Duplicate
QueueExecutor proof tests are non-blocking cleanup; do not remove or consolidate
them during Phase 6 unless explicitly requested.

| call_id | path / function | classification | exact proof node or code reference |
| --- | --- | --- | --- |
| `api.sse.cancel_message.canceled` | `api/sse.py` / `cancel_message` | `best_effort_after_emit`; cleanup root cancellation delivery must not depend on paused-agent cleanup succeeding. | `tests/test_api_sse.py::TestCancelMessage::test_paused_agent_cleanup_failure_does_not_block_root_cancellation` |
| `jobs.stale_task_checker.StaleTaskChecker._fail_stale_runs.failed.call-01` | `jobs/stale_task_checker.py` / `StaleTaskChecker._fail_stale_runs` | `no_post_emit_business_side_effects`; transport-only no-delivery path when payload is missing; no Delivery callback into business modules. | `tests/test_stale_task_checker_run_lifecycle.py::test_watchdog_payload_none_suppresses_metric_and_delivery` |
| `jobs.stale_task_checker.StaleTaskChecker._fail_stale_runs.failed.call-02` | `jobs/stale_task_checker.py` / `StaleTaskChecker._fail_stale_runs` | `required_side_effects_before_emit`; timeout append and metric precede run-event broadcast and failed status. | `tests/test_stale_task_checker_run_lifecycle.py::test_watchdog_broadcasts_pre_recorded_payload_before_failed_status` |
| `modules.QueueExecutor.QueueExecutor.process_queue.awaiting_input` | `modules/QueueExecutor.py` / `QueueExecutor.process_queue` | `no_post_emit_business_side_effects`; HITL pause/progress path only. | `tests/test_phase7a_processing_status_gate.py::test_production_processing_status_callers_are_manifest_covered` |
| `modules.QueueExecutor.QueueExecutor.process_queue.sse_status` | `modules/QueueExecutor.py` / `QueueExecutor.process_queue` | `no_post_emit_business_side_effects`; deferred status path is safe for Delivery extraction. | `tests/test_module_queue_executor.py::test_deferred_sse_status_has_no_required_post_emit_business_side_effects` |
| `modules.QueueExecutor.QueueExecutor.resume_from_continuation.failed` | `modules/QueueExecutor.py` / `QueueExecutor.resume_from_continuation` | `required_side_effects_before_emit`; V1 queue-resume failure record/run-event/status order is proven. | `tests/test_module_queue_executor.py::test_resume_from_continuation_failure_records_before_terminal_emit` |
| `modules.RoomMessageCenter.RoomMessageCenter.process_room_user_message.failed` | `modules/RoomMessageCenter.py` / `RoomMessageCenter.process_room_user_message` | `required_side_effects_before_emit`; failed room-lock notifications precede failed status. | `tests/test_module_room_message_center.py::test_failed_room_lock_notifies_non_terminal_tasks_before_failed_processing_status` |
| `modules.RoomMessageCenter.RoomMessageCenter._process_room_user_message_locked.failed.call-01` | `modules/RoomMessageCenter.py` / `RoomMessageCenter._process_room_user_message_locked` | `required_side_effects_before_emit`; supervisor V2 prep-missing failure side effects precede failed status. | `tests/test_module_room_message_center.py::test_supervisor_v2_prep_missing_notifies_before_failed_processing_status` |
| `modules.RoomMessageCenter.RoomMessageCenter._process_room_user_message_locked.canceled` | `modules/RoomMessageCenter.py` / `RoomMessageCenter._process_room_user_message_locked` | `required_side_effects_before_emit`; queue cancellation side effects precede canceled status. | `tests/test_module_room_message_center.py::test_queue_canceled_side_effects_complete_before_canceled_processing_status` |
| `modules.RoomMessageCenter.RoomMessageCenter._process_room_user_message_locked.failed.call-02` | `modules/RoomMessageCenter.py` / `RoomMessageCenter._process_room_user_message_locked` | `required_side_effects_before_emit`; `turn_failed` append and notification precede failed status. | `tests/test_module_room_message_center.py::test_queue_failure_appends_turn_failed_and_notifies_before_failed_processing_status` |
| `modules.RoomMessageCenter.RoomMessageCenter._process_room_user_message_locked.completed` | `modules/RoomMessageCenter.py` / `RoomMessageCenter._process_room_user_message_locked` | `required_side_effects_before_emit`; root queue `turn_completed` append precedes completed status. | `tests/test_module_room_message_center.py::test_root_queue_completion_appends_turn_completed_before_completed_processing_status` |
| `modules.RoomMessageCenter.RoomMessageCenter._process_supervisor_v2.failed.call-01` | `modules/RoomMessageCenter.py` / `RoomMessageCenter._process_supervisor_v2` | `required_side_effects_before_emit`; corrupted/incomplete V2 data notification precedes failed status. | `tests/test_module_room_message_center.py::test_supervisor_v2_corrupted_data_notifies_before_failed_processing_status` |
| `modules.RoomMessageCenter.RoomMessageCenter._process_supervisor_v2.completed` | `modules/RoomMessageCenter.py` / `RoomMessageCenter._process_supervisor_v2` | `no_post_emit_business_side_effects`; clarify-resume soft-clear path is safe. | `tests/test_module_room_message_center.py::test_supervisor_v2_clarify_resume_failed_has_no_required_post_emit_side_effects` |
| `modules.RoomMessageCenter.RoomMessageCenter._process_supervisor_v2.failed.call-02` | `modules/RoomMessageCenter.py` / `RoomMessageCenter._process_supervisor_v2` | `required_side_effects_before_emit`; supervisor planning failure notification precedes failed status. | `tests/test_module_room_message_center.py::test_supervisor_v2_planning_failure_notifies_before_failed_processing_status` |
| `modules.RoomMessageCenter.RoomMessageCenter._process_supervisor_v2.failed.call-03` | `modules/RoomMessageCenter.py` / `RoomMessageCenter._process_supervisor_v2` | `required_side_effects_before_emit`; supervisor execution failure notification precedes failed status. | `tests/test_module_room_message_center.py::test_supervisor_v2_execution_failure_notifies_before_failed_processing_status` |
| `modules.RoomMessageCenter.RoomMessageCenter._resume_supervisor_v2.failed.call-01` | `modules/RoomMessageCenter.py` / `RoomMessageCenter._resume_supervisor_v2` | `required_side_effects_before_emit`; resume deserialization failure notification precedes failed status. | `tests/test_module_room_message_center.py::test_supervisor_v2_resume_deserialization_failure_notifies_before_failed_status` |
| `modules.RoomMessageCenter.RoomMessageCenter._resume_supervisor_v2.failed.call-02` | `modules/RoomMessageCenter.py` / `RoomMessageCenter._resume_supervisor_v2` | `required_side_effects_before_emit`; resume room lookup failure notification precedes failed status. | `tests/test_module_room_message_center.py::test_supervisor_v2_resume_room_lookup_failure_notifies_before_failed_status` |
| `modules.RoomMessageCenter.RoomMessageCenter._resume_supervisor_v2.canceled` | `modules/RoomMessageCenter.py` / `RoomMessageCenter._resume_supervisor_v2` | `required_side_effects_before_emit`; resume cancellation side effects precede canceled status. | `tests/test_module_room_message_center.py::test_supervisor_v2_resume_canceled_appends_and_notifies_before_canceled_status` |
| `modules.RoomMessageCenter.RoomMessageCenter._resume_supervisor_v2.failed.call-03` | `modules/RoomMessageCenter.py` / `RoomMessageCenter._resume_supervisor_v2` | `required_side_effects_before_emit`; resumed executor failure notification precedes failed status. | `tests/test_module_room_message_center.py::test_supervisor_v2_resume_executor_failure_notifies_before_failed_status` |
| `modules.RoomMessageCenter.RoomMessageCenter._handle_v2_run_result.completed.call-01` | `modules/RoomMessageCenter.py` / `RoomMessageCenter._handle_v2_run_result` | `required_side_effects_before_emit`; V2 completed `turn_completed` append precedes completed status. | `tests/test_module_room_message_center.py::test_supervisor_v2_completed_appends_turn_completed_before_completed_processing_status` |
| `modules.RoomMessageCenter.RoomMessageCenter._handle_v2_run_result.completed.call-02` | `modules/RoomMessageCenter.py` / `RoomMessageCenter._handle_v2_run_result` | `required_side_effects_before_emit`; clarifying soft-complete append precedes frontend completed status. | `tests/test_module_room_message_center.py::test_clarifying_soft_complete_appends_turn_completed_before_frontend_completed_status` |
| `modules.RoomMessageCenter.RoomMessageCenter._handle_v2_run_result.canceled` | `modules/RoomMessageCenter.py` / `RoomMessageCenter._handle_v2_run_result` | `required_side_effects_before_emit`; V2 canceled append/notification precedes terminal status. | `tests/test_module_room_message_center.py::test_supervisor_v2_canceled_appends_and_notifies_before_terminal_status` |
| `modules.RoomMessageCenter.RoomMessageCenter._handle_v2_run_result.failed` | `modules/RoomMessageCenter.py` / `RoomMessageCenter._handle_v2_run_result` | `required_side_effects_before_emit`; V2 failed append/notification precedes terminal status. | `tests/test_module_room_message_center.py::test_supervisor_v2_failed_appends_and_notifies_before_terminal_status` |
| `modules.RoomMessageCenter.RoomMessageCenter._resume_continuation_locked.completed` | `modules/RoomMessageCenter.py` / `RoomMessageCenter._resume_continuation_locked` | `required_side_effects_before_emit`; V1 resume completion side effects precede completed status. | `tests/test_module_room_message_center.py::test_v1_resume_completion_side_effects_complete_before_completed_processing_status` |
| `modules.SupervisorExecutor.SupervisorExecutor.run.processing.call-01` | `modules/SupervisorExecutor.py` / `SupervisorExecutor.run` | `no_post_emit_business_side_effects`; progress-only record/run-event ordering path. | `tests/test_phase7a_processing_status_gate.py::test_production_processing_status_callers_are_manifest_covered` |
| `modules.SupervisorExecutor.SupervisorExecutor.run.processing.call-02` | `modules/SupervisorExecutor.py` / `SupervisorExecutor.run` | `no_post_emit_business_side_effects`; progress-only record/run-event ordering path. | `tests/test_phase7a_processing_status_gate.py::test_production_processing_status_callers_are_manifest_covered` |
| `modules.SupervisorExecutor.SupervisorExecutor.run.awaiting_input.call-01` | `modules/SupervisorExecutor.py` / `SupervisorExecutor.run` | `no_post_emit_business_side_effects`; awaiting-input pause path. | `tests/test_phase7a_processing_status_gate.py::test_production_processing_status_callers_are_manifest_covered` |
| `modules.SupervisorExecutor.SupervisorExecutor.run.processing.call-03` | `modules/SupervisorExecutor.py` / `SupervisorExecutor.run` | `no_post_emit_business_side_effects`; progress-only record/run-event ordering path. | `tests/test_phase7a_processing_status_gate.py::test_production_processing_status_callers_are_manifest_covered` |
| `modules.SupervisorExecutor.SupervisorExecutor.run.processing.call-04` | `modules/SupervisorExecutor.py` / `SupervisorExecutor.run` | `no_post_emit_business_side_effects`; progress-only record/run-event ordering path. | `tests/test_phase7a_processing_status_gate.py::test_production_processing_status_callers_are_manifest_covered` |
| `modules.SupervisorExecutor.SupervisorExecutor.run.awaiting_input.call-02` | `modules/SupervisorExecutor.py` / `SupervisorExecutor.run` | `no_post_emit_business_side_effects`; awaiting-input pause path. | `tests/test_phase7a_processing_status_gate.py::test_production_processing_status_callers_are_manifest_covered` |
| `modules.SupervisorExecutor.SupervisorExecutor.run.processing.call-05` | `modules/SupervisorExecutor.py` / `SupervisorExecutor.run` | `no_post_emit_business_side_effects`; progress-only record/run-event ordering path. | `tests/test_phase7a_processing_status_gate.py::test_production_processing_status_callers_are_manifest_covered` |
| `modules.WorkflowCenter.WorkflowCenter.run_workflow.canceled` | `modules/WorkflowCenter.py` / `WorkflowCenter.run_workflow` | `no_post_emit_business_side_effects`; cancellation classification remains outside Delivery business logic. | `tests/test_phase7a_processing_status_gate.py::test_production_processing_status_callers_are_manifest_covered` |
| `modules.agent_response_handler.AgentResponseHandler._maybe_create_hitl_for_async_interactive.awaiting_input` | `modules/agent_response_handler.py` / `AgentResponseHandler._maybe_create_hitl_for_async_interactive` | `no_post_emit_business_side_effects`; HITL awaiting-input path. | `tests/test_phase7a_processing_status_gate.py::test_production_processing_status_callers_are_manifest_covered` |
| `modules.agent_response_handler.AgentResponseHandler._on_processing_status.state.call-01` | `modules/agent_response_handler.py` / `AgentResponseHandler._on_processing_status` | `no_post_emit_business_side_effects`; dynamic-state callback has no required post-emit business side effect. | `tests/test_agent_response_handler.py::test_processing_status_callback_has_no_required_post_emit_business_side_effects` |
| `services.room_services.RoomServices.send_message_to_room.failed.call-01` | `services/room_services.py` / `RoomServices.send_message_to_room` | `required_side_effects_before_emit`; room-service failure call-01 side effects precede failed status. | `tests/test_service_room.py::test_send_message_failure_call_01_side_effects_before_failed_processing_status` |
| `services.room_services.RoomServices.send_message_to_room.failed.call-02` | `services/room_services.py` / `RoomServices.send_message_to_room` | `required_side_effects_before_emit`; room-service failure call-02 side effects precede failed status. | `tests/test_service_room.py::test_send_message_failure_call_02_side_effects_before_failed_processing_status` |
| `services.room_services.RoomServices.send_message_to_room.canceled` | `services/room_services.py` / `RoomServices.send_message_to_room` | `required_side_effects_before_emit`; room-service cancellation side effects precede canceled status. | `tests/test_service_room.py::test_send_message_canceled_side_effects_before_canceled_processing_status` |
| `services.room_services.RoomServices.send_message_to_room.failed.call-03` | `services/room_services.py` / `RoomServices.send_message_to_room` | `required_side_effects_before_emit`; room-service failure call-03 side effects precede failed status. | `tests/test_service_room.py::test_send_message_failure_call_03_side_effects_before_failed_processing_status` |
| `services.room_services.RoomServices._send_processing_status.processing` | `services/room_services.py` / `RoomServices._send_processing_status` | `required_side_effects_before_emit`; record -> run_event -> processing_status order is preserved. | `tests/test_phase7a_processing_status_golden.py::test_golden_send_message_processing_status_order` |
| `services.room_services.RoomServices._handle_no_agents_fallback.completed` | `services/room_services.py` / `RoomServices._handle_no_agents_fallback` | `required_side_effects_before_emit`; fallback persistence/turn side effects precede completed status. | `tests/test_service_room.py::test_no_agents_fallback_side_effects_before_completed_processing_status` |

Secondary verification (not the authoritative gate):

```bash
PYTHONPATH=. uv run pytest -q tests/test_phase7a_processing_status_gate.py tests/test_phase7a_processing_status_golden.py tests/test_module_room_message_center.py tests/test_module_queue_executor.py tests/test_stale_task_checker_run_lifecycle.py tests/test_api_sse.py
```

Current verified result after Phase 7a handoff cleanup: 80 passed. This is
secondary verification only. Passing this broad suite alone is not enough to
proceed; the 34-node diagnostic collect-only gate above is the authoritative
missing-proof check.

Design-drift note before Task 2: until Task 13 updates `docs/MODULAR_DECOUPLING_DESIGN.md`, this Phase 6 plan is authoritative for the `SSETransport.connect()` shape (`def connect(...) -> AsyncIterator[dict]`). Do not update the design doc before Task 2 as part of Phase 6 implementation. Task 13 is the required reconciliation point and must update the design doc before merge; the current `async def` wording in the protocol/design docs is known drift, not a reason to change the Phase 6 implementation shape once Task 1b passes.

### Task 2: Package Skeleton, Config, and Import Boundary Tests

**Files:**
- Create: `delivery/__init__.py`
- Create: `delivery/config.py`
- Create: `delivery/types.py`
- Create: `delivery/sse/__init__.py`
- Create: `delivery/event_bus/__init__.py`
- Modify: `common/observability/tracing.py` to add `traced_create_task()`, `get_current_trace_id()`, and `trace_id_context()` if absent
- Modify: `common/observability/__init__.py` to export `traced_create_task`, `get_current_trace_id`, and `trace_id_context`
- Modify: `common/config/settings.py` to expose Delivery-owned settings/env fields for every runtime `DeliveryConfig` field: heartbeat interval, shutdown drain seconds, TTLs, cache maxsizes, terminal statuses, Redis channels/prefixes, internal/dead-letter channels, reconnect delays, dead-letter/handler limits, change-stream backoff, Redis pool size, room-subscription limit, and reserved Pub/Sub connection headroom
- Modify: `pyproject.toml`
- Test: `tests/test_delivery_protocols.py`

- [ ] Write failing protocol/export/package/import-boundary tests.

Test expectations:
- `delivery`, `delivery.sse`, `delivery.event_bus`, `delivery.config`, and `delivery.types` import without side effects.
- Task 2 `delivery/__init__.py` is a minimal package initializer and must not import `delivery.facade`, `delivery.event_publisher`, `delivery.sse.manager`, or final concrete classes that do not exist yet. Do not assert final `delivery.__all__` contents until Task 9.
- `delivery/config.py` has no imports from `common.config`, top-level `config`, `container`, `main`, or any settings singleton; the dataclass defaults are pure literals.
- Import-boundary tests reject global settings singleton imports anywhere under `delivery/**`: `from common.config import settings`, `from common.config.settings import settings`, `from config.settings import settings`, `import common.config` / `import common.config.settings` followed by settings access, and dynamic imports of `common.config` / `common.config.settings` used to access settings. They must also reject `Settings` type imports/annotations from app settings modules under `delivery/**` and any settings-backed `DeliveryConfig` classmethod that accepts the full app settings object. Delivery may receive only primitive config values through `DeliveryConfig(...)`; full-settings extraction belongs to `container.py` or a container-owned helper.
- `delivery.config.DeliveryStartupPolicy` imports cleanly, has no defaults for `redis_expected` or `multi_worker`, allows an explicit fatal policy `DeliveryStartupPolicy(redis_expected=True, multi_worker=True, allow_degraded_change_stream=False)`, allows `DeliveryStartupPolicy(redis_expected=False, multi_worker=False, allow_degraded_change_stream=True)`, and rejects degraded policy combinations where `allow_degraded_change_stream=True` with either `redis_expected=True` or `multi_worker=True`.
- `delivery.types.TaskRunner` exists and its callable signature uses keyword-only `name`.
- `delivery.types.RoomSubscriptionLimitExceeded` exists for the event bus to reject over-capacity room subscriptions and for `SSETransportImpl` tests to assert rollback without importing event-bus internals.
- `DeliveryConfig` default `redis_room_subscription_production_limit` is compatible with default `redis_max_connections=50` after reserving non-room connections. Unsafe configs where `redis_room_subscription_production_limit + redis_subscription_reserved_connections > redis_max_connections` are rejected by `DeliveryConfig` validation in Task 2 before Redis fan-out or facade startup exists.
- `DeliveryConfig` rejects invalid non-capacity values at construction: non-positive heartbeat/drain/TTL/cache/reconnect/backoff/pool values, `redis_reconnect_max_delay < redis_reconnect_delay`, `cs_backoff_max < cs_backoff_base`, `cs_backoff_factor < 1.0`, `cs_jitter_fraction` outside `[0, 1]`, empty Redis channel/prefix strings, raw-string `terminal_processing_statuses`, non-string terminal statuses, blank terminal statuses, and empty `terminal_processing_statuses`. Valid terminal statuses are normalized to a stripped/lowercased `frozenset[str]`.
- Common settings expose deployment-configurable Delivery fields for every runtime `DeliveryConfig` field: `heartbeat_interval_seconds`, `shutdown_drain_seconds`, `cancellation_ttl_seconds`, `terminal_dedup_ttl_seconds`, `cancellation_cache_maxsize`, `cancellation_token_cache_maxsize`, `terminal_dedup_cache_maxsize`, `redis_sse_channel_prefix`, `redis_cancel_channel`, `redis_internal_channel`, `redis_dead_letter_channel`, `redis_reconnect_delay`, `redis_reconnect_max_delay`, `redis_cancel_key_prefix`, `redis_terminal_key_prefix`, `dead_letter_memory_maxlen`, `handler_shutdown_timeout_seconds`, `redis_max_connections`, `redis_room_subscription_production_limit`, `redis_subscription_reserved_connections`, `cs_backoff_base`, `cs_backoff_max`, `cs_backoff_factor`, `cs_jitter_fraction`, and `terminal_processing_statuses`. Task 2 only tests settings field existence/default validation and `DeliveryConfig` purity. It must not assert container helper mapping or concrete `RedisPubSubImpl(max_connections=...)` wiring before Task 11 creates those composition-boundary helpers.
- `{"delivery", "delivery.sse", "delivery.event_bus"}` are listed in `pyproject.toml`.
- AST import boundary forbids all business/import-shell roots listed above.
- AST import boundary rejects dynamic forbidden imports via `importlib.import_module("services...")`, `importlib.import_module("modules...")`, and `__import__("services...")`.
- Business-module boundary rejects concrete `delivery.*` imports everywhere except `container.py`. `main.py` receives/passes the facade object returned by container helpers without importing concrete Delivery, and `services/sse_services.py` uses structural typing or local protocols for `bind_facade()` instead of importing concrete Delivery classes.
- Delivery-owned background task creation uses `traced_create_task()` or injected `task_runner`; AST checks reject bare or aliased task creation under `delivery/**`, including `asyncio.create_task(...)`, `from asyncio import create_task` plus `create_task(...)`, and `loop.create_task(...)`.
- `common.observability.traced_create_task` exists in `common/observability/tracing.py`, is exported from `common.observability`, and accepts `name` as a keyword-only argument.
- `common.observability.get_current_trace_id() -> str | None` exists and is exported; in Phase 6 it may return only an explicitly stored contextvar value or `None`.
- `common.observability.trace_id_context(trace_id)` exists, is exported, sets the explicit trace id for the context, and restores the prior value after exit.

Run:

```bash
PYTHONPATH=. uv run pytest -q tests/test_delivery_protocols.py
```

Expected: fail because the package does not exist.

- [ ] Implement the package skeleton and `DeliveryConfig`.

Implementation notes:
- `delivery/config.py` must not read from `common.config.settings`; keep `DeliveryConfig` pure and pass in resolved primitive settings from the app shell/container. Do not implement a settings-backed `from_settings` method in `delivery/config.py` that accepts the full app settings object.
- Add Common settings/env fields for every runtime `DeliveryConfig` field, including heartbeat interval, shutdown drain seconds, cancellation/terminal TTLs, cancellation/token/terminal cache maxsizes, terminal statuses, Redis channels/prefixes, `redis_internal_channel`, `redis_dead_letter_channel`, reconnect delays, dead-letter memory max length, handler shutdown timeout, change-stream backoff, Redis pool size, room-subscription limit, and reserved Pub/Sub connection headroom. Do not implement the container-owned extraction helper in Task 2; Task 11 owns reading those fields from the full app settings object and constructing `DeliveryConfig(...)`.
- Define `DeliveryStartupPolicy` in `delivery/config.py` and validate degraded-policy combinations in `__post_init__` or equivalent constructor validation. Later tasks import it from `delivery.config`, not from `delivery.types` or `delivery.facade`.
- `delivery/types.py` defines `TaskRunner` in Task 2 so later tasks import one shared protocol instead of duplicating local definitions.
- `delivery/types.py` defines `RoomSubscriptionLimitExceeded` in Task 2 so Task 3 can test transport rollback on subscription rejection before the concrete event bus is implemented in Task 6.
- Keep config as primitive values so tests can inject a custom config without monkeypatching global settings.
- Do not import `config.settings`, `common.config.settings`, or `common.config.settings.settings`.
- Add `common.observability.tracing.traced_create_task(coro, *, name: str | None = None)` if absent, and export it from `common.observability`. Keep it dependency-light: use `contextvars.copy_context()` plus `asyncio.create_task(..., name=name)` inside the helper; Delivery code itself must not call bare `asyncio.create_task()`. Do not add OpenTelemetry as a new runtime dependency in Phase 6.
- Add `common.observability.tracing.get_current_trace_id() -> str | None`. Without OpenTelemetry, this helper should return an explicit trace-id contextvar if one exists, otherwise `None`.
- Add `common.observability.tracing.trace_id_context(trace_id: str | None)` as a context manager for tests/app-shell code to set the explicit trace id used by `get_current_trace_id()`.
- Design deviation to document in Task 13: the design sketch links parent spans with OpenTelemetry `Link(parent_span.get_span_context())`; Phase 6's helper preserves Python context and task names without adding OTel dependencies. If OTel is introduced later, enhance this helper in place without changing Delivery call sites.

- [ ] Run protocol tests.

Run:

```bash
PYTHONPATH=. uv run pytest -q tests/test_delivery_protocols.py
```

Expected: pass for skeleton-level tests, except tests that depend on later concrete methods may remain skipped or marked to implement in later tasks.

- [ ] Commit.

```bash
git add delivery common/config/settings.py common/observability/tracing.py common/observability/__init__.py pyproject.toml tests/test_delivery_protocols.py
git commit -m "feat(delivery): add delivery package boundary"
```

### Task 3: SSEConnection and Local SSETransport Behavior

**Files:**
- Create: `delivery/sse/connection.py`
- Create: `delivery/sse/manager.py`
- Modify: `common/protocols/delivery_protocols.py`
- Test: `tests/test_delivery_sse_connection.py`
- Test: `tests/test_delivery_sse_manager.py`
- Test: `tests/test_delivery_protocols.py`

- [ ] Write failing connection tests.

Cover:
- Constructor stores provided `connection_id` and `room_id` as public attributes for the legacy `api/sse.py` path.
- `is_active` is true after construction, false after `close()`, and remains the property used by `api/sse.py` while looping.
- `send_frame()` queues a frame dict and returns true when active.
- `send_message(message_type, data)` compatibility method builds the exact legacy frame dict with top-level `type`, `timestamp`, `room_id`, and `data`, queues it as a dict, and returns false when inactive.
- Internal queue contents are always dict frames. Do not queue JSON strings internally; retarget legacy queue-level JSON assertions to call `get_message()`.
- `get_message(timeout=...)` serializes the next queued dict frame to JSON for legacy adapter compatibility.
- `next_frame(timeout=...)` returns dict frames for the `SSETransport.connect()` protocol path.
- Timeout returns heartbeat frame with exactly current shape: `{"type": "heartbeat", "timestamp": iso, "room_id": room_id}`.
- `close()` marks inactive.

Run:

```bash
PYTHONPATH=. uv run pytest -q tests/test_delivery_sse_connection.py
```

Expected: fail.

- [ ] Implement `SSEConnection`.

Implementation notes:
- `SSEConnection.__init__` requires `room_id`, `connection_id`, `heartbeat_interval`, and injected `now`; do not generate an id inside `SSEConnection`.
- Preserve current heartbeat cadence default of 30 seconds through config.
- Use injected `now()` for deterministic tests.
- JSON serialization belongs in `get_message()` compatibility method; internal delivery can pass dicts.

- [ ] Write failing local transport tests.

Cover:
- `open_connection()` adds room and connection maps.
- `connect(room_id, connection_id)` stores the connection under the caller-provided `connection_id`; `disconnect(connection_id)` removes that exact id from both room and reverse maps.
- `open_connection(room_id)` has no connection-id parameter, so it calls `id_factory()` exactly once, stores the connection under that generated id, and returns a connection whose `connection_id` equals that generated id.
- First active local connection for a room calls `event_bus.subscribe_room(room_id)` exactly once, for both `open_connection()` and `connect()` paths.
- Additional active connections in the same room do not call `subscribe_room()` again.
- After the room becomes empty and unsubscribes, a later new first connection to that room calls `subscribe_room(room_id)` again.
- If `event_bus.subscribe_room(room_id)` raises `RoomSubscriptionLimitExceeded` or another subscription-admission exception for a first connection to a new room, `open_connection()` and `connect()` reject with `ConnectionRefusedError` before local admission. The failed attempt must leave no `room_connections` entry, no `connection_rooms` reverse-map entry, no active `SSEConnection`, no unsubscribe requirement, and no `hybro_delivery_sse_connections` gauge increment.
- Concurrent first-connection admission test: with a fake `event_bus.subscribe_room()` that blocks and then raises for a new room, start two concurrent `open_connection("room-1")` calls. While the first subscription is pending, no connection may appear in `room_connections` or `connection_rooms`, no connection gauge may increment, and the second opener must wait on the same per-room admission state instead of observing/admitting a partial room. After the fake subscription fails, both callers reject with `ConnectionRefusedError`, maps remain empty, no unsubscribe is called, and the gauge remains unchanged.
- Concurrent first-connection success test: with a fake `event_bus.subscribe_room()` that blocks and then succeeds for a new room, start two concurrent `open_connection("room-1")` calls. Expected: exactly one `subscribe_room("room-1")` call, both connections admitted after the subscription succeeds, `room_connections["room-1"]` contains both connection ids, both reverse-map entries exist, and `hybro_delivery_sse_connections` gauge records absolute value `2`.
- `remove_connection()` closes and removes connection.
- Last disconnect triggers room-unsubscribe hook on the event bus.
- `broadcast_frame_to_room()` snapshots connections under lock and sends outside the lock.
- Dead/inactive connections are removed.
- If `broadcast_frame_to_room()` removes dead/inactive connections and that cleanup removes the last connection in a room, it must also remove the room entry, remove all reverse-map entries, call `event_bus.unsubscribe_room(room_id)` exactly once, and record `hybro_delivery_sse_connections` gauge value `0`.
- `close_all_connections()` closes every active connection, clears `room_connections` and `connection_rooms`, unsubscribes every subscribed room exactly once, is idempotent, and records `hybro_delivery_sse_connections` gauge value `0`.
- Local ordering is preserved for sequential broadcasts.
- Empty-room broadcast is a no-op.
- `set_draining(True)` rejects new connections with `ConnectionRefusedError`.
- `connect()` yields heartbeat frames and disconnects in `finally`.
- Custom heartbeat interval test: with `DeliveryConfig(heartbeat_interval_seconds=3)`, `SSEConnection.get_message()` / transport `connect()` yields heartbeat frames after the configured 3-second timeout, while default-config tests still preserve the legacy 30-second cadence. No test should pass if connection code hard-codes 30 seconds.
- `hybro_delivery_sse_connections` gauge records absolute active connection counts with `metrics.gauge(name, value, tags)`: 0 initially, 1 after first connection, 2 after second connection, and decremented absolute values after disconnects, labeled by `worker_id=instance_id`.
- Transport-level metric tests own connection gauge assertions; publisher tests should not assert connection counts except through integration coverage.

Production-path note: during Phase 6, API routes still call the C3 adapter's `add_connection()`, which delegates to `facade.compat.open_connection()`, then use the `SSEConnection.get_message()` loop. The Common `SSETransport.connect()` path must still be fully tested here even though it does not become the live API route path until a later API/caller migration phase.
Protocol call-shape update: Phase 6 must deliberately change `common/protocols/delivery_protocols.py` so `SSETransport.connect` is `def connect(...) -> AsyncIterator[dict]`, not `async def connect(...)`. Add a protocol conformance test that uses the production-intended call shape `async for frame in transport.connect(room_id, connection_id): ...`; no caller should need `await transport.connect(...)` before iterating. Task 13 must update `docs/MODULAR_DECOUPLING_DESIGN.md` to document this as the narrow async-iterator-factory exception to the broader "cross-module methods are async" invariant.

Run:

```bash
PYTHONPATH=. uv run pytest -q tests/test_delivery_sse_manager.py
```

Expected: fail.

- [ ] Implement local `SSETransportImpl`.

Implementation notes:
- Keep `room_connections: dict[str, dict[str, SSEConnection]]`.
- Also maintain `connection_rooms: dict[str, str]` so protocol `disconnect(connection_id)` does not need a room id.
- Subscribe to the room channel outside the lock only when the active-connection count transitions from 0 to 1. Unsubscribe when it transitions from 1 to 0.
- Subscription failure rollback belongs in Task 3, not Task 6: create the connection object only after first-room subscription admission succeeds, or remove it in a `try`/`except` rollback before raising `ConnectionRefusedError`. Do not add the connection to room maps and then leave it admitted when `subscribe_room()` rejects.
- Protect first-room admission with a per-room admission lock or explicit pending-subscription state. The implementation must prevent concurrent first openers from seeing a half-admitted room while `subscribe_room()` is still pending.
- Dead-connection cleanup and shutdown cleanup must reuse the same last-connection path as explicit disconnects so room unsubscribe, map cleanup, and connection gauge behavior cannot diverge.
- Use the `instance_id` constructor argument as the metrics `worker_id` label. `DeliveryFacade` must pass the same shared id to `SSETransportImpl`, `EventPublisherImpl`, and `CrossInstanceEventBus`.
- Do not publish to Redis from the transport directly except through event-bus subscription hooks; EventPublisher owns emit fan-out.

- [ ] Run local SSE tests.

Run:

```bash
PYTHONPATH=. uv run pytest -q tests/test_delivery_sse_connection.py tests/test_delivery_sse_manager.py tests/test_delivery_protocols.py
```

Expected: pass.

- [ ] Commit.

```bash
git add delivery/sse common/protocols/delivery_protocols.py tests/test_delivery_sse_connection.py tests/test_delivery_sse_manager.py tests/test_delivery_protocols.py
git commit -m "feat(delivery): add local sse transport"
```

### Task 4: Terminal Processing Status Deduplication

**Files:**
- Create: `delivery/sse/deduplication.py`
- Modify: `delivery/config.py`
- Modify: `dal/redis/kv.py`
- Modify: `dal/redis/streams.py`
- Test: `tests/test_delivery_deduplication.py`
- Test: `tests/test_dal_protocols.py`
- Test: `tests/test_dal_unit.py`

- [ ] Write failing dedup tests.

Cover:
- Non-terminal statuses are never deduped.
- First terminal status for `(room_id, message_id)` passes and stores L1.
- Second terminal status for same key is suppressed by L1 without Redis call.
- On L1 miss, Redis `setnx("terminal:{room_id}:{message_id}", status, ttl=300)` suppresses if false.
- Custom Redis key-prefix test: with `DeliveryConfig(redis_terminal_key_prefix="termx:")`, the L2 Redis call uses `setnx("termx:{room_id}:{message_id}", status, ttl=...)`; no test should pass if the deduplicator hard-codes `"terminal:"`.
- Custom-config TTL test: with `DeliveryConfig(terminal_dedup_ttl_seconds=7)`, Redis `setnx()` receives `ttl=7`; no test should pass if the deduplicator hard-codes `300`.
- Custom L1 TTL test: with `DeliveryConfig(terminal_dedup_ttl_seconds=7)` and a fake cache timer, the immediate second terminal status is suppressed by L1, then after advancing past 7 seconds the same `(room_id, message_id)` is no longer suppressed by L1 and falls through to Redis/L2 logic. This test must fail if the L1 `TTLCache` hard-codes `ttl=300`.
- Custom terminal-status set test: with `DeliveryConfig(terminal_processing_statuses=frozenset({"done"}))`, status `"done"` is deduped and a default-only terminal status such as `"completed"` is not deduped under that custom config. Keep separate default-config tests for legacy terminal statuses.
- `RedisKV.setnx()` `TransientError` or other exceptions degrade to L1-only behavior inside the deduplicator and never escape Delivery.
- `TerminalStatusDeduplicator` catches `TransientError` or other Redis exceptions from `RedisKV.setnx()` and falls back to L1-only delivery for that event.
- `RedisKVImpl.setnx()` returns `False` only for a real Redis NX miss. If Redis is configured and the driver raises, `setnx()` raises `common.errors.TransientError` instead of returning `False`, so Delivery can distinguish transient Redis failure from duplicate terminal status.
- `RedisKVImpl.get()`, `set()`, `delete()`, `increment()`, `setnx()`, and `exists()` all raise `TransientError` on configured-driver failures; tests cover each method so Redis DAL behavior is consistent instead of method-specific.
- `RedisStreamsImpl.xadd()` and `xread()` raise `TransientError` on configured-driver failures while preserving empty-Redis graceful fallbacks.
- Empty Redis URL remains graceful: `RedisKVImpl(client=None, url="")` keeps returning disabled/no-op values for every KV operation (`get()` returns `None`, `set()` no-ops, `delete()` false, `increment()` zero, `setnx()` false, `exists()` false, `ping()` false). The new `TransientError` behavior applies only when Redis is configured or a client was injected and the driver operation fails.
- `TerminalStatusDeduplicator(redis_kv=None)` uses L1-only deduplication and does not treat missing Redis as a duplicate or failure.
- Missing `message_id` never dedups.
- With the default config, legacy raw statuses `rejected`, `rate_limited`, and `error` are terminal and deduped.
- With the default config, legacy raw status `awaiting_input` is non-terminal and is not deduped.
- The L1 `TTLCache` uses `maxsize=config.terminal_dedup_cache_maxsize` and `ttl=config.terminal_dedup_ttl_seconds`, defaulting to `10_000` and `300` respectively to preserve current cache behavior.

Run:

```bash
PYTHONPATH=. uv run pytest -q tests/test_delivery_deduplication.py
```

Expected: fail.

- [ ] Implement `TerminalStatusDeduplicator`.

Implementation sketch:

```python
class TerminalStatusDeduplicator:
    async def should_deliver(
        self,
        *,
        room_id: str,
        message_id: str | None,
        status: str,
    ) -> bool:
        ...
```

Use `RedisKV.setnx()`, not `infrastructure.redis_service.RedisService.set_nx()`.
Do not pass a concrete `RedisKVImpl` into Delivery when Redis is not configured; pass `None`. That keeps single-process/no-Redis mode from treating "no Redis client" as a duplicate status.
Inject a fake timer or cache factory in tests so L1 TTL expiry is proven without sleeping.

- [ ] Run dedup tests.

Run:

```bash
PYTHONPATH=. uv run pytest -q tests/test_delivery_deduplication.py tests/test_dal_protocols.py tests/test_dal_unit.py -k "redis or dedup or get or set or delete or increment or setnx or exists or xadd or xread"
```

Expected: pass.

- [ ] Commit.

```bash
git add delivery/sse/deduplication.py delivery/config.py dal/redis/kv.py dal/redis/streams.py tests/test_delivery_deduplication.py tests/test_dal_protocols.py tests/test_dal_unit.py
git commit -m "feat(delivery): add terminal status deduplication"
```

### Task 5: Cancellation State and Mongo Change-Stream Watcher

**Files:**
- Create: `delivery/sse/cancellation_watcher.py`
- Modify: `delivery/sse/manager.py`
- Modify: `common/protocols/dal_protocols.py`
- Modify: `common/protocols/__init__.py`
- Modify: `dal/mongo/client.py`
- Modify: `dal/redis/kv.py`
- Test: `tests/test_delivery_cancellation.py`
- Test: `tests/test_dal_protocols.py`
- Test: `tests/test_dal_unit.py`

- [ ] Write failing cancellation tests.

Cover:
- `cancel_message()` marks L1 cancelled and signals an existing `CancellationToken`.
- `create_token()` pre-signals when the message was already cancelled.
- Cancellation tokens are stored in a TTL cache using `maxsize=config.cancellation_token_cache_maxsize` and `ttl=config.cancellation_ttl_seconds`. With `DeliveryConfig(cancellation_ttl_seconds=11)` and a fake timer, `get_token(message_id)` returns the token before 11 seconds and returns `None` after expiry.
- With the default config, `mark_cancelled()` writes Redis L2 key `cancelled:{message_id}` with 3600-second TTL.
- Custom Redis key-prefix tests: with `DeliveryConfig(redis_cancel_key_prefix="cx:")`, `mark_cancelled()` writes `cx:{message_id}`, `check_cancelled()` calls Redis `exists("cx:{message_id}")` after the L1 miss path, and incoming remote cancellation best-effort L2 writes also use `cx:{message_id}`. No test should pass if the watcher hard-codes `"cancelled:"`.
- Custom-config TTL test: with `DeliveryConfig(cancellation_ttl_seconds=11)`, `mark_cancelled()` writes the Redis L2 key with `ttl=11`; no test should pass if cancellation TTL is hard-coded to 3600.
- `check_cancelled()` uses L1 fast path before Redis `exists()`.
- `RedisKVImpl.set()` raises `TransientError` when Redis is configured and the driver write fails, so cancellation L2 write failures are visible to Delivery and can be logged/dead-lettered according to the caller path.
- `RedisKVImpl.exists()` raises `TransientError` when Redis is configured and the driver read fails, so cancellation L2 checks do not silently look like "not cancelled".
- Existing non-Delivery RedisKV callers/tests must be updated to the same split behavior: empty Redis URL is graceful, configured-driver failures raise `TransientError`.
- `CancellationWatcher.mark_cancelled()` catches Redis `set()` `TransientError` after updating L1/token state, logs it, and continues to Pub/Sub fan-out.
- `CancellationWatcher.mark_cancelled()` catches `event_bus.publish_cancellation()` failures after local L1/token cancellation and Redis L2 write attempt, logs the failure, and does not raise to callers. Local cancellation must remain effective even when cross-instance fan-out fails.
- `CancellationWatcher.check_cancelled()` catches Redis `exists()` `TransientError`, logs a warning, and returns the L1 result without raising.
- Incoming broker cancellation marks L1, signals token, and best-effort writes Redis L2 so late-joining instances can observe it. Redis L2 write failures are warning-only and must not prevent local token cancellation.
- `clear_cancellation()` removes L1 and token.
- Change-stream insert event with `fullDocument.message_id` marks cancellation.
- `MongoCollection.watch()` protocol is typed as an async context manager whose entered value is an `AsyncIterator[dict]`.
- `MongoCollectionAdapter.watch()` in `dal/mongo/client.py` has the public annotation/shape `-> MongoChangeStream` (or the concrete runtime-compatible return type satisfying it), and tests prove `async with adapter.watch(...) as stream:` works with the Motor change-stream object. Do not leave the concrete adapter annotated as `AsyncIterator[dict]`.
- `MongoChangeStream` is exported from `common/protocols/__init__.py`; it is a public Common protocol because `MongoCollection.watch()` exposes it in the public DAL contract.
- Watcher reconnect uses resume token and clears stale token after 3 consecutive change-stream failures, matching current legacy behavior.
- Custom change-stream backoff tests: with `DeliveryConfig(cs_backoff_base=0.2, cs_backoff_max=0.8, cs_backoff_factor=3.0, cs_jitter_fraction=0.0)`, reconnect scheduling uses the configured base/max/factor/jitter values through an injected sleeper/backoff recorder or fake clock. No test should pass if the watcher hard-codes legacy backoff values.
- `stop_cancellation_watcher()` resets shutdown state so a later start is possible after startup failure cleanup.

Run:

```bash
PYTHONPATH=. uv run pytest -q tests/test_delivery_cancellation.py
```

Expected: fail.

- [ ] Implement `CancellationWatcher`.

Implementation notes:
- Import `CancellationToken` from `common.utils.cancellation`.
- Constructor accepts a non-optional `MongoCollection`, `RedisKV | None`, `CrossInstanceEventBus | None`, `DeliveryConfig`, `now`, and an injected `task_runner`. Tests that do not exercise the watcher should pass a fake `MongoCollection` or a dedicated test fixture; do not make the production constructor accept `None`.
- Update `common/protocols/dal_protocols.py` before implementing the watcher: define a small public `MongoChangeStream` protocol with `__aenter__`, `__aexit__`, and async iteration over `dict`, then make `MongoCollection.watch(...) -> MongoChangeStream`. Export `MongoChangeStream` from `common/protocols/__init__.py` and `common/protocols/dal_protocols.py.__all__`. This resolves the current misleading `AsyncIterator[dict]` annotation and matches Motor cleanup semantics.
- Update `dal/mongo/client.py` in the same task so `MongoCollectionAdapter.watch()` advertises and returns the same async-context-manager shape. Add DAL protocol/conformance tests for both the protocol and the concrete adapter annotation/usage.
- Implement the watcher only with `async with collection.watch(...) as change_stream: async for change in change_stream: ...`. Do not implement a bare iterator path that skips context-manager cleanup.
- The watcher runs per worker; do not use leader election.
- Production startup must provide a cancellation collection. If `cancellation_collection` is missing, Delivery startup is fatal outside explicit unit-test fakes; do not silently run any production worker without the cancelled-messages watcher. Only initial `MongoCollection.watch()` setup failure, with a valid collection object present, may use the `DeliveryStartupPolicy` degraded single-worker/no-Redis path.
- Start the watcher background loop with the injected `task_runner`/`traced_create_task`; do not call bare `asyncio.create_task()` inside Delivery.
- `mark_cancelled()` should do local cancellation first, then Redis L2, then Pub/Sub fan-out.
- `is_cancelled()` remains L1-only for hot paths.
- `start_cancellation_watcher()` must include a startup readiness handshake. It should not return successfully until the first `collection.watch(...)` setup has entered the async context and the watcher has set `change_stream_connected=True`. If initial watch setup fails, the watcher must surface that failure synchronously to `DeliveryFacade.start()` as an exception or explicit readiness result; `DeliveryFacade.start()` then applies `DeliveryStartupPolicy` to either re-raise as fatal or continue degraded. Tests must prove initial watch setup failure is visible to startup rather than hidden in a background task.
- Use `TTLCache(maxsize=config.cancellation_cache_maxsize, ttl=config.cancellation_ttl_seconds)` for cancelled ids and `TTLCache(maxsize=config.cancellation_token_cache_maxsize, ttl=config.cancellation_ttl_seconds)` for cancellation tokens. Defaults are `10_000` maxsize and 3600 seconds TTL, preserving the legacy cleanup behavior.
- Resume-token recovery threshold is exactly 3 consecutive change-stream failures. Add a test that failures 1 and 2 retain the resume token and failure 3 clears it before reconnecting from the current oplog position.
- DAL failure contract: Phase 6 updates all `RedisKVImpl` data operations to propagate configured-driver failures as `TransientError`. Delivery catches those at the cancellation boundary; the DAL must not silently return `None`/`False`/`0` for configured Redis driver failures.

- [ ] Wire cancellation compatibility methods through `SSETransportImpl`.

- [ ] Run cancellation tests.

Run:

```bash
PYTHONPATH=. uv run pytest -q tests/test_delivery_cancellation.py tests/test_delivery_sse_manager.py tests/test_dal_protocols.py tests/test_dal_unit.py
```

Expected: pass.

- [ ] Commit.

```bash
git add delivery/sse/cancellation_watcher.py delivery/sse/manager.py common/protocols/dal_protocols.py common/protocols/__init__.py dal/mongo/client.py dal/redis/kv.py tests/test_delivery_cancellation.py tests/test_dal_protocols.py tests/test_dal_unit.py
git commit -m "feat(delivery): add cancellation propagation"
```

### Task 6: Redis Cross-Instance Event Bus

**Files:**
- Create: `delivery/event_bus/cross_instance.py`
- Modify: `delivery/event_bus/__init__.py`
- Modify: `delivery/types.py`
- Modify: `dal/redis/pubsub.py`
- Test: `tests/test_delivery_event_bus.py`
- Test: `tests/test_dal_protocols.py`
- Test: `tests/test_dal_unit.py`

- [ ] Write failing event-bus tests with fake `RedisPubSub`.

Cover:
- `publish_sse(room_id, frame)` uses `json.dumps()` to serialize an envelope and passes a `str` to `RedisPubSub.publish()`. The decoded JSON envelope has `kind="sse_event"`, `origin`, `room_id`, `type`, `data`, and `frame`.
- Custom SSE channel-prefix tests: with `DeliveryConfig(redis_sse_channel_prefix="custom:sse:")`, `publish_sse("room-1", frame)` publishes to channel `"custom:sse:room-1"`, `subscribe_room("room-1")` subscribes/tracks desired channel `"custom:sse:room-1"`, `unsubscribe_room("room-1")` cancels/removes that same configured channel, and incoming callbacks are still routed by `room_id`. No event-bus test should pass if the prefix is hard-coded to `"sse:room:"`.
- `publish_sse()`, `publish_cancellation()`, `publish_internal()`, and `publish_dead_letter()` never pass raw dicts to `RedisPubSub.publish()`; fake Redis tests assert the captured `message` argument is a `str`, is valid JSON, and decodes to the exact expected envelope.
- Subscription loops decode incoming JSON strings from `RedisPubSub.subscribe()` before routing. Malformed JSON is logged and dropped without raising.
- Incoming self-origin SSE envelopes are ignored.
- Incoming other-origin SSE envelopes call local delivery callback exactly once.
- Incoming legacy SSE envelopes without `frame` are accepted for rolling deploy/backward compatibility: reconstruct the full legacy SSE frame as `{"type": payload["type"], "timestamp": now().isoformat(), "room_id": payload["room_id"], "data": payload["data"]}` and deliver it locally. This preserves the legacy remote-delivery frame shape produced by `_deliver_to_local_connections()`.
- `subscribe_room(room_id)` starts one subscription task per active room and is idempotent.
- `unsubscribe_room(room_id)` cancels the room subscription.
- Bounded subscription-load test: with the default pool-compatible config, subscribing 40 distinct rooms with fake Redis creates exactly 40 desired room channels/subscription tasks, duplicate `subscribe_room()` calls for those rooms create zero additional tasks, and unsubscribing all rooms leaves zero desired room channels. This test is the Phase 6 gate for the intentional per-room model under the current default Redis pool.
- Custom-config room-limit test: with `redis_room_subscription_production_limit=2` and Redis Pub/Sub configured, `subscribe_room()` accepts two distinct active rooms and raises `RoomSubscriptionLimitExceeded` for the third distinct active room. This proves the configured limit is used rather than hard-coded 100.
- Unsafe-capacity ownership check: Task 6 relies on Task 2 `DeliveryConfig` validation for `redis_room_subscription_production_limit + redis_subscription_reserved_connections <= redis_max_connections`. Event-bus tests should use already-valid configs; if they include a smoke assertion for `redis_max_connections=50`, `redis_subscription_reserved_connections=10`, and `redis_room_subscription_production_limit=100`, it must assert `DeliveryConfig(...)` fails before event-bus startup rather than duplicating facade/container validation.
- Over-limit enforcement test: with default config, `subscribe_room()` for a 41st distinct active room raises `RoomSubscriptionLimitExceeded`, does not create a desired channel or task, and logs a warning. Additional connections to any of the existing 40 rooms remain idempotent and allowed, and after one room unsubscribes a new distinct room can subscribe successfully.
- Room-limit rejection is a capacity/admission event, not a Redis connectivity failure. Do not add a health field in Phase 6 and do not flip `delivery_pubsub_connected` / bus `is_connected` solely because the active-room limit was reached; the observable behavior is exception + warning + documented deployment limit.
- If `RedisPubSub.subscribe(channel)` raises before yielding, the room/global subscription loop marks the bus disconnected, waits with exponential backoff, and retries while the channel is still desired.
- If the async iterator returned by `subscribe(channel)` raises during listen, the loop marks disconnected, backs off, re-subscribes to the same channel, and continues delivering later messages.
- `unsubscribe_room(room_id)` stops retrying that room after the current subscription task is cancelled.
- Global cancellation/internal subscription loops use the same reconnect/backoff behavior as room subscriptions.
- `publish_cancellation(message_id)` publishes a JSON string to `config.redis_cancel_channel` with decoded envelope fields `kind="cancellation"`, `origin`, and `message_id`.
- Custom cancellation channel tests: with `DeliveryConfig(redis_cancel_channel="custom:cancel")`, `publish_cancellation()` publishes to `"custom:cancel"` and the global cancellation subscription loop subscribes/listens to `"custom:cancel"`, not the default `"cancel:global"`.
- Incoming cancellation skips self-origin envelopes and calls the cancellation callback exactly once for other-origin envelopes with `message_id`.
- `publish_internal(event)` publishes a JSON string to the configured internal channel.
- Custom internal channel test: with `DeliveryConfig(redis_internal_channel="custom:internal")`, `publish_internal(event)` publishes to `"custom:internal"` and the internal global subscription loop subscribes/listens to `"custom:internal"`, not the default `"internal:global"`.
- Internal Redis event envelopes use a pinned JSON shape: `{"kind": "internal_event", "origin": instance_id, "event_type": event.event_type, "event": event.model_dump(mode="json"), "trace_id": trace_id_or_none}`. The bus validates envelope shape and self-origin only, then passes the raw envelope to the registered `on_internal_envelope(envelope)` callback. `EventPublisherImpl` owns `TypeAdapter(InternalEvent)` deserialization and handler dispatch.
- Internal Redis envelope mismatch validation is performed by `EventPublisherImpl.handle_remote_internal_event()`: if top-level `envelope["event_type"]` differs from the reconstructed nested event's `event_type`, log/drop the envelope and do not dispatch a handler. Add a publisher test with top-level `"message_committed"` and nested `"run_state_changed"` proving it is rejected.
- Remote internal-event trace restoration is publisher-owned: `handle_remote_internal_event()` must enter `trace_id_context(envelope["trace_id"])` before scheduling/running handlers, and tests must prove a handler invoked for a remote internal event sees `get_current_trace_id() == envelope["trace_id"]`. If the envelope trace id is absent/`None`, the prior trace context must be restored and no synthetic id is created.
- Incoming internal events skip self-origin and dispatch to the registered callback.
- `publish_dead_letter(envelope)` publishes a JSON string to `config.redis_dead_letter_channel` and propagates `TransientError` to the publisher fallback path.
- Custom dead-letter channel test: with `DeliveryConfig(redis_dead_letter_channel="custom:dead")`, `publish_dead_letter(envelope)` publishes to `"custom:dead"`, and EventPublisher fan-out/handler failure paths that use bus dead-letter publication target that configured channel.
- Malformed JSON/envelopes are logged and dropped without raising.
- With `redis_pubsub=None`, `start()`, `publish_sse()`, `publish_cancellation()`, `publish_internal()`, `publish_dead_letter()`, `subscribe_room()`, and `unsubscribe_room()` are no-op/best-effort operations that do not raise, create Redis subscription tasks, or mark Redis connected.
- `RedisPubSubImpl.publish()` raises `common.errors.TransientError` when Redis is configured and the driver publish call fails.
- `RedisPubSubImpl.subscribe()` returns an iterator that propagates subscribe/listen failures to the caller instead of swallowing them forever; reconnect ownership belongs to `CrossInstanceEventBus`.
- `RedisPubSubImpl.ping()` returns `False` for an invalid Redis URL or failed connection, and the event bus reports disconnected before multi-worker safety passes.
- `RedisPubSubImpl` accepts an explicit `max_connections`/pool-size constructor argument, stores it, and uses it when creating the Redis connection/pool. Tests must assert the constructor value is used instead of silently reading a different global `settings.redis_max_connections`.
- `CrossInstanceEventBus.refresh_health()` awaits `RedisPubSub.ping()` when Redis Pub/Sub is configured, updates cached Redis reachability, combines it with subscription readiness, and catches/logs ping exceptions as disconnected. `delivery_pubsub_connected` / bus `is_connected` is true only when Redis is reachable and all required global subscriptions plus currently desired room subscriptions are active/listening, not backing off. `start()` should call this method rather than duplicating ping logic, and `/health` refresh through `DeliveryFacade.refresh_health()` should call it too.
- Pub/Sub health tests must distinguish reachability from readiness: ping true while cancellation/internal or desired room subscription loops are in reconnect/backoff reports `delivery_pubsub_connected=False`; ping true plus all required subscriptions active reports true; ping false reports false regardless of subscription state.
- `CrossInstanceEventBus.start()` has a startup readiness handshake for global cancellation/internal subscriptions. It should create the global subscription tasks, wait until both have successfully entered their first subscribed/listening state or have reported initial failure/backoff, and only then return. Health must remain `delivery_pubsub_connected=False` until those readiness signals are true, even when Redis ping succeeds.
- Custom reconnect-delay tests: with `DeliveryConfig(redis_reconnect_delay=0.25, redis_reconnect_max_delay=0.5)`, failed room/global subscribe/listen loops schedule retries using the configured base/max delays. Tests may inject a fake sleeper/backoff recorder; no test should pass if the bus hard-codes `1.0`/`30.0`.

Run:

```bash
PYTHONPATH=. uv run pytest -q tests/test_delivery_event_bus.py
```

Expected: fail.

- [ ] Implement `CrossInstanceEventBus`.

Implementation notes:
- Depend on `RedisPubSub | None`.
- Constructor accepts `RedisPubSub | None`, `DeliveryConfig`, `instance_id`, injected `task_runner`, injected `now: Callable[[], datetime]`, and optional callbacks: `on_sse_frame(room_id, frame)`, `on_cancellation(message_id)`, and `on_internal_envelope(envelope)`. Use callback protocols/type aliases from `delivery/types.py`.
- Provide explicit setters `set_sse_callback(...)`, `set_cancellation_callback(...)`, and `set_internal_callback(...)` so `DeliveryFacade` can finish wiring after constructing the bus, transport, publisher, and cancellation watcher without circular constructor dependencies.
- The facade wiring order is: construct bus with no callbacks, construct cancellation watcher with bus for outbound fan-out, construct transport with watcher+bus, construct publisher with transport+bus, then register bus callbacks to `transport.broadcast_frame_to_room`, `watcher.handle_remote_cancellation`, and `publisher.handle_remote_internal_event`. The internal callback receives the raw internal envelope; the publisher owns DTO deserialization and mismatch validation.
- Constructor accepts injected `task_runner` and `now`, and defaults those through the facade to exported `common.observability.traced_create_task` and the shared Delivery clock. Do not call `utcnow()` directly inside the bus; the legacy-envelope fallback timestamp must use injected `now()` for deterministic tests.
- Use one subscription task per subscribed room because the existing `RedisPubSub` protocol exposes `async def subscribe(channel) -> AsyncIterator[str]`. Call it as `messages = await redis_pubsub.subscribe(channel); async for message in messages: ...`. Do not write `async for message in redis_pubsub.subscribe(channel)` unless the Common protocol is deliberately changed in the same task.
- Scalability gate/defer: this per-room subscription model matches the target `redis_sse_channel_prefix` design, but differs from the current `RedisBroker` single subscriber loop. Phase 6 explicitly supports and tests up to `config.redis_room_subscription_production_limit` active SSE rooms per worker, default `40`, because the current Redis pool default is `50` and Delivery reserves headroom for global subscriptions, publish, ping, and other Redis operations. `CrossInstanceEventBus.subscribe_room()` enforces the limit and raises `RoomSubscriptionLimitExceeded`; `SSETransportImpl` rollback/rejection for that exception is already tested in Task 3. Do not silently create the 41st room subscription or admit a local-only connection. Do not deploy a higher active-room-per-worker count until either `RedisPubSubImpl` is changed to multiplex many channels over one PubSub listener or Redis pool sizing and Delivery config are deliberately raised with load evidence. Document this limit and the over-limit rejection behavior in Task 13.
- Capacity validation must use the actual DAL pool size. If `RedisPubSubImpl` is configured with `max_connections=50`, Delivery must validate against 50; if the container configures `max_connections=120`, Delivery may validate a higher room limit only when the same 120 is passed into the DAL client.
- Preserve the current RedisBroker resilience semantics even though the channel model changes: track desired room/global channels, retry failed subscribe/listen loops with `config.redis_reconnect_delay`/`config.redis_reconnect_max_delay` (or equivalent DeliveryConfig fields), and re-subscribe after reconnect until `unsubscribe_room()` or `stop()` removes the desired channel.
- Start global cancellation and internal subscription tasks in `start()`.
- `start()` must call `refresh_health()` once when Redis Pub/Sub is configured and set `is_connected=False` if ping fails. Do not mark the bus healthy merely because a client object exists.
- `start()` must wait for the initial global cancellation/internal subscription readiness signals described above before it can report healthy. If initial subscribe/listen setup fails, `start()` may return degraded with retry tasks running only after setting readiness false and health disconnected; it must not report connected while the global subscriptions are in backoff.
- Create room, cancellation, and internal subscription tasks with the injected `task_runner`/`traced_create_task`; do not use bare `asyncio.create_task()`.
- Cancel all subscription tasks in `stop()`, then call `RedisPubSub.close()` exactly once when Redis Pub/Sub is configured. `stop()` must be idempotent and must not close Pub/Sub before room unsubscriptions/desired-channel cleanup runs.
- Expose `is_connected` for health as `redis_reachable and subscriptions_ready`. With no Redis configured, return `False` and let `main.compute_health_status()` decide whether that is degraded based on `settings.redis_url`.
- No-Redis mode is first-class for single-worker deployments: the event bus must preserve local Delivery behavior by making cross-instance fan-out a no-op when `RedisPubSub` is `None`.
- Add a `publish_dead_letter()` method on the concrete bus only; it is not part of the Common protocol. EventPublisher can depend on the concrete `CrossInstanceEventBus` inside Delivery.
- Do not import `infrastructure.event_broker` or `infrastructure.brokers.redis_broker`.
- DAL failure contract: Phase 6 updates `RedisPubSubImpl.publish()` to propagate configured-driver failures as `TransientError`. Delivery relies on that behavior to dead-letter fan-out failures. If the DAL continues swallowing publish exceptions, the EventPublisher dead-letter tests are invalid and Phase 6 must not pass.
- DAL subscribe contract: Phase 6 does not make `RedisPubSubImpl.subscribe()` own reconnects. It must surface subscribe/listen failures so `CrossInstanceEventBus` can apply one consistent backoff/resubscribe policy for room, cancellation, internal, and dead-letter-adjacent channels.

- [ ] Run event-bus tests.

Run:

```bash
PYTHONPATH=. uv run pytest -q tests/test_delivery_event_bus.py tests/test_dal_protocols.py tests/test_dal_unit.py -k "pubsub or redis or event_bus"
```

Expected: pass.

- [ ] Commit.

```bash
git add delivery/event_bus delivery/types.py dal/redis/pubsub.py tests/test_delivery_event_bus.py tests/test_dal_protocols.py tests/test_dal_unit.py
git commit -m "feat(delivery): add cross-instance event bus"
```

### Task 7: Pure DomainEvent to SSE Translator

**Files:**
- Create: `delivery/translator.py`
- Modify: `common/dto/delivery.py` if optional `trace_id` or `RunEventNotification.correlation_id` is missing
- Test: `tests/test_delivery_translator.py`
- Test: `tests/test_common_foundation.py`

- [ ] Write failing translator tests for all 9 `DeliveryEvent` variants.

Use a fixed UTC timestamp and assert exact dicts for:
- `ProcessingStatusEvent`
- `RunEventNotification`
- `AgentMessagePartial`
- `AgentMessageFinal`
- `CancellationEvent`
- `HITLRequestEvent`
- `HITLResolvedEvent`
- `HubAgentEvent`
- `DebateRoundEvent`
- Common DTO/export tests cover the optional `trace_id` field on `DeliveryEventBase`/`DeliveryEnvelope` without breaking existing DTO construction that omits it.
- Common DTO/export tests cover optional `RunEventNotification.correlation_id`: omitting it remains valid at DTO construction, setting it preserves the exact value, and no existing required fields change.
- `RunEventNotification` translator tests assert the legacy `run_event` frame always includes the `correlation_id` key. When the DTO field is `None` or omitted, the frame value must be `None`, matching current legacy `send_processing_status()` behavior and preserving null-vs-missing wire shape.

Run:

```bash
PYTHONPATH=. uv run pytest -q tests/test_delivery_translator.py tests/test_common_foundation.py -k "delivery or dto or trace or correlation"
```

Expected: fail.

- [ ] Implement `to_sse_frame()`.

Implementation notes:
- Use `isinstance()` checks against concrete DTO classes.
- Do not call `utcnow()` inside the translator.
- Timestamp precedence is owned by the publisher: use `event.timestamp` when present, otherwise compute one `now()` value before translation and pass it in. The translator should receive the resolved timestamp and only format it into the frame.
- If typed events include `trace_id`, copy it to `frame["data"]["trace_id"]`; if no `trace_id` is present, omit `data["trace_id"]`. Redis envelopes use top-level `trace_id`, not nested `data.trace_id`, for cross-worker correlation.
- `RunEventNotification` includes optional `correlation_id: str | None = None`; always include `data["correlation_id"]` in the `run_event` SSE frame, even when the value is `None`, so the current legacy wire shape is preserved.
- Do not log, dedup, publish, or mutate event DTOs.
- Keep data keys compatible with current frontend expectations.

- [ ] Run translator tests.

Run:

```bash
PYTHONPATH=. uv run pytest -q tests/test_delivery_translator.py tests/test_common_foundation.py -k "delivery or dto or trace or correlation"
```

Expected: pass.

- [ ] Commit.

```bash
git add delivery/translator.py common/dto/delivery.py tests/test_delivery_translator.py tests/test_common_foundation.py
git commit -m "feat(delivery): add sse translator"
```

### Task 8: EventPublisher Implementation

**Files:**
- Create: `delivery/event_publisher.py`
- Modify: `delivery/sse/manager.py`
- Test: `tests/test_delivery_event_publisher.py`
- Test: `tests/test_delivery_protocols.py`

- [ ] Write failing publisher tests.

Cover:
- `emit(ProcessingStatusEvent(...processing...))` translates and delivers locally.
- `emit(ProcessingStatusEvent(...completed...))` calls terminal dedup and suppresses duplicate terminal sends.
- `emit()` publishes to event bus and locally delivers through the same frame.
- `emit()` does not call registered internal handlers, even when a handler is registered with a frontend-visible event type such as `"processing_status"`.
- `emit(RunEventNotification(...))` produces the same legacy `run_event` frame shape currently emitted from `send_processing_status()` when `run_event_sse_enabled()` is true, including the `correlation_id` key even when its value is `None`.
- If event bus publish fails, local delivery still happens and `emit()` does not raise.
- If local SSE delivery fails, the failure is warning-logged only, not dead-lettered, and `emit()` does not raise.
- If local SSE delivery fails for typed `emit()`, Redis fan-out still happens with the translated frame and no dead-letter is produced for the local failure. This pins the ordering/independence contract: local connection failure cannot suppress cross-worker delivery.
- If Redis fan-out fails, the failure is error-logged, published to `redis_dead_letter_channel`, retained in the in-memory fallback, and `emit()` does not raise.
- If `emit_internal()` Redis `publish_internal()` fan-out fails, the failure is error-logged, published to `redis_dead_letter_channel` or retained in the in-memory fallback if dead-letter publication fails, and `emit_internal()` does not raise.
- `handle_remote_internal_event(envelope)` reconstructs the DTO with `TypeAdapter(InternalEvent)`, verifies top-level `envelope["event_type"]` matches the reconstructed DTO's `event_type`, and drops/logs malformed or mismatched envelopes without scheduling handlers.
- `handle_remote_internal_event(envelope)` restores cross-worker trace context before scheduling handlers: with an incoming envelope containing `"trace_id": "trace-remote"`, a registered handler that calls `get_current_trace_id()` observes `"trace-remote"`. The publisher must restore the previous context after scheduling/execution so unrelated local work does not inherit the remote trace id.
- If translator fails, the event is published to the dead-letter channel/fallback and `emit()` does not raise.
- If dead-letter Redis publication fails, the error is logged once and the in-memory fallback still records the envelope.
- `trace_id_context("trace-123")` causes emitted typed SSE frames to include `frame["data"]["trace_id"] == "trace-123"` and Redis envelopes to include top-level `envelope["trace_id"] == "trace-123"` when the DTO/frame does not already set one.
- Explicit DTO/frame `trace_id` takes precedence over `trace_id_context()`.
- `_emit_legacy_frame()` uses the same local + fan-out path for legacy frames.
- If local SSE delivery fails for `_emit_legacy_frame()`, Redis fan-out still happens with the original legacy frame and no dead-letter is produced for the local failure.
- `_emit_legacy_frame()` dedups terminal legacy `processing_status` frames before local delivery/fan-out and preserves raw legacy payload shape.
- `_emit_legacy_frame()` increments `hybro_delivery_events_emitted_total` with `event_type=frame["type"]` for delivered legacy frames.
- `_emit_legacy_frame()` increments `hybro_delivery_events_deduplicated_total` with `event_type="processing_status"` when terminal legacy `processing_status` frames are suppressed by dedup.
- `emit_internal(MessageCommitted(...))` schedules same-worker handlers and publishes to Redis internal channel.
- `emit_internal(RunStateChanged(...))` schedules same-worker handlers and publishes to Redis internal channel.
- Registering two handlers for the same internal event type schedules both handlers exactly once on `emit_internal()` and on incoming remote internal events. This pins the multiple-handler contract from `register_internal_handler()`; do not silently replace the first handler.
- With `redis_pubsub=None`, `emit_internal()` still schedules same-worker handlers, skips Redis fan-out without raising or dead-lettering, and returns before handler completion.
- Incoming internal `HubAgentResponseInternal`, `MessageCommitted`, and `RunStateChanged` events schedule registered handlers.
- Handler exceptions are dead-lettered and never propagated.
- Handler exception dead-lettering is asynchronous and tracked: because task done callbacks cannot `await publish_dead_letter()` directly, the callback must schedule dead-letter publication through the injected `task_runner`/`traced_create_task`, add that dead-letter task to a tracked set, and tests must await the recorded dead-letter task to prove `publish_dead_letter()` completed. This must not introduce bare `asyncio.create_task()`.
- Handler tasks are tracked; `stop()` waits for pending handlers up to `handler_shutdown_timeout_seconds`, then cancels/logs unfinished handlers.
- A handler registered for `"processing_status"` is not scheduled by `emit(ProcessingStatusEvent(...))`; frontend-visible events and internal events remain separate entry points.
- `emit_internal()` does not await handler completion; a test handler that blocks on an `asyncio.Event` must not block `emit_internal()` from returning after scheduling. `emit()` does not schedule handlers.
- `emit_internal()` never calls SSE local delivery.
- `emit()` uses `event.timestamp` when present and otherwise uses the injected clock once.
- Typed SSE frames include trace ids at `frame["data"]["trace_id"]`; Redis envelopes include trace ids at top-level `envelope["trace_id"]`; legacy `_emit_legacy_frame()` delivery preserves the exact input frame and does not inject a trace id into the delivered frame.
- Metrics use the design names: increment `hybro_delivery_events_emitted_total` for emitted events and increment `hybro_delivery_events_deduplicated_total` for dedup suppressions. `hybro_delivery_sse_connections` is owned by transport tests in Task 3. Failure/dead-letter metrics may be added only as documented extensions.
- With `redis_pubsub=None`, `emit()` still delivers to local SSE connections, skips cross-instance fan-out without raising or dead-lettering, and leaves `hybro_delivery_events_emitted_total` behavior unchanged.
- Production references to `_emit_legacy_frame` are limited to the definition in `delivery/event_publisher.py` and the concrete compatibility wrapper in `delivery/facade.py`; `services/sse_services.py` uses only `facade.compat.emit_legacy_frame()`.
- Add `tests/test_delivery_protocols.py::test_legacy_frame_private_helper_call_sites` in this task. It must AST-scan production files and prove the private `_emit_legacy_frame()` method is called only from `delivery/facade.py`; the legacy adapter may call only `facade.compat.emit_legacy_frame()`.

Run:

```bash
PYTHONPATH=. uv run pytest -q tests/test_delivery_event_publisher.py
```

Expected: fail.

- [ ] Implement `EventPublisherImpl`.

Implementation notes:
- Use `pydantic.TypeAdapter(InternalEvent)` to reconstruct incoming internal events from Redis payloads.
- Keep incoming internal event deserialization in `EventPublisherImpl`; `CrossInstanceEventBus` passes raw envelopes and must not construct `InternalEvent` DTOs.
- For same-worker handlers, support both sync and async callables by checking awaitability.
- Resolve the design-doc ambiguity in favor of the N8 split: `emit()` performs only frontend-visible delivery and Redis fan-out; `emit_internal()` is the only entry point that dispatches registered internal handlers. Do not schedule internal handlers from `emit()`.
- `emit_internal()` schedules internal handlers and fans out cross-worker, but skips SSE delivery.
- Handler tasks should be named with event type and a short event id when available, for example `delivery-handler-processing_status`.
- Track handler tasks in `self._handler_tasks`; attach a done callback that removes the task and handles exceptions without awaiting inside the callback.
- Handler task done callbacks must schedule async dead-letter publication through the injected `task_runner` with a task name such as `delivery-dead-letter-handler-<event_type>`, track those tasks in `self._dead_letter_tasks`, and remove them when complete. Do not call bare `asyncio.create_task()` or silently fall back to synchronous-only in-memory dead-lettering for handler failures.
- `stop()` must stop accepting/scheduling new handler work, then drain handler tasks and tracked dead-letter tasks with the configured timeout. After timeout, cancel remaining handler/dead-letter tasks and log `event_type`/task name. It must not stop Redis subscriptions or close the event bus; `DeliveryFacade.stop()` owns bus shutdown.
- Use Redis dead-letter publication as the primary path and bounded fallback storage as the secondary path: `deque(maxlen=config.dead_letter_memory_maxlen)`.
- Custom publisher config tests: with `DeliveryConfig(handler_shutdown_timeout_seconds=0.05)`, `stop()` uses that timeout when draining handler/dead-letter tasks; with `DeliveryConfig(dead_letter_memory_maxlen=2)`, repeated fallback dead-letter writes retain only the last two envelopes. No test should pass if publisher timeout or deque length is hard-coded.
- Dead-letter envelopes should include `origin`, `failure_stage`, `event_type`, `trace_id` if available, serialized event/frame payload, exception class, exception message, and timestamp.
- `emit()` should call dedup before translation for terminal `ProcessingStatusEvent`.
- `_emit_legacy_frame()` should call the same dedup path only for legacy `processing_status` frames; it should not try to parse legacy frames into `ProcessingStatusEvent`.
- Use `common.observability.MetricsCollector` with `NoopMetricsCollector` default. Metrics failures are swallowed after debug logging. Do not introduce the older `delivery.emit.*` metric names.

- [ ] Run publisher tests plus earlier units.

Run:

```bash
PYTHONPATH=. uv run pytest -q tests/test_delivery_event_publisher.py tests/test_delivery_translator.py tests/test_delivery_deduplication.py tests/test_delivery_event_bus.py tests/test_delivery_protocols.py::test_legacy_frame_private_helper_call_sites
```

Expected: pass.

- [ ] Commit.

```bash
git add delivery/event_publisher.py delivery/sse/manager.py tests/test_delivery_event_publisher.py tests/test_delivery_protocols.py
git commit -m "feat(delivery): add event publisher"
```

### Task 9: DeliveryFacade Wiring

**Files:**
- Create: `delivery/facade.py`
- Modify: `delivery/__init__.py`
- Test: `tests/test_delivery_protocols.py`

- [ ] Write or extend failing facade tests.

Cover:
- `DeliveryFacade`, `EventPublisherImpl`, and `SSETransportImpl` are exported through `delivery.__all__ == ["DeliveryFacade", "EventPublisherImpl", "SSETransportImpl"]`.
- This final export assertion belongs in Task 9 only. Task 2's minimal `delivery/__init__.py` must not try to import these classes before they exist; Task 9 updates `delivery/__init__.py` after `delivery/facade.py`, `delivery/event_publisher.py`, and `delivery/sse/manager.py` are implemented.
- `EventPublisherImpl` is an `EventPublisher`.
- `SSETransportImpl` is an `SSETransport`.
- `DeliveryFacade.event_publisher` is `EventPublisher`.
- `DeliveryFacade.sse_transport` is `SSETransport`.
- `DeliveryFacade.compat` exposes adapter-only concrete compatibility methods and is not part of `DeliveryDeps`.
- Adapter-only methods are reachable through `facade.compat`, not through the Common protocol-typed `event_publisher`/`sse_transport` attributes.
- A single `instance_id` is shared between publisher, bus, and transport.
- `DeliveryFacade` passes that shared `instance_id` into `SSETransportImpl` so transport-owned connection metrics use the same `worker_id` label as publisher/bus envelopes.
- `DeliveryFacade.instance_id` exposes that shared id for app-shell consumers such as leader election; the legacy adapter may also expose read-only `_instance_id`/`instance_id` compatibility if `main.py` has not been fully updated yet.
- `DeliveryFacade.refresh_health()` awaits `RedisKV.ping()` when `redis_kv` is configured, updates a cached `_delivery_kv_connected` boolean, calls `CrossInstanceEventBus.refresh_health()` for Pub/Sub, and never infers Redis health from object construction. If `redis_kv` or `redis_pubsub` is `None`, the corresponding cached value is `False`; app-shell degraded/unsafe decisions depend on whether Redis is expected.
- `DeliveryFacade.delivery_kv_connected` and `DeliveryFacade.delivery_pubsub_connected` are synchronous properties returning the last values verified by `refresh_health()` or `start()`, so `services/sse_services.py` and `compute_health_status()` can read them without calling async DAL methods directly.
- `DeliveryFacade.start()` calls `await refresh_health()` before multi-worker safety can pass; invalid Redis URLs and KV/PubSub ping failures must produce `delivery_kv_connected=False` or `delivery_pubsub_connected=False`.
- `DeliveryFacade.start()` receives an explicit startup policy with `redis_expected`, `multi_worker`, and `allow_degraded_change_stream`; tests cover all fatal/degraded combinations rather than inferring behavior from globals.
- Facade wires `CrossInstanceEventBus` callbacks after constructing publisher, transport, and cancellation watcher; no component reaches around through globals to find another component.
- `DeliveryFacade.start()` is the only app-shell Delivery startup API. It starts components in one owned order: first start the cancellation watcher and wait for the watch readiness handshake, then start the event bus/global subscriptions and health ping, then call `EventPublisher.start()` as a component-level protocol hook. App-shell code must not call `event_publisher.start()` or `sse_transport.start_cancellation_watcher()` directly.
- `DeliveryFacade.stop()` is the only app-shell Delivery shutdown API. It stops components in one owned order and is idempotent: stop publisher handler tasks first through `EventPublisher.stop()`, then call `SSETransportImpl.close_all_connections()` while the event bus is still available for room unsubscriptions, then stop event bus subscriptions/Redis resources, then stop the cancellation watcher. `EventPublisher.stop()` must not also stop the bus, avoiding duplicate ownership.
- `DeliveryFacade.stop()` must close Delivery-owned Redis clients idempotently. Event bus stop closes `RedisPubSub.close()` after room unsubscriptions and subscription-task cancellation; facade stop closes `RedisKV.close()` once after transport/event-bus shutdown. Tests must prove repeated `stop()` calls do not double-close either client.
- `DeliveryFacade.start()` must be transactional for fatal failures. If any fatal component startup step fails after an earlier component started, it must call `stop()` on already-started components in reverse order, clear internal started flags, leave cached health reflecting the failure, and re-raise. A second `start()` attempt in the same process must be able to succeed after the failed attempt.
- Initial change-stream setup failure is fatal unless `startup_policy.allow_degraded_change_stream=True` and both `startup_policy.multi_worker=False` and `startup_policy.redis_expected=False`. In the allowed degraded case, `DeliveryFacade.start()` continues, sets `change_stream_connected=False`, logs a warning, starts the event bus/publisher according to the rest of the policy, and health remains degraded. Tests must prove the invalid policy combination `allow_degraded_change_stream=True` with either `multi_worker=True` or `redis_expected=True` fails before component startup.
- `set_draining()` delegates to transport.

Run:

```bash
PYTHONPATH=. uv run pytest -q tests/test_delivery_protocols.py
```

Expected: fail for facade tests.

- [ ] Implement `DeliveryFacade`.

Implementation notes:
- Constructor should accept already-created dependencies, not create Redis or Mongo clients.
- Provide a `create()` classmethod only if it remains protocol-based and does not import concrete application shell modules.
- Store the explicit `DeliveryStartupPolicy` provided by the container. Do not inspect `common.config.settings`, environment variables, process count, or Redis client construction inside Delivery to decide fatal vs degraded startup behavior.
- Own the shared `task_runner` dependency and pass it to `EventPublisherImpl`, `SSETransportImpl`, `CrossInstanceEventBus`, and `CancellationWatcher`. Default it to exported `common.observability.traced_create_task` implemented in `common/observability/tracing.py`.
- Import `TaskRunner` and callback types from `delivery.types`.
- Wire bus callbacks explicitly: remote SSE frames go to transport local broadcast, remote cancellations go to the cancellation watcher local handler, and remote internal envelopes go to the publisher internal-event deserializer/dispatcher.
- Pass the shared injected clock into `CrossInstanceEventBus` so legacy-envelope fallback timestamps, publisher timestamps, transport heartbeats, and cancellation watcher behavior are all deterministic in tests.
- Expose explicit health properties used by the app shell and adapter: `delivery_pubsub_connected`, `delivery_kv_connected`, and `change_stream_connected`. Keep `broker_connected` and `redis_connected` only as C3 aliases for legacy call sites.
- Keep Delivery Redis health cached and explicitly refreshed: `delivery_kv_connected` and `delivery_pubsub_connected` are not allowed to call `ping()` synchronously, and they are not allowed to return true merely because Redis client objects exist. `refresh_health()` is the async refresh point used by startup and `/health`.
- Expose `compat: DeliveryCompatibility` for the C3 adapter. `compat` may wrap private concrete methods such as `EventPublisherImpl._emit_legacy_frame()` and `SSETransportImpl.open_connection()`, but those concrete calls stay inside `delivery/facade.py`.
- Tests must assert the exact `DeliveryFacade.start()` and `DeliveryFacade.stop()` order so the design sketch cannot be implemented by starting/stopping only `event_publisher` from `main.py`.
- Tests must assert `DeliveryFacade.stop()` closes all active SSE connections, clears transport maps, unsubscribes rooms, records connection gauge `0`, then stops bus/watcher in order.
- Tests must assert `DeliveryFacade.stop()` closes Delivery-owned Redis Pub/Sub and KV clients once, in order after room unsubscriptions and before final shutdown completes.
- Tests must assert partial-start rollback: watcher starts then bus start fails, or bus starts then publisher start fails; expected behavior is reverse-order cleanup including `close_all_connections()` when needed, no leaked connection/subscription/watcher tasks, facade not marked started, and a later start can succeed.

- [ ] Run facade tests.

Run:

```bash
PYTHONPATH=. uv run pytest -q tests/test_delivery_protocols.py
```

Expected: pass.

- [ ] Commit.

```bash
git add delivery/facade.py delivery/__init__.py tests/test_delivery_protocols.py
git commit -m "feat(delivery): wire delivery facade"
```

### Task 10: C3 Legacy SSEManager Adapter

Task 10 is not a standalone mergeable checkpoint unless startup wiring has already been updated. The fail-fast adapter would break current `main.py` if `start_event_broker(...)`, `start_redis_service(...)`, or `start_change_stream_watcher(...)` still run before facade binding. Implement Task 10 and Task 11 in one integration window, and do not commit/merge the fail-fast adapter until Task 11's `main.py` binding order is in place. If an implementation insists on a separate Task 10 commit, the legacy lifecycle methods used by current `main.py` must remain temporary pre-bind no-ops with explicit tests, and Task 11 must remove that temporary compatibility by binding before those calls.

**Files:**
- Modify: `services/sse_services.py`
- Test: `tests/test_sse_adapter_delivery.py`
- Update: `tests/test_service_sse.py`
- Update: `tests/test_sse_event_broker.py`
- Update: `tests/test_phase7a_processing_status_golden.py`

- [ ] Write failing adapter tests.

Cover:
- Every public method raises `RuntimeError("SSEManager.bind_facade() not called - startup incomplete")` before binding, except harmless introspection if needed.
- `bind_facade(delivery_facade)` enables delegation.
- `unbind_facade()` clears the bound facade and returns the adapter to the same fail-fast state as before startup.
- After a successful bind followed by `unbind_facade()`, calls such as `add_connection()`, `broadcast_to_room()`, and lifecycle methods fail fast instead of delegating to a stopped facade.
- A second lifespan/startup can bind a new facade after unbind; calls then delegate to the new facade, not the stopped old facade.
- `add_connection()` delegates to `facade.compat.open_connection()`.
- `remove_connection()` delegates to `facade.compat.remove_connection()`.
- `broadcast_to_room()` builds a legacy frame and delegates to `facade.compat.emit_legacy_frame()`.
- `send_processing_status()` does not call `run_command_handler.record_processing_status()`.
- `send_processing_status()` does not call `run_event_sse_enabled()` or emit `run_event`; Phase 7a callers already preserve that branch with legacy `sse_manager.broadcast_to_room(..., "run_event", ...)` from the `record_processing_status()` return payload when that feature flag is enabled.
- `send_processing_status()` applies terminal dedup through Delivery while preserving legacy raw payload shape.
- `send_processing_status()` accepts legacy statuses outside `ProcessingStatusEvent.status`, including `rejected`, `rate_limited`, `error`, and `awaiting_input`, without DTO validation.
- `send_processing_status()` preserves string `details` in the SSE payload; it must not coerce legacy `details` to a dict.
- `send_agent_response()`, `send_artifact_update()`, `send_task_submitted()`, `send_task_update()`, `send_error()`, `send_rate_limit_error()`, and `send_user_message()` delegate through `facade.compat.emit_legacy_frame()`.
- Exact golden-frame tests assert the legacy SSE frame shape for `send_agent_response()`, `send_artifact_update()`, `send_task_submitted()`, `send_task_update()`, `send_error()`, `send_rate_limit_error()`, `send_user_message()`, `broadcast_to_room()`, and legacy `send_processing_status()`. These tests must compare complete frame dicts, including `type`, `data`, timestamp/client request fields when present, and status/details wire values.
- `cancel_message_and_broadcast()`, `cancel_message()`, `check_cancelled()`, `is_cancelled()`, `clear_cancellation()`, `create_token()`, `get_token()`, and `remove_token()` delegate through `facade.compat`.
- `get_room_status()` delegates through `facade.compat.get_room_status()` and preserves the legacy dict shape.
- Legacy lifecycle methods used by `main.py` delegate or fail fast: `start_event_broker()`, `stop_event_broker()`, `start_redis_service()`, `stop_redis_service()`, `start_change_stream_watcher()`, `stop_change_stream_watcher()`, `set_draining()`, `broker_connected`, `redis_connected`, and `change_stream_connected`.
- `start_event_broker(_event_broker)` and `start_redis_service(_redis_service)` accept the current legacy positional arguments and ignore them safely after bind, or `main.py` is updated in the same task to call those methods without arguments. Tests must cover whichever path is implemented.
- `sse_manager._instance_id` compatibility either returns `delivery_facade.instance_id` or `main.py` is updated to pass `delivery_facade.instance_id` directly into `LeaderElection`.
- Health properties delegate to facade and reflect actual Redis ping/connectivity status, not merely object construction.
- Phase 7a golden ordering tests must survive the C3 adapter. This is the only Phase 6-allowed update to a Phase 7a artifact: update `tests/test_phase7a_processing_status_golden.py` so any direct `SSEManager()` instance calls `bind_facade(fake_or_real_delivery_facade)` before `add_connection()` or other public methods. The fake/real facade must capture `broadcast_to_room(..., "run_event", ...)` and processing-status frames so the test still proves `record_processing_status()` -> legacy `run_event` broadcast -> `processing_status` order after Phase 6.
- Startup-order safety test: if `main.py` still calls any legacy SSE lifecycle method before `sse_manager.bind_facade(...)`, Task 10 must not be committed with fail-fast lifecycle behavior. Prefer updating startup binding in Task 11 before committing the adapter.

Run:

```bash
PYTHONPATH=. uv run pytest -q tests/test_sse_adapter_delivery.py
```

Expected: fail.

- [ ] Convert `services/sse_services.py` into an adapter.

Implementation notes:
- Keep the module-level `sse_manager = SSEManager()`.
- Add `SSEManager.unbind_facade()` for app shutdown. It must clear only the adapter binding and compatibility aliases; it must not try to stop Delivery itself because `DeliveryFacade.stop()` owns shutdown.
- Preserve the `services.sse_services.SSEConnection` import contract explicitly without importing concrete Delivery. Either provide a small local compatibility wrapper whose constructor accepts `SSEConnection(room_id="...")` and whose `send_message(message_type, data)` method emits the legacy JSON shape, or retarget all legacy tests to `delivery.sse.connection.SSEConnection(room_id=..., connection_id=..., heartbeat_interval=..., now=...)` and stop claiming constructor/method compatibility. Do not directly re-export the new Delivery class under the old name from `services/sse_services.py`.
- Keep `_enum_value()` if needed for legacy payload values.
- Keep `_resolve_client_request_id()` only in this adapter if tests require current DB fallback. Prefer passing the supplied `client_request_id` directly and avoid DB fallback when callers already provide it.
- Keep legacy `send_processing_status()` on the raw-frame compatibility path. Do not construct `ProcessingStatusEvent` from legacy calls because current legacy statuses and `details` values exceed the DTO schema.
- For legacy `processing_status`, the adapter builds the exact old frame and calls `facade.compat.emit_legacy_frame()`; dedup happens inside Delivery's `_emit_legacy_frame()` compatibility path, not in the adapter.
- Remove imports of `run_command_handler` and `run_event_sse_enabled`.
- Do not replicate the legacy `run_event_sse_enabled()` branch in the adapter. If a test needs the old `run_event` SSE, it belongs in the Phase 7a caller tests that verify the legacy `broadcast_to_room(..., "run_event", ...)` emission from the returned lifecycle payload. Phase 7b owns migrating that legacy broadcast to `RunEventNotification`.
- Do not import concrete Delivery from business modules or adapters. Only `container.py` may import concrete `delivery.*`. `services/sse_services.py` is the C3 migration shell but must still avoid concrete Delivery imports/annotations; use structural typing, `typing.Protocol`, or `Any` for the bound facade shape.
- Keep legacy method names and signatures stable.
- Rule for legacy SSE event broker: Phase 6 stops constructing the legacy `RedisBroker` for SSE fan-out. `main.py` must not create or pass an SSE `RedisBroker` into `sse_manager.start_event_broker()` except as a deliberately temporary no-op compatibility call covered by tests; no legacy broker object may be retained or started for SSE after `DeliveryFacade` is bound.
- Keep `infrastructure.redis_service.create_redis_service()` construction only for leader election and relay streams until those are migrated. This is separate from SSE fan-out and does not justify constructing the legacy SSE event broker.
- Keep public lifecycle methods used by `main.py` even if their old dependencies are no longer used. `start_event_broker(broker=None)` and `start_redis_service(redis_service=None)` should delegate to the already-bound Delivery facade or be harmless compatibility no-ops that validate the facade is bound and update no fake health state. They must accept the current legacy arguments until `main.py` is updated not to pass them.
- `start_change_stream_watcher(db_collection)` delegates to `facade.compat.start_change_stream_watcher()` after the facade has already been built with the DAL collection; do not pass `db_collection` into Delivery from the adapter unless the facade exposes a safe compatibility method for it.
- `stop_change_stream_watcher()`, `stop_event_broker()`, and `stop_redis_service()` delegate to facade stop methods or compatibility no-ops that are covered by tests. Before `bind_facade()`, all public lifecycle methods must raise the same fail-fast `RuntimeError` except explicitly harmless read-only health properties.

- [ ] Update legacy SSE tests to bind a real or fake facade-like object.

Migration strategy:
- Move core behavior assertions from `tests/test_service_sse.py` to `tests/test_delivery_*`.
- Keep `tests/test_service_sse.py` focused on adapter compatibility.
- Move broker behavior assertions from `tests/test_sse_event_broker.py` to `tests/test_delivery_event_bus.py` and `tests/test_delivery_event_publisher.py`.
- Keep `tests/test_phase7a_processing_status_golden.py` as a Phase 7a behavior proof, not a legacy implementation test. Its setup must bind a fake or real facade-like object instead of relying on an unbound `SSEManager`, and the expected golden ordering/payload assertions must remain unchanged.
- Keep `tests/test_run_lifecycle_service.py` and `tests/test_stale_task_checker_run_lifecycle.py` in the adapter verification path; they protect the Phase 7a record -> legacy run_event -> processing_status ordering through the C3 adapter and stale-task lifecycle callers.

- [ ] Run adapter and migrated tests.

Run:

```bash
PYTHONPATH=. uv run pytest -q tests/test_sse_adapter_delivery.py tests/test_service_sse.py tests/test_sse_event_broker.py tests/test_api_sse.py tests/test_phase7a_processing_status_golden.py tests/test_run_lifecycle_service.py tests/test_stale_task_checker_run_lifecycle.py
```

Expected: pass.

- [ ] Do not commit Task 10 by itself.

Continue directly to Task 11 and commit the adapter plus startup wiring together after the combined startup tests pass. Task 10 has no standalone commit command because a fail-fast adapter without the Task 11 startup binding order can break `main.py`.

### Task 11: Container and Startup Wiring

**Files:**
- Modify: `container.py`
- Modify: `main.py`
- Test: `tests/test_delivery_protocols.py`
- Test: `tests/test_multi_worker_safety.py`
- Test: `tests/test_sse_event_broker.py`

- [ ] Write failing container wiring tests.

Cover:
- `create_delivery_facade(cancellation_collection=..., redis_kv=..., redis_pubsub=...)` returns the concrete facade object from `container.py`; `main.py` passes that object to the legacy C3 adapter without importing concrete `delivery.*`.
- Passing `cancellation_collection=None` to the facade factory is forbidden outside tests; production workers must have the cancelled-messages collection configured. Single-worker degraded local mode still requires a collection object, but its `watch()` setup may fail and be reported degraded as described below.
- `create_delivery_deps(facade)` returns `DeliveryDeps`.
- `DeliveryDeps.event_publisher` is an `EventPublisher`.
- `DeliveryDeps.sse_transport` is an `SSETransport`.
- `DeliveryDeps` has exactly the design-doc fields: `event_publisher` and `sse_transport`.
- `event_publisher` and `sse_transport` come from the same facade/shared state.
- No business deps are required to create Delivery.
- Container/app-shell code derives an explicit `DeliveryStartupPolicy(redis_expected=..., multi_worker=..., allow_degraded_change_stream=...)` and passes it to `create_delivery_facade()`. Tests must prove `allow_degraded_change_stream=True` is used only for single-worker/no-Redis startup and that multi-worker or Redis-expected startup uses fatal change-stream behavior.
- `tests/test_delivery_protocols.py::test_main_does_not_import_or_instantiate_concrete_dal` is added/enabled in this task after `main.py` is fixed. It fails if `main.py` imports or instantiates concrete DAL classes such as `MongoDALImpl`, `VectorDALImpl`, any `*DALImpl`, `RedisKVImpl`, or `RedisPubSubImpl`, and it also fails on direct cancellation collection extraction such as `mongo_dal.collection("cancelled_messages")`, `mongodb.cancelled_messages_collection`, or other direct Mongo collection access in `main.py`.
- Add container config extraction tests proving the full app `Settings` object is consumed only in `container.py` / container-owned helpers: `create_delivery_config(settings)` returns a `DeliveryConfig` with every runtime field mapped from the corresponding settings/env value (`heartbeat_interval_seconds`, `shutdown_drain_seconds`, `cancellation_ttl_seconds`, `terminal_dedup_ttl_seconds`, `cancellation_cache_maxsize`, `cancellation_token_cache_maxsize`, `terminal_dedup_cache_maxsize`, `redis_sse_channel_prefix`, `redis_cancel_channel`, `redis_internal_channel`, `redis_dead_letter_channel`, `redis_cancel_key_prefix`, `redis_terminal_key_prefix`, `dead_letter_memory_maxlen`, `handler_shutdown_timeout_seconds`, `redis_reconnect_delay`, `redis_reconnect_max_delay`, `redis_max_connections`, `redis_subscription_reserved_connections`, `redis_room_subscription_production_limit`, `cs_backoff_base`, `cs_backoff_max`, `cs_backoff_factor`, `cs_jitter_fraction`, and `terminal_processing_statuses`), and no `delivery/**` code imports, annotates, or receives the full `Settings` type. Include env/settings tests where terminal statuses are configured as deployment input, parsed by Common settings or the container helper into an iterable of strings, and normalized by `DeliveryConfig` to `frozenset({"done", "failed"})`; raw string, non-string, and blank-status cases must fail.
- Add a startup boundary test that `main.py` no longer imports or calls `create_event_broker()` and no longer references `RedisBroker`/`infrastructure.brokers` for SSE fan-out.

Run:

```bash
PYTHONPATH=. uv run pytest -q tests/test_delivery_protocols.py
```

Expected: fail for container wiring tests.

- [ ] Implement `DeliveryDeps`, `create_delivery_facade()`, and `create_delivery_deps()`.

Constructor outline:

```python
@dataclass(frozen=True)
class DeliveryDeps:
    event_publisher: EventPublisher
    sse_transport: SSETransport

def create_delivery_facade(
    *,
    cancellation_collection: MongoCollection,
    startup_policy: DeliveryStartupPolicy,
    redis_kv: RedisKV | None = None,
    redis_pubsub: RedisPubSub | None = None,
    config: DeliveryConfig | None = None,
    now: Callable[[], datetime] | None = None,
    id_factory: Callable[[], str] | None = None,
    instance_id: str | None = None,
    task_runner: TaskRunner | None = None,
    metrics: MetricsCollector | None = None,
) -> DeliveryFacade: ...

def create_delivery_deps(facade: DeliveryFacade) -> DeliveryDeps: ...
```

Keep `DeliveryDeps` aligned with `docs/MODULAR_DECOUPLING_DESIGN.md §6.2`: only `event_publisher` and `sse_transport`. The legacy adapter still needs the facade object; `main.py` should keep the local `delivery_facade` object returned by container helpers and pass it to `sse_manager.bind_facade(delivery_facade)` without importing concrete `delivery.*` or exposing the facade through `DeliveryDeps`.

Factory-shape note: the design doc's final container sketch uses `_create_delivery(dal)` with a future `DALContainer`. The current `container.py` pattern from Phases 3-5 passes individual protocols instead, so Phase 6 uses `cancellation_collection`, `redis_kv`, and `redis_pubsub` parameters. When `DALContainer` is introduced, this helper should collapse to the design-doc `_create_delivery(dal)` shape while still extracting concrete collections in the container/composition boundary.

Runtime-primitive rule: `create_delivery_facade()` must pass the same resolved `now`, `task_runner`, and `instance_id` to publisher, transport, event bus, and watcher. If `now` is omitted, use the Common UTC clock; if `id_factory` is omitted, use a UUID-based factory; if `instance_id` is omitted, generate one once in the facade factory and share it. Factory tests must inject fixed `now`, deterministic `id_factory`, and explicit `instance_id`, then assert the bus legacy-envelope timestamp, transport connection ids/metric labels, and publisher envelopes all use those injected values.

Dependency-shape rule: `create_delivery_facade()` must receive the specific cancellation `MongoCollection`, not the broad `MongoDAL`, to keep Delivery dependent on only the DAL protocols it actually uses. `container.py` or a container-owned helper owns `mongo_dal.collection("cancelled_messages")` or the existing collection name and passes that collection as `cancellation_collection`; `main.py` should not perform concrete Mongo DAL construction or direct collection extraction.

Startup-policy rule: `create_delivery_facade()` must receive an explicit `DeliveryStartupPolicy`; the container helper constructs it from app-shell inputs and passes it through. The factory signature intentionally has no `startup_policy=None` default. Tests should use an explicit fatal fixture (`redis_expected=True`, `multi_worker=True`, `allow_degraded_change_stream=False`) when they do not exercise degraded startup. Do not let Delivery infer this policy from global settings.

Import `DeliveryStartupPolicy` from `delivery.config` wherever the facade/container tests need the concrete policy type. Do not duplicate the policy type in `delivery.types`, `delivery.facade`, or `container.py`.

- [ ] Wire `main.py`.

Startup sequence:
- Obtain `mongo_dal` and the cancellation collection through `container.py` / container-owned helpers. Phase 6 must move concrete Mongo construction and collection extraction behind the same composition boundary as Redis construction; `main.py` must not instantiate `MongoDALImpl` or call concrete Mongo collection APIs directly after this wiring task. The boundary test must reject direct `.collection(...)` extraction in `main.py`, not only concrete class imports.
- Move current `main.py` concrete vector DAL construction behind container-owned helpers in the same wiring task. The broad final DAL scan intentionally rejects `from dal...` imports, so `VectorDALImpl` / Pinecone DAL creation must also leave `main.py`, not only Delivery-specific Mongo/Redis construction.
- Create one `delivery_config = container.create_delivery_config(settings)` (or equivalent container-owned helper) before constructing the facade. This helper extracts only Delivery-owned fields from the full app settings object and returns `DeliveryConfig(...)`; Delivery itself must not receive or type against the full app `Settings` object. This is where deployment env/settings fields for every runtime Delivery knob are read, including heartbeat interval, TTLs, cache maxsizes, terminal statuses, room-subscription limit, reserved Pub/Sub connections, and Redis pool size; do not instantiate bare `DeliveryConfig()` in production startup if that would ignore deploy-time settings.
- Create one `delivery_startup_policy` from app-shell state before constructing the facade. `redis_expected` comes from whether Redis URL/config is expected for this deployment, `multi_worker` comes from the existing worker/multi-process guard, and `allow_degraded_change_stream` is `True` only for explicit single-worker/no-Redis local mode. Pass this policy to `create_delivery_facade(startup_policy=delivery_startup_policy)`.
- Pass the container-provided `cancellation_collection` to `create_delivery_facade(cancellation_collection=...)`.
- Add a startup assertion that `cancellation_collection` is not `None` before creating Delivery. Missing cancellation collection is fatal in every production mode because every worker must monitor cancelled messages. Degraded startup applies only when a valid collection object exists but initial `MongoCollection.watch()` setup fails under the explicit single-worker/no-Redis `DeliveryStartupPolicy`.
- Create Delivery immediately after DAL and before Agent/Room/Context & Memory.
- Construct `RedisKVImpl` and `RedisPubSubImpl` for Delivery inside `container.py` (or a container-owned helper), not directly in `main.py`. `main.py` may pass `settings.redis_url`/config into the container helper but should not import concrete `dal.redis.*` classes.
- Pass the same Redis Pub/Sub pool size into both Delivery config validation and `RedisPubSubImpl(max_connections=...)`. Do not let `RedisPubSubImpl` read a hidden global value that differs from `delivery_config.redis_max_connections`. Add a container test where `settings.redis_max_connections=120` and `redis_room_subscription_production_limit=100` is accepted only when the constructed `RedisPubSubImpl` receives `max_connections=120`; with `max_connections=50`, the same room limit is rejected.
- Add a container helper test for empty Redis URL wiring: when Redis is disabled, Delivery receives `redis_kv=None` and `redis_pubsub=None`, not `RedisKVImpl(url="")` or `RedisPubSubImpl(url="")`. This preserves single-worker no-Redis mode and prevents disabled Redis `setnx()` returning `False` from suppressing terminal statuses as duplicates.
- Before declaring multi-worker delivery healthy, validate Redis KV and Pub/Sub with `ping()`. `DeliveryFacade.start()` and `/health` must call `await delivery_facade.refresh_health()` so `delivery_kv_connected` is the last verified `RedisKV.ping()` result, not constructed-client state. An invalid Redis URL must make KV/PubSub health false and fail/degrade multi-worker safety according to existing `settings.redis_url` expectations.
- Call `await delivery_facade.start()` as the single Delivery startup API before binding the legacy adapter. Do not call `delivery.event_publisher.start()` or `delivery.sse_transport.start_cancellation_watcher()` directly from `main.py`.
- `DeliveryFacade.start()` must preserve the design startup order internally: cancellation watcher readiness first, then event bus/global subscriptions and health ping, then `EventPublisher.start()` as the component-level protocol hook.
- Bind `sse_manager.bind_facade(delivery_facade)` only after `DeliveryFacade.start()` succeeds, then store the same facade on `app.state.delivery_facade`. If `DeliveryFacade.start()` fails, do not bind the adapter, do not set `app.state.delivery_facade`, call/await facade rollback cleanup, and leave `sse_manager` in fail-fast unbound state so a later startup attempt in the same process is not poisoned.
- `/health` must retrieve the facade from `request.app.state.delivery_facade` (or the app object used by the route) and call `await delivery_facade.refresh_health()` before computing the response. Do not rely on a startup-local `delivery_facade` variable, because the route cannot access it after lifespan setup. If the facade is absent because startup failed or has not completed, health should report degraded/unready rather than healthy.
- Use `delivery_facade.instance_id` for `LeaderElection(..., instance_id=...)`; do not keep reaching into `sse_manager._instance_id` unless the adapter explicitly exposes it as a compatibility alias to the same facade id.
- Do not construct the legacy SSE `RedisBroker` for Delivery/SSE fan-out. If a legacy `start_event_broker()` call remains temporarily for startup-order compatibility, it must be a no-op against the bound facade and must not receive or retain an `infrastructure.brokers.RedisBroker` instance.
- Keep `infrastructure.redis_service.create_redis_service()` for leader election and relay streams until those are migrated.
- During normal shutdown, call `sse_manager.set_draining(True)`, sleep the same local `delivery_config.shutdown_drain_seconds`, then call `await delivery_facade.stop()` as the single Delivery shutdown API before closing non-Delivery Redis clients. `DeliveryFacade.stop()` must close all active SSE connections and unsubscribe rooms through the transport before stopping the event bus. Do not read a second drain value from global settings unless this plan is explicitly amended.
- During shutdown, call `sse_manager.unbind_facade()` and clear `app.state.delivery_facade` (or set it to `None`) in a `finally` block after attempting `DeliveryFacade.stop()`, so post-shutdown calls fail fast even if Delivery cleanup raises partway through. Add tests for stop failure during shutdown, post-shutdown fail-fast, and clean rebind on a second startup. Prefer making `DeliveryFacade.stop()` swallow/log cleanup failures and complete best-effort shutdown, but the app shell must still unbind/clear in `finally`.
- During startup failure cleanup, avoid poisoning draining/shutdown flags. `DeliveryFacade.start()` owns partial-start rollback; `main.py` should not set draining just to clean up a failed startup. Tests must cover a failed startup followed by a successful startup in the same process, proving `sse_manager` can bind after retry and `app.state.delivery_facade` is updated only on success.

- [ ] Update multi-worker safety tests.

Expected behavior remains:
- Gunicorn requires Delivery Pub/Sub health from `delivery_facade.delivery_pubsub_connected`, Delivery KV health from `delivery_facade.delivery_kv_connected`, legacy RedisService/streams health for leader election and relay streams until those are migrated, and `delivery_facade.change_stream_connected=True` whenever cancellation watching is expected. `sse_manager.broker_connected` and `sse_manager.redis_connected` may remain C3 aliases only.
- Single-process mode can run with Redis disabled.
- Update `check_multi_worker_safety()` signature to receive explicit booleans: `delivery_pubsub_connected`, `delivery_kv_connected`, `redis_service_connected`, `relay_streams_connected`, and `change_stream_connected`. Do not keep the generic `broker_connected` name once both legacy and Delivery Redis paths exist.
- Update `compute_health_status()` signature and `/health` response fields to expose `delivery_pubsub_connected`, `delivery_kv_connected`, `legacy_redis_service_connected`, `relay_streams_available`, `change_stream_connected`, and `redis_expected`.
- Preserve existing `/health` fields unconditionally as deprecated aliases for zero backend breaking changes: `broker_connected` maps to `delivery_pubsub_connected`, `broker_expected` maps to `redis_expected`, and `redis_service_connected` maps to `legacy_redis_service_connected`.
- Health endpoint reports degraded when Redis URL is configured but required Delivery Pub/Sub, Delivery KV, or legacy RedisService/streams health is disconnected.
- Invalid Redis URL with `settings.redis_url` configured reports KV/PubSub disconnected and fails/degrades the multi-worker safety checks; a constructed Redis client object alone is not healthy.
- Add tests where `RedisKV.ping()` returns true, false, and raises. Expected: `refresh_health()` caches true only for true; false/exception both cache `delivery_kv_connected=False`, log without raising from `/health`, and make multi-worker safety fail/degrade when Redis is expected.
- Add matching Pub/Sub refresh tests where `RedisPubSub.ping()` returns true, false, and raises through `CrossInstanceEventBus.refresh_health()` / `DeliveryFacade.refresh_health()`. Expected: ping false/exception cache false and make multi-worker safety fail/degrade when Redis is expected.
- Add Pub/Sub readiness tests where ping returns true but global or desired room subscription loops are in reconnect/backoff. Expected: `delivery_pubsub_connected=False` until the required loops are active/listening; once ping is true and all required subscriptions are ready, `delivery_pubsub_connected=True`.
- If the cancellation collection is missing (`None`), startup is fatal outside explicit tests for every deployment mode. If a valid collection object exists but `MongoCollection.watch()` setup fails at startup, behavior is controlled only by `DeliveryStartupPolicy`: fatal when `allow_degraded_change_stream=False`; degraded when `allow_degraded_change_stream=True`, `multi_worker=False`, and `redis_expected=False`. In degraded startup, `change_stream_connected=False` must be exposed in health, local in-process cancellation must still work, and a warning must be logged.
- If the change stream later disconnects after startup, `/health` reports degraded with `change_stream_connected=False`; the watcher continues reconnecting with backoff and does not require leader election.
- Single-worker empty-Redis wiring test passes with Delivery KV/PubSub set to `None` and no multi-worker safety failure.
- Add an explicit multi-worker test where Delivery Pub/Sub, Delivery KV, legacy RedisService, and relay streams are healthy but `change_stream_connected=False`; expected result is unsafe/degraded, not pass.
- Add an explicit single-worker/no-Redis test with `DeliveryStartupPolicy(redis_expected=False, multi_worker=False, allow_degraded_change_stream=True)` where initial `MongoCollection.watch()` setup fails: expected result is Delivery startup completes degraded, `change_stream_connected=False`, `/health` is degraded/unready for watcher status but single-worker multi-worker safety does not fail, and `mark_cancelled()` / `is_cancelled()` still work locally.
- Add invalid-policy tests where `allow_degraded_change_stream=True` with `multi_worker=True` or `redis_expected=True` fails before starting watcher/bus/publisher.

- [ ] Run wiring tests.

Run:

```bash
PYTHONPATH=. uv run pytest -q tests/test_sse_adapter_delivery.py tests/test_service_sse.py tests/test_sse_event_broker.py tests/test_api_sse.py tests/test_phase7a_processing_status_golden.py tests/test_run_lifecycle_service.py tests/test_stale_task_checker_run_lifecycle.py tests/test_delivery_protocols.py tests/test_multi_worker_safety.py
```

Expected: pass.

- [ ] Commit.

```bash
git add services/sse_services.py container.py main.py tests/test_sse_adapter_delivery.py tests/test_service_sse.py tests/test_sse_event_broker.py tests/test_api_sse.py tests/test_phase7a_processing_status_golden.py tests/test_run_lifecycle_service.py tests/test_stale_task_checker_run_lifecycle.py tests/test_delivery_protocols.py tests/test_multi_worker_safety.py
git commit -m "refactor(delivery): wire legacy sse adapter to delivery startup"
```

### Task 12: Internal Event Dispatch Readiness

**Files:**
- Modify: `delivery/event_publisher.py`
- Test: `tests/test_delivery_event_publisher.py`

- [ ] Add explicit tests for `HubAgentResponseInternal`, `MessageCommitted`, and `RunStateChanged`.

Cover:
- Register handler for `"hub_agent_response_internal"` and emit `HubAgentResponseInternal`; the scheduled handler receives the DTO instance when the test awaits the recorded handler task.
- Register handler for `"message_committed"` and emit `MessageCommitted`; the scheduled handler receives the DTO instance when the test awaits the recorded handler task.
- Register handler for `"run_state_changed"` and emit `RunStateChanged`; the scheduled handler receives the DTO instance when the test awaits the recorded handler task.
- Register two handlers for the same event type, for example `"message_committed"`, and verify both are scheduled exactly once for local `emit_internal()` and incoming Redis internal envelopes.
- Incoming Redis internal envelopes for `HubAgentResponseInternal`, `MessageCommitted`, and `RunStateChanged` reconstruct DTOs and schedule handlers.
- No subscriber being registered is a no-op with a debug log, not an error.

Run:

```bash
PYTHONPATH=. uv run pytest -q tests/test_delivery_event_publisher.py -k "internal or HubAgentResponseInternal or MessageCommitted or RunStateChanged"
```

Expected: pass after implementation from Task 8; if not, fix before continuing.

- [ ] Confirm container can register handlers later without Delivery changes.

No Phase 7b handlers are registered yet except optional no-op tests. Do not import Context & Memory or Execution from Delivery.

- [ ] Commit if changes were needed.

```bash
git add delivery tests/test_delivery_event_publisher.py
git commit -m "test(delivery): verify internal event dispatch readiness"
```

### Task 13: Documentation and Design Drift Updates

**Files:**
- Modify: `docs/MODULAR_DECOUPLING_DESIGN.md`
- Modify: `docs/BEHAVIORAL_DECISIONS.md` if needed
- Reference: this plan

- [ ] Update design docs with actual Phase 6 decisions.

Document:
- Delivery uses Common `RedisKV` and `RedisPubSub` protocols instead of importing `infrastructure.brokers`.
- `services/sse_services.py` is a C3 adapter bound during startup.
- Temporary `_emit_legacy_frame()` exists only for legacy SSE methods not represented in `DeliveryEvent`; the adapter reaches it only through `DeliveryFacade.compat.emit_legacy_frame()`.
- Legacy `send_processing_status()` uses the raw-frame compatibility path, preserves legacy statuses/details, and relies on the `DeliveryFacade.compat` path for terminal dedup.
- Delivery does not resolve `client_request_id` from DB.
- Delivery does not call `record_processing_status()`.
- Phase 7a callers preserve the old `run_event_sse_enabled()` behavior using the legacy `sse_manager.broadcast_to_room(..., "run_event", ...)` path from the `record_processing_status()` return payload before emitting processing status; Delivery and `RunEventNotification` take over in Phase 7b.
- Phase 7a has already made `RunLifecycleService.record_processing_status()` / the lifecycle port return `dict | None` so callers have the run-event payload needed for that preservation; direct `RunCommandHandler` use is not required for normal callers.
- Cancellation watcher remains per-worker.
- Internal handler dispatch is fire-and-forget for emitters, but handler tasks are tracked and drained/cancelled during `EventPublisher.stop()`.
- Delivery-owned background work uses `common.observability.tracing.traced_create_task(coro, *, name=None)` through an injected task runner.
- `DeliveryFacade.start()` / `DeliveryFacade.stop()` are the single app-shell lifecycle APIs. The app shell must not partially start Delivery by calling `event_publisher.start()` directly; the facade owns cancellation watcher readiness, event-bus subscription lifecycle, publisher handler lifecycle, and ordered shutdown.
- `DeliveryFacade.stop()` closes/drains active SSE connections through `SSETransportImpl.close_all_connections()` after the shutdown drain window and before event-bus shutdown, so room unsubscriptions still work and connection gauges end at zero.
- Required design update: replace the Phase 6 `traced_create_task()` OpenTelemetry span-link sketch with the implemented contextvars/task-name behavior, and document this as a Phase 6 deviation with a future enhancement path. This docs update must be merged before Phase 6 is accepted.
- `common.observability.get_current_trace_id()` and `trace_id_context()` exist; Phase 6 only preserves explicit DTO/frame trace ids or values returned by that helper. It does not synthesize ids.
- Update the full design DTO code block to match current Common DTO defaults and Phase 6 additions, not just the new fields. This includes `DeliveryEnvelope.timestamp: datetime | None = None`, `DeliveryEventBase.timestamp: datetime | None = None`, optional `trace_id` on the base/envelope where implemented, `RunEventNotification.payload: dict = Field(default_factory=dict)`, and `RunEventNotification.correlation_id: str | None = None`.
- Correct the design EventPublisher sample that dispatches internal handlers from `emit()`: Phase 6 implements the N8 split, so `emit()` is frontend-visible delivery + fan-out only and `emit_internal()` is the internal handler dispatch entry point.
- Document exact trace-id wire placement: typed SSE frames use `frame["data"]["trace_id"]`, Redis envelopes use top-level `envelope["trace_id"]`, and legacy `_emit_legacy_frame()` preserves the delivered frame without injecting `trace_id`.
- Document exact `RunEventNotification` wire compatibility: `run_event` frames always include the `correlation_id` key, with value `None` when no correlation id is available.
- Redis DAL driver failures propagate as `TransientError` when Redis is configured for all Redis DAL data operations touched by Phase 6: `RedisKV.get()`, `RedisKV.set()`, `RedisKV.delete()`, `RedisKV.increment()`, `RedisKV.setnx()`, `RedisKV.exists()`, `RedisPubSub.publish()`, `RedisPubSub.subscribe()` listen/setup failures, `RedisStreams.xadd()`, and `RedisStreams.xread()`. `ping()` remains a health boolean and `close()` remains best-effort cleanup.
- Redis DAL compatibility note: empty Redis URL remains graceful and returns disabled/no-op behavior as before; configured-driver failures raise/surface for the listed methods. Update affected non-Delivery callers and existing DAL tests to expect this split behavior.
- `MongoCollection.watch()` protocol now models Motor's async-context-manager change stream shape, and the watcher uses `async with`.
- `SSETransport.connect()` protocol is `def connect(...) -> AsyncIterator[dict]`, not `async def`, so the intended call shape is `async for frame in transport.connect(...): ...`.
- Required design-invariant update: document `SSETransport.connect()` as the narrow async-iterator-factory exception to the "cross-module methods are async" invariant. All other Common Delivery protocol methods remain async where they perform async work; `connect()` is synchronous only to return an `AsyncIterator` directly for `async for` call sites.
- Dead-lettering publishes to a Redis dead-letter channel with structured logs; the in-memory deque is fallback/test aid, not the primary sink.
- Delivery frames/envelopes propagate `trace_id` when available and emit design-aligned metrics: `hybro_delivery_sse_connections`, `hybro_delivery_events_emitted_total`, and `hybro_delivery_events_deduplicated_total`.
- Redis Pub/Sub subscription reconnect behavior remains explicit in Delivery: desired room/global channels are retried with backoff after subscribe/listen failures, `CrossInstanceEventBus.start()` waits for initial global subscription readiness, and Delivery Pub/Sub health requires both Redis reachability and subscription readiness.
- Redis Pub/Sub scaling decision: Phase 6 intentionally uses one room-subscription task per active room and supports/tests up to `redis_room_subscription_production_limit=40` active SSE rooms per worker with the current default Redis pool of 50. The 41st distinct active room is rejected before local connection admission in Redis-configured deployments, while additional connections to already-subscribed rooms remain allowed. This is documented as capacity admission behavior, not a `/health` connectivity field. Deployments needing more must either raise Redis pool/config together with load evidence or implement a multiplexed Redis Pub/Sub DAL before raising that limit.
- Main/startup no longer constructs or starts the legacy SSE `RedisBroker` for SSE fan-out; legacy RedisService construction remains only for leader election and relay streams.
- App-shell health and multi-worker safety fields distinguish Delivery Pub/Sub, Delivery KV, legacy RedisService, relay streams, and cancellation change-stream connectivity; generic `broker_connected` is not reused for multiple meanings.
- Delivery Redis health is explicitly refreshed cached state: `DeliveryFacade.refresh_health()` awaits `RedisKV.ping()` and `CrossInstanceEventBus.refresh_health()` / `RedisPubSub.ping()`, sync properties expose the last verified results, and constructed clients are never treated as healthy without successful pings.
- Delivery-owned Redis clients are closed by Delivery shutdown: Pub/Sub is closed by the event bus after subscriptions/room unsubscriptions are stopped, and KV is closed by the facade after event bus shutdown; both closes are idempotent.
- Startup failure handling is transactional: partial Delivery starts roll back already-started components, the legacy `sse_manager` is bound only after facade start succeeds, and `app.state.delivery_facade` is set only after successful startup so `/health` has a stable access path.
- Change-stream startup policy is explicit: fatal in multi-worker or Redis-expected deployments, and degraded only for `DeliveryStartupPolicy(redis_expected=False, multi_worker=False, allow_degraded_change_stream=True)`.
- Delivery config is pure and app-shell-resolved: `delivery/config.py` does not import global settings or accept the full app `Settings` object; a container-owned helper maps every runtime `DeliveryConfig` field from Delivery-owned settings/env fields into the resolved config.
- Concrete Mongo and Redis construction for Delivery are owned by `container.py` / container-owned helpers. `main.py` should receive/use protocol objects and extracted collections from those helpers rather than instantiating `MongoDALImpl`, `RedisKVImpl`, or `RedisPubSubImpl` directly.
- Business modules must not import concrete `delivery.*`; cross-module access remains through Common protocols injected by the application shell.

- [ ] Run docs-adjacent import tests.

Run:

```bash
PYTHONPATH=. uv run pytest -q tests/test_delivery_protocols.py tests/test_common_foundation.py
```

Expected: pass.

- [ ] Commit.

```bash
git add docs/MODULAR_DECOUPLING_DESIGN.md docs/BEHAVIORAL_DECISIONS.md
git commit -m "docs(delivery): record phase 6 delivery extraction"
```

### Task 14: Full Verification Gate

**Files:**
- All touched files.

- [ ] Run Delivery-specific suite.

Run:

```bash
PYTHONPATH=. uv run pytest -q tests/test_phase7a_processing_status_gate.py tests/test_phase7a_processing_status_golden.py tests/test_run_lifecycle_service.py tests/test_stale_task_checker_run_lifecycle.py tests/test_delivery_protocols.py tests/test_delivery_translator.py tests/test_delivery_sse_connection.py tests/test_delivery_sse_manager.py tests/test_delivery_deduplication.py tests/test_delivery_cancellation.py tests/test_delivery_event_bus.py tests/test_delivery_event_publisher.py tests/test_sse_adapter_delivery.py
```

Expected: pass.

- [ ] Run legacy SSE/API safety suite.

Run:

```bash
PYTHONPATH=. uv run pytest -q tests/test_service_sse.py tests/test_sse_event_broker.py tests/test_api_sse.py tests/test_service_hitl.py tests/test_service_task_notification.py tests/test_agent_response_handler.py tests/test_multi_worker_safety.py
```

Expected: pass.

- [ ] Run modular boundary suites.

Run:

```bash
PYTHONPATH=. uv run pytest -q tests/test_common_foundation.py tests/test_dal_protocols.py tests/test_dal_unit.py tests/test_agent_protocols.py tests/test_room_protocols.py tests/test_context_memory_protocols.py tests/test_delivery_protocols.py
```

Expected: pass.

- [ ] Run Redis health/failure contract tests.

Run:

```bash
PYTHONPATH=. uv run pytest -q tests/test_dal_protocols.py tests/test_dal_unit.py tests/test_multi_worker_safety.py -k "redis or pubsub or streams or get or set or delete or increment or setnx or exists or xadd or xread or health"
```

Expected: pass. Invalid Redis URLs must make KV/PubSub health false, and configured Redis driver failures must propagate as `TransientError` from the DAL before Delivery catches them.

- [ ] Run the full suite.

Run:

```bash
PYTHONPATH=. uv run pytest -q
```

Expected: pass. This is mandatory for Phase 6 because the plan touches Common DTO/protocols, DAL Redis/Mongo contracts, `main.py`, and the legacy SSE singleton. If unrelated existing failures appear, capture exact failing test names and verify all Delivery/Common/DAL/startup suites are green before requesting guidance.

- [ ] Final import scan.

Run:

```bash
rg -n "^(from (a2a_adapter|agent|api|config|container|context_memory|dal|database|execution|hub_runtime_bridge|infrastructure|jobs|llm_gateway|main|models|modules|platform_module|room|services)\\b|import (a2a_adapter|agent|api|config|container|context_memory|dal|database|execution|hub_runtime_bridge|infrastructure|jobs|llm_gateway|main|models|modules|platform_module|room|services)\\b)" delivery
```

Expected: no output. This anchored scan is only a coarse root-import check; the AST boundary test is authoritative and must additionally reject settings singleton access under `delivery/**`, including `from common.config import settings`, `from common.config.settings import settings`, `from config.settings import settings`, `import common.config` / `import common.config.settings` followed by settings access, and dynamic imports of `common.config` / `common.config.settings` used to obtain settings.

- [ ] Final business-module Delivery import scan.

Run:

```bash
rg -n "^(from delivery\\b|import delivery\\b)" main.py a2a_adapter agent common config context_memory dal database execution hub_runtime_bridge infrastructure jobs llm_gateway models modules platform_module room services api
PYTHONPATH=. uv run pytest -q tests/test_delivery_protocols.py::test_business_modules_do_not_import_delivery_concretes
```

Expected: no `rg` output and the AST test passes. `container.py` is the only Phase 6 file allowed to import concrete `delivery.*`; `main.py`, `services/sse_services.py`, and all other production roots, including Common, DAL, config, database, models, infrastructure, Jobs, A2A, and LLM Gateway, must use Common protocols/DTOs or structural/local adapter typing. The AST test is authoritative because it also rejects dynamic concrete Delivery imports.

- [ ] Final app-shell DAL ownership scan.

Run:

```bash
rg -n "\\b[A-Za-z_][A-Za-z0-9_]*DALImpl\\b|RedisKVImpl|RedisPubSubImpl|^(from dal\\.|import dal\\.)|\\.collection\\(|cancelled_messages_collection|create_event_broker|RedisBroker|infrastructure\\.brokers" main.py
PYTHONPATH=. uv run pytest -q tests/test_delivery_protocols.py::test_main_does_not_import_or_instantiate_concrete_dal
```

Expected: no `rg` output and the AST test passes. Concrete DAL construction, including `MongoDALImpl`, `VectorDALImpl`, and any other `*DALImpl`, cancellation collection extraction, and legacy SSE broker construction belong outside `main.py` after Delivery wiring.

- [ ] Final dynamic import scan.

Run:

```bash
rg -n "import_module\\(|__import__\\(" delivery
```

Expected: no output unless the AST boundary test explicitly proves the call cannot reference a forbidden root. Prefer no dynamic imports in Delivery.

- [ ] Final legacy-frame seam scan.

Run:

```bash
PYTHONPATH=. uv run pytest -q tests/test_delivery_protocols.py::test_legacy_frame_private_helper_call_sites
rg -n "\\._emit_legacy_frame\\(" delivery services --glob "*.py" --glob "!tests/**"
```

Expected: AST call-site test passes, and the regex reports only the concrete wrapper call in `delivery/facade.py`. This avoids false failures from comments/docstrings while still proving `services/sse_services.py` references `emit_legacy_frame` on `facade.compat`, not the private `_emit_legacy_frame`.

- [ ] Final background-task tracing scan.

Run:

```bash
rg -n "asyncio\\.create_task\\(" delivery
PYTHONPATH=. uv run pytest -q tests/test_delivery_protocols.py::test_delivery_background_tasks_use_traced_task_runner
```

Expected: no `rg` output and the AST test passes. The AST test must reject direct and aliased task creation in `delivery/**`, including `asyncio.create_task(...)`, `from asyncio import create_task`, bare `create_task(...)`, and `loop.create_task(...)`. Delivery background tasks must be created through injected `task_runner`/`traced_create_task`, not bare task APIs.

- [ ] Final A1 scan.

Run:

```bash
rg -n "record_processing_status|run_command_handler|run_event_sse_enabled" delivery services/sse_services.py
rg -n "\\.send_processing_status\\(" modules services api jobs
PYTHONPATH=. uv run pytest -q tests/test_phase7a_processing_status_gate.py
```

Expected: first command has no output. The second command is a human cross-check for call sites and intentionally matches method calls, not function definitions; comments/definitions are ignored. The AST Phase 7a gate is authoritative and must pass, proving every remaining production `send_processing_status()` call site is either record-before-send or explicitly transport-only. Phase 6 must not introduce new unrecorded callers.

- [ ] Commit final fixes if needed.

```bash
git status --short
git diff --name-only
# Review the status/diff and stage only Phase 6 files intentionally changed by this implementation.
# Do not use broad directory staging in a dirty worktree; exclude unrelated user edits and unrelated untracked files.
git add <exact Phase 6 file paths reviewed from git status/diff>
git commit -m "test(delivery): complete phase 6 verification"
```

## Implementation Gate Checklist

- [ ] `delivery/` implements `EventPublisher` and `SSETransport`.
- [ ] Existing SSE connection, broadcast, heartbeat, room status, cancellation, terminal dedup, and cross-instance fan-out behavior works through Delivery.
- [ ] Broadcast cleanup of the last dead/inactive connection removes room state, reverse maps, unsubscribes the room, and records SSE connection gauge `0`.
- [ ] `DeliveryFacade.stop()` closes all active SSE connections, clears transport state, unsubscribes rooms before event-bus shutdown, and records SSE connection gauge `0`.
- [ ] `delivery/**` has zero forbidden business imports.
- [ ] `services/sse_services.py` is a fail-fast C3 adapter with `bind_facade()` and `unbind_facade()`; post-shutdown calls fail fast and a second startup cleanly rebinds a new facade.
- [ ] `send_processing_status()` no longer calls `record_processing_status()`.
- [ ] Phase 7a prerequisite `tests/test_phase7a_processing_status_gate.py` proves every remaining production `send_processing_status()` call site is either record-before-send or explicitly transport-only.
- [ ] Phase 7a caller tests prove `record_processing_status()` return payloads emit legacy `run_event` frames via `sse_manager.broadcast_to_room()` before processing-status delivery when run-event SSE is enabled.
- [ ] `tests/test_phase7a_processing_status_golden.py` binds a fake or real Delivery facade to the C3 adapter and still proves run-event-before-processing-status ordering after Phase 6.
- [ ] Phase 7a prerequisite has made `RunLifecycleService.record_processing_status()` or the chosen lifecycle port return `dict | None` payloads from the lower-level writer.
- [ ] Legacy `send_processing_status()` preserves raw legacy statuses and string `details` through `_emit_legacy_frame()` while terminal dedup still works.
- [ ] `_emit_legacy_frame()` dedups legacy `processing_status` frames and bypasses dedup for other raw legacy frames.
- [ ] Exact golden-frame tests preserve all legacy SSE wire formats listed in Task 10.
- [ ] All legacy SSE lifecycle methods used by `main.py` fail fast before bind and delegate/no-op safely after bind.
- [ ] `emit()` never raises; local SSE failures are warning-only and do not prevent Redis fan-out, while translator and fan-out failures are published to the dead-letter topic plus fallback deque. Internal handler failures are dead-lettered from the `emit_internal()` path.
- [ ] `_emit_legacy_frame()` also keeps local SSE failure warning-only and still performs Redis fan-out with the original legacy frame.
- [ ] `emit()` performs local delivery and Redis fan-out only; it never dispatches registered internal handlers for frontend-visible events.
- [ ] `emit_internal()` schedules in-process handlers and Redis fan-out, but never reaches SSE clients or waits for subscriber completion.
- [ ] Handler tasks and async handler-failure dead-letter tasks are scheduled through injected `task_runner`, tracked, and drained/cancelled during `EventPublisher.stop()`.
- [ ] `HubAgentResponseInternal`, `MessageCommitted`, and `RunStateChanged` handler paths work.
- [ ] Terminal status dedup uses L1 `TTLCache` and L2 Redis `SET NX`, with L1 `maxsize` and `ttl` sourced from `DeliveryConfig`.
- [ ] TTL cache maxsizes default to `10_000` for terminal dedup, cancellation ids, and cancellation tokens.
- [ ] Redis Pub/Sub publish/listen failures, Redis KV data-operation failures, and Redis Streams `xadd()`/`xread()` configured-driver failures propagate as `TransientError` or surface to the Delivery bus; Delivery catches and handles Delivery-owned failures at the boundary.
- [ ] Empty Redis URL remains graceful, and affected non-Delivery Redis callers/tests are updated for configured-driver `TransientError` behavior.
- [ ] Redis health uses `ping()` and invalid Redis URLs report disconnected before multi-worker safety passes; Delivery KV and Pub/Sub health are cached only from `DeliveryFacade.refresh_health()` / startup ping, not inferred from object construction, and Pub/Sub health additionally requires subscription readiness.
- [ ] Delivery startup failure rollback is transactional: partial watcher/bus/publisher starts are stopped in reverse order, adapter binding and `app.state.delivery_facade` happen only after successful start, and retrying startup in the same process works.
- [ ] Delivery shutdown closes Delivery-owned Redis Pub/Sub and KV clients exactly once, idempotently, after room unsubscriptions and subscription-task cleanup.
- [ ] Redis Pub/Sub subscription loops retry with backoff and re-subscribe to desired room/global channels after subscribe/listen failures.
- [ ] `CrossInstanceEventBus.start()` includes a global subscription readiness handshake, and Pub/Sub health remains false while global or desired room subscription loops are in reconnect/backoff even if Redis ping succeeds.
- [ ] Redis Pub/Sub per-room subscription scaling is bounded by tests covering the default 40 active rooms per worker, unsafe 100-with-50-pool config rejection, and 41st-room rejection before local connection admission; docs state the `redis_room_subscription_production_limit=40` deployment limit until Redis pool sizing is raised or multiplexed Pub/Sub is implemented.
- [ ] Cancellation watcher runs per worker, not under leader election.
- [ ] Cancellation id cache and cancellation token cache use `TTLCache` with `ttl=config.cancellation_ttl_seconds` and default 3600-second cleanup behavior.
- [ ] `MongoCollection.watch()` protocol and `dal/mongo/client.py` adapter implementation use async-context-manager change stream cleanup.
- [ ] Production Delivery startup fails if the cancellation `MongoCollection` is missing; initial `MongoCollection.watch()` setup failure is fatal for multi-worker/Redis-expected deployments and degraded-only for explicit `DeliveryStartupPolicy(redis_expected=False, multi_worker=False, allow_degraded_change_stream=True)` local mode.
- [ ] Multi-worker safety fails/degrades when `change_stream_connected=False`, even if Redis Pub/Sub, Redis KV, legacy RedisService, and relay streams are healthy.
- [ ] Draining mode rejects new SSE connections.
- [ ] App shell uses `DeliveryFacade.start()` and `DeliveryFacade.stop()` as the only Delivery lifecycle API; direct `event_publisher.start()` / `event_publisher.stop()` and direct `sse_transport.start_cancellation_watcher()` calls remain internal to the facade.
- [ ] Graceful shutdown uses the `delivery_config.shutdown_drain_seconds` instance owned by `main.py`.
- [ ] `delivery/config.py` has pure defaults and no global settings imports; the import-boundary tests reject `from common.config import settings`, `from common.config.settings import settings`, top-level `config.settings`, settings access through `import common.config`, and dynamic settings imports under `delivery/**`.
- [ ] A container-owned helper, not `delivery/config.py`, maps every runtime `DeliveryConfig` field, including `redis_internal_channel`, `redis_dead_letter_channel`, TTLs, cache maxsizes, handler/dead-letter limits, and `terminal_processing_statuses`, from Delivery-owned settings/env fields before facade construction; `delivery/**` never receives or annotates the full app `Settings` object.
- [ ] Custom-config usage tests prove `redis_sse_channel_prefix`, `redis_cancel_channel`, `redis_internal_channel`, `redis_dead_letter_channel`, `redis_cancel_key_prefix`, and `redis_terminal_key_prefix` are consumed by event bus, publisher dead-lettering, cancellation watcher, and terminal dedup instead of hard-coded defaults.
- [ ] Runtime config-fidelity tests cover custom `heartbeat_interval_seconds`, cancellation/terminal TTLs, Redis reconnect delays, change-stream backoff fields, handler shutdown timeout, dead-letter fallback maxlen, cache maxsizes, and `terminal_processing_statuses`.
- [ ] `DeliveryConfig` validates `redis_room_subscription_production_limit + redis_subscription_reserved_connections <= redis_max_connections` in Task 2; Task 11 proves the same `redis_max_connections` value is passed into `RedisPubSubImpl(max_connections=...)`.
- [ ] `DeliveryConfig` rejects invalid env/settings values for non-capacity fields: non-positive intervals, TTLs, cache sizes, reconnect/backoff values, `cs_backoff_factor < 1.0`, invalid jitter bounds, empty Redis channels/prefixes, raw-string/non-string/blank terminal statuses, and empty terminal-status sets; valid terminal statuses normalize to `frozenset[str]`.
- [ ] `DeliveryStartupPolicy` lives in `delivery.config`, has no defaults for `redis_expected` or `multi_worker`, imports cleanly in Task 2, and rejects invalid degraded-policy combinations before facade startup. Production container wiring passes an explicit policy.
- [ ] `common.protocols.SSETransport.connect` is updated to `def connect(...) -> AsyncIterator[dict]`, and protocol tests prove `async for frame in transport.connect(...): ...` works without awaiting `connect()` first.
- [ ] `docs/MODULAR_DECOUPLING_DESIGN.md` documents `SSETransport.connect()` as the narrow async-iterator-factory exception to the "cross-module methods are async" invariant.
- [ ] Delivery-owned background tasks use `common.observability.tracing.traced_create_task()`/injected `task_runner`; no bare `asyncio.create_task()` appears in `delivery/**`.
- [ ] Delivery frames/envelopes preserve existing timestamps when provided, otherwise use one injected-clock timestamp.
- [ ] Trace id propagation uses explicit DTO/frame `trace_id` or `get_current_trace_id()` with exact wire placement (`frame["data"]["trace_id"]` for typed SSE, top-level `envelope["trace_id"]` for Redis envelopes, no injected trace id for delivered legacy frames), and delivery metrics use the design metric names.
- [ ] Heartbeat frame shape and default 30-second cadence are preserved, while custom `config.heartbeat_interval_seconds` is honored.
- [ ] Container creates Delivery from DAL-only dependencies before business modules.
- [ ] Business modules, `main.py`, and `services/sse_services.py` do not import concrete `delivery.*`; only `container.py` does during Phase 6.
- [ ] `jobs/` is included in both Delivery forbidden-import boundaries and reverse concrete-Delivery import scans.
- [ ] Phase 7a prerequisite scans and the final A1 scan include `jobs/` production callers.
- [ ] Phase 7a delivery-extraction handoff risks are covered by the Task 1b manifest-driven audit, exact pytest-node collection, Phase 7a golden tests, and focused RoomMessageCenter/QueueExecutor/API SSE tests before Delivery extraction starts; no required business side effect remains after a terminal/frontend emit unless explicitly best-effort.
- [ ] Task 1b is verify-only and STOP-only: if the Phase 7a manifest fixture is stale, exact proof nodes are missing, executable gates fail, or a concrete terminal/frontend-visible manifest entry lacks focused test coverage, Phase 6 stops and the owning Phase 7a/handoff work lands first.
- [ ] Reverse concrete-Delivery import checks cover all production roots and app-shell files outside the single explicit allowlist: `container.py`.
- [ ] Reverse concrete-Delivery import checks include `config/`, `database/`, and `models/`.
- [ ] `main.py` concrete DAL ownership checks reject `VectorDALImpl` and any other `*DALImpl`, not only Mongo/Redis examples.
- [ ] Redis Pub/Sub capacity validation uses the actual `RedisPubSubImpl` pool size passed by the container; Delivery config cannot validate against a different value than the DAL client uses.
- [ ] Delivery background-task boundary tests reject aliased `create_task` and `loop.create_task` calls, not only literal `asyncio.create_task(` text.
- [ ] `docs/MODULAR_DECOUPLING_DESIGN.md` is updated before merge for the Phase 6 `traced_create_task()` no-OpenTelemetry deviation.
- [ ] `DeliveryFacade.instance_id` replaces `sse_manager._instance_id` for leader election, or the adapter compatibility property returns the same facade id.
- [ ] Health and multi-worker safety use explicit Delivery Pub/Sub, Delivery KV, legacy RedisService, and relay-stream fields.
- [ ] `create_delivery_facade()` receives a specific cancellation `MongoCollection`, not broad `MongoDAL`.
- [ ] `RunEventNotification` translator always includes the `correlation_id` key, using `None` when absent, preserving the legacy `run_event` SSE frame shape.
- [ ] `common.dto.RunEventNotification` includes optional `correlation_id`.
- [ ] `main.py` does not instantiate concrete Mongo or Redis DAL implementations for Delivery; container helpers own concrete DAL construction.
- [ ] `TaskRunner` and bus callback types live in `delivery/types.py`.
- [ ] Cross-instance bus callbacks are wired explicitly by `DeliveryFacade`; no component relies on globals or ad hoc circular references.
- [ ] All Delivery-specific, legacy SSE, and import-boundary tests pass.
- [ ] Final verification includes `tests/test_run_lifecycle_service.py` and `tests/test_stale_task_checker_run_lifecycle.py` to protect record -> run_event -> processing_status behavior through the C3 adapter.

## Review Notes

This plan intentionally adds one temporary private non-protocol helper, `EventPublisherImpl._emit_legacy_frame()`, because the existing legacy `sse_manager` API sends several SSE frame types that are not represented in the current `DeliveryEvent` union. This is a C3 compatibility seam, not a new module API. The implementation must restrict private helper calls to `delivery/facade.py`, expose them to the C3 adapter only through `DeliveryFacade.compat`, and remove the helper in Phase 7b after callers emit typed `DeliveryEvent` DTOs.

The same compatibility seam handles legacy `processing_status` payloads. Phase 6 deliberately keeps the Common `ProcessingStatusEvent` schema narrow and design-aligned; widening the DTO belongs in a separate Common-contract change if Phase 7b finds typed callers still need `rejected`, `rate_limited`, `error`, `awaiting_input`, or string `details`.

`AgentRegistered` and `RoomCreated` are exported by `common.dto.internal_events` but are not currently members of the `InternalEvent` discriminated union. Phase 6 should deserialize only the union members that exist today: `MessageCommitted`, `RunStateChanged`, and `HubAgentResponseInternal`. If Common adds those extra event types to the union later, update the publisher's `TypeAdapter(InternalEvent)` tests at the same time.

Task 12 is intentionally a verification gate rather than a separate implementation task. It repeats the key `HubAgentResponseInternal`, `MessageCommitted`, and `RunStateChanged` paths after the publisher and bus are integrated so the later Hub/Execution wiring has a clear readiness check.

The Redis DAL changes in this plan are required, not optional. Current `RedisPubSubImpl.publish()`, `RedisKVImpl` data-operation methods, and `RedisStreamsImpl.xadd()`/`xread()` behavior hide driver failures, which would make Delivery dead-letter, cancellation, dedup, and Redis health guarantees untestable. Phase 6 must make those configured-driver failures raise `TransientError` or surface subscription/listen failures to the Delivery bus; this plan does not weaken the design contract.

The shutdown model resolves the fire-and-forget ambiguity by separating emitter latency from lifecycle ownership. `emit_internal()` schedules subscriber handlers and returns without awaiting subscribers, while `EventPublisherImpl` owns those tasks and drains or cancels them in `stop()`. `emit()` does not schedule subscriber handlers.

Before implementation, run an execution-neutral architecture review of this plan against `docs/MODULAR_DECOUPLING_DESIGN.md`, `BEHAVIORAL_DECISIONS.md`, and the current codebase. Treat any blocking mismatch as a plan update before writing production code.
