# API Gateway Traffic Consolidation Implementation Plan

> **For agentic workers:** REQUIRED: Use `superpowers:subagent-driven-development` if subagents are available, or `superpowers:executing-plans` to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve every existing public API path while moving HTTP route declaration, traffic policy, and route ownership into one first-class API Gateway module.

**Architecture:** Introduce an `api_gateway/` package that owns FastAPI route registration for the whole application, while existing business modules remain behind injected facades/protocols. Public paths such as `/api/v1/roomCenter/sendMessage`, `/api/v1/agent/getAllAgents`, `/api/v1/rooms/{room_id}/hitl/pending`, `/api/v1/gateway/agents/{agent_id}/message/send`, and `/api/v1/relay/...` must remain stable; the change is internal ownership, not URL migration. Keep `platform_module.gateway.PlatformGateway` as the external agent-call service and expose it through `api_gateway/routes/platform_gateway_routes.py` to avoid confusing it with the whole-app API Gateway module.

**Tech Stack:** Python 3.13-compatible code, FastAPI, pytest, pytest-asyncio, existing `app_shell` route-owner protocols, existing module facades, `common.protocols`, current auth dependencies, no new runtime dependencies.

---

## Scope

Include:
- Create a new `api_gateway/` package that owns all public HTTP route declaration under the configured `API_PREFIX`.
- Keep all currently exposed public route paths, HTTP methods, auth behavior, response schemas, SSE semantics, upload semantics, status codes, and error payloads stable unless a route is already explicitly deprecated or returning 410.
- Move route registration out of `main.py` so `main.py` mounts one API Gateway router plus `/health` only.
- Use neutral route group names such as `room_routes.py`, `agent_routes.py`, and `platform_gateway_routes.py`; do not name new files `legacy_*`.
- Preserve existing route handler behavior by delegating to current route-owner protocols, facades, or extracted handler functions.
- Centralize traffic concerns that are currently scattered across route modules: route inventory, auth policy, CORS/open-origin policy for discovery/gateway/relay, API key policy, request context, error mapping, and route tags.
- Keep `api/` modules as temporary compatibility wrappers only during migration, then either delete them or reduce them to re-export shims with explicit removal gates.
- Add tests proving public route inventory parity and proving all public paths are declared from `api_gateway`.

Exclude:
- Do not change frontend-visible URLs.
- Do not change frontend request or response models as part of this migration.
- Do not rename `platform_module.gateway.PlatformGateway` in the first pass unless a later review finds an unavoidable ambiguity; prefer `api_gateway/routes/platform_gateway_routes.py` for naming clarity.
- Do not rewrite room orchestration, execution, HITL, relay, discovery, files, agent registry, or platform business logic.
- Do not delete old `api/*.py` files until route parity and import gates prove the new gateway owns the traffic.
- Do not modify production code during this plan-writing task.

## Current State

As of 2026-05-21:
- `main.py` directly imports and includes many route modules: `api.agent`, `api.room_center`, `api.hitl`, `api.gateway`, `api.discovery`, `api.relay`, `api.files`, `api.webhooks`, and others.
- `api.gateway` delegates only to `platform_module.gateway.PlatformGateway` and exposes `/gateway/...` external agent endpoints.
- `platform_module.gateway.PlatformGateway` is not a whole-backend traffic gateway; it is the service for discovering/calling individual agents through the platform gateway API.
- Several legacy path contracts are still active frontend contracts. Examples: `/roomCenter/sendMessage`, `/roomCenter/createNewRoom`, `/agent/getAllActiveAgents`, `/agentGroups`, `/rooms/{room_id}/hitl/pending`, and `/sse/room/{room_id}/stream`.
- The desired end state is one API Gateway module that owns declaration and traffic policy for all of those paths while preserving the paths.

## Target Package Shape

