# Phase 8 HubRuntimeBridge Implementation Plan

> **For agentic workers:** REQUIRED: Use `superpowers:subagent-driven-development` if subagents are available, or `superpowers:executing-plans` to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract hub relay, liveness, publish intake, agent sync, and hub task ownership into a `hub_runtime_bridge/` module that exposes `HubManagement`, async `HubLivenessReader`, and a Hub dispatch adapter without changing the relay API contract or frontend-visible behavior.

**Architecture:** Phase 8 starts from `main` after Delivery and Execution have landed. The new `HubFacade` owns hub connection state, Redis Streams / in-memory relay transport, offline queue behavior, publish normalization, durable hub response journaling, and heartbeat monitoring. Hub uses `AgentRegistryWriter` for hub-agent sync/offline transitions, a full Room publish-authorization protocol for hub/room/message/agent checks, a cancellation reader for cancelled publish drops, and an app-shell-bound `HubInternalResponseDispatcher` protocol for local owned orchestration resume. Hub never imports or calls concrete Execution code directly; the app shell wires a Hub-owned ownership guard to Execution's `HubAgentResponseSink`. `EventPublisher.emit_internal(HubAgentResponseInternal(...))` remains the cross-worker fanout path.

**Tech Stack:** Python 3.11+, FastAPI, pytest, pytest-asyncio, existing Mongo/Redis DAL protocols, Common DTOs/protocols, existing Delivery `EventPublisher`, existing Execution `HubAgentResponseSink` internal handler, AST import-boundary tests, no new dependencies.

---

## Scope

Include:
- Create branch `phase-8-hub-runtime-bridge` from current `main`.
- Create `hub_runtime_bridge/` and move hub-owned behavior out of `services/relay_service.py`, `api/relay.py`, `api/hub.py`, and `infrastructure/relay_streams.py` behind protocol-bound module code.
- Implement a `HubFacade` that conforms to `HubManagement` and async `HubLivenessReader`. Expose dispatch to Execution only through Common protocols such as `HubDispatchPort` and `HubDispatchPolicy`; the concrete `HubDispatchAdapter` is constructed in the app shell/container so the current sync `HubDispatchPort.is_hub_online(...)` collision cannot make one object satisfy incompatible liveness signatures. Long-term preferred direction is to make authoritative dispatch decisions async.
- Keep the existing public HTTP route URLs, authentication behavior, request models, response models, SSE hub stream frames, Redis stream payloads, offline queue semantics, heartbeat semantics, and hub status responses stable.
- Use `AgentRegistryWriter.sync_hub_agents(...)` and `mark_hub_agents_offline(...)` for all hub-agent writes; HubRuntimeBridge must not write `agents_collection` or instantiate Agent repositories directly.
- Use a full Room publish-authorization protocol for hub publish intake. Authorization must preserve current checks: hub owner equals room owner, `agent_message_id` belongs to the request room, and the message's agent belongs to the authenticated hub. `RoomOwnershipReader.verify_room_hub_ownership(...)` alone is not sufficient because current `RoomFacade` only proves that some room member has the hub id.
- Emit frontend-visible hub updates through `EventPublisher.emit(HubAgentEvent(...))` or a documented compatibility frame only where golden parity proves Hub owns that frontend frame. Default: do not emit Hub frontend events for publish event types whose current frontend/DB side effects are produced by Execution's `AgentResponseHandler`.
- Emit local owned orchestration resumes through the app-shell-bound `HubInternalResponseDispatcher` so current local publish ordering is preserved: same-request hub publish events are processed sequentially, and the route must not return 204 until locally owned handler work for those events has completed or been explicitly journaled for retry. Remote cross-worker delivery uses `EventPublisher.emit_internal(HubAgentResponseInternal(...))`.
- Add durable hub response journaling before internal emission, as described in `docs/MODULAR_DECOUPLING_DESIGN.md §4.5`. Use a sidecar `hub_response_journal` collection that references canonical `run_id`; do not insert non-lifecycle hub response records into the existing `run_events` `(run_id, seq)` lifecycle stream.
- Define hub response identity without changing the route schema: `task_id` is read from `HubPublishEvent.data.task_id` or the tracked agent message task data; `response_seq` is read from `data.response_seq` when supplied. New hub daemons should send `response_seq` and get durable idempotent identity `(hub_id, task_id, response_seq)`. Legacy payloads without `response_seq` or another stable upstream event id are accepted with a unique ingest `journal_id` per event and at-least-once/no-cross-request-dedupe semantics, preserving current behavior where repeated publishes are processed sequentially. A legacy compatibility fingerprint may include `(hub_id, room_id, agent_message_id, event_type, publish batch index, normalized payload hash)` for logging/correlation, but it is not a cross-request dedupe key.
- Add an owner-worker map for hub tasks dispatched by the current process and a durable `hub_task_ownership` lease record so duplicate or remote hub responses are ignored before Execution handles them.
- Track ownership aliases by `agent_message_id`, generated local pending task id, and hub-acknowledged `task_id` in both the local owner map and durable ownership lease. The first `task_submitted` publish must alias the hub task id to the existing owner before subsequent events are filtered. Recovery ownership can be claimed only after the durable owner lease expires.
- Add a durable replay lease/processed marker for journaled hub responses. Startup replay must atomically claim an unprocessed or expired journal event with `claim_owner`, `claim_token`, `claimed_at`, and `claim_expires_at`, then seed or bypass local owner-worker filtering for that claimed replay so a crashed owner worker does not cause permanent discard. Processed/dead-letter updates must match the same claim token.
- Preserve current cancellation behavior for hub publishes: if the agent message or its related root message is cancelled, publish intake must return without journaling, frontend emission, or internal emission.
- Preserve `RelayService`, `RelayHubLivenessReader`, `relay_service`, `init_relay_service(...)`, and `services.relay_service` / `infrastructure.relay_streams` import compatibility shims until Phase 9 cleanup.
- Add a legacy workflow cleanup readiness gate for `base_tasks`, `meta_tasks`, `task_sessions`, and `chat_contexts`. Current `main` still mounts legacy workflow/task routers, so Phase 8 must not drop those collections unless tests prove those routes and all live code references are decommissioned. If they are still active, the implementation must leave a blocked cleanup manifest instead of dropping data.
- Add tests and static gates proving HubRuntimeBridge boundaries, protocol conformance, route parity, liveness behavior, agent sync through protocol, response journaling/idempotency, owner-worker filtering, and legacy cleanup readiness.

Exclude:
- Do not change frontend event names, payload fields, route URLs, API-key authentication, Clerk-authenticated hub status behavior, or SSE formatting.
- Do not rewrite the A2A SDK adapter layer. Existing hub `AgentCard` validation may stay in a Hub-owned adapter, but new business logic must not spread `a2a.types` imports outside exact allowlisted adapter paths.
- Do not change Execution orchestration behavior except through the already-existing `HubAgentResponseInternal` internal event seam.
- Do not remove `services/relay_service.py`, `api/relay.py`, `api/hub.py`, or `infrastructure/relay_streams.py` in Phase 8; leave shims for Phase 9.
- Do not implement Platform extraction or API thin-adapter cleanup; Phase 9 owns that.
- Do not alter Agent, Room, Delivery, or Execution source except for this explicit allowlist: Agent-owned hub status reader and call-counter implementations, Room-owned publish authorization/lineage reader implementation, Execution dispatch adapter/middleware injection cleanup (`execution/dispatch/transports/relay.py`, `execution/dispatch/middleware/hub_transport.py`, `execution/dispatch/agent_message_processor.py`, `execution/orchestration/room_message_center.py`, `execution/orchestration/factory.py`), Common protocol/DTO surfaces, and app-shell/container wiring. Do not change orchestration semantics, HITL semantics, run lifecycle state transitions, Delivery transport internals, or unrelated Agent/Room behavior.

## Current Repo Check

As of 2026-05-18 on `main`:
- `delivery/` and `execution/` source packages exist and are registered in `pyproject.toml`.
- `container.py` exposes `DeliveryDeps` and `ExecutionDeps`; it does not yet expose `HubDeps`.
- `main.py` still initializes `services.relay_service.init_relay_service(...)`, binds `RelayHubLivenessReader` into Agent, and registers Execution's internal handler for `"hub_agent_response_internal"`.
- `main.py` still mounts legacy workflow routers (`api/orchestration_center.py` and `api/task.py`), so legacy workflow collection dropping is not currently safe on `main`.
- `common/protocols/hub_protocols.py` already defines `HubDispatchPort`, `HubManagement`, and `HubLivenessReader`.
- `common/dto/internal_events.py` defines `HubAgentResponseInternal`.
- `common/dto/delivery.py` defines `HubAgentEvent`.
- `common/protocols/repository_protocols.py` defines `HubRepository`, but no concrete Hub repository package exists yet.
- `services/relay_service.py` still owns hub registration, connection streams, in-memory offline queues, Redis Streams liveness, hub heartbeat monitoring, agent sync, publish authorization, publish delegation, cancel/reply relay controls, and status queries.
- `execution/dispatch/transports/relay.py` still owns outbound hub dispatch and inbound publish normalization and imports `database.mongodb`, `models.hub`, and `services.relay_service` types.
- `execution/facade.py` already validates and handles `HubAgentResponseInternal` through `handle_hub_agent_response(...)`; Phase 7b explicitly did not implement durable idempotency or owner-worker routing.
- `api/relay.py` and `api/hub.py` fetch the global relay singleton from `services.relay_service`.
- `infrastructure/relay_streams.py` is a concrete Redis Streams adapter outside a Hub module.

## File Inventory

