# Orchestrator Production Cutover (Plan 4)

This document is the implementation plan for wiring the shared orchestrator
runtime (`execution.orchestrator`, `execution.orchestrator.a2a_runtime`,
`dal.orchestrator`) into the current Hybro product. It replaces the temporary
V2/V3 architecture branding and the "production-unbound" status with a
version-neutral, dark-launch-first production composition.

Everything below keys off the current repository state on the `plan4-cutover`
branch: the runtime contracts, kernel, A2A adapter layer, and Mongo stores are
already implemented and fully unit-tested; the production composition does not
exist yet.

## Product constraints

- **Single code path.** Fast and Ultimate share one Kernel. They differ only in
  resolved parameters (system prompt, model route, token/tool/time budgets,
  tool-execution policy). Do not add per-profile code forks. The profile is a
  parameter table: `profile_id` is a snapshot label, and future convergence of
  Fast/Ultimate is a configuration change, not a code change. Parameters are
  resolved at Run creation and frozen into `OrchestratorProfile`; they never
  change mid-Run.
- **Orchestrator role.** The backend never executes local tools. It recognizes
  intent through the kernel model turns and coordinates agents inside the
  resolved candidate scope. The only production `ToolRuntime` is
  `A2AAgentToolRuntime`; the only production `ToolCatalog` is the frozen agent
  catalog produced by `AgentToolCatalogAssembler`.
- **Reserved profile dimensions.** `initial_routing` and `finalization` are
  frozen per Run but not consumed by the kernel. Production pins
  `initial_routing=explicit_agent_first` (the API pre-filters the candidate
  scope) and `finalization=pass_through` (the final assistant message is
  delivered unchanged). `model_select` and `synthesize` are deferred product
  capabilities; `test_orchestrator_cutover_contracts.py` fails loudly if either
  is silently consumed.
- **Mode mapping.** The API accepts `direct|supervisor` today; the frontend
  already maps Fast→direct and Ultimate→supervisor
  (`frontend/src/lib/types/chat-mode.ts`). The backend boundary additionally
  accepts `fast|ultimate` and maps both pairs onto the same Kernel path:
  `fast/direct` and `ultimate/supervisor` are two rows of the profile parameter
  table. No second executor may exist behind either mode.

## 1. Version-neutral naming — DONE

Committed in `refactor: drop temporary V3 branding from the orchestrator
runtime`:

- `dal/orchestrator_v3/` → `dal/orchestrator/`; all imports, packaging, and
  test references updated atomically.
- The orchestrator test family and helpers renamed to neutral names.
- Module docstrings, error messages (`No orchestrator model route configured`),
  and System Architecture prose no longer mention V3 or the temporary plan
  phases.
- Naming guards added: owned surfaces and test module names must not contain
  `orchestrator_v3`, `orchestrator-v3`, or `Orchestrator V3`.

**Durable identity that must remain unchanged** (enforced by pinned-string
tests):

- All `schema_version` discriminators, including
  `OrchestratorRunState.schema_version = 5` and the legacy
  `ORCHESTRATION_RUN_SCHEMA_VERSION = 2`.
- `HITLRouteSnapshotV2` and its `schema_version: Literal[2]` wire/persistence
  discriminator in `common/dto/hitl.py`.
- The artifact write-lease owner `orchestrator-v3-a2a-artifact` and the
  `orchestrator-v3-a2a` origin-key namespace in `dal/orchestrator/artifacts.py`
  (SHA-256 idempotency preimage; renaming would duplicate owned artifacts).
- A2A/API/event protocol versions, migration versions, and any external
  compatibility version identifiers.

## 2. Production Composition Root

Build a composer that assembles the full runtime and expose it through a
narrow adapter implementing the existing
`RoomMessageCenterPort.process_room_user_message` contract, so routes and
`ExecutionFacade` do not fork.

### 2.1 Already implemented, needs production binding

| Component | Production binding |
|---|---|
| `GatewayModelRuntime` | existing `LLMGatewayImpl` (bound as the `LLMTurnGateway` contract); `route_configuration_from_gateway` separately resolves the profile's `ModelRouteConfiguration` |
| `AgentToolCatalogAssembler` | `AgentService`/`AgentResolver` candidate listing (see 2.2) |
| `A2AAgentToolRuntime` + call ledger | Mongo stores in `dal.orchestrator` |
| `RoomAgentSession` | in-process session host keyed by Room (see 2.3) |
| `A2AObservationIngress` | authenticators from 2.2 |
| `MongoOrchestratorRunStore` / `MongoOrchestratorEventStore` | `mongo.collection("orchestrator_runs" / "orchestrator_run_events")` |
| Recovery services / `A2ARecoveryCycle` | leader-elected jobs (section 7) |
| `RunCheckpointReader` | run store |
| `RoomFilesEpochFencedArtifactOwner` / `GuardedRoomFileArtifactWriter` | existing `file_storage` |