Create:
- `api_gateway/__init__.py`: package marker and public export for `router`.
- `api_gateway/router.py`: creates the root API Gateway `APIRouter`, includes all route groups, and exposes `build_api_gateway_router(...)` if dependency injection needs a factory.
- `api_gateway/registry.py`: route inventory helpers and constants used by tests to prove route ownership and public path parity. This registry is the authoritative ownership source for dynamically generated routes whose endpoint functions may be produced by helper classes.
- `api_gateway/policies.py`: declarative route policy definitions for auth mode, API-key mode, CORS mode, tags, and deprecation state.
- `api_gateway/dependencies.py`: gateway-owned dependency accessors for bound app services/facades/protocols. These should wrap existing `bind_*` patterns or replace them with explicit `GatewayDeps`.
- `api_gateway/errors.py`: shared HTTP error mapping helpers for domain errors, platform route errors, HITL errors, and gateway errors.
- `api_gateway/context.py`: request context helpers for authenticated Clerk users, optional users, API keys, room ownership checks, client request ids, and route metadata.
- `api_gateway/routes/agent_routes.py`: owns existing `/agent/*` and agent viewset paths.
- `api_gateway/routes/agent_group_routes.py`: owns existing `/agentGroups*` paths.
- `api_gateway/routes/room_routes.py`: owns existing `/roomCenter/*` paths.
- `api_gateway/routes/hitl_routes.py`: owns existing `/rooms/{room_id}/hitl/*` paths.
- `api_gateway/routes/sse_routes.py`: owns existing `/sse/*` paths.
- `api_gateway/routes/memory_routes.py`: owns existing memory center paths.
- `api_gateway/routes/inspection_routes.py`: owns existing inspection paths.
- `api_gateway/routes/orchestration_routes.py`: owns existing orchestration center paths, including 410 deprecated behavior.
- `api_gateway/routes/task_routes.py`: owns existing task routes, including 410 deprecated behavior.
- `api_gateway/routes/files_routes.py`: owns existing `/files/*` paths.
- `api_gateway/routes/discovery_routes.py`: owns existing discovery paths.
- `api_gateway/routes/discovery_api_key_routes.py`: owns existing discovery API key management paths.
- `api_gateway/routes/platform_gateway_routes.py`: owns existing `/gateway/*` paths and delegates to `platform_module.gateway.PlatformGateway`.
- `api_gateway/routes/relay_routes.py`: owns existing `/relay/*` paths.
- `api_gateway/routes/hub_routes.py`: owns existing `/hub/*` paths.
- `api_gateway/routes/a2a_task_routes.py`: owns existing A2A task paths.
- `api_gateway/routes/webhook_routes.py`: owns existing webhook paths.
- `tests/test_api_gateway_route_inventory.py`: route parity and route ownership tests.
- `tests/test_api_gateway_policies.py`: auth/CORS/API-key policy tests.
- `tests/test_api_gateway_module_boundaries.py`: AST/import boundary tests proving route declaration moved out of old `api/` and into `api_gateway/`.
- `tests/fixtures/api_gateway_route_inventory_before.json`: captured current route inventory.
- `tests/fixtures/api_gateway_route_inventory_expected.json`: expected route inventory after migration.
- `tests/fixtures/api_gateway_policy_matrix.json`: expected auth, API-key, CORS, tag, and deprecation policy per route group.

Modify:
- `main.py`: stop importing individual `api.*` route modules for route registration; include only `api_gateway.router` under `api_prefix` plus `/health`.
- Existing `api/*.py`: move declarations to `api_gateway/routes/*.py`, then either delete old route modules or leave explicit compatibility re-export shims that do not own public traffic.
- `container.py`: if needed, produce `GatewayDeps` from existing module facades and route-owner protocols.
- Startup binding checks: verify API Gateway dependencies rather than individual scattered route module globals.
- `common/middleware/discovery_cors_middleware.py`: either move the policy into API Gateway middleware/policies or make it consume API Gateway policy config.
- Existing API tests: update imports only if they directly call old route functions. Prefer testing through `api_gateway.routes.*` or HTTP client route paths.

Do not modify in the plan-writing task:
- Any production Python files.
- Any test Python files.
- Any non-plan docs.

## Dependency Direction

Desired dependency graph:

```text
main.py
  -> api_gateway.router
  -> app_shell/container binding only

api_gateway/**
  -> FastAPI
  -> common.auth / common.api_key_auth
  -> common.protocols / common.dto / common.errors
  -> app_shell route-owner protocols
  -> models request/response schemas that are public API contracts
  -> no direct database.mongodb access
  -> no concrete services.* singleton imports
  -> no modules.* singleton imports

business modules
  -> unchanged ownership: room, agent, execution, delivery, hub_runtime_bridge, platform_module, context_memory
```

## Invariants