Create:
- `hub_runtime_bridge/__init__.py`: exports `HubFacade`, `HubRuntimeBridgeDeps`, and compatibility constructor helpers. `HubDeps` is reserved for the container protocol bundle.
- `hub_runtime_bridge/facade.py`: implements `HubManagement` and async `HubLivenessReader`; delegates to focused services and owns lifecycle start/stop.
- `hub_runtime_bridge/dispatch_adapter.py`: implements the dispatch-facing port/adapter and any cache-only sync compatibility required by current `HubDispatchPort`.
- `hub_runtime_bridge/deps.py`: `HubRuntimeBridgeDeps` dataclass for injected dependencies (`HubRepository`, `HubResponseJournal`, `HubTaskOwnershipStore`, Hub-owned `worker_id` / `instance_id`, `AgentRegistryWriter`, Agent-owned hub status reader, Agent call-counter, Room publish authorization/lineage reader protocol, Room-owned message cancellation reader, offline failure persistence/error-frame port, `LeaderElector | None`, optional `MetricsCollector`/`NoopMetricsCollector`, `EventPublisher`, `RedisStreams | None`, `RedisKV | None` or a narrow `HubRelayRedis` protocol, relay transport, clock, traced task runner, `HubRuntimeBridgeConfig`). Do not depend on concrete `EventPublisherImpl.instance_id`; the Common publisher protocol does not expose worker identity.
- `hub_runtime_bridge/config.py`: maps existing relay settings into a narrow `HubRuntimeBridgeConfig` dataclass and validates offline queue, heartbeat, Redis stream, and idempotency settings. This file may accept an app-settings object passed from `container.py`, but it must not import `config.settings` or read global settings directly.
- `hub_runtime_bridge/repository/__init__.py`: package marker for repository implementations.
- `hub_runtime_bridge/repository/mongo.py`: concrete `HubRepository` implementation over existing hub collections only.
- `hub_runtime_bridge/hub_response_journal.py`: sidecar durable journal for hub responses with `run_id` references, idempotency claims, replay leases, processed markers, retry metadata, and dead-letter metadata.
- `hub_runtime_bridge/service/hub_connection.py`: hub registration, ownership lookup, connect stream lifecycle, and status projection.
- `hub_runtime_bridge/service/__init__.py`: package marker for service implementations.
- `hub_runtime_bridge/service/hub_liveness.py`: authoritative liveness checks and heartbeat monitor orchestration.
- `hub_runtime_bridge/service/hub_publish.py`: hub publish authorization, event normalization, frontend hub event emission, idempotent internal event persistence, and internal event emission.
- `hub_runtime_bridge/service/hub_relay.py`: outbound user message / cancel / reply relay operations implementing `send_to_hub`, `cancel_hub_task`, and `reply_to_hub_task` using explicit command DTOs.
- `hub_runtime_bridge/service/agent_sync.py`: hub agent sync and offline marking through `AgentRegistryWriter`.
- `hub_runtime_bridge/service/dispatch_policy.py`: Hub-owned liveness/offline policy used by Execution dispatch middleware when pre-dispatch checks need the existing deny behavior.
- `hub_runtime_bridge/ownership.py`: owner-worker map for dispatched hub tasks and response filtering.
- `hub_runtime_bridge/task_ownership.py`: durable `hub_task_ownership` lease store keyed by ownership aliases with owner id, lease token, lease expiry, partial unique alias indexes for non-empty aliases, owner/expiry lookup indexes, and atomic alias update operations.
- `hub_runtime_bridge/service/ownership_lease_maintainer.py`: background owner-side lease maintainer that renews durable hub task ownership while tasks remain live and releases terminal/cancelled ownership.
- `hub_runtime_bridge/service/hub_response_replay_worker.py`: background journal retry worker that scans expired replay claims and internal emit claims, retries remote emission, or attempts recovery claims without requiring process restart.
- `hub_runtime_bridge/internal_response_router.py`: ownership guard that receives `HubAgentResponseInternal`, discards non-owned duplicate/remote events, and delegates owned events to `HubAgentResponseSink`.
- `hub_runtime_bridge/idempotency.py`: idempotency key derivation for stable upstream ids such as `(hub_id, task_id, response_seq)` and non-deduping legacy compatibility fingerprints.
- `hub_runtime_bridge/transport/offline_queue.py`: in-memory offline queue implementation with TTL/overflow failure behavior preserved.
- `hub_runtime_bridge/transport/__init__.py`: package marker for transport implementations.
- `hub_runtime_bridge/transport/relay_streams.py`: moved Redis Streams relay adapter from `infrastructure/relay_streams.py`.
- `hub_runtime_bridge/transport/relay_transport.py`: Hub-owned transport abstraction for pushing and reading hub stream events.
- `hub_runtime_bridge/adapters/api_key.py`: API-key owner adapter used by route shims and tests.
- `hub_runtime_bridge/adapters/__init__.py`: package marker for adapter implementations.
- `hub_runtime_bridge/adapters/a2a_card.py`: exact allowlisted A2A `AgentCard` validation adapter.
- `hub_runtime_bridge/adapters/legacy_models.py`: translation between `models.hub` request/response models and Common DTOs.
- `hub_runtime_bridge/adapters/legacy_failure.py`: compatibility path for existing offline failure writes to room agent messages until Execution/Room owns a cleaner port; it uses only an injected offline failure persistence/error-frame protocol and may not import service singletons or Mongo globals.
- `tests/test_hub_runtime_bridge_protocols.py`: package registration, protocol conformance, imports, and static boundary tests.
- `tests/test_hub_runtime_bridge_facade.py`: facade registration, status, liveness, dispatch, cancel/reply, start/stop, and dependency wiring tests.
- `tests/test_hub_runtime_bridge_publish.py`: publish authorization, frontend/internal event split, sidecar hub response journaling, idempotency, owner-worker filtering, legacy payload keying, and payload validation tests.
- `tests/test_hub_runtime_bridge_agent_sync.py`: agent sync through `AgentRegistryWriter`, invalid card handling, prune semantics, and offline transitions.
- `tests/test_hub_runtime_bridge_relay_streams.py`: Redis stream payload parity, heartbeat TTL, resume IDs, disconnect signaling, and degraded/in-memory behavior.
- `tests/test_hub_runtime_bridge_api_parity.py`: `api/relay.py` and `api/hub.py` route-shim parity using the existing request/response models.
- `tests/test_phase8_hub_runtime_bridge_gate.py`: AST gate for HubRuntimeBridge import boundaries and forbidden direct writes.
- `tests/fixtures/phase8_hub_import_allowlist.json`: exact temporary compatibility imports with expiry notes.
- `tests/fixtures/phase8_hub_routes.json`: expected relay/hub route inventory and route-to-protocol mapping.
- `tests/fixtures/phase8_legacy_collection_cleanup.json`: legacy workflow collection names, active-code-reference gate metadata, and cleanup-blocker evidence.
- `database/migration/phase8_legacy_workflow_cleanup.py`: app-shell/database migration readiness helper for legacy workflow collection cleanup; not imported by HubRuntimeBridge.

Modify:
- `common/dto/hub.py`: add any missing Common DTO fields needed by the facade, preserving current route-model translation.
- `common/dto/__init__.py`: export new Hub command DTOs and publish lineage DTOs used across module boundaries.
- `common/protocols/hub_protocols.py`: resolve the `is_hub_online` signature collision by keeping `HubDispatchPort.is_hub_online(...)` synchronous and cache-only on the app-shell-injected `HubDispatchAdapter`, while authoritative dispatch denial/offline marking uses async `HubDispatchPolicy` / `HubLivenessReader`. Add tests proving Execution denial/offline marking never uses the sync cache path.
- `common/protocols/repository_protocols.py`: extend `HubRepository` only for concrete Phase 8 needs such as owner lookup, registration/status projection, heartbeat/liveness scans, `list_online_hubs_for_liveness()`, `list_offline_hubs_for_recovery(limit)`, `update_hub_status(...)`, and `update_hub_status_if_current(hub_id, connection_id, ...)`; add a separate `HubResponseJournal` protocol if the sidecar journal is shared outside the Hub module.
- `common/protocols/room_protocols.py`: add or extend a publish-authorization protocol that proves room owner, message-room match, and agent-hub ownership for hub publish intake.
- `common/protocols/agent_protocols.py`: add an Agent-owned hub status reader protocol such as `HubAgentStatusReader.count_hub_agents(hub_id)` for active/inactive counts used by Hub status projection.
- `common/protocols/agent_protocols.py`: add an Agent-owned `AgentCallCounter` / `AgentUsageWriter` protocol for preserving relay dispatch call-count behavior without `database.mongodb` globals.
- `agent/facade.py`: implement `HubAgentStatusReader` and `AgentCallCounter` by delegating to the Agent repository; no Hub or Execution dispatch code may query Agent persistence directly.
- `agent/repository/mongo.py`: add the Agent-owned active/inactive hub-agent count query and call-count increment method if no existing repository methods cover them.
- `common/dto/room.py`: add a Hub publish lineage snapshot DTO, or add it to `common/dto/hub.py`, carrying `room_id`, `room_owner_id`, `agent_message_id`, `agent_id`, `agent_hub_id`, `related_message_id`, `turn_id`, root `run_id`/user message id, tracked `task_id`, and cancellation-relevant ids.
- `common/protocols/room_protocols.py`: add a Room-owned `RoomAgentTaskTracker` protocol only if task-tracking persistence cannot remain in Execution dispatch before the Hub send.
- `room/facade.py`: implement the Room-owned publish authorization/lineage reader that returns the full snapshot and replaces the current insufficient “any room agent has hub_id” check for publish intake.
- `room/facade.py`: implement a Room-owned `MessageCancellationReader` for hub publish intake, or expose cancellation state on the publish lineage reader, checking both `agent_message_id` and the related root/user message id.
- `room/repository/mongo.py`: add repository support for fetching the agent-message/root lineage snapshot if it is not already available through existing message repositories.
- `common/protocols/room_protocols.py`: define `MessageCancellationReader` if cancellation state is not included in the publish lineage snapshot. Do not put hub publish cancellation checks in Delivery protocols; Delivery remains transport-only.
- `common/dto/internal_events.py`: add correlation fields or required payload metadata for `HubAgentResponseInternal`, including `journal_id`, `idempotency_key`, and `run_id`, so the router can mark sidecar records processed/dead-lettered after handling.
- `common/protocols/__init__.py`: export new or updated protocol surfaces, including `HubDispatchPolicy`, `HubAgentStatusReader`, `AgentCallCounter`, Room publish authorization/lineage, Room cancellation reader, and shared journal protocol if exposed.
- `container.py`: add `HubDeps`, `create_hub_facade(...)`, and `create_hub_deps(...)`; wire Hub after Agent/Room/Delivery and before Execution.
- `main.py`: replace direct `init_relay_service(...)` construction with container-created Hub deps; bind Agent liveness from `HubLivenessReader`; register the Hub internal-response router exactly once after Execution creation, not Execution's sink directly.
- `pyproject.toml`: register `hub_runtime_bridge`, `hub_runtime_bridge.repository`, `hub_runtime_bridge.service`, `hub_runtime_bridge.transport`, and `hub_runtime_bridge.adapters`.
- `services/relay_service.py`: replace implementation with a compatibility shim/proxy over the Hub facade while preserving `RelayService`, `relay_service`, `init_relay_service(...)`, and `RelayHubLivenessReader` imports and old method names used by current tests/routes.
- `api/relay.py`: keep existing route URLs and response models, but delegate to `HubManagement` / route adapter instead of importing the relay singleton directly.
- `api/hub.py`: delegate status reads to `HubManagement`.
- `infrastructure/relay_streams.py`: compatibility import from `hub_runtime_bridge.transport.relay_streams`.
- `execution/dispatch/transports/relay.py`: replace direct `RelayService` assumptions with injected Common `HubDispatchPort` / narrow Hub relay compatibility protocol, while preserving dispatch result semantics. Execution must not import `hub_runtime_bridge.*`.
- `execution/dispatch/middleware/hub_transport.py`: replace direct RelayService liveness/offline mutation with the Hub dispatch policy protocol, or remove the pre-dispatch mutation and prove `send_to_hub` preserves the same denial/offline side effects.
- `execution/dispatch/agent_message_processor.py`: remove the lazy `services.relay_service` dependency by injecting a Hub dispatch/relay port through construction or a narrow factory adapter.
- `execution/orchestration/room_message_center.py`: pass the Hub dispatch/relay port through the construction path used by `AgentMessageProcessor`.
- `execution/orchestration/factory.py`: update factory/binder wiring if needed so `AgentMessageProcessor` no longer lazily imports `services.relay_service`.
- Existing tests under `tests/test_api_relay.py`, `tests/test_heartbeat_fixes.py`, `tests/test_dispatch_middleware.py`, `tests/test_call_counters.py`, `tests/test_common_foundation.py`, and `tests/test_execution_protocols.py` as needed for new module paths and shims.
- `docs/MODULAR_DECOUPLING_DESIGN.md`: implementation phase may update Phase 8 status after code lands, but this plan-writing task must not edit it.

Reference-only:
- `docs/MODULAR_DECOUPLING_DESIGN.md` sections 3.3, 4.4, 4.5, 4.6, 6.3, 8.2, 9, 10, and 14.
- `docs/superpowers/plans/2026-05-17-phase-7-execution-module.md`.
- `services/relay_service.py`.
- `api/relay.py`.
- `api/hub.py`.
- `infrastructure/relay_streams.py`.
- `execution/dispatch/transports/relay.py`.
- `execution/facade.py`.
- `delivery/event_publisher.py`.
- `agent/facade.py`.
- `room/facade.py`.
- `models/hub.py`.
- `models/run.py`.
- `database/mongodb.py` run event indexes and collection setup, as a reference for why hub responses use a sidecar journal instead of lifecycle `run_events`.

## Dependency Shape

