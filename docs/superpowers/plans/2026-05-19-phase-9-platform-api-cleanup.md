# Phase 9 Platform API Cleanup Implementation Plan

> **For agentic workers:** REQUIRED: Use `superpowers:subagent-driven-development` if subagents are available, or `superpowers:executing-plans` to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the modular decoupling migration by extracting Platform services behind protocols, making `api/` a thin route-adapter layer, and removing post-migration compatibility shims, singleton imports, and obsolete `modules/` / `services/` code.

**Architecture:** Phase 9 starts after Phase 8 HubRuntimeBridge has landed and all earlier module facades are container-bound. Create a real `platform_module/` package for gateway, rate limiting, file upload, and content storage behavior, then convert route modules to dependency-injected adapters that call Common protocols or module facades. Finish by deleting legacy compatibility wrappers only after static gates and parity tests prove no runtime code imports them.

**Tech Stack:** Python 3.11+, FastAPI, pytest, pytest-asyncio, existing Mongo/Redis/S3 DAL protocols, Common DTOs/protocols, module facades from Phases 3-8, AST import-boundary tests, no new dependencies.

---

## Scope

Include:
- Implement `platform_module/` around existing Platform protocols: `GatewayService`, `RateLimiter`, and `FileStorage`.
- Preserve public route URLs, HTTP methods, auth dependencies, request/response models, status codes, headers, streaming formats, file validation behavior, and error payloads.
- Convert `api/` modules into thin adapters. Route handlers may parse HTTP input, call auth dependencies, fetch container-bound protocols, map domain errors to HTTP responses, and format responses. They must not import concrete services, concrete repositories, `database.mongodb`, `modules.*`, or global singleton instances.
- Wire Platform in `container.py` and `main.py` alongside existing Agent, Room, ContextMemory, Delivery, Execution, and HubRuntimeBridge facades.
- Remove old compatibility shims and old implementation directories only after no production code or tests require them.
- Enforce all final import-linter contracts from `docs/MODULAR_DECOUPLING_DESIGN.md`: SDK confinement, protocol-only cross-module dependencies, no singleton service imports, and no direct business imports from Delivery.
- Delete legacy workflow routes and code only if the Phase 0d/8 readiness evidence proves no active frontend traffic and no live code references remain. Otherwise leave a blocked cleanup manifest and fail the final "no old code" gate intentionally until the project owner resolves decommission readiness.

Exclude:
- Do not change frontend-visible API schemas or route paths.
- Do not introduce DBOS, AG-UI, OpenTelemetry, new queues, new SDKs, or new storage technology.
- Do not rewrite business logic already owned by Agent, Room, ContextMemory, Delivery, Execution, HubRuntimeBridge, A2A Adapter, LLM Gateway, or DAL.
- Do not remove legacy code by broad directory deletion until import scans, route inventory tests, and parity tests identify every remaining dependency.
- Do not drop Mongo collections from app startup. Collection cleanup must remain an explicit migration with readiness evidence and backup/rollback notes.

## Preconditions

- Phase 8 code has landed on the current branch. Required source packages exist: `agent/`, `room/`, `context_memory/`, `delivery/`, `execution/`, `hub_runtime_bridge/`, `a2a_adapter/`, `llm_gateway/`, and `dal/`.
- `container.py` creates all module facades and binds old adapters where earlier phases still need compatibility.
- Phase 8 cleanup manifest documents whether legacy workflow collections and endpoints are safe to remove.
- Existing focused suites for Agent, Room, ContextMemory, Delivery, Execution, HubRuntimeBridge, Gateway, Files, Discovery, API keys, and SSE pass before Phase 9 begins.
- The implementation work stays on the current branch unless the project owner explicitly asks for a branch change.

## Current Repo Check

As of 2026-05-19 on `main`:
- `common/protocols/platform_protocols.py` already defines `GatewayService`, `RateLimiter`, and `FileStorage`.
- `platform_module/` exists on disk but has no tracked implementation files.
- `api/gateway.py` imports `services.gateway_service.gateway_service` and `services.gateway_rate_limit_service.gateway_rate_limit_service`.
- `api/files.py` imports `services.file_upload_service.file_upload_service` and `api.room_center.verify_room_ownership`.
- Several API modules still import `modules.*`, `services.*`, or `database.mongodb` directly.
- `services/gateway_service.py` imports `a2a.types`, `config.settings`, `database.mongodb`, `services.a2a_service`, `services.discovery_service`, and `services.rate_limit_service`.
- `services/file_upload_service.py` imports `database.mongodb`, `config.settings`, and lazily imports `services.s3_service`.
- `pyproject.toml` packages include old `modules`, `services`, `config`, and `infrastructure` packages, but not `platform_module`.

## File Inventory