- Public URL paths are stable.
- OpenAPI operation ids may change only if existing tests confirm clients do not rely on them; otherwise preserve names.
- Auth behavior is stable:
  - Clerk-protected app routes stay Clerk-protected.
  - optional-user routes remain optional-user routes.
  - API-key routes remain API-key routes.
  - relay routes keep existing API-key/JWT behavior.
  - webhook routes keep token validation behavior and must not accidentally inherit Clerk auth.
- CORS behavior is stable for discovery, gateway, and relay paths.
- `/health` remains outside `/api/v1` and outside the gateway router. It is the only intended HTTP endpoint exception to whole-app API Gateway ownership.
- `platform_module.gateway.PlatformGateway` remains an agent-call service, not the whole HTTP gateway.
- Implementation must be incremental. Each task must keep the app bootable and preserve route parity.

## Tasks

### Task 1: Capture Route Inventory And Policy Matrix

**Files:**
- Create: `tests/fixtures/api_gateway_route_inventory_before.json`
- Create: `tests/fixtures/api_gateway_route_inventory_expected.json`
- Create: `tests/fixtures/api_gateway_policy_matrix.json`
- Create: `tests/test_api_gateway_route_inventory.py`
- Create: `tests/test_api_gateway_policies.py`
- Reference: `main.py`
- Reference: `api/*.py`

- [ ] **Step 1: Capture current route inventory**

Run:

```bash
python - <<'PY'
import json
from main import app

rows = []
for route in sorted(app.routes, key=lambda r: (getattr(r, "path", ""), sorted(getattr(r, "methods", []) or []))):
    path = getattr(route, "path", "")
    if not path.startswith("/api/") and path != "/health":
        continue
    rows.append({
        "path": path,
        "methods": sorted(getattr(route, "methods", []) or []),
        "name": getattr(route, "name", ""),
        "endpoint_module": getattr(getattr(route, "endpoint", None), "__module__", ""),
        "declared_owner": getattr(getattr(route, "endpoint", None), "__module__", ""),
        "tags": list(getattr(route, "tags", []) or []),
    })
print(json.dumps(rows, indent=2, sort_keys=True))
PY
```

Expected: output includes all current `/api/v1/*` routes and `/health`.

- [ ] **Step 2: Save inventory fixtures**

Copy the current output into `tests/fixtures/api_gateway_route_inventory_before.json`.

Create `tests/fixtures/api_gateway_route_inventory_expected.json` with the same path/method/name/tag contract, but with `declared_owner` expected to start with `api_gateway.routes.` for `/api/v1/*` routes after migration. Keep `/health` owned by `main`.

`declared_owner` must be computed by a helper in `api_gateway.registry`: use endpoint `__module__` for ordinary route functions, and use explicit registry metadata for generated/dynamic routes such as Agent viewset routes. Keep raw `endpoint_module` in the fixture for diagnostics only.

- [ ] **Step 3: Write failing route ownership test**

Add a test that imports `main.app` and asserts:

```python
assert current_paths_and_methods == expected_paths_and_methods
assert all(row["declared_owner"].startswith("api_gateway.routes.") for row in api_routes)
```

Run:

```bash
pytest tests/test_api_gateway_route_inventory.py -q
```

Expected: FAIL because current route declarations are still owned by `api.*`.

- [ ] **Step 4: Write policy matrix fixture**

Create `tests/fixtures/api_gateway_policy_matrix.json` with entries for route groups:

```json
{
  "agent": {"auth": "mixed-route-level", "cors": "default", "api_key": false},
  "room": {"auth": "clerk-route-level", "cors": "default", "api_key": false},
  "hitl": {"auth": "clerk-route-level", "cors": "default", "api_key": false},
  "sse": {"auth": "query-token-supported", "cors": "default", "api_key": false},
  "discovery": {"auth": "api-key-route-level", "cors": "open", "api_key": true},
  "platform_gateway": {"auth": "api-key-route-level", "cors": "open", "api_key": true},
  "relay": {"auth": "api-key-or-jwt-route-level", "cors": "open", "api_key": true},
  "webhooks": {"auth": "bearer-token-route-level", "cors": "default", "api_key": false}
}
```

Add missing route groups discovered in Step 1.

- [ ] **Step 5: Write failing policy test**

Add tests asserting policy matrix entries exist for every route group and that open-CORS groups are exactly discovery, platform gateway, and relay.

Run:

```bash
pytest tests/test_api_gateway_policies.py -q
```

Expected: FAIL because `api_gateway.policies` does not exist.

### Task 2: Create API Gateway Package Skeleton