```text
api.relay / api.hub
  -> container-bound HubManagement
    -> hub_runtime_bridge.facade.HubFacade
      -> hub_runtime_bridge.service.*
      -> HubRepository
      -> AgentRegistryWriter
      -> HubAgentStatusReader
      -> Room publish authorization/lineage reader protocol
      -> EventPublisher.emit(HubAgentEvent)
      -> EventPublisher.emit_internal(HubAgentResponseInternal)
      -> hub_response_journal sidecar
      -> hub_runtime_bridge.transport.*

execution.dispatch
  -> common.protocols.HubDispatchPort + HubDispatchPolicy
    -> app-shell-injected hub_runtime_bridge.dispatch_adapter.HubDispatchAdapter
      -> hub_runtime_bridge service layer

delivery internal event bus
  -> hub_runtime_bridge.internal_response_router.HubInternalResponseRouter
    -> ownership owns_hub_task(task_id)
    -> Execution HubAgentResponseSink only for owned tasks

agent.facade
  -> HubLivenessReader
    -> hub_runtime_bridge.facade.HubFacade

hub_runtime_bridge/**
  -> common.dto / common.protocols
  -> injected Agent / Room / Delivery protocols
  -> DAL repository protocols and exact compatibility adapters
  -> no direct execution imports
  -> no direct Agent repository writes
  -> no direct database.mongodb, services.*, config.settings, api, main, or container imports outside exact allowlisted adapters
  -> no api/main/container imports
```

## Known Deviations / Deferred Target Architecture

- **Compatibility shims remain until Phase 9.** `services.relay_service`, including `RelayService`, `RelayHubLivenessReader`, `relay_service`, and `init_relay_service(...)`, plus `api.relay`, `api.hub`, and `infrastructure.relay_streams` stay import-compatible so existing callers, tests, and external routes do not break during Phase 8.
- **Offline failure persistence is a named compatibility adapter.** Current `RelayService._fail_offline_message(...)` mutates room agent messages, mutates A2A task status to failed, persists the room agent message, and emits an error frame. Phase 8 may preserve that behavior in `hub_runtime_bridge/adapters/legacy_failure.py` with a static allowlist and expiry note, but only through an injected offline failure persistence/error-frame protocol wired by the app shell or Room-facing adapter. The adapter must not import `services.*`, `database.mongodb`, or global SSE managers. It may either use minimal `a2a.types` task-status imports or a dict/DTO-preserving implementation, but parity tests must prove failed task status, DB update, message text, and SSE error frame.
- **A2A imports are adapter-local.** Phase 8 can keep `a2a.types.AgentCard` inside `hub_runtime_bridge/adapters/a2a_card.py` and the minimal task-status imports needed by offline failure compatibility inside `hub_runtime_bridge/adapters/legacy_failure.py`. No other Hub module file should import `a2a.types`.
- **Hub publish event vocabulary remains legacy-model compatible.** Common DTOs may grow, but the route-facing `models.hub.HubPublishRequest` schema remains the wire contract in Phase 8.
- **Redis Streams payload format must remain stable.** `hub_runtime_bridge.transport.relay_streams` must read old stream entries and write the same JSON envelope as `infrastructure/relay_streams.py`.
- **Workflow collection cleanup is app-shell/database migration work, not Hub-owned data.** The design lists legacy workflow collection dropping in Phase 8, but current `main` still mounts and references legacy workflow/task code. Phase 8 must implement an explicit cleanup readiness gate in `database/migration/phase8_legacy_workflow_cleanup.py` or another app-shell migration artifact. HubRuntimeBridge must not own cleanup helpers for `base_tasks`, `meta_tasks`, `task_sessions`, or `chat_contexts`.
- **Hub response journaling is sidecar, not lifecycle sequencing.** Current run events require `run_id`, monotonic `seq`, event type, and existing unique indexes on `event_id` and `(run_id, seq)`. Phase 8 must not insert hub response journal entries into the lifecycle `run_events` stream unless it also rewrites lifecycle sequence allocation and healing. This plan uses a sidecar `hub_response_journal` collection with unique `idempotency_key`, `run_id`, `task_id`, normalized payload, processing status, replay lease fields, retry/dead-letter fields, and timestamps.
- **Local owned publish handling intentionally bypasses Delivery scheduling.** The design target describes Delivery-mediated internal events, but current `EventPublisher.emit_internal(...)` schedules local handlers asynchronously and cannot preserve current `RelayService.process_publish(...)` per-request await/order semantics. Phase 8 uses an injected `HubInternalResponseDispatcher` protocol for local owned events and uses Delivery only for cross-worker fanout. Static gates must prove Hub imports only the protocol and not concrete Execution.
- **Publish frontend/internal mapping must be explicit.** Current `AgentResponseHandler` emits frontend and DB side effects when Execution handles internal events. Phase 8 must not also emit duplicate frontend frames from Hub for those same events. Each `HubPublishEventType` must be classified as frontend-only, internal-only, or both with a documented order and golden parity tests. The default classification is internal-only unless a parity test proves a Hub-owned frontend frame is required.
- **Synchronous hub liveness is cache-only.** `HubDispatchPort.is_hub_online(...)` is synchronous on current `main`, but Redis-backed authoritative liveness is async. Phase 8 must either remove the sync method from dispatch decisions or document it as cache-only; authoritative dispatch and middleware decisions use async `HubLivenessReader` or `HubDispatchPolicy`.
- **Durable ownership prevents remote-owner theft.** The local owner map is not enough to distinguish a crashed owner from a live owner on another worker. Dispatch must create/refresh a durable ownership lease keyed by all aliases, with a configured TTL. The owner renews the lease while dispatched tasks remain live, refreshes it when handling owned hub responses, and releases/cleans it up on terminal task completion or cancellation. Publish handling can claim live recovery only after that lease expires; otherwise non-owner workers fan out and do not mark processed.
- **Hub journal delivery is at-least-once across the Execution sink crash window.** Phase 8 prevents duplicate journal claims, duplicate internal emissions, and duplicate same-process handler scheduling before the Execution sink runs. Current `main` Execution side effects are not guarded by a sink-level inbox keyed by `journal_id`, so a worker crash after `HubAgentResponseSink.handle_hub_agent_response(...)` applies DB/SSE side effects but before Hub marks the journal processed can replay the event and duplicate those side effects. Phase 8 must test and document this exact residual risk instead of claiming end-to-end exactly-once side effects; a later Execution inbox/outbox phase can close it.
- **Design deviations are explicit Phase 8 implementation constraints.** This plan intentionally deviates from the design document's run-events journaling sketch and unconditional legacy collection drop because current `main` would otherwise risk run sequence corruption and active legacy route data loss. Static gates should assert these deviations are documented, evidence-backed, and tracked for a later design/status update, not require literal consistency with outdated target text.

## Tasks

### Task 1: Baseline, Branch, and Boundary Gates

**Files:**
- Create: `tests/test_phase8_hub_runtime_bridge_gate.py`
- Create: `tests/fixtures/phase8_hub_import_allowlist.json`
- Create: `tests/fixtures/phase8_hub_routes.json`
- Reference: `docs/MODULAR_DECOUPLING_DESIGN.md`

- [ ] **Step 1: Record baseline status**

Run:

```bash
git status --short --branch
git switch -c phase-8-hub-runtime-bridge
rg -n "relay_service|RelayService|RelayTransport|RelayHubLivenessReader|HubAgentResponseInternal|HubDispatchPort|HubManagement|HubLivenessReader" services api infrastructure execution common container.py main.py tests --glob '*.py'
```

Expected: branch is created from `main`; relay behavior is still owned by `services/relay_service.py` and route shims still use the relay singleton.

- [ ] **Step 2: Write the failing static gate**

Create an AST test that fails until the new package exists. It must assert:
- `hub_runtime_bridge/` exists and is registered in `pyproject.toml`.
- Hub package files do not import `execution`, `modules.*`, `api`, `main`, `container`, `database.mongodb`, `services.*`, `agent.*`, `room.*`, `delivery.*`, concrete `dal.*` implementation packages, `config.settings`, `common.config`, `common.config.settings`, direct `settings`, concrete Agent repositories, concrete Room repositories, concrete Redis infrastructure, or legacy `models.*` except exact allowlisted adapters (`adapters/legacy_models.py`, `adapters/api_key.py`, `adapters/legacy_failure.py`) with expiry notes. `hub_runtime_bridge/config.py` may accept a passed app-settings object but may not import global settings modules. HubRuntimeBridge uses `common.dto` / `common.protocols` plus injected protocols for Agent/Room/Delivery access.
- Hub package files do not write `agents_collection` or call `mongodb.upsert_hub_agent(...)` directly.
- Hub package files do not query `agents_collection` directly for status counts; status counts come from `HubAgentStatusReader`.
- Hub package files do not call `enable_task_tracking_on_message` or mutate room agent messages, except the exact allowlisted offline failure compatibility adapter `hub_runtime_bridge/adapters/legacy_failure.py`.
- Hub package files do not call bare `asyncio.create_task` for heartbeat, stream, replay, or ownership lease background work; they must use the injected traced task runner.
- Hub service/repository files consume only `HubRuntimeBridgeConfig`, not the full app settings object; settings-to-config mapping is allowed only by passing app settings into `hub_runtime_bridge/config.py` from app-shell/container wiring.
- Hub publish intake does not depend on `SSETransport.is_cancelled` or Delivery cancellation APIs; cancellation state comes from Room lineage/cancellation protocols.
- Only `hub_runtime_bridge/adapters/a2a_card.py` may import `a2a.types.AgentCard`; only `hub_runtime_bridge/adapters/legacy_failure.py` may import minimal `a2a.types` task-status classes if the implementation cannot preserve status shape with dictionaries.
- Only `hub_runtime_bridge/adapters/legacy_failure.py` may use the injected offline failure compatibility protocol; no Hub file, including `legacy_failure.py`, may import service singletons or Mongo globals for offline failure persistence.
- `execution/**` no longer imports `services.relay_service`; any temporary exception must be in `tests/fixtures/phase8_hub_import_allowlist.json` with an expiry note.
- `execution/**` must not import `hub_runtime_bridge.*`; Execution depends only on Common DTO/protocol surfaces and receives concrete Hub implementations from `container.py` / `main.py`.
- `execution/dispatch/transports/relay.py` is outbound-only after migration: no `handle_publish_event`, no inbound hub publish normalization, no `models.hub` import, no `database.mongodb` global import, and no `services.relay_service` type dependency.
- `services/relay_service.py` and `infrastructure/relay_streams.py` are shims after migration.
- `services/relay_service.py` shim imports no `modules.*`, `execution.*`, concrete DB/SSE services, or concrete transports, and instantiates no `AgentResponseHandler` or `RelayTransport`; `init_relay_service(...)` only binds or returns a compatibility proxy over the app-shell-created Hub facade.
- `api/relay.py` and `api/hub.py` route inventory matches `tests/fixtures/phase8_hub_routes.json`.
- Legacy collection cleanup is blocked unless static discovery proves `api/orchestration_center.py`, `api/task.py`, and all live collection references are decommissioned.
- Phase 8 design deviations from `docs/MODULAR_DECOUPLING_DESIGN.md` are explicitly documented in this plan, backed by current-`main` evidence, and tracked for a later design/status update. The gate must not require literal consistency where Known Deviations intentionally differ from the target design.

Run:

```bash
pytest tests/test_phase8_hub_runtime_bridge_gate.py -q
```

Expected: FAIL because the HubRuntimeBridge package and shims do not exist yet.

### Task 2: Common Hub DTO and Protocol Tightening

**Files:**
- Modify: `common/dto/hub.py`
- Modify: `common/dto/internal_events.py`
- Modify: `common/dto/room.py`
- Modify: `common/dto/__init__.py`
- Modify: `common/protocols/hub_protocols.py`
- Modify: `common/protocols/agent_protocols.py`
- Modify: `common/protocols/room_protocols.py`
- Modify: `common/protocols/repository_protocols.py`
- Modify: `common/protocols/__init__.py`
- Modify: `tests/test_common_foundation.py`
- Create: `tests/test_hub_runtime_bridge_protocols.py`

- [ ] **Step 1: Write protocol conformance tests**