Create:
- `platform_module/__init__.py`: public exports for `PlatformFacade`, `PlatformDeps`, and helper adapters.
- `platform_module/deps.py`: `PlatformDeps` dataclass for injected Agent, A2A Adapter, Discovery/Agent matching, RateLimiter stores, FileStorage repository, ObjectStorage, settings-derived scalar config, clock, and logger dependencies.
- `platform_module/config.py`: narrow `PlatformConfig` dataclass created from app settings by the container. This file must not import global `settings`.
- `platform_module/facade.py`: implements high-level Platform facade methods and exposes `GatewayService`, `RateLimiter`, and `FileStorage` protocol implementations.
- `platform_module/gateway.py`: gateway card masking, access checks, direct-call guard for hub agents, sync send, streaming send, and discovery response masking.
- `platform_module/rate_limit.py`: shared per-key, global, per-user, and per-agent rate-limit logic over injected Redis/DAL abstractions.
- `platform_module/files.py`: upload validation, magic-byte detection, S3/ObjectStorage write, metadata persistence, compensating delete, presigned URL lookup, delete, and room file listing.
- `platform_module/content_storage.py`: Platform-owned implementation for full-content object storage, with a protocol injected into ContextMemory if ContextMemory needs to read or write full-content references.
- `platform_module/translators.py`: conversions between route models and Common DTOs where direct model reuse would leak legacy service types.
- `platform_module/adapters/__init__.py`: package marker for route/model compatibility adapters.
- `platform_module/adapters/legacy_models.py`: route-facing translation for `models.gateway`, `models.file_upload`, and API-key model compatibility.
- `tests/test_platform_module_protocols.py`: protocol conformance, package registration, import-boundary gates, and forbidden singleton checks.
- `tests/test_platform_gateway.py`: gateway discovery/card/send/stream parity, access control, hub-agent 502 guard, rate-limit behavior, and upstream error mapping.
- `tests/test_platform_files.py`: upload MIME/size/magic validation, S3/ObjectStorage interactions, Mongo metadata persistence, compensating delete, URL generation, delete, and list behavior.
- `tests/test_platform_content_storage.py`: full-content hash/upsert, reference expansion, TTL/expiry behavior, object-storage fallback, and compatibility shim parity.
- `tests/test_platform_rate_limit.py`: shared rate-limit counter behavior for gateway, discovery, and per-agent limits.
- `tests/test_api_thin_adapters.py`: AST gate proving API modules are thin adapters and route handlers do not import concrete services/modules/database globals.
- `tests/test_phase9_cleanup_gate.py`: final cleanup gate for old directories, singleton imports, package list, migration adapters, and import-linter contracts.
- `tests/fixtures/phase9_api_routes.json`: expected public route inventory, auth dependency, protocol dependency, and response model mapping.
- `tests/fixtures/phase9_import_allowlist.json`: temporary path-level allowlist for route compatibility imports with owner and expiry notes.
- `tests/fixtures/phase9_cleanup_manifest.json`: explicit old-code deletion manifest, blocked cleanup entries, legacy workflow readiness evidence, and package-removal checklist.

Modify:
- `common/protocols/platform_protocols.py`: align protocol method names with the existing gateway/file route surface without leaking A2A SDK or FastAPI request types.
- `common/dto/platform.py` or existing Common DTO files: add immutable DTOs only where route models cannot cross module boundaries safely.
- `common/dto/__init__.py`: export new Platform DTOs.
- `common/protocols/__init__.py`: export Platform protocol surfaces.
- `container.py`: add `PlatformDeps`, `create_platform_facade(...)`, and dependency providers for gateway/rate-limit/file storage.
- `main.py`: create Platform after DAL, Agent, A2A Adapter, LLM Gateway, Room, ContextMemory, Delivery, Execution, and Hub are available; pass Platform dependencies into route setup without importing concrete services.
- `api/gateway.py`: keep route URLs and response behavior while depending on container-bound `GatewayService` and `RateLimiter` protocols.
- `api/discovery.py`: route through Platform or Agent protocol dependencies instead of `services.discovery_service` and `services.discovery_rate_limit_service`.
- `api/discovery_api_keys.py`: keep API-key management behavior but remove direct concrete persistence imports where a DAL/repository protocol exists.
- `api/files.py`: route through `FileStorage` plus Room ownership protocol instead of `file_upload_service` and `api.room_center.verify_room_ownership`.
- `api/agent.py`, `api/agent_viewset.py`, `api/viewset.py`, `api/agent_group.py`, `api/room_center.py`, `api/memory_center.py`, `api/inspection_center.py`, `api/hitl.py`, `api/a2a_tasks.py`, `api/webhooks.py`, `api/relay.py`, `api/hub.py`, `api/sse.py`: remove concrete singleton imports and use container-bound protocols/facades or route adapters.
- `api/orchestration_center.py` and `api/task.py`: delete only if legacy workflow decommission readiness is proven; otherwise replace with explicit 410 Gone routes and document the blocker in the cleanup manifest.
- `common/api_key_auth.py`: remove direct `database.mongodb` access by depending on an app-shell-bound API-key validation/usage protocol.
- `common/auth.py`: remove direct `config.settings` access by accepting app-shell-bound auth/JWT configuration.
- `common/middleware/discovery_cors_middleware.py`: remove direct `config.settings` access by accepting injected CORS/config values during app construction.
- `common/utils/a2a_helpers.py`: remove lazy `services.s3_service` and `config.settings` imports by moving URL signing/storage behavior behind Platform/ObjectStorage protocols or route-adapter helpers.
- `common/utils/context_utils.py`: remove lazy `services.openai_service` import by moving token/model helper behavior behind LLM Gateway or injected utility functions.
- `services/gateway_service.py`, `services/gateway_rate_limit_service.py`, `services/discovery_rate_limit_service.py`, `services/rate_limit_service.py`, `services/file_upload_service.py`, `services/content_storage_service.py`: replace with fail-fast or delegating compatibility shims during Task 2-4, then delete in Task 8 after all imports are gone.
- `services/__init__.py`: remove exports of deleted service singletons.
- `modules/__init__.py`: remove exports of deleted module singletons.
- `pyproject.toml`: add `platform_module` and `platform_module.adapters`; remove `modules`, `services`, legacy `config`, and obsolete `infrastructure` packages only when no runtime code imports them.
- Existing tests under `tests/test_api_gateway.py`, `tests/test_api_discovery.py`, `tests/test_api_discovery_api_keys.py`, `tests/test_file_upload.py`, `tests/test_api_agent.py`, `tests/test_api_agent_group.py`, `tests/test_api_room_center.py`, `tests/test_api_memory.py`, `tests/test_api_hitl.py`, `tests/test_api_a2a_tasks.py`, `tests/test_api_webhooks.py`, `tests/test_api_relay.py`, `tests/test_api_sse.py`, and route-specific golden tests as needed for dependency injection and final path names.
- `docs/MODULAR_DECOUPLING_DESIGN.md`: implementation may update Phase 9 status after code lands. This plan-writing task must not edit it.

Delete after gates prove safe:
- `modules/` legacy implementation package.
- `services/` legacy implementation package.
- Legacy `config/` package if all code uses `common.config` or injected `PlatformConfig`.
- Obsolete `infrastructure/` files whose implementations moved into DAL, Delivery, or HubRuntimeBridge.
- Legacy workflow code and routes if decommission readiness is proven.