**Files:**
- Create: `api_gateway/__init__.py`
- Create: `api_gateway/router.py`
- Create: `api_gateway/registry.py`
- Create: `api_gateway/policies.py`
- Create: `api_gateway/dependencies.py`
- Create: `api_gateway/errors.py`
- Create: `api_gateway/context.py`
- Create: `api_gateway/routes/__init__.py`
- Test: `tests/test_api_gateway_policies.py`
- Test: `tests/test_api_gateway_module_boundaries.py`

- [ ] **Step 1: Write package import boundary test**

Create `tests/test_api_gateway_module_boundaries.py` with AST checks:

```python
FORBIDDEN_API_GATEWAY_IMPORTS = (
    "database.mongodb",
    "modules",
    "services.gateway_service",
    "services.file_upload_service",
    "services.rate_limit_service",
)
```

Allow imports from `app_shell`, `common`, `models`, and business module facade protocols.

Also assert that no gateway route file uses `legacy_*` naming:

```python
def test_api_gateway_route_files_do_not_use_legacy_prefix():
    route_files = Path("api_gateway/routes").glob("*.py")
    assert [p.name for p in route_files if p.name.startswith("legacy_")] == []
```

Run:

```bash
pytest tests/test_api_gateway_module_boundaries.py -q
```

Expected: FAIL until `api_gateway` exists.

- [ ] **Step 2: Implement policy constants**

Create `api_gateway/policies.py` with a `RoutePolicy` dataclass and a `ROUTE_POLICIES` mapping that matches `tests/fixtures/api_gateway_policy_matrix.json`.

- [ ] **Step 3: Implement registry constants**

Create `api_gateway/registry.py` with route group names, expected module prefixes, and helper functions for tests. Keep this module free of FastAPI app construction side effects.

- [ ] **Step 4: Implement empty route group router**

Create `api_gateway/router.py`:

```python
from fastapi import APIRouter

router = APIRouter()

def build_api_gateway_router() -> APIRouter:
    return router
```

This is intentionally incomplete; route inventory tests should still fail.

- [ ] **Step 5: Run skeleton tests**

Run:

```bash
pytest tests/test_api_gateway_policies.py tests/test_api_gateway_module_boundaries.py -q
```

Expected: policy and import tests pass; route inventory still fails because no routes have moved.

### Task 3: Move Platform Gateway, Discovery, Relay, And Webhook Route Declarations

**Files:**
- Create: `api_gateway/routes/platform_gateway_routes.py`
- Create: `api_gateway/routes/discovery_routes.py`
- Create: `api_gateway/routes/discovery_api_key_routes.py`
- Create: `api_gateway/routes/relay_routes.py`
- Create: `api_gateway/routes/webhook_routes.py`
- Modify: `api_gateway/router.py`
- Modify: `main.py`
- Modify: old `api/gateway.py`, `api/discovery.py`, `api/discovery_api_keys.py`, `api/relay.py`, `api/webhooks.py`
- Test: `tests/test_api_gateway_route_inventory.py`
- Test: existing `tests/test_api_gateway.py`, `tests/test_api_discovery.py`, `tests/test_api_discovery_api_keys.py`, `tests/test_api_relay.py`, `tests/test_api_webhooks.py`

- [ ] **Step 1: Extract platform gateway routes**

Move declarations for:
- `/gateway/agents/discover`
- `/gateway/agents/{agent_id}/message/send`
- `/gateway/agents/{agent_id}/message/stream`
- `/gateway/agents/{agent_id}/card`

from `api/gateway.py` into `api_gateway/routes/platform_gateway_routes.py`.

Keep function behavior and dependencies equivalent. Delegate to `platform_module.gateway.PlatformGateway` through existing `GatewayService` protocol dependency.

- [ ] **Step 2: Extract discovery and API-key routes**

Move route declarations from `api/discovery.py` and `api/discovery_api_keys.py` into `api_gateway/routes/discovery_routes.py` and `api_gateway/routes/discovery_api_key_routes.py`.

Preserve API-key auth and open CORS policy.

- [ ] **Step 3: Extract relay routes**

Move route declarations from `api/relay.py` into `api_gateway/routes/relay_routes.py`.

Preserve hub publish, heartbeat, stream, auth mode, and high-frequency access log expectations.

- [ ] **Step 4: Extract webhook routes**

Move route declarations from `api/webhooks.py` into `api_gateway/routes/webhook_routes.py`.