Add tests asserting the final protocol method sets:
- `HubDispatchPort`: `send_to_hub`, `cancel_hub_task`, `reply_to_hub_task`, and sync cache-only `is_hub_online`.
- Protocol signature collision test: prove `HubFacade` is not forced to implement both async and sync `is_hub_online`; the chosen adapter shape must type-check/runtime-check cleanly.
- `HubDispatchPolicy`: async authoritative pre-dispatch check used by Execution middleware, including the existing offline-marking side effect or a tested decision to defer marking to `send_to_hub`.
- `HubManagement`: registration, status lookup/list, stream connection, publish intake, heartbeat monitor lifecycle, heartbeat record.
- Route-facing Hub API surface: `register_hub`, `connect_hub_stream`, `publish_from_hub`, `sync_agents`, `get_hub_status`, `record_hub_heartbeat`, and `hub_status_for_user` / owner-scoped status projection. This can be `HubManagement` if the protocol is expanded, or a separate `HubRouteService` protocol if keeping domain and route methods separate is cleaner.
- `connect_hub_stream` callable shape: the route must consume it with `async for` without awaiting a coroutine. Prefer `def connect_hub_stream(...) -> AsyncIterator[dict]` or an explicitly tested async-generator method shape; tests must fail if the implementation returns a coroutine that needs `await` before iteration.
- Outbound hub dispatch command DTO: includes `hub_id`, cloud `agent_id`, hub `local_agent_id`, `room_id`, `user_message_id`, `agent_message_id`, serialized prepared A2A message payload, generated local pending `task_id` as internal-only metadata, task tracking timestamps/data, and cancel/reply fields (`context_id`, `reply_text`) needed to preserve current relay envelopes. The generated pending task id must not be serialized into the Cloud-to-Hub `user_message` wire envelope unless a golden parity fixture intentionally changes the hub daemon contract.
- Offline hub failure DTO/protocol: define `OfflineHubFailurePort` and a command carrying `room_id`, `agent_message_id`, `agent_id`, failed task id/status payload, user-facing error text, and SSE error frame fields needed to preserve current offline failure DB update and frontend error behavior.
- Task-tracking ownership decision: preserve current behavior by keeping pending task tracking persistence in Execution dispatch before `HubDispatchPort.send_to_hub(...)`. If implementation proves that is impossible, use a Room-owned `RoomAgentTaskTracker` protocol; HubRuntimeBridge must not own this Room mutation.
- `HubCancelCommand` DTO or widened cancel method: includes `hub_id`, `agent_message_id`, `local_agent_id`, and optional `task_id`, and must serialize to the current `cancel_task` `RelayToHubEvent`.
- `HubReplyCommand` DTO or widened reply method: includes `hub_id`, `agent_message_id`, `local_agent_id`, `room_id`, `reply_text`, optional `task_id`, and optional `context_id`, and must serialize to the current `user_reply` `RelayToHubEvent`.
- `HubLivenessReader`: async `is_hub_online`, `get_hub_owner_id`.
- `HubRepository`: get/upsert/status/heartbeat methods needed by Phase 8, including liveness scan/update methods `list_online_hubs_for_liveness()`, `list_offline_hubs_for_recovery(limit)`, `update_hub_status(...)`, and `update_hub_status_if_current(hub_id, connection_id, ...)` so heartbeat monitor code never reads raw hub collections directly.
- Room publish authorization protocol: validates hub owner, room ownership, agent message room membership, and agent-hub ownership.
- Room publish lineage reader protocol: returns the full agent-message/root snapshot needed for publish authorization, cancellation checks, canonical `run_id` derivation, `lifecycle_message_id` validation, and tracked `task_id` fallback.
- Room-owned message cancellation reader protocol: checks both `agent_message_id` and related root/user message id for publish drop decisions without Hub importing database or service singletons.
- Agent-owned hub status reader protocol: returns active/inactive hub-agent counts without HubRuntimeBridge reading Agent persistence directly.
- Agent-owned call-counter protocol: preserves current relay dispatch `increment_agent_call_count(agent_id, success=...)` behavior, including swallowed/logged counter failures.
- Hub response journal adapter/protocol: derives `run_id` from the hub agent message/root user message, persists a sidecar hub response record with `journal_id`, `run_id`, `task_id`, event type, normalized payload, unique `idempotency_key`, replay lease fields, processed state, retry metadata, and dead-letter metadata, and supports replay/lookup.
- Cancellation reader protocol: checks both `agent_message_id` and the related root/user message id before publish normalization; cancelled publishes are ignored without side effects.
- Root package exports: `common.dto.__init__` and `common.protocols.__init__` export all new DTOs/protocols used outside their defining files, including Hub dispatch/cancel/reply commands, publish lineage snapshot DTOs, `HubDispatchPolicy`, `HubAgentStatusReader`, `AgentCallCounter`, Room publish authorization/lineage, Room cancellation reader, and shared journal protocol if exposed.

Run:

```bash
pytest tests/test_common_foundation.py tests/test_hub_runtime_bridge_protocols.py -q
```

Expected: FAIL until Common DTO/protocol surfaces are aligned.

- [ ] **Step 2: Update Common surfaces minimally**

Add only the fields and protocol methods required to represent current behavior. Keep route-specific schemas in `models/hub.py`; use `common/dto/hub.py` for module boundaries.

- [ ] **Step 3: Verify Common protocol tests**

Run:

```bash
pytest tests/test_common_foundation.py tests/test_hub_runtime_bridge_protocols.py -q
```

Expected: PASS.

### Task 3: Hub Package Skeleton and Repository

**Files:**
- Create: `hub_runtime_bridge/__init__.py`
- Create: `hub_runtime_bridge/facade.py`
- Create: `hub_runtime_bridge/config.py`
- Create: `hub_runtime_bridge/deps.py`
- Create: `hub_runtime_bridge/service/__init__.py`
- Create: `hub_runtime_bridge/transport/__init__.py`
- Create: `hub_runtime_bridge/adapters/__init__.py`
- Create: `hub_runtime_bridge/repository/__init__.py`
- Create: `hub_runtime_bridge/repository/mongo.py`
- Create: `hub_runtime_bridge/hub_response_journal.py`
- Create: `hub_runtime_bridge/task_ownership.py`
- Modify: `pyproject.toml`
- Modify: `tests/test_hub_runtime_bridge_protocols.py`

- [ ] **Step 1: Write package and repository tests**

Cover config defaults from current relay settings, package exports, pyproject registration including subpackage `__init__.py` imports, repository calls over a fake `MongoDAL`, sidecar hub response journal idempotency upsert/find, exact journal index creation, concurrent stable-id duplicate publish claims, startup replay lookup, atomic replay lease/processed marker behavior, crash-after-claim lease expiry replay, and same-token processed updates. Journal indexes must be: unique `journal_id` for every record; partial unique `stable_idempotency_key` only for records where a stable upstream id such as `response_seq` is present; non-unique lookup indexes for `run_id`, `task_id`, replay/emit claim expiry, and processed/dead-letter status. Legacy no-sequence records use `dedupe_mode="none"` and `idempotency_key="ingest:<journal_id>"` or equivalent so null/duplicate stable keys cannot collide.
Also cover durable `hub_task_ownership` store setup: partial unique indexes for each ownership alias (`agent_message_id`, generated local pending task id, hub task id) with filters for non-null/non-empty values, indexes for owner/lease-expiry lookup, and atomic concurrent alias claim/update behavior. Tests must allow multiple ownership records with missing `hub_task_id`, prove later hub-task alias attachment succeeds, and reject duplicate non-null aliases.
Cover dependency defaults for observability: `HubRuntimeBridgeDeps` accepts a `MetricsCollector` or `NoopMetricsCollector`, and all background workers use the injected traced task runner in tests.
Cover settings isolation: `HubRuntimeBridgeDeps` accepts `HubRuntimeBridgeConfig`, and tests/static gates prove Hub service/repository files do not receive or import the full app settings object.

Run:

```bash
pytest tests/test_hub_runtime_bridge_protocols.py -q
```

Expected: FAIL until package skeleton and repository exist.

- [ ] **Step 2: Implement package skeleton and repository**

Implement config validation, dependency dataclasses, subpackage marker files, a minimal `HubFacade` skeleton for exports/protocol signature tests, a Mongo repository over existing hub collections, the sidecar hub response journal adapter/index setup, and `task_ownership.py` durable ownership-store index/setup in the same task that tests them. Task 4B and Task 8 extend the facade with behavior. Repository methods must not import `database.mongodb` or global settings. `container.py` or `hub_runtime_bridge/config.py` maps the full app settings object into `HubRuntimeBridgeConfig`; Hub services consume only the dataclass.

- [ ] **Step 3: Verify package and repository tests**

Run:

```bash
pytest tests/test_hub_runtime_bridge_protocols.py -q
```

Expected: PASS.

### Task 3B: Agent and Room Support Protocol Implementations

**Files:**
- Modify: `container.py`
- Modify: `agent/facade.py`
- Modify: `agent/repository/mongo.py`
- Modify: `room/facade.py`
- Modify: `room/repository/mongo.py`
- Create/Update: `tests/test_hub_runtime_bridge_protocols.py`
- Create/Update: `tests/test_agent_protocols.py`
- Create/Update: `tests/test_room_protocols.py`
- Update: `tests/test_agent_facade.py`
- Update: `tests/test_agent_repository.py`
- Update: `tests/test_room_golden.py`
- Update: `tests/test_room_repository.py`

- [ ] **Step 1: Write Agent/Room support tests**

Cover `HubAgentStatusReader.count_hub_agents(hub_id)` returning active/inactive counts from Agent-owned persistence, the Room publish authorization/lineage reader returning the snapshot required for hub publish authorization, root/run resolution, lifecycle validation, and tracked task-id fallback, and Room-owned cancellation checks for both `agent_message_id` and related root/user message id. Add negative tests for wrong room, wrong owner, wrong hub, missing agent message, missing root lineage, and cancelled publish messages.
Also cover container-visible protocol wiring: `AgentDeps` exposes/binds `HubAgentStatusReader` and `AgentCallCounter`, while `RoomDeps` exposes/binds the publish authorization/lineage and cancellation reader protocols that Hub will consume. The tests must fail if Hub support protocols are implemented on facades but are not reachable through container dependency bundles.
Add direct repository tests for Agent count/call-counter query filters and Room lineage/cancellation repository behavior, including missing lineage, tracked task fallback, and cancellation lookup.

Run:

```bash
pytest tests/test_hub_runtime_bridge_protocols.py tests/test_agent_protocols.py tests/test_room_protocols.py tests/test_agent_facade.py tests/test_agent_repository.py tests/test_room_golden.py tests/test_room_repository.py -q
```

Expected: FAIL until Agent and Room support protocols are implemented.

- [ ] **Step 2: Implement narrow Agent/Room support**

Add only the protocol methods required by HubRuntimeBridge. Do not move Hub logic into Agent or Room; Agent owns hub-agent counts, and Room owns message/ownership lineage.

- [ ] **Step 3: Verify Agent/Room support**

Run:

```bash
pytest tests/test_hub_runtime_bridge_protocols.py tests/test_agent_protocols.py tests/test_room_protocols.py tests/test_agent_facade.py tests/test_agent_repository.py tests/test_room_golden.py tests/test_room_repository.py -q
```

Expected: PASS.

### Task 4: Liveness, Heartbeat, and Relay Streams

**Files:**
- Create: `hub_runtime_bridge/service/hub_liveness.py`
- Create: `hub_runtime_bridge/transport/relay_streams.py`
- Create: `hub_runtime_bridge/transport/offline_queue.py`
- Modify: `infrastructure/relay_streams.py`
- Create: `tests/test_hub_runtime_bridge_relay_streams.py`
- Update: `tests/test_relay_streams.py`
- Update: `tests/test_heartbeat_fixes.py`

- [ ] **Step 1: Write liveness and stream parity tests**