### 2.2 Missing production adapters (must be written)

- `AgentToolCandidateSource` over `AgentService`/`AgentResolver`: list in-scope
  candidates with card digest, endpoint scope digest, transport kind, direct
  capabilities, input/output modes; filter inactive/unauthorized/excluded.
- `AuthorizationRefreshPort` over existing room membership/selection checks.
- `HITLApplicationPort` over the existing durable HITL store: preserve route
  fingerprint, authenticated answerer, verified auth references; `answer_applied`
  is the idempotency marker. The production adapter must also keep the
  coordinator's replay rules intact: an applied answer whose continuation
  command is gone finalizes against the durable terminal winner, and a
  conflicting observation record must mark the call uncertain instead of
  applying evidence (`a2a_runtime/hitl.py`).
- `DirectA2AClient` over the `a2a_adapter` SDK client. `a2a_adapter` is the
  SDK anti-corruption layer: it is the only backend package allowed to import
  the `a2a` SDK (`a2a.types`, `a2a.client`; pinned by
  `test_a2a_adapter_does_not_import_orchestrator_policy`), so SDK types must
  not leak past `dispatch.py`'s provider-neutral boundary protocol.
- `RelayCommandJournal` / `RelayCommandSender` over `hub_runtime_bridge`.
  Note the journaling semantics: `hub_runtime_bridge` today journals only
  **inbound** hub→backend responses (`hub_response_journal.py`); the
  **outgoing** command journal (`persist_dispatch`/`persist_continuation`/
  `persist_cancellation` plus `inspect(command_id)` idempotency/dedupe, per
  the `RelayCommandJournal` protocol in `dispatch.py`) must be built new.
  `RelayCommandSender` maps onto `HubRelayService.send_to_hub` /
  `cancel_hub_task` / `reply_to_hub_task`, which currently push to the
  transport with no journaling.
- `ObservationIngressAuthenticator` implementations per source kind: webhook
  HMAC, relay identity. `RejectExternalIngressAuthenticator` remains the safe
  default until each source is enabled. The webhook HMAC authenticator must
  reuse the existing scheme — static `WEBHOOK_SIGNING_KEY` (≥ 32 bytes),
  per-message `secrets.token_urlsafe(32)` tokens stored only as HMAC-SHA256
  hex digests (`dal/runtime_store/parts/webhook_tokens.py`), verified with
  `hmac.compare_digest` — and bridge it to the orchestrator's
  `authenticate(source_kind, headers, body) -> source_identity` signature
  (`ports.py`). The existing route (`api_gateway/routes/webhook_routes.py`)
  reads the token from `X-A2A-Notification-Token` / `Authorization: Bearer`
  and looks up the stored hash by the `message_id` path parameter; do not
  invent a second HMAC key or scheme for the orchestrator ingress.
- `ResourceMaterializerPort` file source over `room_files`/attachment storage
  with the epoch-fenced artifact owner for inbound remote artifacts.
- Profile resolver: reads the Fast/Ultimate parameter table plus model/prompt
  configuration and resolves `OrchestratorProfile` snapshots per Run.

### 2.3 Session hosting

A process-local `RoomAgentSession` host (one active session per Room) that:

- resolves the profile/scope/epoch/catalog snapshot before Run creation;
- feeds `SessionEvent` lifecycle into the durable projection outbox, never
  directly into SSE;
- on shutdown cancels the in-process asyncio task directly without writing
  terminal state (matching `ExecutionFacade.cancel_inflight_tasks`
  graceful-shutdown semantics); note `RoomAgentSession.abort()` persists a
  terminal `canceled` status through the kernel, so it is the user-facing
  cancellation path, not the shutdown path;
- lets recovery workers re-enter a Run without the session object
  (`RunAddressedToolObservationSink` + kernel factory already support this).

## 3. Immutable Runtime Ownership

- `OrchestratorRunState.runtime_generation` (always `"orchestrator"` in its
  store) is persisted at Run creation — done, schema version 5. Legacy-owned
  Runs are identified by their absence from `orchestrator_runs`.