Reference-only:
- `docs/MODULAR_DECOUPLING_DESIGN.md` sections 3.3, 8.2, 8.3, 9, 10, 11, and 14.
- `docs/superpowers/plans/2026-05-17-phase-7-execution-module.md`.
- `docs/superpowers/plans/2026-05-18-phase-8-hub-runtime-bridge.md`.
- `services/gateway_service.py`.
- `services/rate_limit_service.py`.
- `services/gateway_rate_limit_service.py`.
- `services/discovery_rate_limit_service.py`.
- `services/file_upload_service.py`.
- `services/content_storage_service.py`.
- `api/gateway.py`.
- `api/files.py`.
- `api/discovery.py`.
- `api/room_center.py`.
- `api/webhooks.py`.
- `api/a2a_tasks.py`.
- `container.py`.
- `main.py`.
- `pyproject.toml`.

## Dependency Shape

```text
api/*
  -> route dependency helpers
  -> common.protocols.* or module facade protocols
  -> no services.*, modules.*, database.mongodb, config.settings, concrete delivery/execution/hub imports

platform_module/**
  -> common.dto / common.protocols / common.errors
  -> injected AgentRegistry / AgentMatcher / AgentManagement as needed
  -> injected A2A AgentTransport and card resolver protocols
  -> injected DAL repository / Redis / ObjectStorage protocols
  -> injected PlatformConfig scalar config
  -> no FastAPI request objects, no API modules, no main/container imports

container.py / main.py
  -> only app shell files allowed to import concrete module implementations
  -> owns final construction and route dependency binding
```

## Known Deviations / Deferred Target Architecture

- The Python package should be named `platform_module`, not `platform`, to avoid shadowing the Python standard-library `platform` module. Protocol names can still use "Platform" terminology.
- Some route-facing Pydantic models may remain under `models/` in Phase 9 if they are public API schema contracts. Moving schemas is optional; behavior and OpenAPI stability matter more than path purity.
- `api/` can depend on FastAPI, route models, auth dependencies, and Common protocols. It should not be forced to become dependency-free.
- Deleting legacy workflow code depends on real decommission evidence. If active routes or collection references remain, Phase 9 must keep a blocked manifest rather than pretending cleanup is complete.
- Compatibility shims may exist during intermediate tasks, but final acceptance requires either deletion or an explicit blocked manifest entry with owner, reason, and expiry.

## Tasks

### Task 1: Baseline Inventory And Static Gates

**Files:**
- Create: `tests/test_phase9_cleanup_gate.py`
- Create: `tests/test_api_thin_adapters.py`
- Create: `tests/fixtures/phase9_api_routes.json`
- Create: `tests/fixtures/phase9_import_allowlist.json`
- Create: `tests/fixtures/phase9_cleanup_manifest.json`
- Reference: `docs/MODULAR_DECOUPLING_DESIGN.md`

- [ ] **Step 1: Record branch and baseline status**

Run:

```bash
git branch --show-current
git status --short
```

Expected: implementation remains on the current branch requested by the project owner; status contains only intentional Phase 9 files.

- [ ] **Step 2: Capture current API route inventory**

Run:

```bash
python - <<'PY'
from main import app
for route in sorted(app.routes, key=lambda r: getattr(r, "path", "")):
    methods = ",".join(sorted(getattr(route, "methods", []) or []))
    print(f"{methods} {getattr(route, 'path', '')} {getattr(route, 'name', '')}")
PY
```

Expected: output is copied into `tests/fixtures/phase9_api_routes.json` with route path, methods, name, auth dependency, owning module protocol, and expected response model.

- [ ] **Step 3: Add failing API thin-adapter gate**

Add an AST test that scans `api/**/*.py` and fails on imports from:

```python
FORBIDDEN_API_IMPORT_PREFIXES = (
    "database",
    "modules",
    "services",
    "delivery",
    "execution",
    "hub_runtime_bridge",
    "agent.repository",
    "room.repository",
    "context_memory.repository",
)
```

Allow only exact temporary entries in `tests/fixtures/phase9_import_allowlist.json`.

- [ ] **Step 4: Add failing cleanup gate**

Add a test that fails if final cleanup is claimed while production files still import old service/module singletons or while `pyproject.toml` still packages deleted directories.

Run:

```bash
pytest tests/test_api_thin_adapters.py tests/test_phase9_cleanup_gate.py -q
```

Expected: FAIL with concrete current imports from API modules and cleanup blockers.

- [ ] **Step 5: Commit baseline gates**

```bash
git add tests/test_api_thin_adapters.py tests/test_phase9_cleanup_gate.py tests/fixtures/phase9_api_routes.json tests/fixtures/phase9_import_allowlist.json tests/fixtures/phase9_cleanup_manifest.json
git commit -m "test: add phase 9 platform cleanup gates"
```

### Task 2: Platform Module Foundation

**Files:**
- Create: `platform_module/__init__.py`
- Create: `platform_module/deps.py`
- Create: `platform_module/config.py`
- Create: `platform_module/facade.py`
- Create: `platform_module/translators.py`
- Create: `platform_module/adapters/__init__.py`
- Create: `platform_module/adapters/legacy_models.py`
- Modify: `common/protocols/platform_protocols.py`
- Modify: `common/protocols/__init__.py`
- Modify: `common/dto/__init__.py`
- Modify: `pyproject.toml`
- Test: `tests/test_platform_module_protocols.py`

- [ ] **Step 1: Write protocol conformance tests**

Test that `PlatformFacade` exposes gateway, rate limiter, and file storage objects satisfying the Common protocols, and that `platform_module/**` does not import FastAPI route modules, `services.*`, `modules.*`, `database.mongodb`, or global `settings`.

Run:

```bash
pytest tests/test_platform_module_protocols.py -q
```

Expected: FAIL because `platform_module` implementation is absent.

- [ ] **Step 2: Add Platform config and deps**

Create `PlatformConfig` with scalar fields for:
- `gateway_base_url`
- gateway/discovery per-key and global rate limits
- per-agent rate-limit window seconds
- max upload size bytes
- allowed MIME types
- presigned URL TTL

Create `PlatformDeps` with injected protocol dependencies only. No field should be a concrete service singleton.

- [ ] **Step 3: Add facade skeleton**

Create `PlatformFacade` with explicit constructor dependencies and properties or methods that expose:
- `gateway_service`
- `gateway_rate_limiter`
- `discovery_rate_limiter`
- `agent_rate_limiter`
- `file_storage`