Cover Redis TTL as authoritative liveness, in-memory connection fallback, `last_event_id` resume, heartbeat frame timing hooks, stale hub offline marking through `AgentRegistryWriter`, self-heal of Mongo projection when Redis TTL is alive, and offline queue TTL/overflow behavior.
Cover `HubRepository` heartbeat monitor methods for online/offline scans, recovery scans with limits, status updates, and conditional `connection_id` status updates that preserve current stale-connection protection.
Cover active connection metrics such as `hybro_hub_connections_active` for connect/disconnect and degraded stream modes.
Tests must prove `hub_runtime_bridge/transport/relay_streams.py` uses injected Redis protocols (`RedisStreams` plus `RedisKV`, or `HubRelayRedis`) and does not import `infrastructure.redis_service`.
`tests/test_relay_streams.py` must continue proving the legacy `infrastructure.relay_streams.RelayStreamService` import shim preserves old behavior.
Cover leader-election parity for the heartbeat/offline sweep: when `LeaderElector` is injected, non-leader workers skip heartbeat/offline sweeps, leader workers acquire/release the same logical monitor lock, and no leader means single-worker behavior matches current code.

Run:

```bash
pytest tests/test_hub_runtime_bridge_relay_streams.py tests/test_relay_streams.py tests/test_heartbeat_fixes.py -q
```

Expected: FAIL until Hub liveness and stream code is moved.

- [ ] **Step 2: Move Redis Streams and offline queue logic**

Move code from `infrastructure/relay_streams.py` and the offline queue parts of `RelayService` into HubRuntimeBridge transport/service classes. Leave `infrastructure/relay_streams.py` as an import shim.

- [ ] **Step 3: Verify liveness and stream parity**

Run:

```bash
pytest tests/test_hub_runtime_bridge_relay_streams.py tests/test_relay_streams.py tests/test_heartbeat_fixes.py -q
```

Expected: PASS.

### Task 4B: Hub Connection Lifecycle and Status Projection

**Files:**
- Create: `hub_runtime_bridge/service/hub_connection.py`
- Update: `hub_runtime_bridge/facade.py`
- Update: `tests/test_hub_runtime_bridge_facade.py`
- Update: `tests/test_hub_runtime_bridge_api_parity.py`

- [ ] **Step 1: Write connection lifecycle tests**

Cover current behavior for `register_hub`, `get_hub_owner_id`, `connect_hub_stream`, disconnect supersession, offline queue flush on connect, Redis Streams resume from `Last-Event-ID`, in-memory connection replacement via `_disconnect`, status projection, and active/inactive agent counts through `HubAgentStatusReader`.

Run:

```bash
pytest tests/test_hub_runtime_bridge_facade.py tests/test_hub_runtime_bridge_api_parity.py -q
```

Expected: FAIL until connection lifecycle and status projection are implemented.

- [ ] **Step 2: Implement connection lifecycle service**

Move registration, owner lookup, connect/disconnect state transitions, offline queue flush, and status projection from `services/relay_service.py` into `hub_connection.py`. Query hub-agent active/inactive counts only through the Agent-owned status reader protocol.

- [ ] **Step 3: Verify connection lifecycle**

Run:

```bash
pytest tests/test_hub_runtime_bridge_facade.py tests/test_hub_runtime_bridge_api_parity.py -q
```

Expected: PASS.

### Task 5: Agent Sync Through AgentRegistryWriter

**Files:**
- Create: `hub_runtime_bridge/service/agent_sync.py`
- Create: `hub_runtime_bridge/adapters/a2a_card.py`
- Create: `tests/test_hub_runtime_bridge_agent_sync.py`
- Update: `tests/test_api_relay.py`
- Update: `tests/test_phase8_hub_runtime_bridge_gate.py`

- [ ] **Step 1: Write agent sync tests**

Cover owner authorization, Redis Streams heartbeat refresh before sync, invalid AgentCard filtering, empty invalid payload behavior, prune semantics, activation of valid hub agents, offline marking, and no direct Agent repository or collection writes from Hub code.

Run:

```bash
pytest tests/test_hub_runtime_bridge_agent_sync.py tests/test_api_relay.py::TestRelayServiceAgentSync tests/test_phase8_hub_runtime_bridge_gate.py -q
```

Expected: FAIL until agent sync is moved and allowlists are updated.

- [ ] **Step 2: Implement agent sync service**

Translate `models.hub.HubAgentSync` to `HubAgentDescriptor`, validate cards in the adapter, refresh Redis liveness TTL before sync in Streams mode, and call only `AgentRegistryWriter` for sync/offline writes.

- [ ] **Step 3: Verify agent sync**

Run:

```bash
pytest tests/test_hub_runtime_bridge_agent_sync.py tests/test_api_relay.py::TestRelayServiceAgentSync tests/test_phase8_hub_runtime_bridge_gate.py -q
```

Expected: PASS.

### Task 6: Hub Dispatch Port and Outbound Relay

**Files:**
- Create: `hub_runtime_bridge/service/hub_relay.py`
- Create: `hub_runtime_bridge/dispatch_adapter.py`
- Create: `hub_runtime_bridge/service/dispatch_policy.py`
- Create: `hub_runtime_bridge/service/ownership_lease_maintainer.py`
- Create: `hub_runtime_bridge/transport/relay_transport.py`
- Create: `hub_runtime_bridge/adapters/legacy_failure.py`
- Update: `execution/dispatch/transports/relay.py`
- Update: `execution/dispatch/middleware/hub_transport.py`
- Update: `execution/dispatch/agent_message_processor.py`
- Update: `execution/orchestration/room_message_center.py`
- Update: `execution/orchestration/factory.py`
- Update: `tests/test_dispatch_middleware.py`
- Update: `tests/test_call_counters.py`
- Update: `tests/test_execution_protocols.py`
- Create/Update: `tests/test_hub_runtime_bridge_facade.py`

- [ ] **Step 1: Write outbound dispatch tests**

Cover `send_to_hub`, `cancel_hub_task`, `reply_to_hub_task`, online dispatch, offline queued dispatch, offline rejection after grace period, Redis Streams append failure while Redis TTL/liveness is still online, overflow failure behavior, offline failure parity through `legacy_failure.py` (failed task status, message text update, DB persistence, and SSE error frame), offline failure persistence/error-frame protocol wiring, static proof that `legacy_failure.py` does not import `services.*` or `database.mongodb`, call-counter preservation through `AgentCallCounter` including swallowed/logged counter failures, unchanged Redis/SSE Cloud-to-Hub wire envelopes for `user_message`, `cancel_task`, and `user_reply`, pending task tracking persistence before Hub send, durable ownership lease creation/aliasing/renewal/release, and the exact pre-dispatch offline behavior currently implemented by `HubTransportMiddleware`.
Tests must prove the generated pending task id remains internal-only: it is used for local tracking/ownership aliases but is absent from the serialized `user_message` event unless an explicit compatibility fixture documents a wire-contract change.
Redis-alive append-failure tests must preserve current compatibility behavior unless an explicit later decision changes it: `push_event()` returning no stream id returns `False` to Execution and lets the transport emit the generic frontend dispatch error, without marking the room message/task failed through offline-failure persistence.
Offline failure tests must assert the concrete `OfflineHubFailurePort` command shape, app-shell binding, failed task status mutation, room agent message persistence, and SSE error frame parity.
Ownership tests must include concurrent alias creation/update races so two workers cannot claim different aliases for the same task, and owner/lease-expiry indexes must support recovery scans without collection-wide ambiguity.
Add virtual-clock tests for the owner-side lease maintainer: it starts after Hub dispatch ownership is recorded, renews leases beyond TTL while tasks remain live, refreshes on owned responses, releases on terminal response/cancel, and does not keep renewing released or dead-lettered tasks.
Add dispatch observability tests for metrics such as `hybro_hub_dispatch_duration_seconds` and failure/queue tags, using `NoopMetricsCollector` in tests that do not assert metrics.
Add focused constructor-injection tests proving `execution/orchestration/room_message_center.py` and `execution/orchestration/factory.py` pass the Hub dispatch/relay port into `AgentMessageProcessor`, and `AgentMessageProcessor` no longer lazily imports `services.relay_service`.

Run:

```bash
pytest tests/test_hub_runtime_bridge_facade.py tests/test_dispatch_middleware.py tests/test_call_counters.py tests/test_execution_protocols.py -q
```

Expected: FAIL until outbound relay is protocol-backed.

- [ ] **Step 2: Implement outbound relay service**

Build Cloud-to-Hub events from widened Common hub command DTOs that contain the current relay metadata. `HubDispatchCommand` covers `user_message`; `HubCancelCommand` covers `cancel_task`; `HubReplyCommand` covers `user_reply`. Implement `dispatch_adapter.py` inside HubRuntimeBridge, but inject it into Execution only as Common `HubDispatchPort` / `HubDispatchPolicy`; Execution source must not import `hub_runtime_bridge.*`. Keep pending task tracking persistence in Execution dispatch before calling Hub, unless replaced by a Room-owned `RoomAgentTaskTracker` protocol. Track owner-worker task ownership when a hub task id is accepted or generated by writing both the local owner map and durable `hub_task_ownership` lease record keyed by `agent_message_id`, generated local pending task id, and later hub-acknowledged task id. Ownership leases use a configured TTL. Implement and start `ownership_lease_maintainer` for owned live tasks after dispatch ownership is recorded; it renews leases while tasks remain live, refreshes when owned responses are handled, and releases or marks terminal during task completion/cancellation cleanup. Tests must prove an active renewing owner cannot be stolen by recovery handling on another worker, and that an expired lease can be claimed exactly once. Preserve existing `ProcessingResult.RELAY_DISPATCHED` behavior and Cloud-to-Hub `RelayToHubEvent` wire shape in Execution transport. Remove the `AgentMessageProcessor` lazy relay singleton lookup by passing the Common Hub dispatch/relay protocol through the same construction path that selects transport middleware. Move the pre-dispatch liveness/offline decision into a Hub-owned dispatch policy so Execution does not call `mark_hub_agents_offline(...)` directly.

- [ ] **Step 3: Verify outbound dispatch**

Run:

```bash
pytest tests/test_hub_runtime_bridge_facade.py tests/test_dispatch_middleware.py tests/test_call_counters.py tests/test_execution_protocols.py -q
```

Expected: PASS.

### Task 7: Publish Intake, Event Split, Idempotency, and Owner Filtering

**Files:**
- Create: `hub_runtime_bridge/service/hub_publish.py`
- Create: `hub_runtime_bridge/service/hub_response_replay_worker.py`
- Create: `hub_runtime_bridge/ownership.py`
- Create: `hub_runtime_bridge/idempotency.py`
- Update: `hub_runtime_bridge/hub_response_journal.py`
- Create: `hub_runtime_bridge/internal_response_router.py`
- Create: `hub_runtime_bridge/adapters/legacy_models.py`
- Update: `common/dto/internal_events.py`
- Update: `common/dto/__init__.py`
- Create: `tests/test_hub_runtime_bridge_publish.py`
- Update: `tests/test_execution_facade.py`
- Update: `tests/test_delivery_event_publisher.py`
- Update: `tests/test_phase7_execution_event_gate.py` only if the expected hub ingress helper changes.

- [ ] **Step 1: Write publish tests**

Cover:
- Route-level unknown hub / wrong API key / room-not-found / wrong room owner rejection remains exception-driven and maps to current 403/404/error behavior before any batch events are processed.
- Per-event publish authorization and lineage failures, including unknown `agent_message_id`, room mismatch, missing agent, cross-hub agent ownership, and cancelled messages, are silent drop-and-continue cases that return 204 for the batch, create no journal/frontend/internal side effects for the dropped event, and do not abort later valid events in the same `HubPublishRequest`.
- Cancelled `agent_message_id` or cancelled related root/user message drops the publish before normalization, with no journal entry, no frontend event, and no internal event.
- Each current publish event type from `models.hub.HubPublishEventType`.
- A per-event mapping for every `HubPublishEventType`:
  - `task_submitted`: internal-only. Execution's `AgentResponseHandler` owns the submitted frontend frame; Hub must still journal/route the event and persist the hub `data.task_id` ownership alias before subsequent events are filtered.
  - `agent_response`, `agent_error`, `task_status`, `task_interactive`: internal-only when Execution/`AgentResponseHandler` owns DB/frontend side effects.
  - `artifact_update`: internal-only if Execution preserves current artifact SSE side effects; frontend-only only if tests prove Hub must bypass Execution for streaming.
  - `processing_status`: internal-only for lifecycle/root-resume semantics unless a typed `HubAgentEvent` is required by current frontend parity tests.