Preserve bearer token validation and avoid adding Clerk auth.

- [ ] **Step 5: Include route groups in API Gateway router**

Update `api_gateway/router.py` to include the new route modules with the same path definitions they previously had. Do not add `API_PREFIX` here; `main.py` owns the prefix.

- [ ] **Step 6: Update main route mounting for migrated groups**

In `main.py`, include `api_gateway.router` under `api_prefix` while leaving not-yet-migrated `api.*` routers mounted directly.

This temporary mixed state is allowed only during migration.

- [ ] **Step 7: Run focused tests**

Run:

```bash
pytest tests/test_api_gateway.py tests/test_api_discovery.py tests/test_api_discovery_api_keys.py tests/test_api_relay.py tests/test_api_webhooks.py tests/test_api_gateway_route_inventory.py -q
```

Expected: behavior tests pass; route inventory test reports fewer remaining `api.*` owners.

### Task 4: Move Room, HITL, SSE, And Files Route Declarations

**Files:**
- Create: `api_gateway/routes/room_routes.py`
- Create: `api_gateway/routes/hitl_routes.py`
- Create: `api_gateway/routes/sse_routes.py`
- Create: `api_gateway/routes/files_routes.py`
- Modify: `api_gateway/router.py`
- Modify: `main.py`
- Modify: old `api/room_center.py`, `api/hitl.py`, `api/sse.py`, `api/files.py`
- Test: `tests/test_api_room_center.py`
- Test: `tests/test_api_hitl.py`
- Test: `tests/test_api_sse.py`
- Test: `tests/test_file_upload.py`
- Test: `tests/test_flow_contracts.py`

- [ ] **Step 1: Extract room routes**

Move declarations for `/roomCenter/*` into `api_gateway/routes/room_routes.py`.

Preserve function names where tests or OpenAPI depend on them. Keep helpers for attachment extraction and room ownership either in `api_gateway/context.py` or in `room_routes.py` if they are route-specific.

- [ ] **Step 2: Extract HITL routes**

Move `/rooms/{room_id}/hitl/*` declarations into `api_gateway/routes/hitl_routes.py`.

Use shared room ownership verification from `api_gateway/context.py` rather than importing from `room_routes.py`.

- [ ] **Step 3: Extract SSE routes**

Move SSE route declarations into `api_gateway/routes/sse_routes.py`.

Preserve query-token support for EventSource.

- [ ] **Step 4: Extract files routes**

Move `/files/*` declarations into `api_gateway/routes/files_routes.py`.

Preserve upload validation, room ownership behavior, and file response schema.

- [ ] **Step 5: Remove direct mounts for migrated groups**

Update `main.py` to stop directly mounting old room, HITL, SSE, and files routers once gateway route parity passes for those groups.

- [ ] **Step 6: Run focused tests**

Run:

```bash
pytest tests/test_api_room_center.py tests/test_api_hitl.py tests/test_api_sse.py tests/test_file_upload.py tests/test_flow_contracts.py tests/test_api_gateway_route_inventory.py -q
```

Expected: behavior tests pass and route inventory shows these groups owned by `api_gateway.routes.*`.

### Task 5: Move Agent, Agent Group, Memory, Inspection, Orchestration, Task, Hub, And A2A Task Route Declarations

**Files:**
- Create: `api_gateway/routes/agent_routes.py`
- Create: `api_gateway/routes/agent_group_routes.py`
- Create: `api_gateway/routes/memory_routes.py`
- Create: `api_gateway/routes/inspection_routes.py`
- Create: `api_gateway/routes/orchestration_routes.py`
- Create: `api_gateway/routes/task_routes.py`
- Create: `api_gateway/routes/hub_routes.py`
- Create: `api_gateway/routes/a2a_task_routes.py`
- Modify: `api_gateway/router.py`
- Modify: `main.py`
- Modify: old matching `api/*.py` route modules
- Test: all matching API test suites

- [ ] **Step 1: Extract agent and agent viewset routes**

Move agent route declarations and viewset registration into `api_gateway/routes/agent_routes.py`.

Preserve mixed auth behavior: public GET routes stay public where currently public, and mutating routes keep Clerk auth.

For routes produced by `AgentViewSet` / `ViewSet`, do not merely mount an old router whose generated endpoint modules still point at `api.viewset`. Choose one of these implementation strategies and document the choice in `api_gateway/registry.py`:

1. Move the viewset classes themselves under `api_gateway/routes/agent_routes.py` or an `api_gateway/viewsets.py` helper so generated endpoint functions belong to API Gateway-owned modules.
2. Wrap generated handlers with small `api_gateway.routes.agent_routes` endpoint functions while preserving path/method/schema behavior.
3. If a helper must remain outside `api_gateway`, add explicit registry metadata proving declaration ownership and make the route ownership test use that metadata instead of raw endpoint `__module__`.

The final gate must fail if agent viewset paths still depend on ambiguous old `api.viewset` ownership.

- [ ] **Step 2: Extract agent group routes**

Move agent group declarations into `api_gateway/routes/agent_group_routes.py`.

- [ ] **Step 3: Extract memory and inspection routes**

Move memory center and inspection route declarations into their gateway route modules, preserving Clerk auth dependencies.

- [ ] **Step 4: Extract orchestration and task routes**

Move deprecated orchestration and task route declarations into gateway route modules. Preserve explicit 410 behavior and deprecation metadata.

- [ ] **Step 5: Extract hub and A2A task routes**

Move hub and A2A task route declarations into gateway route modules. Preserve route-level auth semantics.

- [ ] **Step 6: Remove direct mounts for all old API routers**

Update `main.py` so it includes only:

```python
app.include_router(api_gateway.router, prefix=api_prefix)
```

for `/api/v1/*` routes, plus `/health`.

- [ ] **Step 7: Run focused tests**

Run:

```bash
pytest tests/test_api_agent.py tests/test_api_agent_group.py tests/test_api_memory.py tests/test_api_inspection.py tests/test_api_orchestration.py tests/test_api_task.py tests/test_api_a2a_tasks.py tests/test_api_gateway_route_inventory.py -q
```

Expected: behavior tests pass and route inventory shows all `/api/v1/*` endpoints owned by `api_gateway.routes.*`.

### Task 6: Consolidate Binding, Startup Checks, And CORS Policy

**Files:**
- Modify: `api_gateway/dependencies.py`
- Modify: `api_gateway/context.py`
- Modify: `api_gateway/policies.py`
- Modify: `main.py`
- Modify: `common/middleware/discovery_cors_middleware.py`
- Test: `tests/test_api_gateway_policies.py`
- Test: `tests/test_api_gateway_route_inventory.py`
- Test: relevant CORS tests or create `tests/test_api_gateway_cors.py`

- [ ] **Step 1: Create gateway dependency binding object**

Replace scattered module globals with a gateway-owned dependency holder such as:

```python
@dataclass
class APIGatewayDeps:
    room_center: RoomCenterRouteOwner
    execution_engine: ExecutionEngine
    gateway_service: GatewayService
    gateway_rate_limiter: APIKeyRateLimiter
    relay_service: RelayServiceProtocol
    ...
```

Use only protocols or route-owner interfaces.

- [ ] **Step 2: Bind gateway dependencies at startup**

In `main.py`, bind one `APIGatewayDeps` object after all facades are constructed.

- [ ] **Step 3: Update startup binding assertions**

Change `_assert_startup_bindings_complete` so it verifies `api_gateway` binding completeness instead of individual old route module globals.

- [ ] **Step 4: Centralize open CORS path policy**

Move or mirror the open CORS path policy into `api_gateway.policies` and make `DiscoveryCORSMiddleware` consume that policy.

- [ ] **Step 5: Run focused tests**

Run:

```bash
pytest tests/test_api_gateway_policies.py tests/test_api_gateway_route_inventory.py tests/test_api_gateway_cors.py -q
```

Expected: pass.

### Task 7: Retire Old API Route Ownership

**Files:**
- Modify/Delete: old `api/*.py` route modules after parity is proven
- Modify: tests importing old route functions directly
- Modify: `tests/test_api_gateway_module_boundaries.py`
- Modify: `tests/test_phase9_cleanup_gate.py` if this plan is implemented after Phase 9 gates

- [ ] **Step 1: Identify old route modules still imported by production code**

Run:

```bash
pattern=$(python - <<'PY'
from pathlib import Path

modules = [
    p.stem
    for p in Path("api").glob("*.py")
    if p.name != "__init__.py"
]
pattern = "|".join(sorted(modules))
print(pattern)
PY
)
rg -n "from api import|import api\\.|api\\.(${pattern})" --glob '*.py'
```

Expected: no production imports except optional compatibility shims explicitly allowed by tests.