The skeleton can raise `NotImplementedError` for behavior not yet migrated.

- [ ] **Step 4: Register package**

Add `platform_module` and `platform_module.adapters` to `pyproject.toml`.

- [ ] **Step 5: Verify foundation tests**

Run:

```bash
pytest tests/test_platform_module_protocols.py -q
```

Expected: PASS for package registration, constructor shape, protocol conformance stubs, and import boundaries.

- [ ] **Step 6: Commit Platform foundation**

```bash
git add platform_module common/protocols/platform_protocols.py common/protocols/__init__.py common/dto/__init__.py pyproject.toml tests/test_platform_module_protocols.py
git commit -m "feat: add platform module foundation"
```

### Task 3: Rate Limit Extraction

**Files:**
- Create: `platform_module/rate_limit.py`
- Modify: `platform_module/facade.py`
- Modify: `platform_module/deps.py`
- Modify: `services/rate_limit_service.py`
- Modify: `services/gateway_rate_limit_service.py`
- Modify: `services/discovery_rate_limit_service.py`
- Test: `tests/test_platform_rate_limit.py`

- [ ] **Step 1: Write focused rate-limit parity tests**

Cover:
- per-agent per-user limit
- per-agent system limit
- gateway per-key limit
- gateway global limit
- discovery per-key limit
- discovery global limit
- disabled limits allow requests
- `Retry-After` values match existing behavior

Run:

```bash
pytest tests/test_platform_rate_limit.py -q
```

Expected: FAIL because Platform rate-limit implementation is absent.

- [ ] **Step 2: Implement shared rate limiter**

Move counter-key construction, window handling, and result DTO mapping behind `platform_module/rate_limit.py`. Use injected Redis/DAL clock dependencies; do not import Redis globals.

- [ ] **Step 3: Add compatibility shims**

Update old rate-limit service files to delegate to the bound Platform facade or fail fast before binding. Do not keep duplicate counter logic.

- [ ] **Step 4: Verify rate-limit parity**

Run:

```bash
pytest tests/test_platform_rate_limit.py tests/test_api_gateway.py tests/test_api_discovery.py -q
```

Expected: PASS with unchanged HTTP rate-limit status, payload, and headers.

- [ ] **Step 5: Commit rate-limit extraction**

```bash
git add platform_module/rate_limit.py platform_module/facade.py platform_module/deps.py services/rate_limit_service.py services/gateway_rate_limit_service.py services/discovery_rate_limit_service.py tests/test_platform_rate_limit.py tests/test_api_gateway.py tests/test_api_discovery.py
git commit -m "feat: extract platform rate limiting"
```

### Task 4: Gateway Extraction

**Files:**
- Create: `platform_module/gateway.py`
- Modify: `platform_module/facade.py`
- Modify: `platform_module/deps.py`
- Modify: `platform_module/adapters/legacy_models.py`
- Modify: `services/gateway_service.py`
- Modify: `api/gateway.py`
- Test: `tests/test_platform_gateway.py`
- Test: `tests/test_api_gateway.py`

- [ ] **Step 1: Write gateway parity tests**

Cover:
- discovery returns gateway-masked card URLs
- `GET /gateway/agents/{agent_id}/card` masks all supported card URL fields
- private agent access returns 403
- missing or inactive agent returns 404
- hub-sourced agent direct send/stream returns 502
- upstream A2A send failures return 502 with existing error shape
- stream emits `data: ...\n\n` frames and records the API-key request in `finally`

Run:

```bash
pytest tests/test_platform_gateway.py tests/test_api_gateway.py -q
```

Expected: FAIL before implementation.

- [ ] **Step 2: Move gateway behavior into Platform**

Implement gateway behavior with injected protocols:
- Agent lookup and visibility through Agent protocols
- A2A send/stream through adapter protocol, not `a2a.types` outside adapter boundaries
- Discovery through Agent matching or existing protocol seam
- Rate limiting through Platform rate limiter
- gateway URL masking through `PlatformConfig.gateway_base_url`

- [ ] **Step 3: Convert API route to thin adapter**

`api/gateway.py` should depend on route dependency helpers returning `GatewayService` and `RateLimiter` protocols. It must not import `services.gateway_service` or concrete Platform files directly unless the import allowlist documents a short-lived route-binding adapter.

- [ ] **Step 4: Keep old gateway shim temporary**

Make `services/gateway_service.py` delegate to Platform only for tests or imports that have not yet moved. Add the file to the cleanup manifest with Task 8 deletion.

- [ ] **Step 5: Verify gateway tests and API gate**

Run:

```bash
pytest tests/test_platform_gateway.py tests/test_api_gateway.py tests/test_api_thin_adapters.py -q
```

Expected: PASS for gateway files; remaining API thin-adapter failures list other route modules only.

- [ ] **Step 6: Commit gateway extraction**

```bash
git add platform_module/gateway.py platform_module/facade.py platform_module/deps.py platform_module/adapters/legacy_models.py services/gateway_service.py api/gateway.py tests/test_platform_gateway.py tests/test_api_gateway.py tests/test_api_thin_adapters.py tests/fixtures/phase9_import_allowlist.json tests/fixtures/phase9_cleanup_manifest.json
git commit -m "feat: extract platform gateway"
```

### Task 5: File Storage Extraction

**Files:**
- Create: `platform_module/files.py`
- Modify: `platform_module/facade.py`
- Modify: `platform_module/deps.py`
- Modify: `services/file_upload_service.py`
- Modify: `api/files.py`
- Modify: `api/room_center.py` only if ownership helper must move behind Room protocol
- Test: `tests/test_platform_files.py`
- Test: `tests/test_file_upload.py`

- [ ] **Step 1: Write file-storage parity tests**

Cover:
- allowed and rejected MIME types
- upload size limit
- magic-byte mismatch rejection
- MP4/WebM/Office ZIP compatibility cases
- S3/ObjectStorage upload call
- Mongo metadata write through repository/DAL protocol
- compensating object delete on metadata failure
- presigned URL generation
- file delete and list-for-room behavior required by `FileStorage`

Run:

```bash
pytest tests/test_platform_files.py tests/test_file_upload.py -q
```

Expected: FAIL until Platform file storage is implemented.

- [ ] **Step 2: Implement file storage**