- Frontend `HubAgentEvent` emission only for event types proven frontend-only or both; no duplicate frontend delivery when Execution handles the internal event. Default assertion: no `HubAgentEvent` is emitted for `task_submitted`, `agent_response`, `agent_error`, `task_status`, `task_interactive`, `artifact_update`, or `processing_status` until a golden parity fixture proves Hub must own that frame. `task_submitted` is excluded from that exception because Execution already emits the submitted frame and Hub needs the internal path for task-id aliasing.
- Internal `HubAgentResponseInternal` emission for orchestration-resume events.
- `run_id` derivation from the hub agent message/root user message before journaling; reject or compatibility-skip internal emission when the run id cannot be proven.
- Per-event missing-`run_id` behavior is explicit and parity-tested:
  - `processing_status` and other lifecycle/root-resume events require a verified canonical `run_id` and lifecycle message id; if lineage cannot prove them, they are dropped before journaling or side effects while the route preserves current 204 behavior and logs the compatibility drop.
  - Non-lifecycle legacy events without a provable `run_id` use a documented compatibility transient path only if current golden tests prove legacy behavior needs it: no durable replay, no cross-worker recovery claim, and no claim of exactly-once processing. Otherwise the implementation must prove all current hub-dispatched messages have canonical `run_id`.
- Publish lineage snapshot contains the fallback tracked `task_id` and all ids needed for cancellation and lifecycle validation; do not rely on generic `RoomMessageInfo` if it lacks those fields.
- Sidecar hub response journal persistence before internal emission with idempotency key `(hub_id, task_id, response_seq)` for payloads with a stable upstream sequence/event id. Legacy payloads without `response_seq` or reliable upstream event id get a unique ingest `journal_id` per event and no cross-request dedupe; the legacy compatibility fingerprint is correlation-only.
- Legacy compatibility tests include two identical legacy events in the same `HubPublishRequest` batch and two identical single-event legacy publish requests; all are processed in order because no stable upstream id exists for safe collapse.
- Journal document shape includes `journal_id`, `run_id`, `task_id`, event type, normalized payload, correlation/idempotency metadata, idempotency key, `claim_owner`, `claim_token`, `claimed_at`, `claim_expires_at`, `internal_emit_claim`, `emitted_at`, `emit_expires_at`, processed marker, retry count, and dead-letter fields.
- `HubAgentResponseInternal` carries `journal_id`, `idempotency_key`, `run_id`, and optional `claim_token` either as top-level DTO fields or as required validated payload metadata. Remote fanout events normally omit `claim_token`; the receiving owner router must acquire its own claim before invoking Execution.
- Normalized internal `processing_status` payloads must include `lifecycle_message_id` and `lifecycle_message_id_verified=True` after Room lineage validation. Positive and negative tests must exercise `execution.facade.hub_agent_response_internal_to_agent_event(...)` for this exact requirement.
- Exact sidecar index behavior prevents accidental legacy collapse: unique `journal_id` applies to all records; partial unique `stable_idempotency_key` applies only when stable upstream identity is present; legacy `dedupe_mode="none"` records never share a unique key except their generated ingest journal id.
- Startup replay atomically claims journaled-but-unprocessed hub response events whose claim is absent or expired, seeds or bypasses local owner filtering for the claimed replay, re-emits them through the internal-response router exactly once, and marks them processed after successful handler completion with the same `claim_token`.
- For live publishes as well as replayed claims, `HubInternalResponseRouter` awaits `HubAgentResponseSink.handle_hub_agent_response(...)` directly and marks the journal record processed only after success; failures increment retry metadata or dead-letter according to config.
- Crash-window tests explicitly cover a process failure after `HubAgentResponseSink.handle_hub_agent_response(...)` has applied Execution DB/SSE side effects but before Hub marks the journal processed. Because current Execution has no idempotent sink inbox, Phase 8 documents this as at-least-once sink delivery with possible duplicate side effects in that narrow crash window; the implementation must not claim end-to-end exactly-once until an Execution-owned inbox/outbox keyed by `journal_id` exists.
- Crash recovery tests cover crash-after-claim, lease expiry replay by another worker, and remote duplicate skip while a non-expired lease is held.
- Batched publish events preserve current sequential semantics: events in one `HubPublishRequest` are normalized, journaled, routed, and awaited in request order, with per-`agent_message_id` ordering tests. The 204 response is sent only after local owned handler work is complete or the event is durably marked retryable/dead-lettered.
- Cross-worker fanout cannot cause local duplicate handling. Because Delivery internals are outside Phase 8's edit scope, do not add a fanout-only Delivery port in this phase; `HubInternalResponseRouter` must idempotently skip already processed/claimed `journal_id`s when `EventPublisher.emit_internal(...)` schedules local handlers. Tests must prove local direct routing plus fanout invokes `HubAgentResponseSink` exactly once.
- Ownership tests cover aliases by `agent_message_id`, generated local pending task id, and hub-acknowledged `data.task_id`; `task_submitted` persists the hub task-id alias before subsequent events are filtered.
- `task_submitted` tests prove the only submitted frontend frame comes from Execution handling, while Hub still journals/routes the event and persists the hub `data.task_id` ownership alias.
- Live ownership state machine:
  - Local owner: direct awaitable router handles the event, marks processed, then optional fanout is a no-op locally.
  - Remote known owner: do not mark processed locally; journal remains unprocessed and `emit_internal(...)` is called immediately so the owner worker can handle it. On receiving fanout, `HubInternalResponseRouter` must first resolve task aliases and read durable `hub_task_ownership`. Only the live owner identified by a non-expired ownership lease may atomically claim `journal_id` with `claim_owner`, `claim_token`, and `claim_expires_at`, await Execution, and mark processed/dead-lettered with the same token. Non-owners that observe a live owner lease no-op without claiming. Duplicate fanout while the remote claim is active no-ops; expired claims are retried by the replay worker.
  - Unknown/crashed owner: check the durable `hub_task_ownership` lease first. Only if the owner lease is expired may this worker atomically claim a live recovery lease, bypass owner filtering with that claim token, handle locally, and mark processed with the same token; if handling fails, mark retryable/dead-lettered before returning.
  - Discard paths must never mark processed.
- Stable-id duplicate publishes with the same `(hub_id, task_id, response_seq)` do not emit duplicate internal events. Legacy `dedupe_mode="none"` events each get distinct journals and internal emissions.
- Remote-owner duplicate publishes do not repeatedly fan out while the first internal emission is still in flight: before `emit_internal(...)`, Hub atomically sets an `internal_emit_claim` with `emitted_at` and `emit_expires_at`; duplicate unprocessed journal records with an active emit claim no-op, and expired emit claims retry with retry metadata.
- `hub_response_replay_worker` tests cover Delivery/event-bus fanout failure without process restart: expired `emit_expires_at` records are retried for remote owners, expired `claim_expires_at` records can be reclaimed, and retry/dead-letter metadata is updated without blocking new publishes.
- Remote-owner router tests cover successful remote processing, a non-owner receiving fanout before the owner and no-oping without a claim, duplicate fanout while a claim is active, expired remote claim retry, and non-owner discard without processed/dead-letter mutation.
- Normalization parity tests cover flattened hub file parts (`raw`, `url`, `mediaType`, `filename`, `metadata`) converted to nested A2A file parts, duplicate file suppression by bytes/uri/mime/name, text/data passthrough, `task_status` mapping to `canceled`, `error`, `response`, `interactive`, and `status_update`, invalid task-state drop, and `task_interactive` `task_id`, `context_id`, and default `input-required` state behavior.
- Non-owner workers discard remote internal responses for tasks they do not own before calling Execution, unless they hold a durable live/replay recovery claim for that journal record.
- App-shell registration routes `"hub_agent_response_internal"` to `HubInternalResponseRouter`, not directly to Execution. Delivery remains a generic internal-event scheduler and must not import Hub or Execution.
- Processing-status `lifecycle_message_id` is accepted only when validated against the root user message.
- Publish-on-non-owner tests prove a live remote owner lease prevents recovery stealing; crashed-owner tests prove an expired owner lease allows exactly-one recovery claim.
- Ownership lease lifecycle tests prove lease TTL configuration, renewal while an owner remains alive, refresh on owned response handling, and terminal release/cleanup after task completion/cancellation.
- Golden parity tests prove legacy frame shape/order for each publish event type and prove no duplicate frontend frames.

Run:

```bash
pytest tests/test_hub_runtime_bridge_publish.py tests/test_delivery_event_publisher.py tests/test_execution_facade.py -q
```

Expected: FAIL until publish intake is implemented.

- [ ] **Step 2: Implement publish service**

Authorize through Hub repository and the full Room publish-authorization/lineage protocol. Check Room-owned cancellation state before normalization. Normalize payloads into the same `AgentEvent`-compatible shape Phase 7b expects, but emit through a bound `HubInternalResponseDispatcher` instead of calling Execution or `AgentResponseHandler` directly. Derive the canonical run id from the publish lineage snapshot, persist the hub response to the sidecar journal, claim the idempotency key, then apply the live ownership state machine. Publish failure semantics must be explicit: journal persistence/claim failure returns 500 and is not accepted; post-journal handler failure preserves current 500 unless the event is durably marked retryable/dead-lettered before returning 204, in which case that is documented as an intentional route-semantic deviation. Implement `hub_response_replay_worker.py` to retry expired emit/replay claims without restart. Required local-owner ordering: direct awaitable router handles and marks processed first; only after success may `emit_internal(...)` fan out to other workers, and any same-process scheduled router invocation must observe the processed/claimed journal state and no-op. Remote/unknown owner paths must either fan out immediately or durably claim recovery ownership; do not mark processed on discard. Do not fan out after a failed direct route unless the journal state has been explicitly marked retryable/dead-lettered.

- [ ] **Step 3: Verify publish event split**

Run:

```bash
pytest tests/test_hub_runtime_bridge_publish.py tests/test_delivery_event_publisher.py tests/test_execution_facade.py -q
```

Expected: PASS.

### Task 8: HubFacade and Compatibility Shims

**Files:**
- Create: `hub_runtime_bridge/facade.py`
- Create: `hub_runtime_bridge/adapters/api_key.py`
- Modify: `services/relay_service.py`
- Modify: `api/relay.py`
- Modify: `api/hub.py`
- Create/Modify: `api/container_dependencies.py`
- Modify: `tests/test_api_relay.py`
- Modify: `tests/test_hub_runtime_bridge_api_parity.py`
- Modify: `tests/test_hub_runtime_bridge_facade.py`

- [ ] **Step 1: Write facade and API parity tests**

Cover existing relay route URLs and models:
- `POST /relay/hub/register`
- `GET /relay/hub/{hub_id}/events`
- `POST /relay/hub/{hub_id}/publish`
- `POST /relay/hub/{hub_id}/agents/sync`
- `GET /relay/hub/status`
- `POST /relay/hub/{hub_id}/heartbeat`
- `GET /hub/my-status`

Also cover route-contract parity beyond URL inventory:
- Relay shim imports and class compatibility: `from services.relay_service import RelayService, relay_service, init_relay_service, RelayHubLivenessReader` must work, and the proxy `RelayService` preserves the old method names used by current tests/routes until Phase 9 cleanup.
- API-key dependency parity, including `get_api_key` vs `get_api_key_no_track` behavior.
- Current 204, 403, 404, and validation/error status mapping.
- Publish failure parity: journal persistence/claim failures return 500 with no acceptance; post-journal handler failures either preserve current 500 or return 204 only after a durable retry/dead-letter marker, with tests documenting any intentional semantic deviation.
- Heartbeat authentication and failure behavior in Redis Streams and in-memory modes.
- `Last-Event-ID` header and current `last_event_id` query resume behavior. If `lastEventId` is added, it is additive only and tests must cover both query names.
- SSE stream `id:` formatting and `_stream_id` preservation.
- PermissionError-as-SSE-error behavior for stream connect failures.
- Response model field names for status and sync responses.
- Route dependency binding uses explicit `bind_hub_management(...)` / `get_hub_management(...)` helpers, or equivalent, so route functions no longer import `services.relay_service` except through a documented legacy test shim.