- Routing decision happens **only** at Run creation. A Run never switches
  runtime: restart, recovery, HITL answers, callbacks, and cancellation all
  return to the original owner.
- During the cutover, existing legacy Runs continue on the legacy executor.
  Rollback only affects new Runs; it never migrates in-flight Runs.
- Never recompute ownership from a live feature flag; flag changes must not
  change the owner of any existing Run.

## 4. Production entry points and dual routing

One ownership-aware dispatcher, used by every ingress:

| Entry point | Today | After cutover |
|---|---|---|
| `roomCenter/sendMessage` | legacy only | owner selection at creation; both engines via the adapter seam |
| Fast/Ultimate mapping | `direct\|supervisor` | accepted as compat input, mapped to the two profile rows at the boundary |
| A2A webhook (`/webhooks/a2a/{message_id}`) | legacy transport | correlation-based dispatch: ledger alias → orchestrator; message correlation → legacy |
| Hub relay events | legacy only | source-kind discriminating envelope; never feed both engines |
| HITL answers | legacy `HITLManager` | routed by persisted interaction/call ownership |
| Cancellation (SSE cancel) | legacy finalizer | `A2ACancellationCoordinator` for orchestrator calls; legacy path unchanged |
| Room deletion | legacy cleanup | epoch-fenced cancellation + cleanup (section 5) |
| Stale recovery jobs | legacy runs | `A2ARecoveryCycle` for orchestrator work; legacy jobs remain for legacy runs until drain |
| Artifact / final-message delivery | legacy delivery | projection outbox (section 6) |

Callback, HITL, and cancellation ingress for orchestrator Runs must correlate
by durable call/invocation lineage (ledger aliases, `runtime_generation`),
never by current flag state.

## 5. Room epoch lifecycle

- **Create**: after `RoomFacade.create_room()` persists the Room, activate the
  epoch with a stable creation identity. Activation failure must compensate
  (delete the just-created Room or leave it non-routable).
- **Delete**: after write drain, durably deactivate the epoch with the same
  `deletion_id`; cancel all non-terminal calls of that exact epoch under
  cleanup authorization; then delete all epoch-owned collections. Keep the
  epoch tombstone/high-water mark so a recreated Room increments the epoch and
  old-incarnation callbacks/artifacts stay rejected.
- Late callbacks and artifacts for a deactivated epoch are rejected before
  persistence (ingress already resolves lineage and the epoch store already
  verifies activity at dispatch/acceptance).
- Cleanup may only delete data; it must never reactivate or produce new side
  effects.
- Add the seven orchestrator collections to `room_owned_collections`:
  `orchestrator_runs`, `orchestrator_run_events`,
  `orchestrator_agent_tool_bindings`, `orchestrator_agent_calls`,
  `orchestrator_a2a_observations`, `orchestrator_a2a_observation_conflicts`,
  `orchestrator_room_epochs`.

## 6. Outbox projection, SSE, and public state

Kernel durable facts project to: Room messages, public Run/task status, SSE
timeline events, final answer, HITL pending/resolved state, agent execution
progress, and artifacts.

- Project **only** from the durable outbox (`ProjectionIntent` claims in
  `orchestrator_runs`). Projection is idempotent; SSE is never a fact source.
- Terminal visibility requires the mandatory projections (event append,
  final-message delivery, public terminal run status) to complete first;
  `transition_projection_settlement` already derives this state
  (`deliver_final_message` is mandatory only for `completed` Runs, per
  `_has_mandatory_terminal_intents` in `settlement.py`).
- Implement the production `ProjectionDriver`/worker:
  - claim with lease, renew, complete/block with backoff, poison quarantine;
  - `append_orchestrator_event` → `MongoOrchestratorEventStore`;
  - `deliver_final_message` → stable Room message with dedupe identity and
    `client_request_id` correlation;
  - `project_terminal_run_status` → existing public run/processing projection;
  - publish SSE only after durable Room/public projection; replay/catch-up
    reads DB projections, not SSE.
- Only terminal transitions mint `ProjectionIntent`s today (`settlement.py`).
  The session host's non-terminal `SessionEvent` feed (section 2.3) needs a
  new intent-minting path — or a direct event-append path — before lifecycle
  events can flow through the outbox.
- Worker crash mid-projection must replay harmlessly (unique indexes plus
  exact-winner re-reads already define the semantics).