Move validation and persistence into `platform_module/files.py`. Use injected object storage and metadata repository/DAL protocol. Do not import `services.s3_service`, `database.mongodb`, or global settings.

- [ ] **Step 3: Convert files route**

Make `api/files.py` call `FileStorage.upload(...)` after verifying room ownership through a Room protocol dependency. The route keeps `multipart/form-data`, `room_id` form field, Clerk auth, response shape, and status behavior.

- [ ] **Step 4: Keep old file upload shim temporary**

Update `services/file_upload_service.py` to delegate to Platform or fail fast. Add deletion to Task 8 cleanup manifest.

- [ ] **Step 5: Verify file tests and API gate**

Run:

```bash
pytest tests/test_platform_files.py tests/test_file_upload.py tests/test_api_thin_adapters.py -q
```

Expected: PASS for file route; remaining API thin-adapter failures list non-Platform route modules only.

- [ ] **Step 6: Commit file extraction**

```bash
git add platform_module/files.py platform_module/facade.py platform_module/deps.py services/file_upload_service.py api/files.py api/room_center.py tests/test_platform_files.py tests/test_file_upload.py tests/test_api_thin_adapters.py tests/fixtures/phase9_import_allowlist.json tests/fixtures/phase9_cleanup_manifest.json
git commit -m "feat: extract platform file storage"
```

### Task 5b: Content Storage Extraction

**Files:**
- Create: `platform_module/content_storage.py`
- Modify: `platform_module/facade.py`
- Modify: `platform_module/deps.py`
- Modify: `services/content_storage_service.py`
- Modify: `context_memory/facade.py` only if it still receives the legacy content-storage service directly
- Test: `tests/test_platform_content_storage.py`
- Test: existing content/compaction tests that reference full-content storage

- [ ] **Step 1: Write content-storage parity tests**

Cover:
- deterministic content hash generation or preservation of existing helper behavior
- upsert of full content metadata
- expansion of content references
- expired content behavior
- object-storage write/read/delete calls through injected storage protocols
- legacy `services.content_storage_service.content_storage_service` shim behavior before deletion

Run:

```bash
pytest tests/test_platform_content_storage.py tests/test_context_memory_compaction.py tests/test_compaction_service.py -q
```

Expected: FAIL until content storage is implemented under Platform.

- [ ] **Step 2: Confirm current content-storage callers**

Inventory current callers and record them in `phase9_cleanup_manifest.json`:

```bash
rg -n "content_storage_service|ContentStorageService|expand_content_reference|upsert_full_content" --glob '*.py'
```

Expected: every caller is classified as Platform-owned behavior, ContextMemory caller needing an injected Platform content-storage protocol, or obsolete legacy shim usage. Phase 9 always provides `platform_module/content_storage.py`; do not choose a branch where the planned file or `tests/test_platform_content_storage.py` is absent.

- [ ] **Step 3: Implement the Platform content-storage wrapper**

Implement `platform_module/content_storage.py` with injected object storage, metadata DAL/repository, TTL config, and no service/database/global-settings imports. If ContextMemory uses full-content storage, inject the Platform content-storage protocol into ContextMemory from the app shell; ContextMemory must not import `platform_module` directly.

- [ ] **Step 4: Add temporary legacy shim**

Update `services/content_storage_service.py` to delegate to the chosen owner or fail fast before binding. Add the shim to the Task 8 deletion manifest.

- [ ] **Step 5: Verify content storage migration**

Run:

```bash
pytest tests/test_platform_content_storage.py tests/test_context_memory_compaction.py tests/test_compaction_service.py tests/test_phase9_cleanup_gate.py -q
```

Expected: PASS, and cleanup gate knows whether `services/content_storage_service.py` is safe to delete.

- [ ] **Step 6: Commit content-storage extraction**

```bash
git add platform_module/content_storage.py platform_module/facade.py platform_module/deps.py services/content_storage_service.py context_memory/facade.py tests/test_platform_content_storage.py tests/test_context_memory_compaction.py tests/test_compaction_service.py tests/fixtures/phase9_cleanup_manifest.json
git commit -m "feat: settle platform content storage ownership"
```

### Task 6: Container Wiring And Route Dependency Binding

**Files:**
- Modify: `container.py`
- Modify: `main.py`
- Modify: `api/__init__.py` if route dependency registration is centralized
- Modify: route modules that need container-bound dependencies
- Test: `tests/test_platform_module_protocols.py`
- Test: `tests/test_api_thin_adapters.py`

- [ ] **Step 1: Write container wiring tests**

Assert:
- `create_platform_facade(...)` receives scalar config and protocol dependencies
- Platform can be constructed without import-time singletons
- route dependency helpers can be overridden in tests
- app startup binds old shims before any shim can be used

Run:

```bash
pytest tests/test_platform_module_protocols.py tests/test_api_thin_adapters.py -q
```

Expected: FAIL until container wiring exists.

- [ ] **Step 2: Wire Platform in container**

Add `PlatformDeps` construction and factory helpers. Container may import concrete implementations; module code may not.

- [ ] **Step 3: Wire Platform in main**

Create Platform facade during startup after required dependencies exist. Bind any temporary compatibility shims immediately after facade creation and before routers handle traffic.

- [ ] **Step 4: Add route dependency helper pattern**

Use consistent helper functions such as `get_gateway_service()`, `get_file_storage()`, and `get_rate_limiter()` that return protocols. Tests must override these helpers without importing concrete Platform classes.

- [ ] **Step 5: Verify wiring**

Run:

```bash
pytest tests/test_platform_module_protocols.py tests/test_api_gateway.py tests/test_file_upload.py tests/test_api_thin_adapters.py -q
```

Expected: PASS for Platform wiring and converted routes.

- [ ] **Step 6: Commit wiring**

```bash
git add container.py main.py api tests/test_platform_module_protocols.py tests/test_api_thin_adapters.py
git commit -m "feat: wire platform facade into app"
```

### Task 7: Convert Remaining API Modules To Thin Adapters