Run:

```bash
pytest tests/test_hub_runtime_bridge_api_parity.py tests/test_api_relay.py tests/test_hub_runtime_bridge_facade.py -q
```

Expected: FAIL until facade and shims delegate correctly.

- [ ] **Step 2: Implement facade and shims**

`HubFacade` composes services and implements the Common protocols. `services.relay_service` becomes a compatibility proxy exposing the old names and delegating to a bound facade, including a `RelayService` proxy class for direct test/legacy instantiation. API modules use explicit dependency helpers such as `bind_hub_management(...)` / `get_hub_management(...)`; `adapters/api_key.py` preserves current API-key owner extraction/auth behavior for route shims. Routes may fall back to the shim only for legacy tests, and static gates must forbid direct relay-singleton imports in route handlers.

- [ ] **Step 3: Verify facade and API parity**

Run:

```bash
pytest tests/test_hub_runtime_bridge_api_parity.py tests/test_api_relay.py tests/test_hub_runtime_bridge_facade.py -q
```

Expected: PASS.

### Task 9: Container and Lifespan Wiring

**Files:**
- Modify: `container.py`
- Modify: `main.py`
- Modify: `services/agent_liveness_service.py` only if binding names change.
- Modify: `tests/test_execution_protocols.py`
- Modify: `tests/test_multi_worker_safety.py`
- Create/Update: `tests/test_hub_runtime_bridge_facade.py`

- [ ] **Step 1: Write wiring tests**

Assert creation order:
1. DAL / adapters.
2. Legacy `RedisService` for leader election/relay KV, separate Redis Streams client, and Delivery are constructed/started; Delivery health is populated before the multi-worker guard.
3. Agent / Room / Context & Memory.
4. Multi-worker safety guard runs with the same relay-stream availability semantics as current `check_multi_worker_safety(...)`, including the Hub relay-stream client health.
5. Leader election is constructed from the started legacy `RedisService` that provides `set_nx` / `eval_script`, then injected into Hub as `LeaderElector | None`.
6. Hub with Hub repository, Hub response journal, durable task ownership store, Agent writer, Agent hub-status reader, Agent call-counter, Room publish authorization/lineage reader, Room cancellation reader, Delivery event publisher, Redis/relay protocols, and leader election dependency.
7. Execution with Hub dispatch port.
8. Post-construction internal-response binding: construct Hub, construct Execution, construct `HubInternalResponseRouter` with Execution sink and journal access, bind it into Hub publish service through `HubFacade.bind_internal_response_dispatcher(...)` or a narrow `HubInternalResponseDispatcher` port, then register the same router with Delivery for `"hub_agent_response_internal"`. Delivery must not import Hub or Execution.
9. Agent liveness receives `HubLivenessReader`.
10. `/health` preserves `relay_streams_available` semantics through a Hub health projection or the relay compatibility shim; no direct private `_streams` read remains outside the shim.
11. Startup sequencing calls `HubFacade.start()` only after legacy Redis, Redis Streams, Delivery, and leader dependencies are started, Delivery health is populated, the multi-worker guard has passed, and the internal router is constructed/registered. `HubFacade.start()` ensures journal indexes and durable ownership-store indexes, starts the ownership lease maintainer and hub response replay worker, runs immediate replay claims, then starts heartbeat monitoring before app traffic is accepted.

Run:

```bash
pytest tests/test_hub_runtime_bridge_facade.py tests/test_execution_protocols.py tests/test_multi_worker_safety.py -q
```

Expected: FAIL until wiring is updated.

- [ ] **Step 2: Wire Hub in app shell**

Add `HubDeps` to `container.py`, map existing app settings into `HubRuntimeBridgeConfig`, and build the facade from Mongo/Redis adapters, the config dataclass, injected `HubTaskOwnershipStore`, injected Redis protocols (`RedisStreams`/`RedisKV` or `HubRelayRedis`), the existing leader election dependency when available, existing `MetricsCollector`/`NoopMetricsCollector`, the traced task runner, the `OfflineHubFailurePort` implementation, `AgentDeps` support protocols (`AgentRegistryWriter`, `HubAgentStatusReader`, `AgentCallCounter`), and `RoomDeps` support protocols (publish authorization/lineage plus cancellation reader). Preserve startup ordering: start the legacy `RedisService`, start the separate Redis Streams client, start Delivery and populate Delivery health, construct `LeaderElection` from the legacy `RedisService`, run the multi-worker guard with the actual Delivery and Hub relay-stream health, then construct/start Hub before app traffic. Bind Agent liveness, pass Hub dispatch into Execution, construct the Hub internal-response router after Execution construction, bind that router into the Hub publish service, register the same router with Delivery, expose Hub health projection for `/health`, and call `HubFacade.start()` after router binding/registration so it ensures journal indexes and durable ownership-store indexes, starts the ownership lease maintainer and hub response replay worker through the traced task runner, runs immediate replay claims, then starts leader-gated heartbeat monitoring. Tests must prove services receive the injected ownership store and do not instantiate Mongo-backed ownership directly. Ensure shutdown stops the hub response replay worker, ownership lease maintainer, and Hub heartbeat/stream tasks before Delivery teardown.
Add a Hub-owned worker identity dependency to `HubDeps`/`HubRuntimeBridgeDeps`. Wiring tests must prove relay dispatch, `HubInternalResponseRouter`, `hub_response_replay_worker`, and `ownership_lease_maintainer` all use the same injected worker id for ownership records, journal claims, emit claims, and lease renewal; no component may rely on concrete `EventPublisherImpl.instance_id`.

- [ ] **Step 3: Verify wiring**

Run:

```bash
pytest tests/test_hub_runtime_bridge_facade.py tests/test_execution_protocols.py tests/test_multi_worker_safety.py -q
```

Expected: PASS.

### Task 10: App-Shell Legacy Workflow Collection Cleanup Readiness Gate

**Files:**
- Create: `database/migration/phase8_legacy_workflow_cleanup.py`
- Create/Update: `tests/fixtures/phase8_legacy_collection_cleanup.json`
- Create/Update: `tests/test_hub_runtime_bridge_protocols.py`
- Update: `main.py` only if cleanup readiness is app-lifecycle driven; do not wire cleanup through Hub startup.

- [ ] **Step 1: Write cleanup tests**

Assert discovery checks for active routers and live references before cleanup. Cleanup targets remain exactly:
- `base_tasks`
- `meta_tasks`
- `task_sessions`
- `chat_contexts`

Assert current `main` blocks cleanup while `api/orchestration_center.py` and `api/task.py` remain active. If a future branch has decommissioned those routes, assert cleanup is idempotent, logs counts, does not block Hub startup when disabled, and cannot drop arbitrary collections from config input.

Run:

```bash
pytest tests/test_hub_runtime_bridge_protocols.py -q
```

Expected: FAIL until the cleanup readiness gate is implemented.

- [ ] **Step 2: Implement guarded cleanup readiness**

Implement a named Phase 8 readiness gate outside HubRuntimeBridge. On current `main`, it must record blocker evidence and skip destructive cleanup. Only if the active-code gate passes may cleanup run as an explicit migration helper. Do not tie cleanup success to relay connectivity or Hub startup.

- [ ] **Step 3: Verify cleanup**

Run:

```bash
pytest tests/test_hub_runtime_bridge_protocols.py -q
```

Expected: PASS.

### Task 11: Final Static Gates and Regression Suite

**Files:**
- Update: `tests/test_phase8_hub_runtime_bridge_gate.py`
- Update: `tests/fixtures/phase8_hub_import_allowlist.json`
- Update: affected tests only.

- [ ] **Step 1: Run focused Phase 8 suite**

Run:

```bash
pytest \
  tests/test_phase8_hub_runtime_bridge_gate.py \
  tests/test_common_foundation.py \
  tests/test_hub_runtime_bridge_protocols.py \
  tests/test_agent_protocols.py \
  tests/test_room_protocols.py \
  tests/test_agent_facade.py \
  tests/test_agent_repository.py \
  tests/test_room_golden.py \
  tests/test_room_repository.py \
  tests/test_hub_runtime_bridge_facade.py \
  tests/test_hub_runtime_bridge_publish.py \
  tests/test_hub_runtime_bridge_agent_sync.py \
  tests/test_hub_runtime_bridge_relay_streams.py \
  tests/test_relay_streams.py \
  tests/test_hub_runtime_bridge_api_parity.py \
  tests/test_api_relay.py \
  tests/test_heartbeat_fixes.py \
  tests/test_multi_worker_safety.py \
  tests/test_dispatch_middleware.py \
  tests/test_call_counters.py \
  tests/test_execution_protocols.py \
  tests/test_execution_facade.py \
  tests/test_delivery_event_publisher.py \
  -q
```

Expected: PASS.

- [ ] **Step 2: Run full test suite**

Run:

```bash
pytest -q
```

Expected: PASS.

- [ ] **Step 3: Run import and packaging checks**

Run:

```bash
python -m compileall hub_runtime_bridge services/relay_service.py api/relay.py api/hub.py infrastructure/relay_streams.py
python - <<'PY'
import hub_runtime_bridge
import services.relay_service
import infrastructure.relay_streams
print("imports-ok")
PY
```

Expected: both commands exit 0 and print `imports-ok`.

- [ ] **Step 4: Final manual route inventory check**

Run:

```bash
python - <<'PY'
from api.relay import router as relay_router
from api.hub import router as hub_router
for route in [*relay_router.routes, *hub_router.routes]:
    print(sorted(route.methods), route.path)
PY
```

Expected: route list matches `tests/fixtures/phase8_hub_routes.json`; no route URL drift.

## Review Loop Log

This plan is intentionally reviewed by fresh Codex reviewer agents before completion. Each loop must review the whole plan against `docs/MODULAR_DECOUPLING_DESIGN.md` and current `main` code, then this file is the only editable artifact.