- `ProjectionIntentStatus["blocked"]` is a terminal intent state
  (`PROJECTION_INTENT_TRANSITIONS["blocked"]` is empty in `settlement.py`).
  A blocked mandatory intent holds `projection_state == "blocked"`
  indefinitely, so the worker needs an operational requeue/replacement path
  for poisoned intents — otherwise the section 9 drain criterion ("projection
  intents reach zero") can never be satisfied.

## 7. Recovery workers

Bind `A2ARecoveryCycle` to the leader-elected background-job framework after
indexes and stores are ready. Preserve the required phase order:

1. cancellation reconciliation
2. HITL continuation recovery
3. observation inbox processing
4. A2A call recovery (abandoned Runs, accepted-but-not-dispatched,
   delivery-uncertain inspection, streaming/poll reconciliation)
5. artifact recovery
6. generic Run recovery (non-terminal `orchestrator_runs`)
7. projection outbox delivery
8. watchdog

Today `A2ARecoveryCycle` has exactly seven phases — cancellation,
continuation, observations, calls, artifacts, generic_runs, watchdog — with
watchdog pinned last by `test_recovery_cycle_keeps_watchdog_last`. Phase 7
(projection outbox delivery) is not in the class yet: the production binding
must extend the constructor with a `projection` phase between `generic_runs`
and `watchdog` (or bind projection delivery as a separate adjacent job) and
update the order-pinning test in the same commit.

Each phase must be isolated so one phase failure does not suppress later
cycles, while within-cycle order is preserved. `A2ARecoveryCycle.run_once`
currently iterates phases without per-phase exception isolation, so the
binding must wrap each phase (or harden `run_once`) to meet this requirement.
Multiple backend replicas must not duplicate side effects (lease/fencing
already enforced by the stores).

## 8. Feature flags and progressive rollout

Add typed settings (new settings section, not string flags):

- orchestrator runtime master switch
- Fast profile routing ratio; Ultimate profile routing ratio
- user/room allowlist
- emergency kill switch
- per-worker switches (recovery, projection, ingress)

Selection must use a stable hash or persisted choice so the same request never
lands on different runtimes across replicas. Recommended order:

1. Shadow/disabled composition validation (0% traffic, full construction)
2. Internal user canary
3. Fast small traffic
4. Fast full
5. Ultimate small traffic
6. Ultimate full
7. Stability observation window
8. Plan 5: legacy cleanup

### 8.1 Configuration and secrets

Enumerate cutover configuration before the first canary user:

- Webhook HMAC: reuse the existing `WEBHOOK_SIGNING_KEY` (≥ 32 bytes); never
  provision a second webhook key for the orchestrator authenticator (2.2).
- Relay identity: agent cards, endpoint scopes, and digests come from
  `AgentService`/`AgentResolver`; define the relay ingress authenticator's
  identity material before the relay source kind is enabled.
- LLM providers: the orchestrator adds no provider-specific secrets — it
  consumes the existing gateway settings (`openai_api_key`,
  `deepseek_api_key`) through `LLMGatewayConfig`.
- Redis (`REDIS_URL`): recovery/projection worker leader election is enabled
  only when the DAL Redis KV connects (`container.py`); under gunicorn the
  app refuses to start multi-worker without fully connected Redis. Never run
  orchestrator recovery workers without leader election — jobs then execute
  once per replica.
- Local vs production: document docker-compose vs production differences
  (replica count, Mongo topology, secrets manager) in the cutover runbook;
  the compose stack is the manual-test baseline, not the production shape.

### 8.2 Canary observability

Everything below derives from existing durable stores; no new fact sources.

- Orchestrator Run outcome rate per profile (success/failure/aborted) from
  `orchestrator_runs` status transitions.
- Projection intent backlog and blocked count. `blocked` is terminal
  (section 6), so any blocked mandatory intent needs operational
  requeue/replacement — alert on it, don't just watch it.
- Recovery cycle cadence: time between completed `A2ARecoveryCycle.run_once`
  cycles. `run_once` has no per-phase exception isolation yet (section 7), so
  one failing phase suppresses the whole cycle until the next tick — a
  stalled cycle means orphaned work across every phase.
- Ingress rejection rate. During dark launch every orchestrator ingress must
  reject (safe default); an acceptance before its source kind is enabled is a
  bug.
- Outstanding A2A calls (`orchestrator_agent_calls`) and
  `orchestrator_a2a_observation_conflicts` growth.
- Room-deletion cleanup failures (`room_files` cleanup over
  `room_owned_collections`).
- New-Run routing share per profile, confirming the ratio flags and the
  stable-hash selection behave as configured.

Suggested initial thresholds (tune before launch): Run failure rate > 1%
over 5 minutes; any blocked mandatory intent > 10 minutes; recovery cycle
age beyond twice the normal cadence; ingress 5xx rate > 0.5%; sustained
observation-conflict growth. Each threshold maps to a named owner in the
execution order below.

## 9. Rollback and drain

- Stop assigning new Runs to the orchestrator runtime (flag off or ratio 0).
- Runs already owned by the orchestrator runtime keep completing; their
  recovery, ingress, HITL, cancellation, and projection workers keep running.
- Legacy and orchestrator workers may run simultaneously.
- Never hand an orchestrator checkpoint to the legacy executor, and never
  change an existing Run's owner via feature flag.
- Keep ingress/recovery/projection workers alive until orchestrator non-
  terminal Runs, pending inbox rows, HITL continuations, cancellations,
  artifacts, and projection intents reach zero. Only then may worker groups
  stop.
- **Rollback triggers** — the cutover DRI stops assignment first (kill
  switch, then diagnose) whenever a canary threshold in 8.2 breaks, dual
  routing misroutes a delivery (wrong-owner answer), a duplicate final
  message appears, or any other data-integrity incident is attributable to
  the orchestrator. After diagnosis, choose between ratio reduction and full
  rollback; both reuse the mechanics above.
- The seven `room_owned_collections` registrations (section 5) stay in
  place on rollback: they are inert while assignment is off, and removing
  them would churn indexes and break the drain. They are removed only in
  Plan 5 cleanup.

## 10. Validation and acceptance

Must cover at least:

- Fast and Ultimate both traverse the same Kernel.
- New Run owner is persisted and immutable (`runtime_generation`).
- Restart resumes into the correct runtime.
- Callback/HITL/cancel routing by persisted ownership.
- Room deletion vs late callback race; epoch deactivation rejects late writes;
  recreated Room increments epoch.
- Terminal winner vs cancellation race.
- Outbox replay, duplicate delivery, worker crash during projection.
- Multi-replica Mongo lease/CAS behavior.
- Canary rollback leaves in-flight Runs untouched.
- SSE and final Room state converge from DB projections.
- Docker full-stack real Fast and Ultimate A2A flows.

Required gates:

- backend full test suite
- Ruff format/check
- frontend lint/tests/build
- live Mongo concurrency tests promoted into CI (currently opt-in via
  `HYBRO_TEST_LIVE_MONGO=1`; index provisioning must move into startup)
- `docker compose up -d --build` and `/health`
- real Fast and Ultimate E2E through the product UI

The `test_orchestrator_package_is_distributable_but_not_bound_to_production`
gate is replaced by coexistence invariants (persisted-ownership routing,
flag-off zero traffic, no mid-run owner switching) in the same commit that
first wires `container.py`.

## Execution order

1. **DONE** — pre-cutover contracts (ownership schema, event stores, reserved
   profile semantics, durable-identity pins).
2. **DONE** — version-neutral naming.
3. Persistence wiring: create all seven collections and their indexes through
   the startup registry; unique-index conflicts are fatal; add collections to
   `room_owned_collections`; wire Mongo epoch store.
4. Room epoch lifecycle: create activation, delete deactivation/tombstone,
   exact-epoch cleanup; enforce the fenced deletion path (the fallback without
   `file_lifecycle` is incompatible with epoch-safe deletion).
5. Missing production adapters (2.2) and the dark-launch composition
   (0% traffic) with full construction validation; replace the architecture
   gate in the same commit.
6. Projection outbox worker and concrete projectors; recovery cycle binding.
7. Dual-routing ingress (webhook/relay/HITL/cancel/recovery) keyed by
   persisted ownership, plus `fast|ultimate` mode acceptance at the API
   boundary (section 4 mode mapping).
8. Feature flags and canary rollout.
9. Rollback manual exercise + acceptance matrix (section 10).

Steps 3–9 each need a named DRI and an explicit exit criterion (the
corresponding section 10 gate or 8.2 threshold). The cutover DRI owns the
canary on-call during steps 8–9 and the rollback decision in section 9.

## Plan 5 boundaries (out of scope)

- Removing `SupervisorExecutor`/`QueueExecutor` production branches and
  compatibility adapters (after legacy-owned Runs drain).
- API/product renaming of `direct|supervisor` (frontend keeps its mapping
  until then).
- Legacy collection/field cleanup and migrations.
- Removing dual callback/HITL routing.
- Dropping any remaining `dal.orchestrator` compatibility re-exports or
  historical durable identity (the `orchestrator-v3-a2a` artifact namespace
  may remain forever as durable data identity).