**Files:**
- Modify: `api/agent.py`
- Modify: `api/agent_viewset.py`
- Modify: `api/viewset.py`
- Modify: `api/agent_group.py`
- Modify: `api/room_center.py`
- Modify: `api/memory_center.py`
- Modify: `api/inspection_center.py`
- Modify: `api/hitl.py`
- Modify: `api/a2a_tasks.py`
- Modify: `api/webhooks.py`
- Modify: `api/relay.py`
- Modify: `api/hub.py`
- Modify: `api/sse.py`
- Modify: `api/discovery.py`
- Modify: `api/discovery_api_keys.py`
- Modify: `api/orchestration_center.py`
- Modify: `api/task.py`
- Test: route-specific API suites
- Test: `tests/test_api_thin_adapters.py`

- [ ] **Step 1: Migrate one route module at a time**

For each API module:
- Replace concrete singleton imports with protocol dependency helpers.
- Preserve path, method, auth, request model, response model, background task behavior, streaming behavior, and error mapping.
- Add or update route tests before modifying the route.
- Run that route's focused test file before continuing.

- [ ] **Step 2: Handle legacy workflow routes explicitly**

If Phase 0d/8 readiness evidence proves decommission:
- delete `api/orchestration_center.py` and `api/task.py`
- remove router includes from `main.py`
- delete related route tests or replace with decommission tests

If readiness is not proven:
- replace routes with 410 Gone only if the documented deprecation window is complete
- otherwise keep blocked manifest entries and do not claim "no old code" final gate

- [ ] **Step 3: Verify all API adapters**

Run:

```bash
pytest tests/test_api_thin_adapters.py tests/test_api_agent.py tests/test_api_agent_group.py tests/test_api_room_center.py tests/test_api_memory.py tests/test_api_inspection.py tests/test_api_hitl.py tests/test_api_a2a_tasks.py tests/test_api_webhooks.py tests/test_api_relay.py tests/test_api_sse.py tests/test_api_discovery.py tests/test_api_discovery_api_keys.py tests/test_api_orchestration.py tests/test_api_task.py -q
```

Expected: PASS or fail only on documented legacy workflow blockers in `phase9_cleanup_manifest.json`.

- [ ] **Step 4: Commit API adapter conversion**

```bash
git add api main.py tests tests/fixtures/phase9_api_routes.json tests/fixtures/phase9_import_allowlist.json tests/fixtures/phase9_cleanup_manifest.json
git commit -m "refactor: convert api routes to protocol adapters"
```

### Task 7b: Common Internal Dependency Cleanup

**Files:**
- Modify: `common/api_key_auth.py`
- Modify: `common/auth.py`
- Modify: `common/middleware/discovery_cors_middleware.py`
- Modify: `common/utils/a2a_helpers.py`
- Modify: `common/utils/context_utils.py`
- Modify: `common/protocols/platform_protocols.py`
- Modify: `container.py`
- Modify: `main.py`
- Test: `tests/test_common_foundation.py`
- Test: `tests/test_common_api_key_auth.py`
- Test: `tests/test_common_a2a_helpers.py`
- Test: `tests/test_api_discovery_api_keys.py`

- [ ] **Step 1: Write failing Common dependency scan**

Add or extend a static test that scans `common/**/*.py` for imports from:

```python
FORBIDDEN_COMMON_IMPORT_PREFIXES = (
    "database",
    "services",
    "modules",
    "config",
    "delivery",
    "execution",
    "hub_runtime_bridge",
    "platform_module",
)
```

Allow `common.config` itself and exact test-only fixtures only. `common/` may define protocols and DTOs, but it must not import concrete app modules.

Run:

```bash
pytest tests/test_common_foundation.py tests/test_common_api_key_auth.py tests/test_common_a2a_helpers.py -q
```

Expected: FAIL on current direct imports from `common/api_key_auth.py`, `common/auth.py`, `common/middleware/discovery_cors_middleware.py`, `common/utils/a2a_helpers.py`, and `common/utils/context_utils.py`.

- [ ] **Step 2: Add API-key auth protocol seam**

Define a narrow protocol for API-key validation and usage tracking, for example:

```python
class APIKeyAuthenticator(Protocol):
    async def validate_api_key(self, plaintext_key: str, *, track_usage: bool = True) -> APIKey: ...
```

Bind the concrete implementation in `container.py` using DAL/repository access. `common/api_key_auth.py` should only parse headers, call the bound protocol, and map validation failures to HTTP responses. It must not import `database.mongodb`.

- [ ] **Step 3: Inject auth and CORS config**

Move Clerk/JWT/CORS settings reads out of `common/auth.py` and `common/middleware/discovery_cors_middleware.py`. App startup should pass immutable config values or a narrow config object from the app shell. Tests must prove no global `config.settings` import remains in those files.

- [ ] **Step 4: Move storage URL signing out of Common helpers**

For `common/utils/a2a_helpers.py`, replace lazy `services.s3_service` calls with an injected URL resolver/object-storage protocol supplied by Platform or route adapters. If the helper is only used by API/Platform code, move the storage-aware function out of Common and keep Common functions pure.

- [ ] **Step 5: Move LLM helper dependency out of Common**

For `common/utils/context_utils.py`, replace lazy `services.openai_service` access with LLM Gateway or an injected tokenizer/model utility. Common may keep deterministic formatting/token-count helpers only.

- [ ] **Step 6: Verify Common is leaf-clean**

Run:

```bash
pytest tests/test_common_foundation.py tests/test_common_api_key_auth.py tests/test_common_a2a_helpers.py tests/test_api_discovery_api_keys.py tests/test_api_thin_adapters.py -q
rg -n "from database|import database|from services|import services|from modules|import modules|from config\\.settings|import config\\.settings|from platform_module|import platform_module" common --glob '*.py'
```

Expected: tests pass and the `rg` scan returns no production Common dependency violations.

- [ ] **Step 7: Commit Common cleanup**

```bash
git add common container.py main.py tests/test_common_foundation.py tests/test_common_api_key_auth.py tests/test_common_a2a_helpers.py tests/test_api_discovery_api_keys.py tests/test_api_thin_adapters.py
git commit -m "refactor: remove concrete dependencies from common"
```

### Task 8: Delete Legacy Shims And Old Code