- [ ] **Step 2: Convert direct route-function tests to gateway route modules**

Update tests that import route functions from `api.*` so they import from `api_gateway.routes.*` or use HTTP client requests.

- [ ] **Step 3: Delete or shim old API modules**

Delete old `api/*.py` modules if no imports remain. If deletion is too large for one step, replace each with a small deprecation shim that imports from `api_gateway.routes.*` and add an explicit cleanup gate requiring deletion before final completion.

- [ ] **Step 4: Run import boundary tests**

Run:

```bash
pytest tests/test_api_gateway_module_boundaries.py tests/test_api_gateway_route_inventory.py -q
```

Expected: pass, proving public traffic is declared by `api_gateway`.

### Task 8: Full Verification And Handoff

**Files:**
- Modify: `docs/superpowers/plans/2026-05-21-api-gateway-traffic-consolidation.md` only for implementation notes if needed
- Reference: all tests

- [ ] **Step 1: Run complete API-focused suite**

Run:

```bash
pytest tests/test_api_gateway_route_inventory.py tests/test_api_gateway_policies.py tests/test_api_gateway_module_boundaries.py tests/test_api_gateway.py tests/test_api_discovery.py tests/test_api_discovery_api_keys.py tests/test_api_relay.py tests/test_api_webhooks.py tests/test_api_room_center.py tests/test_api_hitl.py tests/test_api_sse.py tests/test_file_upload.py tests/test_api_agent.py tests/test_api_agent_group.py tests/test_api_memory.py tests/test_api_orchestration.py tests/test_api_task.py tests/test_api_a2a_tasks.py -q
```

Expected: pass.

- [ ] **Step 2: Run full suite**

Run:

```bash
pytest -q
```

Expected: pass.

- [ ] **Step 3: Run startup smoke test**

Run:

```bash
uv run uvicorn main:app --host 127.0.0.1 --port 8001
```

Expected: startup reaches `Application startup complete`. Stop the process after verification.

- [ ] **Step 4: Confirm route ownership manually**

Run:

```bash
python - <<'PY'
from api_gateway.registry import resolve_declared_owner
from main import app
bad = []
for route in app.routes:
    path = getattr(route, "path", "")
    if path.startswith("/api/"):
        owner = resolve_declared_owner(route)
        if not owner.startswith("api_gateway.routes."):
            bad.append((path, owner, getattr(getattr(route, "endpoint", None), "__module__", "")))
print(bad)
PY
```

Expected: `[]`.

- [ ] **Step 5: Commit implementation changes**

This step is for a future implementation session only. Do not run it during the plan-writing and review-only task.

```bash
git add api_gateway main.py common/middleware/discovery_cors_middleware.py container.py api tests
git commit -m "refactor: consolidate api traffic through gateway module"
```

## Review Log

This plan-writing task requires 20 independent Codex review rounds. Each round must review the whole current plan and may only result in edits to this plan file.

| Round | Reviewer Status | Plan Changes Applied |
|---:|---|---|
| 1 | Approved | Added explicit filename gate forbidding `api_gateway/routes/legacy_*.py`. |
| 2 | Issues Found | Clarified agent viewset ownership strategy and changed route ownership gate to use explicit declared-owner metadata. |
| 3 | Issues Found | Unified route ownership checks around `declared_owner` and defined registry metadata fallback for generated routes. |
| 4 | Approved | No plan changes required. |
| 5 | Issues Found | Added missing `resolve_declared_owner` import to final manual route ownership check. |
| 6 | Approved | Clarified `/health` as the only intended non-gateway HTTP endpoint exception. |
| 7 | Approved | Clarified final commit step applies only to future implementation, not this plan-only review task. |
| 8 | Approved | No plan changes required. |
| 9 | Approved | No plan changes required. |
| 10 | Approved | No plan changes required. |
| 11 | Approved | No plan changes required. |
| 12 | Approved | No plan changes required. |
| 13 | Approved | No plan changes required. |
| 14 | Approved | No plan changes required. |
| 15 | Approved | No plan changes required. |
| 16 | Approved | No plan changes required. |
| 17 | Approved | No plan changes required. |
| 18 | Approved | No plan changes required. |
| 19 | Approved | Broadened old API import search to cover all current `api/*.py` route modules. |
| 20 | Approved | Broadened future implementation commit command to include old `api/*.py` deletions/shims and dependency binding changes. |