| Loop | Reviewer Result | Plan Change |
|------|-----------------|-------------|
| 1 | Issues found: unsafe legacy collection drop on current `main`, owner filtering on wrong side of internal-event boundary, idempotency not tied to run-events durability, missing `response_seq` source, incomplete publish authorization, missed `execution/dispatch/agent_message_processor.py`, and narrow boundary gates. | Added cleanup readiness gate instead of unconditional drop; added full Room publish-authorization protocol; added run-event journal adapter and legacy idempotency key fallback; added `HubInternalResponseRouter`; added `agent_message_processor.py` migration; broadened static gate forbidden imports. |
| 2 | Issues found: run-event journaling underspecified for current `models/run.py` shape and indexes; route-facing Hub API methods missing from protocol plan; Execution relay code still too inbound-aware; Hub liveness/offline mutation vague; internal `HubDeps` name conflicted with container `HubDeps`. | Added run-id/seq/event-shape/idempotency-index/replay requirements; added route-facing Hub API surface; required Execution relay to become outbound-only and remove direct Mongo/model/singleton imports; added Hub dispatch policy for pre-dispatch offline behavior; renamed internal dependency dataclass to `HubRuntimeBridgeDeps`. |
| 3 | Issues found: cancelled publish behavior missing; startup replay conflicted with in-memory owner filtering; run-event journal creation/index work split across tasks; frontend/internal publish mapping could duplicate frames; sync liveness ambiguity remained. | Added cancellation reader and no-side-effect cancelled publish tests; added atomic replay claim/processed marker that seeds/bypasses owner filtering after durable claim; moved journal/index implementation into Task 3; added event-type frontend/internal mapping and golden no-duplicate tests; documented sync liveness as cache-only and dispatch decisions as async. |
| 4 | Issues found: lifecycle `run_events` journaling could corrupt `(run_id, seq)` sequencing and startup healing; replay marking needed an awaited router path; legacy cleanup was incorrectly Hub-owned; API parity was too narrow; subpackage `__init__.py` files were missing. | Switched to sidecar `hub_response_journal`; clarified router/journal replay responsibilities; moved legacy cleanup to an app-shell/database migration artifact; expanded API parity requirements; added subpackage `__init__.py` inventory and packaging checks. |
| 5 | Issues found: Hub status needed Agent-owned counts; `hub_connection.py` lacked implementation tasks; publish lineage snapshot fields were missing from current DTOs; sync missed Redis heartbeat refresh; Delivery/router wording implied a boundary violation; frontend HubAgentEvent default was ambiguous. | Added `HubAgentStatusReader`; added Task 4B for connection lifecycle/status projection; added publish lineage snapshot protocol/DTO; required sync heartbeat refresh before Agent sync; reworded internal handler routing as app-shell registration with Delivery remaining generic; made no HubAgentEvent the default unless parity proves Hub owns a frame. |
| 6 | Issues found: live publish journal processed marking lacked correlation fields and awaited handling; owner filtering missed hub task-id aliases; journal file ordering was inconsistent; cleanup path used plural `migrations`; route binding was vague. | Added `journal_id`/`idempotency_key`/`run_id` internal-event correlation; required router to await sink and mark processed/dead-lettered; added ownership aliases by agent message, local pending task, and hub task id; moved journal creation to Task 3 and Task 7 updates it; corrected path to `database/migration`; added explicit Hub route dependency binding helpers. |
| 7 | Issues found: static design consistency gate contradicted intentional deviations; publish intake could change current sequential/awaited handling because Delivery schedules internal handlers asynchronously; one plural migration path remained. | Changed design gate to require documented/evidence-backed deviations, not literal consistency; added ordered, awaitable local router semantics and route 204 timing tests; corrected remaining cleanup path to `database/migration`. |
| 8 | Issues found: scope exclusion contradicted required Agent/Room/Execution support edits; Agent/Room protocol implementations lacked tasks; local direct routing plus `emit_internal` fanout could duplicate same-process handling; outbound dispatch DTO lacked current relay metadata; stale run-event wording remained. | Replaced broad exclusion with a narrow source-edit allowlist; added Task 3B for Agent `HubAgentStatusReader` and Room publish authorization/lineage implementations; added fanout-only or router-idempotency requirement; widened outbound hub dispatch DTO requirements and wire-envelope tests; renamed stale wording to sidecar hub response journaling. |
| 9 | Issues found: replay claims lacked leases; `/health` relay stream parity was omitted; resume query name should be `last_event_id`; fanout-only option conflicted with Delivery no-change boundary; legacy idempotency keys were inconsistent. | Added claim lease fields and lease-expiry crash tests; added `/health` `relay_streams_available` parity to wiring; corrected SSE query parity to `last_event_id`; mandated router idempotency instead of fanout-only Delivery edits; documented legacy fingerprinting for correlation. |
| 10 | Issues found: `HubFacade` could not satisfy both async and sync `is_hub_online`; `AgentMessageProcessor` injection path omitted `room_message_center.py` / factory; relay call-counter replacement protocol missing; Task 3 cleanup readiness was premature; startup replay sequencing not explicit. | Split facade/liveness from dispatch adapter signature shape; added orchestration construction files to allowed edits; added Agent-owned call-counter protocol and tests; removed cleanup readiness from Task 3; required `HubFacade.start()` after router registration to ensure journal indexes, replay claims, then heartbeat monitoring. |
| 11 | Issues found: static gate missed `modules.*` and legacy `models.*`; Redis dependency shape was underspecified; cancel/reply commands lacked concrete DTO fields. | Added `modules.*`/`models.*` boundary gates with adapter allowlists; required injected Redis protocols or `HubRelayRedis`; added `HubCancelCommand` and `HubReplyCommand` DTO requirements and unchanged wire-envelope tests. |
| 12 | Issues found: cancellation reader had no implementation path; heartbeat leader-election parity was missing; offline failure parity conflicted with A2A allowlist. | Added Room-owned cancellation reader implementation/tests; added `LeaderElector | None` dependency and leader-gated heartbeat tests; allowlisted minimal A2A task-status imports in `legacy_failure.py` or dict-preserving equivalent, with parity tests for failed task status, DB update, message text, and SSE error. |
| 13 | Issues found: outbound pending task-tracking persistence ownership was missing; cancellation protocol location still allowed Delivery; `emit_internal` fanout wording remained misleading. | Kept task tracking in Execution before Hub send or Room-owned `RoomAgentTaskTracker` fallback; moved cancellation reader definitively to Room protocols/snapshot and gated against Delivery cancellation APIs; clarified direct router handling/processed marking before any fanout and no fanout after failed direct route unless retry/dead-letter state is set. |
| 14 | Issues found: awaited router path had no post-construction binding seam; non-owner live publishes could be dropped or delayed until replay; dependency graph still pointed dispatch at `HubFacade`. | Added `HubInternalResponseDispatcher`/`bind_internal_response_dispatcher` wiring; added live ownership state machine for local owner, remote owner, and crashed/unknown owner; updated graph to route Execution through `HubDispatchAdapter + HubDispatchPolicy`, not directly through `HubFacade`. |
| 15 | Issues found: recovery could steal live remote-owner work without durable ownership leases; processing-status payloads did not require `lifecycle_message_id_verified=True`; static gate missed `common.config.settings`. | Added durable `hub_task_ownership` lease records keyed by aliases and recovery only after lease expiry; required normalized processing-status payloads to include verified lifecycle id fields with Execution adapter tests; broadened static gate to forbid `common.config`/direct settings imports. |
| 16 | Issues found: Hub wiring omitted new Agent/Room support protocols; durable ownership leases lacked renewal/release lifecycle; legacy idempotency fallback could drop identical events in one publish batch. | Extended AgentDeps/RoomDeps wiring and protocol tests; added ownership lease TTL, renewal, response refresh, and terminal cleanup requirements; added per-event legacy compatibility fingerprinting and duplicate identical-event tests. |
| 17 | Issues found: remote-owner duplicates could repeatedly fan out before owner processing; durable ownership store lacked index/setup requirements; Common root exports were incomplete. | Added internal emit claim state with expiry; added ownership-store unique alias and lease lookup indexes plus concurrency tests; added `common/dto/__init__.py` and complete root protocol export requirements. |
| 18 | Issues found: journal processed marking leaves a crash-after-Execution-side-effects duplicate window; ownership lease renewal lacked an assigned maintainer; missing-`run_id` behavior was vague. | Documented at-least-once sink delivery and crash-window tests; added `ownership_lease_maintainer` inventory, tests, and startup/shutdown wiring; defined per-event missing-`run_id` behavior with parity tests. |
| 19 | Issues found: local awaited dispatcher contradicted Delivery-only design wording; remote-owner fanout could remain stuck without restart; legacy fallback still deduped repeated single-event publishes; Task 6 omitted construction-path files. | Added explicit local-dispatch Known Deviation and protocol-only boundary tests; added `hub_response_replay_worker` retry scanning and wiring; changed legacy no-sequence events to unique ingest journal ids with no cross-request dedupe; added `room_message_center.py`/`factory.py` Task 6 coverage. |
| 20 | Issues found: offline failure adapter lacked boundary-safe dependency shape; Task 6 commands omitted `tests/test_execution_protocols.py`; journal index semantics could still collapse legacy repeats or collide on null stable keys. | Added injected offline failure persistence/error-frame protocol and static adapter gates; included `tests/test_execution_protocols.py` in Task 6 commands; specified unique `journal_id`, partial unique stable-id index, and legacy `dedupe_mode=none` semantics. |
| 21 | Issues found: remote-owner fanout lacked a journal claim before Execution; Redis/leader startup ordering and multi-worker guard parity were underspecified; publish normalization parity missed file-part and task-state mapping behavior. | Added remote-owner router claim-token flow and tests; added Redis Streams/leader startup ordering plus `check_multi_worker_safety` parity; added concrete file-part dedupe and task status/interactive normalization parity tests. |
| 22 | Issues found: non-owner workers could claim remote fanout before the owner; duplicate-publish wording conflicted with legacy no-dedupe behavior; multi-worker safety tests were omitted from wiring/focused suites. | Required ownership lease check before remote journal claim; qualified duplicate suppression to stable-id publishes only; added `tests/test_multi_worker_safety.py` to Task 9 and final focused suite. |
| 23 | Issues found: critical inventory files were not assigned to tasks; HubRepository heartbeat methods were too vague; existing `tests/test_relay_streams.py` was omitted. | Assigned dispatch adapter, task ownership, lease maintainer, replay worker, and API-key adapter to implementation tasks; added explicit HubRepository liveness methods; added `tests/test_relay_streams.py` to Task 4 and the final focused suite. |
| 24 | Issues found: Execution-facing wording allowed concrete `hub_runtime_bridge` imports; Relay shim compatibility omitted the `RelayService` class. | Reworded Execution dependencies to Common protocols with app-shell-injected concrete adapters and added a static gate against `execution/**` importing `hub_runtime_bridge.*`; added `RelayService` proxy export and shim import tests. |
| 25 | APPROVED. | No content change beyond recording the review result. |
| 26 | Issue found: Hub observability/tracing tasks were missing. | Added optional metrics collector dependency, dispatch/connection metric tests, and a static/runtime gate requiring background Hub workers to use the injected traced task runner instead of bare `asyncio.create_task`. |
| 27 | Issues found: offline failure port was not explicit or wired; ownership alias indexes needed partial unique filters; `connect_hub_stream` callable shape was ambiguous; final focused suite omitted Task 3B tests. | Added `OfflineHubFailurePort` DTO/protocol, binding, and parity tests; required partial unique alias indexes with missing-alias tests; locked `connect_hub_stream` to async-iterator consumption; expanded the focused suite with Common, Agent, and Room tests. |
| 28 | Issue found: `HubRuntimeBridgeDeps` still accepted raw app settings. | Replaced raw settings dependency with `HubRuntimeBridgeConfig` and added settings-to-config mapping gates limited to `container.py` / `hub_runtime_bridge/config.py`. |
| 29 | Issue found: `task_submitted` classification could duplicate frontend frames or skip internal aliasing. | Made `task_submitted` explicitly internal-only, added no-Hub-frontend assertion, and required tests proving Execution emits the submitted frame while Hub persists the task-id alias. |
| 30 | Issues found: leader election construction used the wrong Redis abstraction; Delivery health ordering before the multi-worker guard was ambiguous; Redis Streams append failure parity was missing. | Kept `LeaderElection` on legacy `RedisService`, required Delivery start/health before the guard, and added Redis-alive append-failure tests preserving current non-offline-failure behavior. |
| 31 | Issues found: tightened `HubAgentResponseInternal` DTO had no owning task; `HubFacade` was referenced before creation; Common Agent/Room DTO/protocol files were omitted from Task 2. | Added Common DTO/protocol files to Task 2, created a minimal `HubFacade` skeleton in Task 3, and made `tests/test_execution_facade.py` coverage unconditional for the internal-event contract. |
| 32 | APPROVED. | No content change beyond recording the review result. |
| 33 | Issues found: boundary gate missed direct Agent/Room/Delivery/DAL imports; package marker files were not assigned to a task. | Forbid direct `agent.*`, `room.*`, `delivery.*`, and concrete `dal.*` imports from HubRuntimeBridge; added service/transport/adapters `__init__.py` files to Task 3 skeleton work. |
| 34 | Issues found: relay shim still allowed old concrete RelayTransport/AgentResponseHandler construction; dispatch liveness shape remained either/or; Agent/Room repository tests and final execution protocol tests were omitted. | Added shim AST gates against old concrete imports/instantiation; fixed `HubDispatchPort.is_hub_online` as sync cache-only with async policy for authority; added Agent/Room repository tests and final `tests/test_execution_protocols.py` coverage. |
| 35 | Issues found: config mapping allowance contradicted settings import gate; durable task ownership store was not wired as a dependency; publish failure HTTP semantics were ambiguous. | Clarified `config.py` accepts passed settings without importing globals; added `HubTaskOwnershipStore` to deps/wiring with injection tests; specified publish failure 500 vs durable retry/dead-letter acceptance behavior. |