**Files:**
- Delete: selected files under `services/`
- Delete: selected files under `modules/`
- Delete: obsolete `config/` files if unused
- Delete: obsolete `infrastructure/` files if moved
- Modify: `pyproject.toml`
- Modify: `tests/fixtures/phase9_cleanup_manifest.json`
- Test: `tests/test_phase9_cleanup_gate.py`
- Test: legacy `tests/test_service_*.py`, `tests/test_module_*.py`, and `tests/test_p2_modules_services.py` migration/deletion inventory

- [ ] **Step 1: Generate import evidence**

Run:

```bash
rg -n "from services|import services|from modules|import modules|from config|import config|from infrastructure|import infrastructure|database\\.mongodb|settings" --glob '*.py'
```

Replace the broad `settings` term with AST checks or exact import patterns before
turning this into a gate:

```bash
rg -n "from services|import services|from modules|import modules|from config(\\.| import)|import config|from infrastructure|import infrastructure|database\\.mongodb|from config\\.settings import settings|import config\\.settings" --glob '*.py'
```

Expected: only app shell allowlisted imports or blocked manifest entries remain. Do not fail on local variables or parameters named `settings`, and do not flag `common/config/settings.py` merely because the file defines the canonical settings model.

- [ ] **Step 2: Delete safe shims**

Before deleting a shim or old module file, inventory the tests that import it:

```bash
rg -n "from services|import services|from modules|import modules" tests --glob '*.py'
```

For each hit, choose one outcome and record it in `phase9_cleanup_manifest.json`:
- migrate the test to the new module/facade behavior
- replace it with a route/module parity test that proves the same behavior through protocols
- delete it only when the old behavior is decommissioned and covered by a new blocker/decommission test

Do not delete `services/` or `modules/` while `pytest -q` still depends on legacy test imports.

For each file listed as safe in `phase9_cleanup_manifest.json`:
- verify no production import remains
- delete the file
- remove package exports
- remove package listing from `pyproject.toml` if the package is empty and no longer shipped

- [ ] **Step 3: Re-run import and package gates**

Run:

```bash
pytest tests/test_phase9_cleanup_gate.py tests/test_api_thin_adapters.py -q
python -m compileall agent room context_memory delivery execution hub_runtime_bridge platform_module common dal a2a_adapter llm_gateway api
```

Expected: PASS unless blocked manifest entries explicitly prevent final cleanup.

- [ ] **Step 4: Commit cleanup**

```bash
git add -A services modules config infrastructure pyproject.toml tests/fixtures/phase9_cleanup_manifest.json tests/test_phase9_cleanup_gate.py tests/test_api_thin_adapters.py
git commit -m "refactor: remove legacy shims after modular migration"
```

### Task 9: Final Import-Linter Enforcement

**Files:**
- Modify: import-linter configuration or tests enforcing module contracts
- Modify: `tests/test_phase9_cleanup_gate.py`
- Modify: `tests/fixtures/phase9_import_allowlist.json`
- Test: full static gate suite

- [ ] **Step 1: Encode final contracts**

Enforce:
- Common depends on nothing internal.
- DAL depends only on Common.
- A2A Adapter and LLM Gateway do not leak SDK types into business modules.
- Business modules communicate through Common protocols.
- Delivery imports no business modules and no run lifecycle writers.
- API imports only FastAPI/auth/schema helpers plus Common protocols or route dependency helpers.
- `container.py` and `main.py` are the only concrete cross-module composition points.
- No module imports `main.py` or `container.py`.
- No import-time singleton service construction remains.
- Import-linter root/package names must use `platform_module` for the Platform implementation package. Do not create or enforce a `platform` Python package, because that shadows the Python standard-library `platform` module.

- [ ] **Step 2: Empty temporary allowlists**

Remove every resolved entry from `phase9_import_allowlist.json`. Any remaining entry must include:
- exact file path
- imported symbol
- reason
- owner
- expiry task
- why deletion is blocked

- [ ] **Step 3: Verify final static gates**

Run:

```bash
pytest tests/test_common_foundation.py tests/test_adapter_protocols.py tests/test_delivery_protocols.py tests/test_execution_protocols.py tests/test_hub_runtime_bridge_protocols.py tests/test_platform_module_protocols.py tests/test_api_thin_adapters.py tests/test_phase9_cleanup_gate.py -q
```

Expected: PASS, or fail only with documented blocked legacy workflow cleanup that prevents claiming Phase 9 complete.

- [ ] **Step 4: Commit linter enforcement**

```bash
git add tests tests/fixtures/phase9_import_allowlist.json
git commit -m "test: enforce final modular import contracts"
```

### Task 10: Full Verification And Handoff

**Files:**
- Modify: `docs/MODULAR_DECOUPLING_DESIGN.md` after implementation lands, if updating phase status is requested
- Reference: all changed files

- [ ] **Step 1: Run focused Phase 9 suite**

Run:

```bash
pytest tests/test_platform_module_protocols.py tests/test_platform_gateway.py tests/test_platform_files.py tests/test_platform_content_storage.py tests/test_platform_rate_limit.py tests/test_api_thin_adapters.py tests/test_phase9_cleanup_gate.py -q
```

Expected: PASS when no legacy workflow or old-code blockers remain. If legacy workflow decommission readiness is not proven, this command may fail only on the documented `phase9_cleanup_manifest.json` blocker; in that case do not mark Phase 9 complete and hand off the blocker explicitly.

- [ ] **Step 2: Run route and module regression suites**

Run:

```bash
pytest tests/test_api_gateway.py tests/test_api_discovery.py tests/test_api_discovery_api_keys.py tests/test_api_agent.py tests/test_api_agent_group.py tests/test_api_room_center.py tests/test_api_memory.py tests/test_api_inspection.py tests/test_api_hitl.py tests/test_api_a2a_tasks.py tests/test_api_webhooks.py tests/test_api_relay.py tests/test_api_sse.py tests/test_api_orchestration.py tests/test_api_task.py tests/test_file_upload.py -q
```

Expected: PASS with unchanged public API behavior.

- [ ] **Step 3: Run broader regression suite**

Run:

```bash
pytest -q
```

Expected: PASS when no blocked cleanup manifest entries remain. If blocked manifest entries intentionally remain, record the exact failing gate and do not claim Phase 9 complete. If this is too slow for CI-local handoff, record the exact subset run and the reason full suite was deferred.

- [ ] **Step 4: Run final old-code scan**

Run:

```bash
rg -n "from services|import services|from modules|import modules|from config|import config|database\\.mongodb|settings" --glob '*.py'
```

Use exact import matching for settings rather than a bare text search:

```bash
rg -n "from services|import services|from modules|import modules|from config(\\.| import)|import config|database\\.mongodb|from config\\.settings import settings|import config\\.settings" --glob '*.py'
```

Expected: no production hits outside app-shell/config compatibility entries documented in `phase9_cleanup_manifest.json`. Local identifiers named `settings` are allowed when they do not import or read global configuration.

- [ ] **Step 5: Commit verification repairs**

Commit only if final verification required changes:

```bash
git add -A
git commit -m "test: verify phase 9 modular cleanup"
```

## Acceptance Checklist

- [ ] `platform_module` implements `GatewayService`, `RateLimiter`, and `FileStorage` protocol surfaces.
- [ ] Gateway discovery, card masking, send, stream, access control, hub-agent 502 guard, and rate-limit behavior match the pre-Phase 9 API.
- [ ] File upload validation, storage, metadata persistence, compensating delete, presigned URL, delete, and list behavior match the pre-Phase 9 API.
- [ ] Full-content storage upsert, reference expansion, expiry, and object-storage behavior match the pre-Phase 9 implementation.
- [ ] `api/` route modules are thin adapters with no concrete `services.*`, `modules.*`, `database.mongodb`, or global settings imports outside exact blocked manifest entries.
- [ ] `container.py` and `main.py` own concrete module construction and cross-module wiring.
- [ ] Legacy compatibility shims are deleted, or remaining blockers are explicitly listed in `phase9_cleanup_manifest.json` with owner and expiry.
- [ ] `modules/` and `services/` are no longer shipped packages unless a blocked legacy workflow manifest entry proves deletion is unsafe.
- [ ] Legacy workflow routes and collections are removed only with Phase 0d/8 readiness evidence; otherwise Phase 9 is not marked complete.
- [ ] Import-linter/static gates enforce all final modular dependency contracts.
- [ ] Full route-focused tests and final static gates pass.
- [ ] No new runtime dependency or stack replacement was introduced.

## Plan Review Loop Log

This section is for the plan-writing task only. It records the required external review loops and plan edits. Implementation agents do not need to execute these review-loop entries.

| Loop | Reviewer Status | Blocking Issues | Plan Change Applied |
|------|-----------------|-----------------|---------------------|
| 0 | Initial draft | Not reviewed | Initial Phase 9 plan created from `docs/MODULAR_DECOUPLING_DESIGN.md`. |
| 1 | Issues Found | Broad `settings` scan would create false positives; final import contract did not explicitly reconcile `platform_module` with the design's Platform terminology. | Replaced bare `settings` scans with exact import/AST guidance and added final-contract instruction to use `platform_module`, not `platform`. |
| 2 | Approved | None | Recorded approval; no plan content changes required. |
| 3 | Approved | None | Recorded approval; no plan content changes required. |
| 4 | Issues Found | Final Common leaf-module gate lacked planned work for existing `common/` imports from `database`, `config.settings`, and `services`. | Added explicit Common dependency cleanup file inventory and Task 7b covering API-key auth, auth/CORS config, storage URL signing, and LLM helper seams. |
| 5 | Issues Found | `api/viewset.py` and `api/agent_viewset.py` were omitted from API migration scope; route verification omitted inspection/orchestration/task tests; legacy service/module tests were not inventoried before deleting old packages. | Added viewset files to API migration scope, expanded focused route verification commands, and added a legacy test import inventory requirement before deleting `services/` or `modules/`. |
| 6 | Approved | None | Recorded approval; no plan content changes required. |
| 7 | Approved | None | Recorded approval; no plan content changes required. |
| 8 | Approved | None | Recorded approval; no plan content changes required. |
| 9 | Issues Found | `platform_module/content_storage.py` and `services/content_storage_service.py` were listed but no task migrated, tested, wired, or safely deleted content storage. | Added `tests/test_platform_content_storage.py`, Task 5b for content-storage ownership/migration, shim handling, verification, and cleanup-gate coverage. |
| 10 | Approved | None | Recorded approval; no plan content changes required. |
| 11 | Approved | None | Recorded approval; no plan content changes required. |
| 12 | Issues Found | Task 5b allowed removing `platform_module/content_storage.py` while later verification and git commands required that file and `tests/test_platform_content_storage.py`. | Made content-storage ownership deterministic: Phase 9 always implements `platform_module/content_storage.py`, keeps the platform content-storage tests, and injects the protocol into ContextMemory if needed. |
| 13 | Issues Found | Final verification expected unconditional cleanup-gate PASS even though earlier scope allowed an intentional blocked manifest when legacy workflow decommission readiness is not proven. | Updated Task 10 expectations to distinguish complete PASS from documented blocked-manifest failure and to forbid claiming Phase 9 complete while blockers remain. |
| 14 | Approved | None | Recorded approval; no plan content changes required. |
| 15 | Approved | None | Recorded approval; no plan content changes required. |
| 16 | Approved | None | Recorded approval; no plan content changes required. |
| 17 | Approved | None | Recorded approval; no plan content changes required. |
| 18 | Approved | None | Recorded approval; no plan content changes required. |
| 19 | Issues Found | Loop log only recorded through loop 14 while the active review was iteration 19, weakening evidence for the required 25-loop process. | Recorded loops 15-19 in the plan log before continuing. |
| 20 | Approved | None | Recorded approval; no plan content changes required. |
| 21 | Approved | None | Recorded approval; no plan content changes required. |
| 22 | Approved | None | Recorded approval; no plan content changes required. |
| 23 | Approved | None | Recorded approval; no plan content changes required. |
| 24 | Approved | None | Recorded approval; no plan content changes required. |
| 25 | Approved | None | Recorded approval; no plan content changes required. |
| 26 | Approved | None | Extra post-audit approval after stale content-storage wording was cleaned up. |

## Handoff

Plan complete when this file has completed at least 25 review loops, all blocking reviewer issues have been addressed or explicitly rejected with technical rationale, and the completion audit confirms this plan is the only edited file for the plan-writing task.
